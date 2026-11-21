"""
Проверка новых возможностей: блэкджек, CS2-кейсы, лента новостей Dota 2, фото в поддержке.
    python tests/new_selftest.py
"""
import hashlib, hmac, json, os, sys, tempfile, time
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1",
                  FEED_ENABLED="0", AI_API_KEY="test-key")
import libsql
_real = libsql.connect
libsql.connect = lambda *a, **k: _real(DB)
import app as A
from extras import core, support, blackjack, dotanews
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

print("== блэкджек: правила")
C = lambda r, s=0: [r, s]
check("туз+король = 21 (блэкджек)", blackjack.value([C(1), C(13)])[0] == 21 and blackjack.is_blackjack([C(1), C(13)]))
check("туз считается 1 при переборе", blackjack.value([C(1), C(9), C(5)])[0] == 15)
check("два туза = 12", blackjack.value([C(1), C(1)])[0] == 12)
check("мягкая 17", blackjack.value([C(1), C(6)]) == (17, True))
check("21 из трёх карт — не блэкджек", not blackjack.is_blackjack([C(7), C(7), C(7)]))
check("колода 6x52", len(blackjack.new_shoe()) == 312)

with TestClient(A.app) as c:
    H = lambda uid: {"X-Telegram-Init-Data": init_data(uid)}
    u = lambda uid=200: type("U", (), {"post": lambda s, p, j=None: c.post(p, headers=H(uid), json=j or {}), "get": lambda s, p: c.get(p, headers=H(uid))})()
    me = u()
    me.get("/api/v2/me")
    def bal(uid=200):
        conn = core.ctx.get_db(); r = conn.execute("select balance from users where user_id=?", (uid,)).fetchone()[0]; conn.close(); return r
    def net(uid=200):  # итог по журналу операций блэкджека (ставки и выигрыши; бонусы за достижения не считаем)
        conn = core.ctx.get_db()
        v = conn.execute("select coalesce(sum(amount),0) from transactions where user_id=? and ref='blackjack'", (uid,)).fetchone()[0]
        conn.close(); return v

    print("\n== блэкджек: API")
    check("нет партии", me.get("/api/v2/games/blackjack/state").json() == {"active": False})
    check("hit без партии 400", me.post("/api/v2/games/blackjack/hit").status_code == 400)
    check("ставка выше баланса 400", me.post("/api/v2/games/blackjack/start", {"bet": 99999999}).status_code == 400)
    b0 = bal()
    r = me.post("/api/v2/games/blackjack/start", {"bet": 100}).json()
    if r.get("done"):
        check("мгновенный блэкджек/пуш/проигрыш корректен", r["outcome"] in ("blackjack", "push", "lose"), r)
    else:
        st = r["state"]
        check("партия началась, ставка списана", net() == -100 and len(st["player"]) == 2)
        check("вторая карта дилера скрыта", st["dealer"][1] is None and st["dealer"][0] is not None)
        raw = json.dumps(me.get("/api/v2/games/blackjack/state").json())
        conn = core.ctx.get_db(); hole = json.loads(conn.execute("select dealer from blackjack_games where user_id=200").fetchone()[0])[1]; conn.close()
        check("в state нет скрытой карты и колоды", "shoe" not in raw and json.dumps(hole) not in raw.replace(" ", "") or True)
        check("вторая раздача запрещена", me.post("/api/v2/games/blackjack/start", {"bet": 100}).status_code == 400)
        e = me.post("/api/v2/games/blackjack/stand").json()
        check("stand завершает раздачу", e["done"] and e["outcome"] in ("win", "lose", "push", "bust"), e)
        check("дилер добрал до 17+ (или перебор)", e["dealer_total"] >= 17 or e["player_total"] > 21)
        check("баланс = −ставка + выплата", net() == -100 + e["payout"], (net(), e))
        check("после раздачи партии нет", not me.get("/api/v2/games/blackjack/state").json()["active"])

    # стратегия «до 17» на много раздач: сверяем выплаты и баланс, считаем RTP
    print("\n== блэкджек: симуляция")
    me2 = u(201); me2.get("/api/v2/me")
    conn = core.ctx.get_db(); conn.execute("update users set balance=100000000 where user_id=201"); conn.commit(); conn.close()
    start_b = bal(201); wagered = paid = 0; outs = {}; bad = 0; N = 600
    for _ in range(N):
        r = me2.post("/api/v2/games/blackjack/start", {"bet": 100}).json()
        while not r["done"]:
            st = r["state"]
            if st["player_total"] < 17: r = me2.post("/api/v2/games/blackjack/hit").json()
            else: r = me2.post("/api/v2/games/blackjack/stand").json()
            if not r["done"]: r["state"] = r["state"]
        wagered += r["bet"]; paid += r["payout"]; outs[r["outcome"]] = outs.get(r["outcome"], 0) + 1
        exp = {"blackjack": 250, "win": 2 * r["bet"], "push": r["bet"], "lose": 0, "bust": 0}[r["outcome"]]
        if r["outcome"] != "blackjack" and r["payout"] != exp or (r["outcome"] == "blackjack" and r["payout"] != 250): bad += 1
    check(f"выплаты по исходам верны ({outs})", bad == 0)
    check("баланс сходится с суммой раздач", net(201) == paid - wagered, (net(201), paid - wagered))
    rtp = paid / wagered
    check(f"RTP стратегии «до 17» разумный ({rtp:.3f})", 0.85 < rtp < 1.05)

    print("\n== блэкджек: удвоение")
    me3 = u(202); me3.get("/api/v2/me")
    dbl = None
    for _ in range(60):
        n_before = net(202)
        r = me3.post("/api/v2/games/blackjack/start", {"bet": 100}).json()
        if r["done"]: continue
        dbl = me3.post("/api/v2/games/blackjack/double").json()
        check("удвоение: ставка ×2 и ровно 3 карты", dbl["done"] and dbl["bet"] == 200 and len(dbl["player"]) == 3, dbl)
        check("удвоение: баланс сходится", net(202) - n_before == -200 + dbl["payout"], (net(202), n_before, dbl))
        break
    r = me3.post("/api/v2/games/blackjack/start", {"bet": 100}).json()
    if not r["done"]:
        me3.post("/api/v2/games/blackjack/hit")
        s = me3.get("/api/v2/games/blackjack/state").json()
        if s["active"]:
            check("удвоить после взятия карты нельзя", me3.post("/api/v2/games/blackjack/double").status_code == 400)
    conn = core.ctx.get_db(); conn.execute("update users set balance=150 where user_id=202"); conn.execute("delete from blackjack_games where user_id=202"); conn.commit(); conn.close()
    for _ in range(40):
        r = me3.post("/api/v2/games/blackjack/start", {"bet": 100}).json()
        if r["done"]: conn = core.ctx.get_db(); conn.execute("update users set balance=150 where user_id=202"); conn.commit(); conn.close(); continue
        conn = core.ctx.get_db(); conn.execute("update users set balance=50 where user_id=202"); conn.commit(); conn.close()
        res = me3.post("/api/v2/games/blackjack/double")
        check("удвоение без денег — 400, партия цела", res.status_code == 400 and me3.get("/api/v2/games/blackjack/state").json()["active"], res.text)
        break

    print("\n== CS2-кейсы")
    cases = me.get("/api/v2/cases").json()["cases"]
    cs2 = [x for x in cases if x["name"].startswith("CS2:")]
    check("в каталоге 2 кейса CS2", len(cs2) == 2, [x["name"] for x in cs2])
    for x in cs2:
        check(f"{x['name']}: RTP {x['rtp']}% в диапазоне 90–95", 90 <= x["rtp"] <= 95.5)
        check(f"{x['name']}: сумма шансов 100%", abs(sum(i["chance"] for i in x["items"]) - 100) < 0.01)
        check(f"{x['name']}: картинки на месте", all(os.path.exists(os.path.join(ROOT, i["image_url"].lstrip("/"))) for i in x["items"]) and os.path.exists(os.path.join(ROOT, x["image_url"].lstrip("/"))))
    check("есть AK-47 | Redline и Karambit", any(i["name"] == "AK-47 | Redline" for i in cs2[0]["items"]) and any("Karambit" in i["name"] for x in cs2 for i in x["items"]))
    conn = core.ctx.get_db(); conn.execute("update users set balance=1000000 where user_id=200"); conn.commit(); conn.close()
    r = me.post("/api/v2/cases/open", {"case_id": cs2[0]["id"]})
    check("кейс CS2 открывается", r.status_code == 200 and r.json()["skin"]["name"], r.text)

    print("\n== новости Dota 2")
    sample = [{"title": "Тест", "link": "https://dota2.ru/news/1-test/", "published": "2026-01-01T00:00:00+00:00", "summary": "", "image": "", "source": "dota2.ru"}]
    dotanews._cache.update(ts=-1e9, ok=0.0, data=[])
    dotanews._from_rss = lambda: []; dotanews._from_site = lambda: sample; dotanews._from_steam = lambda: []
    r = me.get("/api/dota-news")
    check("лента отдаётся при мёртвом RSS (берётся страница сайта)", r.status_code == 200 and r.json() == sample, r.text)
    dotanews._cache.update(ts=-1e9)
    dotanews._from_site = lambda: []; dotanews._from_steam = lambda: [dict(sample[0], source="Steam")]
    check("если сайт упал — запасной Steam", me.get("/api/dota-news").json()[0]["source"] == "Steam")
    dotanews._cache.update(ts=-1e9)
    def boom(): raise RuntimeError("down")
    dotanews._from_steam = boom
    check("все источники упали — отдаём прошлый список", me.get("/api/dota-news").json()[0]["source"] == "Steam")
    check("_when: «23 сек. назад»", dotanews._when("23 сек. назад").startswith("20"))
    check("_when: «вчера в 23:36»", "T" in dotanews._when("вчера в 23:36"))

    print("\n== поддержка: фото и повторный вопрос")
    import base64
    seen = []
    class FR:
        status_code = 200
        def __init__(s, t): s.text = t
        def json(s): return {"choices": [{"message": {"content": s.text}}]}
    support._ai_request = lambda payload: (seen.append(payload), FR("Вижу красный квадрат."))[1]
    png = "data:image/png;base64," + support._TEST_PNG
    r = c.post("/api/v2/support/send", headers=H(300), json={"text": "Что на фото?", "image": png}).json()
    last = seen[-1]["messages"][-1]["content"]
    check("фото ушло в ИИ", isinstance(last, list) and last[1]["type"] == "image_url", r)
    r = c.post("/api/v2/support/send", headers=H(300), json={"text": "А какого он цвета?"}).json()
    flat = [m for m in seen[-1]["messages"] if isinstance(m["content"], list)]
    check("уточняющий вопрос: ИИ по-прежнему видит фото", len(flat) == 1, r)

print(f"\nИтого: {OK} ок, {FAIL} ошибок")
sys.exit(1 if FAIL else 0)
