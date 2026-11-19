"""
extras/nards.py — Нарды (короткие, Backgammon) против бота на баллы.

Полные правила: 24 пункта, бар, удары, выброс шашек, дубль = 4 хода,
обязанность сыграть максимум возможных кубиков и больший кубик, если играется только один.
Кубики бросает сервер (криптостойкий ГСЧ) — и за игрока, и за бота; клиент ничего не решает.
Выигрыш: ставка × 1.9 (RTP против равного соперника 95%). Партия хранится в Turso,
поэтому не теряется при перезапуске сервера; можно сдаться (ставка сгорает).

Позиция: пункты 0..23, >0 шашки игрока (идёт 23→0, дом 0..5), <0 шашки бота (идёт 0→23, дом 18..23).
BAR = 24 (источник хода «с бара»), OFF = -1 (выброс).
"""
import json
from functools import lru_cache

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import RNG, auth, db, now_iso, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/nards")

BAR, OFF = 24, -1
WIN_MULT = 1.9
MAX_BET = 1_000_000


def sgn(s):
    return 1 if s == 0 else -1


def initial_pos():
    pts = [0] * 24
    pts[23], pts[12], pts[7], pts[5] = 2, 5, 3, 5
    pts[0], pts[11], pts[16], pts[18] = -2, -5, -3, -5
    return (tuple(pts), (0, 0), (0, 0))


def all_home(pos, s):
    pts, bar, _ = pos
    if bar[s]:
        return False
    sg = sgn(s)
    rng = range(6, 24) if s == 0 else range(0, 18)
    return all(pts[i] * sg <= 0 for i in rng)


def moves_for_die(pos, s, die):
    pts, bar, _ = pos
    sg = sgn(s)
    res = []
    if bar[s] > 0:
        dest = 24 - die if s == 0 else die - 1
        if pts[dest] * sg >= -1:
            res.append((BAR, dest))
        return res
    home = all_home(pos, s)
    step = -1 if s == 0 else 1
    for i in range(24):
        if pts[i] * sg <= 0:
            continue
        dest = i + step * die
        if 0 <= dest <= 23:
            if pts[dest] * sg >= -1:
                res.append((i, dest))
        elif home:
            dist = i + 1 if s == 0 else 24 - i
            if die == dist:
                res.append((i, OFF))
            elif die > dist:
                farther = range(i + 1, 24) if s == 0 else range(0, i)
                if not any(pts[j] * sg > 0 for j in farther):
                    res.append((i, OFF))
    return res


def apply_move(pos, s, mv):
    pts, bar, off = pos
    pts, bar, off = list(pts), list(bar), list(off)
    sg = sgn(s)
    frm, to = mv
    hit = False
    if frm == BAR:
        bar[s] -= 1
    else:
        pts[frm] -= sg
    if to == OFF:
        off[s] += 1
    else:
        if pts[to] * sg == -1:
            pts[to] = 0
            bar[1 - s] += 1
            hit = True
        pts[to] += sg
    return (tuple(pts), tuple(bar), tuple(off)), hit


def _rest(dice, die):
    d = list(dice)
    d.remove(die)
    return tuple(d)


@lru_cache(maxsize=200_000)
def max_play(pos, s, dice):
    if not dice:
        return 0
    best = 0
    for die in set(dice):
        rest = _rest(dice, die)
        for mv in moves_for_die(pos, s, die):
            best = max(best, 1 + max_play(apply_move(pos, s, mv)[0], s, rest))
            if best == len(dice):
                return best
    return best


def legal_first(pos, s, dice):
    """Допустимые первые ходы [(mv, die)] с учётом «играть максимум кубиков»."""
    dice = tuple(sorted(dice))
    total = max_play(pos, s, dice)
    if total == 0:
        return []
    out = []
    for die in set(dice):
        rest = _rest(dice, die)
        for mv in moves_for_die(pos, s, die):
            if 1 + max_play(apply_move(pos, s, mv)[0], s, rest) == total:
                out.append((mv, die))
    if len(dice) == 2 and dice[0] != dice[1] and total == 1:
        big = max(dice)
        if any(d == big for _, d in out):
            out = [c for c in out if c[1] == big]
    return out


def pips(pos, s):
    pts, bar, _ = pos
    sg = sgn(s)
    total = bar[s] * 25
    for i in range(24):
        if pts[i] * sg > 0:
            total += pts[i] * sg * ((i + 1) if s == 0 else (24 - i))
    return total


def evaluate(pos, s):
    """Эвристика для бота (чем больше — тем лучше позиция стороны s)."""
    pts, bar, off = pos
    sg, o = sgn(s), 1 - s
    score = (pips(pos, o) - pips(pos, s)) * 1.0
    score += 14 * off[s] - 14 * off[o]
    score += 14 * bar[o] - 16 * bar[s]
    run = best_run = 0
    for k in range(24):
        i = k if s == 1 else 23 - k
        mine = pts[i] * sg
        if mine == 1:
            score -= 6.0 + (4 if bar[o] else 0)
        elif mine >= 2:
            score += 3.0
            home = (i >= 18) if s == 1 else (i <= 5)
            if home:
                score += 2.0
        if mine >= 2:
            run += 1
            best_run = max(best_run, run)
        else:
            run = 0
        if pts[i] * sg == -1:
            score += 2.0
    score += 4.0 * best_run if best_run >= 3 else 0
    return score


def bot_sequence(pos, dice):
    """Лучшая полная последовательность ходов бота: [(mv, die)]."""
    s = 1
    results = {}
    seen = set()

    def rec(p, rest, seq):
        key = (p, rest)
        if key in seen:
            return
        seen.add(key)
        cands = legal_first(p, s, rest)
        if not cands:
            if p not in results:
                results[p] = seq
            return
        for mv, die in cands:
            np_, _ = apply_move(p, s, mv)
            rec(np_, _rest(tuple(sorted(rest)), die), seq + [(mv, die)])

    rec(pos, tuple(sorted(dice)), [])
    if not results:
        return []
    best, best_score = None, -1e18
    for p, seq in results.items():
        sc = evaluate(p, s) + RNG.random() * 0.5
        if sc > best_score:
            best, best_score = seq, sc
    return best


# ------------------------- состояние партии -------------------------
def roll_dice():
    r = [RNG.randrange(1, 7), RNG.randrange(1, 7)]
    return r, ([r[0]] * 4 if r[0] == r[1] else list(r))


def to_pos(st):
    return (tuple(st["pts"]), tuple(st["bar"]), tuple(st["off"]))


def from_pos(st, pos):
    st["pts"], st["bar"], st["off"] = list(pos[0]), list(pos[1]), list(pos[2])


def new_state():
    pos = initial_pos()
    a = b = 0
    while a == b:
        a, b = RNG.randrange(1, 7), RNG.randrange(1, 7)
    st = {"pts": list(pos[0]), "bar": [0, 0], "off": [0, 0], "turn": "p" if a > b else "b", "winner": None}
    st["roll"] = [a, b]
    st["dice"] = [a, b]
    return st


def run(st, events):
    """Прокручивает ходы до момента, когда нужен ход игрока (или партия окончена)."""
    guard = 0
    while st["winner"] is None and guard < 200:
        guard += 1
        pos = to_pos(st)
        if st["turn"] == "p":
            if st["dice"] and legal_first(pos, 0, st["dice"]):
                return
            if st["dice"] and st.get("announced") != "p":
                events.append({"who": "p", "dice": st["roll"], "moves": [], "pass": True})
            st["turn"] = "b"
            st["roll"], st["dice"] = roll_dice()
            st["announced"] = None
        else:
            seq = bot_sequence(pos, st["dice"])
            mv_log = []
            for mv, die in seq:
                pos, hit = apply_move(pos, 1, mv)
                mv_log.append({"frm": mv[0], "to": mv[1], "die": die, "hit": hit})
            from_pos(st, pos)
            events.append({"who": "b", "dice": st["roll"], "moves": mv_log, "pass": not seq})
            if st["off"][1] == 15:
                st["winner"] = "b"
                return
            st["turn"] = "p"
            st["roll"], st["dice"] = roll_dice()
            st["announced"] = None


def view(st, bet):
    pos = to_pos(st)
    legal = []
    if st["winner"] is None and st["turn"] == "p" and st["dice"]:
        seen = set()
        for mv, die in legal_first(pos, 0, st["dice"]):
            if mv not in seen:
                seen.add(mv)
                legal.append({"frm": mv[0], "to": mv[1]})
    return {
        "pts": st["pts"], "bar": st["bar"], "off": st["off"], "turn": st["turn"], "dice": st["dice"], "roll": st["roll"],
        "winner": st["winner"], "legal": legal, "bet": bet, "win_mult": WIN_MULT,
        "pips": [pips(pos, 0), pips(pos, 1)],
    }


def _load(conn, uid):
    row = one(conn, "SELECT bet, state FROM nards_games WHERE user_id=?", (uid,))
    if not row:
        raise HTTPException(400, "Нет активной партии")
    return row[0], json.loads(row[1])


def _finish(conn, uid, bet, st, resigned=False):
    won = st["winner"] == "p"
    payout = core.pay(bet, WIN_MULT) if won else 0
    conn.execute("DELETE FROM nards_games WHERE user_id=?", (uid,))
    core.game_win(conn, uid, payout, "nards", "Нарды: победа над ботом")
    core.log_round(conn, uid, "nards", bet, payout, WIN_MULT if won else 0, {"winner": st["winner"], "resigned": resigned, "off": st["off"]})
    core.game_done(conn, uid, "nards")
    if won:
        core.bump(conn, uid, "nards_won", 1)
    return payout


@router.get("/state")
def state(request: Request):
    user = auth(request)
    with db() as conn:
        row = one(conn, "SELECT bet, state FROM nards_games WHERE user_id=?", (int(user["id"]),))
    if not row:
        return {"active": False}
    return {"active": True, "state": view(json.loads(row[1]), row[0])}


@router.post("/start")
def start(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    bet = core.check_amount(body.get("bet"), 1, MAX_BET)
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        if one(conn, "SELECT 1 FROM nards_games WHERE user_id=?", (uid,)):
            raise HTTPException(400, "Сначала завершите текущую партию")
        core.game_bet(conn, uid, bet, "nards", "Ставка: Нарды")
        core.quest_event(conn, uid, "nards_play", 1)
        st = new_state()
        events = []
        opening = list(st["roll"])
        if st["turn"] == "b":
            st["dice"] = list(opening)
        run(st, events)
        conn.execute("INSERT INTO nards_games (user_id, bet, state, created_at) VALUES (?,?,?,?)", (uid, bet, json.dumps(st), now_iso()))
        bal = core.balance_of(conn, uid)
    return {"ok": True, "balance": bal, "opening": opening, "events": events, "state": view(st, bet), "unlocked": core.pop_unlocked()}


@router.post("/move")
def move(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    frm, to = int(body.get("frm", -9)), int(body.get("to", -9))
    with user_lock(uid), db() as conn:
        bet, st = _load(conn, uid)
        if st["winner"] is not None or st["turn"] != "p":
            raise HTTPException(400, "Сейчас не ваш ход")
        pos = to_pos(st)
        cands = [(mv, d) for mv, d in legal_first(pos, 0, st["dice"]) if mv == (frm, to)]
        if not cands:
            raise HTTPException(400, "Недопустимый ход")
        mv, die = min(cands, key=lambda c: c[1])
        pos, hit = apply_move(pos, 0, mv)
        from_pos(st, pos)
        st["dice"].remove(die)
        events = [{"who": "p", "move": {"frm": frm, "to": to, "die": die, "hit": hit}}]
        if st["off"][0] == 15:
            st["winner"] = "p"
        else:
            run(st, events)
        payout, bal = 0, None
        if st["winner"] is not None:
            payout = _finish(conn, uid, bet, st)
            bal = core.balance_of(conn, uid)
        else:
            conn.execute("UPDATE nards_games SET state=? WHERE user_id=?", (json.dumps(st), uid))
    return {"ok": True, "events": events, "state": view(st, bet), "payout": payout, "balance": bal, "unlocked": core.pop_unlocked()}


@router.post("/resign")
def resign(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        bet, st = _load(conn, uid)
        st["winner"] = "b"
        _finish(conn, uid, bet, st, resigned=True)
        bal = core.balance_of(conn, uid)
    return {"ok": True, "balance": bal}
