"""
extras/crash.py — обычный (одиночный) Краш.

Раунд запускается ТОЛЬКО когда игрок сам нажал «Играть»:
  * нет общего цикла раундов, WebSocket, чата и фоновых задач — сервер ничего не делает,
    пока никто не играет, и не нагружает Turso лишними запросами;
  * точка краша определяется сразу при ставке (seed → HMAC), игроку показывается хеш seed,
    сам seed раскрывается после окончания раунда (можно проверить честность);
  * множитель растёт от времени: m(t) = exp(0.10 * t); график рисует клиент;
  * «забрать» / автовывод проверяет сервер по своим часам; RTP = 96%;
  * состояние активного раунда лежит в памяти (быстро), в Turso пишется только
    ставка (1 транзакция) и итог (1 транзакция);
  * если сервер перезапустился посреди раунда — ставка возвращается (см. recover()).
"""
import asyncio
import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from calendar import timegm

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import all_, auth, db, now_iso, touch_user, user_lock

log = logging.getLogger("mspbets.crash")
router = APIRouter()

GROWTH = 0.10
EDGE = 0.04
MAX_CRASH = 1000.0
MAX_BET = 1_000_000
WAIT_SECONDS = 20.0  # сколько максимум держим long-poll /wait (потом клиент переспрашивает)

ACTIVE = {}  # uid -> раунд (в памяти)
_guard = threading.Lock()
_bg = set()


def crash_from_seed(seed: str) -> float:
    h = hmac.new(seed.encode(), b"mspbets-crash", hashlib.sha256).hexdigest()
    r = int(h[:13], 16) / 16 ** 13  # [0, 1)
    cp = math.floor(100 * (1 - EDGE) / (1 - r)) / 100
    return float(min(MAX_CRASH, max(1.0, cp)))


def mult_at(t: float) -> float:
    return math.exp(GROWTH * max(0.0, t))


def _floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


def _view(r, balance=None):
    """Что видит игрок. Точка краша и seed — только когда раунд закончен."""
    done = r["status"] != "running"
    v = {
        "ok": True,
        "phase": r["status"],  # running | crashed | cashed
        "round_id": r["id"],
        "seed_hash": r["seed_hash"],
        "server_time": time.time(),
        "start": r["start"],
        "growth": GROWTH,
        "bet": r["amount"],
        "auto": r["auto"],
        "mult": r["mult"],
        "payout": r["payout"],
        "crash_point": r["cp"] if done else None,
        "seed": r["seed"] if done else None,
    }
    if balance is not None:
        v["balance"] = balance
    return v


def _event_ts(r):
    """Когда у раунда наступит событие: автовывод (если он раньше краша) или краш."""
    if r["auto"] and r["auto"] <= r["cp"]:
        return r["start"] + math.log(r["auto"]) / GROWTH, True
    return r["start"] + math.log(r["cp"]) / GROWTH, False


# ------------------------------- запись итога в БД -------------------------------
def _db_finish(r, status):
    """Итог раунда в Turso. status: won | lost."""
    uid = r["uid"]
    try:
        with db() as conn:
            if status == "won":
                core.adjust(conn, uid, r["payout"], "game_win", f"Краш #{r['id']}: забрал x{r['mult']:.2f}", ref=f"crash:{r['id']}")
                conn.execute(
                    "UPDATE crash_bets SET cashed_at=?, payout=?, status='won' WHERE round_id=? AND user_id=? AND status='open'",
                    (r["mult"], r["payout"], r["id"], uid),
                )
                core.log_round(conn, uid, "crash", r["amount"], r["payout"], r["mult"], {"round": r["id"], "auto": r["auto"], "crash": r["cp"]})
                core.set_max(conn, uid, "biggest_win", r["payout"])
                core.set_max(conn, uid, "crash_best_x", int(r["mult"]))
                if r["mult"] >= 2:
                    core.quest_event(conn, uid, "crash_2x", 1)
            else:
                conn.execute("UPDATE crash_bets SET status='lost' WHERE round_id=? AND user_id=? AND status='open'", (r["id"], uid))
                core.log_round(conn, uid, "crash", r["amount"], 0, 0, {"round": r["id"], "crash": r["cp"]})
            conn.execute("UPDATE crash_rounds SET status='done' WHERE id=?", (r["id"],))
    except Exception as e:
        log.exception("crash finish failed: %s", e)


def _db_finish_bg(r, status):
    """Проигрыш можно записать уже после ответа игроку — он не ждёт Turso."""
    t = asyncio.get_running_loop().run_in_executor(None, _db_finish, r, status)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


# ------------------------------- логика раунда -------------------------------
def _resolve_locked(r, now):
    """Если у раунда наступило событие (краш/автовывод) — зафиксировать результат в памяти.
    Возвращает 'won' | 'lost' | None (ничего не изменилось). Вызывать под user_lock."""
    if r["status"] != "running":
        return None
    ts, is_auto = _event_ts(r)
    if now < ts:
        return None
    if is_auto:
        r["status"], r["mult"] = "cashed", float(r["auto"])
        r["payout"] = core.pay(r["amount"], r["mult"])
        return "won"
    r["status"], r["mult"], r["payout"] = "crashed", 0.0, 0
    return "lost"


def start_round(user, amount, auto):
    uid = int(user["id"])
    amount = core.check_amount(amount, 1, MAX_BET)
    if auto is not None:
        try:
            auto = round(float(auto), 2)
        except Exception:
            raise HTTPException(400, "Неверный автовывод")
        if not (1.01 <= auto <= MAX_CRASH):
            raise HTTPException(400, "Автовывод от 1.01x")
    with user_lock(uid):
        old = ACTIVE.get(uid)
        if old and old["status"] == "running":
            res = _resolve_locked(old, time.time())
            if res is None:
                raise HTTPException(400, "Раунд уже идёт — заберите выигрыш")
            _db_finish(old, res)  # прошлый раунд закончился, пока клиент не спрашивал
        seed = secrets.token_hex(16)
        cp = crash_from_seed(seed)
        sh = hashlib.sha256(seed.encode()).hexdigest()
        with db() as conn:  # одна транзакция: ставка + раунд
            touch_user(conn, user)
            core.game_bet(conn, uid, amount, "crash", "Ставка: Краш")
            core.game_done(conn, uid, "crash")
            rid = conn.execute("INSERT INTO crash_rounds (seed, seed_hash, crash_point, created_at) VALUES (?,?,?,?)", (seed, sh, cp, now_iso())).lastrowid
            conn.execute(
                "INSERT INTO crash_bets (round_id, user_id, amount, auto_cashout, created_at) VALUES (?,?,?,?,?)",
                (rid, uid, amount, auto, now_iso()),
            )
            bal = core.balance_of(conn, uid)
        r = {"id": rid, "uid": uid, "seed": seed, "seed_hash": sh, "cp": cp, "amount": amount, "auto": auto,
             "status": "running", "mult": 0.0, "payout": 0, "start": time.time()}  # старт — ПОСЛЕ записи в БД
        with _guard:
            if len(ACTIVE) > 2000:
                for k in [k for k, v in ACTIVE.items() if v["status"] != "running"][:1000]:
                    ACTIVE.pop(k, None)
            ACTIVE[uid] = r
        out = _view(r, bal)
        out["unlocked"] = core.pop_unlocked()
        return out


def cash_out(uid):
    with user_lock(uid):
        r = ACTIVE.get(uid)
        if not r or r["status"] != "running":
            raise HTTPException(400, "Нет активного раунда")
        now = time.time()
        res = _resolve_locked(r, now)  # автовывод/краш могли наступить раньше клика
        if res is None:
            m = max(1.0, _floor2(mult_at(now - r["start"])))
            r["status"], r["mult"], r["payout"] = "cashed", m, core.pay(r["amount"], m)
            res = "won"
        if res == "lost":
            _db_finish(r, "lost")
            raise HTTPException(400, "Раунд уже завершён — крэш")
        _db_finish(r, "won")
        with db() as conn:
            bal = core.balance_of(conn, uid)
        out = _view(r, bal)
        out["unlocked"] = core.pop_unlocked()
        return out


def settle_due(uid):
    """Состояние раунда игрока; если событие уже наступило — зафиксировать его."""
    with user_lock(uid):
        r = ACTIVE.get(uid)
        if not r:
            return None
        res = _resolve_locked(r, time.time())
        if res:
            _db_finish(r, res)
        return _view(r)


def recover():
    """При старте сервера: незавершённые раунды (память потеряна) — возврат ставки,
    а если по времени раунд уже должен был закончиться — фиксируем проигрыш."""
    try:
        with db() as conn:
            rows = all_(
                conn,
                "SELECT b.id, b.user_id, b.amount, b.round_id, b.created_at, r.crash_point "
                "FROM crash_bets b JOIN crash_rounds r ON r.id=b.round_id WHERE b.status='open'",
            )
            n = 0
            for bid, uid, amount, rid, created, cp in rows:
                try:
                    started = timegm(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    started = 0
                over = time.time() > started + math.log(max(cp, 1.0)) / GROWTH + 5
                if over:
                    conn.execute("UPDATE crash_bets SET status='lost' WHERE id=? AND status='open'", (bid,))
                else:
                    cur = conn.execute("UPDATE crash_bets SET status='refunded' WHERE id=? AND status='open'", (bid,))
                    if core.changed(cur, conn) == 1:
                        core.adjust(conn, uid, amount, "refund", f"Возврат: раунд Краша #{rid} прерван", ref=f"crash:{rid}")
                        n += 1
                conn.execute("UPDATE crash_rounds SET status='done' WHERE id=?", (rid,))
            conn.execute("UPDATE crash_rounds SET status='aborted' WHERE status='live'")
        if n:
            log.info("Краш: возвращено ставок прерванных раундов: %s", n)
    except Exception as e:
        log.exception("crash recover failed: %s", e)


# ------------------------------- HTTP -------------------------------
@router.post("/api/v2/crash/start")
def api_start(request: Request, body: dict = Body(...)):
    return start_round(auth(request), body.get("bet", body.get("amount")), body.get("auto") or None)


@router.post("/api/v2/crash/cashout")
def api_cashout(request: Request):
    return cash_out(int(auth(request)["id"]))


@router.get("/api/v2/crash/state")
def api_state(request: Request):
    """Текущий раунд игрока (если он есть) — чтобы вернуться в игру после перезахода."""
    uid = int(auth(request)["id"])
    return settle_due(uid) or {"ok": True, "phase": "idle", "server_time": time.time()}


@router.get("/api/v2/crash/wait")
async def api_wait(request: Request, round: int = 0):
    """Long-poll: отвечает в момент краша/автовывода (клиент не знает точку краша заранее
    и не опрашивает сервер каждые N мс). Пока раунд идёт — без запросов к БД."""
    uid = int(auth(request)["id"])
    deadline = time.time() + WAIT_SECONDS
    while True:
        r = ACTIVE.get(uid)
        if not r or r["id"] != round or r["status"] != "running":
            break
        now = time.time()
        ts, _ = _event_ts(r)
        if now >= ts or now >= deadline:
            break
        await asyncio.sleep(max(0.02, min(ts - now, deadline - now, 0.25)))
    r = ACTIVE.get(uid)
    if not r or r["id"] != round:
        return {"ok": True, "phase": "idle", "server_time": time.time()}
    if r["status"] == "running" and time.time() >= _event_ts(r)[0]:
        with user_lock(uid):
            res = _resolve_locked(r, time.time())
        if res == "lost":
            _db_finish_bg(r, "lost")  # игрок видит крэш сразу, запись в БД — в фоне
        elif res == "won":
            await asyncio.to_thread(_db_finish, r, "won")
        out = _view(r)
        if res == "won":
            out["unlocked"] = core.pop_unlocked()
        return out
    return _view(r)


@router.get("/api/v2/crash/rounds")
def rounds(request: Request):
    """Последние завершённые раунды игрока (seed раскрыт — можно проверить честность)."""
    uid = int(auth(request)["id"])
    with db() as conn:
        rows = all_(
            conn,
            "SELECT r.id, r.crash_point, r.seed_hash, r.seed, b.amount, b.payout, b.status FROM crash_bets b "
            "JOIN crash_rounds r ON r.id=b.round_id WHERE b.user_id=? AND b.status IN ('won','lost') ORDER BY b.id DESC LIMIT 20",
            (uid,),
        )
    return [{"id": r[0], "crash_point": r[1], "seed_hash": r[2], "seed": r[3], "bet": r[4], "payout": r[5], "status": r[6]} for r in rows]
