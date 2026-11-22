"""
extras/roulette.py — европейская рулетка (одно зеро, 37 ячеек).

Ставки и выплаты (выплата включает ставку):
  * число 0–36 ............ x36  (35 к 1)
  * дюжина / колонна ....... x3   (2 к 1)
  * красное / чёрное, чёт / нечет, 1–18 / 19–36 ... x2 (1 к 1)
На зеро проигрывают все внешние ставки. RTP = 36/37 ≈ 97,3% для любой ставки.
Число выпадает криптостойким ГСЧ на сервере (core.RNG), клиент только рисует колесо.
Списание/выплата — через core.game_bet / core.game_win (журнал, XP, задания, турниры).
"""
import json

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import RNG, all_, auth, db, touch_user, user_lock

router = APIRouter(prefix="/api/v2/roulette")

MAX_BET = 1_000_000  # на весь спин
MAX_SPOTS = 60
RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
WHEEL = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26]
RTP = round(36 / 37 * 100, 2)


def color(n):
    return "green" if n == 0 else "red" if n in RED else "black"


def parse(key):
    """Ключ ставки → (множитель, функция «выиграла ли при числе n», подпись)."""
    key = str(key)
    if key.startswith("n:"):
        try:
            v = int(key[2:])
        except ValueError:
            v = -1
        if 0 <= v <= 36:
            return 36, (lambda n, v=v: n == v), f"Число {v}"
    if key.startswith("dozen:") and key[6:] in ("1", "2", "3"):
        d = int(key[6:])
        return 3, (lambda n, d=d: n != 0 and (n - 1) // 12 + 1 == d), f"Дюжина {d}"
    if key.startswith("col:") and key[4:] in ("1", "2", "3"):
        c = int(key[4:])
        return 3, (lambda n, c=c: n != 0 and (n - 1) % 3 + 1 == c), f"Колонна {c}"
    simple = {
        "red": (lambda n: n in RED, "Красное"),
        "black": (lambda n: n != 0 and n not in RED, "Чёрное"),
        "even": (lambda n: n != 0 and n % 2 == 0, "Чёт"),
        "odd": (lambda n: n % 2 == 1, "Нечет"),
        "low": (lambda n: 1 <= n <= 18, "1–18"),
        "high": (lambda n: 19 <= n <= 36, "19–36"),
    }
    if key in simple:
        f, label = simple[key]
        return 2, f, label
    raise HTTPException(400, f"Неизвестная ставка: {key[:20]}")


@router.get("/config")
def config(request: Request):
    auth(request)
    return {"wheel": WHEEL, "red": sorted(RED), "rtp": RTP, "max_bet": MAX_BET, "max_spots": MAX_SPOTS,
            "payouts": {"number": 36, "dozen": 3, "column": 3, "even_money": 2}}


@router.get("/history")
def history(request: Request):
    user = auth(request)
    with db() as conn:
        rows = all_(conn, "SELECT detail, bet, payout FROM game_rounds WHERE user_id=? AND game='roulette' ORDER BY id DESC LIMIT 20", (int(user["id"]),))
    out = []
    for d, bet, payout in rows:
        try:
            n = int(json.loads(d or "{}").get("n"))
        except Exception:
            continue
        out.append({"n": n, "color": color(n), "bet": bet, "payout": payout})
    return out


@router.post("/spin")
def spin(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    raw = body.get("bets")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(400, "Сделайте ставку")
    if len(raw) > MAX_SPOTS:
        raise HTTPException(400, f"Максимум {MAX_SPOTS} позиций за спин")
    bets = {}
    for b in raw:
        if not isinstance(b, dict):
            raise HTTPException(400, "Неверная ставка")
        key = str(b.get("key", ""))
        parse(key)  # проверка ключа
        bets[key] = bets.get(key, 0) + core.check_amount(b.get("amount"), 1, MAX_BET)
    total = sum(bets.values())
    if total > MAX_BET:
        raise HTTPException(400, f"Максимум {core.money(MAX_BET)} PTS за спин")
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        core.game_bet(conn, uid, total, "roulette", f"Ставка: Рулетка ({len(bets)} поз.)")
        n = RNG.randrange(37)
        payout, wins = 0, []
        for key, amount in bets.items():
            mult, won, _ = parse(key)
            if won(n):
                payout += core.pay(amount, mult)
                wins.append(key)
        core.game_win(conn, uid, payout, "roulette", f"Выигрыш: Рулетка, выпало {n}")
        core.log_round(conn, uid, "roulette", total, payout, payout / total, {"n": n, "bets": bets})
        core.game_done(conn, uid, "roulette")
        bal = core.balance_of(conn, uid)
    return {"ok": True, "number": n, "color": color(n), "payout": payout, "total": total, "wins": wins,
            "balance": bal, "unlocked": core.pop_unlocked()}
