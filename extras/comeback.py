"""
extras/comeback.py — бонус за возвращение для игроков, которые давно не делали ставок.

Правила (суммы меняются переменными окружения):
  * «Неактивен» = нет ставок на матчи и в играх (журнал: kind bet / game_bet) COMEBACK_MIN_DAYS (3) дня.
  * Награда растёт с длиной перерыва:
        от 3 дней  — 100 000 PTS;
        от 7 дней  — 100 000 PTS + фрибет 100 000;
        от 14 дней — 200 000 PTS + фрибет 150 000.
  * Не чаще раза в COMEBACK_COOLDOWN_DAYS (7) дней, и только если после прошлого такого бонуса игрок
    снова ставил (иначе можно было бы забирать его каждую неделю, не играя).
  * Только для тех, кто реально играл: за всё время ставок минимум на COMEBACK_MIN_WAGER (20 000) PTS —
    чтобы бонус нельзя было фармить пачкой новых аккаунтов.
  * Одно напоминание в Telegram: когда игрок не ставит 3 дня, бот один раз пишет, что его ждёт бонус
    (не чаще раза в COMEBACK_NOTIFY_EVERY (14) дней, с учётом настройки «уведомления» в профиле).
  * Забрать — в приложении: окно «С возвращением!» при входе и баннер в «Бонусах».

Слабый сервер (Render free): статус игрока считается при чтении двумя запросами по индексу
idx_tx_user; для напоминаний своя таблица последних ставок cb_activity обновляется инкрементально
по id журнала (читаются только новые строки), раз в 10 минут, не больше 25 сообщений за проход.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from . import core, freebets
from .core import all_, auth, db, money, now_iso, one, touch_user, user_lock

log = logging.getLogger("mspbets.comeback")
router = APIRouter(prefix="/api/v2/comeback")

MIN_DAYS = int(os.getenv("COMEBACK_MIN_DAYS", "3"))
COOLDOWN_DAYS = int(os.getenv("COMEBACK_COOLDOWN_DAYS", "7"))
MIN_WAGER = int(os.getenv("COMEBACK_MIN_WAGER", "20000"))
NOTIFY_EVERY = int(os.getenv("COMEBACK_NOTIFY_EVERY", "14"))
NOTIFY_WINDOW = 45  # дольше 45 дней не заходил — уже не пишем
NOTIFY_BATCH = 25
SCAN_EVERY = 600  # секунд между проходами напоминаний
SCAN_CHUNK = 20000  # строк журнала за один проход
FB_DAYS = 7
TIERS = [  # (от скольких дней, PTS, фрибет) — от большего к меньшему
    (int(os.getenv("COMEBACK_T3_DAYS", "14")), int(os.getenv("COMEBACK_T3_PTS", "200000")), int(os.getenv("COMEBACK_T3_FB", "150000"))),
    (int(os.getenv("COMEBACK_T2_DAYS", "7")), int(os.getenv("COMEBACK_T2_PTS", "100000")), int(os.getenv("COMEBACK_T2_FB", "100000"))),
    (MIN_DAYS, int(os.getenv("COMEBACK_T1_PTS", "100000")), int(os.getenv("COMEBACK_T1_FB", "0"))),
]
BET_KINDS = "('bet', 'game_bet')"
META_CURSOR = "cb_tx_cursor"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS cb_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, last_bet TEXT NOT NULL,
        days INTEGER NOT NULL, pts INTEGER NOT NULL, freebet INTEGER NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(user_id, last_bet))""",
    "CREATE INDEX IF NOT EXISTS idx_cb_claims_user ON cb_claims(user_id, id)",
    "CREATE TABLE IF NOT EXISTS cb_activity (user_id INTEGER PRIMARY KEY, last_bet TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_cb_activity_last ON cb_activity(last_bet)",
    "CREATE TABLE IF NOT EXISTS cb_notify (user_id INTEGER PRIMARY KEY, last_bet TEXT NOT NULL, notified_at TEXT NOT NULL)",
]


def _dt(iso):
    d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def tier_for(days):
    for d, pts, fb in TIERS:
        if days >= d:
            return {"from_days": d, "pts": pts, "freebet": fb}
    return None


def tiers_public():
    return [{"from_days": d, "pts": p, "freebet": f} for d, p, f in sorted(TIERS)]


def last_bet(conn, uid):
    r = one(conn, f"SELECT created_at FROM transactions WHERE user_id=? AND kind IN {BET_KINDS} ORDER BY id DESC LIMIT 1", (uid,))
    return r[0] if r else None


def offer(conn, uid, now=None):
    """Что игрок может забрать прямо сейчас: dict с available=True/False и причиной."""
    now = now or datetime.now(timezone.utc)
    lb = last_bet(conn, uid)
    if not lb:
        return {"available": False, "reason": "never"}
    days = int((now - _dt(lb)).total_seconds() // 86400)
    if days < MIN_DAYS:
        return {"available": False, "reason": "active", "days": days}
    lc = one(conn, "SELECT created_at, last_bet FROM cb_claims WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))
    if lc:
        if _dt(lc[0]) > now - timedelta(days=COOLDOWN_DAYS):
            return {"available": False, "reason": "cooldown", "days": days,
                    "next_at": (_dt(lc[0]) + timedelta(days=COOLDOWN_DAYS)).isoformat()}
        if lb <= lc[1]:  # последняя ставка та же, что была при прошлом бонусе — после него не играл
            return {"available": False, "reason": "no_bets_since", "days": days}
    wager = one(conn, f"SELECT COALESCE(SUM(-amount), 0) FROM transactions WHERE user_id=? AND kind IN {BET_KINDS}", (uid,))[0]
    if int(wager or 0) < MIN_WAGER:
        return {"available": False, "reason": "low_wager", "days": days}
    t = tier_for(days)
    return {"available": True, "days": days, "last_bet": lb, **t}


@router.get("")
def status(request: Request):
    uid = int(auth(request)["id"])
    with db() as conn:
        o = offer(conn, uid)
    o.pop("last_bet", None)
    return {**o, "tiers": tiers_public(), "cooldown_days": COOLDOWN_DAYS, "min_days": MIN_DAYS, "fb_days": FB_DAYS}


@router.post("/claim")
def claim(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        o = offer(conn, uid)
        if not o["available"]:
            raise HTTPException(400, {"cooldown": "Бонус за возвращение уже получен — следующий не раньше чем через неделю",
                                      "active": "Бонус за возвращение — для тех, кто не делал ставок 3 дня и больше",
                                      "no_bets_since": "Сначала сделай ставку — бонус дают за возвращение после перерыва",
                                      "low_wager": "Бонус за возвращение доступен игрокам, которые уже активно играли"}.get(o["reason"], "Бонус недоступен"))
        cur = conn.execute("INSERT OR IGNORE INTO cb_claims (user_id, last_bet, days, pts, freebet, created_at) VALUES (?,?,?,?,?,?)",
                           (uid, o["last_bet"], o["days"], o["pts"], o["freebet"], now_iso()))
        if core.changed(cur, conn) != 1:
            raise HTTPException(409, "Бонус уже получен")
        if o["pts"]:
            core.adjust(conn, uid, o["pts"], "comeback", f"Бонус за возвращение: {o['days']} дн. без ставок", ref=f"cb:{uid}:{o['last_bet'][:10]}")
        fb_ok = bool(o["freebet"]) and bool(freebets.issue(conn, uid, o["freebet"], "comeback", f"С возвращением! ({o['days']} дн. перерыв)", FB_DAYS))
        bal = core.balance_of(conn, uid)
    parts = [f"+{money(o['pts'])} PTS"] if o["pts"] else []
    if fb_ok:
        parts.append(f"фрибет {money(o['freebet'])}")
    return {"ok": True, "pts": o["pts"], "freebet": o["freebet"] if fb_ok else 0, "days": o["days"], "balance": bal,
            "message": "С возвращением! " + " и ".join(parts)}


# ------------------------------------------------------------------
# Напоминания в Telegram (из планировщика wave1)
# ------------------------------------------------------------------
_last_scan = 0.0


def _sync_activity(conn, meta_get, meta_set):
    """Обновить cb_activity по новым строкам журнала (по id). Первый запуск — один полный проход."""
    cur = meta_get(conn, META_CURSOR)
    top = one(conn, "SELECT COALESCE(MAX(id), 0) FROM transactions")[0]
    if cur is None:
        rows = all_(conn, f"SELECT user_id, MAX(created_at) FROM transactions WHERE kind IN {BET_KINDS} GROUP BY user_id")
        hi = top
    else:
        lo = int(cur)
        hi = min(top, lo + SCAN_CHUNK)
        if hi <= lo:
            return 0
        rows = all_(conn, f"SELECT user_id, MAX(created_at) FROM transactions WHERE id>? AND id<=? AND kind IN {BET_KINDS} GROUP BY user_id", (lo, hi))
    for uid, lb in rows:
        conn.execute("""INSERT INTO cb_activity (user_id, last_bet) VALUES (?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET last_bet=excluded.last_bet WHERE excluded.last_bet > cb_activity.last_bet""", (uid, lb))
    meta_set(conn, META_CURSOR, hi)
    return len(rows)


def remind(meta_get, meta_set, force=False):
    """Раз в SCAN_EVERY: синхронизировать активность и написать неактивным (не больше NOTIFY_BATCH за раз)."""
    global _last_scan
    if not force and time.monotonic() - _last_scan < SCAN_EVERY:
        return 0
    _last_scan = time.monotonic()
    now = datetime.now(timezone.utc)
    with db() as conn:
        _sync_activity(conn, meta_get, meta_set)
    with db() as conn:
        cands = all_(conn, """SELECT a.user_id, a.last_bet FROM cb_activity a LEFT JOIN cb_notify n ON n.user_id=a.user_id
                              WHERE a.last_bet < ? AND a.last_bet > ? AND (n.user_id IS NULL OR (n.last_bet != a.last_bet AND n.notified_at < ?))
                              ORDER BY a.last_bet DESC LIMIT ?""",
                     ((now - timedelta(days=MIN_DAYS)).isoformat(), (now - timedelta(days=NOTIFY_WINDOW)).isoformat(),
                      (now - timedelta(days=NOTIFY_EVERY)).isoformat(), NOTIFY_BATCH * 2))
        msgs = []
        for uid, lb in cands:
            o = offer(conn, uid, now)
            conn.execute("""INSERT INTO cb_notify (user_id, last_bet, notified_at) VALUES (?,?,?)
                            ON CONFLICT(user_id) DO UPDATE SET last_bet=excluded.last_bet, notified_at=excluded.notified_at""",
                         (uid, lb, now_iso() if o["available"] else "1970-01-01T00:00:00+00:00"))
            if not o["available"]:
                continue  # не подходит (кулдаун, мало играл) — отмечаем перерыв, но не пишем
            gift = f"<b>{money(o['pts'])} PTS</b>" + (f" и <b>фрибет {money(o['freebet'])}</b>" if o["freebet"] else "")
            msgs.append((uid, f"👋 <b>Давно не виделись!</b>\nТебя не было {o['days']} дн. — держи бонус за возвращение: {gift}.\n"
                              f"Открой приложение и забери его в окне «С возвращением»."))
            if len(msgs) >= NOTIFY_BATCH:
                break
        core.notify_many(conn, msgs)
    if msgs:
        log.info("comeback: напоминаний отправлено %s", len(msgs))
    return len(msgs)
