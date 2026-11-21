"""
extras/analytics.py — ИИ-аналитика матча: советует, на кого поставить (П1 / Ничья / П2).

Как работает
  1. Игрок нажимает «🤖 ИИ-анализ» на карточке матча → POST /api/v2/analytics/match {"event_id": N}.
  2. Собираем данные: коэффициенты → вероятности без маржи БК, а для матчей из ESPN ещё форму и
     турнирные показатели команд (рекорды дома/в гостях, серия результатов).
  3. Отправляем это ИИ (тот же AI_API_KEY / AI_MODEL, что и у поддержки) и просим строгий JSON:
     рекомендованный исход, уверенность, причины, риски.
  4. Если ИИ не настроен или упал — отдаём статистический разбор только по коэффициентам
     (source = "stats"), так что кнопка работает всегда.
  5. Ответ кэшируется в таблице match_analysis (пока не изменились кэфы, до 3 часов) — это экономит
     запросы к ИИ и делает повторное открытие мгновенным.

Баллы виртуальные, поэтому это развлекательная подсказка, а не финансовый совет — в ответе всегда есть дисклеймер.

Переменные окружения (необязательные):
  ANALYTICS_ENABLED     0 — выключить кнопку анализа
  ANALYTICS_TTL_MIN     сколько минут жить кэшу анализа (по умолчанию 180)
  ANALYTICS_RATE        сколько НОВЫХ анализов на игрока за 10 минут (по умолчанию 8; из кэша — без лимита)
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import betting, core, support
from .core import all_, auth, db, now_iso, one

log = logging.getLogger("mspbets.analytics")
router = APIRouter(prefix="/api/v2")

ENABLED = os.getenv("ANALYTICS_ENABLED", "1") not in ("0", "false", "False")
TTL_MIN = max(1, int(os.getenv("ANALYTICS_TTL_MIN", "180")))
RATE = max(1, int(os.getenv("ANALYTICS_RATE", "8")))
RATE_WINDOW = 600  # секунд

DISCLAIMER = "Это подсказка ИИ для развлечения, а не гарантия результата. Баллы виртуальные."

SYSTEM_PROMPT = """Ты — спортивный аналитик в развлекательном приложении «MSPBETS» (ставки ВИРТУАЛЬНЫМИ баллами PTS).
Тебе дают данные одного матча: коэффициенты, вероятности исходов без маржи БК и (если есть) форму и показатели команд.
Задача — посоветовать, на какой исход поставить, и коротко объяснить почему.

Правила:
• Опирайся в первую очередь на переданные данные. Общие знания о командах/игроках используй осторожно и только если уверен;
  НЕ выдумывай травмы, составы, счета прошлых матчей и статистику, которых нет в данных.
• Рыночные вероятности — сильный ориентир (рынок учитывает много информации). Отклоняйся от фаворита только с понятной причиной
  (форма, домашнее поле, мотивация) и снижай уверенность.
• Если матч равный или данных мало — честно скажи об этом, поставь низкую уверенность (35–55) и можешь выбрать "none"
  (рекомендую пропустить) либо осторожный исход.
• Уверенность (confidence) — целое число 0–100 и не может быть выше 85. Гарантий не даёшь.
• Пиши по-русски, дружелюбно, без Markdown. Обращайся на «вы».

Ответ — СТРОГО один JSON-объект без пояснений и без ```:
{"pick": "1" | "X" | "2" | "none",
 "confidence": число,
 "headline": "одна фраза-вывод до 90 символов",
 "reasons": ["причина 1", "причина 2", "причина 3"],   // 2–4 коротких пункта, каждый до 140 символов
 "risks": "главный риск одним предложением"}
"X" допустим только если у матча есть исход «Ничья»."""

_schema_ok = False
_schema_lock = threading.Lock()
_rate = {}
_rate_lock = threading.Lock()


# --------------------------------------------------------------------------
# Схема и кэш
# --------------------------------------------------------------------------
def init():
    global _schema_ok
    if _schema_ok:
        return
    with _schema_lock:
        if _schema_ok:
            return
        conn = core.ctx.get_db()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS match_analysis (
                    event_id INTEGER PRIMARY KEY, sig TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL)"""
            )
            conn.commit()
        finally:
            conn.close()
        _schema_ok = True


def _sig(ev):
    return f"{ev['odds_1']:.2f}|{(ev['odds_x'] or 0):.2f}|{ev['odds_2']:.2f}"


def _cache_get(event_id, sig):
    with db() as conn:
        r = one(conn, "SELECT sig, data, created_at FROM match_analysis WHERE event_id=?", (event_id,))
    if not r or r[0] != sig:
        return None
    try:
        created = datetime.fromisoformat(r[2])
        if datetime.now(timezone.utc) - created > timedelta(minutes=TTL_MIN):
            return None
        data = json.loads(r[1])
    except Exception:
        return None
    data["cached"] = True
    return data


def _cache_put(event_id, sig, data):
    try:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO match_analysis (event_id, sig, data, created_at) VALUES (?,?,?,?)",
                (event_id, sig, json.dumps(data, ensure_ascii=False), now_iso()),
            )
    except Exception as e:
        log.warning("analysis cache failed: %s", e)


def _check_rate(uid):
    now = time.monotonic()
    with _rate_lock:
        hits = [t for t in _rate.get(uid, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE:
            _rate[uid] = hits
            raise HTTPException(429, "Слишком много анализов подряд. Подождите пару минут.")
        hits.append(now)
        _rate[uid] = hits
        if len(_rate) > 5000:
            _rate.clear()


# --------------------------------------------------------------------------
# Данные о матче
# --------------------------------------------------------------------------
def _load_event(event_id):
    with db() as conn:
        r = one(
            conn,
            "SELECT e.id, e.sport, e.title, e.status, e.starts_at, e.odds_1, e.odds_x, e.odds_2, "
            "f.league, f.home, f.away, f.ext_id, f.sport_path "
            "FROM events e LEFT JOIN feed_events f ON f.event_id = e.id WHERE e.id=?",
            (event_id,),
        )
    if not r:
        raise HTTPException(404, "Матч не найден")
    ev = dict(zip(
        ("id", "sport", "title", "status", "starts_at", "odds_1", "odds_x", "odds_2", "league", "home", "away", "ext_id", "path"), r
    ))
    if not ev["home"] or not ev["away"]:
        parts = re.split(r"\s+[—–-]\s+", ev["title"] or "")
        ev["home"], ev["away"] = (parts[0], parts[1]) if len(parts) == 2 else (ev["title"], "")
    return ev


def probabilities(ev):
    """Вероятности исходов без маржи букмекера: нормируем обратные коэффициенты."""
    raw = {"1": 1 / ev["odds_1"], "2": 1 / ev["odds_2"]}
    if ev["odds_x"]:
        raw["X"] = 1 / ev["odds_x"]
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}, total - 1


def _labels(ev):
    return {"1": f"П1 — {ev['home']}", "X": "Ничья", "2": f"П2 — {ev['away']}" if ev["away"] else "П2"}


def _odds_of(ev, choice):
    return {"1": ev["odds_1"], "X": ev["odds_x"], "2": ev["odds_2"]}.get(choice)


def _team_info(c):
    """Компактные данные команды из scoreboard ESPN: рекорды и форма."""
    info = {"name": (c.get("team") or {}).get("displayName")}
    recs = {}
    for r in c.get("records") or []:
        name = (r.get("name") or r.get("type") or "").lower()
        if r.get("summary"):
            recs[name] = r["summary"]
    if recs:
        info["records"] = recs
    if c.get("form"):
        info["recent_form_old_to_new"] = c["form"]  # напр. WWDLW (W — победа, D — ничья, L — поражение)
    return info


def _espn_context(ev):
    """Доп. данные из ESPN (форма, рекорды, линии). Ошибки не критичны — просто без них."""
    ext = ev.get("ext_id") or ""
    if not ext.startswith("espn:") or not ev.get("starts_at"):
        return None
    try:
        from . import feed

        _, path, eid = ext.split(":", 2)
        day = datetime.fromisoformat(ev["starts_at"])
        for e in feed._espn_day(path, day):
            if str(e.get("id")) != eid:
                continue
            comp = (e.get("competitions") or [None])[0] or {}
            teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            out = {"home_team": _team_info(teams.get("home", {})), "away_team": _team_info(teams.get("away", {}))}
            o = (comp.get("odds") or [None])[0] or {}
            if o.get("details"):
                out["market_line"] = o["details"]
            if o.get("overUnder"):
                out["total_line"] = o["overUnder"]
            venue = (comp.get("venue") or {}).get("fullName")
            if venue:
                out["venue"] = venue
            if comp.get("neutralSite"):
                out["neutral_site"] = True
            return out
    except Exception as e:
        log.info("espn context failed: %s", e)
    return None


def _context(ev):
    probs, margin = probabilities(ev)
    ctx = {
        "sport": ev["sport"], "league": ev["league"], "home": ev["home"], "away": ev["away"],
        "starts_at_utc": ev["starts_at"],
        "odds": {k: round(_odds_of(ev, k), 2) for k in probs},
        "fair_probability_percent": {k: round(v * 100, 1) for k, v in probs.items()},
        "bookmaker_margin_percent": round(margin * 100, 1),
        "has_draw_outcome": "X" in probs,
        "outcome_meaning": {"1": f"победа {ev['home']}", "X": "ничья", "2": f"победа {ev['away']}"},
    }
    extra = _espn_context(ev)
    if extra:
        ctx["team_data"] = extra
    elif (ev.get("ext_id") or "").startswith("ps:"):
        ctx["note"] = "Коэффициенты киберспортивного матча посчитаны по рейтингу Эло из последних матчей команд."
    return ctx


# --------------------------------------------------------------------------
# Разбор без ИИ (запасной вариант) и обработка ответа ИИ
# --------------------------------------------------------------------------
def _stats_analysis(ev, ctx):
    probs, margin = probabilities(ev)
    labels = _labels(ev)
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    (best, p1), (second, p2) = ranked[0], ranked[1]
    gap = p1 - p2
    reasons = [
        f"Рынок оценивает шансы так: " + ", ".join(f"{labels[k].split(' — ')[0] if k != 'X' else 'Ничья'} {round(v * 100)}%" for k, v in ranked) + ".",
    ]
    reasons.append(
        f"{labels[best]} — фаворит с отрывом {round(gap * 100)} п.п. (кэф {_odds_of(ev, best):.2f})."
        if gap >= 0.08 else "Матч выглядит равным: явного фаворита нет."
    )
    td = ctx.get("team_data") or {}
    for side, name in (("home_team", ev["home"]), ("away_team", ev["away"])):
        t = td.get(side) or {}
        if t.get("recent_form_old_to_new"):
            reasons.append(f"Форма {name}: {t['recent_form_old_to_new']} (W — победа, D — ничья, L — поражение).")
    if td.get("market_line"):
        reasons.append(f"Линия рынка: {td['market_line']}.")
    pick = best if gap >= 0.04 else "none"
    conf = int(min(80, round(p1 * 100))) if pick != "none" else 40
    return {
        "pick": pick, "confidence": conf,
        "headline": (f"Статистика за {labels[best].split(' — ')[0]}" if pick != "none" and best != "X" else
                     "Ничья — самый вероятный исход по рынку" if pick == "X" else "Явного фаворита нет — можно пропустить"),
        "reasons": reasons[:4],
        "risks": "Разбор построен только на коэффициентах: ИИ сейчас недоступен, форма и новости не учтены."
                 if not td else "Спорт непредсказуем: даже явный фаворит проигрывает довольно часто.",
        "source": "stats",
    }


def _extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no json")
    return json.loads(m.group(0))


def _clip(v, n):
    return str(v or "").strip().replace("\n", " ")[:n]


def _ai_analysis(ev, ctx, probs):
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Данные матча (JSON):\n" + json.dumps(ctx, ensure_ascii=False)},
    ]
    text = support.ai_complete(msgs, temperature=0.4, max_tokens=900, reasoning="none")
    try:
        d = _extract_json(text)
    except Exception:
        raise support.AIError(f"bad analysis json: {text[:160]}")
    pick = str(d.get("pick", "none")).strip().upper().replace("П", "")
    if pick not in probs:
        pick = "none"
    try:
        conf = int(round(float(d.get("confidence", 50))))
    except Exception:
        conf = 50
    conf = max(5, min(85, conf))
    reasons = [_clip(r, 160) for r in (d.get("reasons") or []) if str(r).strip()][:4]
    if not reasons:
        raise support.AIError("analysis without reasons")
    return {
        "pick": pick, "confidence": conf, "headline": _clip(d.get("headline"), 120) or "Разбор матча",
        "reasons": reasons, "risks": _clip(d.get("risks"), 220), "source": "ai",
    }


def analyze_event(event_id, uid=None):
    """Главная функция: -> словарь анализа. Блокирующая (запускать в потоке — FastAPI делает это для sync-эндпоинтов)."""
    init()
    ev = _load_event(event_id)
    if ev["status"] != "open" or betting._started(ev["starts_at"]):
        raise HTTPException(400, "Матч уже начался или закрыт — анализ недоступен")
    sig = _sig(ev)
    hit = _cache_get(event_id, sig)
    if hit:
        return hit
    if uid is not None:
        _check_rate(uid)
    ctx = _context(ev)
    probs, margin = probabilities(ev)
    result = None
    if support.AI_ENABLED:
        try:
            result = _ai_analysis(ev, ctx, probs)
        except support.AIError as e:
            log.warning("analytics AI failed: %s", e)
    if result is None:
        result = _stats_analysis(ev, ctx)
    labels = _labels(ev)
    pick = result["pick"]
    out = {
        "event_id": ev["id"], "title": ev["title"], "sport": ev["sport"], "league": ev["league"],
        "pick": pick, "pick_label": labels.get(pick) if pick != "none" else None,
        "pick_odds": round(_odds_of(ev, pick), 2) if pick != "none" else None,
        "confidence": result["confidence"], "headline": result["headline"], "reasons": result["reasons"],
        "risks": result["risks"], "source": result["source"],
        "probs": {k: round(v * 100) for k, v in probs.items()},
        "odds": {k: round(_odds_of(ev, k), 2) for k in probs},
        "labels": labels, "margin": round(margin * 100, 1),
        "disclaimer": DISCLAIMER, "generated_at": now_iso(), "cached": False,
    }
    # В кэш кладём только ответы ИИ: «запасной» разбор не должен надолго подменять настоящий.
    if out["source"] == "ai":
        _cache_put(event_id, sig, out)
    return out


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
@router.post("/analytics/match")
def analytics_match(request: Request, body: dict = Body(...)):
    user = auth(request)
    if not ENABLED:
        raise HTTPException(503, "ИИ-аналитика временно отключена")
    try:
        event_id = int(body.get("event_id"))
    except Exception:
        raise HTTPException(400, "Неверный матч")
    return analyze_event(event_id, int(user["id"]))
