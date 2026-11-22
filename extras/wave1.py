"""
extras/wave1.py — подключение «первой волны» MSP 3.0:

  * accaperks.py  — бонус к экспрессу (+5% … +50%) и страховка экспресса (фрибет, если не зашло одно событие);
  * freebets.py   — фрибеты: ставка без списания баланса, игроку — чистый выигрыш;
  * seasonpass.py — сезонный пропуск: 30 уровней за ставки месяца (1-й — 50 000 PTS, каждый следующий +5 000), бесплатная и премиум-дорожки;
  * betcards.py   — ставки на новые рынки в общей «Истории ставок», карточка ставки, «Поделиться» ссылкой.

Подключается из extras/__init__.py тремя вызовами: wave1.init(), wave1.register(app), wave1.scheduler().
Только новые таблицы w1_* (CREATE TABLE IF NOT EXISTS), старые таблицы и расчёт ставок не меняются.
Фоновая сверка — раз в минуту и только короткими транзакциями (сервер Render free).
Отключается так же, как plus: PLUS_BG=0.
"""
import asyncio
import logging
import time

from fastapi import APIRouter, Request

from . import accaperks, betcards, core, freebets, plus, seasonpass
from .core import auth, db

log = logging.getLogger("mspbets.wave1")
router = APIRouter(prefix="/api/v2/wave1")

ROUTERS = (router, accaperks.router, freebets.router, seasonpass.router, betcards.router)
SCHEMA = [*freebets.SCHEMA, *accaperks.SCHEMA, *seasonpass.SCHEMA]
TICK = 60


def init():
    with core.db() as conn:
        for s in SCHEMA:
            conn.execute(s)
    with core.db() as conn:
        accaperks.init(conn, plus.meta_get, plus.meta_set)


def register(app):
    for r in ROUTERS:
        app.include_router(r)


@router.get("/summary")
def summary(request: Request):
    """Одним запросом для фронта: правила бонуса/страховки, активные фрибеты, короткий статус пропуска."""
    uid = int(auth(request)["id"])
    with db() as conn:
        fbs = freebets.active(conn, uid)
        s, pts, lvl, prem, claimed = seasonpass._state(conn, uid)
    return {"rules": accaperks.rules(), "freebets": fbs,
            "pass": {"title": s["title"], "level": lvl, "max_level": seasonpass.MAX_LEVEL, "points": pts["points"],
                     "level_points": seasonpass.progress(pts["points"], lvl)[1], "into_level": seasonpass.progress(pts["points"], lvl)[0], "premium": prem, "ends_at": s["end"],
                     "claimable": len(seasonpass._claimable(lvl, prem, claimed))}}


def tick():
    n = 0
    try:
        n += freebets.reconcile()
    except Exception as e:
        log.warning("wave1 freebets: %s", e)
    try:
        n += accaperks.process(plus.meta_get)
    except Exception as e:
        log.warning("wave1 perks: %s", e)
    return n


async def scheduler():
    if not plus.BG:
        log.info("Фоновые задачи wave1 отключены (PLUS_BG=0)")
        return
    await asyncio.sleep(20)
    while True:
        t = time.monotonic()
        try:
            await asyncio.to_thread(tick)
        except Exception as e:
            log.exception("wave1 scheduler: %s", e)
        await asyncio.sleep(max(5.0, TICK - (time.monotonic() - t)))
