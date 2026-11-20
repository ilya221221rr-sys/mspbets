"""
extras/core.py — ядро новых возможностей MSPBETS.

Что здесь:
  * единый баланс + журнал операций (adjust) — ЛЮБОЕ движение баллов проходит здесь;
  * XP / уровни / ранги / VIP;
  * ежедневные задания, достижения, счётчики;
  * уведомления игроку в бота;
  * схема новых таблиц (только CREATE TABLE IF NOT EXISTS — старые данные не трогаются).

Модуль НЕ импортирует app.py (чтобы не было циклических импортов): всё нужное
передаётся через ctx в extras.register().
"""
import asyncio
import json
import logging
import math
import random
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import HTTPException

log = logging.getLogger("mspbets.core")

# --------------------------------------------------------------------------
# Контекст (заполняется из app.py)
# --------------------------------------------------------------------------
class Ctx:
    get_db = None
    bot = None
    admin_ids = set()
    webapp_url = ""
    bot_username = "mspbetsbot"
    loop = None
    validate = None  # validate_init_data из app.py


ctx = Ctx()
RNG = secrets.SystemRandom()  # криптостойкий ГСЧ для всех игр и кейсов


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Работа с БД
# --------------------------------------------------------------------------
_tl = threading.local()


@contextmanager
def db():
    """Соединение с Turso: commit при успехе, rollback при ошибке (всё или ничего)."""
    conn = ctx.get_db()
    _tl.unlocked = []
    try:
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def changed(cur, conn):
    """Сколько строк изменил последний запрос."""
    rc = getattr(cur, "rowcount", -1)
    if rc is None or rc < 0:
        rc = conn.execute("SELECT changes()").fetchone()[0]
    return rc


def one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def all_(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


_locks = {}
_locks_guard = threading.Lock()


def user_lock(uid):
    """Блокировка на игрока: защита от гонок (двойной клик, параллельные запросы)."""
    with _locks_guard:
        lk = _locks.get(uid)
        if lk is None:
            if len(_locks) > 5000:
                _locks.clear()
            lk = _locks[uid] = threading.Lock()
        return lk


SCHEMA = [
    # --- журнал операций ---
    """CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL, balance_after INTEGER NOT NULL,
        kind TEXT NOT NULL, note TEXT, ref TEXT, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id, id)",
    # --- профиль, XP, серии, рефералка ---
    """CREATE TABLE IF NOT EXISTS profiles (
        user_id INTEGER PRIMARY KEY, xp INTEGER NOT NULL DEFAULT 0,
        wagered INTEGER NOT NULL DEFAULT 0, bets_count INTEGER NOT NULL DEFAULT 0,
        streak INTEGER NOT NULL DEFAULT 0, best_streak INTEGER NOT NULL DEFAULT 0,
        last_daily TEXT, frame TEXT DEFAULT '', badge TEXT DEFAULT '', bio TEXT DEFAULT '',
        notify INTEGER NOT NULL DEFAULT 1, hide_bets INTEGER NOT NULL DEFAULT 0,
        referrer_id INTEGER, ref_paid INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS quest_progress (
        user_id INTEGER NOT NULL, day TEXT NOT NULL, key TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day, key))""",
    """CREATE TABLE IF NOT EXISTS achievements (
        user_id INTEGER NOT NULL, key TEXT NOT NULL, unlocked_at TEXT NOT NULL,
        PRIMARY KEY (user_id, key))""",
    """CREATE TABLE IF NOT EXISTS counters (
        user_id INTEGER NOT NULL, key TEXT NOT NULL, value INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, key))""",
    """CREATE TABLE IF NOT EXISTS collection_claims (
        user_id INTEGER NOT NULL, case_id INTEGER NOT NULL, claimed_at TEXT NOT NULL,
        PRIMARY KEY (user_id, case_id))""",
    # --- турниры ---
    """CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL,
        min_level INTEGER NOT NULL DEFAULT 1, prize_pool INTEGER NOT NULL DEFAULT 0,
        starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')""",
    """CREATE TABLE IF NOT EXISTS tournament_scores (
        tournament_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        score INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (tournament_id, user_id))""",
    # --- уведомления «коэффициент вырос» ---
    """CREATE TABLE IF NOT EXISTS odds_watch (
        user_id INTEGER NOT NULL, event_id INTEGER NOT NULL, choice TEXT NOT NULL,
        last_odds REAL NOT NULL, PRIMARY KEY (user_id, event_id, choice))""",
    # --- автозагрузка матчей ---
    """CREATE TABLE IF NOT EXISTS feed_events (
        event_id INTEGER PRIMARY KEY, source TEXT NOT NULL, ext_id TEXT NOT NULL UNIQUE,
        league TEXT, sport_path TEXT, home TEXT, away TEXT, starts_at TEXT, updated_at TEXT)""",
    # --- мини-игры ---
    """CREATE TABLE IF NOT EXISTS game_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, game TEXT NOT NULL,
        bet INTEGER NOT NULL, payout INTEGER NOT NULL, mult REAL NOT NULL,
        detail TEXT, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_rounds_user ON game_rounds(user_id, id)",
    """CREATE TABLE IF NOT EXISTS mines_games (
        user_id INTEGER PRIMARY KEY, bet INTEGER NOT NULL, mines INTEGER NOT NULL,
        positions TEXT NOT NULL, revealed TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)""",
    # --- краш (одиночные раунды) ---
    """CREATE TABLE IF NOT EXISTS crash_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, seed TEXT NOT NULL, seed_hash TEXT NOT NULL,
        crash_point REAL NOT NULL, status TEXT NOT NULL DEFAULT 'live', created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS crash_bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL, auto_cashout REAL, cashed_at REAL, payout INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_cbets_round ON crash_bets(round_id)",
    """CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT,
        text TEXT NOT NULL, created_at TEXT NOT NULL)""",
]

# Безопасные ALTER (колонка добавляется один раз; если уже есть — пропускаем)
ALTERS = [
    "ALTER TABLE skins ADD COLUMN weight INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE cases ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE predictions ADD COLUMN cashout INTEGER",  # сумма выкупа ординара
    "ALTER TABLE bets ADD COLUMN cashout INTEGER",  # сумма выкупа экспресса
]


def init_schema():
    conn = ctx.get_db()
    try:
        for s in SCHEMA:
            conn.execute(s)
        for s in ALTERS:
            try:
                conn.execute(s)
            except Exception:
                pass  # колонка уже существует
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Единый баланс и журнал
# --------------------------------------------------------------------------
def adjust(conn, uid, delta, kind, note="", ref=None):
    """Изменить баланс И записать операцию в журнал. Возвращает новый баланс.
    Списание атомарно: UPDATE ... WHERE balance >= сумма (нельзя уйти в минус)."""
    delta = int(delta)
    if delta < 0:
        cur = conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=? AND balance>=?",
            (delta, uid, -delta),
        )
        if changed(cur, conn) != 1:
            raise HTTPException(400, "Мало баллов")
    elif delta > 0:
        conn.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (delta, uid))
    row = one(conn, "SELECT balance FROM users WHERE user_id=?", (uid,))
    if not row:
        raise HTTPException(404, "Игрок не найден")
    if delta != 0:
        conn.execute(
            "INSERT INTO transactions (user_id, amount, balance_after, kind, note, ref, created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, delta, row[0], kind, (note or "")[:200], ref, now_iso()),
        )
    return row[0]


def balance_of(conn, uid):
    r = one(conn, "SELECT balance FROM users WHERE user_id=?", (uid,))
    return r[0] if r else 0


def pay(bet, mult):
    """Выплата = floor(ставка × множитель) в целых числах (без ошибок float)."""
    return int(bet) * int(round(float(mult) * 100)) // 100


def check_amount(v, lo=1, hi=10_000_000):
    try:
        v = int(v)
    except Exception:
        raise HTTPException(400, "Неверная сумма")
    if v < lo:
        raise HTTPException(400, f"Минимальная ставка {lo}")
    if v > hi:
        raise HTTPException(400, f"Максимальная ставка {hi:,}".replace(",", " "))
    return v


# --------------------------------------------------------------------------
# Уровни, ранги, VIP
# --------------------------------------------------------------------------
def level_for(xp):
    return math.isqrt(max(0, int(xp)) // 100) + 1


def xp_for_level(level):
    return 100 * (level - 1) ** 2


RANKS = [  # (мин. уровень, название, цвет, рамка)
    (1, "Новичок", "#9AA0B5", "none"),
    (5, "Бронза", "#CD7F32", "bronze"),
    (10, "Серебро", "#C9D1E3", "silver"),
    (20, "Золото", "#FFC93C", "gold"),
    (30, "Платина", "#5EE7E0", "platinum"),
    (45, "Алмаз", "#6EA8FF", "diamond"),
    (60, "Легенда", "#FF5DAE", "legend"),
]
FRAME_KEYS = [r[3] for r in RANKS] + ["streak"]


def rank_for(level):
    cur = RANKS[0]
    for r in RANKS:
        if level >= r[0]:
            cur = r
    return {"name": cur[1], "color": cur[2], "frame": cur[3], "min_level": cur[0]}


def vip_for(level):
    return 4 if level >= 60 else 3 if level >= 45 else 2 if level >= 30 else 1 if level >= 20 else 0


def ensure_profile(conn, uid):
    conn.execute("INSERT OR IGNORE INTO profiles (user_id, created_at) VALUES (?, ?)", (uid, now_iso()))


# --------------------------------------------------------------------------
# Достижения и счётчики
# --------------------------------------------------------------------------
ACH = {
    "first_bet": ("Первый шаг", "Сделай первую ставку или игру", "🎯", 100, "bets_total", 1),
    "bets_50": ("Азартный", "50 ставок и игр", "🔥", 300, "bets_total", 50),
    "bets_500": ("Завсегдатай", "500 ставок и игр", "💎", 1500, "bets_total", 500),
    "express_1": ("Экспресс-мастер", "Выиграй экспресс", "🚀", 300, "express_won", 1),
    "express_10": ("Король экспрессов", "Выиграй 10 экспрессов", "👑", 2000, "express_won", 10),
    "single_10": ("Прогнозист", "Выиграй 10 ординаров", "🧠", 500, "single_won", 10),
    "streak_7": ("Неделя подряд", "Серия бонуса 7 дней", "📅", 500, "streak", 7),
    "streak_30": ("Железная воля", "Серия бонуса 30 дней", "⚡", 3000, "streak", 30),
    "level_5": ("Бронзовый ранг", "Достигни 5 уровня", "🥉", 250, "level", 5),
    "level_10": ("Серебряный ранг", "Достигни 10 уровня", "🥈", 500, "level", 10),
    "level_20": ("Золотой ранг · VIP", "Достигни 20 уровня", "🥇", 1500, "level", 20),
    "level_30": ("Платиновый ранг", "Достигни 30 уровня", "🏅", 3000, "level", 30),
    "cases_10": ("Коллекционер", "Открой 10 кейсов", "📦", 300, "cases_opened", 10),
    "cases_100": ("Кейс-маньяк", "Открой 100 кейсов", "🎁", 3000, "cases_opened", 100),
    "legend_drop": ("Легендарный дроп", "Выбей Legendary или Immortal", "🌟", 1000, "legend_drops", 1),
    "upgrade_1": ("Апгрейдер", "Успешный апгрейд", "⬆️", 200, "upgrades_won", 1),
    "upgrade_10": ("Мастер апгрейда", "10 успешных апгрейдов", "🛠️", 1500, "upgrades_won", 10),
    "crash_10x": ("Космонавт", "Забери x10 в Краше", "🛸", 500, "crash_best_x", 10),
    "mines_10": ("Сапёр", "Открой 10 ячеек в одной партии Мин", "💣", 500, "mines_best", 10),
    "plinko_big": ("Шарик-везунчик", "Выиграй x50 в Plinko", "🔮", 800, "plinko_best_x", 50),
    "big_win": ("Крупный куш", "Выигрыш от 10 000 PTS за раз", "💰", 1000, "biggest_win", 10000),
    "ref_3": ("Душа компании", "3 друга сделали ставки по твоей ссылке", "🤝", 1500, "refs_paid", 3),
    "collector": ("Полный сет", "Собери коллекцию кейса целиком", "🏆", 1000, "collections_done", 1),
}
ACH_BY_COUNTER = {}
for _k, _v in ACH.items():
    ACH_BY_COUNTER.setdefault(_v[4], []).append((_k, _v[5]))


def ach_info(key):
    a = ACH[key]
    return {"key": key, "title": a[0], "desc": a[1], "icon": a[2], "reward": a[3], "target": a[5], "counter": a[4]}


def _push_unlocked(info):
    if not hasattr(_tl, "unlocked"):
        _tl.unlocked = []
    _tl.unlocked.append(info)


def pop_unlocked():
    out = getattr(_tl, "unlocked", [])
    _tl.unlocked = []
    return out


def unlock(conn, uid, key):
    cur = conn.execute(
        "INSERT OR IGNORE INTO achievements (user_id, key, unlocked_at) VALUES (?,?,?)", (uid, key, now_iso())
    )
    if changed(cur, conn) == 1:
        a = ACH[key]
        adjust(conn, uid, a[3], "achievement", f"Достижение: {a[0]}", ref=key)
        _push_unlocked(ach_info(key))
        return True
    return False


def _check(conn, uid, key, value):
    for ak, target in ACH_BY_COUNTER.get(key, []):
        if value >= target:
            unlock(conn, uid, ak)


def bump(conn, uid, key, n=1):
    conn.execute(
        "INSERT INTO counters (user_id, key, value) VALUES (?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=value+?",
        (uid, key, n, n),
    )
    if key in ACH_BY_COUNTER:
        v = one(conn, "SELECT value FROM counters WHERE user_id=? AND key=?", (uid, key))[0]
        _check(conn, uid, key, v)


def set_max(conn, uid, key, v):
    v = int(v)
    conn.execute(
        "INSERT INTO counters (user_id, key, value) VALUES (?,?,?) ON CONFLICT(user_id, key) DO UPDATE SET value=MAX(value, ?)",
        (uid, key, v, v),
    )
    _check(conn, uid, key, v)


# --------------------------------------------------------------------------
# Ежедневные задания
# --------------------------------------------------------------------------
QUESTS = {  # key: (название, событие, цель, награда)
    "bet3": ("Поставь на 3 матча", "match_bet", 3, 150),
    "express_win": ("Выиграй экспресс", "express_won", 1, 400),
    "single_win2": ("Выиграй 2 ординара", "single_won", 2, 250),
    "play5": ("Сыграй 5 раундов в мини-играх", "game_round", 5, 120),
    "case2": ("Открой 2 кейса", "case_open", 2, 150),
    "crash2x": ("Забери x2 или больше в Краше", "crash_2x", 1, 200),
    "mines8": ("Открой 8 безопасных ячеек в Минах", "mines_safe", 8, 180),
    "wager1000": ("Поставь суммарно 1000 PTS", "wager", 1000, 200),
    "upgrade1": ("Сделай апгрейд скина", "upgrade", 1, 160),
}
FIXED_QUESTS = ["bet3", "express_win"]


def quests_for(uid, day=None):
    day = day or today()
    rnd = random.Random(f"{uid}:{day}")
    pool = [k for k in QUESTS if k not in FIXED_QUESTS]
    return FIXED_QUESTS + rnd.sample(pool, 2)


def quest_event(conn, uid, event, n=1):
    day = today()
    for k in quests_for(uid, day):
        title, ev, target, _ = QUESTS[k]
        if ev == event:
            conn.execute(
                "INSERT INTO quest_progress (user_id, day, key, progress) VALUES (?,?,?,MIN(?,?)) "
                "ON CONFLICT(user_id, day, key) DO UPDATE SET progress=MIN(?, progress+?) WHERE claimed=0",
                (uid, day, k, target, n, target, n),
            )


# --------------------------------------------------------------------------
# Ставки / XP / рефералка / турниры
# --------------------------------------------------------------------------
_tour_cache = {"ts": 0, "ids": []}
REF_MIN_BETS = 5
REF_MIN_WAGER = 500
REF_REWARD_REFERRER = 10000
REF_REWARD_FRIEND = 150
REF_MAX_PAID = 100


def _active_tournament_ids(conn):
    import time

    if time.time() - _tour_cache["ts"] > 30:
        rows = all_(conn, "SELECT id FROM tournaments WHERE status='active' AND ends_at>?", (now_iso(),))
        _tour_cache["ids"] = [r[0] for r in rows]
        _tour_cache["ts"] = time.time()
    return _tour_cache["ids"]


def invalidate_tournaments():
    _tour_cache["ts"] = 0


def on_wager(conn, uid, amount, source="game"):
    """Вызывается при КАЖДОЙ ставке: XP, уровни, задания, достижения, турниры, рефералка."""
    amount = int(amount)
    if amount <= 0:
        return
    ensure_profile(conn, uid)
    xp = max(1, amount // 5)
    conn.execute(
        "UPDATE profiles SET xp=xp+?, wagered=wagered+?, bets_count=bets_count+1 WHERE user_id=?",
        (xp, amount, uid),
    )
    row = one(conn, "SELECT xp, wagered, bets_count, referrer_id, ref_paid FROM profiles WHERE user_id=?", (uid,))
    old_l, new_l = level_for(row[0] - xp), level_for(row[0])
    for lvl in range(old_l + 1, new_l + 1):
        adjust(conn, uid, lvl * 50, "levelup", f"Новый уровень {lvl}")
        _push_unlocked({"key": f"lvl{lvl}", "title": f"Уровень {lvl}!", "icon": "⭐", "reward": lvl * 50, "desc": "Новый уровень"})
        set_max(conn, uid, "level", lvl)
    ids = _active_tournament_ids(conn)
    if ids:
        marks = ",".join("?" * len(ids))
        conn.execute(
            f"UPDATE tournament_scores SET score=score+? WHERE user_id=? AND tournament_id IN ({marks})",
            (amount, uid, *ids),
        )
    quest_event(conn, uid, "wager", amount)
    bump(conn, uid, "bets_total", 1)
    # --- рефералка: бонус только после реальной активности друга ---
    if row[3] and not row[4] and row[2] >= REF_MIN_BETS and row[1] >= REF_MIN_WAGER:
        _pay_referral(conn, uid, row[3])


def _pay_referral(conn, friend, referrer):
    cur = conn.execute("UPDATE profiles SET ref_paid=1 WHERE user_id=? AND ref_paid=0", (friend,))
    if changed(cur, conn) != 1:
        return
    paid = one(conn, "SELECT COUNT(*) FROM profiles WHERE referrer_id=? AND ref_paid=1", (referrer,))[0]
    if paid > REF_MAX_PAID:
        return
    adjust(conn, referrer, REF_REWARD_REFERRER, "referral", f"Друг сделал первые ставки (ID {friend})", ref=str(friend))
    adjust(conn, friend, REF_REWARD_FRIEND, "referral", "Бонус за приглашение", ref=str(referrer))
    set_max(conn, referrer, "refs_paid", paid)
    notify(referrer, f"🤝 <b>Рефералка!</b> Твой друг сделал первые ставки — тебе начислено <b>+{REF_REWARD_REFERRER} PTS</b>.")


def register_referral(conn, invitee, referrer):
    """Привязать друга к пригласившему. Только для НОВЫХ игроков, один раз, не к себе."""
    invitee, referrer = int(invitee), int(referrer)
    if invitee == referrer:
        return False
    if not one(conn, "SELECT 1 FROM users WHERE user_id=?", (referrer,)):
        return False
    u = one(conn, "SELECT created_at FROM users WHERE user_id=?", (invitee,))
    if u:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(u[0])).total_seconds()
        except Exception:
            age = 10 ** 9
        if age > 86400:  # старый аккаунт — реферал не засчитывается
            return False
    ensure_profile(conn, invitee)
    cur = conn.execute(
        "UPDATE profiles SET referrer_id=? WHERE user_id=? AND referrer_id IS NULL AND bets_count=0", (referrer, invitee)
    )
    return changed(cur, conn) == 1


def log_round(conn, uid, game, bet, payout, mult, detail=None):
    cur = conn.execute(
        "INSERT INTO game_rounds (user_id, game, bet, payout, mult, detail, created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, game, int(bet), int(payout), float(mult), json.dumps(detail, ensure_ascii=False) if detail is not None else None, now_iso()),
    )
    return cur.lastrowid


def game_bet(conn, uid, amount, game, note=None):
    """Списать ставку мини-игры + XP/задания/турниры."""
    adjust(conn, uid, -amount, "game_bet", note or f"Ставка: {game}", ref=game)
    on_wager(conn, uid, amount, game)


def game_win(conn, uid, payout, game, note=None):
    if payout > 0:
        adjust(conn, uid, payout, "game_win", note or f"Выигрыш: {game}", ref=game)
        set_max(conn, uid, "biggest_win", payout)


def game_done(conn, uid, game):
    quest_event(conn, uid, "game_round", 1)


# --------------------------------------------------------------------------
# Уведомления в бота
# --------------------------------------------------------------------------
async def _send(uid, text):
    try:
        await ctx.bot.send_message(chat_id=uid, text=text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:  # игрок заблокировал бота и т.п.
        log.debug("notify %s failed: %s", uid, e)


def notify(uid, text):
    """Неблокирующая отправка (можно звать из любого потока)."""
    if not ctx.bot or not ctx.loop:
        return
    try:
        asyncio.run_coroutine_threadsafe(_send(uid, text), ctx.loop)
    except Exception:
        pass


def notify_many(conn, items):
    """items: [(uid, text)] — отправка с учётом настройки «уведомления» в профиле."""
    if not items or not ctx.bot:
        return
    uids = list({u for u, _ in items})
    marks = ",".join("?" * len(uids))
    off = {r[0] for r in all_(conn, f"SELECT user_id FROM profiles WHERE notify=0 AND user_id IN ({marks})", uids)}
    for u, t in items:
        if u not in off:
            notify(u, t)


def money(n):
    return f"{int(n):,}".replace(",", " ")


# --------------------------------------------------------------------------
# Авторизация для новых эндпоинтов (синхронная, без лишних запросов к БД)
# --------------------------------------------------------------------------
def auth(request):
    user = ctx.validate(request.headers.get("X-Telegram-Init-Data", ""))
    if not user:
        raise HTTPException(401, "Откройте через Telegram")
    return user


def touch_user(conn, user):
    """Гарантирует строку в users (баланс 1000 при первом входе) и профиль. Возвращает uid."""
    uid = int(user["id"])
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, balance, created_at) VALUES (?, ?, ?, 1000, ?)",
        (uid, user.get("username"), user.get("first_name", "Игрок"), now_iso()),
    )
    ensure_profile(conn, uid)
    return uid


def display_name(user):
    return (user.get("first_name") or user.get("username") or "Игрок")[:24]
