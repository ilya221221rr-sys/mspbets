"""
extras/esports_odds.py — «букмекерские» коэффициенты для киберспорта (Dota 2 / CS2).

Бесплатный PandaScore не отдаёт линии БК, поэтому кэфы считаются моделью, которая ведёт себя
как линия BetBoom / Winline:

  1. Рейтинг Эло на уровне КАРТ (а не серий): учитывается счёт серии (2:0 сильнее, чем 2:1),
     динамический K-фактор (новые команды быстро «находят» уровень), вес турнира (tier S/A важнее)
     и затухание по давности матча.
  2. Рейтинг сжимается к среднему, если у команды мало сыгранных карт (нет «ложных» фаворитов).
  3. Вероятность карты → вероятность серии с учётом формата bo1 / bo3 / bo5 (фаворит в bo3 имеет
     более низкий кэф, чем в bo1). Для bo2 появляется исход «Ничья» (X), как у настоящих БК.
  4. Маржа БК (ESPORTS_MARGIN, по умолчанию 7%) накладывается степенным методом: аутсайдер
     «теряет» больше, фаворит меньше (favourite-longshot bias) — так делают реальные конторы.
  5. Границы линии: минимум 1.04, максимум 12.0.

Модуль чистый (без сети и БД): tests/esports_selftest.py.
"""
import math
import os
from datetime import datetime, timezone

BASE = 1500.0
MARGIN = float(os.getenv("ESPORTS_MARGIN", "0.07"))
MIN_ODDS = float(os.getenv("ESPORTS_MIN_ODDS", "1.04"))
MAX_ODDS = float(os.getenv("ESPORTS_MAX_ODDS", "12.0"))
SHRINK_MAPS = float(os.getenv("ESPORTS_SHRINK_MAPS", "8"))      # доверие к рейтингу: n / (n + SHRINK_MAPS)
HALF_LIFE_DAYS = float(os.getenv("ESPORTS_HALF_LIFE_DAYS", "75"))  # через столько дней вес матча падает вдвое
SPREAD = float(os.getenv("ESPORTS_SPREAD", "1.0"))               # >1 — кэфы «жёстче», фавориты сильнее

TIER_WEIGHT = {"s": 1.25, "a": 1.1, "b": 1.0, "c": 0.85, "d": 0.7}


# ------------------------------- вероятности -------------------------------
def map_prob(ra, rb):
    """Вероятность выигрыша карты командой A по Эло."""
    return 1.0 / (1.0 + 10 ** (-(ra - rb) * SPREAD / 400.0))


def series_prob(p, best_of):
    """Вероятность выиграть серию bo{best_of}, если на каждой карте шанс p."""
    p = min(0.995, max(0.005, p))
    n = max(1, int(best_of or 1))
    if n % 2 == 0:
        n -= 1
    need = n // 2 + 1
    q = 1 - p
    return sum(math.comb(need - 1 + lost, lost) * p ** need * q ** lost for lost in range(need))


def three_way(p):
    """bo2: (победа A 2:0, ничья 1:1, победа B 0:2)."""
    p = min(0.995, max(0.005, p))
    return p * p, 2 * p * (1 - p), (1 - p) ** 2


# ------------------------------- маржа -------------------------------
def apply_margin(probs, margin=None):
    """
    Справедливые вероятности → кэфы с маржой БК (степенной метод):
    ищем k<1, при котором сумма p_i^k = 1 + маржа; кэф = 1 / p_i^k.
    """
    m = MARGIN if margin is None else margin
    s = sum(probs)
    probs = [min(0.999, max(0.001, p / s)) for p in probs]
    lo, hi = 0.2, 1.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(p ** k for p in probs) > 1 + m:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    return [round(min(MAX_ODDS, max(MIN_ODDS, 1.0 / (p ** k))), 2) for p in probs]


# ------------------------------- рейтинги -------------------------------
def _parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _scores(m, ids):
    """Счёт серии {team_id: карт выиграно}. Нет results — берём winner_id как 1:0."""
    res = {r.get("team_id"): r.get("score") for r in (m.get("results") or []) if r.get("team_id") in ids}
    if len(res) == 2 and all(isinstance(v, int) for v in res.values()) and sum(res.values()) > 0:
        return res
    w = m.get("winner_id")
    if w in ids:
        return {i: (1 if i == w else 0) for i in ids}
    return None


def build_ratings(past, now=None):
    """past — завершённые матчи PandaScore. Возвращает ({team_id: рейтинг}, {team_id: сыграно карт})."""
    now = now or datetime.now(timezone.utc)
    rating, games, rows = {}, {}, []
    for m in past:
        if m.get("forfeit"):
            continue
        ids = [o["opponent"]["id"] for o in (m.get("opponents") or []) if o.get("opponent")]
        if len(ids) != 2 or ids[0] == ids[1]:
            continue
        sc = _scores(m, ids)
        if not sc:
            continue
        t = _parse_dt(m.get("end_at") or m.get("begin_at") or m.get("scheduled_at")) or now
        rows.append((t, m, ids, sc))
    rows.sort(key=lambda x: x[0])
    for t, m, ids, sc in rows:
        a, b = ids
        age = max(0.0, (now - t).total_seconds() / 86400.0)
        tier = str(((m.get("tournament") or {}).get("tier") or "")).lower()
        w = TIER_WEIGHT.get(tier, 0.9) * (0.35 + 0.65 * 0.5 ** (age / HALF_LIFE_DAYS))
        ia, ib = sc[a], sc[b]
        seq = []  # чередуем карты, чтобы порядок не влиял на результат
        while ia or ib:
            if ia:
                seq.append(a); ia -= 1
            if ib:
                seq.append(b); ib -= 1
        for winner in seq:
            ra, rb = rating.get(a, BASE), rating.get(b, BASE)
            ga, gb = games.get(a, 0), games.get(b, 0)
            ea = map_prob(ra, rb)
            sa = 1.0 if winner == a else 0.0
            ka = (14 + 46 / (1 + ga / 10.0)) * w  # новичок K≈60, ветеран K≈14
            kb = (14 + 46 / (1 + gb / 10.0)) * w
            rating[a] = ra + ka * (sa - ea)
            rating[b] = rb + kb * ((1 - sa) - (1 - ea))
            games[a], games[b] = ga + 1, gb + 1
    return rating, games


def effective(rating, games, team_id):
    """Рейтинг с поправкой на малую выборку."""
    n = games.get(team_id, 0)
    return BASE + (rating.get(team_id, BASE) - BASE) * (n / (n + SHRINK_MAPS))


# ------------------------------- кэфы матча -------------------------------
def match_odds(rating, games, id_a, id_b, best_of=3, margin=None):
    """(odds_1, odds_x | None, odds_2). best_of=2 → рынок с ничьёй."""
    pm = map_prob(effective(rating, games, id_a), effective(rating, games, id_b))
    if int(best_of or 3) == 2:
        o1, ox, o2 = apply_margin(list(three_way(pm)), margin)
        return o1, ox, o2
    ps = series_prob(pm, best_of or 3)
    o1, o2 = apply_margin([ps, 1 - ps], margin)
    return o1, None, o2


def best_of_of(m):
    """Формат серии из матча PandaScore (по умолчанию bo3)."""
    n = m.get("number_of_games")
    return n if isinstance(n, int) and 1 <= n <= 7 else 3
