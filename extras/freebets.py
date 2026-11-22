"""
extras/freebets.py — фрибеты (бесплатные ставки), как в настоящих БК.

Фрибет — это «жетон» на сумму N PTS. Им можно поставить:
  * одинар на исход матча (П1 / X / П2);
  * одинар на доп. рынок (тотал, фора, точный счёт …);
  * экспресс+ (от 2 событий).
Ставка фрибетом не списывает баланс. Если ставка выиграла — игрок получает ЧИСТЫЙ выигрыш
(сумма фрибета не возвращается, как в БК: «stake not returned»). Проиграла — ничего не теряет.
Матч отменили — фрибет возвращается жетоном (а не баллами).

Откуда берутся фрибеты: страховка экспресса (accaperks.py), сезонный пропуск (seasonpass.py),
выдача админом. Срок жизни — FB_DAYS (7) дней, потом сгорает.

Как это сделано без правки старого кода: при ставке фрибетом на баланс зачисляется сумма жетона
(вид «freebet») и тут же обычным способом ставится ставка (старые функции place_*). Расчёт идёт
старым кодом (выплата = ставка × кэф), а фоновая сверка reconcile() после расчёта забирает назад
сумму жетона (вид «freebet_stake»). Если баллов не хватает (игрок успел потратить) — остаток
долга запоминается и забирается при следующих сверках. Выкупленная ставка фрибетом — забирается
min(жетон, сумма выкупа).
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import betting, core, markets, mexpress
from .core import all_, auth, db, money, now_iso, one, touch_user, user_lock

log = logging.getLogger("mspbets.freebets")
router = APIRouter(prefix="/api/v2/freebets")

FB_DAYS = int(os.getenv("FREEBET_DAYS", "7"))
MAX_ACTIVE = 30  # больше активных жетонов не выдаём (защита от накруток)

SOURCES = {"insurance": "🛡 Страховка экспресса", "pass": "🎟 Сезонный пропуск", "admin": "🛠 Подарок админа",
           "void": "↩️ Возврат фрибета (матч отменён)"}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS w1_freebets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
        source TEXT NOT NULL, note TEXT, status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT, bet_kind TEXT, bet_id INTEGER)""",
    "CREATE INDEX IF NOT EXISTS idx_w1fb_user ON w1_freebets(user_id, status)",
    """CREATE TABLE IF NOT EXISTS w1_fb_bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, bet_id INTEGER NOT NULL,
        freebet_id INTEGER NOT NULL, amount INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'open',
        owed INTEGER NOT NULL DEFAULT 0, result TEXT, created_at TEXT NOT NULL, done_at TEXT,
        UNIQUE(kind, bet_id))""",
    "CREATE INDEX IF NOT EXISTS idx_w1fbb_state ON w1_fb_bets(state)",
    "CREATE INDEX IF NOT EXISTS idx_w1fbb_user ON w1_fb_bets(user_id, id)",
]

# вид ставки -> (таблица, колонка статуса, колонка суммы выкупа или None)
TABLES = {"single": ("predictions", "cashout"), "market": ("market_bets", None), "mx": ("mx_bets", None)}


def _iso_in(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def issue(conn, uid, amount, source, note="", days=FB_DAYS):
    """Выдать фрибет (внутри транзакции вызывающего). Возвращает id или None (лимит активных)."""
    amount = int(amount)
    if amount <= 0:
        return None
    n = one(conn, "SELECT COUNT(*) FROM w1_freebets WHERE user_id=? AND status='active' AND expires_at>?", (uid, now_iso()))[0]
    if n >= MAX_ACTIVE:
        return None
    return conn.execute(
        "INSERT INTO w1_freebets (user_id, amount, source, note, created_at, expires_at) VALUES (?,?,?,?,?,?)",
        (uid, amount, source, (note or "")[:120], now_iso(), _iso_in(days)),
    ).lastrowid


def _row(r):
    return {"id": r[0], "amount": r[1], "source": r[2], "source_label": SOURCES.get(r[2], r[2]), "note": r[3] or "",
            "status": r[4], "created_at": r[5], "expires_at": r[6], "used_at": r[7], "bet_kind": r[8], "bet_id": r[9]}


_COLS = "id, amount, source, note, status, created_at, expires_at, used_at, bet_kind, bet_id"


def active(conn, uid):
    return [_row(r) for r in all_(conn, f"SELECT {_COLS} FROM w1_freebets WHERE user_id=? AND status='active' AND expires_at>? ORDER BY expires_at, id",
                                  (uid, now_iso()))]


def bet_map(conn, uid):
    """{'kind:id': {...}} — ставки игрока, сделанные фрибетом (для истории и карточки ставки)."""
    out = {}
    for kind, bid, amount, state, owed, result in all_(conn, "SELECT kind, bet_id, amount, state, owed, result FROM w1_fb_bets WHERE user_id=? ORDER BY id DESC LIMIT 300", (uid,)):
        out[f"{kind}:{bid}"] = {"amount": amount, "state": state, "owed": owed, "result": result}
    return out


def is_freebet(conn, kind, bid):
    return bool(one(conn, "SELECT 1 FROM w1_fb_bets WHERE kind=? AND bet_id=?", (kind, int(bid))))


# ------------------------------------------------------------------
# API
# ------------------------------------------------------------------
@router.get("")
def my_freebets(request: Request):
    uid = int(auth(request)["id"])
    with db() as conn:
        act = active(conn, uid)
        hist = [_row(r) for r in all_(conn, f"""SELECT {_COLS} FROM w1_freebets WHERE user_id=? AND (status!='active' OR expires_at<=?)
                                               ORDER BY id DESC LIMIT 20""", (uid, now_iso()))]
    for h in hist:
        if h["status"] == "active":
            h["status"] = "expired"
    return {"active": act, "history": hist, "total": sum(f["amount"] for f in act), "days": FB_DAYS}


@router.post("/use")
def use(request: Request, body: dict = Body(...)):
    """Поставить фрибетом. type: single (П1/X/П2) | market (доп. рынок) | mx (экспресс+)."""
    user = auth(request)
    uid = int(user["id"])
    try:
        fid = int(body.get("freebet_id"))
    except Exception:
        raise HTTPException(400, "Выберите фрибет")
    typ = str(body.get("type") or "")
    if typ not in ("single", "market", "mx"):
        raise HTTPException(400, "Неверный вид ставки")
    legs = mexpress._parse_legs(body.get("legs")) if typ == "mx" else None
    if typ != "mx":
        try:
            eid = int(body.get("event_id"))
            seen = float(body["odds"]) if body.get("odds") is not None else None
        except Exception:
            raise HTTPException(400, "Неверные данные")
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        fb = one(conn, "SELECT amount, status, expires_at FROM w1_freebets WHERE id=? AND user_id=?", (fid, uid))
        if not fb or fb[1] != "active":
            raise HTTPException(400, "Фрибет уже использован")
        if fb[2] <= now_iso():
            raise HTTPException(400, "Срок фрибета истёк")
        amount = int(fb[0])
        cur = conn.execute("UPDATE w1_freebets SET status='used', used_at=? WHERE id=? AND status='active'", (now_iso(), fid))
        if core.changed(cur, conn) != 1:
            raise HTTPException(400, "Фрибет уже использован")
        core.adjust(conn, uid, amount, "freebet", f"Фрибет #{fid}: ставка {money(amount)} PTS", ref=f"fb:{fid}")
        if typ == "single":
            choice = str(body.get("choice") or body.get("pick") or "")
            ev = one(conn, "SELECT odds_1, odds_x, odds_2 FROM events WHERE id=?", (eid,))
            cur_odds = betting._odds_of(ev, choice) if ev else None
            if seen is not None and cur_odds and abs(float(cur_odds) - seen) > 0.004:
                raise HTTPException(409, f"Коэффициент изменился: {seen:.2f} → {float(cur_odds):.2f}")
            odds = betting.place_single(conn, uid, eid, choice, amount, note_prefix="Фрибет")["odds"]
            # id берём запросом: place_single читает cursor.lastrowid уже после других INSERT (XP, журнал),
            # и в libsql он может указывать на чужую строку. Мы внутри user_lock — последняя ставка наша.
            kind, bid = "single", one(conn, "SELECT MAX(id) FROM predictions WHERE user_id=?", (uid,))[0]
        elif typ == "market":
            market, pick = str(body.get("market") or ""), str(body.get("pick") or "")
            bid, item = markets.place_market_bet(conn, uid, eid, market, pick, amount, seen, note_prefix="Фрибет")
            kind, odds = "market", item["odds"]
        else:
            bid, odds, _ = mexpress.place(conn, uid, amount, legs, check_odds=True, note_prefix="Экспресс+ фрибетом")
            kind = "mx"
        conn.execute("UPDATE w1_freebets SET bet_kind=?, bet_id=? WHERE id=?", (kind, bid, fid))
        conn.execute("INSERT INTO w1_fb_bets (user_id, kind, bet_id, freebet_id, amount, created_at) VALUES (?,?,?,?,?,?)",
                     (uid, kind, bid, fid, amount, now_iso()))
        bal = core.balance_of(conn, uid)
    net = core.pay(amount, odds) - amount
    return {"ok": True, "kind": kind, "id": bid, "odds": odds, "amount": amount, "net_win": net, "balance": bal,
            "unlocked": core.pop_unlocked()}


@router.post("/admin/give")
def admin_give(request: Request, body: dict = Body(...)):
    user = auth(request)
    if int(user["id"]) not in core.ctx.admin_ids:
        raise HTTPException(403, "Только для админа")
    try:
        uid, amount = int(body["user_id"]), core.check_amount(body.get("amount"), 1, 10_000_000)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Нужны user_id и amount")
    days = max(1, min(int(body.get("days") or FB_DAYS), 60))
    with db() as conn:
        if not one(conn, "SELECT 1 FROM users WHERE user_id=?", (uid,)):
            raise HTTPException(404, "Игрок не найден")
        fid = issue(conn, uid, amount, "admin", str(body.get("note") or "Подарок"), days)
        if not fid:
            raise HTTPException(400, "У игрока слишком много активных фрибетов")
        core.notify_many(conn, [(uid, f"🎁 <b>Тебе подарили фрибет {money(amount)} PTS!</b>\nДействует {days} дн. Поставь им в купоне — кнопка «🎁 Фрибетом».")])
    return {"ok": True, "id": fid}


# ------------------------------------------------------------------
# Фоновая сверка (plus/wave1 scheduler, раз в минуту)
# ------------------------------------------------------------------
def _collect(conn, uid, owed):
    """Забрать до owed баллов (сколько есть на балансе). Возвращает остаток долга."""
    if owed <= 0:
        return 0
    bal = core.balance_of(conn, uid)
    take = min(int(owed), int(bal))
    if take > 0:
        core.adjust(conn, uid, -take, "freebet_stake", "Фрибет: сумма жетона не возвращается (чистый выигрыш)", ref="fb")
    return int(owed) - max(take, 0)


def reconcile():
    """Довести до конца ставки фрибетами, которые уже рассчитаны старым кодом. Возвращает число обработанных."""
    todo = []
    with db() as conn:
        for kind, (table, cash_col) in TABLES.items():
            cash = f"COALESCE(t.{cash_col}, 0)" if cash_col else "0"
            todo += [(kind, *r) for r in all_(conn, f"""SELECT f.id, f.user_id, f.amount, f.state, f.owed, f.freebet_id, t.status, {cash}
                     FROM w1_fb_bets f JOIN {table} t ON t.id=f.bet_id
                     WHERE f.kind=? AND f.state!='done' AND (t.status!='pending' OR f.state='debt') LIMIT 200""", (kind,))]
    done = 0
    for kind, fid, uid, amount, state, owed, freebet_id, status, cashout in todo:
        try:
            with user_lock(uid), db() as conn:
                cur = one(conn, "SELECT state, owed FROM w1_fb_bets WHERE id=?", (fid,))
                if not cur or cur[0] == "done":
                    continue
                notes = []
                if cur[0] == "open":
                    if status == "lost":
                        owed, result = 0, "lost"
                    elif status == "won":
                        owed, result = amount, "won"
                    elif status == "void":
                        owed, result = amount, "void"
                        if issue(conn, uid, amount, "void", f"Матч отменён — фрибет #{freebet_id} возвращён"):
                            notes.append((uid, f"↩️ Матч отменён — фрибет <b>{money(amount)} PTS</b> вернулся к тебе жетоном."))
                    elif status == "cashed":
                        owed, result = min(amount, int(cashout or 0)), "cashed"
                    else:
                        continue
                    conn.execute("UPDATE w1_fb_bets SET result=? WHERE id=?", (result, fid))
                else:
                    owed = cur[1]
                left = _collect(conn, uid, owed)
                conn.execute("UPDATE w1_fb_bets SET state=?, owed=?, done_at=? WHERE id=?",
                             ("done" if left <= 0 else "debt", max(left, 0), now_iso() if left <= 0 else None, fid))
                if notes:
                    core.notify_many(conn, notes)
                done += 1
        except Exception as e:
            log.warning("freebet reconcile %s: %s", fid, e)
    return done
