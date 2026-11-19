"""
extras/games.py — мини-игры на баллы: Мины, Plinko, Dice, Колесо, Монетка.

Все игры «доказуемо честные по математике»: RTP фиксирован и показывается игроку
(Dice/Мины/Монетка ~97%, Plinko ~96%, Колесо 95.8%), результат определяется
криптостойким ГСЧ на сервере, клиент ничего не решает.
Любое списание/начисление идёт через core.adjust → журнал операций.
"""
import json
import math

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import RNG, all_, auth, db, now_iso, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/games")

MAX_BET = 1_000_000

# ------------------------------- Plinko -------------------------------
PLINKO_G = {"low": 1.5, "medium": 3.8, "high": 7.0}
PLINKO_ROWS = (8, 10, 12, 14, 16)


def plinko_table(rows, risk):
    g = PLINKO_G[risk]
    raw = [math.exp(g * (abs(k - rows / 2) / (rows / 2)) ** 2) for k in range(rows + 1)]
    pr = [math.comb(rows, k) / 2 ** rows for k in range(rows + 1)]
    s = 0.96 / sum(a * b for a, b in zip(raw, pr))
    mult = [round(r * s, 2) for r in raw]
    rtp = sum(m * p for m, p in zip(mult, pr))
    return mult, pr, rtp


PLINKO = {r: {k: plinko_table(r, k) for k in PLINKO_G} for r in PLINKO_ROWS}

# ------------------------------- Колесо -------------------------------
WHEEL_COUNTS = {0: 27, 1.2: 7, 1.5: 9, 2: 4, 3: 1, 5: 1, 10: 1}  # 50 секторов, RTP 95.8%


def _spread(counts):
    pos = []
    for vi, (m, c) in enumerate(sorted(counts.items(), key=lambda x: -x[1])):
        for j in range(c):
            pos.append(((j + 0.5) / c + vi * 0.013, m))
    pos.sort()
    return [m for _, m in pos]


WHEEL = _spread(WHEEL_COUNTS)
WHEEL_RTP = sum(WHEEL) / len(WHEEL)

COIN_MULT = 1.94
DICE_EDGE = 0.97


def mines_mult(k, m):
    """Множитель после k безопасных ячеек при m минах (поле 25): RTP 97%."""
    p = 0.97
    for i in range(k):
        p *= (25 - i) / (25 - m - i)
    return math.floor(p * 100) / 100


def _round_end(conn, uid, game, bet, payout, detail):
    core.game_win(conn, uid, payout, game)
    core.log_round(conn, uid, game, bet, payout, payout / bet if bet else 0, detail)
    core.game_done(conn, uid, game)
    return core.balance_of(conn, uid)


@router.get("/config")
def config(request: Request):
    auth(request)
    return {
        "plinko": {
            str(r): {k: {"mult": v[0], "prob": [round(p * 100, 4) for p in v[1]], "rtp": round(v[2] * 100, 2)} for k, v in d.items()}
            for r, d in PLINKO.items()
        },
        "wheel": {"segments": WHEEL, "rtp": round(WHEEL_RTP * 100, 2)},
        "coin": {"mult": COIN_MULT, "rtp": round(COIN_MULT * 50, 2)},
        "dice": {"rtp": DICE_EDGE * 100},
        "mines": {"rtp": 97},
        "max_bet": MAX_BET,
    }


@router.get("/history")
def history(request: Request, game: str = ""):
    user = auth(request)
    with db() as conn:
        if game:
            rows = all_(conn, "SELECT game, bet, payout, mult, created_at FROM game_rounds WHERE user_id=? AND game=? ORDER BY id DESC LIMIT 15", (int(user["id"]), game))
        else:
            rows = all_(conn, "SELECT game, bet, payout, mult, created_at FROM game_rounds WHERE user_id=? ORDER BY id DESC LIMIT 30", (int(user["id"]),))
    return [{"game": r[0], "bet": r[1], "payout": r[2], "mult": r[3], "at": r[4]} for r in rows]


# ------------------------------- Монетка -------------------------------
@router.post("/coin")
def coin(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    side = str(body.get("side"))
    if side not in ("heads", "tails"):
        raise HTTPException(400, "Выберите сторону")
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        core.game_bet(conn, uid, bet, "coin", "Ставка: Монетка")
        result = RNG.choice(["heads", "tails"])
        win = result == side
        payout = core.pay(bet, COIN_MULT) if win else 0
        bal = _round_end(conn, uid, "coin", bet, payout, {"side": side, "result": result})
    return {"ok": True, "result": result, "win": win, "payout": payout, "mult": COIN_MULT, "balance": bal, "unlocked": core.pop_unlocked()}


# ------------------------------- Dice -------------------------------
@router.post("/dice")
def dice(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    direction = str(body.get("direction", "under"))
    if direction not in ("under", "over"):
        raise HTTPException(400, "Неверное направление")
    try:
        chance = float(body.get("chance"))
    except Exception:
        raise HTTPException(400, "Неверный шанс")
    bp = int(round(chance * 100))
    if not (100 <= bp <= 9500):
        raise HTTPException(400, "Шанс от 1% до 95%")
    mult = (int(DICE_EDGE * 1_000_000) // bp) / 100  # floor(97 / chance%, 2 знака)
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        core.game_bet(conn, uid, bet, "dice", "Ставка: Dice")
        roll = RNG.randrange(10000)  # 0.00 .. 99.99
        win = roll < bp if direction == "under" else roll >= 10000 - bp
        payout = core.pay(bet, mult) if win else 0
        bal = _round_end(conn, uid, "dice", bet, payout, {"roll": roll / 100, "chance": bp / 100, "dir": direction})
    return {"ok": True, "roll": roll / 100, "win": win, "payout": payout, "mult": mult, "chance": bp / 100, "balance": bal, "unlocked": core.pop_unlocked()}


# ------------------------------- Колесо -------------------------------
@router.post("/wheel")
def wheel(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        core.game_bet(conn, uid, bet, "wheel", "Ставка: Колесо")
        idx = RNG.randrange(len(WHEEL))
        mult = WHEEL[idx]
        payout = core.pay(bet, mult)
        bal = _round_end(conn, uid, "wheel", bet, payout, {"index": idx, "mult": mult})
    return {"ok": True, "index": idx, "mult": mult, "win": payout > 0, "payout": payout, "balance": bal, "unlocked": core.pop_unlocked()}


# ------------------------------- Plinko -------------------------------
@router.post("/plinko")
def plinko(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    rows = int(body.get("rows", 12))
    risk = str(body.get("risk", "medium"))
    if rows not in PLINKO or risk not in PLINKO_G:
        raise HTTPException(400, "Неверные параметры")
    table = PLINKO[rows][risk][0]
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        core.game_bet(conn, uid, bet, "plinko", "Ставка: Plinko")
        path = [RNG.randrange(2) for _ in range(rows)]
        slot = sum(path)
        mult = table[slot]
        payout = core.pay(bet, mult)
        if mult >= 1:
            core.set_max(conn, uid, "plinko_best_x", int(mult))
        bal = _round_end(conn, uid, "plinko", bet, payout, {"rows": rows, "risk": risk, "slot": slot})
    return {"ok": True, "path": path, "slot": slot, "mult": mult, "payout": payout, "win": payout > bet, "balance": bal, "unlocked": core.pop_unlocked()}


# ------------------------------- Мины -------------------------------
def _mines_view(row, balance=None):
    bet, m, revealed = row[0], row[1], json.loads(row[3])
    k = len(revealed)
    cur = mines_mult(k, m)
    nxt = mines_mult(k + 1, m) if k < 25 - m else cur
    return {
        "active": True, "bet": bet, "mines": m, "revealed": revealed, "mult": cur if k else 1.0,
        "next_mult": nxt, "cashout": core.pay(bet, cur) if k else 0,
    }


@router.get("/mines/state")
def mines_state(request: Request):
    user = auth(request)
    with db() as conn:
        row = one(conn, "SELECT bet, mines, positions, revealed FROM mines_games WHERE user_id=?", (int(user["id"]),))
    return _mines_view(row) if row else {"active": False}


@router.post("/mines/start")
def mines_start(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    m = int(body.get("mines", 3))
    if not (1 <= m <= 24):
        raise HTTPException(400, "Мин от 1 до 24")
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        if one(conn, "SELECT 1 FROM mines_games WHERE user_id=?", (uid,)):
            raise HTTPException(400, "Сначала завершите текущую партию")
        core.game_bet(conn, uid, bet, "mines", "Ставка: Мины")
        positions = RNG.sample(range(25), m)
        conn.execute(
            "INSERT INTO mines_games (user_id, bet, mines, positions, revealed, created_at) VALUES (?,?,?,?,?,?)",
            (uid, bet, m, json.dumps(positions), "[]", now_iso()),
        )
        bal = core.balance_of(conn, uid)
    return {"ok": True, "balance": bal, "state": {"active": True, "bet": bet, "mines": m, "revealed": [], "mult": 1.0, "next_mult": mines_mult(1, m), "cashout": 0}, "unlocked": core.pop_unlocked()}


@router.post("/mines/reveal")
def mines_reveal(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    idx = int(body.get("index", -1))
    if not (0 <= idx < 25):
        raise HTTPException(400, "Неверная ячейка")
    with user_lock(uid), db() as conn:
        row = one(conn, "SELECT bet, mines, positions, revealed FROM mines_games WHERE user_id=?", (uid,))
        if not row:
            raise HTTPException(400, "Нет активной партии")
        bet, m, positions, revealed = row[0], row[1], json.loads(row[2]), json.loads(row[3])
        if idx in revealed:
            raise HTTPException(400, "Ячейка уже открыта")
        if idx in positions:
            conn.execute("DELETE FROM mines_games WHERE user_id=?", (uid,))
            bal = _round_end(conn, uid, "mines", bet, 0, {"mines": m, "opened": len(revealed), "hit": idx})
            return {"ok": True, "boom": True, "positions": positions, "revealed": revealed, "balance": bal, "payout": 0, "unlocked": core.pop_unlocked()}
        revealed.append(idx)
        core.quest_event(conn, uid, "mines_safe", 1)
        core.set_max(conn, uid, "mines_best", len(revealed))
        if len(revealed) == 25 - m:  # открыты все безопасные — автокэшаут
            payout = core.pay(bet, mines_mult(len(revealed), m))
            conn.execute("DELETE FROM mines_games WHERE user_id=?", (uid,))
            bal = _round_end(conn, uid, "mines", bet, payout, {"mines": m, "opened": len(revealed), "cleared": True})
            return {"ok": True, "boom": False, "done": True, "positions": positions, "revealed": revealed, "payout": payout, "mult": mines_mult(len(revealed), m), "balance": bal, "unlocked": core.pop_unlocked()}
        conn.execute("UPDATE mines_games SET revealed=? WHERE user_id=?", (json.dumps(revealed), uid))
        view = _mines_view((bet, m, None, json.dumps(revealed)))
    return {"ok": True, "boom": False, "state": view, "unlocked": core.pop_unlocked()}


@router.post("/mines/cashout")
def mines_cashout(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        row = one(conn, "SELECT bet, mines, positions, revealed FROM mines_games WHERE user_id=?", (uid,))
        if not row:
            raise HTTPException(400, "Нет активной партии")
        bet, m, positions, revealed = row[0], row[1], json.loads(row[2]), json.loads(row[3])
        if not revealed:
            raise HTTPException(400, "Откройте хотя бы одну ячейку")
        mult = mines_mult(len(revealed), m)
        payout = core.pay(bet, mult)
        conn.execute("DELETE FROM mines_games WHERE user_id=?", (uid,))
        bal = _round_end(conn, uid, "mines", bet, payout, {"mines": m, "opened": len(revealed)})
    return {"ok": True, "payout": payout, "mult": mult, "positions": positions, "revealed": revealed, "balance": bal, "unlocked": core.pop_unlocked()}
