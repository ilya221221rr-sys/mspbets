"""
extras/seasonpass.py — сезонный пропуск (Battle Pass).

  * Сезон = календарный месяц (UTC). 30 уровней, награды на каждом уровне.
  * Прогресс = сумма ставок этого сезона в PTS: ставки на матчи засчитываются полностью
    (ставки фрибетом не считаются), ставки в играх казино — на 1/GAME_DIV (25%).
  * Уровень 1 стоит LEVEL_BASE (50 000) PTS ставок, каждый следующий дороже на LEVEL_STEP (5 000):
    50 000, 55 000, 60 000 … 195 000 за 30-й (всего 3 675 000 PTS за весь пропуск).
    Прогресс считается по журналу операций при открытии пропуска (никаких фоновых задач и записей
    при каждой ставке — бережём слабый сервер).
  * Бесплатная дорожка — у всех. Премиум-дорожка покупается за PREMIUM_PRICE PTS один раз на сезон;
    награды премиума за уже пройденные уровни можно забрать сразу после покупки.
  * Награды: баллы (вид «pass_reward» в журнале) и фрибеты (freebets.py, срок PASS_FB_DAYS).
"""
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from . import core, freebets
from .core import all_, auth, db, money, now_iso, one, touch_user, user_lock

log = logging.getLogger("mspbets.seasonpass")
router = APIRouter(prefix="/api/v2/pass")

MAX_LEVEL = 30
LEVEL_BASE = int(os.getenv("PASS_LEVEL_BASE", "50000"))  # стоимость 1-го уровня в PTS ставок
LEVEL_STEP = int(os.getenv("PASS_LEVEL_STEP", "5000"))  # каждый следующий уровень дороже на столько
GAME_DIV = int(os.getenv("PASS_GAME_DIV", "4"))  # ставки в играх засчитываются на 1/GAME_DIV
PREMIUM_PRICE = int(os.getenv("PASS_PREMIUM_PRICE", "250000"))
PASS_FB_DAYS = 14
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS w1_pass_premium (
        user_id INTEGER NOT NULL, season TEXT NOT NULL, price INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, season))""",
    """CREATE TABLE IF NOT EXISTS w1_pass_claims (
        user_id INTEGER NOT NULL, season TEXT NOT NULL, level INTEGER NOT NULL, track TEXT NOT NULL,
        kind TEXT NOT NULL, amount INTEGER NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, season, level, track))""",
]


def reward(level, track):
    """Награда уровня: {'type': 'pts'|'freebet', 'amount': N}."""
    if track == "free":
        if level % 5 == 0:
            return {"type": "freebet", "amount": level * 1000}  # 5к … 30к
        return {"type": "pts", "amount": 1000 + level * 200}
    if level == MAX_LEVEL:
        return {"type": "pts", "amount": 250_000}
    if level % 3 == 0:
        return {"type": "freebet", "amount": level * 2000}  # 6к … 54к
    return {"type": "pts", "amount": 3000 + level * 700}


def totals():
    out = {}
    for tr in ("free", "premium"):
        s = {"pts": 0, "freebet": 0}
        for lv in range(1, MAX_LEVEL + 1):
            r = reward(lv, tr)
            s[r["type"]] += r["amount"]
        out[tr] = s
    return out


def season_now(now=None):
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    return {"key": start.strftime("%Y-%m"), "title": f"{MONTHS[now.month - 1]} {now.year}",
            "start": start.isoformat(), "end": end.isoformat()}


_cache = {}  # (uid, season) -> (ts, points)
_txid = {}  # season -> id последней операции ДО начала сезона (id в журнале растут вместе со временем)


def season_txid(conn, season):
    """Граница сезона в журнале: бинарный поиск по PRIMARY KEY (≈20 быстрых запросов раз на процесс),
    чтобы подсчёт очков читал только операции сезона, а не всю историю игрока."""
    k = season["key"]
    if k in _txid:
        return _txid[k]
    hi = one(conn, "SELECT COALESCE(MAX(id), 0) FROM transactions")[0]
    lo = 0
    while lo < hi:  # первый id с created_at >= start
        mid = (lo + hi + 1) // 2
        r = one(conn, "SELECT created_at FROM transactions WHERE id<=? ORDER BY id DESC LIMIT 1", (mid,))
        if r and r[0] >= season["start"]:
            hi = mid - 1
        else:
            lo = mid
    _txid[k] = lo
    return lo


def points(conn, uid, season, fresh=False):
    k = (uid, season["key"])
    hit = _cache.get(k)
    if hit and not fresh and time.monotonic() - hit[0] < 15:
        return hit[1]
    r = one(conn, """SELECT COALESCE(SUM(CASE WHEN kind='bet' THEN -amount ELSE 0 END), 0),
                            COALESCE(SUM(CASE WHEN kind='game_bet' THEN -amount ELSE 0 END), 0),
                            COALESCE(SUM(CASE WHEN kind='freebet' THEN amount ELSE 0 END), 0)
                     FROM transactions WHERE user_id=? AND id>? AND created_at>=? AND created_at<? AND kind IN ('bet', 'game_bet', 'freebet')""",
            (uid, season_txid(conn, season), season["start"], season["end"]))
    sport, game, fb = (int(x or 0) for x in r)
    sport = max(0, sport - fb)
    pts = {"sport": sport, "game": game, "points": sport + game // GAME_DIV}
    if len(_cache) > 5000:
        _cache.clear()
    _cache[k] = (time.monotonic(), pts)
    return pts


def level_cost(n):
    """Сколько PTS ставок стоит уровень n (1-й — 50 000, затем +5 000 за каждый)."""
    return LEVEL_BASE + LEVEL_STEP * (n - 1)


def level_need(n):
    """Сколько PTS ставок нужно всего, чтобы получить уровень n."""
    return n * LEVEL_BASE + LEVEL_STEP * n * (n - 1) // 2


def level_of(p):
    lv = 0
    while lv < MAX_LEVEL and p >= level_need(lv + 1):
        lv += 1
    return lv


def progress(p, lvl):
    """(набрано в текущем уровне, стоимость следующего уровня)."""
    if lvl >= MAX_LEVEL:
        return level_cost(MAX_LEVEL), level_cost(MAX_LEVEL)
    return p - level_need(lvl), level_cost(lvl + 1)


def _state(conn, uid, fresh=False):
    s = season_now()
    pts = points(conn, uid, s, fresh)
    lvl = level_of(pts["points"])
    prem = bool(one(conn, "SELECT 1 FROM w1_pass_premium WHERE user_id=? AND season=?", (uid, s["key"])))
    claimed = {(lv, tr) for lv, tr in all_(conn, "SELECT level, track FROM w1_pass_claims WHERE user_id=? AND season=?", (uid, s["key"]))}
    return s, pts, lvl, prem, claimed


def _claimable(lvl, prem, claimed):
    out = []
    for lv in range(1, lvl + 1):
        for tr in ("free", "premium") if prem else ("free",):
            if (lv, tr) not in claimed:
                out.append((lv, tr))
    return out


@router.get("")
def get_pass(request: Request):
    uid = int(auth(request)["id"])
    with db() as conn:
        s, pts, lvl, prem, claimed = _state(conn, uid)
    levels = [{"level": lv, "cost": level_cost(lv), "need": level_need(lv), "free": reward(lv, "free"), "premium": reward(lv, "premium"),
               "free_claimed": (lv, "free") in claimed, "premium_claimed": (lv, "premium") in claimed} for lv in range(1, MAX_LEVEL + 1)]
    p = pts["points"]
    into, cost = progress(p, lvl)
    return {"season": s["key"], "title": s["title"], "ends_at": s["end"], "points": p, "level": lvl, "max_level": MAX_LEVEL,
            "level_points": cost, "into_level": into, "level_base": LEVEL_BASE, "level_step": LEVEL_STEP,
            "total_need": level_need(MAX_LEVEL), "game_share": round(100 / GAME_DIV),
            "sport_wager": pts["sport"], "game_wager": pts["game"],
            "premium": prem, "price": PREMIUM_PRICE, "levels": levels, "claimable": len(_claimable(lvl, prem, claimed)),
            "totals": totals(), "fb_days": PASS_FB_DAYS}


@router.get("/brief")
def brief(request: Request):
    """Коротко для баннера в «Бонусах»: уровень и сколько наград можно забрать."""
    uid = int(auth(request)["id"])
    with db() as conn:
        s, pts, lvl, prem, claimed = _state(conn, uid)
    into, cost = progress(pts["points"], lvl)
    return {"title": s["title"], "level": lvl, "max_level": MAX_LEVEL, "points": pts["points"], "level_points": cost, "into_level": into,
            "premium": prem, "claimable": len(_claimable(lvl, prem, claimed)), "ends_at": s["end"]}


@router.post("/buy")
def buy(request: Request):
    user = auth(request)
    uid = int(user["id"])
    s = season_now()
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        if one(conn, "SELECT 1 FROM w1_pass_premium WHERE user_id=? AND season=?", (uid, s["key"])):
            raise HTTPException(400, "Премиум уже куплен в этом сезоне")
        core.adjust(conn, uid, -PREMIUM_PRICE, "pass_buy", f"Премиум-пропуск: {s['title']}", ref=f"pass:{s['key']}")
        conn.execute("INSERT INTO w1_pass_premium (user_id, season, price, created_at) VALUES (?,?,?,?)", (uid, s["key"], PREMIUM_PRICE, now_iso()))
        bal = core.balance_of(conn, uid)
    return {"ok": True, "balance": bal}


@router.post("/claim")
def claim(request: Request):
    """Забрать все доступные награды (бесплатные и премиум, если куплен)."""
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        s, pts, lvl, prem, claimed = _state(conn, uid, fresh=True)
        got, pts_sum, fb_sum, skipped = [], 0, 0, 0
        for lv, tr in _claimable(lvl, prem, claimed):
            r = reward(lv, tr)
            if r["type"] == "freebet":
                if not freebets.issue(conn, uid, r["amount"], "pass", f"Пропуск {s['title']}: уровень {lv}", PASS_FB_DAYS):
                    skipped += 1
                    continue
                fb_sum += r["amount"]
            cur = conn.execute("INSERT OR IGNORE INTO w1_pass_claims (user_id, season, level, track, kind, amount, created_at) VALUES (?,?,?,?,?,?,?)",
                               (uid, s["key"], lv, tr, r["type"], r["amount"], now_iso()))
            if core.changed(cur, conn) != 1:
                raise HTTPException(409, "Награда уже получена")
            if r["type"] == "pts":
                pts_sum += r["amount"]
            got.append({"level": lv, "track": tr, **r})
        if pts_sum:
            core.adjust(conn, uid, pts_sum, "pass_reward", f"Сезонный пропуск {s['title']}: награды ({len(got)})", ref=f"pass:{s['key']}")
        bal = core.balance_of(conn, uid)
    if not got and not skipped:
        raise HTTPException(400, "Пока нечего забирать — делай ставки, чтобы пройти уровень")
    msg = []
    if pts_sum:
        msg.append(f"+{money(pts_sum)} PTS")
    if fb_sum:
        msg.append(f"фрибеты на {money(fb_sum)} PTS")
    return {"ok": True, "rewards": got, "pts": pts_sum, "freebets": fb_sum, "skipped": skipped, "balance": bal,
            "message": "Получено: " + ", ".join(msg) if msg else "Слишком много активных фрибетов — используй их и забери позже"}
