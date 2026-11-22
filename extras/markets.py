"""
extras/markets.py — дополнительные рынки на матчи «как в настоящих БК».

Что есть (для матчей автозагрузки ESPN: футбол, NBA, NHL):
  * Футбол: двойной шанс, тоталы 1.5 / 2.5 / 3.5, форы ±1.5, обе забьют, индивидуальные тоталы, точный счёт.
  * Баскетбол (NBA): форы и тоталы очков (3 линии), индивидуальные тоталы.
  * Хоккей (NHL): тоталы шайб, пак-лайн ±1.5, индивидуальные тоталы.

Откуда коэффициенты:
  * основа — те же коэффициенты П1 / X / П2, что и на карточке матча (рыночные, с ESPN);
  * из них подбирается модель счёта: футбол и хоккей — Пуассон (ожидаемые голы хозяев/гостей),
    баскетбол — нормальное распределение разницы и суммы очков;
  * если у ESPN есть линия тотала (overUnder) — модель подгоняется и под неё;
  * на каждый рынок накладывается маржа MARKETS_MARGIN (по умолчанию 6%, точный счёт — 15%).
  Коэффициенты пересчитываются при каждом обновлении основной линии матча.

Расчёт: после того как основной исход матча рассчитан (автоматически или админом), берём итоговый счёт
с ESPN (тот же источник) и рассчитываем рынки. Все линии «половинчатые» (2.5, −1.5 …) — возвратов нет.
Отмена матча → возврат ставок. Админ может ввести счёт вручную: POST /api/v2/markets/admin/score.

Старые таблицы и код ставок не меняются: свои таблицы market_bets / market_lines / match_scores,
баланс — только через core.adjust (журнал операций), XP/задания/турниры — через core.on_wager.
"""
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import all_, auth, db, money, now_iso, one, touch_user, user_lock

log = logging.getLogger("mspbets.markets")
router = APIRouter(prefix="/api/v2/markets")

MARGIN = float(os.getenv("MARKETS_MARGIN", "0.06"))
MARGIN_CS = float(os.getenv("MARKETS_MARGIN_CS", "0.15"))
MAX_BET = int(os.getenv("MARKETS_MAX_BET", "1000000"))
MAX_ODDS = 101.0
MIN_PROB = 0.009  # исходы с меньшей вероятностью (кэф > ~100) не показываем

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS market_bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, event_id INTEGER NOT NULL,
        market TEXT NOT NULL, pick TEXT NOT NULL, label TEXT NOT NULL, amount INTEGER NOT NULL,
        odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', payout INTEGER NOT NULL DEFAULT 0,
        score TEXT, created_at TEXT NOT NULL, settled_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_mb_event ON market_bets(event_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_mb_user ON market_bets(user_id, id)",
    """CREATE TABLE IF NOT EXISTS market_lines (
        event_id INTEGER PRIMARY KEY, total REAL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS match_scores (
        event_id INTEGER PRIMARY KEY, home INTEGER NOT NULL, away INTEGER NOT NULL,
        source TEXT, created_at TEXT NOT NULL)""",
]

RULES = {
    "football": "Футбол: рынки рассчитываются по итоговому счёту матча (тот же, по которому рассчитывается П1/X/П2).",
    "basketball": "Баскетбол: с учётом овертаймов.",
    "hockey": "Хоккей: по итоговому счёту с учётом овертайма и буллитов (победный буллит = +1 шайба).",
}
SPORT_OF_PATH = (("soccer/", "football"), ("basketball/", "basketball"), ("hockey/", "hockey"))


def sport_kind(path):
    for prefix, kind in SPORT_OF_PATH:
        if (path or "").startswith(prefix):
            return kind
    return None


# ------------------------------------------------------------------
# Математика
# ------------------------------------------------------------------
def fair(o1, ox, o2):
    """Коэффициенты → вероятности без маржи."""
    inv = [1 / o1, (1 / ox) if ox else 0.0, 1 / o2]
    s = sum(inv)
    return [x / s for x in inv]


def _pois(lam, n):
    p = [math.exp(-lam)]
    for k in range(1, n + 1):
        p.append(p[-1] * lam / k)
    return p


def score_matrix(lh, la, n=10):
    ph, pa = _pois(lh, n), _pois(la, n)
    m = [[ph[i] * pa[j] for j in range(n + 1)] for i in range(n + 1)]
    s = sum(map(sum, m))
    return [[v / s for v in row] for row in m]


def _nelder_mead(f, x0, step=0.35, iters=160):
    pts = [list(x0), [x0[0] + step, x0[1]], [x0[0], x0[1] + step]]
    vals = [f(p) for p in pts]
    for _ in range(iters):
        order = sorted(range(3), key=lambda i: vals[i])
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if vals[2] - vals[0] < 1e-13:
            break
        c = [(pts[0][k] + pts[1][k]) / 2 for k in range(2)]
        xr = [c[k] + (c[k] - pts[2][k]) for k in range(2)]
        fr = f(xr)
        if fr < vals[0]:
            xe = [c[k] + 2 * (c[k] - pts[2][k]) for k in range(2)]
            fe = f(xe)
            pts[2], vals[2] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[1]:
            pts[2], vals[2] = xr, fr
        else:
            xc = [c[k] + 0.5 * (pts[2][k] - c[k]) for k in range(2)]
            fc = f(xc)
            if fc < vals[2]:
                pts[2], vals[2] = xc, fc
            else:
                for i in (1, 2):
                    pts[i] = [pts[0][k] + 0.5 * (pts[i][k] - pts[0][k]) for k in range(2)]
                    vals[i] = f(pts[i])
    i = min(range(3), key=lambda i: vals[i])
    return pts[i]


def final_dist(m, q=0.5):
    """Хоккей: ничья в основное время → овертайм/буллиты, победитель получает +1 шайбу."""
    out = {}
    n = len(m)
    for i in range(n):
        for j in range(n):
            p = m[i][j]
            if i == j:
                out[(i + 1, j)] = out.get((i + 1, j), 0) + p * q
                out[(i, j + 1)] = out.get((i, j + 1), 0) + p * (1 - q)
            else:
                out[(i, j)] = out.get((i, j), 0) + p
    return out


def _matrix_dist(m):
    return {(i, j): m[i][j] for i in range(len(m)) for j in range(len(m))}


_fit_cache = {}


def fit_goals(kind, probs, total_line=None):
    """Подбор ожидаемых голов (λ хозяев, λ гостей) под вероятности исходов и линию тотала."""
    key = (kind, tuple(round(p, 4) for p in probs), total_line)
    hit = _fit_cache.get(key)
    if hit:
        return hit
    pH, pD, pA = probs
    prior = 2.6 if kind == "football" else 6.0
    n = 10 if kind == "football" else 12

    def f(x):
        lh, la = math.exp(x[0]), math.exp(x[1])
        if not (0.02 < lh < 9 and 0.02 < la < 9):
            return 10.0
        m = score_matrix(lh, la, n)
        if kind == "football":
            d = _matrix_dist(m)
            H = sum(p for (i, j), p in d.items() if i > j)
            D = sum(p for (i, j), p in d.items() if i == j)
            err = (H - pH) ** 2 + (D - pD) ** 2 + (1 - H - D - pA) ** 2
        else:  # хоккей: двусторонний рынок, ничьей нет
            d = final_dist(m)
            H = sum(p for (i, j), p in d.items() if i > j)
            err = 2 * (H - pH) ** 2
        if total_line:
            over = sum(p for (i, j), p in d.items() if i + j > total_line)
            err += 0.6 * (over - 0.5) ** 2
        else:
            err += 0.002 * (lh + la - prior) ** 2
        return err

    x = _nelder_mead(f, [math.log(prior / 2), math.log(prior / 2)])
    res = (math.exp(x[0]), math.exp(x[1]))
    if len(_fit_cache) > 3000:
        _fit_cache.clear()
    _fit_cache[key] = res
    return res


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def ninv(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    lo, hi = -8.0, 8.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if ncdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def half(x):
    """Ближайшая «половинчатая» линия (…, 2.5, 3.5, …) — чтобы не было возвратов."""
    return math.floor(x) + 0.5


def price(p, margin=MARGIN):
    if p <= 0:
        return None
    return round(min(MAX_ODDS, max(1.01, 1 / (p * (1 + margin)))), 2)


def fmt_line(v):
    s = f"{v:+.1f}" if v else "0"
    return s.replace("-", "−")


# ------------------------------------------------------------------
# Построение рынков
# ------------------------------------------------------------------
def _item(market, pick, label, p, margin=MARGIN):
    if p < MIN_PROB or p > 1 - 0.005:
        return None
    return {"market": market, "pick": pick, "label": label, "odds": price(p, margin), "prob": p}


def _two(group, market, a, b):
    """Пара взаимоисключающих исходов. a, b = (pick, label, prob)."""
    ia, ib = _item(market, *a), _item(market, *b)
    if ia and ib:
        group["items"].extend([ia, ib])


def _dist_markets(kind, dist, home, away, total_lines, hcp_lines, it_lines, with_cs, with_btts):
    groups = []
    g = {"key": "total", "title": "Тотал голов" if kind == "football" else "Тотал шайб", "items": []}
    for L in total_lines:
        over = sum(p for (i, j), p in dist.items() if i + j > L)
        _two(g, f"total:{L}", ("over", f"Больше {L}", over), ("under", f"Меньше {L}", 1 - over))
    groups.append(g)
    g = {"key": "hcp", "title": "Фора" if kind == "football" else "Фора (пак-лайн)", "items": []}
    for h in hcp_lines:
        p1 = sum(p for (i, j), p in dist.items() if i + h > j)
        _two(g, f"hcp:{h}", ("1", f"{home} ({fmt_line(h)})", p1), ("2", f"{away} ({fmt_line(-h)})", 1 - p1))
    groups.append(g)
    if with_btts:
        g = {"key": "btts", "title": "Обе забьют", "items": []}
        yes = sum(p for (i, j), p in dist.items() if i > 0 and j > 0)
        _two(g, "btts", ("yes", "Да", yes), ("no", "Нет", 1 - yes))
        groups.append(g)
    g = {"key": "itotal", "title": "Индивидуальный тотал", "items": []}
    for L in it_lines:
        o1 = sum(p for (i, j), p in dist.items() if i > L)
        _two(g, f"it1:{L}", ("over", f"{home} больше {L}", o1), ("under", f"{home} меньше {L}", 1 - o1))
    for L in it_lines:
        o2 = sum(p for (i, j), p in dist.items() if j > L)
        _two(g, f"it2:{L}", ("over", f"{away} больше {L}", o2), ("under", f"{away} меньше {L}", 1 - o2))
    groups.append(g)
    if with_cs:
        g = {"key": "cs", "title": "Точный счёт", "items": []}
        rest = 1.0
        for i in range(4):
            for j in range(4):
                p = dist.get((i, j), 0)
                it = _item("cs", f"{i}:{j}", f"{i}:{j}", p, MARGIN_CS)
                if it:
                    g["items"].append(it)
                rest -= p
        it = _item("cs", "other", "Другой счёт", max(0.0, rest), MARGIN_CS)
        if it:
            g["items"].append(it)
        groups.append(g)
    return groups


def build_markets(kind, o1, ox, o2, home, away, total_line=None):
    """Список групп рынков для матча. Детерминированно зависит от кэфов и линии тотала."""
    if not o1 or not o2 or o1 <= 1 or o2 <= 1:
        return []
    probs = fair(o1, ox, o2)
    if kind == "football":
        if not ox:
            return []
        lh, la = fit_goals("football", probs, total_line)
        dist = _matrix_dist(score_matrix(lh, la, 10))
        pH, pD, pA = probs
        dc = {"key": "dc", "title": "Двойной шанс", "items": [x for x in (
            _item("dc", "1X", f"1X · {home} или ничья", pH + pD),
            _item("dc", "12", "12 · без ничьей", pH + pA),
            _item("dc", "X2", f"X2 · ничья или {away}", pD + pA)) if x]}
        lines = [1.5, 2.5, 3.5]
        if total_line and half(total_line) not in lines and 0.5 < total_line < 7:
            lines = sorted(lines + [half(total_line)])
        return [dc] + _dist_markets(kind, dist, home, away, lines, [-1.5, 1.5], [0.5, 1.5], True, True)
    if kind == "hockey":
        lh, la = fit_goals("hockey", [probs[0], 0.0, probs[2]], total_line)
        dist = final_dist(score_matrix(lh, la, 12))
        lines = [4.5, 5.5, 6.5, 7.5]
        return _dist_markets(kind, dist, home, away, lines, [-1.5, 1.5], [2.5, 3.5], False, False)
    if kind == "basketball":
        pH = probs[0] / (probs[0] + probs[2])
        sd_m, sd_t, sd_i = 12.0, 18.0, 11.0
        mu = sd_m * ninv(pH)  # ожидаемая разница (хозяева − гости)
        T = float(total_line) if total_line and 150 < float(total_line) < 300 else 226.0
        g_h = {"key": "hcp", "title": "Фора", "items": []}
        h0 = half(-mu)
        for h in (h0 - 4, h0, h0 + 4):
            p1 = ncdf((mu + h) / sd_m)
            _two(g_h, f"hcp:{h}", ("1", f"{home} ({fmt_line(h)})", p1), ("2", f"{away} ({fmt_line(-h)})", 1 - p1))
        g_t = {"key": "total", "title": "Тотал очков", "items": []}
        t0 = half(T)
        for L in (t0 - 6, t0, t0 + 6):
            over = 1 - ncdf((L - T) / sd_t)
            _two(g_t, f"total:{L}", ("over", f"Больше {L}", over), ("under", f"Меньше {L}", 1 - over))
        g_i = {"key": "itotal", "title": "Индивидуальный тотал", "items": []}
        for idx, (name, mean) in enumerate(((home, (T + mu) / 2), (away, (T - mu) / 2)), 1):
            L = half(mean)
            over = 1 - ncdf((L - mean) / sd_i)
            _two(g_i, f"it{idx}:{L}", ("over", f"{name} больше {L}", over), ("under", f"{name} меньше {L}", 1 - over))
        return [g_h, g_t, g_i]
    return []


def grade(market, pick, h, a):
    """'won' | 'lost' по итоговому счёту (h — хозяева, a — гости)."""
    kind, _, arg = market.partition(":")
    if kind == "total":
        L = float(arg)
        win = (h + a > L) if pick == "over" else (h + a < L)
    elif kind == "it1":
        win = (h > float(arg)) if pick == "over" else (h < float(arg))
    elif kind == "it2":
        win = (a > float(arg)) if pick == "over" else (a < float(arg))
    elif kind == "hcp":
        x = float(arg)
        win = (h + x > a) if pick == "1" else (h + x < a)
    elif kind == "btts":
        both = h > 0 and a > 0
        win = both if pick == "yes" else not both
    elif kind == "dc":
        win = {"1X": h >= a, "12": h != a, "X2": a >= h}.get(pick, False)
    elif kind == "cs":
        if pick == "other":
            win = h > 3 or a > 3
        else:
            try:
                ph, pa = (int(x) for x in pick.split(":"))
            except Exception:
                return "lost"
            win = (h, a) == (ph, pa)
    else:
        return "lost"
    return "won" if win else "lost"


# ------------------------------------------------------------------
# Данные матчей
# ------------------------------------------------------------------
def _event_info(conn, event_id):
    r = one(conn, """SELECT e.id, e.title, e.sport, e.status, e.odds_1, e.odds_x, e.odds_2, e.starts_at, e.result,
                     f.source, f.sport_path, f.home, f.away, f.league, f.ext_id, l.total
                     FROM events e LEFT JOIN feed_events f ON f.event_id=e.id LEFT JOIN market_lines l ON l.event_id=e.id
                     WHERE e.id=?""", (int(event_id),))
    if not r:
        return None
    keys = ["id", "title", "sport", "status", "o1", "ox", "o2", "starts_at", "result", "source", "path", "home", "away", "league", "ext_id", "total"]
    d = dict(zip(keys, r))
    if not d["home"] or not d["away"]:
        parts = [x.strip() for x in str(d["title"]).replace("–", "—").split("—")]
        d["home"], d["away"] = (parts[0], parts[1]) if len(parts) == 2 else ("Хозяева", "Гости")
    d["kind"] = sport_kind(d["path"]) if d["source"] == "espn" else None
    return d


def _started(starts_at):
    if not starts_at:
        return False
    try:
        return datetime.fromisoformat(starts_at) <= datetime.now(timezone.utc)
    except Exception:
        return False


def markets_for(ev):
    if not ev or not ev["kind"]:
        return []
    return build_markets(ev["kind"], ev["o1"], ev["ox"], ev["o2"], ev["home"], ev["away"], ev["total"])


def _public_groups(groups):
    return [{"key": g["key"], "title": g["title"],
             "items": [{k: it[k] for k in ("market", "pick", "label", "odds")} for it in g["items"]]}
            for g in groups if g["items"]]


_avail_cache = {"ts": 0.0, "data": {}}


def invalidate():
    _avail_cache["ts"] = 0.0


# ------------------------------------------------------------------
# API
# ------------------------------------------------------------------
@router.get("/available")
def available(request: Request):
    """{event_id: число исходов} для открытых матчей, у которых есть доп. рынки."""
    auth(request)
    if time.monotonic() - _avail_cache["ts"] < 30:
        return _avail_cache["data"]
    out = {}
    with db() as conn:
        rows = all_(conn, """SELECT e.id FROM events e JOIN feed_events f ON f.event_id=e.id
                             WHERE e.status='open' AND f.source='espn' LIMIT 300""")
        for (eid,) in rows:
            ev = _event_info(conn, eid)
            if ev and not _started(ev["starts_at"]):
                n = sum(len(g["items"]) for g in markets_for(ev))
                if n:
                    out[str(eid)] = n
    _avail_cache.update(ts=time.monotonic(), data=out)
    return out


def _bet_row(r):
    return {"id": r[0], "event_id": r[1], "market": r[2], "pick": r[3], "label": r[4], "full_label": full_label(r[2], r[4]), "amount": r[5], "odds": r[6],
            "status": r[7], "payout": r[8], "score": r[9], "created_at": r[10], "settled_at": r[11],
            "title": r[12] if len(r) > 12 else None, "sport": r[13] if len(r) > 13 else None}


_BET_COLS = "mb.id, mb.event_id, mb.market, mb.pick, mb.label, mb.amount, mb.odds, mb.status, mb.payout, mb.score, mb.created_at, mb.settled_at"


@router.get("/event/{event_id}")
def event_markets(event_id: int, request: Request):
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        ev = _event_info(conn, event_id)
        if not ev:
            raise HTTPException(404, "Матч не найден")
        mine = all_(conn, f"SELECT {_BET_COLS} FROM market_bets mb WHERE mb.user_id=? AND mb.event_id=? ORDER BY mb.id DESC LIMIT 20", (uid, event_id))
    open_ = ev["status"] == "open" and not _started(ev["starts_at"])
    return {
        "event_id": ev["id"], "title": ev["title"], "sport": ev["sport"], "league": ev["league"], "starts_at": ev["starts_at"],
        "home": ev["home"], "away": ev["away"], "open": open_, "kind": ev["kind"],
        "groups": _public_groups(markets_for(ev)) if open_ else [],
        "main": main_items(ev) if open_ else [],
        "rules": RULES.get(ev["kind"] or "", ""), "margin": round(MARGIN * 100, 1),
        "my_bets": [_bet_row(r) for r in mine], "max_bet": MAX_BET,
    }


def main_items(ev):
    """Основной исход матча (П1 / X / П2) в формате рынков — для экспресса с рынками."""
    if not ev:
        return []
    out = []
    for pick, odds, label in (("1", ev["o1"], f"П1 · {ev['home']}"), ("X", ev["ox"], "Ничья"), ("2", ev["o2"], f"П2 · {ev['away']}")):
        try:
            odds = round(float(odds), 2)
        except (TypeError, ValueError):
            continue
        if odds > 1:
            out.append({"market": "1x2", "pick": pick, "label": label, "odds": odds})
    return out


MARKET_TITLES = {"dc": "Двойной шанс", "total": "Тотал", "hcp": "Фора", "btts": "Обе забьют", "it1": "Инд. тотал", "it2": "Инд. тотал", "cs": "Точный счёт"}


def full_label(market, label):
    """Подпись с названием рынка — для экспрессов, профилей и ленты: «Тотал: Больше 2.5», «Обе забьют: Да»."""
    t = MARKET_TITLES.get(str(market).split(":")[0])
    if not t or str(label).startswith(t):
        return label
    return f"{t} {label}" if market == "cs" else f"{t}: {label}"


def find_item(ev, market, pick):
    """Исход по ключу рынка (включая основной «1x2») с текущим коэффициентом или None."""
    if market == "1x2":
        return next((it for it in main_items(ev) if it["pick"] == pick), None)
    return next((it for g in markets_for(ev) for it in g["items"] if it["market"] == market and it["pick"] == pick), None)


def place_market_bet(conn, uid, event_id, market, pick, amount, seen_odds=None, note_prefix="Ставка"):
    """Одинар на доп. рынок (внутри транзакции и user_lock вызывающего). Возвращает (id, item)."""
    ev = _event_info(conn, event_id)
    if not ev or ev["status"] != "open" or _started(ev["starts_at"]):
        raise HTTPException(400, "Приём ставок закрыт")
    item = None if market == "1x2" else find_item(ev, market, pick)
    if not item:
        raise HTTPException(400, "Рынок недоступен")
    odds = item["odds"]
    if seen_odds is not None and abs(seen_odds - odds) > 0.004:
        raise HTTPException(409, f"Коэффициент изменился: {seen_odds:.2f} → {odds:.2f}")
    core.adjust(conn, uid, -amount, "bet", f"{note_prefix}: {ev['title']} — {item['label']} (кф {odds:.2f})", ref=f"event:{event_id}")
    bid = conn.execute(
        "INSERT INTO market_bets (user_id, event_id, market, pick, label, amount, odds, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, event_id, market, pick, item["label"], amount, odds, now_iso()),
    ).lastrowid
    core.on_wager(conn, uid, amount, "match")
    core.quest_event(conn, uid, "match_bet", 1)
    return bid, item


@router.post("/bet")
def place_bet(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    try:
        event_id = int(body.get("event_id"))
        market, pick = str(body.get("market") or ""), str(body.get("pick") or "")
        seen_odds = float(body["odds"]) if body.get("odds") is not None else None
    except Exception:
        raise HTTPException(400, "Неверные данные")
    amount = core.check_amount(body.get("amount"), 1, MAX_BET)
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        bid, item = place_market_bet(conn, uid, event_id, market, pick, amount, seen_odds)
        bal = core.balance_of(conn, uid)
    return {"ok": True, "id": bid, "odds": item["odds"], "label": item["label"], "balance": bal,
            "potential": core.pay(amount, item["odds"]), "unlocked": core.pop_unlocked()}


@router.get("/my")
def my_bets(request: Request, limit: int = 50):
    user = auth(request)
    limit = max(1, min(int(limit), 100))
    with db() as conn:
        rows = all_(conn, f"""SELECT {_BET_COLS}, e.title, e.sport FROM market_bets mb JOIN events e ON e.id=mb.event_id
                             WHERE mb.user_id=? ORDER BY mb.id DESC LIMIT ?""", (int(user["id"]), limit))
        stats = one(conn, """SELECT COUNT(*), COALESCE(SUM(amount),0), COALESCE(SUM(payout),0),
                             COALESCE(SUM(CASE WHEN status='won' THEN 1 ELSE 0 END),0) FROM market_bets WHERE user_id=?""", (int(user["id"]),))
    return {"bets": [_bet_row(r) for r in rows],
            "stats": {"count": stats[0], "staked": stats[1], "returned": stats[2], "won": stats[3]}}


# ------------------------------------------------------------------
# Расчёт
# ------------------------------------------------------------------
def _espn_score(conn, ev, cache):
    """Итоговый счёт с ESPN (тот же источник, что и автозагрузка). None — если ещё нет."""
    from . import feed  # поздний импорт: feed тянет requests и esports-модель

    if ev["source"] != "espn" or not ev["ext_id"] or not ev["path"] or not ev["starts_at"]:
        return None
    start = datetime.fromisoformat(ev["starts_at"])
    key = (ev["path"], start.date())
    if key not in cache:
        cache[key] = {str(e["id"]): e for e in feed.espn_fetch(ev["path"], start - timedelta(days=1), start + timedelta(days=1))}
    e = cache[key].get(ev["ext_id"].rsplit(":", 1)[-1])
    if not e:
        return None
    st = (e.get("status") or {}).get("type", {})
    if not st.get("completed"):
        return None
    comp = e["competitions"][0]
    sc = {c["homeAway"]: int(float(c.get("score") or 0)) for c in comp["competitors"]}
    if "home" not in sc or "away" not in sc:
        return None
    return sc["home"], sc["away"]


def settle_event_bets(conn, event_id, h=None, a=None, void=False):
    """Рассчитать все ожидающие ставки на доп. рынки матча. Возвращает уведомления [(uid, text)]."""
    ev = one(conn, "SELECT title FROM events WHERE id=?", (int(event_id),))
    title = ev[0] if ev else f"Матч #{event_id}"
    notes = []
    rows = all_(conn, "SELECT id, user_id, market, pick, label, amount, odds FROM market_bets WHERE event_id=? AND status='pending'", (int(event_id),))
    score_txt = None if void else f"{h}:{a}"
    for bid, uid, market, pick, label, amount, odds in rows:
        status = "void" if void else grade(market, pick, h, a)
        payout = amount if status == "void" else core.pay(amount, odds) if status == "won" else 0
        cur = conn.execute("UPDATE market_bets SET status=?, payout=?, score=?, settled_at=? WHERE id=? AND status='pending'",
                           (status, payout, score_txt, now_iso(), bid))
        if core.changed(cur, conn) != 1:
            continue
        if status == "void":
            core.adjust(conn, uid, amount, "refund", f"Возврат (матч отменён): {title} — {label}", ref=f"mbet:{bid}")
            notes.append((uid, f"↩️ <b>Матч отменён</b>\n{title}\n{label}: ставка <b>{money(amount)} PTS</b> возвращена."))
        elif status == "won":
            core.adjust(conn, uid, payout, "win", f"Выигрыш: {title} — {label}", ref=f"mbet:{bid}")
            core.quest_event(conn, uid, "single_won", 1)
            core.bump(conn, uid, "single_won", 1)
            core.set_max(conn, uid, "biggest_win", payout)
            notes.append((uid, f"✅ <b>Ставка сыграла!</b>\n{title} · счёт {score_txt}\n{label} · кф {odds:.2f}\nВыигрыш: <b>+{money(payout)} PTS</b>"))
        else:
            notes.append((uid, f"❌ <b>Ставка не сыграла</b>\n{title} · счёт {score_txt}\n{label} · {money(amount)} PTS"))
    return notes


def settle_pending():
    """Фоновая задача: рассчитать доп. рынки по завершённым матчам."""
    with db() as conn:
        ids = [r[0] for r in all_(conn, "SELECT DISTINCT event_id FROM market_bets WHERE status='pending'")]
    if not ids:
        return 0
    cache, done = {}, 0
    now = datetime.now(timezone.utc)
    for eid in ids:
        try:
            with db() as conn:
                ev = _event_info(conn, eid)
                if not ev:
                    notes = settle_event_bets(conn, eid, void=True)
                elif ev["status"] == "settled" and ev["result"] == "void":
                    notes = settle_event_bets(conn, eid, void=True)
                elif ev["status"] == "settled":
                    sc = one(conn, "SELECT home, away FROM match_scores WHERE event_id=?", (eid,))
                    if not sc:
                        sc = _espn_score(conn, ev, cache)
                        if sc:
                            conn.execute("INSERT OR IGNORE INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?)",
                                         (eid, sc[0], sc[1], "espn", now_iso()))
                    if sc:
                        notes = settle_event_bets(conn, eid, sc[0], sc[1])
                    elif ev["starts_at"] and now - datetime.fromisoformat(ev["starts_at"]) > timedelta(days=5):
                        notes = settle_event_bets(conn, eid, void=True)  # счёт так и не появился
                    else:
                        notes = []
                else:
                    notes = []
                if notes:
                    done += 1
                    core.notify_many(conn, notes)
        except Exception as e:
            log.warning("markets settle %s: %s", eid, e)
    return done


def refresh_lines():
    """Подтянуть линии тотала (overUnder) ESPN для открытых матчей — модель подстроится под рынок."""
    from . import feed

    with db() as conn:
        rows = all_(conn, """SELECT f.event_id, f.ext_id, f.sport_path, f.starts_at FROM feed_events f JOIN events e ON e.id=f.event_id
                             WHERE e.status='open' AND f.source='espn'""")
    groups = {}
    for eid, ext, path, starts in rows:
        if sport_kind(path) and starts:
            groups.setdefault((path, starts[:10]), []).append((eid, ext))
    found = 0
    for (path, day), items in groups.items():
        try:
            d = datetime.fromisoformat(day + "T00:00:00+00:00")
            evs = {str(e["id"]): e for e in feed.espn_fetch(path, d, d)}
            with db() as conn:
                for eid, ext in items:
                    e = evs.get(ext.rsplit(":", 1)[-1])
                    comp = (e or {}).get("competitions") or [None]
                    odds = ((comp[0] or {}).get("odds") or [None])[0] or {}
                    try:
                        total = float(odds.get("overUnder"))
                    except (TypeError, ValueError):
                        continue
                    if total > 0:
                        conn.execute("INSERT INTO market_lines (event_id, total, updated_at) VALUES (?,?,?) "
                                     "ON CONFLICT(event_id) DO UPDATE SET total=excluded.total, updated_at=excluded.updated_at",
                                     (eid, total, now_iso()))
                        found += 1
        except Exception as e:
            log.info("markets lines %s %s: %s", path, day, e)
    if found:
        invalidate()
    return found


@router.post("/admin/score")
def admin_score(request: Request, body: dict = Body(...)):
    """Ручной ввод итогового счёта (если ESPN не отдал) и мгновенный расчёт доп. рынков."""
    user = auth(request)
    if int(user["id"]) not in core.ctx.admin_ids:
        raise HTTPException(403, "Только для админа")
    try:
        eid, h, a = int(body["event_id"]), int(body["home"]), int(body["away"])
    except Exception:
        raise HTTPException(400, "Нужны event_id, home, away")
    if h < 0 or a < 0 or h > 300 or a > 300:
        raise HTTPException(400, "Неверный счёт")
    with db() as conn:
        ev = one(conn, "SELECT status FROM events WHERE id=?", (eid,))
        if not ev:
            raise HTTPException(404, "Матч не найден")
        if ev[0] == "open":
            raise HTTPException(400, "Сначала закройте приём ставок на матч")
        conn.execute("INSERT INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?) "
                     "ON CONFLICT(event_id) DO UPDATE SET home=excluded.home, away=excluded.away, source=excluded.source",
                     (eid, h, a, "admin", now_iso()))
        notes = settle_event_bets(conn, eid, h, a)
        core.notify_many(conn, notes)
    return {"ok": True, "settled": len(notes)}
