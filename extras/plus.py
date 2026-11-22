"""
extras/plus.py — подключение новых модулей «казино / БК»:

  * markets.py  — доп. рынки на матчи (тоталы, форы, двойной шанс, обе забьют, точный счёт …);
  * roulette.py — европейская рулетка;
  * duels.py    — дуэли 1 на 1 между игроками;
  * jackpot.py  — прогрессивный джекпот Mini / Major / Grand;
  * vipback.py  — VIP-кэшбэк и рейкбек.

Подключается из extras/__init__.py тремя вызовами: plus.init(), plus.register(app), plus.scheduler().
Только новые таблицы (CREATE TABLE IF NOT EXISTS), старые таблицы и код не меняются.
Фоновую задачу можно выключить переменной PLUS_BG=0 (например, в тестах).
"""
import asyncio
import logging
import os
import time

from . import core, duels, jackpot, markets, roulette, vipback
from .core import one

log = logging.getLogger("mspbets.plus")

ROUTERS = (markets.router, roulette.router, duels.router, jackpot.router, vipback.router)
BG = os.getenv("PLUS_BG", "1") not in ("0", "false", "False", "")

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS plus_meta (key TEXT PRIMARY KEY, value TEXT)",
    *markets.SCHEMA, *duels.SCHEMA, *jackpot.SCHEMA, *vipback.SCHEMA,
]


class CursorMoved(Exception):
    """Курсор журнала уже сдвинут другим процессом — транзакция откатывается."""


def meta_get(conn, key):
    r = one(conn, "SELECT value FROM plus_meta WHERE key=?", (key,))
    return r[0] if r else None


def meta_set(conn, key, value, expect=None):
    if expect is None:
        conn.execute("INSERT INTO plus_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        return
    cur = conn.execute("UPDATE plus_meta SET value=? WHERE key=? AND value=?", (str(value), key, str(expect)))
    if core.changed(cur, conn) != 1:
        raise CursorMoved(key)


def init():
    with core.db() as conn:
        for s in SCHEMA:
            conn.execute(s)
    with core.db() as conn:
        jackpot.init(conn, meta_get, meta_set)
        vipback.init(conn, meta_get, meta_set)


def register(app):
    for r in ROUTERS:
        app.include_router(r)


def jackpot_tick():
    try:
        return jackpot.process(meta_get, meta_set)
    except CursorMoved:
        return 0


async def scheduler():
    if not BG:
        log.info("Фоновые задачи plus отключены (PLUS_BG=0)")
        return
    await asyncio.sleep(10)
    last = {"duels": 0.0, "settle": 0.0, "lines": 0.0}
    while True:
        t = time.time()
        try:
            await asyncio.to_thread(jackpot_tick)
            if t - last["duels"] >= 60:
                last["duels"] = t
                await asyncio.to_thread(duels.expire_old)
            if t - last["settle"] >= 300:
                last["settle"] = t
                await asyncio.to_thread(markets.settle_pending)
            if t - last["lines"] >= 1800:
                last["lines"] = t
                await asyncio.to_thread(markets.refresh_lines)
        except Exception as e:
            log.exception("plus scheduler: %s", e)
        await asyncio.sleep(15)
