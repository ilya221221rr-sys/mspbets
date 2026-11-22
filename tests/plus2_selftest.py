"""
Самопроверка второй волны (дуэли у создателя, экспресс+, единая лента ставок, ежедневное колесо):
    python tests/plus2_selftest.py
Локальная база, Turso не трогается; сеть (ESPN) подменяется заглушкой.
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
DB = os.path.join(tempfile.mkdtemp(), "plus2.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1", FEED_ENABLED="0", PLUS_BG="0")
import libsql
_real = libsql.connect
libsql.connect = lambda *a, **k: _real(DB)
import app as A
from extras import core, plus, markets, duels, mexpress, betlog, dailywheel
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


def manual_event(title, o, hours=5):
    st = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return sql("INSERT INTO events (sport, title, starts_at, status, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?,?,?)",
               ("Dota 2", title, st, "open", o[0], o[1], o[2], core.now_iso()))


def settle(adm, eid, result, score=None):
    adm.post(f"/api/admin/toggle-event/{eid}")
    if score:
        sql("INSERT OR REPLACE INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?)", (eid, score[0], score[1], "test", core.now_iso()))
    r = adm.post(f"/api/admin/settle/{eid}", {"result": result})
    assert r.status_code == 200, r.text


markets._espn_score = lambda *a, **k: None  # без сети: счёт только из match_scores


class Track:
    """Обёртка соединения: считает открытые транзакции записи (в Turso они держат блокировку всей базы)."""
    open_tx = 0
    writes = []

    def __init__(self, c):
        self._c, self._w = c, False

    def execute(self, q, *a, **k):
        if q.lstrip()[:7].upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
            Track.writes.append(" ".join(q.split()[:3]))
            if not self._w:
                self._w = True
                Track.open_tx += 1
        return self._c.execute(q, *a, **k)

    def _end(self):
        if self._w:
            self._w = False
            Track.open_tx -= 1

    def commit(self):
        self._end()
        return self._c.commit()

    def rollback(self):
        self._end()
        return self._c.rollback()

    def close(self):
        self._end()
        return self._c.close()

    def __getattr__(self, n):
        return getattr(self._c, n)


_plain_get_db = core.ctx.get_db
core.ctx.get_db = lambda: Track(_plain_get_db())


def item(eid, u, market, pick):
    d = u.get(f"/api/v2/markets/event/{eid}").json()
    if market == "1x2":
        return next(i for i in d["main"] if i["pick"] == pick)
    return next(i for g in d["groups"] for i in g["items"] if i["market"] == market and i["pick"] == pick)


def leg(eid, u, market, pick):
    it = item(eid, u, market, pick)
    return {"event_id": eid, "market": market, "pick": pick, "odds": it["odds"]}


def kinds_of(rows):
    return [r["kind"] for r in rows]


with TestClient(A.app) as c:
    a, b, adm, g = U(c, 100, "Алиса"), U(c, 200, "Боб"), U(c, 1, "Админ"), U(c, 300, "Гена")
    a.bal(); b.bal(); adm.bal(); g.bal()
    give(100, 2_000_000); give(200, 2_000_000); give(300, 100_000)

    # ------------------------------------------------------------------
    print("\n== дуэли: создатель видит результат")
    r = a.post("/api/v2/duels/create", {"game": "dice", "amount": 1000})
    did = r.json()["duel"]["id"]; code = r.json()["duel"]["code"]
    p = a.get("/api/v2/duels/pulse").json()
    check("пульс: 1 открытый вызов, непросмотренных нет", p["open"] == 1 and p["unseen"] == [], p)
    r = b.post("/api/v2/duels/join", {"code": code}); check("Боб принял вызов", r.status_code == 200, r.text)
    p = a.get("/api/v2/duels/pulse").json()
    check("у создателя появилась непросмотренная дуэль", p["open"] == 0 and len(p["unseen"]) == 1 and p["unseen"][0]["id"] == did, p)
    u0 = p["unseen"][0]
    check("в ней всё для анимации: кубики, победитель, приз", u0["detail"] and u0["detail"].get("rounds") and u0["winner_id"] in (100, 200) and u0["prize"] == 1900, u0)
    check("seed раскрыт создателю", u0["seed"] and hashlib.sha256(u0["seed"].encode()).hexdigest() == u0["seed_hash"])
    check("у принявшего непросмотренных нет (он видел анимацию сразу)", b.get("/api/v2/duels/pulse").json()["unseen"] == [])
    check("чужой не может отметить просмотренным", b.post("/api/v2/duels/seen", {"id": did}).status_code == 200 and len(a.get("/api/v2/duels/pulse").json()["unseen"]) == 1)
    a.post("/api/v2/duels/seen", {"id": did})
    check("после просмотра — пусто", a.get("/api/v2/duels/pulse").json()["unseen"] == [])
    r = a.post("/api/v2/duels/create", {"game": "coin", "pick": "heads", "amount": 500}); a.post("/api/v2/duels/cancel", {"id": r.json()["duel"]["id"]})
    check("отменённый вызов не попадает в непросмотренные", a.get("/api/v2/duels/pulse").json()["unseen"] == [])

    # ------------------------------------------------------------------
    print("\n== экспресс+: приём")
    fb = new_event("Футбол", "soccer/eng.1", "Арсенал", "Челси", (1.95, 3.6, 4.0), "e1")
    nba = new_event("Баскетбол", "basketball/nba", "Lakers", "Celtics", (1.7, None, 2.2), "e2")
    dota = manual_event("Team Spirit — Gaimin", (1.6, None, 2.3))
    d = a.get(f"/api/v2/markets/event/{fb}").json()
    check("в рынках матча есть основной исход П1/X/П2", [i["pick"] for i in d["main"]] == ["1", "X", "2"] and d["main"][0]["odds"] == 1.95, d["main"])
    check("у баскетбола — без ничьей", [i["pick"] for i in a.get(f"/api/v2/markets/event/{nba}").json()["main"]] == ["1", "2"])
    legs = [leg(fb, a, "total:2.5", "over"), leg(nba, a, "1x2", "1"), {"event_id": dota, "market": "1x2", "pick": "2", "odds": 2.3}]
    exp_total = round(legs[0]["odds"] * legs[1]["odds"] * legs[2]["odds"], 2)
    b0, t0 = a.bal(), last_tx()
    r = a.post("/api/v2/mexpress/bet", {"amount": 10_000, "legs": legs})
    check("экспресс+ из 3 событий (тотал + П1 + П2 ручного матча) принят", r.status_code == 200, r.text)
    mx1 = r.json()["id"]
    check(f"общий кэф = произведение ({exp_total})", r.json()["total_odds"] == exp_total, r.json()["total_odds"])
    check("баланс списан", a.bal() == b0 - 10_000 + bonus(100, t0))
    tx = sql("select kind, ref from transactions where ref=?", (f"mx:{mx1}",), fetch=True)
    check("в журнале вид bet с ref mx:id", tx == [("bet", f"mx:{mx1}")], tx)
    check("с одного матча два события → 400", a.post("/api/v2/mexpress/bet", {"amount": 100, "legs": [leg(fb, a, "total:2.5", "over"), leg(fb, a, "btts", "yes")]}).status_code == 400)
    check("одно событие → 400", a.post("/api/v2/mexpress/bet", {"amount": 100, "legs": [leg(fb, a, "btts", "yes")]}).status_code == 400)
    bad = [dict(legs[0], odds=legs[0]["odds"] + 0.3), legs[1]]
    b1 = a.bal()
    check("кэф изменился → 409, баланс цел", a.post("/api/v2/mexpress/bet", {"amount": 100, "legs": bad}).status_code == 409 and a.bal() == b1)
    q = a.post("/api/v2/mexpress/quote", {"legs": bad}).json()["legs"]
    check("quote отдаёт свежие кэфы", q[0]["odds"] == legs[0]["odds"] and q[0]["open"], q)
    check("неизвестный рынок → 400", a.post("/api/v2/mexpress/bet", {"amount": 100, "legs": [legs[1], {"event_id": fb, "market": "total:9.5", "pick": "over"}]}).status_code == 400)
    check("ручной матч: доп. рынков нет → 400", a.post("/api/v2/mexpress/bet", {"amount": 100, "legs": [legs[0], {"event_id": dota, "market": "total:2.5", "pick": "over"}]}).status_code == 400)
    e3 = new_event("Футбол", "soccer/esp.1", "Реал", "Барселона", (2.4, 3.5, 2.8), "e3")
    e4 = new_event("Футбол", "soccer/ita.1", "Интер", "Милан", (2.5, 3.3, 2.8), "e4")
    def top_cs(eid):
        cs = next(g2 for g2 in a.get(f"/api/v2/markets/event/{eid}").json()["groups"] if g2["key"] == "cs")
        it = max(cs["items"], key=lambda i: i["odds"])
        return {"event_id": eid, "market": it["market"], "pick": it["pick"], "odds": it["odds"]}
    big = [top_cs(fb), top_cs(e3), top_cs(e4)]
    check("общий кэф > 1000 → 400", a.post("/api/v2/mexpress/bet", {"amount": 100, "legs": big}).status_code == 400)
    check("сумма 0 → 400", a.post("/api/v2/mexpress/bet", {"amount": 0, "legs": legs}).status_code == 400)
    check("мало баллов → 400", g.post("/api/v2/mexpress/bet", {"amount": 10_000_000, "legs": legs}).status_code == 400)
    my = a.get("/api/v2/mexpress/my").json()["bets"]
    check("экспресс+ в «моих ставках» с 3 событиями", my[0]["id"] == mx1 and len(my[0]["legs"]) == 3 and my[0]["legs"][0]["label"] == "Тотал: Больше 2.5", my[0]["legs"][0])

    print("\n== экспресс+: расчёт")
    r = a.post("/api/v2/mexpress/bet", {"amount": 5_000, "legs": [leg(e3, a, "1x2", "2"), leg(e4, a, "total:2.5", "under")]})
    mx_lost = r.json()["id"]
    nba_total = next(i["market"] for g2 in a.get(f"/api/v2/markets/event/{nba}").json()["groups"] for i in g2["items"] if i["market"].startswith("total:"))
    r = a.post("/api/v2/mexpress/bet", {"amount": 3_000, "legs": [leg(nba, a, nba_total, "over"), leg(e4, a, "1x2", "1")]})
    mx_nba = r.json()["id"]
    check("до расчёта матчей ничего не меняется", mexpress.settle_pending() == 0)
    settle(adm, e3, "1", (2, 0))
    b2 = a.bal()
    check("матч рассчитан → проигравший экспресс закрыт сразу, не дожидаясь второго матча", mexpress.settle_pending() == 1 and
          sql("select status from mx_bets where id=?", (mx_lost,), fetch=True)[0][0] == "lost" and a.bal() == b2)
    check("событие второго матча ещё ждёт", sql("select status from mx_legs where bet_id=? and event_id=?", (mx_lost, e4), fetch=True)[0][0] == "pending")
    b3, t3 = a.bal(), last_tx()
    settle(adm, fb, "1", (2, 1)); settle(adm, nba, "1"); settle(adm, dota, "2")
    n = mexpress.settle_pending()
    st = sql("select status, payout from mx_bets where id=?", (mx1,), fetch=True)[0]
    check("тотал 2.5 при 2:1 ✓ + П1 Lakers ✓ + П2 Gaimin ✓ → экспресс выиграл", st[0] == "won", st)
    check(f"выплата = 10000 × {exp_total} = {core.pay(10_000, exp_total)}", st[1] == core.pay(10_000, exp_total) and a.bal() - bonus(100, t3) == b3 + st[1], (st, b3, a.bal()))
    check("события помечены выигранными со счётом", sql("select status, score from mx_legs where bet_id=? order by id", (mx1,), fetch=True)[0] == ("won", "2:1"))
    check("повторный расчёт ничего не платит", mexpress.settle_pending() == 0 and a.bal() - bonus(100, t3) == b3 + st[1])
    check("П1 в баскетболе считается без счёта, а тотал очков ждёт счёт", sql("select status from mx_legs where bet_id=? and event_id=?", (mx_nba, nba), fetch=True)[0][0] == "pending")
    sql("INSERT INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?)", (nba, 115, 104, "test", core.now_iso()))
    mexpress.settle_pending()
    check("появился счёт 115:104 → тотал очков рассчитан", sql("select status, score from mx_legs where bet_id=? and event_id=?", (mx_nba, nba), fetch=True)[0][1] == "115:104")
    settle(adm, e4, "void")
    mexpress.settle_pending()
    st = sql("select status, payout, total_odds from mx_bets where id=?", (mx_nba,), fetch=True)[0]
    nba_leg = sql("select status, odds from mx_legs where bet_id=? and event_id=?", (mx_nba, nba), fetch=True)[0]
    check(f"отменённый матч в экспрессе = кэф 1 (осталось {nba_leg[1]})", nba_leg[0] in ("won", "lost") and (st[0] == "lost" or (st[0] == "won" and abs(st[2] - nba_leg[1]) < 0.005 and st[1] == core.pay(3000, nba_leg[1]))), (st, nba_leg))
    v1 = manual_event("A — B", (1.8, None, 2.0)); v2 = manual_event("C — D", (1.8, None, 2.0))
    r = a.post("/api/v2/mexpress/bet", {"amount": 700, "legs": [{"event_id": v1, "market": "1x2", "pick": "1", "odds": 1.8}, {"event_id": v2, "market": "1x2", "pick": "1", "odds": 1.8}]})
    mxv = r.json()["id"]
    b4 = a.bal(); settle(adm, v1, "void"); settle(adm, v2, "void"); mexpress.settle_pending()
    check("все матчи отменены → возврат ставки", sql("select status from mx_bets where id=?", (mxv,), fetch=True)[0][0] == "void" and a.bal() == b4 + 700)
    old = new_event("Футбол", "soccer/fra.1", "ПСЖ", "Лион", (1.5, 4.0, 6.0), "e5")
    o2 = manual_event("E — F", (1.8, None, 2.0))
    r = a.post("/api/v2/mexpress/bet", {"amount": 400, "legs": [leg(old, a, "btts", "yes"), {"event_id": o2, "market": "1x2", "pick": "1", "odds": 1.8}]})
    mxo = r.json()["id"]
    settle(adm, old, "1"); settle(adm, o2, "1")
    sql("UPDATE events SET starts_at=? WHERE id=?", ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=6)).isoformat(), old))
    mexpress.settle_pending()
    st = sql("select status, payout from mx_bets where id=?", (mxo,), fetch=True)[0]
    check("счёта нет 5 дней → событие = кэф 1, экспресс по остальным", st == ("won", core.pay(400, 1.8)), st)
    journal_ok(100, "экспресс+")

    # ------------------------------------------------------------------
    print("\n== единая лента ставок")
    f1 = new_event("Футбол", "soccer/eng.1", "Ливерпуль", "Эвертон", (1.6, 4.0, 5.5), "f1")
    f2 = new_event("Футбол", "soccer/ger.1", "Бавария", "Дортмунд", (1.7, 4.0, 4.5), "f2")
    m1 = manual_event("NaVi — Vitality", (1.9, None, 1.9))
    rs = b.post("/api/predict", {"event_id": f1, "points": 300, "choice": "1"}); check("Боб: одинар П1", rs.status_code == 200, rs.text)
    rs = b.post("/api/express", {"amount": 400, "items": [{"event_id": f1, "choice": "X"}, {"event_id": m1, "choice": "2"}]}); check("Боб: старый экспресс", rs.status_code == 200, rs.text)
    rs = b.post("/api/v2/markets/bet", {"event_id": f2, "market": "btts", "pick": "yes", "amount": 500}); check("Боб: ставка на «обе забьют»", rs.status_code == 200, rs.text)
    rs = b.post("/api/v2/mexpress/bet", {"amount": 600, "legs": [leg(f1, b, "total:2.5", "over"), leg(f2, b, "hcp:-1.5", "1")]}); check("Боб: экспресс+ (тотал + фора)", rs.status_code == 200, rs.text)
    pl = a.get("/api/v2/betlog/player/200").json()
    check("в профиле Боба видны все 4 вида ставок, свежие сверху", kinds_of(pl["bets"])[:4] == ["mx", "market", "express", "single"], kinds_of(pl["bets"]))
    mk = next(x for x in pl["bets"] if x["kind"] == "market")
    check("ставка на рынок — с подписью «Обе забьют: Да»", mk["legs"][0]["label"] == "Обе забьют: Да" and mk["legs"][0]["title"] == "Бавария — Дортмунд", mk["legs"][0])
    mxr = next(x for x in pl["bets"] if x["kind"] == "mx")
    check("экспресс+ — с форой и тоталом", [l["label"] for l in mxr["legs"]] == ["Тотал: Больше 2.5", "Фора: Бавария (−1.5)"], mxr["legs"])
    check("все ставки Боба можно скопировать (матчи не начались)", all(x["copyable"] for x in pl["bets"][:4]))
    check("в профиле нет user_id (лишнего не отдаём)", "user_id" not in pl["bets"][0])
    pa = a.get("/api/v2/betlog/player/100").json()
    check("у Алисы: сыгравшие экспрессы+ со статусами", any(x["kind"] == "mx" and x["status"] == "won" and x["payout"] > 0 for x in pa["bets"]))
    old_p = a.get("/api/v2/player/200").json()
    check("старый профиль работает как раньше (только одинары)", len(old_p["bets"]) == 1)
    fd = a.get("/api/v2/betlog/feed").json()
    check("лента: ставки всех видов от других игроков", {"single", "express", "market", "mx"} <= set(kinds_of(fd)), kinds_of(fd))
    check("в ленте имя и уровень", fd[0]["name"] == "Боб" and fd[0]["level"] >= 1 and fd[0]["uid"] == 200)
    for kind in ("single", "market", "express", "mx"):
        x = next(y for y in fd if y["kind"] == kind)
        b5 = a.bal(); t5 = last_tx()
        r = a.post("/api/v2/betlog/copy", {"kind": kind, "id": x["id"], "amount": 200})
        check(f"копирование «{kind}» по текущему кэфу", r.status_code == 200 and a.bal() - bonus(100, t5) == b5 - 200, r.text)
    mine = a.get("/api/v2/betlog/player/100").json()["bets"][:4]
    check("скопированные ставки у Алисы — те же 4 вида", sorted(kinds_of(mine)) == sorted(["single", "market", "express", "mx"]), kinds_of(mine))
    check("свою ставку копировать нельзя", b.post("/api/v2/betlog/copy", {"kind": "market", "id": mk["id"], "amount": 100}).status_code == 400)
    check("неверный вид → 400", a.post("/api/v2/betlog/copy", {"kind": "zzz", "id": 1, "amount": 100}).status_code == 400)
    sql("UPDATE profiles SET hide_bets=1 WHERE user_id=200")
    check("Боб скрыл ставки → в профиле скрыто", a.get("/api/v2/betlog/player/200").json() == {"hidden": True, "me": False, "bets": []})
    check("сам Боб свои ставки видит", len(b.get("/api/v2/betlog/player/200").json()["bets"]) >= 4)
    check("скрытые не попадают в ленту", all(x["uid"] != 200 for x in a.get("/api/v2/betlog/feed").json()))
    check("скрытую ставку не скопировать", a.post("/api/v2/betlog/copy", {"kind": "market", "id": mk["id"], "amount": 100}).status_code == 404)
    sql("UPDATE profiles SET hide_bets=0 WHERE user_id=200")
    sql("UPDATE events SET status='closed' WHERE id=?", (f2,))
    fd2 = a.get("/api/v2/betlog/feed").json()
    check("матч закрыт → ставки на него пропали из ленты (копировать нельзя)", all(all(l["event_id"] != f2 for l in x["legs"]) for x in fd2))
    check("а в профиле остались, без копирования", any(x["kind"] == "market" and not x["copyable"] for x in a.get("/api/v2/betlog/player/200").json()["bets"]))
    journal_ok(100, "лента"); journal_ok(200, "лента")

    # ------------------------------------------------------------------
    print("\n== ежедневное колесо")
    ch = dailywheel.chances()
    check("шансы: 20к 40%, 30к 25%, 50к 17%, 75к 10%, 100к 5%, 200к 2.5%, 500к 0.5%",
          {k: round(v, 4) for k, v in ch.items()} == {20000: 0.4, 30000: 0.25, 50000: 0.17, 75000: 0.1, 100000: 0.05, 200000: 0.025, 500000: 0.005})
    ev = sum(k * v for k, v in ch.items())
    check(f"минимум 20 000, максимум 500 000, среднее {ev:.0f}", min(ch) == 20000 and max(ch) == 500000 and abs(ev - 44000) < 1)
    import random
    rnd = random.Random(7)
    N = 300_000
    cnt = {}
    for _ in range(N):
        p = dailywheel.SECTORS[dailywheel.pick_sector(rnd)][0]
        cnt[p] = cnt.get(p, 0) + 1
    dev = max(abs(cnt.get(k, 0) / N - v) for k, v in ch.items())
    check(f"ГСЧ: частоты совпадают с шансами (откл. {dev:.4f}), 500к выпал {cnt.get(500000, 0)} раз из {N}", dev < 0.004 and cnt.get(500000, 0) > 0)
    s0 = g.get("/api/v2/dailywheel").json()
    check("колесо доступно, 10 секторов", s0["available"] and len(s0["sectors"]) == 10 and s0["today"] is None)
    b6 = g.bal()
    r = g.post("/api/v2/dailywheel/spin"); j = r.json()
    check("вращение: приз из сектора", r.status_code == 200 and j["prize"] == dailywheel.SECTORS[j["sector"]][0], r.text)
    check("приз зачислен", g.bal() == b6 + j["prize"] and j["balance"] == b6 + j["prize"])
    check("второе вращение сегодня → 400", g.post("/api/v2/dailywheel/spin").status_code == 400 and g.bal() == b6 + j["prize"])
    s1 = g.get("/api/v2/dailywheel").json()
    check("после вращения: недоступно, показан сегодняшний приз и время сброса", not s1["available"] and s1["today"]["prize"] == j["prize"] and s1["next_at"] > core.now_iso())
    check("в журнале вид daily_wheel", sql("select kind, amount from transactions where user_id=300 and kind='daily_wheel'", fetch=True) == [("daily_wheel", j["prize"])])
    sql("UPDATE daily_wheel SET day='2000-01-01' WHERE user_id=300")
    check("на следующий день снова можно", g.post("/api/v2/dailywheel/spin").status_code == 200)
    sql("INSERT INTO daily_wheel (user_id, day, sector, prize, created_at) VALUES (200, '2000-01-02', 0, 500000, ?)", (core.now_iso(),))
    check("крупные выигрыши в ленте колеса", any(x["prize"] == 500000 and x["name"] == "Боб" for x in g.get("/api/v2/dailywheel").json()["big_wins"]))
    check("вращение не даёт XP (это не ставка)", sql("select xp from profiles where user_id=300", fetch=True)[0][0] == 0)
    journal_ok(300, "колесо")

    print("\n== короткие транзакции Turso (зависание «Бонусов», SQLITE_BUSY)")
    gets = ["/api/v2/vipback", "/api/v2/dailywheel", "/api/v2/duels", "/api/v2/duels/pulse", "/api/v2/jackpot",
            "/api/v2/markets/available", f"/api/v2/markets/event/{fb}", "/api/v2/markets/my", "/api/v2/mexpress/my",
            "/api/v2/betlog/player/200", "/api/v2/betlog/feed", "/api/v2/roulette/history", "/api/v2/roulette/config"]
    for u in gets:
        a.get(u)  # прогрев (первый вход игрока может создать профиль)
    Track.writes.clear()
    codes = {u: a.get(u).status_code for u in gets}
    check("GET-эндпоинты plus отвечают 200", all(v == 200 for v in codes.values()), codes)
    check("GET-эндпоинты plus ничего не пишут — не берут блокировку записи Turso", Track.writes == [], Track.writes)
    from extras import plusdb
    plusdb.forget(100)
    Track.writes.clear()
    a.get("/api/v2/vipback")
    check("vipback: известный игрок не открывает транзакцию даже после сброса кэша", Track.writes == [], Track.writes)
    plusdb.forget()
    Track.writes.clear()
    nu = U(c, 777, "Новичок")
    r = nu.get("/api/v2/vipback")
    check("vipback: новый игрок регистрируется (баланс 1000) отдельной короткой записью",
          r.status_code == 200 and nu.bal() == 1000 and all(w.startswith("INSERT OR IGNORE") for w in Track.writes) and Track.writes, Track.writes)
    check("индекс transactions(user_id, created_at) для кэшбэка",
          bool(sql("select 1 from sqlite_master where type='index' and name='idx_tx_user_time'", fetch=True)))
    # Запрос счёта к ESPN — только без открытой транзакции записи
    f2 = new_event("Футбол", "soccer/esp.1", "Реал", "Барселона", (2.4, 3.5, 2.8), "espn:soccer/esp.1:t1")
    f3 = new_event("Футбол", "soccer/ita.1", "Милан", "Интер", (2.6, 3.3, 2.7), "espn:soccer/ita.1:t2")
    t_f2 = item(f2, b, "total:2.5", "over")
    rb = b.post("/api/v2/markets/bet", {"event_id": f2, "market": "total:2.5", "pick": "over", "amount": 100, "odds": t_f2["odds"]})
    rx = b.post("/api/v2/mexpress/bet", {"amount": 100, "legs": [leg(f2, b, "total:2.5", "over"), leg(f3, b, "btts", "yes")]})
    check("ставки для проверки расчёта приняты", rb.status_code == 200 and rx.status_code == 200, rb.text + rx.text)
    settle(adm, f2, "1"); settle(adm, f3, "1")
    seen = []
    def spy(conn, ev, cache):
        seen.append((ev["id"], Track.open_tx))
        return (2, 1)
    markets._espn_score = spy
    try:
        markets.settle_pending(); mexpress.settle_pending()
    finally:
        markets._espn_score = lambda *a, **k: None
    check("счёт запрошен с ESPN для обоих матчей", {e for e, _ in seen} == {f2, f3}, seen)
    check("во время сетевого запроса нет открытых транзакций записи", seen and all(o == 0 for _, o in seen), seen)
    check("ставка на тотал рассчитана по счёту ESPN", sql("select status, score from market_bets where id=?", (rb.json()["id"],), fetch=True) == [("won", "2:1")])
    check("экспресс+ рассчитан по счёту ESPN", sql("select status from mx_bets where id=?", (rx.json()["id"],), fetch=True)[0][0] == "won")
    check("счета сохранены в match_scores", sql(f"select count(*) from match_scores where event_id in ({f2},{f3}) and source='espn'", fetch=True)[0][0] == 2)

    print("\n== фронтенд")
    js = c.get("/web/plus.js").text
    check("plus.js: пульс дуэлей, экспресс+, лента, колесо", all(x in js for x in ("/api/v2/duels/pulse", "/api/v2/mexpress/bet", "/api/v2/betlog/player/", "/api/v2/dailywheel/spin")))
    check("старые эндпоинты живы", a.get("/api/events").status_code == 200 and a.get("/api/v2/social/feed").status_code == 200)

print(f"\nИтог: {OK} ✓, {FAIL} ✗")
sys.exit(1 if FAIL else 0)
