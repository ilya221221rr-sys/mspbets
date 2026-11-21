"""
extras/blackjack.py — Блэкджек (21) на баллы.

Правила (стандартные, как в казино):
  * колода — 6 стандартных колод (312 карт), тасуется криптостойким ГСЧ на сервере при каждой новой раздаче;
  * игроку и дилеру по 2 карты, вторая карта дилера скрыта (клиенту её значение не отправляется до конца раздачи);
  * Блэкджек (туз + 10/картинка с первых двух карт) платит 3:2; обычная победа 1:1; ничья («пуш») — возврат ставки;
  * дилер добирает до 17 и останавливается на любом 17 (в т.ч. «мягком»);
  * действия: «Ещё» (hit), «Хватит» (stand), «Удвоить» (double — только на первых двух картах, одна карта и стоп);
  * если у дилера блэкджек (а у игрока нет) — раздача заканчивается сразу (дилер «подсматривает» скрытую карту);
  * сплита и страховки нет. При базовой стратегии RTP ≈ 99,3% (преимущество дома ≈ 0,7%).
Состояние партии хранится в таблице blackjack_games (одна активная партия на игрока), поэтому после закрытия
приложения игрок возвращается к своей раздаче.
Любое списание/начисление идёт через core.adjust → журнал операций.
"""
import json

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import RNG, auth, db, now_iso, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/games/blackjack")

MAX_BET = 1_000_000
DECKS = 6
BJ_PAY = 1.5  # блэкджек платит 3:2 -> выплата 2.5 × ставка

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS blackjack_games (
        user_id INTEGER PRIMARY KEY, bet INTEGER NOT NULL, doubled INTEGER NOT NULL DEFAULT 0,
        shoe TEXT NOT NULL, player TEXT NOT NULL, dealer TEXT NOT NULL, created_at TEXT NOT NULL)""",
]


def init():
    conn = core.ctx.get_db()
    try:
        for q in SCHEMA:
            conn.execute(q)
        conn.commit()
    finally:
        conn.close()


# Карта — [ранг 1..13, масть 0..3]; 1 = туз, 11/12/13 = В/Д/К
def new_shoe():
    cards = [[r, s] for _ in range(DECKS) for r in range(1, 14) for s in range(4)]
    RNG.shuffle(cards)
    return cards


def value(hand):
    """-> (сумма, «мягкая» ли сумма). Туз считается за 11, пока нет перебора."""
    total = sum(min(c[0], 10) for c in hand)
    aces = sum(1 for c in hand if c[0] == 1)
    soft = False
    if aces and total + 10 <= 21:
        total += 10
        soft = True
    return total, soft


def is_blackjack(hand):
    return len(hand) == 2 and value(hand)[0] == 21


def _load(conn, uid):
    row = one(conn, "SELECT bet, doubled, shoe, player, dealer FROM blackjack_games WHERE user_id=?", (uid,))
    if not row:
        return None
    return {"bet": row[0], "doubled": bool(row[1]), "shoe": json.loads(row[2]), "player": json.loads(row[3]), "dealer": json.loads(row[4])}


def _save(conn, uid, g):
    conn.execute(
        "UPDATE blackjack_games SET doubled=?, shoe=?, player=?, dealer=? WHERE user_id=?",
        (1 if g["doubled"] else 0, json.dumps(g["shoe"]), json.dumps(g["player"]), json.dumps(g["dealer"]), uid),
    )


def _view(g):
    """Состояние для клиента: вторая карта дилера скрыта (null)."""
    p, soft = value(g["player"])
    up = min(g["dealer"][0][0], 10)
    return {
        "active": True, "bet": g["bet"], "doubled": g["doubled"],
        "player": g["player"], "player_total": p, "player_soft": soft,
        "dealer": [g["dealer"][0], None], "dealer_up": 11 if g["dealer"][0][0] == 1 else up,
        "can_double": len(g["player"]) == 2 and not g["doubled"],
    }


def _finish(conn, uid, g, outcome=None):
    """Дилер доигрывает (если нужно), считаем выплату, закрываем партию. -> ответ клиенту."""
    p_total, _ = value(g["player"])
    wager = g["bet"] * (2 if g["doubled"] else 1)
    if outcome is None:
        if p_total <= 21:
            while value(g["dealer"])[0] < 17:  # дилер стоит на любом 17
                g["dealer"].append(g["shoe"].pop())
        d_total = value(g["dealer"])[0]
        if p_total > 21:
            outcome = "bust"
        elif d_total > 21 or p_total > d_total:
            outcome = "win"
        elif p_total == d_total:
            outcome = "push"
        else:
            outcome = "lose"
    d_total = value(g["dealer"])[0]
    if outcome == "blackjack":
        payout = core.pay(wager, 1 + BJ_PAY)
    elif outcome == "win":
        payout = wager * 2
    elif outcome == "push":
        payout = wager
    else:
        payout = 0
    conn.execute("DELETE FROM blackjack_games WHERE user_id=?", (uid,))
    core.game_win(conn, uid, payout, "blackjack")
    core.log_round(conn, uid, "blackjack", wager, payout, payout / wager if wager else 0,
                   {"outcome": outcome, "player": g["player"], "dealer": g["dealer"], "doubled": g["doubled"]})
    core.game_done(conn, uid, "blackjack")
    if outcome in ("win", "blackjack"):
        core.bump(conn, uid, "blackjack_wins", 1)
    bal = core.balance_of(conn, uid)
    return {
        "ok": True, "done": True, "outcome": outcome, "bet": wager,
        "player": g["player"], "player_total": p_total, "dealer": g["dealer"], "dealer_total": d_total,
        "payout": payout, "profit": payout - wager, "balance": bal, "unlocked": core.pop_unlocked(),
    }


@router.get("/state")
def state(request: Request):
    user = auth(request)
    with db() as conn:
        g = _load(conn, int(user["id"]))
    return _view(g) if g else {"active": False}


@router.post("/start")
def start(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        if _load(conn, uid):
            raise HTTPException(400, "Сначала завершите текущую раздачу")
        core.game_bet(conn, uid, bet, "blackjack", "Ставка: Блэкджек")
        shoe = new_shoe()
        player = [shoe.pop(), shoe.pop()]
        dealer = [shoe.pop(), shoe.pop()]
        g = {"bet": bet, "doubled": False, "shoe": shoe, "player": player, "dealer": dealer}
        conn.execute(
            "INSERT INTO blackjack_games (user_id, bet, doubled, shoe, player, dealer, created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, bet, 0, json.dumps(shoe), json.dumps(player), json.dumps(dealer), now_iso()),
        )
        pbj, dbj = is_blackjack(player), is_blackjack(dealer)
        if pbj or dbj:  # дилер «подсмотрел» карту — раздача закончена сразу
            res = _finish(conn, uid, g, "push" if pbj and dbj else "blackjack" if pbj else "lose")
            return res
        bal = core.balance_of(conn, uid)
        view = _view(g)
    return {"ok": True, "done": False, "balance": bal, "state": view, "unlocked": core.pop_unlocked()}


def _active(conn, uid):
    g = _load(conn, uid)
    if not g:
        raise HTTPException(400, "Нет активной раздачи")
    return g


@router.post("/hit")
def hit(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        g = _active(conn, uid)
        g["player"].append(g["shoe"].pop())
        total = value(g["player"])[0]
        if total >= 21:  # перебор или ровно 21 — дальше брать нечего
            return _finish(conn, uid, g)
        _save(conn, uid, g)
        view = _view(g)
    return {"ok": True, "done": False, "state": view}


@router.post("/stand")
def stand(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        g = _active(conn, uid)
        return _finish(conn, uid, g)


@router.post("/double")
def double(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        g = _active(conn, uid)
        if len(g["player"]) != 2 or g["doubled"]:
            raise HTTPException(400, "Удвоить можно только на первых двух картах")
        core.game_bet(conn, uid, g["bet"], "blackjack", "Удвоение: Блэкджек")  # недостаточно баллов -> 400, партия не меняется
        g["doubled"] = True
        g["player"].append(g["shoe"].pop())
        return _finish(conn, uid, g)
