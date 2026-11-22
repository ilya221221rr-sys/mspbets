"""
extras/dailywheel.py — ежедневное колесо фортуны: одно бесплатное вращение в сутки.

Призы: от 20 000 до 500 000 PTS. Шансы (сумма 100%):
    20 000 — 40%   ·  30 000 — 25%  ·  50 000 — 17%  ·  75 000 — 10%
   100 000 — 5%    · 200 000 — 2,5% · 500 000 — 0,5%  (≈ раз в 200 дней)
Среднее — 44 000 PTS за вращение.

Новые сутки начинаются в 00:00 UTC — как у ежедневного бонуса (core.today()).
Сектор выбирает сервер криптостойким ГСЧ (core.RNG); повторное вращение в те же сутки невозможно —
первичный ключ (user_id, day) защищает и от двойного нажатия, и от параллельных запросов.
Вращение — не ставка: XP, задания и джекпот не затрагиваются. В журнале — вид операции daily_wheel.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

from . import core
from .core import all_, auth, db, money, now_iso, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/dailywheel")

# Секторы по кругу (по часовой стрелке от указателя). weight — в 1/3000 (сумма 3000 = 100%).
SECTORS = [
    (500_000, 15),
    (20_000, 400),
    (30_000, 375),
    (75_000, 300),
    (20_000, 400),
    (50_000, 510),
    (200_000, 75),
    (20_000, 400),
    (30_000, 375),
    (100_000, 150),
]
TOTAL_W = sum(w for _, w in SECTORS)
assert TOTAL_W == 3000

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS daily_wheel (
        user_id INTEGER NOT NULL, day TEXT NOT NULL, sector INTEGER NOT NULL, prize INTEGER NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY (user_id, day))""",
    "CREATE INDEX IF NOT EXISTS idx_dw_prize ON daily_wheel(prize, created_at)",
]


def chances():
    """{приз: вероятность} — суммарно по всем секторам с этим призом."""
    out = {}
    for prize, w in SECTORS:
        out[prize] = out.get(prize, 0) + w / TOTAL_W
    return out


def pick_sector(rng=None):
    x = (rng or core.RNG).randrange(TOTAL_W)
    for i, (_, w) in enumerate(SECTORS):
        if x < w:
            return i
        x -= w
    return len(SECTORS) - 1  # недостижимо


def next_reset():
    now = datetime.now(timezone.utc)
    return (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()


@router.get("")
def state(request: Request):
    user = auth(request)
    uid = int(user["id"])
    day = core.today()
    with db() as conn:
        mine = one(conn, "SELECT sector, prize, created_at FROM daily_wheel WHERE user_id=? AND day=?", (uid, day))
        stats = one(conn, "SELECT COUNT(*), COALESCE(SUM(prize),0), COALESCE(MAX(prize),0) FROM daily_wheel WHERE user_id=?", (uid,))
        big = all_(conn, """SELECT COALESCE(u.first_name, u.username, 'Игрок'), w.prize, w.created_at FROM daily_wheel w
                            LEFT JOIN users u ON u.user_id=w.user_id WHERE w.prize>=100000 ORDER BY w.created_at DESC LIMIT 8""")
    ch = chances()
    return {
        "sectors": [{"prize": p, "chance": round(w * 100 / TOTAL_W, 2)} for p, w in SECTORS],
        "prizes": [{"prize": p, "chance": round(c * 100, 2)} for p, c in sorted(ch.items())],
        "available": mine is None,
        "today": {"sector": mine[0], "prize": mine[1], "at": mine[2]} if mine else None,
        "next_at": next_reset(),
        "stats": {"spins": stats[0], "total": stats[1], "best": stats[2]},
        "big_wins": [{"name": r[0], "prize": r[1], "at": r[2]} for r in big],
    }


@router.post("/spin")
def spin(request: Request):
    user = auth(request)
    uid = int(user["id"])
    day = core.today()
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        if one(conn, "SELECT 1 FROM daily_wheel WHERE user_id=? AND day=?", (uid, day)):
            raise HTTPException(400, "Колесо уже крутили сегодня — приходи завтра")
        i = pick_sector()
        prize = SECTORS[i][0]
        cur = conn.execute("INSERT OR IGNORE INTO daily_wheel (user_id, day, sector, prize, created_at) VALUES (?,?,?,?,?)",
                           (uid, day, i, prize, now_iso()))
        if core.changed(cur, conn) != 1:
            raise HTTPException(400, "Колесо уже крутили сегодня — приходи завтра")
        core.adjust(conn, uid, prize, "daily_wheel", f"Ежедневное колесо: +{money(prize)} PTS", ref=f"dwheel:{day}")
        bal = core.balance_of(conn, uid)
    return {"ok": True, "sector": i, "prize": prize, "balance": bal, "next_at": next_reset()}
