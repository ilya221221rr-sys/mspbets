"""
extras/feed.py — автоматическая загрузка матчей и коэффициентов «как в настоящих БК».

Источники:
  1) ESPN (без ключа): футбол (АПЛ, Ла Лига, Серия A, Бундеслига, Лига 1, ЛЧ, ЛЕ), NBA, NHL.
     Берём РЫНОЧНЫЕ линии (moneyline DraftKings), переводим в десятичные кэфы, убираем
     маржу источника и накладываем собственную (FEED_MARGIN, по умолчанию 6%).
  2) PandaScore (опционально, нужен PANDASCORE_TOKEN): Dota 2 и CS2. Бесплатный тариф не отдаёт
     линии букмекеров, поэтому кэфы считаются по рейтингу Эло, собранному из последних матчей.

Что делает:
  * каждые FEED_SYNC_MINUTES добавляет новые матчи (ближайшие FEED_WINDOW_HOURS часов, по умолчанию 48) и обновляет кэфы
    (рост кэфа → уведомления подписчикам 🔔);
  * закрывает приём ставок в момент начала матча (starts_at);
  * после окончания сам рассчитывает матч (тот же код, что и админский расчёт) и шлёт игрокам
    «ставка сыграла»; отменённые матчи → возврат ставок.
Ничего не удаляет и не трогает матчи, созданные админом вручную.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, HTTPException, Request

from . import betting, core
from .core import all_, auth, db, one

log = logging.getLogger("mspbets.feed")
router = APIRouter(prefix="/api/v2/admin/feed")

ENABLED = os.getenv("FEED_ENABLED", "1") not in ("0", "false", "False", "")
SYNC_MIN = max(5, int(os.getenv("FEED_SYNC_MINUTES", "20")))
# Окно загрузки: берём только матчи, которые начнутся в ближайшие FEED_WINDOW_HOURS часов (по умолчанию 48).
# (старая переменная FEED_DAYS больше не используется)
WINDOW_H = max(1, int(os.getenv("FEED_WINDOW_HOURS", "48")))
MARGIN = float(os.getenv("FEED_MARGIN", "0.06"))
PER_LEAGUE = int(os.getenv("FEED_PER_LEAGUE", "12"))
MAX_OPEN = int(os.getenv("FEED_MAX_OPEN", "100"))
BROADCAST = os.getenv("FEED_BROADCAST", "0") in ("1", "true", "True")
PANDA_TOKEN = os.getenv("PANDASCORE_TOKEN", "")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"

# (путь ESPN, вид спорта в приложении, лига, есть ли ничья)
LEAGUES = [
    ("soccer/eng.1", "Футбол", "Англия · АПЛ", True),
    ("soccer/esp.1", "Футбол", "Испания · Ла Лига", True),
    ("soccer/ita.1", "Футбол", "Италия · Серия A", True),
    ("soccer/ger.1", "Футбол", "Германия · Бундеслига", True),
    ("soccer/fra.1", "Футбол", "Франция · Лига 1", True),
    ("soccer/uefa.champions", "Футбол", "Лига чемпионов", True),
    ("soccer/uefa.europa", "Футбол", "Лига Европы", True),
    ("basketball/nba", "Баскетбол", "NBA", False),
    ("hockey/nhl", "Хоккей", "NHL", False),
]
if os.getenv("FEED_LEAGUES"):  # например FEED_LEAGUES="soccer/eng.1,basketball/nba"
    _only = {x.strip() for x in os.getenv("FEED_LEAGUES").split(",")}
    LEAGUES = [l for l in LEAGUES if l[0] in _only]

PANDA_GAMES = [("dota2", "Dota 2"), ("csgo", "CS2")]

status = {"enabled": ENABLED, "panda": bool(PANDA_TOKEN), "last_sync": None, "last_settle": None,
          "last_added": 0, "last_updated": 0, "errors": [], "total_added": 0, "total_settled": 0}

ISO = "%Y-%m-%dT%H:%M:%S+00:00"


def _now():
    return datetime.now(timezone.utc)


def to_decimal(ml):
    """Американский moneyline ('-105', '+265', 'EVEN') → десятичный кэф."""
    try:
        s = str(ml).strip().upper()
        if s in ("EVEN", "EV", "PK"):
            return 2.0
        v = float(s.replace("+", ""))
        return 1 + v / 100 if v > 0 else 1 + 100 / abs(v)
    except Exception:
        return None


def with_margin(decimals):
    """Убираем маржу источника (нормализация) и накладываем свою."""
    if any(d is None or d <= 1 for d in decimals):
        return None
    probs = [1 / d for d in decimals]
    s = sum(probs)
    return [max(1.05, round(1 / ((p / s) * (1 + MARGIN)), 2)) for p in probs]


def _pick(d):
    if not isinstance(d, dict):
        return None
    for k in ("close", "open"):
        v = (d.get(k) or {}).get("odds")
        if v not in (None, ""):
            return v
    return None


def parse_espn_odds(comp, has_draw):
    o = (comp.get("odds") or [None])[0]
    if not o:
        return None
    ml = o.get("moneyline") or {}
    home, away = _pick(ml.get("home")), _pick(ml.get("away"))
    if home is None:
        home = (o.get("homeTeamOdds") or {}).get("moneyLine")
    if away is None:
        away = (o.get("awayTeamOdds") or {}).get("moneyLine")
    dec = [to_decimal(home), to_decimal(away)]
    if has_draw:
        draw = (o.get("drawOdds") or {}).get("moneyLine")
        if draw is None:
            draw = _pick(ml.get("draw"))
        dec.insert(1, to_decimal(draw))
    out = with_margin(dec)
    if not out:
        return None
    return (out[0], out[1], out[2]) if has_draw else (out[0], None, out[1])


def _espn_day(path, day):
    r = requests.get(ESPN.format(path=path), params={"dates": f"{day:%Y%m%d}", "limit": 100}, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0 mspbets"})
    if r.status_code == 404:  # лиги нет в ESPN / нет сезона
        return []
    r.raise_for_status()
    return r.json().get("events", [])


def espn_fetch(path, d1, d2):
    """События лиги за дни d1..d2 (ESPN принимает только одиночную дату dates=YYYYMMDD)."""
    from concurrent.futures import ThreadPoolExecutor

    days, d = [], d1.date()
    while d <= d2.date():
        days.append(d)
        d += timedelta(days=1)
    out = {}
    with ThreadPoolExecutor(max_workers=min(6, len(days))) as ex:
        for evs in ex.map(lambda x: _espn_day(path, x), days):
            for e in evs:
                out[str(e["id"])] = e
    return list(out.values())


def parse_event(e, has_draw):
    comp = (e.get("competitions") or [None])[0]
    if not comp:
        return None
    teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    if "home" not in teams or "away" not in teams:
        return None
    return {
        "id": str(e["id"]), "home": teams["home"]["team"]["displayName"], "away": teams["away"]["team"]["displayName"],
        "start": datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(timezone.utc),
        "state": (e.get("status") or {}).get("type", {}).get("state"),
        "odds": parse_espn_odds(comp, has_draw), "comp": comp,
    }


# ------------------------------- PandaScore (Эло) -------------------------------
def _panda(path, params=None):
    r = requests.get("https://api.pandascore.co" + path, params=params or {}, timeout=20,
                     headers={"Authorization": f"Bearer {PANDA_TOKEN}"})
    r.raise_for_status()
    return r.json()


def elo_ratings(game):
    past = _panda(f"/{game}/matches/past", {"per_page": 100, "sort": "-begin_at", "filter[status]": "finished"})
    rating = {}
    for m in reversed(past):
        opp = [o["opponent"]["id"] for o in m.get("opponents", []) if o.get("opponent")]
        w = m.get("winner_id")
        if len(opp) != 2 or w not in opp:
            continue
        a, b = opp
        ra, rb = rating.get(a, 1500), rating.get(b, 1500)
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        sa = 1.0 if w == a else 0.0
        rating[a] = ra + 24 * (sa - ea)
        rating[b] = rb + 24 * ((1 - sa) - (1 - ea))
    return rating


def panda_matches(game, label):
    rating = elo_ratings(game)
    out = []
    for m in _panda(f"/{game}/matches/upcoming", {"per_page": 25, "sort": "begin_at"}):
        opp = [o["opponent"] for o in m.get("opponents", []) if o.get("opponent")]
        if len(opp) != 2 or not m.get("begin_at"):
            continue
        ra, rb = rating.get(opp[0]["id"], 1500), rating.get(opp[1]["id"], 1500)
        pa = 1 / (1 + 10 ** ((rb - ra) / 400))
        pa = min(0.9, max(0.1, pa))
        o1, o2 = with_margin([1 / pa, 1 / (1 - pa)])
        out.append({
            "id": f"ps:{game}:{m['id']}", "home": opp[0]["name"], "away": opp[1]["name"],
            "start": datetime.fromisoformat(m["begin_at"].replace("Z", "+00:00")).astimezone(timezone.utc),
            "odds": (o1, None, o2), "sport": label, "league": ((m.get("league") or {}).get("name") or label),
            "path": f"panda/{game}",
        })
    return out


# ------------------------------- синхронизация -------------------------------
def _upsert(conn, source, ext, sport, league, path, home, away, start, odds, stats, notes):
    row = one(conn, "SELECT event_id FROM feed_events WHERE ext_id=?", (ext,))
    if row:
        ev = one(conn, "SELECT status, odds_1, odds_x, odds_2 FROM events WHERE id=?", (row[0],))
        if ev and ev[0] == "open" and (abs(ev[1] - odds[0]) >= 0.01 or abs((ev[3] or 0) - odds[2]) >= 0.01 or abs((ev[2] or 0) - (odds[1] or 0)) >= 0.01):
            conn.execute("UPDATE events SET odds_1=?, odds_x=?, odds_2=? WHERE id=?", (odds[0], odds[1], odds[2], row[0]))
            conn.execute("UPDATE feed_events SET updated_at=? WHERE event_id=?", (core.now_iso(), row[0]))
            notes.extend(betting.odds_changed(conn, row[0], odds))
            stats["updated"] += 1
        return
    open_cnt = one(conn, "SELECT COUNT(*) FROM feed_events f JOIN events e ON e.id=f.event_id WHERE e.status='open'")[0]
    if open_cnt >= MAX_OPEN:
        return
    eid = conn.execute(
        "INSERT INTO events (sport, title, starts_at, status, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (sport, f"{home} — {away}", start.strftime(ISO), "open", odds[0], odds[1], odds[2], core.now_iso()),
    ).lastrowid
    conn.execute(
        "INSERT INTO feed_events (event_id, source, ext_id, league, sport_path, home, away, starts_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (eid, source, ext, league, path, home, away, start.strftime(ISO), core.now_iso()),
    )
    stats["new"] += 1
    stats["fresh"].append((sport, league, home, away, odds))


def sync_once():
    stats = {"new": 0, "updated": 0, "fresh": []}
    errors, notes = [], []
    now = _now()
    d1, d2 = now - timedelta(hours=1), now + timedelta(hours=WINDOW_H)
    for path, sport, league, has_draw in LEAGUES:
        try:
            events = espn_fetch(path, d1, d2)
            parsed = [p for p in (parse_event(e, has_draw) for e in events) if p and p["state"] == "pre" and p["odds"] and now < p["start"] <= d2]
            parsed.sort(key=lambda p: p["start"])
            with db() as conn:
                for p in parsed[:PER_LEAGUE]:
                    _upsert(conn, "espn", f"espn:{path}:{p['id']}", sport, league, path, p["home"], p["away"], p["start"], p["odds"], stats, notes)
                core.notify_many(conn, notes)
                notes.clear()
        except Exception as e:
            errors.append(f"{path}: {e}")
            log.warning("ESPN %s: %s", path, e)
    if PANDA_TOKEN:
        for game, label in PANDA_GAMES:
            try:
                parsed = [p for p in panda_matches(game, label) if now < p["start"] <= d2]
                with db() as conn:
                    for p in parsed[:PER_LEAGUE]:
                        _upsert(conn, "panda", p["id"], p["sport"], p["league"], p["path"], p["home"], p["away"], p["start"], p["odds"], stats, notes)
                    core.notify_many(conn, notes)
                    notes.clear()
            except Exception as e:
                errors.append(f"pandascore/{game}: {e}")
                log.warning("PandaScore %s: %s", game, e)
    status.update(last_sync=now.isoformat(), last_added=stats["new"], last_updated=stats["updated"], errors=errors[-6:])
    status["total_added"] += stats["new"]
    if BROADCAST and stats["fresh"]:
        _broadcast(stats["fresh"][:6])
    return stats


def _broadcast(fresh):
    if not core.ctx.bot:
        return
    text = "🔥 <b>Новые матчи в MSPBETS!</b>\n\n" + "\n\n".join(
        f"🏆 <b>{lg}</b>\n⚔️ {h} — {a}\nП1: <b>{o[0]}</b>" + (f" | X: <b>{o[1]}</b>" if o[1] else "") + f" | П2: <b>{o[2]}</b>"
        for _, lg, h, a, o in fresh
    )
    if core.ctx.webapp_url:
        text += f"\n\n👉 <a href='{core.ctx.webapp_url}'>Сделать ставку</a>"
    with db() as conn:
        users = [(r[0], text) for r in all_(conn, "SELECT user_id FROM users")]
        core.notify_many(conn, users)


def close_started():
    """Приём ставок закрывается в момент начала матча."""
    with db() as conn:
        conn.execute("UPDATE events SET status='closed' WHERE status='open' AND starts_at IS NOT NULL AND starts_at<=?", (_now().strftime(ISO),))


def _result_from_scores(home, away, has_draw):
    if home > away:
        return "1"
    if away > home:
        return "2"
    return "X" if has_draw else "void"


def settle_finished():
    """Рассчитывает завершённые матчи автозагрузки."""
    now = _now()
    cutoff = (now - timedelta(minutes=40)).strftime(ISO)
    with db() as conn:
        rows = all_(
            conn,
            "SELECT f.event_id, f.ext_id, f.sport_path, f.starts_at, f.source FROM feed_events f JOIN events e ON e.id=f.event_id "
            "WHERE e.status IN ('open','closed') AND f.starts_at<=?",
            (cutoff,),
        )
    if not rows:
        status["last_settle"] = now.isoformat()
        return 0
    draw_by_path = {l[0]: l[3] for l in LEAGUES}
    cache = {}
    done = 0
    for event_id, ext, path, starts, source in rows:
        try:
            start = datetime.fromisoformat(starts)
            result = None
            if source == "espn":
                key = (path, start.date())
                if key not in cache:
                    cache[key] = {str(e["id"]): e for e in espn_fetch(path, start - timedelta(days=1), start + timedelta(days=1))}
                e = cache[key].get(ext.rsplit(":", 1)[-1])
                if e:
                    st = (e.get("status") or {}).get("type", {})
                    comp = e["competitions"][0]
                    if st.get("completed"):
                        sc = {c["homeAway"]: float(c.get("score") or 0) for c in comp["competitors"]}
                        result = _result_from_scores(sc["home"], sc["away"], draw_by_path.get(path, False))
                    elif st.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED"):
                        result = "void"
            elif source == "panda":
                m = _panda(f"/matches/{ext.rsplit(':', 1)[-1]}")
                if m.get("status") == "finished" and m.get("winner_id"):
                    opp = [o["opponent"]["id"] for o in m.get("opponents", [])]
                    result = "1" if m["winner_id"] == opp[0] else "2"
                elif m.get("status") in ("canceled", "postponed"):
                    result = "void"
            if result is None and now - start > timedelta(days=5):
                result = "void"  # результат так и не появился — возвращаем ставки
            if result:
                with db() as conn:
                    ev = one(conn, "SELECT status FROM events WHERE id=?", (event_id,))
                    if ev and ev[0] != "settled":
                        core.notify_many(conn, betting.settle_event_core(conn, event_id, result))
                        done += 1
        except Exception as e:
            log.warning("settle %s: %s", ext, e)
    status["last_settle"] = now.isoformat()
    status["total_settled"] += done
    return done


async def scheduler():
    if not ENABLED:
        log.info("Автозагрузка матчей отключена (FEED_ENABLED=0)")
        return
    await asyncio.sleep(15)
    last_sync = last_settle = 0.0
    while True:
        t = time.time()
        try:
            await asyncio.to_thread(close_started)
            if t - last_sync >= SYNC_MIN * 60:
                last_sync = t
                await asyncio.to_thread(sync_once)
            if t - last_settle >= 300:
                last_settle = t
                await asyncio.to_thread(settle_finished)
        except Exception as e:
            log.exception("feed scheduler: %s", e)
        await asyncio.sleep(30)


def _admin(request):
    user = auth(request)
    if int(user["id"]) not in core.ctx.admin_ids:
        raise HTTPException(403, "Только для админа")


@router.get("/status")
def feed_status(request: Request):
    _admin(request)
    with db() as conn:
        n = one(conn, "SELECT COUNT(*) FROM feed_events f JOIN events e ON e.id=f.event_id WHERE e.status='open'")[0]
    return {**status, "open_events": n, "sync_minutes": SYNC_MIN, "margin": MARGIN, "leagues": [l[2] for l in LEAGUES]}


@router.post("/sync")
def feed_sync(request: Request):
    _admin(request)
    close_started()
    s = sync_once()
    settled = settle_finished()
    return {"ok": True, "new": s["new"], "updated": s["updated"], "settled": settled, "errors": status["errors"]}
