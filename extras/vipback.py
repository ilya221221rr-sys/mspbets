"""
extras/vipback.py — VIP-кэшбэк и рейкбек, как в настоящих казино и БК.

  * Рейкбек — процент от КАЖДОЙ ставки (выиграла она или нет) копится и забирается в любой момент.
  * Кэшбэк — раз в неделю возвращается процент чистого проигрыша прошлой недели
    (понедельник 00:00 — понедельник 00:00 UTC). Забрать можно в течение текущей недели.
Процент зависит от VIP-уровня (тот же VIP, что в профиле: 20 ур. — VIP 1, 30 — VIP 2, 45 — VIP 3, 60 — VIP 4).

Источник данных — журнал операций (transactions), код игр и ставок не меняется.
Ставки: bet, game_bet, case. Возвраты: win, game_win, cashout, sell, refund, jackpot.
Возвраты вычитаются из суммы ставок для рейкбека (отмена дуэли/матча не даёт рейкбек).
Рейкбек считается с момента запуска модуля (курсор plus_meta.rb_start_tx) — без начислений «задним числом».
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from . import core
from .core import all_, auth, db, now_iso, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/vipback")

BET_KINDS = ("bet", "game_bet", "case")
WIN_KINDS = ("win", "game_win", "cashout", "sell", "refund", "jackpot")
RAKEBACK = [0.005, 0.0075, 0.01, 0.0125, 0.015]  # VIP 0..4
CASHBACK = [0.05, 0.07, 0.10, 0.12, 0.15]
CASHBACK_CAP = [50_000, 100_000, 200_000, 350_000, 500_000]
VIP_LEVEL = [1, 20, 30, 45, 60]

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS vip_rakeback (
        user_id INTEGER PRIMARY KEY, last_tx_id INTEGER NOT NULL, total INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS vip_cashback (
        user_id INTEGER NOT NULL, week TEXT NOT NULL, amount INTEGER NOT NULL, claimed_at TEXT NOT NULL,
        PRIMARY KEY (user_id, week))""",
]

_meta_get = None


def init(conn, meta_get, meta_set):
    global _meta_get
    _meta_get = meta_get
    if meta_get(conn, "rb_start_tx") is None:
        last = one(conn, "SELECT COALESCE(MAX(id), 0) FROM transactions")[0]
        meta_set(conn, "rb_start_tx", str(last))


def week_bounds(now=None):
    now = now or datetime.now(timezone.utc)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=7), start, start + timedelta(days=7)  # прошлая неделя, текущая, следующая


def _vip(conn, uid):
    r = one(conn, "SELECT xp FROM profiles WHERE user_id=?", (uid,))
    lvl = core.level_for(r[0] if r else 0)
    return core.vip_for(lvl), lvl


def _rb_cursor(conn, uid):
    r = one(conn, "SELECT last_tx_id, total FROM vip_rakeback WHERE user_id=?", (uid,))
    if r:
        return r[0], r[1]
    return int(_meta_get(conn, "rb_start_tx") or 0), 0


def _rakeback(conn, uid, vip):
    last, total = _rb_cursor(conn, uid)
    # возвраты (отмена дуэли, отмена матча) вычитаются — иначе рейкбек можно было бы «накручивать»
    kinds = BET_KINDS + ("refund",)
    marks = ",".join("?" * len(kinds))
    top, wager = one(conn, f"SELECT COALESCE(MAX(id), ?), COALESCE(-SUM(amount), 0) FROM transactions WHERE user_id=? AND id>? AND kind IN ({marks})",
                     (last, uid, last, *kinds))
    wager = max(0, int(wager))
    return {"available": int(int(wager) * RAKEBACK[vip]), "wager": int(wager), "rate": RAKEBACK[vip], "total": total, "top": top, "last": last}


def _net(conn, uid, a, b):
    kinds = BET_KINDS + WIN_KINDS
    marks = ",".join("?" * len(kinds))
    rows = all_(conn, f"SELECT kind, COALESCE(SUM(amount), 0) FROM transactions WHERE user_id=? AND created_at>=? AND created_at<? AND kind IN ({marks}) GROUP BY kind",
                (uid, a.isoformat(), b.isoformat(), *kinds))
    wager = -sum(v for k, v in rows if k in BET_KINDS)
    back = sum(v for k, v in rows if k in WIN_KINDS)
    return int(wager), int(back), max(0, int(wager - back))


def _cashback(conn, uid, vip):
    prev, cur, nxt = week_bounds()
    wager, back, loss = _net(conn, uid, prev, cur)
    amount = min(int(loss * CASHBACK[vip]), CASHBACK_CAP[vip])
    claimed = one(conn, "SELECT amount, claimed_at FROM vip_cashback WHERE user_id=? AND week=?", (uid, prev.date().isoformat()))
    w2, b2, loss2 = _net(conn, uid, cur, nxt)
    return {
        "week": prev.date().isoformat(), "week_label": f"{prev:%d.%m}–{(cur - timedelta(days=1)):%d.%m}",
        "wager": wager, "returned": back, "loss": loss, "amount": amount, "rate": CASHBACK[vip], "cap": CASHBACK_CAP[vip],
        "claimed": bool(claimed), "claimed_amount": claimed[0] if claimed else 0,
        "deadline": nxt.isoformat(),
        "current": {"wager": w2, "returned": b2, "loss": loss2, "estimate": min(int(loss2 * CASHBACK[vip]), CASHBACK_CAP[vip]),
                    "pays_from": nxt.isoformat()},
    }


@router.get("")
def status(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        touch_user(conn, user)
        vip, lvl = _vip(conn, uid)
        rb = _rakeback(conn, uid, vip)
        cb = _cashback(conn, uid, vip)
    rb.pop("top", None)
    rb.pop("last", None)
    return {
        "vip": vip, "level": lvl, "rakeback": rb, "cashback": cb,
        "table": [{"vip": i, "level": VIP_LEVEL[i], "rakeback": RAKEBACK[i], "cashback": CASHBACK[i], "cap": CASHBACK_CAP[i]} for i in range(5)],
    }


@router.post("/rakeback")
def claim_rakeback(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        vip, _ = _vip(conn, uid)
        rb = _rakeback(conn, uid, vip)
        if rb["available"] < 1:
            raise HTTPException(400, "Рейкбек пока не накопился — делайте ставки")
        conn.execute("INSERT OR IGNORE INTO vip_rakeback (user_id, last_tx_id, total) VALUES (?,?,0)", (uid, rb["last"]))
        cur = conn.execute("UPDATE vip_rakeback SET last_tx_id=?, total=total+? WHERE user_id=? AND last_tx_id=?",
                           (rb["top"], rb["available"], uid, rb["last"]))
        if core.changed(cur, conn) != 1:
            raise HTTPException(409, "Рейкбек уже получен")
        bal = core.adjust(conn, uid, rb["available"], "rakeback", f"Рейкбек VIP {vip} ({RAKEBACK[vip] * 100:g}% от {core.money(rb['wager'])} PTS ставок)")
    return {"ok": True, "amount": rb["available"], "balance": bal}


@router.post("/cashback")
def claim_cashback(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        vip, _ = _vip(conn, uid)
        cb = _cashback(conn, uid, vip)
        if cb["claimed"]:
            raise HTTPException(400, "Кэшбэк за прошлую неделю уже получен")
        if cb["amount"] < 1:
            raise HTTPException(400, "За прошлую неделю нет проигрыша — кэшбэка нет")
        cur = conn.execute("INSERT OR IGNORE INTO vip_cashback (user_id, week, amount, claimed_at) VALUES (?,?,?,?)",
                           (uid, cb["week"], cb["amount"], now_iso()))
        if core.changed(cur, conn) != 1:
            raise HTTPException(409, "Кэшбэк уже получен")
        bal = core.adjust(conn, uid, cb["amount"], "cashback", f"Кэшбэк VIP {vip} за неделю {cb['week_label']} ({CASHBACK[vip] * 100:g}%)", ref=cb["week"])
    return {"ok": True, "amount": cb["amount"], "balance": bal}
