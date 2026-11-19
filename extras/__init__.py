"""
extras — новые возможности MSPBETS (игры, кейсы, краш-раунды, нарды, социальное, автоматические матчи).

Подключается из app.py тремя вызовами:
    extras.setup(get_db=..., validate=..., bot=..., admin_ids=..., webapp_url=..., bot_username=...)
    extras.register(app)
    await extras.startup() / await extras.shutdown()   # в lifespan
База (Turso), Render и Python остаются прежними: используются только новые таблицы
(CREATE TABLE IF NOT EXISTS) и две безопасные колонки (skins.weight, cases.active).
"""
import asyncio
import logging

from . import betting, cases, core, crash, feed, games, nards, social  # noqa: F401

log = logging.getLogger("mspbets.extras")
_tasks = []


def setup(get_db, validate, bot, admin_ids, webapp_url, bot_username):
    c = core.ctx
    c.get_db, c.validate, c.bot = get_db, validate, bot
    c.admin_ids, c.webapp_url, c.bot_username = set(admin_ids), webapp_url, bot_username


def register(app):
    for r in (cases.router, games.router, crash.router, nards.router, social.router, feed.router):
        app.include_router(r)


def _init():
    core.init_schema()
    cases.seed()


async def startup():
    core.ctx.loop = asyncio.get_running_loop()
    await asyncio.to_thread(_init)
    crash.engine.start_loop()
    _tasks.append(asyncio.create_task(social.scheduler()))
    _tasks.append(asyncio.create_task(feed.scheduler()))
    log.info("extras запущены: игры, кейсы, краш-раунды, нарды, турниры, автозагрузка матчей")


async def shutdown():
    for t in _tasks + [crash.engine.task]:
        if t:
            t.cancel()


async def handle_start(from_user, payload):
    """/start ref_<id> — привязка друга к пригласившему (только для новых игроков)."""
    if not payload.startswith("ref_") or not payload[4:].isdigit():
        return

    def _do():
        with core.db() as conn:
            core.register_referral(conn, from_user.id, int(payload[4:]))

    try:
        await asyncio.to_thread(_do)
    except Exception as e:
        log.warning("referral start failed: %s", e)
