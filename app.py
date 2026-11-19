import os
import json
import hmac
import hashlib
import logging
import random
import asyncio
import feedparser
from urllib.parse import parse_qsl
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import libsql
import extras
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Update, WebAppInfo, MenuButtonWebApp, Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mspbets")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_WEBAPP_URL = os.getenv("BOT_WEBAPP_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None
dp = Dispatcher()

# --- РАССЫЛКА О МАТЧАХ ---
pending_matches = []
notify_task = None

async def flush_pending_matches():
    global pending_matches, notify_task
    await asyncio.sleep(60)
    if not pending_matches:
        notify_task = None
        return

    text = "🔥 <b>Новые матчи в MSPBETS!</b>\n\n"
    for m in pending_matches:
        text += f"🏆 <b>{m['sport']}</b>\n"
        text += f"⚔️ {m['title']}\n"
        text += f"П1: <b>{m['o1']}</b> "
        if m['ox']: text += f"| X: <b>{m['ox']}</b> "
        text += f"| П2: <b>{m['o2']}</b>\n\n"
    
    text += f"👉 <a href='{BOT_WEBAPP_URL}'>Сделать ставку</a>"
    pending_matches = []

    conn = get_db()
    cur = conn.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()
    
    for u in users:
        try:
            await bot.send_message(chat_id=u[0], text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass
    notify_task = None

# --- ПОДКЛЮЧЕНИЕ К TURSO ---
def get_db():
    if not TURSO_URL or not TURSO_TOKEN:
        raise Exception("Не заданы TURSO_DATABASE_URL или TURSO_AUTH_TOKEN")
    return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)

# --- АВТОРИЗАЦИЯ ---
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
    raise HTTPException(status_code=401, detail="Откройте через Telegram")

# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ ---
def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        balance INTEGER NOT NULL DEFAULT 1000, created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT NOT NULL,
        title TEXT NOT NULL, starts_at TEXT, status TEXT NOT NULL DEFAULT 'open',
        result TEXT, odds_1 REAL NOT NULL DEFAULT 2.0, odds_x REAL, odds_2 REAL NOT NULL DEFAULT 2.0,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        event_id INTEGER NOT NULL, choice TEXT NOT NULL, points INTEGER NOT NULL,
        locked_odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL, total_odds REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS bet_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bet_id INTEGER NOT NULL,
        event_id INTEGER NOT NULL, choice TEXT NOT NULL, locked_odds REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS promo_codes (code TEXT PRIMARY KEY, reward INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY (user_id, code))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, price INTEGER NOT NULL, image_url TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS skins (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, name TEXT NOT NULL, rarity TEXT NOT NULL, image_url TEXT NOT NULL, price INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, skin_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'owned', created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_settings (user_id INTEGER PRIMARY KEY, forced_skin_id INTEGER, custom_upgrade_chance INTEGER DEFAULT 10)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS crash_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, bet_amount INTEGER NOT NULL, multiplier REAL NOT NULL, win_amount INTEGER NOT NULL, created_at TEXT NOT NULL)""")
    conn.commit()
    
    cur = conn.execute("SELECT COUNT(*) FROM cases")
    if cur.fetchone()[0] == 0:
        cases_data = [
            (1, "Горячая Аркана", 600, "https://i.imgur.com/QRp0iKe.png"),
            (2, "Аркана 100%", 2000, "https://i.imgur.com/e1wXFiA.png"),
            (3, "Шейх", 2500, "https://i.imgur.com/LPHDRsY.png"),
            (4, "Старый Бог", 900, "https://i.imgur.com/CuUjs92.png"),
            (5, "The International", 2000, "https://i.imgur.com/aJOka5b.png")
        ]
        for c in cases_data: conn.execute("INSERT INTO cases (id, name, price, image_url) VALUES (?, ?, ?, ?)", c)
        
        skins_data = [
            (1, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
            (1, "Fiery Soul", "Rare", 1600, "https://i.imgur.com/AQiM6I8.png"),
            (1, "Kindred", "Common", 160, "https://i.imgur.com/G7XpkYs.png"),
            (1, "Barding of Balaur", "Common", 180, "https://i.imgur.com/0QWoP2i.png"),
            (1, "Solar Gyre", "Common", 266, "https://i.imgur.com/7x8YDiz.png"),
            (2, "Demon Eater", "Rare", 2000, "https://i.imgur.com/BegS1KI.png"),
            (2, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
            (2, "Аркана на венгу", "Rare", 1700, "https://i.imgur.com/d7TJSbb.png"),
            (3, "Аркана на тб", "Rare", 2500, "https://i.imgur.com/T9M1BIX.png"),
            (4, "Аркана на врку", "Immortal", 14000, "https://i.imgur.com/rQAXrZd.png"),
            (5, "Аркана на пуджа", "Common", 1600, "https://placehold.co/100x100?text=Pudge")
        ]
        for s in skins_data: conn.execute("INSERT INTO skins (case_id, name, rarity, price, image_url) VALUES (?, ?, ?, ?, ?)", s)
        conn.commit()
    conn.close()

def ensure_user(user):
    uid = int(user["id"])
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, balance, created_at) VALUES (?, ?, ?, 1000, ?)",
                 (uid, user.get("username"), user.get("first_name", "Игрок"), datetime.now(timezone.utc).isoformat()))
    conn.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (user.get("username"), user.get("first_name", "Игрок"), uid))
    conn.commit()
    conn.close()
    return uid

# --- БОТ ---
@dp.message(Command("start"))
async def start_message(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and message.from_user:
        await extras.handle_start(message.from_user, parts[1].strip())  # /start ref_<id>
    if not BOT_WEBAPP_URL: return
    await message.answer(
        "🏆 <b>MSPBETS</b>\n\n"
        "Делай прогнозы на матчи, получай виртуальные баллы и поднимайся в рейтинге!\n"
        "Баллы не имеют денежной стоимости.\n\n"
        "📢 <b>Наш Telegram-канал:</b> https://t.me/mspbetsprizi\n"
        "Здесь начинается победа.",
        reply_markup=None
    )
    await bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=MenuButtonWebApp(text="Открыть MSPBETS", web_app=WebAppInfo(url=BOT_WEBAPP_URL)))

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    
    text = message.text.replace("/broadcast", "").strip()
    if not text:
        await message.answer("❌ Используйте: <code>/broadcast Текст сообщения</code>")
        return
    
    await message.answer("⏳ Начинаю рассылку...")
    
    conn = get_db()
    cur = conn.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()
    
    success = 0
    fail = 0
    for u in users:
        try:
            await bot.send_message(chat_id=u[0], text=text, parse_mode="HTML")
            success += 1
        except Exception:
            fail += 1
    
    await message.answer(f"✅ Рассылка завершена.\nОтправлено: {success}\nОшибок: {fail}")

# --- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await extras.startup()  # новые игры, кейсы, краш, автозагрузка матчей (extras/)
    if bot and BOT_WEBAPP_URL and WEBHOOK_SECRET:
        await bot.set_webhook(f"{BOT_WEBAPP_URL}/telegram/webhook", secret_token=WEBHOOK_SECRET, drop_pending_updates=True)
    yield
    await extras.shutdown()
    if bot: await bot.session.close()

app = FastAPI(title="MSPBETS API", lifespan=lifespan)
extras.setup(get_db=get_db, validate=validate_init_data, bot=bot, admin_ids=ADMIN_IDS,
             webapp_url=BOT_WEBAPP_URL, bot_username=os.getenv("BOT_USERNAME", "mspbetsbot"))
extras.register(app)
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

# --- API ЭНДПОИНТЫ ---
@app.get("/api/me")
async def me(request: Request):
    user = await current_user(request)
    uid = ensure_user(user)
    conn = get_db()
    cur = conn.execute("SELECT user_id, username, first_name, balance FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return {"user_id": row[0], "username": row[1], "first_name": row[2], "balance": row[3], "is_admin": uid in ADMIN_IDS}

@app.get("/api/rating")
async def get_rating(request: Request):
    await current_user(request)
    conn = get_db()
    cur = conn.execute("""SELECT u.first_name, u.username, u.balance, u.user_id, COALESCE(p.xp, 0), COALESCE(p.frame, '')
        FROM users u LEFT JOIN profiles p ON p.user_id = u.user_id ORDER BY u.balance DESC LIMIT 50""")
    rows = cur.fetchall()
    conn.close()
    return [{"first_name": r[0], "username": r[1], "balance": r[2], "user_id": r[3],
             "level": extras.core.level_for(r[4]), "frame": r[5]} for r in rows]

@app.get("/api/events")
async def events(request: Request):
    user = await current_user(request)
    ensure_user(user)
    conn = get_db()
    cur = conn.execute("""SELECT e.id, e.sport, e.title, e.status, e.odds_1, e.odds_x, e.odds_2, e.starts_at, f.league
        FROM events e LEFT JOIN feed_events f ON f.event_id = e.id
        WHERE e.status <> 'settled' OR e.id IN (SELECT id FROM events WHERE status='settled' ORDER BY id DESC LIMIT 30)
        ORDER BY CASE WHEN e.status='open' THEN 0 ELSE 1 END, CASE WHEN e.starts_at IS NULL THEN 0 ELSE 1 END,
                 COALESCE(e.starts_at, ''), e.id DESC""")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "sport": r[1], "title": r[2], "status": r[3], "odds_1": r[4], "odds_x": r[5], "odds_2": r[6],
             "starts_at": r[7], "league": r[8]} for r in rows]

@app.post("/api/predict")
async def predict(request: Request):
    user = await current_user(request)
    uid = ensure_user(user)
    body = await request.json()
    try:
        event_id = int(body["event_id"]); points = int(body["points"]); choice = str(body["choice"])
    except Exception:
        raise HTTPException(400, "Неверные данные")
    # ставка, списание, журнал, XP, задания — extras/betting.py
    with extras.core.user_lock(uid), extras.core.db() as conn:
        extras.betting.place_single(conn, uid, event_id, choice, points)
    return {"ok": True, "unlocked": extras.core.pop_unlocked()}

@app.post("/api/calc-win")
async def calc_win(request: Request):
    user = await current_user(request)
    body = await request.json()
    event_id = int(body["event_id"]); points = int(body["points"]); choice = str(body["choice"])
    
    conn = get_db()
    cur = conn.execute("SELECT status, odds_1, odds_x, odds_2 FROM events WHERE id=?", (event_id,))
    event = cur.fetchone()
    conn.close()
    
    if not event or event[0] != "open": raise HTTPException(400, "Закрыто")
    odds_map = {"1": event[1], "X": event[2], "2": event[3]}
    odds = odds_map.get(choice)
    if not odds: raise HTTPException(400, "Неверный исход")
    
    potential_win = int(points * odds)
    return {"potential_win": potential_win}

@app.post("/api/express")
async def express_bet(request: Request):
    user = await current_user(request)
    uid = ensure_user(user)
    body = await request.json()
    try:
        amount = int(body.get("amount", 0)); items = body.get("items", [])
    except Exception:
        raise HTTPException(400, "Неверные данные")
    with extras.core.user_lock(uid), extras.core.db() as conn:
        res = extras.betting.place_express(conn, uid, amount, items)
    return {"ok": True, "message": f"Экспресс принят. Общий кф: {round(res['total_odds'], 2)}", "unlocked": extras.core.pop_unlocked()}

@app.get("/api/history")
async def get_history(request: Request):
    user = await current_user(request)
    uid = ensure_user(user)
    conn = get_db()
    
    cur = conn.execute("""
        SELECT p.choice, p.points, p.locked_odds, p.status, p.created_at, e.title, e.sport, e.result
        FROM predictions p JOIN events e ON p.event_id = e.id
        WHERE p.user_id = ? ORDER BY p.id DESC LIMIT 50
    """, (uid,))
    rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    
    cur = conn.execute("SELECT id, amount, total_odds, status, created_at FROM bets WHERE user_id = ?", (uid,))
    bets = cur.fetchall()
    for b in bets:
        cur2 = conn.execute("""SELECT e.title, be.choice, be.locked_odds FROM bet_events be 
            JOIN events e ON be.event_id = e.id WHERE be.bet_id = ?""", (b[0],))
        ev_list = cur2.fetchall()
        titles = ", ".join([f"{ev[0]} ({ev[1]})" for ev in ev_list])
        rows.append({
            "choice": "Экспресс", "points": b[1], "locked_odds": b[2], "status": b[3],
            "created_at": b[4], "title": titles, "sport": "Экспресс", "result": None
        })
    conn.close()
    rows.sort(key=lambda x: x["created_at"], reverse=True)
    return rows[:50]

@app.get("/api/bet/{bet_id}")
async def get_bet_detail(bet_id: int, request: Request):
    await current_user(request)
    conn = get_db()
    cur = conn.execute("""
        SELECT e.title, e.sport, p.choice, p.points, p.locked_odds, p.status, p.created_at
        FROM predictions p JOIN events e ON p.event_id = e.id
        WHERE p.id = ?
    """, (bet_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Ставка не найдена")
    return {
        "title": row[0], "sport": row[1], "choice": row[2],
        "points": row[3], "odds": row[4], "status": row[5], "created_at": row[6]
    }

# Краш, кейсы, инвентарь и апгрейд теперь в extras/ (/api/v2/...): честная математика, журнал операций.

# --- АДМИНКА ---
@app.post("/api/admin/give-points")
async def give_points(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    target = str(body.get("target", "")).strip()
    amount = int(body.get("amount", 0)); action = str(body.get("action", "add"))
    
    conn = get_db()
    if target.startswith("@"): cur = conn.execute("SELECT user_id FROM users WHERE username=?", (target[1:],))
    else: cur = conn.execute("SELECT user_id FROM users WHERE user_id=?", (int(target),))
    u_row = cur.fetchone()
    if not u_row: raise HTTPException(404, "Игрок не найден")
    
    final_amount = amount if action == "add" else -amount
    extras.core.adjust(conn, u_row[0], final_amount, "admin", "Админ: начисление" if final_amount > 0 else "Админ: списание", ref=str(uid))
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"Успешно! {action} {amount} баллов"}

@app.post("/api/admin/update-odds/{event_id}")
async def admin_update_odds(event_id: int, request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    o1, ox, o2 = float(body["o1"]), body.get("ox"), float(body["o2"])
    ox = float(ox) if ox else None
    if o1 < 1.01 or o2 < 1.01 or (ox is not None and ox < 1.01):
        raise HTTPException(400, "Коэффициент должен быть не меньше 1.01")
    with extras.core.db() as conn:
        conn.execute("UPDATE events SET odds_1=?, odds_x=?, odds_2=? WHERE id=?", (o1, ox, o2, event_id))
        extras.core.notify_many(conn, extras.betting.odds_changed(conn, event_id, (o1, ox, o2)))  # «коэффициент вырос»
    return {"ok": True, "message": "Коэффициенты обновлены!"}

@app.post("/api/admin/toggle-event/{event_id}")
async def toggle_event(event_id: int, request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    
    conn = get_db()
    cur = conn.execute("SELECT status FROM events WHERE id=?", (event_id,))
    row = cur.fetchone()
    if not row: 
        conn.close()
        raise HTTPException(404, "Матч не найден")
    
    current_status = row[0]
    new_status = "closed" if current_status == "open" else "open"
    
    conn.execute("UPDATE events SET status=? WHERE id=?", (new_status, event_id))
    conn.commit()
    conn.close()
    
    return {"ok": True, "status": new_status, "message": f"Матч {'закрыт' if new_status == 'closed' else 'открыт'}"}

@app.post("/api/admin/user-settings")
async def admin_user_settings(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    target_id = int(body.get("user_id", 0))
    forced_skin_id = body.get("forced_skin_id")
    custom_chance = int(body.get("custom_chance", 10))
    
    if forced_skin_id == "": forced_skin_id = None
    if forced_skin_id: forced_skin_id = int(forced_skin_id)
    
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO user_settings (user_id, custom_upgrade_chance) VALUES (?, 10)", (target_id,))
    conn.execute("UPDATE user_settings SET forced_skin_id=?, custom_upgrade_chance=? WHERE user_id=?", (forced_skin_id, custom_chance, target_id))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Настройки обновлены!"}

@app.get("/api/admin/users")
async def admin_users(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    conn = get_db()
    cur = conn.execute("SELECT user_id, first_name, username, balance FROM users ORDER BY created_at DESC LIMIT 100")
    rows = cur.fetchall()
    conn.close()
    return [{"user_id": r[0], "first_name": r[1], "username": r[2], "balance": r[3]} for r in rows]

@app.get("/api/admin/all-skins")
async def admin_all_skins(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    conn = get_db()
    cur = conn.execute("SELECT id, name, price FROM skins ORDER BY price DESC")
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "price": r[2]} for r in rows]

@app.post("/api/admin/create-event")
async def admin_create_event(request: Request):
    global notify_task
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    
    conn = get_db()
    conn.execute("INSERT INTO events (sport, title, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?)",
                 (body["sport"], body["title"], body["o1"], body.get("ox"), body["o2"], datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    
    pending_matches.append({
        "sport": body["sport"],
        "title": body["title"],
        "o1": body["o1"],
        "ox": body.get("ox"),
        "o2": body["o2"]
    })
    
    if notify_task is None or notify_task.done():
        notify_task = asyncio.create_task(flush_pending_matches())
        
    return {"ok": True}

@app.post("/api/admin/settle/{event_id}")
async def settle_event(event_id: int, request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    result = str((await request.json()).get("result", ""))  # "1" | "X" | "2" | "void" (отмена)
    with extras.core.db() as conn:
        notes = extras.betting.settle_event_core(conn, event_id, result)  # выплаты + журнал + задания
        extras.core.notify_many(conn, notes)                              # «ставка сыграла» в бота
    return {"ok": True, "notified": len(notes)}

@app.get("/api/admin/bets-report")
async def admin_bets_report(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    
    conn = get_db()
    report = []
    
    cur = conn.execute("""
        SELECT p.id, u.first_name, u.username, e.title, p.choice, p.points, p.locked_odds, p.status, p.created_at
        FROM predictions p 
        JOIN users u ON p.user_id = u.user_id
        JOIN events e ON p.event_id = e.id
    """)
    for r in cur.fetchall():
        report.append({
            "id": r[0], "user": r[1] or r[2] or "Без имени", "event": r[3],
            "choice": r[4], "points": r[5], "odds": r[6], "status": r[7], "time": r[8], "type": "Ординар"
        })
    
    cur = conn.execute("""
        SELECT b.id, u.first_name, u.username, b.amount, b.total_odds, b.status, b.created_at
        FROM bets b JOIN users u ON b.user_id = u.user_id
    """)
    for r in cur.fetchall():
        bet_id = r[0]
        cur2 = conn.execute("""
            SELECT e.title, be.choice FROM bet_events be 
            JOIN events e ON be.event_id = e.id WHERE be.bet_id = ?
        """, (bet_id,))
        evs = cur2.fetchall()
        ev_titles = "; ".join([f"{e[0]} ({e[1]})" for e in evs])
        
        report.append({
            "id": r[0], "user": r[1] or r[2] or "Без имени", "event": ev_titles,
            "choice": "Экспресс", "points": r[3], "odds": r[4], "status": r[5], "time": r[6], "type": "Экспресс"
        })
    
    conn.close()
    report.sort(key=lambda x: x["time"] or "", reverse=True)
    return report[:50]

@app.post("/api/admin/create-promo")
async def create_promo(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    body = await request.json()
    conn = get_db()
    try:
        conn.execute("INSERT INTO promo_codes (code, reward) VALUES (?, ?)", (body["code"].upper(), int(body["reward"])))
        conn.commit()
    except Exception:
        raise HTTPException(400, "Такой промокод уже есть")
    finally:
        conn.close()
    return {"ok": True, "message": "Промокод создан!"}

@app.post("/api/admin/broadcast")
async def admin_broadcast(request: Request):
    user = await current_user(request)
    if ensure_user(user) not in ADMIN_IDS: raise HTTPException(403)
    
    body = await request.json()
    message_text = str(body.get("text", "")).strip()
    
    if not message_text:
        raise HTTPException(400, "Текст сообщения не может быть пустым")
    
    conn = get_db()
    cur = conn.execute("SELECT user_id FROM users")
    users = cur.fetchall()
    conn.close()
    
    success_count = 0
    for u in users:
        try:
            await bot.send_message(chat_id=u[0], text=message_text, parse_mode="HTML")
            success_count += 1
        except Exception:
            pass
    
    return {"ok": True, "message": f"Сообщение отправлено {success_count} игрокам"}

@app.post("/api/promo/activate")
async def activate_promo(request: Request):
    user = await current_user(request)
    uid = ensure_user(user)
    code = str((await request.json()).get("code", "")).strip().upper()
    conn = get_db()
    cur = conn.execute("SELECT reward FROM promo_codes WHERE code=?", (code,))
    promo = cur.fetchone()
    if not promo: raise HTTPException(404, "Нет такого кода")
    cur = conn.execute("SELECT 1 FROM promo_uses WHERE user_id=? AND code=?", (uid, code))
    if cur.fetchone(): raise HTTPException(400, "Уже активирован")
    conn.execute("INSERT INTO promo_uses (user_id, code) VALUES (?,?)", (uid, code))
    extras.core.adjust(conn, uid, promo[0], "promo", f"Промокод {code}", ref=code)
    conn.commit()
    conn.close()
    return {"ok": True, "message": f"+{promo[0]} баллов!"}

# --- НОВОСТИ DOTA 2 ---
@app.get("/api/dota-news")
async def get_dota_news(request: Request):
    await current_user(request)
    rss_url = "https://dota2.ru/news/rss/"
    try:
        feed = feedparser.parse(rss_url)
        news_list = []
        for entry in feed.entries[:10]:
            news_list.append({
                "title": entry.get("title", "Без названия"),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": entry.get("summary", "")[:150] + "..."
            })
        return news_list
    except Exception:
        return []

@app.get("/api/news-detail")
async def get_news_detail(url: str, request: Request):
    await current_user(request)
    return {"html": f'<p style="color: #fff;">Читайте полную версию по ссылке:</p><a href="{url}" target="_blank" style="color: #55ff00; font-size: 16px; font-weight: bold;">Открыть новость</a>'}

@app.get("/health")
async def health(): return {"status": "ok"}
