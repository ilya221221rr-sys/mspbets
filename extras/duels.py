"""
extras/duels.py — дуэли 1 на 1 между игроками на баллы.

Как это работает:
  * игрок создаёт вызов: игра (Монетка или Кости), ставка, для монетки — своя сторона;
    ставка сразу списывается и «замораживается» до конца дуэли;
  * вызов виден всем в лобби (или только по коду — приватная дуэль для друга);
  * соперник принимает вызов — с него списывается такая же ставка, результат определяется мгновенно;
  * победитель забирает банк минус комиссия заведения DUEL_FEE (по умолчанию 5%);
  * вызов можно отменить, пока его не приняли (ставка возвращается); через DUEL_TTL_HOURS (24 ч)
    непринятый вызов сгорает и ставка возвращается автоматически.

Честность (provably fair): при создании сервер генерирует секрет seed и сразу показывает его SHA-256.
Результат = HMAC-SHA256(seed, "<id дуэли>:<id соперника>") — соперник неизвестен в момент создания,
а seed неизвестен игрокам до конца. После дуэли seed раскрывается — любой может перепроверить.
  * Монетка: первый байт HMAC чётный → «орёл», иначе «решка».
  * Кости: у каждого по два кубика (байты HMAC % 6 + 1), больше сумма — победа; ничья → переброс
    (HMAC с суффиксом ":1", ":2", …).
"""
import hashlib
import hmac
import html
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import core, plusdb
from .core import all_, auth, db, money, now_iso, one, user_lock

router = APIRouter(prefix="/api/v2/duels")

FEE = float(os.getenv("DUEL_FEE", "0.05"))
TTL_H = int(os.getenv("DUEL_TTL_HOURS", "24"))
MIN_BET = 10
MAX_BET = 1_000_000
MAX_OPEN = 5
GAMES = {"coin": "Монетка", "dice": "Кости"}
SIDES = {"heads": "Орёл", "tails": "Решка"}
CODE_ABC = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS duels (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, game TEXT NOT NULL,
        amount INTEGER NOT NULL, pick TEXT, private INTEGER NOT NULL DEFAULT 0,
        creator_id INTEGER NOT NULL, creator_name TEXT, opponent_id INTEGER, opponent_name TEXT,
        seed TEXT NOT NULL, seed_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
        winner_id INTEGER, prize INTEGER NOT NULL DEFAULT 0, detail TEXT,
        created_at TEXT NOT NULL, finished_at TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_duels_code ON duels(code)",
    "CREATE INDEX IF NOT EXISTS idx_duels_status ON duels(status, id)",
    "CREATE INDEX IF NOT EXISTS idx_duels_creator ON duels(creator_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_duels_opp ON duels(opponent_id, id)",
    # результат, который создатель ещё не видел (анимация броска покажется ему при следующем опросе)
    "CREATE TABLE IF NOT EXISTS duel_unseen (duel_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_duel_unseen_user ON duel_unseen(user_id)",
]

_COLS = ("id, code, game, amount, pick, private, creator_id, creator_name, opponent_id, opponent_name, "
         "seed, seed_hash, status, winner_id, prize, detail, created_at, finished_at")
_KEYS = [k.strip() for k in _COLS.split(",")]


def prize_for(amount):
    return int(2 * int(amount) * (1 - FEE))


def _hm(seed, msg):
    return hmac.new(seed.encode(), msg.encode(), hashlib.sha256).digest()


def resolve(seed, duel_id, opponent_id, game, pick):
    """→ (creator_wins: bool, detail: dict). Детерминированно — можно перепроверить по раскрытому seed."""
    base = f"{duel_id}:{opponent_id}"
    if game == "coin":
        side = "heads" if _hm(seed, base)[0] % 2 == 0 else "tails"
        return side == pick, {"side": side}
    rounds = []
    for k in range(50):
        h = _hm(seed, base if k == 0 else f"{base}:{k}")
        c, o = [h[0] % 6 + 1, h[1] % 6 + 1], [h[2] % 6 + 1, h[3] % 6 + 1]
        rounds.append({"creator": c, "opponent": o})
        if sum(c) != sum(o):
            return sum(c) > sum(o), {"rounds": rounds}
    return True, {"rounds": rounds}  # недостижимо на практике


def _row(r, viewer=None):
    d = dict(zip(_KEYS, r))
    done = d["status"] == "done"
    try:
        detail = json.loads(d["detail"]) if d["detail"] else None
    except Exception:
        detail = None
    exp = None
    try:
        exp = (datetime.fromisoformat(d["created_at"]) + timedelta(hours=TTL_H)).isoformat()
    except Exception:
        pass
    mine = viewer is not None and viewer in (d["creator_id"], d["opponent_id"])
    return {
        "id": d["id"], "code": d["code"] if (not d["private"] or mine) else None, "game": d["game"], "game_name": GAMES.get(d["game"], d["game"]),
        "amount": d["amount"], "prize": prize_for(d["amount"]), "pick": d["pick"], "private": bool(d["private"]),
        "creator_id": d["creator_id"], "creator_name": d["creator_name"] or "Игрок",
        "opponent_id": d["opponent_id"], "opponent_name": d["opponent_name"], "status": d["status"],
        "winner_id": d["winner_id"], "detail": detail, "seed_hash": d["seed_hash"], "seed": d["seed"] if done else None,
        "created_at": d["created_at"], "finished_at": d["finished_at"], "expires_at": exp,
        "mine": viewer == d["creator_id"],
    }


def _new_code(conn):
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ABC) for _ in range(6))
        if not one(conn, "SELECT 1 FROM duels WHERE code=?", (code,)):
            return code
    raise HTTPException(500, "Не удалось создать код, попробуйте ещё раз")


def _cutoff():
    return (datetime.now(timezone.utc) - timedelta(hours=TTL_H)).isoformat()


def expire_old():
    """Фоновая задача: вернуть ставки по непринятым вызовам старше TTL."""
    n = 0
    with db() as conn:
        rows = all_(conn, "SELECT id, creator_id, amount, game FROM duels WHERE status='open' AND created_at<?", (_cutoff(),))
        notes = []
        for did, uid, amount, game in rows:
            cur = conn.execute("UPDATE duels SET status='expired', finished_at=? WHERE id=? AND status='open'", (now_iso(), did))
            if core.changed(cur, conn) == 1:
                core.adjust(conn, uid, amount, "refund", f"Дуэль #{did}: вызов никто не принял", ref=f"duel:{did}")
                notes.append((uid, f"⌛ <b>Дуэль #{did}</b> ({GAMES.get(game, game)}) никто не принял за {TTL_H} ч.\nСтавка <b>{money(amount)} PTS</b> возвращена."))
                n += 1
        core.notify_many(conn, notes)
    return n


@router.get("")
def lobby(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        open_ = all_(conn, f"SELECT {_COLS} FROM duels WHERE status='open' AND private=0 AND creator_id<>? AND created_at>=? ORDER BY id DESC LIMIT 30", (uid, _cutoff()))
        mine = all_(conn, f"SELECT {_COLS} FROM duels WHERE status='open' AND creator_id=? ORDER BY id DESC", (uid,))
        hist = all_(conn, f"SELECT {_COLS} FROM duels WHERE status<>'open' AND (creator_id=? OR opponent_id=?) ORDER BY id DESC LIMIT 15", (uid, uid))
        st = one(conn, """SELECT COUNT(*), COALESCE(SUM(CASE WHEN winner_id=? THEN 1 ELSE 0 END),0) FROM duels
                          WHERE status='done' AND (creator_id=? OR opponent_id=?)""", (uid, uid, uid))
        live = all_(conn, f"SELECT {_COLS} FROM duels WHERE status='done' ORDER BY id DESC LIMIT 8")
    return {
        "open": [_row(r, uid) for r in open_], "mine": [_row(r, uid) for r in mine], "history": [_row(r, uid) for r in hist],
        "live": [_row(r) for r in live],
        "stats": {"played": st[0], "won": st[1]},
        "config": {"fee": FEE, "fee_pct": round(FEE * 100, 1), "min": MIN_BET, "max": MAX_BET, "ttl_h": TTL_H, "max_open": MAX_OPEN,
                   "games": GAMES, "bot": core.ctx.bot_username},
    }


@router.get("/code/{code}")
def by_code(code: str, request: Request):
    user = auth(request)
    with db() as conn:
        r = one(conn, f"SELECT {_COLS} FROM duels WHERE code=?", (code.strip().upper()[:12],))
    if not r:
        raise HTTPException(404, "Дуэль не найдена")
    d = _row(r, int(user["id"]))
    d["code"] = code.strip().upper()  # знающий код может принять приватный вызов
    return d


@router.post("/create")
def create(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    game = str(body.get("game", ""))
    if game not in GAMES:
        raise HTTPException(400, "Выберите игру")
    pick = str(body.get("pick") or "") if game == "coin" else None
    if game == "coin" and pick not in SIDES:
        raise HTTPException(400, "Выберите сторону монеты")
    amount = core.check_amount(body.get("amount"), MIN_BET, MAX_BET)
    private = 1 if body.get("private") else 0
    with user_lock(uid), db() as conn:
        plusdb.ensure_user(user)
        n_open = one(conn, "SELECT COUNT(*) FROM duels WHERE creator_id=? AND status='open'", (uid,))[0]
        if n_open >= MAX_OPEN:
            raise HTTPException(400, f"Не больше {MAX_OPEN} открытых вызовов")
        seed = secrets.token_hex(16)
        code = _new_code(conn)
        did = conn.execute(
            "INSERT INTO duels (code, game, amount, pick, private, creator_id, creator_name, seed, seed_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (code, game, amount, pick, private, uid, core.display_name(user), seed, hashlib.sha256(seed.encode()).hexdigest(), now_iso()),
        ).lastrowid
        core.adjust(conn, uid, -amount, "game_bet", f"Дуэль #{did}: ставка ({GAMES[game]})", ref=f"duel:{did}")
        bal = core.balance_of(conn, uid)
        r = one(conn, f"SELECT {_COLS} FROM duels WHERE id=?", (did,))
    return {"ok": True, "duel": _row(r, uid), "balance": bal}


@router.post("/cancel")
def cancel(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    try:
        did = int(body.get("id"))
    except Exception:
        raise HTTPException(400, "Неверная дуэль")
    with user_lock(uid), db() as conn:
        r = one(conn, "SELECT creator_id, amount FROM duels WHERE id=?", (did,))
        if not r or r[0] != uid:
            raise HTTPException(404, "Дуэль не найдена")
        cur = conn.execute("UPDATE duels SET status='cancelled', finished_at=? WHERE id=? AND status='open'", (now_iso(), did))
        if core.changed(cur, conn) != 1:
            raise HTTPException(409, "Вызов уже принят или закрыт")
        bal = core.adjust(conn, uid, r[1], "refund", f"Дуэль #{did}: вызов отменён", ref=f"duel:{did}")
    return {"ok": True, "balance": bal}


@router.post("/join")
def join(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    code = str(body.get("code") or "").strip().upper()[:12]
    if not code:
        raise HTTPException(400, "Укажите код дуэли")
    with user_lock(uid), db() as conn:
        plusdb.ensure_user(user)
        r = one(conn, f"SELECT {_COLS} FROM duels WHERE code=?", (code,))
        if not r:
            raise HTTPException(404, "Дуэль не найдена")
        d = dict(zip(_KEYS, r))
        if d["status"] != "open" or d["created_at"] < _cutoff():
            raise HTTPException(409, "Вызов уже принят или закрыт")
        if d["creator_id"] == uid:
            raise HTTPException(400, "Нельзя принять свой вызов")
        name = core.display_name(user)
        cur = conn.execute("UPDATE duels SET status='done', opponent_id=?, opponent_name=?, finished_at=? WHERE id=? AND status='open'",
                           (uid, name, now_iso(), d["id"]))
        if core.changed(cur, conn) != 1:
            raise HTTPException(409, "Вызов уже принят")
        core.adjust(conn, uid, -d["amount"], "game_bet", f"Дуэль #{d['id']}: ставка ({GAMES[d['game']]})", ref=f"duel:{d['id']}")
        creator_wins, detail = resolve(d["seed"], d["id"], uid, d["game"], d["pick"])
        winner = d["creator_id"] if creator_wins else uid
        loser = uid if creator_wins else d["creator_id"]
        prize = prize_for(d["amount"])
        conn.execute("UPDATE duels SET winner_id=?, prize=?, detail=? WHERE id=?", (winner, prize, json.dumps(detail), d["id"]))
        core.adjust(conn, winner, prize, "game_win", f"Дуэль #{d['id']}: победа", ref=f"duel:{d['id']}")
        core.set_max(conn, winner, "biggest_win", prize)
        for p in (d["creator_id"], uid):  # XP, задания, турниры — только за сыгранные дуэли
            core.on_wager(conn, p, d["amount"], "duel")
            core.log_round(conn, p, "duel", d["amount"], prize if p == winner else 0, (prize / d["amount"]) if p == winner else 0,
                           {"duel": d["id"], "game": d["game"]})
            core.game_done(conn, p, "duel")
        conn.execute("INSERT OR IGNORE INTO duel_unseen (duel_id, user_id) VALUES (?, ?)", (d["id"], d["creator_id"]))
        r2 = one(conn, f"SELECT {_COLS} FROM duels WHERE id=?", (d["id"],))
        bal = core.balance_of(conn, uid)
        won_txt = f"🏆 Ты победил! <b>+{money(prize)} PTS</b>" if creator_wins else f"😔 Победил соперник. Ставка {money(d['amount'])} PTS проиграна."
        if d["game"] == "coin":
            res_txt = f"Выпало: {SIDES[detail['side']]} (твоя сторона — {SIDES.get(d['pick'], '?')})"
        else:
            last = detail["rounds"][-1]
            res_txt = f"Кости: у тебя {sum(last['creator'])}, у соперника {sum(last['opponent'])}"
        core.notify_many(conn, [(d["creator_id"], f"⚔️ <b>Дуэль #{d['id']}</b> — {html.escape(name)} принял вызов!\n{res_txt}\n{won_txt}")])
    out = _row(r2, uid)
    return {"ok": True, "duel": out, "win": winner == uid, "prize": prize if winner == uid else 0, "loser": loser,
            "balance": bal, "unlocked": core.pop_unlocked()}


@router.get("/pulse")
def pulse(request: Request):
    """Лёгкий опрос для создателя: сколько открытых вызовов и какие дуэли сыграны, а он ещё не видел результат."""
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        n_open = one(conn, "SELECT COUNT(*) FROM duels WHERE status='open' AND creator_id=?", (uid,))[0]
        rows = all_(conn, f"""SELECT {', '.join('d.' + k for k in _KEYS)} FROM duel_unseen u JOIN duels d ON d.id=u.duel_id
                              WHERE u.user_id=? AND d.status='done' ORDER BY d.id LIMIT 5""", (uid,))
    return {"open": n_open, "unseen": [_row(r, uid) for r in rows]}


@router.post("/seen")
def seen(request: Request, body: dict = Body(default={})):
    """Создатель посмотрел анимацию результата."""
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        if body.get("id") is not None:
            try:
                did = int(body["id"])
            except Exception:
                raise HTTPException(400, "Неверный id")
            conn.execute("DELETE FROM duel_unseen WHERE user_id=? AND duel_id=?", (uid, did))
        else:
            conn.execute("DELETE FROM duel_unseen WHERE user_id=?", (uid,))
    return {"ok": True}
