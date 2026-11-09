import os
import json
import hmac
import hashlib
import logging
import random
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
BOT_WEBAPP_URL = os.getenv("BOT_WEBAPP_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DATABASE_PATH = os.getenv("DATABASE_PATH", "mspbets.db")
ALLOW_DEV_AUTH = os.getenv("ALLOW_DEV_AUTH", "false").lower() == "true"

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None
dp = Dispatcher()

def validate_init_data(init_data: str):
    if not BOT_TOKEN or not init_data: return None
    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", None)
        if not received_hash: return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash): return None
        auth_date = int(values.get("auth_date", "0"))
        if abs(datetime.now(timezone.utc).timestamp() - auth_date) > 86400: return None
        user = json.loads(values.get("user", "{}"))
        return user if user.get("id") else None
    except Exception: return None

async def current_user(request: Request):
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    user = validate_init_data(init_data)
    if user: return user
    if ALLOW_DEV_AUTH: return {"id": int(os.getenv("DEV_USER_ID", "1")), "first_name": "Тест", "username": "test"}
    raise HTTPException(status_code=401, detail="Откройте через Telegram")

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            balance INTEGER NOT NULL DEFAULT 1000, created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL,
            title TEXT NOT NULL, starts_at TEXT, status TEXT NOT NULL DEFAULT 'open',
            result TEXT, odds_1 REAL NOT NULL DEFAULT 2.0, odds_x REAL, odds_2 REAL NOT NULL DEFAULT 2.0,
            created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL, choice TEXT NOT NULL, points INTEGER NOT NULL,
            locked_odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            price INTEGER NOT NULL, image_url TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS skins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER,
            name TEXT NOT NULL, rarity TEXT NOT NULL,
            image_url TEXT NOT NULL, price INTEGER NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            skin_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'owned',
            created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS crash_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            bet_amount INTEGER NOT NULL, multiplier REAL NOT NULL, win_amount INTEGER NOT NULL, created_at TEXT NOT NULL
        )""")
        await db.commit()
        
        cur = await db.execute("SELECT COUNT(*) FROM cases")
        if (await cur.fetchone())[0] == 0:
            cases_data = [
                (1, "Горячая Аркана", 600, "https://i.imgur.com/QRp0iKe.png"),
                (2, "Аркана 100%", 2000, "https://i.imgur.com/e1wXFiA.png"),
                (3, "Шейх", 2500, "https://i.imgur.com/LPHDRsY.png"),
                (4, "Старый Бог", 900, "https://i.imgur.com/CuUjs92.png"),
                (5, "The International", 2000, "https://i.imgur.com/aJOka5b.png")
            ]
            for c in cases_data: await db.execute("INSERT INTO cases (id, name, price, image_url) VALUES (?, ?, ?, ?)", c)
            
            skins_data = [
                (1, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
                (1, "Fiery Soul", "Rare", 1600, "https://i.imgur.com/AQiM6I8.png"),
                (1, "Kindred", "Common", 160, "https://i.imgur.com/G7XpkYs.png"),
                (2, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
                (2, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
                (3, "Аркана на тб", "Rare", 2500, "https://i.imgur.com/T9M1BIX.png"),
                (4, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
                (5, "Аркана на пуджа", "Common", 1600, "https://placehold.co/100x100?text=Pudge")
            ]
            for s in skins_data: await db.execute("INSERT INTO skins (case_id, name, rarity, price, image_url) VALUES (?, ?, ?, ?, ?)", s)
            await db.commit()

async def ensure_user(user):
    uid = int(user["id"])
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""INSERT OR IGNORE INTO users (user_id, username, first_name, balance, created_at)
            VALUES (?, ?, ?, 1000, ?)""", (uid, user.get("username"), user.get("first_name", "Игрок"), datetime.now(timezone.utc).isoformat()))
        await db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (user.get("username"), user.get("first_name", "Игрок"), uid))
        await db.commit()
    return uid

@dp.message()
async def start_message(message):
    if not BOT_WEBAPP_URL: return
    await message.answer("🏆 <b>MSPBETS</b>\n\nДелай прогнозы и поднимайся в рейтинге!", reply_markup=None)
    await bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=MenuButtonWebApp(text="Открыть MSPBETS", web_app=WebAppInfo(url=BOT_WEBAPP_URL)))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if bot and BOT_WEBAPP_URL and WEBHOOK_SECRET:
        await bot.set_webhook(f"{BOT_WEBAPP_URL}/telegram/webhook", secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    yield
    if bot: await bot.session.close()

app = FastAPI(title="MSPBETS API", lifespan=lifespan)
app.mount("/web", StaticFiles(directory="web"), name="web")

@app.get("/")
async def index(): return FileResponse("web/index.html")

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    if not bot: raise HTTPException(503)
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET: raise HTTPException(403)
    update = Update.model_validate(await request.json(), context={"bot": bot})
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
    row["is_admin"] = uid in ADMIN_IDS
    return row

@app.get("/api/events")
async def events(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM events ORDER BY CASE WHEN status='open' THEN 0 ELSE 1 END, id DESC")
        return [dict(r) for r in await cur.fetchall()]

@app.post("/api/predict")
async def predict(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    event_id = int(body["event_id"]); points = int(body["points"]); choice = str(body["choice"])
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        cur = await db.execute("SELECT sport, status, odds_1, odds_x, odds_2 FROM events WHERE id=?", (event_id,))
        event = await cur.fetchone()
        
        if not event or event["status"] != "open": raise HTTPException(400, "Закрыто")
        
        odds_map = {"1": event["odds_1"], "X": event["odds_x"], "2": event["odds_2"]}
        locked_odds = odds_map.get(choice)
        if not locked_odds: raise HTTPException(400, "Неверный исход")
        if row["balance"] < points: raise HTTPException(400, "Мало баллов")
        
        await db.execute("""INSERT INTO predictions (user_id, event_id, choice, points, locked_odds, created_at) 
                            VALUES (?, ?, ?, ?, ?, ?)""", (uid, event_id, choice, points, locked_odds, datetime.now(timezone.utc).isoformat()))
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (points, uid))
        await db.commit()
    return {"ok": True}

@app.get("/api/history")
async def get_history(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT p.id, p.choice, p.points, p.locked_odds, p.status, p.created_at, e.title, e.sport, e.result
            FROM predictions p JOIN events e ON p.event_id = e.id
            WHERE p.user_id = ? ORDER BY p.id DESC LIMIT 50
        """, (uid,))
        return [dict(r) for r in await cur.fetchall()]

@app.post("/api/crash/play")
async def play_crash(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    bet = int(body.get("amount", 0))
    cashout = float(body.get("cashout", 0)) 
    
    if bet < 1: raise HTTPException(400, "Мин. ставка 1")
    
    rand = random.random()
    if rand < 0.5: crash_point = round(random.uniform(1.0, 1.5), 2)
    else: crash_point = round(random.uniform(1.5, 15.0), 2)
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row or row[0] < bet: raise HTTPException(400, "Мало баллов")
        
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (bet, uid))
        
        win_amount = 0
        if cashout > 0 and cashout <= crash_point:
            win_amount = int(bet * cashout)
            await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (win_amount, uid))
            
        await db.execute("INSERT INTO crash_history (user_id, bet_amount, multiplier, win_amount, created_at) VALUES (?,?,?,?,?)", 
                         (uid, bet, crash_point, win_amount, datetime.now(timezone.utc).isoformat()))
        await db.commit()
        
    return {"ok": True, "crash_point": crash_point, "win": win_amount > 0, "win_amount": win_amount}

@app.get("/api/cases")
async def get_cases(request: Request):
    await current_user(request)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM cases")
        return [dict(r) for r in await cur.fetchall()]

@app.post("/api/cases/open")
async def open_case(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    case_id = int(body.get("case_id", 0))
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT price FROM cases WHERE id=?", (case_id,))
        case = await cur.fetchone()
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row or row["balance"] < case["price"]: raise HTTPException(400, "Мало баллов")
        
        roll = random.randint(1, 100)
        rarity = "Common" if roll <= 60 else "Rare" if roll <= 85 else "Immortal"
        
        cur = await db.execute("SELECT id, name, image_url, price FROM skins WHERE case_id=? AND rarity=?", (case_id, rarity))
        skins = await cur.fetchall()
        if not skins:
            cur = await db.execute("SELECT id, name, image_url, price FROM skins WHERE case_id=?", (case_id,))
            skins = await cur.fetchall()
        skin = random.choice(skins)
        
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (case["price"], uid))
        await db.execute("INSERT INTO inventory(user_id, skin_id, created_at) VALUES(?,?,?)", (uid, skin["id"], datetime.now(timezone.utc).isoformat()))
        await db.commit()
    return {"ok": True, "skin": dict(skin)}

# --- АДМИНКА ---
@app.post("/api/admin/give-points")
async def give_points(request: Request):
    user = await current_user(request)
    if await ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    target = str(body.get("target", "")).strip()
    amount = int(body.get("amount", 0))
    action = str(body.get("action", "add"))
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if target.startswith("@"):
            cur = await db.execute("SELECT user_id FROM users WHERE username=?", (target[1:],))
        else:
            cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (int(target),))
        u_row = await cur.fetchone()
        if not u_row: raise HTTPException(404, "Игрок не найден")
        
        final_amount = amount if action == "add" else -amount
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (final_amount, u_row["user_id"]))
        await db.commit()
    return {"ok": True, "message": f"Успешно! {action} {amount} баллов"}

@app.post("/api/admin/create-event")
async def admin_create_event(request: Request):
    user = await current_user(request)
    if await ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO events (sport, title, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?)",
                         (body["sport"], body["title"], body["o1"], body.get("ox"), body["o2"], datetime.now(timezone.utc).isoformat()))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/settle/{event_id}")
async def settle_event(event_id: int, request: Request):
    user = await current_user(request)
    if await ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    result = str((await request.json()).get("result", ""))
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        await db.execute("UPDATE events SET status='settled', result=? WHERE id=?", (result, event_id))
        cur = await db.execute("SELECT user_id, points, choice, locked_odds FROM predictions WHERE event_id=? AND status='pending'", (event_id,))
        for p in await cur.fetchall():
            if p["choice"] == result:
                reward = int(p["points"] * p["locked_odds"])
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, p["user_id"]))
                await db.execute("UPDATE predictions SET status='won' WHERE user_id=? AND event_id=?", (p["user_id"], event_id))
            else:
                await db.execute("UPDATE predictions SET status='lost' WHERE user_id=? AND event_id=?", (p["user_id"], event_id))
        await db.commit()
    return {"ok": True}
