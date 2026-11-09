import os
import json
import hmac
import hashlib
import logging
from urllib.parse import parse_qsl
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher
from aiogram.types import Update, WebAppInfo, MenuButtonWebApp
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mspbets")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
BOT_WEBAPP_URL = os.getenv("BOT_WEBAPP_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DATABASE_PATH = os.getenv("DATABASE_PATH", "mspbets.db")
ALLOW_DEV_AUTH = os.getenv("ALLOW_DEV_AUTH", "false").lower() == "true"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None
dp = Dispatcher()

def validate_init_data(init_data: str):
    if not BOT_TOKEN or not init_data:
        return None
    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", None)
        if not received_hash:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None
        auth_date = int(values.get("auth_date", "0"))
        if abs(datetime.now(timezone.utc).timestamp() - auth_date) > 86400:
            return None
        user = json.loads(values.get("user", "{}"))
        return user if user.get("id") else None
    except Exception:
        return None

async def current_user(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user = validate_init_data(init_data)
    if user:
        return user
    if ALLOW_DEV_AUTH:
        return {"id": int(os.getenv("DEV_USER_ID", "1")), "first_name": "Тестовый пользователь", "username": "test_user"}
    raise HTTPException(status_code=401, detail="Не удалось проверить Telegram. Откройте приложение через бота.")

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            balance INTEGER NOT NULL DEFAULT 1000, created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL,
            title TEXT NOT NULL, starts_at TEXT, status TEXT NOT NULL DEFAULT 'open',
            result TEXT, reward_multiplier REAL NOT NULL DEFAULT 2.0,
            created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL, choice TEXT NOT NULL, points INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
            UNIQUE(user_id, event_id)
        )""")
        await db.commit()

async def ensure_user(user):
    uid = int(user["id"])
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""INSERT OR IGNORE INTO users
            (user_id, username, first_name, balance, created_at)
            VALUES (?, ?, ?, 1000, ?)""",
            (uid, user.get("username"), user.get("first_name", "Игрок"), datetime.now(timezone.utc).isoformat()))
        await db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                         (user.get("username"), user.get("first_name", "Игрок"), uid))
        await db.commit()
    return uid

@dp.message()
async def start_message(message):
    if not BOT_WEBAPP_URL:
        await message.answer("MSPBETS запущен. Администратору нужно указать BOT_WEBAPP_URL.")
        return
    await message.answer(
        "🏆 <b>MSPBETS</b>\n\nДелай прогнозы на матчи, получай виртуальные баллы и поднимайся в рейтинге!\nБаллы не имеют денежной стоимости, их нельзя купить или вывести.",
        reply_markup=None
    )
    # The persistent menu button opens the Mini App.
    await bot.set_chat_menu_button(
        chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(text="Открыть MSPBETS", web_app=WebAppInfo(url=BOT_WEBAPP_URL))
    )
    await message.answer(f"Открой приложение по кнопке меню «Открыть MSPBETS».")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if bot and BOT_WEBAPP_URL and WEBHOOK_SECRET:
        webhook_url = f"{BOT_WEBAPP_URL}/telegram/webhook"
        await bot.set_webhook(webhook_url, secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
        log.info("Telegram webhook configured")
    yield
    if bot:
        await bot.session.close()

app = FastAPI(title="MSPBETS API", lifespan=lifespan)
app.mount("/web", StaticFiles(directory="web"), name="web")

@app.get("/")
async def index():
    return FileResponse("web/index.html")

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not bot:
        raise HTTPException(503, "Bot token is not configured")
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(403, "Invalid webhook secret")
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})

@app.get("/api/me")
async def me(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT user_id, username, first_name, balance FROM users WHERE user_id=?", (uid,))
        row = dict(await cur.fetchone())
        cur = await db.execute("SELECT COUNT(*) AS n FROM predictions WHERE user_id=?", (uid,))
        row["predictions_count"] = (await cur.fetchone())["n"]
    row["is_admin"] = uid in ADMIN_IDS
    return row

@app.get("/api/events")
async def events(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT e.*, p.choice AS my_choice, p.points AS my_points, p.status AS my_status
            FROM events e LEFT JOIN predictions p ON p.event_id=e.id AND p.user_id=?
            ORDER BY CASE WHEN e.status='open' THEN 0 ELSE 1 END, e.id DESC
        """, (uid,))
        return [dict(r) for r in await cur.fetchall()]

@app.post("/api/predict")
async def predict(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    try:
        event_id = int(body["event_id"])
        points = int(body["points"])
        choice = str(body["choice"])
    except Exception:
        raise HTTPException(400, "Проверьте данные прогноза")
    if points < 1 or points > 1000:
        raise HTTPException(400, "Можно использовать от 1 до 1000 баллов")
    if choice not in ("1", "X", "2"):
        raise HTTPException(400, "Недопустимый вариант")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            raise HTTPException(400, "Пользователь не найден")
        cur = await db.execute("SELECT status FROM events WHERE id=?", (event_id,))
        event = await cur.fetchone()
        if not event or event[0] != "open":
            await db.rollback()
            raise HTTPException(400, "Приём прогнозов закрыт")
        if row[0] < points:
            await db.rollback()
            raise HTTPException(400, "Недостаточно баллов")
        try:
            await db.execute("INSERT INTO predictions(user_id,event_id,choice,points,created_at) VALUES(?,?,?,?,?)",
                             (uid, event_id, choice, points, datetime.now(timezone.utc).isoformat()))
        except aiosqlite.IntegrityError:
            await db.rollback()
            raise HTTPException(409, "Вы уже сделали прогноз на этот матч")
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (points, uid))
        await db.commit()
    return {"ok": True, "message": "Прогноз принят"}

@app.get("/api/leaderboard")
async def leaderboard(request: Request):
    await current_user(request)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT first_name, username, balance FROM users ORDER BY balance DESC LIMIT 50")
        return [dict(r) for r in await cur.fetchall()]

@app.post("/api/admin/events")
async def create_event(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS:
        raise HTTPException(403, "Нет доступа")
    body = await request.json()
    sport = str(body.get("sport", "")).strip()
    title = str(body.get("title", "")).strip()
    starts_at = str(body.get("starts_at", "")).strip()
    try:
        multiplier = float(body.get("reward_multiplier", 2))
    except Exception:
        multiplier = 2
    if sport not in ("CS2", "Dota 2", "Футбол", "Теннис") or not title:
        raise HTTPException(400, "Укажите дисциплину и название матча")
    if not 1 <= multiplier <= 10:
        raise HTTPException(400, "Множитель должен быть от 1 до 10")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO events(sport,title,starts_at,reward_multiplier,created_at) VALUES(?,?,?,?,?)",
                         (sport, title, starts_at, multiplier, datetime.now(timezone.utc).isoformat()))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/events/{event_id}/settle")
async def settle_event(event_id: int, request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS:
        raise HTTPException(403, "Нет доступа")
    body = await request.json()
    result = str(body.get("result", ""))
    if result not in ("1", "X", "2"):
        raise HTTPException(400, "Результат должен быть 1, X или 2")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT status,reward_multiplier FROM events WHERE id=?", (event_id,))
        event = await cur.fetchone()
        if not event or event["status"] != "open":
            await db.rollback()
            raise HTTPException(400, "Матч уже рассчитан или не найден")
        await db.execute("UPDATE events SET status='settled', result=? WHERE id=?", (result, event_id))
        cur = await db.execute("SELECT user_id,points,choice FROM predictions WHERE event_id=?", (event_id,))
        predictions = await cur.fetchall()
        for p in predictions:
            if p["choice"] == result:
                reward = int(p["points"] * event["reward_multiplier"])
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, p["user_id"]))
                await db.execute("UPDATE predictions SET status='won' WHERE user_id=? AND event_id=?", (p["user_id"], event_id))
            else:
                await db.execute("UPDATE predictions SET status='lost' WHERE user_id=? AND event_id=?", (p["user_id"], event_id))
        await db.commit()
    return {"ok": True, "settled": len(predictions)}

@app.get("/health")
async def health():
    return {"status": "ok"}
