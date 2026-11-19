"""
extras/crash.py — Краш по раундам для всех игроков.

  * один общий раунд на всех: фаза ставок (8 c) → полёт → крэш → пауза;
  * множитель считается от времени: m(t) = exp(0.10 * t); клиент рисует график сам,
    сервер рассылает только смены фаз (WebSocket /ws/crash) — крэш приходит мгновенно;
  * точка краша заранее определена seed'ом; хеш seed показывается ДО раунда,
    сам seed — ПОСЛЕ (можно проверить честность). RTP = 96% (шанс m ≥ x равен 0.96/x);
  * ставки и кэшауты других игроков в ленте, чат, автовывод;
  * баланс — только через core.adjust (журнал). Если сервер перезапустился посреди раунда,
    неразыгранные ставки возвращаются.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import math
import secrets
import threading
import time
from collections import deque

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect

from . import core
from .core import all_, auth, db, display_name, now_iso, one, touch_user, user_lock

log = logging.getLogger("mspbets.crash")
router = APIRouter()

GROWTH = 0.10
BET_WINDOW = 8.0
CRASH_PAUSE = 4.5
EDGE = 0.04
MAX_CRASH = 1000.0
MAX_BET = 1_000_000


def crash_from_seed(seed: str) -> float:
    h = hmac.new(seed.encode(), b"mspbets-crash", hashlib.sha256).hexdigest()
    r = int(h[:13], 16) / 16 ** 13  # [0, 1)
    cp = math.floor(100 * (1 - EDGE) / (1 - r)) / 100
    return float(min(MAX_CRASH, max(1.0, cp)))


def mult_at(t: float) -> float:
    return math.exp(GROWTH * max(0.0, t))


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.phase = "idle"
        self.round_id = 0
        self.seed = ""
        self.seed_hash = ""
        self.cp = 1.0
        self.bet_end = 0.0
        self.start = 0.0
        self.crash_ts = 0.0
        self.bets = {}
        self.history = deque(maxlen=20)
        self.chat = deque(maxlen=60)
        self.last_chat = {}
        self.clients = set()
        self.task = None

    # -------------------------- снимок состояния --------------------------
    def snapshot(self):
        crashed = self.phase == "crashed"
        return {
            "t": "state",
            "server_time": time.time(),
            "phase": self.phase,
            "round_id": self.round_id,
            "seed_hash": self.seed_hash,
            "bet_end": self.bet_end,
            "start": self.start,
            "growth": GROWTH,
            "crash_point": self.cp if crashed else None,
            "seed": self.seed if crashed else None,
            "online": len(self.clients),
            "history": list(self.history),
            "bets": sorted(
                (
                    {"uid": b["uid"], "name": b["name"], "amount": b["amount"], "cashed": b["cashed"], "payout": b["payout"],
                     "auto": b["auto"] if b["cashed"] else None}
                    for b in list(self.bets.values())
                ),
                key=lambda x: -x["amount"],
            )[:60],
        }

    async def _broadcast(self, payload=None):
        data = json.dumps(payload or self.snapshot(), ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def push(self, payload=None):
        """Разослать обновление (можно звать из любого потока)."""
        loop = core.ctx.loop
        if loop and self.clients:
            try:
                asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)
            except Exception:
                pass

    # -------------------------- ставки --------------------------
    def place_bet(self, user, amount, auto):
        uid = int(user["id"])
        amount = core.check_amount(amount, 1, MAX_BET)
        if auto is not None:
            try:
                auto = round(float(auto), 2)
            except Exception:
                raise HTTPException(400, "Неверный автовывод")
            if not (1.01 <= auto <= MAX_CRASH):
                raise HTTPException(400, "Автовывод от 1.01x")
        with user_lock(uid), self.lock:
            if self.phase != "betting" or time.time() >= self.bet_end:
                raise HTTPException(400, "Приём ставок закрыт — дождитесь следующего раунда")
            if uid in self.bets:
                raise HTTPException(400, "Ставка в этом раунде уже сделана")
            with db() as conn:
                touch_user(conn, user)
                core.game_bet(conn, uid, amount, "crash", f"Ставка: Краш #{self.round_id}")
                core.game_done(conn, uid, "crash")
                conn.execute(
                    "INSERT INTO crash_bets (round_id, user_id, amount, auto_cashout, created_at) VALUES (?,?,?,?,?)",
                    (self.round_id, uid, amount, auto, now_iso()),
                )
                bal = core.balance_of(conn, uid)
            self.bets[uid] = {"uid": uid, "name": display_name(user), "amount": amount, "auto": auto, "cashed": None, "payout": 0}
        self.push()
        return {"ok": True, "balance": bal, "round_id": self.round_id, "unlocked": core.pop_unlocked()}

    def cash(self, uid, forced=None):
        with self.lock:
            if self.phase != "running":
                raise HTTPException(400, "Сейчас нельзя забрать выигрыш")
            b = self.bets.get(uid)
            if not b or b["cashed"]:
                raise HTTPException(400, "Нет активной ставки")
            now = time.time()
            if forced is None and now >= self.crash_ts:
                raise HTTPException(400, "Раунд уже завершён")
            m = forced if forced is not None else math.floor(mult_at(now - self.start) * 100) / 100
            m = max(1.0, min(m, self.cp))
            payout = core.pay(b["amount"], m)
            with db() as conn:
                bal = core.adjust(conn, uid, payout, "game_win", f"Краш #{self.round_id}: забрал x{m:.2f}", ref=f"crash:{self.round_id}")
                conn.execute(
                    "UPDATE crash_bets SET cashed_at=?, payout=?, status='won' WHERE round_id=? AND user_id=? AND status='open'",
                    (m, payout, self.round_id, uid),
                )
                core.log_round(conn, uid, "crash", b["amount"], payout, m, {"round": self.round_id, "auto": b["auto"]})
                core.set_max(conn, uid, "biggest_win", payout)
                core.set_max(conn, uid, "crash_best_x", int(m))
                if m >= 2:
                    core.quest_event(conn, uid, "crash_2x", 1)
            b["cashed"], b["payout"] = m, payout
        self.push()
        return {"ok": True, "mult": m, "payout": payout, "balance": bal, "unlocked": core.pop_unlocked()}

    # -------------------------- чат --------------------------
    def post_chat(self, user, text):
        uid = int(user["id"])
        text = " ".join(str(text or "").split())[:200]
        if not text:
            raise HTTPException(400, "Пустое сообщение")
        now = time.time()
        if now - self.last_chat.get(uid, 0) < 2:
            raise HTTPException(429, "Не так быстро")
        self.last_chat[uid] = now
        name = display_name(user)
        with db() as conn:
            mid = conn.execute("INSERT INTO chat_messages (user_id, name, text, created_at) VALUES (?,?,?,?)", (uid, name, text, now_iso())).lastrowid
        msg = {"id": mid, "uid": uid, "name": name, "text": text, "ts": now}
        self.chat.append(msg)
        self.push({"t": "chat", "msg": msg})
        return {"ok": True}

    # -------------------------- жизненный цикл --------------------------
    def _recover(self, min_age):
        """Вернуть ставки прерванных раундов (старше min_age секунд)."""
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - min_age))
        with db() as conn:
            rows = all_(conn, "SELECT id, user_id, amount, round_id FROM crash_bets WHERE status='open' AND created_at<?", (cutoff,))
            for bid, uid, amount, rid in rows:
                cur = conn.execute("UPDATE crash_bets SET status='refunded' WHERE id=? AND status='open'", (bid,))
                if core.changed(cur, conn) == 1:
                    core.adjust(conn, uid, amount, "refund", f"Возврат: раунд Краша #{rid} прерван", ref=f"crash:{rid}")
            conn.execute("UPDATE crash_rounds SET status='aborted' WHERE status='live' AND created_at<?", (cutoff,))
            hist = all_(conn, "SELECT id, crash_point FROM crash_rounds WHERE status='done' ORDER BY id DESC LIMIT 20")
            msgs = all_(conn, "SELECT id, user_id, name, text, created_at FROM chat_messages ORDER BY id DESC LIMIT 40")
        return rows, hist, msgs

    def _new_round(self):
        seed = secrets.token_hex(16)
        cp = crash_from_seed(seed)
        sh = hashlib.sha256(seed.encode()).hexdigest()
        with db() as conn:
            rid = conn.execute(
                "INSERT INTO crash_rounds (seed, seed_hash, crash_point, created_at) VALUES (?,?,?,?)", (seed, sh, cp, now_iso())
            ).lastrowid
        with self.lock:
            self.round_id, self.seed, self.seed_hash, self.cp = rid, seed, sh, cp
            self.bets = {}
            self.bet_end = time.time() + BET_WINDOW
            self.start = self.bet_end
            self.crash_ts = self.start + math.log(cp) / GROWTH
            self.phase = "betting"

    def _finish(self):
        with self.lock:
            self.phase = "crashed"
            losers = [b for b in self.bets.values() if not b["cashed"]]
            self.history.append({"id": self.round_id, "cp": self.cp})
        try:
            with db() as conn:
                conn.execute("UPDATE crash_bets SET status='lost' WHERE round_id=? AND status='open'", (self.round_id,))
                conn.execute("UPDATE crash_rounds SET status='done' WHERE id=?", (self.round_id,))
                for b in losers:
                    core.log_round(conn, b["uid"], "crash", b["amount"], 0, 0, {"round": self.round_id, "crash": self.cp})
        except Exception as e:
            log.exception("crash finish failed: %s", e)

    def _autos(self, m):
        with self.lock:
            due = [b for b in self.bets.values() if not b["cashed"] and b["auto"] and m >= b["auto"]]
        for b in due:
            try:
                self.cash(b["uid"], forced=b["auto"])
            except HTTPException:
                pass
            except Exception as e:
                log.exception("auto cashout failed: %s", e)

    async def run(self):
        try:
            rows, hist, msgs = await asyncio.to_thread(self._recover, 60)
            for h in reversed(hist):
                self.history.append({"id": h[0], "cp": h[1]})
            for m in reversed(msgs):
                self.chat.append({"id": m[0], "uid": m[1], "name": m[2], "text": m[3], "ts": 0})
            if rows:
                log.info("Краш: возвращено ставок прерванных раундов: %s", len(rows))
            asyncio.get_running_loop().call_later(90, lambda: asyncio.create_task(asyncio.to_thread(self._recover, 60)))
        except Exception as e:
            log.exception("crash recover failed: %s", e)
        while True:
            try:
                await asyncio.to_thread(self._new_round)
                await self._broadcast()
                await asyncio.sleep(max(0, self.bet_end - time.time()))
                with self.lock:
                    self.phase = "running"
                await self._broadcast()
                while time.time() < self.crash_ts:
                    if self._has_autos():
                        await asyncio.to_thread(self._autos, mult_at(time.time() - self.start))
                    await asyncio.sleep(0.05)
                await asyncio.to_thread(self._finish)
                await self._broadcast()
                await asyncio.sleep(CRASH_PAUSE)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("crash loop error: %s", e)
                await asyncio.sleep(3)

    def _has_autos(self):
        m = mult_at(time.time() - self.start)
        return any((not b["cashed"]) and b["auto"] and m >= b["auto"] for b in list(self.bets.values()))

    def start_loop(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())


engine = Engine()


# ------------------------------- HTTP -------------------------------
@router.get("/api/v2/crash/state")
def state(request: Request, since: int = 0):
    auth(request)
    snap = engine.snapshot()
    snap["chat"] = [m for m in list(engine.chat) if m["id"] > since]
    return snap


@router.post("/api/v2/crash/bet")
def bet(request: Request, body: dict = Body(...)):
    return engine.place_bet(auth(request), body.get("amount"), body.get("auto") or None)


@router.post("/api/v2/crash/cashout")
def cashout(request: Request):
    return engine.cash(int(auth(request)["id"]))


@router.post("/api/v2/crash/chat")
def chat(request: Request, body: dict = Body(...)):
    return engine.post_chat(auth(request), body.get("text"))


@router.get("/api/v2/crash/rounds")
def rounds(request: Request):
    auth(request)
    with db() as conn:
        rows = all_(conn, "SELECT id, crash_point, seed_hash, seed FROM crash_rounds WHERE status='done' ORDER BY id DESC LIMIT 20")
    return [{"id": r[0], "crash_point": r[1], "seed_hash": r[2], "seed": r[3]} for r in rows]


@router.websocket("/ws/crash")
async def ws_crash(ws: WebSocket):
    user = core.ctx.validate(ws.query_params.get("init", ""))
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    engine.clients.add(ws)
    try:
        await ws.send_text(json.dumps({"t": "hello", "chat": list(engine.chat)}, ensure_ascii=False))
        await ws.send_text(json.dumps(engine.snapshot(), ensure_ascii=False))
        while True:
            await ws.receive_text()  # клиент шлёт ping; нужен, чтобы заметить отключение
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        engine.clients.discard(ws)
