"""
Проверка ИИ-аналитики матча и режима «Голда» (локальная БД, ИИ и Telegram — заглушки):
    python tests/ai_selftest.py
"""
import hashlib, hmac, json, os, sys, tempfile, time
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1",
                  FEED_ENABLED="0", AI_API_KEY="test-key", ANALYTICS_RATE="4")
import libsql
_real = libsql.connect
libsql.connect = lambda *a, **k: _real(DB)
import app as A
from extras import core, support, analytics
from fastapi.testclient import TestClient

core.ctx.bot = None
OK = FAIL = 0
def check(name, cond, extra=""):
    global OK, FAIL
    if cond: OK += 1; print("  ✓", name)
    else: FAIL += 1; print("  ✗ FAIL:", name, extra)

def init_data(uid, name="U"):
    d = {"user": json.dumps({"id": uid, "first_name": name, "username": f"u{uid}"}), "auth_date": str(int(time.time()))}
    chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    sec = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(sec, chk.encode(), hashlib.sha256).hexdigest()
    return urlencode(d)

class FakeResp:
    status_code = 200
    def __init__(s, content): s._c = content; s.text = content
    def json(s): return {"choices": [{"message": {"content": s._c}}]}

calls = []
fake = {"mode": "ok", "analysis": None}
def fake_request(payload):
    calls.append(payload)
    sysmsg = payload["messages"][0]["content"]
    if fake["mode"] == "down":
        r = FakeResp("err"); r.status_code = 500; return r
    if sysmsg.startswith("Ты — спортивный аналитик"):
        return FakeResp(fake["analysis"] or json.dumps({"pick": "1", "confidence": 71, "headline": "Хозяева сильнее",
            "reasons": ["Форма лучше", "Дома играют увереннее"], "risks": "Гости опасны в контратаках"}))
    if sysmsg.startswith("Ты — универсальный"):
        return FakeResp("Интеграл — это площадь под графиком функции. [[HUMAN]]")
    return FakeResp("Ставку можно сделать на вкладке матчей.")
support._ai_request = fake_request

def new_event(title="Арсенал — Челси", odds=(1.8, 3.6, 4.2), start_h=5):
    conn = core.ctx.get_db()
    st = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + start_h * 3600))
    cur = conn.execute("INSERT INTO events (sport,title,starts_at,status,odds_1,odds_x,odds_2,created_at) VALUES (?,?,?,?,?,?,?,?)",
                       ("Футбол", title, st, "open", odds[0], odds[1], odds[2], core.now_iso()))
    conn.commit(); eid = cur.lastrowid; conn.close(); return eid

with TestClient(A.app) as c:
    H = lambda uid: {"X-Telegram-Init-Data": init_data(uid)}
    send = lambda uid, text: c.post("/api/v2/support/send", headers=H(uid), json={"text": text}).json()
    print("\n== обычная поддержка (без изменений)")
    r = send(10, "Как сделать ставку?")
    check("отвечает ИИ обычным промптом", r["reply"] and not r["escalated"] and calls[-1]["messages"][0]["content"].startswith("Ты — ИИ-помощник поддержки"), r)
    check("обычный режим не даёт 'голда'-промпт", "универсальный" not in calls[-1]["messages"][0]["content"])

    print("\n== режим «Голда»")
    n = len(calls)
    r = send(10, "Голда")
    check("кодовое слово включает режим без запроса к ИИ", r["reply"] and "Голда" in r["reply"] and len(calls) == n, r)
    r = send(10, "Объясни, что такое интеграл. Позови human не надо")
    check("в режиме Голда отвечает на любой вопрос", "Интеграл" in r["reply"], r)
    check("использован промпт Голда", calls[-1]["messages"][0]["content"].startswith("Ты — универсальный"))
    check("человек не вызывается (маркер и слова игнорируются)", not r["escalated"] and "[[HUMAN]]" not in r["reply"] and r["mode"] == "ai", r)
    check("режим держится без слова", "Интеграл" in send(10, "а ещё про производную?")["reply"])
    check("max_tokens и температура для Голды", calls[-1]["max_tokens"] >= 2500 and calls[-1]["temperature"] > 0.5)
    r = send(10, "голда стоп")
    check("«голда стоп» выключает режим", "выключен" in r["reply"], r)
    r = send(10, "Как получить бонус?")
    check("после выключения снова поддержка", calls[-1]["messages"][0]["content"].startswith("Ты — ИИ-помощник поддержки"), r)
    r = send(11, "ГОЛДА, напиши стихотворение про осень")
    check("слово в первом сообщении + регистр", r["reply"] and calls[-1]["messages"][0]["content"].startswith("Ты — универсальный"), r)
    r = send(12, "Моя голдан копилка")
    check("похожее слово (голдан) режим не включает", calls[-1]["messages"][0]["content"].startswith("Ты — ИИ-помощник поддержки"), r)
    # игрок был передан человеку, потом сказал Голда
    send(13, "позовите оператора")
    r = send(13, "Голда, сколько будет 12*12?")
    check("Голда возвращает ИИ даже после вызова человека", r["reply"] and r["mode"] == "ai", r)
    fake["mode"] = "down"
    r = send(11, "и ещё вопрос")
    check("при сбое ИИ в Голде — передача человеку (как раньше)", r["escalated"] and not r["reply"], r)
    fake["mode"] = "ok"

    print("\n== ИИ-аналитика матча")
    eid = new_event()
    check("без авторизации 401", c.post("/api/v2/analytics/match", json={"event_id": eid}).status_code == 401)
    check("неверный event_id 400", c.post("/api/v2/analytics/match", headers=H(20), json={"event_id": "x"}).status_code == 400)
    check("нет матча 404", c.post("/api/v2/analytics/match", headers=H(20), json={"event_id": 99999}).status_code == 404)
    n = len(calls)
    r = c.post("/api/v2/analytics/match", headers=H(20), json={"event_id": eid}); d = r.json()
    check("анализ возвращает совет", r.status_code == 200 and d["pick"] == "1" and d["source"] == "ai" and d["confidence"] == 71, d)
    check("в ответе кэф, подпись, вероятности", d["pick_odds"] == 1.8 and "Арсенал" in d["pick_label"] and sum(d["probs"].values()) in (99, 100, 101), d)
    check("вероятности без маржи ~ сумма 100%, маржа > 0", d["margin"] > 0)
    check("данные матча ушли в ИИ", "Арсенал" in calls[-1]["messages"][1]["content"] and "fair_probability_percent" in calls[-1]["messages"][1]["content"])
    r2 = c.post("/api/v2/analytics/match", headers=H(21), json={"event_id": eid}).json()
    check("повторный запрос из кэша (без вызова ИИ)", r2["cached"] and len(calls) == n + 1, r2.get("cached"))
    conn = core.ctx.get_db(); conn.execute("UPDATE events SET odds_1=2.5 WHERE id=?", (eid,)); conn.commit(); conn.close()
    r3 = c.post("/api/v2/analytics/match", headers=H(21), json={"event_id": eid}).json()
    check("при смене кэфов анализ пересчитывается", not r3["cached"] and len(calls) == n + 2)
    fake["analysis"] = "```json\n" + json.dumps({"pick": "X", "confidence": 190, "headline": "h", "reasons": ["a"], "risks": ""}) + "\n```"
    eid2 = new_event("Динамо — Спартак", (2.1, 3.2, 3.4))
    d = c.post("/api/v2/analytics/match", headers=H(22), json={"event_id": eid2}).json()
    check("JSON в ```-блоке разобран, уверенность ограничена 85", d["pick"] == "X" and d["confidence"] == 85 and d["pick_label"] == "Ничья", d)
    fake["analysis"] = json.dumps({"pick": "X", "confidence": 60, "headline": "h", "reasons": ["a"], "risks": ""})
    eid3 = new_event("Лейкерс — Бакс", (1.9, None, 1.95))
    d = c.post("/api/v2/analytics/match", headers=H(22), json={"event_id": eid3}).json()
    check("нет ничьей — недопустимый исход 'X' превращается в 'пропустить'", d["pick"] == "none" and d["pick_odds"] is None, d)
    fake["analysis"] = "это вообще не json"
    eid4 = new_event("Реал — Барса", (2.4, 3.3, 2.9))
    d = c.post("/api/v2/analytics/match", headers=H(23), json={"event_id": eid4}).json()
    check("битый ответ ИИ → запасной расчёт по кэфам", d["source"] == "stats" and d["reasons"] and d["confidence"] > 0, d)
    fake["analysis"] = None; fake["mode"] = "down"
    eid5 = new_event("Милан — Интер", (2.0, 3.3, 3.6))
    d = c.post("/api/v2/analytics/match", headers=H(24), json={"event_id": eid5}).json()
    check("ИИ недоступен → статистический совет, кнопка работает", d["source"] == "stats" and d["pick"] in ("1", "X", "2", "none"), d)
    check("статистика выбирает фаворита", d["pick"] == "1", d)
    fake["mode"] = "ok"
    eid6 = new_event("Старый — Матч", start_h=-1)
    check("начавшийся матч — 400", c.post("/api/v2/analytics/match", headers=H(25), json={"event_id": eid6}).status_code == 400)
    codes = [c.post("/api/v2/analytics/match", headers=H(30), json={"event_id": new_event(f"Т{i} — У{i}")}).status_code for i in range(6)]
    check("лимит новых анализов на игрока (429)", codes[:4] == [200] * 4 and codes[-1] == 429, codes)
    check("кэшированный анализ лимитом не блокируется", c.post("/api/v2/analytics/match", headers=H(30), json={"event_id": eid}).status_code == 200)
    print("\n== фронтенд")
    html = c.get("/").text if c.get("/").status_code == 200 else ""
    check("analytics.js подключён и отдаётся", "/web/analytics.js" in html and c.get("/web/analytics.js").status_code == 200)

print(f"\nИтого: {OK} ок, {FAIL} ошибок"); sys.exit(1 if FAIL else 0)
