"""
extras/jackpot.py — прогрессивный джекпот (три уровня: Mini, Major, Grand), как в настоящих казино.

Как растёт: с каждой ставки игроков (матчи, игры, кейсы, рулетка — всё, что пишется в журнал
с видами bet / game_bet / case) в банки отчисляется небольшой процент. Отчисление идёт за счёт
заведения — с игрока ничего дополнительно не списывается.

Как выигрывают: каждая ставка от JACKPOT_MIN_BET баллов участвует в розыгрыше. Шанс сорвать уровень
пропорционален ставке: p = min(ставка, cap) / hit_every. То есть в среднем банк срывается один раз
на hit_every поставленных баллов. Выигравший получает весь банк уровня, банк сбрасывается до стартового.

Реализация не трогает код игр: фоновая задача раз в ~15 секунд читает новые записи журнала
(transactions) после курсора plus_meta.jp_last_tx. Поэтому джекпот срывается через несколько секунд
после ставки — игрок получает уведомление в бота и праздничное окно в приложении.
"""
import html
import os

from fastapi import APIRouter, Request

from . import core
from .core import RNG, all_, auth, db, money, now_iso, one

router = APIRouter(prefix="/api/v2/jackpot")

BET_KINDS = ("bet", "game_bet", "case")
MIN_BET = int(os.getenv("JACKPOT_MIN_BET", "100"))


def _env(name, default):
    try:
        return type(default)(os.getenv(name, default))
    except Exception:
        return default


# (ключ, название, стартовый банк, в среднем срывается раз на N поставленных PTS, доля отчислений)
TIERS = [
    ("mini", "Mini", _env("JACKPOT_MINI_SEED", 5_000), _env("JACKPOT_MINI_EVERY", 1_000_000), _env("JACKPOT_MINI_RATE", 0.003)),
    ("major", "Major", _env("JACKPOT_MAJOR_SEED", 50_000), _env("JACKPOT_MAJOR_EVERY", 12_000_000), _env("JACKPOT_MAJOR_RATE", 0.004)),
    ("grand", "Grand", _env("JACKPOT_GRAND_SEED", 500_000), _env("JACKPOT_GRAND_EVERY", 150_000_000), _env("JACKPOT_GRAND_RATE", 0.005)),
]
TIER = {t[0]: t for t in TIERS}
CAP_SHARE = 0.05  # одна ставка даёт не больше 5% шанса на уровень

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS jackpot_pools (
        tier TEXT PRIMARY KEY, cents INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS jackpot_wins (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tier TEXT NOT NULL, user_id INTEGER NOT NULL, name TEXT,
        amount INTEGER NOT NULL, tx_id INTEGER, seen INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_jpw_user ON jackpot_wins(user_id, seen)",
]


def init(conn, meta_get, meta_set):
    for key, _, seed, _, _ in TIERS:
        conn.execute("INSERT OR IGNORE INTO jackpot_pools (tier, cents, updated_at) VALUES (?,?,?)", (key, seed * 100, now_iso()))
    if meta_get(conn, "jp_last_tx") is None:  # старт с текущего момента — без ретро-розыгрышей
        last = one(conn, "SELECT COALESCE(MAX(id), 0) FROM transactions")[0]
        meta_set(conn, "jp_last_tx", str(last))


def chance(bet, tier):
    _, _, _, every, _ = TIER[tier]
    if bet < MIN_BET:
        return 0.0
    return min(bet, every * CAP_SHARE) / every


def process(meta_get, meta_set, limit=3000):
    """Обработать новые ставки из журнала: пополнить банки и разыграть джекпоты. Возвращает число выигрышей."""
    notes = []
    with db() as conn:
        last = int(meta_get(conn, "jp_last_tx") or 0)
        top = one(conn, "SELECT COALESCE(MAX(id), 0) FROM transactions")[0]
        marks = ",".join("?" * len(BET_KINDS))
        # ставки дуэлей не участвуют: вызов можно отменить с возвратом, иначе это были бы бесплатные попытки
        rows = all_(conn, f"SELECT id, user_id, amount FROM transactions WHERE id>? AND id<=? AND kind IN ({marks}) "
                          "AND COALESCE(ref, '') NOT LIKE 'duel:%' ORDER BY id LIMIT ?",
                    (last, top, *BET_KINDS, limit))
        if not rows:
            if top > last:  # были только не-ставки — двигаем курсор, чтобы не сканировать их снова
                meta_set(conn, "jp_last_tx", str(top), expect=str(last))
            return 0
        pools = {r[0]: r[1] for r in all_(conn, "SELECT tier, cents FROM jackpot_pools")}
        hits = []
        for tx_id, uid, amount in rows:
            bet = -int(amount)
            if bet <= 0:
                continue
            for key, _, _, _, rate in TIERS:
                pools[key] = pools.get(key, 0) + int(round(bet * rate * 100))
            for key, _, seed, _, _ in reversed(TIERS):  # сначала Grand — одна ставка срывает максимум один уровень
                if RNG.random() < chance(bet, key):
                    hits.append((key, uid, pools[key] // 100, tx_id))
                    pools[key] = seed * 100
                    break
        meta_set(conn, "jp_last_tx", str(rows[-1][0]), expect=str(last))
        for key, cents in pools.items():
            conn.execute("UPDATE jackpot_pools SET cents=?, updated_at=? WHERE tier=?", (cents, now_iso(), key))
        for key, uid, win, tx_id in hits:
            name_row = one(conn, "SELECT first_name, username FROM users WHERE user_id=?", (uid,))
            name = (name_row and (name_row[0] or name_row[1])) or "Игрок"
            core.adjust(conn, uid, win, "jackpot", f"Джекпот {TIER[key][1]}!", ref=f"jackpot:{key}")
            core.set_max(conn, uid, "biggest_win", win)
            conn.execute("UPDATE jackpot_pools SET hits=hits+1 WHERE tier=?", (key,))
            conn.execute("INSERT INTO jackpot_wins (tier, user_id, name, amount, tx_id, created_at) VALUES (?,?,?,?,?,?)",
                         (key, uid, str(name)[:24], win, tx_id, now_iso()))
            notes.append((uid, f"🎰💰 <b>ДЖЕКПОТ {TIER[key][1].upper()}!</b>\nТвоя ставка сорвала банк: <b>+{money(win)} PTS</b>\nПоздравляем, {html.escape(str(name))}!"))
        core.notify_many(conn, notes)
    return len(notes)


def _state(conn, uid=None):
    pools = {r[0]: (r[1], r[2]) for r in all_(conn, "SELECT tier, cents, hits FROM jackpot_pools")}
    tiers = []
    for key, name, seed, every, rate in TIERS:
        cents, hits = pools.get(key, (seed * 100, 0))
        tiers.append({"key": key, "name": name, "amount": cents // 100, "seed": seed, "hits": hits,
                      "rate_pct": round(rate * 100, 2), "every": every})
    recent = all_(conn, "SELECT tier, name, amount, created_at FROM jackpot_wins ORDER BY id DESC LIMIT 10")
    out = {"tiers": tiers, "min_bet": MIN_BET,
           "recent": [{"tier": r[0], "tier_name": TIER.get(r[0], (0, r[0]))[1], "name": r[1], "amount": r[2], "at": r[3]} for r in recent]}
    if uid is not None:
        mine = all_(conn, "SELECT id, tier, amount, created_at FROM jackpot_wins WHERE user_id=? AND seen=0 ORDER BY id", (uid,))
        out["unseen"] = [{"id": r[0], "tier": r[1], "tier_name": TIER.get(r[1], (0, r[1]))[1], "amount": r[2], "at": r[3]} for r in mine]
    return out


@router.get("")
def state(request: Request):
    user = auth(request)
    with db() as conn:
        return _state(conn, int(user["id"]))


@router.post("/seen")
def seen(request: Request):
    user = auth(request)
    with db() as conn:
        conn.execute("UPDATE jackpot_wins SET seen=1 WHERE user_id=? AND seen=0", (int(user["id"]),))
    return {"ok": True}
