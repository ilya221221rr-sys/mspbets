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
            result TEXT, odds_1 REAL NOT NULL DEFAULT 2.0, odds_x REAL, odds_2 REAL NOT NULL DEFAULT 2.0,
            created_at TEXT NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            event_id INTEGER NOT NULL, choice TEXT NOT NULL, points INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
            UNIQUE(user_id, event_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, reward INTEGER NOT NULL)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY (user_id, code))""")
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
        # ТАБЛИЦА ДЛЯ ПЕРСОНАЛЬНЫХ НАСТРОЕК ИГРОКОВ
        await db.execute("""CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            forced_skin_id INTEGER,
            custom_upgrade_chance INTEGER DEFAULT 10
        )""")
        await db.commit()
        
        cur = await db.execute("SELECT COUNT(*) FROM cases")
        count = (await cur.fetchone())[0]
        if count == 0:
            cases_data = [
                (1, "Горячая Аркана", 600, "https://i.imgur.com/QRp0iKe.png"),
                (2, "Аркана 100%", 2000, "https://i.imgur.com/e1wXFiA.png"),
                (3, "Шейх", 2500, "https://i.imgur.com/LPHDRsY.png"),
                (4, "Старый Бог", 900, "https://i.imgur.com/CuUjs92.png"),
                (5, "The International", 2000, "https://i.imgur.com/aJOka5b.png")
            ]
            for c in cases_data:
                await db.execute("INSERT INTO cases (id, name, price, image_url) VALUES (?, ?, ?, ?)", c)
            
            skins_data = [
                (1, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
                (1, "Fiery Soul of the Slayer", "Rare", 1600, "https://i.imgur.com/AQiM6I8.png"),
                (1, "Kindred of the Iron Dragon", "Common", 160, "https://i.imgur.com/G7XpkYs.png"),
                (1, "Barding of Balaur", "Common", 180, "https://i.imgur.com/0QWoP2i.png"),
                (1, "Solar Gyre", "Common", 266, "https://i.imgur.com/7x8YDiz.png"),
                (2, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
                (2, "Fiery Soul of the Slayer", "Common", 1600, "https://i.imgur.com/AQiM6I8.png"),
                (2, "Аркана на пуджа", "Common", 1600, "https://placehold.co/100x100?text=Pudge"),
                (2, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
                (2, "Аркана на венгу", "Common", 1700, "https://i.imgur.com/d7TJSbb.png"),
                (3, "Аркана на тб", "Rare", 2500, "https://i.imgur.com/T9M1BIX.png"),
                (3, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
                (3, "Fiery Soul of the Slayer", "Rare", 1600, "https://i.imgur.com/AQiM6I8.png"),
                (3, "Kindred of the Iron Dragon", "Common", 160, "https://i.imgur.com/G7XpkYs.png"),
                (3, "Barding of Balaur", "Common", 180, "https://i.imgur.com/0QWoP2i.png"),
                (4, "Kindred of the Iron Dragon", "Common", 160, "https://i.imgur.com/G7XpkYs.png"),
                (4, "Barding of Balaur", "Common", 180, "https://i.imgur.com/0QWoP2i.png"),
                (4, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
                (4, "Аркана на венгу", "Rare", 1700, "https://i.imgur.com/d7TJSbb.png"),
                (4, "Аркана на тб", "Rare", 2500, "https://i.imgur.com/T9M1BIX.png"),
                (5, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
                (5, "Fiery Soul of the Slayer", "Common", 1600, "https://i.imgur.com/AQiM6I8.png"),
                (5, "Аркана на пуджа", "Common", 1600, "https://placehold.co/100x100?text=Pudge"),
                (5, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
                (5, "Аркана на венгу", "Common", 1700, "https://i.imgur.com/d7TJSbb.png")
            ]
            for s in skins_data:
                await db.execute("INSERT INTO skins (case_id, name, rarity, price, image_url) VALUES (?, ?, ?, ?, ?)", s)
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
        "🏆 <b>MSPBETS</b>\n\nДелай прогнозы на матчи, получай баллы и поднимайся в рейтинге!\n"
        "Баллы не имеют денежной стоимости.\n\n📢 <b>Канал:</b> https://t.me/mspbetsprizi",
        reply_markup=None
    )
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

@app.post("/api/promo")
async def activate_promo(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    code = str(body.get("code", "")).strip().upper()
    if not code: raise HTTPException(400, "Введите промокод")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT reward FROM promo_codes WHERE code=?", (code,))
        promo = await cur.fetchone()
        if not promo:
            await db.rollback(); raise HTTPException(404, "Такого промокода не существует")
        reward = promo[0]
        cur = await db.execute("SELECT 1 FROM promo_uses WHERE user_id=? AND code=?", (uid, code))
        if await cur.fetchone():
            await db.rollback(); raise HTTPException(400, "Вы уже активировали этот промокод")
        await db.execute("INSERT INTO promo_uses(user_id, code) VALUES(?, ?)", (uid, code))
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, uid))
        await db.commit()
    return {"ok": True, "message": f"Успешно! Вы получили {reward} баллов", "reward": reward}

@app.get("/api/events")
async def events(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT e.id, e.sport, e.title, e.starts_at, e.status, e.result,
                   e.odds_1, e.odds_x, e.odds_2,
                   p.choice AS my_choice, p.points AS my_points, p.status AS my_status
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
        event_id = int(body["event_id"]); points = int(body["points"]); choice = str(body["choice"])
    except Exception:
        raise HTTPException(400, "Проверьте данные прогноза")
    if points < 1 or points > 1000000:
        raise HTTPException(400, "Можно использовать от 1 до 1 000 000 баллов")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            await db.rollback(); raise HTTPException(400, "Пользователь не найден")
        cur = await db.execute("SELECT sport, status FROM events WHERE id=?", (event_id,))
        event = await cur.fetchone()
        if not event or event[1] != "open":
            await db.rollback(); raise HTTPException(400, "Приём прогнозов закрыт")
        sport = event[0]
        allowed = {"1", "2"} if sport in ("CS2", "Dota 2", "Теннис") else {"1", "X", "2"}
        if choice not in allowed:
            await db.rollback(); raise HTTPException(400, f"Для {sport} доступны исходы: {', '.join(sorted(allowed))}")
        if row[0] < points:
            await db.rollback(); raise HTTPException(400, "Недостаточно баллов")
        try:
            await db.execute("INSERT INTO predictions(user_id,event_id,choice,points,created_at) VALUES(?,?,?,?,?)",
                             (uid, event_id, choice, points, datetime.now(timezone.utc).isoformat()))
        except aiosqlite.IntegrityError:
            await db.rollback(); raise HTTPException(409, "Вы уже сделали прогноз на этот матч")
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
        
        cur = await db.execute("SELECT price, name FROM cases WHERE id=?", (case_id,))
        case = await cur.fetchone()
        if not case:
            await db.rollback(); raise HTTPException(400, "Кейс не найден")
            
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row or row["balance"] < case["price"]:
            await db.rollback(); raise HTTPException(400, "Недостаточно баллов")
            
        # ПРОВЕРКА ПЕРСОНАЛЬНОЙ ПОДКРУТКИ
        cur = await db.execute("SELECT forced_skin_id FROM user_settings WHERE user_id=?", (uid,))
        settings = await cur.fetchone()
        
        if settings and settings["forced_skin_id"]:
            skin_id = settings["forced_skin_id"]
            cur = await db.execute("SELECT id, name, image_url, price FROM skins WHERE id=?", (skin_id,))
            skin = await cur.fetchone()
            # Сбрасываем подкрутку после выдачи (чтобы не падало вечно)
            await db.execute("UPDATE user_settings SET forced_skin_id=NULL WHERE user_id=?", (uid,))
        else:
            roll = random.randint(1, 100)
            if roll <= 60: rarity = "Common"
            elif roll <= 85: rarity = "Rare"
            else: rarity = "Immortal"
            
            cur = await db.execute("SELECT id, name, image_url, price FROM skins WHERE case_id=? AND rarity=?", (case_id, rarity))
            skins = await cur.fetchall()
            if not skins:
                cur = await db.execute("SELECT id, name, image_url, price FROM skins WHERE case_id=?", (case_id,))
                skins = await cur.fetchall()
            skin = random.choice(skins)
            
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (case["price"], uid))
        await db.execute("INSERT INTO inventory(user_id, skin_id, created_at) VALUES(?,?,?)", (uid, skin["id"], datetime.now(timezone.utc).isoformat()))
        await db.commit()
        
    return {"ok": True, "skin": dict(skin), "new_balance": row["balance"] - case["price"]}

@app.get("/api/inventory")
async def inventory(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT i.id as inv_id, s.name, s.image_url, s.price, s.rarity
            FROM inventory i JOIN skins s ON i.skin_id = s.id 
            WHERE i.user_id=? AND i.status='owned'
            ORDER BY s.price DESC
        """, (uid,))
        return [dict(r) for r in await cur.fetchall()]

@app.post("/api/inventory/sell")
async def sell_skin(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    inv_id = int(body.get("inv_id", 0))
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("""
            SELECT s.price, s.name FROM inventory i JOIN skins s ON i.skin_id = s.id 
            WHERE i.id=? AND i.user_id=? AND i.status='owned'
        """, (inv_id, uid))
        item = await cur.fetchone()
        if not item:
            await db.rollback(); raise HTTPException(400, "Скин не найден или уже продан")
        await db.execute("UPDATE inventory SET status='sold' WHERE id=?", (inv_id,))
        sell_price = int(item["price"])
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (sell_price, uid))
        await db.commit()
    return {"ok": True, "message": f"Скин {item['name']} продан за {sell_price} PTS", "balance_added": sell_price}

@app.post("/api/upgrade")
async def upgrade_skin(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    body = await request.json()
    inv_id = int(body.get("inv_id", 0))
    chosen_chance = float(body.get("chance", 20)) 
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        
        # ПОЛУЧАЕМ ИНДИВИДУАЛЬНЫЙ ШАНС ИГРОКА
        cur = await db.execute("SELECT custom_upgrade_chance FROM user_settings WHERE user_id=?", (uid,))
        settings = await cur.fetchone()
        real_chance = settings["custom_upgrade_chance"] if settings and settings["custom_upgrade_chance"] else 10.0
        
        cur = await db.execute("""
            SELECT s.id as skin_id, s.price, s.name, s.image_url, s.rarity
            FROM inventory i JOIN skins s ON i.skin_id = s.id 
            WHERE i.id=? AND i.user_id=? AND i.status='owned'
        """, (inv_id, uid))
        current_skin = await cur.fetchone()
        if not current_skin:
            await db.rollback(); raise HTTPException(400, "Скин не найден")
            
        ideal_price = current_skin["price"] / (chosen_chance / 100.0)
        
        cur = await db.execute("""
            SELECT id, name, image_url, price, rarity 
            FROM skins 
            WHERE price > ?
            ORDER BY ABS(price - ?) ASC LIMIT 1
        """, (current_skin["price"], ideal_price))
        target_skin = await cur.fetchone()
        
        if not target_skin:
            cur = await db.execute("SELECT id, name, image_url, price, rarity FROM skins ORDER BY price DESC LIMIT 1")
            target_skin = await cur.fetchone()
            
        if not target_skin:
            await db.rollback(); raise HTTPException(400, "Нет скинов для апгрейда")
            
        success = random.uniform(0, 100) <= real_chance
        
        if success:
            await db.execute("UPDATE inventory SET status='upgraded' WHERE id=?", (inv_id,))
            await db.execute("INSERT INTO inventory(user_id, skin_id, created_at) VALUES(?,?,?)", (uid, target_skin["id"], datetime.now(timezone.utc).isoformat()))
            await db.commit()
            return {"ok": True, "success": True, "old_skin": dict(current_skin), "target_skin": dict(target_skin)}
        else:
            await db.execute("UPDATE inventory SET status='lost' WHERE id=?", (inv_id,))
            await db.commit()
            return {"ok": True, "success": False, "old_skin": dict(current_skin), "target_skin": dict(target_skin)}

# --- АДМИНКА ---
@app.post("/api/admin/events")
async def create_event(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    body = await request.json()
    sport = str(body.get("sport", "")).strip()
    title = str(body.get("title", "")).strip()
    starts_at = str(body.get("starts_at", "")).strip()
    try:
        o1 = float(body.get("odds_1", 2.0)); o2 = float(body.get("odds_2", 2.0))
        ox_raw = body.get("odds_x"); ox = float(ox_raw) if ox_raw is not None else None
    except Exception:
        raise HTTPException(400, "Ошибка в коэффициентах")
    if sport not in ("CS2", "Dota 2", "Футбол", "Теннис") or not title:
        raise HTTPException(400, "Укажите дисциплину и название матча")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO events(sport,title,starts_at,odds_1,odds_x,odds_2,created_at) VALUES(?,?,?,?,?,?,?)",
                         (sport, title, starts_at, o1, ox, o2, datetime.now(timezone.utc).isoformat()))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/events/{event_id}/update-odds")
async def update_odds(event_id: int, request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    body = await request.json()
    try:
        o1 = float(body.get("odds_1")); o2 = float(body.get("odds_2"))
        ox_raw = body.get("odds_x"); ox = float(ox_raw) if ox_raw is not None else None
    except Exception:
        raise HTTPException(400, "Ошибка в коэффициентах")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT status FROM events WHERE id=?", (event_id,))
        event = await cur.fetchone()
        if not event or event[0] != "open":
            raise HTTPException(400, "Можно менять кэфы только у открытых матчей")
        await db.execute("UPDATE events SET odds_1=?, odds_x=?, odds_2=? WHERE id=?", (o1, ox, o2, event_id))
        await db.commit()
    return {"ok": True, "message": "Коэффициенты обновлены"}

@app.get("/api/admin/users")
async def admin_users(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT user_id, first_name, username, balance FROM users ORDER BY created_at DESC LIMIT 100")
        return [dict(r) for r in await cur.fetchall()]

@app.get("/api/admin/all-skins")
async def admin_all_skins(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, name, price FROM skins ORDER BY price DESC")
        return [dict(r) for r in await cur.fetchall()]

# НОВЫЙ ЭНДПОИНТ ДЛЯ НАСТРОЙКИ ИГРОКА
@app.post("/api/admin/user-settings")
async def admin_user_settings(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    
    body = await request.json()
    target_id = int(body.get("user_id", 0))
    forced_skin_id = body.get("forced_skin_id")
    custom_chance = int(body.get("custom_chance", 10))
    
    if forced_skin_id == "": forced_skin_id = None
    if forced_skin_id: forced_skin_id = int(forced_skin_id)
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO user_settings (user_id, custom_upgrade_chance) VALUES (?, 10)", (target_id,))
        await db.execute("""
            UPDATE user_settings 
            SET forced_skin_id=?, custom_upgrade_chance=? 
            WHERE user_id=?
        """, (forced_skin_id, custom_chance, target_id))
        await db.commit()
        
    return {"ok": True, "message": "Настройки игрока обновлены!"}

@app.post("/api/admin/give-points")
async def give_points(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    body = await request.json()
    try:
        target_id = int(body.get("user_id", 0))
        amount = int(body.get("amount", 0))
        action = str(body.get("action", "add"))
    except Exception:
        raise HTTPException(400, "Неверный формат данных")
        
    if amount < 1 or amount > 1000000:
        raise HTTPException(400, "Можно использовать от 1 до 1 000 000 баллов")
        
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (target_id,))
        user_row = await cur.fetchone()
        if not user_row: raise HTTPException(404, "Пользователь не найден")
        
        current_balance = user_row[0]
        if action == "remove":
            amount = -amount
            if current_balance + amount < 0:
                raise HTTPException(400, f"У игрока только {current_balance} баллов. Нельзя списать больше.")
                
        await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, target_id))
        await db.commit()
        
    action_text = "Начислено" if amount > 0 else "Списано"
    return {"ok": True, "message": f"{action_text} {abs(amount)} баллов"}

@app.post("/api/admin/create-promo")
async def create_promo(request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    body = await request.json()
    code = str(body.get("code", "")).strip().upper()
    try: reward = int(body.get("reward", 0))
    except Exception: raise HTTPException(400, "Неверный формат суммы")
    if not code or len(code) < 3: raise HTTPException(400, "Код должен быть не короче 3 символов")
    if reward < 1 or reward > 1000000: raise HTTPException(400, "Сумма должна быть от 1 до 1 000 000")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        try:
            await db.execute("INSERT INTO promo_codes (code, reward) VALUES (?, ?)", (code, reward))
            await db.commit()
        except aiosqlite.IntegrityError:
            raise HTTPException(400, "Промокод с таким названием уже существует")
    return {"ok": True, "message": f"Промокод {code} на {reward} баллов создан!"}

@app.post("/api/admin/events/{event_id}/settle")
async def settle_event(event_id: int, request: Request):
    user = await current_user(request)
    uid = await ensure_user(user)
    if uid not in ADMIN_IDS: raise HTTPException(403, "Нет доступа")
    body = await request.json()
    result = str(body.get("result", ""))
    if result not in ("1", "X", "2"): raise HTTPException(400, "Результат должен быть 1, X или 2")
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT status, odds_1, odds_x, odds_2 FROM events WHERE id=?", (event_id,))
        event = await cur.fetchone()
        if not event or event["status"] != "open":
            await db.rollback(); raise HTTPException(400, "Матч уже рассчитан или не найден")
        await db.execute("UPDATE events SET status='settled', result=? WHERE id=?", (result, event_id))
        cur = await db.execute("SELECT user_id,points,choice FROM predictions WHERE event_id=?", (event_id,))
        predictions = await cur.fetchall()
        for p in predictions:
            odds_map = {"1": event["odds_1"], "X": event["odds_x"], "2": event["odds_2"]}
            odds = odds_map.get(p["choice"])
            if p["choice"] == result and odds:
                reward = int(p["points"] * odds)
                await db.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (reward, p["user_id"]))
                await db.execute("UPDATE predictions SET status='won' WHERE user_id=? AND event_id=?", (p["user_id"], event_id))
            else:
                await db.execute("UPDATE predictions SET status='lost' WHERE user_id=? AND event_id=?", (p["user_id"], event_id))
        await db.commit()
    return {"ok": True, "settled": len(predictions)}

@app.get("/health")
async def health():
    return {"status": "ok"}
