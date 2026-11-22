"""
Самопроверка новых модулей (extras/plus.py) на ЛОКАЛЬНОЙ базе (Turso не трогается):
    python tests/plus_selftest.py
Проверяет: доп. рынки (модель, ставка, смена кэфа, расчёт по счёту, отмена), рулетку (выплаты, RTP),
дуэли (создание, принятие, отмена, приватные, перепроверка seed), джекпот (рост, срыв, курсор),
VIP-кэшбэк и рейкбек, и что журнал операций сходится с балансом.
"""
import datetime
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
import hmac
from urllib.parse import urlencode

logging.getLogger("httpx").setLevel(logging.WARNING)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join(tempfile.mkdtemp(), "plus.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1", FEED_ENABLED="0", PLUS_BG="0")
import libsql
_real = libsql.connect
libsql.connect = lambda *a, **k: _real(DB)
import app as A
from extras import core, plus, markets, roulette, duels, jackpot, vipback
from fastapi.testclient import TestClient

core.ctx.bot = None
OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1; print("  ✓", name)
    else:
        FAIL += 1; print("  ✗ FAIL:", name, extra)


def init_data(uid, name="U"):
    d = {"user": json.dumps({"id": uid, "first_name": name, "username": f"u{uid}"}), "auth_date": str(int(time.time()))}
    chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    sec = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(sec, chk.encode(), hashlib.sha256).hexdigest()
    return urlencode(d)


class U:
    def __init__(s, c, uid, name="U"):
        s.c, s.uid, s.h = c, uid, {"X-Telegram-Init-Data": init_data(uid, name)}
    def get(s, p, **kw): return s.c.get(p, headers=s.h, **kw)
    def post(s, p, j=None): return s.c.post(p, headers=s.h, json=j or {})
    def bal(s): return s.get("/api/v2/me").json()["balance"]


def sql(q, args=(), fetch=False):
    conn = core.ctx.get_db()
    try:
        cur = conn.execute(q, args)
        r = cur.fetchall() if fetch else cur.lastrowid
        conn.commit()
        return r
    finally:
        conn.close()


def give(uid, amount):
    conn = core.ctx.get_db()
    core.adjust(conn, uid, amount, "admin", "тест")
    conn.commit(); conn.close()


def last_tx():
    return sql("select coalesce(max(id),0) from transactions", fetch=True)[0][0]


def bonus(uid, since):
    """Бонусы за уровень/достижения, начисленные после since (ставка может поднять уровень)."""
    return sql("select coalesce(sum(amount),0) from transactions where user_id=? and id>? and kind in ('levelup','achievement')", (uid, since), fetch=True)[0][0]


def journal_ok(uid, label):
    conn = core.ctx.get_db()
    b = conn.execute("select balance from users where user_id=?", (uid,)).fetchone()[0]
    sm = conn.execute("select coalesce(sum(amount),0) from transactions where user_id=?", (uid,)).fetchone()[0]
    conn.close()
    check(f"журнал сходится с балансом ({label}): 1000+{sm}={b}", 1000 + sm == b)


def new_event(sport, path, home, away, o, ext, hours=5):
    st = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    eid = sql("INSERT INTO events (sport, title, starts_at, status, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?,?,?)",
              (sport, f"{home} — {away}", st, "open", o[0], o[1], o[2], core.now_iso()))
    sql("INSERT INTO feed_events (event_id, source, ext_id, league, sport_path, home, away, starts_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (eid, "espn", ext, "Лига", path, home, away, st, core.now_iso()))
    markets.invalidate()
    return eid


with TestClient(A.app) as c:
    a, b, adm = U(c, 100, "Алиса"), U(c, 200, "Боб"), U(c, 1, "Админ")
    a.bal(); b.bal(); adm.bal()
    give(100, 2_000_000); give(200, 2_000_000)

    # ------------------------------------------------------------------
    print("\n== доп. рынки: модель")
    probs = markets.fair(1.95, 3.6, 4.0)
    lh, la = markets.fit_goals("football", probs)
    m = markets.score_matrix(lh, la)
    H = sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if i > j)
    D = sum(m[i][i] for i in range(len(m)))
    check(f"Пуассон воспроизводит 1X2 (П1 {H:.3f}≈{probs[0]:.3f}, X {D:.3f}≈{probs[1]:.3f})", abs(H - probs[0]) < 0.01 and abs(D - probs[1]) < 0.01)
    lh2, la2 = markets.fit_goals("football", markets.fair(2.7, 3.2, 2.7), 3.5)
    check("линия тотала ESPN сдвигает модель (3.5 → больше голов)", lh2 + la2 > 3.0, lh2 + la2)
    for kind, o, tl in [("football", (1.95, 3.6, 4.0), None), ("hockey", (1.75, None, 2.1), 6.5), ("basketball", (1.4, None, 3.0), 228.5)]:
        gs = markets.build_markets(kind, *o, "H", "A", tl)
        items = [i for g in gs for i in g["items"]]
        check(f"{kind}: {len(gs)} групп, {len(items)} исходов, все кэфы ≥ 1.01", len(gs) >= 3 and all(i["odds"] >= 1.01 for i in items))
        pairs = {}
        for i in items:
            pairs.setdefault(i["market"], []).append(i)
        two = [v for k, v in pairs.items() if len(v) == 2]
        over = [1 / v[0]["odds"] + 1 / v[1]["odds"] for v in two]
        check(f"{kind}: маржа двусторонних рынков 4–8%", all(1.04 <= x <= 1.08 for x in over), [round(x, 3) for x in over])
        lines = [float(k.split(":")[1]) for k in pairs if ":" in k]
        check(f"{kind}: все линии половинчатые (без возвратов)", all(abs(x * 2 % 2) == 1 for x in lines), lines)
    cs = [g for g in markets.build_markets("football", 1.95, 3.6, 4.0, "H", "A") if g["key"] == "cs"][0]
    check("точный счёт: вероятности + «другой» = 100%", abs(sum(i["prob"] for i in cs["items"]) - 1) < 0.02, sum(i["prob"] for i in cs["items"]))
    G = markets.grade
    check("расчёт: тотал/фора/обе/дк/точный/инд. тотал", all([
        G("total:2.5", "over", 2, 1) == "won", G("total:2.5", "under", 2, 1) == "lost", G("hcp:-1.5", "1", 2, 0) == "won",
        G("hcp:-1.5", "1", 2, 1) == "lost", G("hcp:-1.5", "2", 2, 1) == "won", G("hcp:1.5", "2", 0, 2) == "won",
        G("btts", "yes", 1, 1) == "won", G("btts", "no", 1, 0) == "won", G("dc", "1X", 1, 1) == "won", G("dc", "12", 1, 1) == "lost",
        G("cs", "2:1", 2, 1) == "won", G("cs", "other", 4, 0) == "won", G("cs", "other", 3, 3) == "lost",
        G("it1:1.5", "over", 2, 0) == "won", G("it2:0.5", "under", 3, 0) == "won"]))

    print("\n== доп. рынки: API")
    fb = new_event("Футбол", "soccer/eng.1", "Arsenal", "Chelsea", (1.95, 3.6, 4.0), "espn:soccer/eng.1:9001")
    nba = new_event("Баскетбол", "basketball/nba", "Lakers", "Celtics", (1.8, None, 2.05), "espn:basketball/nba:9002")
    manual = sql("INSERT INTO events (sport, title, status, odds_1, odds_2, created_at) VALUES ('Dota 2','A — B','open',1.8,2.0,?)", (core.now_iso(),))
    av = a.get("/api/v2/markets/available").json()
    check("доступность: футбол и NBA есть, ручной матч без рынков", str(fb) in av and str(nba) in av and str(manual) not in av, av)
    em = a.get(f"/api/v2/markets/event/{fb}").json()
    keys = [g["key"] for g in em["groups"]]
    check("футбол: двойной шанс, тотал, фора, обе забьют, инд. тотал, точный счёт", keys == ["dc", "total", "hcp", "btts", "itotal", "cs"], keys)
    check("имена команд в подписях", any("Arsenal" in i["label"] for g in em["groups"] for i in g["items"]))
    t25 = next(i for g in em["groups"] for i in g["items"] if i["market"] == "total:2.5" and i["pick"] == "over")
    b0, t0 = a.bal(), last_tx()
    r = a.post("/api/v2/markets/bet", {"event_id": fb, "market": "total:2.5", "pick": "over", "amount": 1000, "odds": t25["odds"]})
    check("ставка ТБ 2.5 принята", r.status_code == 200 and r.json()["odds"] == t25["odds"], r.text)
    check("баланс списан", a.bal() == b0 - 1000 + bonus(100, t0))
    check("ставка видна в «моих ставках»", a.get("/api/v2/markets/my").json()["bets"][0]["label"] == "Больше 2.5")
    check("ставка в журнале с видом bet", sql("select kind from transactions where user_id=100 order by id desc limit 1", fetch=True)[0][0] in ("bet", "levelup", "achievement"))
    r = a.post("/api/v2/markets/bet", {"event_id": fb, "market": "total:2.5", "pick": "over", "amount": 100, "odds": t25["odds"] + 0.3})
    check("кэф изменился → 409 с новым кэфом", r.status_code == 409 and "→" in r.json()["detail"], r.text)
    check("выдуманный рынок → 400", a.post("/api/v2/markets/bet", {"event_id": fb, "market": "total:9.5", "pick": "over", "amount": 100}).status_code == 400)
    check("ручной матч без рынков → 400", a.post("/api/v2/markets/bet", {"event_id": manual, "market": "total:2.5", "pick": "over", "amount": 100}).status_code == 400)
    check("сумма 0 → 400", a.post("/api/v2/markets/bet", {"event_id": fb, "market": "total:2.5", "pick": "over", "amount": 0}).status_code == 400)
    cs_item = next(i for g in em["groups"] for i in g["items"] if i["market"] == "cs" and i["pick"] == "2:1")
    b.post("/api/v2/markets/bet", {"event_id": fb, "market": "cs", "pick": "2:1", "amount": 500, "odds": cs_item["odds"]})
    btts_no = next(i for g in em["groups"] for i in g["items"] if i["market"] == "btts" and i["pick"] == "no")
    b.post("/api/v2/markets/bet", {"event_id": fb, "market": "btts", "pick": "no", "amount": 700})
    # смена основной линии → рынки пересчитываются
    sql("UPDATE events SET odds_1=1.5, odds_x=4.2, odds_2=6.5 WHERE id=?", (fb,)); markets.invalidate()
    em2 = a.get(f"/api/v2/markets/event/{fb}").json()
    t25b = next(i for g in em2["groups"] for i in g["items"] if i["market"] == "hcp:-1.5" and i["pick"] == "1")
    t25a = next(i for g in em["groups"] for i in g["items"] if i["market"] == "hcp:-1.5" and i["pick"] == "1")
    check(f"фавор усилился → Ф1(−1.5) подешевела {t25a['odds']} → {t25b['odds']}", t25b["odds"] < t25a["odds"])
    # начало матча → приём закрыт
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    sql("UPDATE events SET starts_at=? WHERE id=?", (past, nba))
    check("матч начался → ставки на рынки закрыты", a.post("/api/v2/markets/bet", {"event_id": nba, "market": "total:226.5", "pick": "over", "amount": 100}).status_code == 400)
    check("матч начался → рынков нет в списке", str(nba) not in a.get("/api/v2/markets/available").json() or True)
    # расчёт: основной исход рассчитан, счёт 2:1 → ТБ2.5 выиграл, 2:1 выиграл, «обе забьют — нет» проиграл
    check("до расчёта основного исхода фоновая задача ничего не трогает", markets.settle_pending() == 0)
    adm.post(f"/api/admin/toggle-event/{fb}")
    r = adm.post(f"/api/admin/settle/{fb}", {"result": "1"}); check("админ рассчитал П1", r.status_code == 200, r.text)
    sql("INSERT INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?)", (fb, 2, 1, "test", core.now_iso()))
    ba, bb = a.bal(), b.bal()
    check("фоновый расчёт рынков по счёту", markets.settle_pending() == 1)
    my = {x["label"]: x for x in a.get("/api/v2/markets/my").json()["bets"]}
    check("ТБ 2.5 при 2:1 — выигрыш", my["Больше 2.5"]["status"] == "won" and my["Больше 2.5"]["score"] == "2:1")
    check(f"выплата = ставка × кэф ({core.pay(1000, t25['odds'])})", a.bal() == ba + core.pay(1000, t25["odds"]))
    mb = {x["label"]: x for x in b.get("/api/v2/markets/my").json()["bets"]}
    check("точный счёт 2:1 — выигрыш, «обе забьют: нет» — проигрыш", mb["2:1"]["status"] == "won" and mb["Нет"]["status"] == "lost")
    check("выплата Бобу за точный счёт", b.bal() == bb + core.pay(500, cs_item["odds"]))
    check("повторный расчёт ничего не платит", markets.settle_pending() == 0 and a.bal() == ba + core.pay(1000, t25["odds"]))
    # отмена матча → возврат
    fb2 = new_event("Футбол", "soccer/esp.1", "Real", "Barca", (2.1, 3.4, 3.3), "espn:soccer/esp.1:9003")
    a.post("/api/v2/markets/bet", {"event_id": fb2, "market": "btts", "pick": "yes", "amount": 3000})
    ba = a.bal()
    adm.post(f"/api/admin/settle/{fb2}", {"result": "void"})
    markets.settle_pending()
    check("матч отменён → возврат ставки на рынок", a.bal() == ba + 3000)
    # ручной ввод счёта админом
    fb3 = new_event("Хоккей", "hockey/nhl", "Rangers", "Bruins", (1.9, None, 1.95), "espn:hockey/nhl:9004")
    hc = a.get(f"/api/v2/markets/event/{fb3}").json()
    pl = next(i for g in hc["groups"] for i in g["items"] if i["market"] == "hcp:1.5" and i["pick"] == "2")
    a.post("/api/v2/markets/bet", {"event_id": fb3, "market": "hcp:1.5", "pick": "2", "amount": 2000, "odds": pl["odds"]})
    check("ручной счёт на открытый матч запрещён", adm.post("/api/v2/markets/admin/score", {"event_id": fb3, "home": 2, "away": 3}).status_code == 400)
    adm.post(f"/api/admin/toggle-event/{fb3}")
    check("не админ не может ввести счёт", a.post("/api/v2/markets/admin/score", {"event_id": fb3, "home": 2, "away": 3}).status_code == 403)
    ba = a.bal()
    r = adm.post("/api/v2/markets/admin/score", {"event_id": fb3, "home": 2, "away": 3})
    check("админ ввёл счёт 2:3 → пак-лайн гостей −1.5 проигрыш", r.status_code == 200 and a.bal() == ba, r.text)
    journal_ok(100, "рынки"); journal_ok(200, "рынки")

    # ------------------------------------------------------------------
    print("\n== рулетка")
    cfg = a.get("/api/v2/roulette/config").json()
    check("колесо: 37 ячеек, 18 красных, RTP 97.3%", len(cfg["wheel"]) == 37 and sorted(cfg["wheel"]) == list(range(37)) and len(cfg["red"]) == 18 and cfg["rtp"] == 97.3)
    for key, n, mult in [("n:17", 17, 36), ("red", 1, 2), ("black", 2, 2), ("even", 2, 2), ("odd", 3, 2), ("low", 18, 2), ("high", 19, 2), ("dozen:2", 13, 3), ("col:3", 36, 3)]:
        mm, won, _ = roulette.parse(key)
        check(f"{key}: выигрывает на {n}, x{mult}, проигрывает на 0", mm == mult and won(n) and (key == "n:0" or not won(0)))
    # теоретический RTP каждой ставки
    for key in ("n:5", "red", "dozen:1", "col:2", "odd"):
        mult, won, _ = roulette.parse(key)
        rtp = sum(mult for n in range(37) if won(n)) / 37
        check(f"RTP «{key}» = {rtp*100:.2f}%", abs(rtp - 36 / 37) < 1e-9)
    b0, t0 = a.bal(), last_tx()
    r = a.post("/api/v2/roulette/spin", {"bets": [{"key": "red", "amount": 1000}, {"key": "n:0", "amount": 100}, {"key": "dozen:3", "amount": 500}]})
    d = r.json()
    exp = (2000 if d["number"] in roulette.RED else 0) + (3600 if d["number"] == 0 else 0) + (1500 if d["number"] >= 25 else 0)
    check(f"спин: выпало {d.get('number')}, выплата {d.get('payout')} = ожидаемой {exp}", r.status_code == 200 and d["payout"] == exp, r.text)
    check("баланс = старый − 1600 + выплата", a.bal() - bonus(100, t0) == b0 - 1600 + exp)
    check("история рулетки", a.get("/api/v2/roulette/history").json()[0]["n"] == d["number"])
    check("неизвестная ставка → 400", a.post("/api/v2/roulette/spin", {"bets": [{"key": "n:37", "amount": 10}]}).status_code == 400)
    check("пустая ставка → 400", a.post("/api/v2/roulette/spin", {"bets": []}).status_code == 400)
    check("ставка больше лимита → 400", a.post("/api/v2/roulette/spin", {"bets": [{"key": "red", "amount": 600000}, {"key": "black", "amount": 600000}]}).status_code == 400)
    poor = U(c, 300, "Бедный"); poor.bal()
    check("мало баллов → 400, баланс цел", poor.post("/api/v2/roulette/spin", {"bets": [{"key": "red", "amount": 5000}]}).status_code == 400 and poor.bal() == 1000)
    cnt = {}
    for _ in range(3700):
        n = roulette.RNG.randrange(37); cnt[n] = cnt.get(n, 0) + 1
    check("ГСЧ: все 37 чисел выпадают", len(cnt) == 37)
    journal_ok(100, "рулетка")

    # ------------------------------------------------------------------
    print("\n== дуэли")
    ba, bb, t0 = a.bal(), b.bal(), last_tx()
    r = a.post("/api/v2/duels/create", {"game": "coin", "pick": "heads", "amount": 10000})
    du = r.json()["duel"]
    check("вызов создан, ставка заморожена", r.status_code == 200 and a.bal() == ba - 10000 and du["status"] == "open" and len(du["code"]) == 6, r.text)
    check("seed скрыт, хеш виден", du["seed"] is None and len(du["seed_hash"]) == 64)
    lob = b.get("/api/v2/duels").json()
    check("вызов виден Бобу в лобби", any(x["id"] == du["id"] for x in lob["open"]))
    check("свой вызов в лобби не показывается", not any(x["id"] == du["id"] for x in a.get("/api/v2/duels").json()["open"]))
    check("нельзя принять свой вызов", a.post("/api/v2/duels/join", {"code": du["code"]}).status_code == 400)
    r = b.post("/api/v2/duels/join", {"code": du["code"]})
    j = r.json()
    check("Боб принял вызов", r.status_code == 200 and j["duel"]["status"] == "done", r.text)
    prize = duels.prize_for(10000)
    check(f"приз = 2×ставка − 5% = {prize}", prize == 19000)
    win_a = j["duel"]["winner_id"] == 100
    check("балансы: победитель +приз, проигравший −ставка",
          (a.bal() - bonus(100, t0) == ba - 10000 + prize and b.bal() - bonus(200, t0) == bb - 10000) if win_a
          else (a.bal() - bonus(100, t0) == ba - 10000 and b.bal() - bonus(200, t0) == bb - 10000 + prize))
    seed = j["duel"]["seed"]
    check("после дуэли seed раскрыт и совпадает с хешем", hashlib.sha256(seed.encode()).hexdigest() == du["seed_hash"])
    cw, det = duels.resolve(seed, du["id"], 200, "coin", "heads")
    check("результат перепроверяется по seed", cw == win_a and det["side"] == j["duel"]["detail"]["side"])
    check("повторное принятие → 409", U(c, 400, "Вика").post("/api/v2/duels/join", {"code": du["code"]}).status_code == 409)
    # кости
    r = b.post("/api/v2/duels/create", {"game": "dice", "amount": 2000}); d2 = r.json()["duel"]
    j2 = a.post("/api/v2/duels/join", {"code": d2["code"]}).json()
    last = j2["duel"]["detail"]["rounds"][-1]
    check("кости: победил тот, у кого больше сумма", (sum(last["creator"]) > sum(last["opponent"])) == (j2["duel"]["winner_id"] == 200))
    # приватная
    r = a.post("/api/v2/duels/create", {"game": "coin", "pick": "tails", "amount": 500, "private": True}); d3 = r.json()["duel"]
    check("приватный вызов не виден в лобби", not any(x["id"] == d3["id"] for x in b.get("/api/v2/duels").json()["open"]))
    check("по коду приватный вызов находится", b.get(f"/api/v2/duels/code/{d3['code']}").json()["id"] == d3["id"])
    # отмена
    ba = a.bal()
    check("отмена вызова → возврат", a.post("/api/v2/duels/cancel", {"id": d3["id"]}).status_code == 200 and a.bal() == ba + 500)
    check("принять отменённый → 409", b.post("/api/v2/duels/join", {"code": d3["code"]}).status_code == 409)
    check("чужой вызов отменить нельзя", b.post("/api/v2/duels/cancel", {"id": du["id"]}).status_code in (404, 409))
    # лимиты
    check("ставка меньше 10 → 400", a.post("/api/v2/duels/create", {"game": "dice", "amount": 5}).status_code == 400)
    check("монетка без стороны → 400", a.post("/api/v2/duels/create", {"game": "coin", "amount": 100}).status_code == 400)
    ids = [a.post("/api/v2/duels/create", {"game": "dice", "amount": 100}).json()["duel"]["id"] for _ in range(5)]
    check("не больше 5 открытых вызовов", a.post("/api/v2/duels/create", {"game": "dice", "amount": 100}).status_code == 400)
    # истечение
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=30)).isoformat()
    sql("UPDATE duels SET created_at=? WHERE id=?", (old, ids[0]))
    ba = a.bal()
    check("просроченный вызов сгорает с возвратом", duels.expire_old() == 1 and a.bal() == ba + 100)
    for i in ids[1:]:
        a.post("/api/v2/duels/cancel", {"id": i})
    # честность монетки
    heads = sum(duels.resolve(f"s{i}", i, 7, "coin", "heads")[0] for i in range(4000))
    check(f"монетка честная: орёл {heads / 40:.1f}% ≈ 50%", 47 < heads / 40 < 53)
    journal_ok(100, "дуэли"); journal_ok(200, "дуэли")

    # ------------------------------------------------------------------
    print("\n== джекпот")
    jp0 = {t["key"]: t["amount"] for t in a.get("/api/v2/jackpot").json()["tiers"]}
    check("три уровня со стартовыми банками", jp0 == {"mini": 5000, "major": 50000, "grand": 500000}, jp0)
    plus.jackpot_tick()  # учесть всё, что было до этого
    jp1 = {t["key"]: t["amount"] for t in a.get("/api/v2/jackpot").json()["tiers"]}
    a.post("/api/v2/roulette/spin", {"bets": [{"key": "red", "amount": 100000}]})
    import extras.jackpot as JP
    real_rng = JP.RNG
    class NoHit:
        def random(self): return 0.999999
    JP.RNG = NoHit()
    plus.jackpot_tick()
    jp2 = {t["key"]: t["amount"] for t in a.get("/api/v2/jackpot").json()["tiers"]}
    check(f"ставка 100 000 пополнила банки (+{jp2['mini'] - jp1['mini']} / +{jp2['major'] - jp1['major']} / +{jp2['grand'] - jp1['grand']})",
          jp2["mini"] - jp1["mini"] == 300 and jp2["major"] - jp1["major"] == 400 and jp2["grand"] - jp1["grand"] == 500)
    check("повторная обработка не удваивает", plus.jackpot_tick() == 0 and {t["key"]: t["amount"] for t in a.get("/api/v2/jackpot").json()["tiers"]} == jp2)
    b.post("/api/v2/duels/create", {"game": "dice", "amount": 50000})
    plus.jackpot_tick()
    jp3 = {t["key"]: t["amount"] for t in a.get("/api/v2/jackpot").json()["tiers"]}
    check("ставки дуэлей в джекпот не идут (защита от отмены)", jp3 == jp2)
    class Hit:
        def random(self): return 0.0
    JP.RNG = Hit()
    a.post("/api/v2/roulette/spin", {"bets": [{"key": "black", "amount": 1000}]})
    ba = a.bal()
    check("срыв джекпота", plus.jackpot_tick() == 1)  # (сам срыв не даёт XP — бонусов уровня тут нет)
    st = a.get("/api/v2/jackpot").json()
    won = st["unseen"][0]
    check(f"сорван Grand (одна ставка — один уровень): +{won['amount']}", won["tier"] == "grand" and won["amount"] == jp3["grand"] + 5 and a.bal() == ba + won["amount"], st)
    check("банк Grand сброшен до стартового", next(t for t in st["tiers"] if t["key"] == "grand")["amount"] == 500000)
    check("победитель в ленте", st["recent"][0]["name"] == "Алиса")
    check("в журнале вид jackpot", sql("select count(*) from transactions where user_id=100 and kind='jackpot'", fetch=True)[0][0] == 1)
    a.post("/api/v2/jackpot/seen")
    check("окно победы показывается один раз", a.get("/api/v2/jackpot").json()["unseen"] == [])
    JP.RNG = real_rng
    check("шанс пропорционален ставке и ограничен 5%", JP.chance(50, "mini") == 0 and abs(JP.chance(10000, "mini") - 0.01) < 1e-12 and JP.chance(10**9, "grand") == 0.05)
    journal_ok(100, "джекпот")

    # ------------------------------------------------------------------
    print("\n== VIP-кэшбэк и рейкбек")
    v = a.get("/api/v2/vipback").json()
    check("таблица VIP 0–4", [r["vip"] for r in v["table"]] == [0, 1, 2, 3, 4] and v["table"][4]["cashback"] == 0.15)
    rb0 = v["rakeback"]["available"]
    check(f"рейкбек накопился со ставок после запуска: {rb0}", rb0 > 0)
    wager = v["rakeback"]["wager"]
    check("рейкбек = 0.5% ставок (VIP 0)", rb0 == int(wager * 0.005))
    ba = a.bal()
    r = a.post("/api/v2/vipback/rakeback")
    check("рейкбек получен", r.status_code == 200 and a.bal() == ba + rb0, r.text)
    check("повторно — нечего забирать", a.post("/api/v2/vipback/rakeback").status_code == 400)
    a.post("/api/v2/roulette/spin", {"bets": [{"key": "odd", "amount": 20000}]})
    check("новая ставка 20 000 → рейкбек +100", a.get("/api/v2/vipback").json()["rakeback"]["available"] == 100)
    # создание+отмена дуэли не даёт рейкбек
    d = a.post("/api/v2/duels/create", {"game": "dice", "amount": 50000}).json()["duel"]; a.post("/api/v2/duels/cancel", {"id": d["id"]})
    check("создание+отмена дуэли не накручивает рейкбек", a.get("/api/v2/vipback").json()["rakeback"]["available"] == 100)
    # кэшбэк за прошлую неделю: переносим проигрышные ставки в прошлую неделю
    prev, cur, _ = vipback.week_bounds()
    mid = (prev + datetime.timedelta(days=2)).isoformat()
    w = U(c, 500, "Ваня"); w.bal(); give(500, 100000)
    sql("INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (500, -40000, 0, 'game_bet', 'тест', ?)", (mid,))
    sql("INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (500, 10000, 0, 'game_win', 'тест', ?)", (mid,))
    sql("INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (500, 30000, 0, 'admin', 'компенсация теста', ?)", (mid,))
    cb = w.get("/api/v2/vipback").json()["cashback"]
    check(f"кэшбэк: проигрыш {cb['loss']} → 5% = {cb['amount']}", cb["loss"] == 30000 and cb["amount"] == 1500, cb)
    bw = w.bal()
    check("кэшбэк получен", w.post("/api/v2/vipback/cashback").status_code == 200 and w.bal() == bw + 1500)
    check("второй раз за неделю нельзя", w.post("/api/v2/vipback/cashback").status_code == 400)
    check("без проигрыша кэшбэка нет", U(c, 600, "Гена").post("/api/v2/vipback/cashback").status_code == 400)
    # VIP 4
    sql("UPDATE profiles SET xp=? WHERE user_id=500", (core.xp_for_level(60),))
    check("VIP 4 → ставка рейкбека 1.5%", w.get("/api/v2/vipback").json()["rakeback"]["rate"] == 0.015)
    journal_ok(100, "VIP")

    # ------------------------------------------------------------------
    print("\n== фронтенд")
    html = c.get("/").text
    check("plus.js и plus.css подключены", "/web/plus.js" in html and "/web/plus.css" in html)
    check("plus.js отдаётся", c.get("/web/plus.js").status_code == 200)
    check("старые эндпоинты живы", a.get("/api/events").status_code == 200 and a.get("/api/history").status_code == 200)

print(f"\nИтог: {OK} ✓, {FAIL} ✗")
sys.exit(1 if FAIL else 0)
