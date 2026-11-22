"""
Самопроверка первой волны MSP 3.0 (бонус и страховка экспресса, фрибеты, сезонный пропуск,
ставки на новые рынки в истории, «Поделиться» ссылкой):
    python tests/wave1_selftest.py
Локальная база, Turso не трогается; сеть (ESPN) подменяется заглушкой.
"""
import datetime
import hashlib
import hmac
import json
import logging
import os
import sys
import tempfile
import time
from urllib.parse import urlencode

logging.getLogger("httpx").setLevel(logging.WARNING)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join(tempfile.mkdtemp(), "wave1.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1", FEED_ENABLED="0", PLUS_BG="0")
import libsql
_real = libsql.connect
libsql.connect = lambda *a, **k: _real(DB)
import app as A
from extras import accaperks, betcards, core, freebets, markets, mexpress, plus, seasonpass, wave1  # noqa: F401  (модули нужны тесту через sys.modules)
from fastapi.testclient import TestClient

core.ctx.bot = None
markets._espn_score = lambda *a, **k: None  # без сети: счёт только из match_scores
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
    return sql("select coalesce(sum(amount),0) from transactions where user_id=? and id>? and kind in ('levelup','achievement')", (uid, since), fetch=True)[0][0]


def kind_sum(uid, kind, since=0):
    return sql("select coalesce(sum(amount),0) from transactions where user_id=? and kind=? and id>?", (uid, kind, since), fetch=True)[0][0]


def journal_ok(uid, label):
    b = sql("select balance from users where user_id=?", (uid,), fetch=True)[0][0]
    sm = sql("select coalesce(sum(amount),0) from transactions where user_id=?", (uid,), fetch=True)[0][0]
    check(f"журнал сходится с балансом ({label}): 1000+{sm}={b}", 1000 + sm == b)


def manual_event(title, o, hours=5):
    st = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return sql("INSERT INTO events (sport, title, starts_at, status, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?,?,?)",
               ("Dota 2", title, st, "open", o[0], o[1], o[2], core.now_iso()))


def espn_event(home, away, o, ext, hours=5):
    st = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    eid = sql("INSERT INTO events (sport, title, starts_at, status, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?,?,?)",
              ("Футбол", f"{home} — {away}", st, "open", o[0], o[1], o[2], core.now_iso()))
    sql("INSERT INTO feed_events (event_id, source, ext_id, league, sport_path, home, away, starts_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (eid, "espn", ext, "Лига", "soccer/eng.1", home, away, st, core.now_iso()))
    markets.invalidate()
    return eid


def settle(adm, eid, result, score=None):
    adm.post(f"/api/admin/toggle-event/{eid}")
    if score:
        sql("INSERT OR REPLACE INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?)", (eid, score[0], score[1], "test", core.now_iso()))
    r = adm.post(f"/api/admin/settle/{eid}", {"result": result})
    assert r.status_code == 200, r.text


def item(eid, u, market, pick):
    d = u.get(f"/api/v2/markets/event/{eid}").json()
    if market == "1x2":
        return next(i for i in d["main"] if i["pick"] == pick)
    return next(i for g in d["groups"] for i in g["items"] if i["market"] == market and i["pick"] == pick)


def perk(kind, bid):
    r = sql("select perk, pct, amount from w1_perks where kind=? and bet_id=?", (kind, bid), fetch=True)
    return r[0] if r else None


def old_express(u, amount, picks):
    """picks: [(event_id, choice)] → id старого экспресса."""
    r = u.post("/api/express", {"amount": amount, "items": [{"event_id": e, "choice": c} for e, c in picks]})
    assert r.status_code == 200, r.text
    return sql("select max(id) from bets where user_id=?", (u.uid,), fetch=True)[0][0]


def fb_give(adm, uid, amount, days=7):
    r = adm.post("/api/v2/freebets/admin/give", {"user_id": uid, "amount": amount, "days": days})
    assert r.status_code == 200, r.text
    return r.json()["id"]


with TestClient(A.app) as c:
    a, b, adm, g, h = U(c, 100, "Алиса"), U(c, 200, "Боб"), U(c, 1, "Админ"), U(c, 300, "Гена"), U(c, 400, "Даша")
    for u in (a, b, adm, g, h):
        u.bal()
    give(100, 5_000_000); give(200, 5_000_000); give(300, 1_000_000)

    print("\n== правила и сводка")
    s = a.get("/api/v2/wave1/summary").json()
    check("сводка: правила бонуса, фрибеты, пропуск", s["rules"]["boost"]["3"] == 5 and s["rules"]["boost"]["12"] == 50 and s["freebets"] == [] and s["pass"]["level"] == 0, s)
    check("бонус: 3 → 5%, 7 → 25%, 12 → 50%, 2 → 0, 15 → 50%", [accaperks.boost_pct(n) for n in (3, 7, 12, 2, 15)] == [5, 25, 50, 0, 50])
    check("страховка только от 4 событий с кэфом ≥ 1.30", accaperks.insurable([1.3, 1.5, 2, 3]) and not accaperks.insurable([1.5, 1.5, 1.5]) and not accaperks.insurable([1.29, 2, 2, 2]))
    check("правила доступны и отдельно", a.get("/api/v2/perks/rules").json()["ins_cap"] == accaperks.INS_CAP)
    check("без Telegram → 401", c.get("/api/v2/wave1/summary").status_code == 401)

    # ------------------------------------------------------------------
    print("\n== бонус к экспрессу")
    E = [manual_event(f"Команда {i} — Соперник {i}", (1.5, None, 2.6)) for i in range(12)]
    t0 = last_tx()
    x1 = old_express(a, 1000, [(e, "1") for e in E[:4]])
    for e in E[:4]:
        settle(adm, e, "1")
    check("экспресс из 4 событий выиграл", sql("select status from bets where id=?", (x1,), fetch=True)[0][0] == "won")
    t1 = last_tx()
    wave1.tick()
    profit = int(1000 * 1.5 ** 4) - 1000
    check(f"бонус +10% к чистому выигрышу ({profit} → {profit // 10})", perk("express", x1) == ("boost", 10, profit // 10) and kind_sum(100, "acca_boost", t1) == profit // 10, perk("express", x1))
    wave1.tick()
    check("повторная сверка не платит второй раз", kind_sum(100, "acca_boost", t1) == profit // 10)
    low = [manual_event(f"Фаворит {i} — Аутсайдер {i}", (1.2, None, 4.0)) for i in range(2)]
    x2 = old_express(a, 1000, [(E[4], "1"), (low[0], "1"), (low[1], "1")])
    for e in (E[4], *low):
        settle(adm, e, "1")
    wave1.tick()
    check("события с кэфом < 1.30 не считаются → бонуса нет", perk("express", x2)[0] == "none")
    # экспресс+ с 5 событиями: +15%
    F = [manual_event(f"Клуб {i} — Гость {i}", (1.4, None, 2.9)) for i in range(5)]
    r = a.post("/api/v2/mexpress/bet", {"amount": 2000, "legs": [{"event_id": e, "market": "1x2", "pick": "1", "odds": 1.4} for e in F]})
    mx1 = r.json()["id"]
    for e in F:
        settle(adm, e, "1")
    mexpress.settle_pending()
    pay = sql("select payout from mx_bets where id=?", (mx1,), fetch=True)[0][0]
    wave1.tick()
    check(f"экспресс+ из 5 событий: +15% ({(pay - 2000) * 15 // 100})", perk("mx", mx1) == ("boost", 15, (pay - 2000) * 15 // 100), perk("mx", mx1))
    # ставки до запуска функции не пересчитываются
    G = [manual_event(f"Старый {i} — Матч {i}", (1.5, None, 2.6)) for i in range(3)]
    x0 = old_express(a, 1000, [(e, "1") for e in G])
    sql("UPDATE plus_meta SET value=? WHERE key='w1_perks_start_express'", (str(x0),))
    for e in G:
        settle(adm, e, "1")
    wave1.tick()
    check("экспресс, сделанный до запуска, не трогаем", perk("express", x0) is None)
    sql("UPDATE plus_meta SET value='0' WHERE key='w1_perks_start_express'")
    wave1.tick()

    # ------------------------------------------------------------------
    print("\n== страховка экспресса")
    I = [manual_event(f"Страх {i} — Тест {i}", (1.6, None, 2.4)) for i in range(12)]
    x3 = old_express(a, 5000, [(e, "1") for e in I[:4]])
    settle(adm, I[0], "2")
    wave1.tick()
    check("экспресс проиграл, но остальные матчи не сыграны → ждём", sql("select status from bets where id=?", (x3,), fetch=True)[0][0] == "lost" and perk("express", x3) is None)
    for e in I[1:4]:
        settle(adm, e, "1")
    fb0 = len(a.get("/api/v2/freebets").json()["active"])
    wave1.tick()
    act = a.get("/api/v2/freebets").json()["active"]
    check("не зашло ровно одно из 4 → фрибет на сумму ставки", perk("express", x3) == ("insurance", 0, 5000) and len(act) == fb0 + 1 and act[-1]["amount"] == 5000 and act[-1]["source"] == "insurance", act)
    x4 = old_express(a, 5000, [(e, "1") for e in I[4:8]])
    settle(adm, I[4], "2"); settle(adm, I[5], "2"); settle(adm, I[6], "1"); settle(adm, I[7], "1")
    wave1.tick()
    check("не зашло два события → страховки нет", perk("express", x4)[0] == "none")
    x5 = old_express(a, 5000, [(e, "1") for e in I[8:11]])
    settle(adm, I[8], "2"); settle(adm, I[9], "1"); settle(adm, I[10], "1")
    wave1.tick()
    check("экспресс из 3 событий не страхуется", perk("express", x5)[0] == "none")
    big = [manual_event(f"Крупный {i} — Матч {i}", (1.6, None, 2.4)) for i in range(4)]
    x6 = old_express(a, 500_000, [(e, "1") for e in big])
    settle(adm, big[0], "2")
    for e in big[1:]:
        settle(adm, e, "1")
    wave1.tick()
    check(f"страховка не больше {accaperks.INS_CAP}", perk("express", x6) == ("insurance", 0, accaperks.INS_CAP))
    # экспресс+ со ставкой на тотал: закрыт по первому проигрышу, остальные досчитываем по счёту
    S = [espn_event(f"Хозяева {i}", f"Гости {i}", (2.0, 3.4, 3.6), f"s{i}") for i in range(4)]
    tot = item(S[1], a, "total:2.5", "over")
    legs = [{"event_id": S[0], "market": "1x2", "pick": "1", "odds": 2.0}, {"event_id": S[1], "market": "total:2.5", "pick": "over", "odds": tot["odds"]},
            {"event_id": S[2], "market": "1x2", "pick": "1", "odds": 2.0}, {"event_id": S[3], "market": "1x2", "pick": "1", "odds": 2.0}]
    r = a.post("/api/v2/mexpress/bet", {"amount": 3000, "legs": legs})
    mx2 = r.json()["id"]
    settle(adm, S[0], "2", (0, 1))
    mexpress.settle_pending()
    check("экспресс+ закрыт по первому проигрышу", sql("select status from mx_bets where id=?", (mx2,), fetch=True)[0][0] == "lost")
    wave1.tick()
    check("остальные матчи ещё идут → ждём", perk("mx", mx2) is None)
    adm.post(f"/api/admin/toggle-event/{S[1]}"); adm.post(f"/api/admin/settle/{S[1]}", {"result": "1"})
    settle(adm, S[2], "1", (2, 0)); settle(adm, S[3], "1", (1, 0))
    wave1.tick()
    check("тотал без счёта → всё ещё ждём", perk("mx", mx2) is None)
    markets._espn_score = lambda conn, ev, cache: (3, 1) if ev["id"] == S[1] else None  # ESPN отдал счёт
    wave1.tick()
    markets._espn_score = lambda *a, **k: None
    check("счёт подтянут (без открытой транзакции), тотал зашёл → страховка", perk("mx", mx2) == ("insurance", 0, 3000)
          and sql("select home, away from match_scores where event_id=?", (S[1],), fetch=True) == [(3, 1)], perk("mx", mx2))
    journal_ok(100, "бонусы и страховка")

    # ------------------------------------------------------------------
    print("\n== фрибеты")
    fb_single = fb_give(adm, 200, 10_000)
    check("админ выдал фрибет, его видно", any(f["id"] == fb_single and f["amount"] == 10_000 and f["source"] == "admin" for f in b.get("/api/v2/freebets").json()["active"]))
    check("не админ не может выдавать → 403", b.post("/api/v2/freebets/admin/give", {"user_id": 200, "amount": 5}).status_code == 403)
    ev1 = manual_event("Фри — Бет", (2.0, None, 1.8))
    b0, t0 = b.bal(), last_tx()
    r = b.post("/api/v2/freebets/use", {"freebet_id": fb_single, "type": "single", "event_id": ev1, "choice": "1", "odds": 2.0})
    check("ставка фрибетом на П1 принята, чистый выигрыш 10 000", r.status_code == 200 and r.json()["net_win"] == 10_000 and r.json()["kind"] == "single", r.text)
    pid = r.json()["id"]
    check("баланс не изменился (жетон +10к, ставка −10к)", b.bal() == b0 + bonus(200, t0))
    check("фрибет второй раз → 400", b.post("/api/v2/freebets/use", {"freebet_id": fb_single, "type": "single", "event_id": ev1, "choice": "1"}).status_code == 400)
    check("в списке активных его больше нет", all(f["id"] != fb_single for f in b.get("/api/v2/freebets").json()["active"]))
    settle(adm, ev1, "1")
    wave1.tick()
    check("выиграл: зачислен только чистый выигрыш (+10 000)", b.bal() == b0 + 10_000 + bonus(200, t0), (b.bal(), b0))
    check("ставка фрибетом закрыта", sql("select state, result, owed from w1_fb_bets where kind='single' and bet_id=?", (pid,), fetch=True) == [("done", "won", 0)])
    # проигрыш
    fbl = fb_give(adm, 200, 7000)
    ev2 = manual_event("Луз — Тест", (2.0, None, 1.8))
    b0, t0 = b.bal(), last_tx()
    b.post("/api/v2/freebets/use", {"freebet_id": fbl, "type": "single", "event_id": ev2, "choice": "1"})
    settle(adm, ev2, "2")
    wave1.tick()
    check("проиграл: баланс не изменился", b.bal() == b0 + bonus(200, t0))
    # чужой, просроченный, изменившийся кэф
    fbo = fb_give(adm, 100, 5000)
    ev3 = manual_event("Чужой — Фрибет", (2.0, None, 1.8))
    check("чужой фрибет → 400", b.post("/api/v2/freebets/use", {"freebet_id": fbo, "type": "single", "event_id": ev3, "choice": "1"}).status_code == 400)
    fbe = fb_give(adm, 200, 5000)
    sql("UPDATE w1_freebets SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (fbe,))
    check("просроченный фрибет → 400 и в истории «сгорел»", b.post("/api/v2/freebets/use", {"freebet_id": fbe, "type": "single", "event_id": ev3, "choice": "1"}).status_code == 400
          and any(x["id"] == fbe and x["status"] == "expired" for x in b.get("/api/v2/freebets").json()["history"]))
    fbc = fb_give(adm, 200, 5000)
    b0 = b.bal()
    r = b.post("/api/v2/freebets/use", {"freebet_id": fbc, "type": "single", "event_id": ev3, "choice": "1", "odds": 2.5})
    check("кэф изменился → 409, фрибет остался, баланс цел", r.status_code == 409 and b.bal() == b0
          and any(f["id"] == fbc for f in b.get("/api/v2/freebets").json()["active"]), r.text)
    # доп. рынок фрибетом
    m1 = espn_event("Ливерпуль", "Эвертон", (1.8, 3.8, 4.5), "fbm")
    it = item(m1, b, "total:2.5", "over")
    b0, t0 = b.bal(), last_tx()
    r = b.post("/api/v2/freebets/use", {"freebet_id": fbc, "type": "market", "event_id": m1, "market": "total:2.5", "pick": "over", "odds": it["odds"]})
    check("фрибет на тотал больше 2.5 принят", r.status_code == 200 and r.json()["kind"] == "market", r.text)
    mb = r.json()["id"]
    settle(adm, m1, "1", (3, 1))
    markets.settle_pending()
    wave1.tick()
    net = core.pay(5000, it["odds"]) - 5000
    check(f"тотал зашёл → чистый выигрыш {net}", b.bal() == b0 + net + bonus(200, t0), (b.bal() - b0, net))
    # экспресс+ фрибетом
    fbx = fb_give(adm, 200, 4000)
    X = [manual_event(f"Икс {i} — Игрек {i}", (1.5, None, 2.6)) for i in range(4)]
    b0, t0 = b.bal(), last_tx()
    r = b.post("/api/v2/freebets/use", {"freebet_id": fbx, "type": "mx", "legs": [{"event_id": e, "market": "1x2", "pick": "1", "odds": 1.5} for e in X]})
    check("экспресс+ фрибетом принят", r.status_code == 200 and r.json()["kind"] == "mx", r.text)
    mxf = r.json()["id"]
    settle(adm, X[0], "2")
    for e in X[1:]:
        settle(adm, e, "1")
    mexpress.settle_pending()
    wave1.tick(); wave1.tick()
    check("экспресс фрибетом не страхуется (не зашло одно из 4)", perk("mx", mxf)[0] == "none" and b.bal() == b0 + bonus(200, t0))
    # отмена матча: фрибет возвращается жетоном
    fbv = fb_give(adm, 200, 6000)
    ev4 = manual_event("Отмена — Матча", (2.0, None, 1.8))
    b0, t0 = b.bal(), last_tx()
    b.post("/api/v2/freebets/use", {"freebet_id": fbv, "type": "single", "event_id": ev4, "choice": "1"})
    settle(adm, ev4, "void")
    wave1.tick()
    act = b.get("/api/v2/freebets").json()["active"]
    check("матч отменён: баллы не начислены, фрибет вернулся жетоном", b.bal() == b0 + bonus(200, t0) and any(f["source"] == "void" and f["amount"] == 6000 for f in act), act)
    # долг: выиграл и успел потратить до сверки
    fbd = fb_give(adm, 400, 2000)
    ev5 = manual_event("Долг — Тест", (3.0, None, 1.4))
    r = h.post("/api/v2/freebets/use", {"freebet_id": fbd, "type": "single", "event_id": ev5, "choice": "1"})
    check("игрок с балансом 1000 ставит фрибет 2000", r.status_code == 200, r.text)
    settle(adm, ev5, "1")
    bal = h.bal()
    sql("UPDATE users SET balance=500 WHERE user_id=400")
    sql("INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (400, ?, 500, 'game_bet', 'тест: потратил', ?)", (500 - bal, core.now_iso()))
    wave1.tick()
    st = sql("select state, owed from w1_fb_bets where user_id=400", fetch=True)[0]
    check("не хватает баллов → забрали 500, долг 1500", st == ("debt", 1500) and h.bal() == 0, (st, h.bal()))
    give(400, 3000)
    wave1.tick()
    check("пополнил → долг закрыт", sql("select state, owed from w1_fb_bets where user_id=400", fetch=True)[0] == ("done", 0) and h.bal() == 1500)
    journal_ok(400, "долг фрибета")
    # выкуп ставки фрибетом
    fbk = fb_give(adm, 200, 8000)
    ev6 = manual_event("Выкуп — Фрибета", (2.0, None, 1.8))
    b0, t0 = b.bal(), last_tx()
    r = b.post("/api/v2/freebets/use", {"freebet_id": fbk, "type": "single", "event_id": ev6, "choice": "1"})
    kid = r.json()["id"]
    off = b.get(f"/api/cashout/offer?kind=single&id={kid}").json()["offer"]
    rr = b.post("/api/cashout", {"kind": "single", "id": kid, "expected": off})
    wave1.tick()
    check(f"выкуп фрибета: забираем min(жетон, выкуп) → итог {max(0, off - 8000)}", rr.status_code == 200 and b.bal() == b0 + max(0, off - 8000) + bonus(200, t0), (rr.text, b.bal() - b0, off))
    # обычный экспресс П1/X/П2 фрибетом
    E1, E2 = manual_event("Экс 1 — Фри", (2.0, None, 1.8)), manual_event("Экс 2 — Фри", (1.5, None, 2.6))
    fbe1 = fb_give(adm, 200, 6000)
    b0, t0 = b.bal(), last_tx()
    r = b.post("/api/v2/freebets/use", {"freebet_id": fbe1, "type": "express", "items": [{"event_id": E1, "choice": "1", "odds": 2.0}, {"event_id": E2, "choice": "1", "odds": 1.4}]})
    check("экспресс фрибетом: кэф изменился → 409, фрибет цел", r.status_code == 409 and any(f["id"] == fbe1 for f in b.get("/api/v2/freebets").json()["active"]) and b.bal() == b0, r.text)
    r = b.post("/api/v2/freebets/use", {"freebet_id": fbe1, "type": "express", "items": [{"event_id": E1, "choice": "1", "odds": 2.0}, {"event_id": E2, "choice": "1", "odds": 1.5}]})
    check("обычный экспресс фрибетом 6 000 (кф 3.00) принят, чистый выигрыш 12 000", r.status_code == 200 and r.json()["kind"] == "express" and r.json()["net_win"] == 12_000, r.text)
    exid = r.json()["id"]
    check("это именно экспресс игрока из 2 событий", sql("select user_id, amount from bets where id=?", (exid,), fetch=True) == [(200, 6000)]
          and sql("select count(*) from bet_events where bet_id=?", (exid,), fetch=True)[0][0] == 2)
    check("баланс не списан", b.bal() == b0 + bonus(200, t0))
    check("в истории экспресс помечен фрибетом", f"express:{exid}" in b.get("/api/v2/betcards/history").json()["freebets"])
    check("одно событие в экспрессе → 400", b.post("/api/v2/freebets/use", {"freebet_id": fb_give(adm, 200, 1000), "type": "express", "items": [{"event_id": E1, "choice": "1"}]}).status_code == 400)
    settle(adm, E1, "1"); settle(adm, E2, "1")
    wave1.tick()
    check("экспресс зашёл → только чистый выигрыш +12 000", b.bal() == b0 + 12_000 + bonus(200, t0), b.bal() - b0)
    E3, E4 = manual_event("Экс 3 — Фри", (2.0, None, 1.8)), manual_event("Экс 4 — Фри", (2.0, None, 1.8))
    fbe2 = fb_give(adm, 200, 3000)
    b0, t0 = b.bal(), last_tx()
    b.post("/api/v2/freebets/use", {"freebet_id": fbe2, "type": "express", "items": [{"event_id": E3, "choice": "1"}, {"event_id": E4, "choice": "2"}]})
    settle(adm, E3, "1"); settle(adm, E4, "1")
    wave1.tick()
    check("экспресс фрибетом проиграл → баланс не изменился", b.bal() == b0 + bonus(200, t0), b.bal() - b0)
    journal_ok(200, "фрибеты")

    print("\n== фрибеты в админке")
    sql("UPDATE users SET username='bob_w1' WHERE user_id=200")
    n0 = len(b.get("/api/v2/freebets").json()["active"])
    r = adm.post("/api/v2/freebets/admin/give", {"target": "@Bob_W1", "amount": 2500, "count": 3, "days": 3, "note": "За активность"})
    check("выдача по @username, 3 штуки на 3 дня", r.status_code == 200 and len(r.json()["ids"]) == 3 and r.json()["user_id"] == 200 and "3 × 2" in r.json()["message"], r.text)
    act = b.get("/api/v2/freebets").json()["active"]
    check("у игрока +3 фрибета с комментарием", len(act) == n0 + 3 and sum(1 for f in act if f["note"] == "За активность" and f["amount"] == 2500) == 3, act[:2])
    check("выдача по ID (поле target)", adm.post("/api/v2/freebets/admin/give", {"target": "200", "amount": 100}).status_code == 200)
    check("неизвестный игрок → 404", adm.post("/api/v2/freebets/admin/give", {"target": "@nobody_here", "amount": 100}).status_code == 404)
    check("без суммы → 400", adm.post("/api/v2/freebets/admin/give", {"target": "200"}).status_code == 400)
    lst = adm.get("/api/v2/freebets/admin/list").json()
    check("список выданных админом: новые сверху, с именем игрока", lst[0]["user_id"] == 200 and lst[0]["name"] == "Боб" and all(x["source"] == "admin" for x in lst), lst[:1])
    lp = adm.get("/api/v2/freebets/admin/list", params={"target": "@bob_w1"}).json()
    check("фрибеты конкретного игрока (все источники и статусы)", lp and all(x["user_id"] == 200 for x in lp) and {"used", "active"} <= {x["status"] for x in lp}, {x["status"] for x in lp})
    check("не админ: список и отмена → 403", b.get("/api/v2/freebets/admin/list").status_code == 403 and b.post("/api/v2/freebets/admin/revoke", {"id": r.json()["ids"][0]}).status_code == 403)
    rv = adm.post("/api/v2/freebets/admin/revoke", {"id": r.json()["ids"][0]})
    check("админ отменил неиспользованный фрибет", rv.status_code == 200 and all(f["id"] != r.json()["ids"][0] for f in b.get("/api/v2/freebets").json()["active"]))
    check("отменённым фрибетом поставить нельзя", b.post("/api/v2/freebets/use", {"freebet_id": r.json()["ids"][0], "type": "single", "event_id": manual_event("Отм — Фри", (2.0, None, 1.8)), "choice": "1"}).status_code == 400)
    check("повторная отмена / использованный → 400", adm.post("/api/v2/freebets/admin/revoke", {"id": r.json()["ids"][0]}).status_code == 400
          and adm.post("/api/v2/freebets/admin/revoke", {"id": fbe1}).status_code == 400)

    # ------------------------------------------------------------------
    print("\n== сезонный пропуск")
    p0 = g.get("/api/v2/pass").json()
    check("пропуск: 30 уровней, 0 очков, премиума нет", p0["max_level"] == 30 and len(p0["levels"]) == 30 and p0["points"] == 0 and not p0["premium"] and p0["claimable"] == 0, p0)
    check("награды: бесплатно ур.5 — фрибет 5 000, премиум ур.30 — 250 000", p0["levels"][4]["free"] == {"type": "freebet", "amount": 5000} and p0["levels"][29]["premium"]["amount"] == 250_000)
    check("забрать нечего → 400", g.post("/api/v2/pass/claim").status_code == 400)
    P = [manual_event(f"Пасс {i} — Матч {i}", (1.9, None, 1.9)) for i in range(3)]
    g.post("/api/predict", {"event_id": P[0], "points": 60_000, "choice": "1"})
    g.post("/api/predict", {"event_id": P[1], "points": 35_000, "choice": "1"})
    conn = core.ctx.get_db(); core.game_bet(conn, 300, 40_000, "dice"); conn.commit(); conn.close()
    seasonpass._cache.clear()
    p1 = g.get("/api/v2/pass").json()
    check("стоимость уровней: 50 000, 55 000 … 195 000 (всего 3 675 000)", [l["cost"] for l in p1["levels"][:3]] == [50_000, 55_000, 60_000]
          and p1["levels"][29]["cost"] == 195_000 and p1["levels"][29]["need"] == 3_675_000 and p1["total_need"] == 3_675_000, p1["levels"][:3])
    check("95 000 на матчи + 40 000 в играх (25% = 10 000) = 105 000 → ровно уровень 2", p1["points"] == 105_000 and p1["level"] == 2 and p1["claimable"] == 2
          and p1["into_level"] == 0 and p1["level_points"] == 60_000, (p1["points"], p1["level"], p1["into_level"], p1["level_points"]))
    fbp = fb_give(adm, 300, 50_000)
    g.post("/api/v2/freebets/use", {"freebet_id": fbp, "type": "single", "event_id": P[2], "choice": "1"})
    seasonpass._cache.clear()
    check("ставка фрибетом прогресс не даёт", g.get("/api/v2/pass").json()["points"] == 105_000)
    sql("INSERT INTO transactions (user_id, amount, balance_after, kind, note, created_at) VALUES (300, -500000, 0, 'bet', 'прошлый месяц', '2000-01-01T00:00:00+00:00')")
    seasonpass._cache.clear()
    check("ставки прошлых сезонов не считаются", g.get("/api/v2/pass").json()["points"] == 105_000)
    sql("DELETE FROM transactions WHERE note='прошлый месяц'")  # тестовая строка ломает порядок дат в журнале
    b0 = g.bal()
    r = g.post("/api/v2/pass/claim")
    check("забрал ур.1 и ур.2 бесплатно: 1 200 + 1 400", r.status_code == 200 and r.json()["pts"] == 2600 and g.bal() == b0 + 2600, r.text)
    check("повторно → 400", g.post("/api/v2/pass/claim").status_code == 400)
    r = g.post("/api/v2/pass/buy")
    check("купил премиум за 250 000", r.status_code == 200 and g.bal() == b0 + 2600 - seasonpass.PREMIUM_PRICE)
    check("второй раз купить нельзя", g.post("/api/v2/pass/buy").status_code == 400)
    r = g.post("/api/v2/pass/claim")
    check("после покупки — премиум-награды за пройденные уровни: 3 700 + 4 400", r.json()["pts"] == 8100 and all(x["track"] == "premium" for x in r.json()["rewards"]), r.text)
    conn = core.ctx.get_db(); core.adjust(conn, 300, -124_999, "bet", "тест: ставки"); conn.commit(); conn.close()
    seasonpass._cache.clear()
    p3 = g.get("/api/v2/pass").json()
    check("229 999 PTS — ещё уровень 3 (4-й стоит 65 000, не хватает 1)", p3["level"] == 3 and p3["into_level"] == 64_999 and p3["level_points"] == 65_000, (p3["level"], p3["into_level"]))
    conn = core.ctx.get_db(); core.adjust(conn, 300, -1, "bet", "тест: ставки"); conn.commit(); conn.close()
    seasonpass._cache.clear()
    r = g.post("/api/v2/pass/claim").json()
    kinds = {(x["level"], x["track"]): x["type"] for x in r["rewards"]}
    check("ур.3–4 (230 000 PTS): премиум ур.3 — фрибет 6 000", r["freebets"] == 6000 and kinds[(3, "premium")] == "freebet" and len(r["rewards"]) == 4, r)
    check("фрибет пропуска в списке, срок 14 дней", any(f["source"] == "pass" and f["amount"] == 6000 for f in g.get("/api/v2/freebets").json()["active"]))
    br = g.get("/api/v2/pass/brief").json()
    check("короткий статус для баннера", br["level"] == 4 and br["premium"] and br["claimable"] == 0, br)
    tot = seasonpass.totals()
    check(f"итого за сезон: бесплатно {tot['free']['pts']} PTS + фрибеты {tot['free']['freebet']}, премиум {tot['premium']['pts']} + {tot['premium']['freebet']}",
          tot["free"]["freebet"] == 105_000 and tot["premium"]["pts"] > seasonpass.PREMIUM_PRICE)
    # граница сезона в журнале — бинарный поиск
    ids = sql("select id, created_at from transactions order by id", fetch=True)
    mid = ids[len(ids) // 2]
    seasonpass._txid.clear()
    got = seasonpass.season_txid(core.ctx.get_db(), {"key": "t", "start": mid[1]})
    exp = max([i for i, ca in ids if ca < mid[1]] or [0])
    check(f"граница сезона: id {got} (ожидалось {exp})", got == exp)
    seasonpass._txid.clear()
    journal_ok(300, "пропуск")

    # ------------------------------------------------------------------
    print("\n== история: ставки на новые рынки, отметки, «Поделиться»")
    hs = b.get("/api/v2/betcards/history").json()
    kinds = {(x["kind"], x["id"]) for x in hs["bets"]}
    check("в истории Боба ставка на тотал и экспресс+", ("market", mb) in kinds and ("mx", mxf) in kinds, kinds)
    mrow = next(x for x in hs["bets"] if x["kind"] == "market" and x["id"] == mb)
    check("у ставки на рынок: название матча, подпись рынка, счёт", mrow["title"] == "Ливерпуль — Эвертон" and mrow["legs"][0]["label"].startswith("Тотал") and mrow["legs"][0]["score"] == "3:1", mrow)
    check("отметки фрибетов для всех видов ставок", f"single:{pid}" in hs["freebets"] and f"market:{mb}" in hs["freebets"] and hs["freebets"][f"single:{pid}"]["amount"] == 10_000)
    ha = a.get("/api/v2/betcards/history").json()
    check("отметки бонуса и страховки", ha["perks"].get(f"express:{x1}", {}).get("perk") == "boost" and ha["perks"].get(f"mx:{mx2}", {}).get("perk") == "insurance")
    mx2row = next(x for x in ha["bets"] if x["kind"] == "mx" and x["id"] == mx2)
    check("закрытый экспресс+: остальные события досчитаны (не «ожидает»)", [l["status"] for l in mx2row["legs"]] == ["lost", "won", "won", "won"], [l["status"] for l in mx2row["legs"]])
    check("чужие ставки не попадают", all(x["id"] != mx1 for x in hs["bets"] if x["kind"] == "mx"))
    # поделиться
    sh = a.post("/api/v2/betcards/share", {"kind": "express", "id": x1}).json()
    check("ссылка на свою ставку: t.me/<бот>?startapp=bet_…", sh["link"].startswith("https://t.me/") and "startapp=bet_e" in sh["link"] and len(sh["payload"]) <= 64, sh)
    check("на чужую ставку ссылку не дадут", b.post("/api/v2/betcards/share", {"kind": "express", "id": x1}).status_code == 404)
    sv = b.get("/api/v2/betcards/shared", params={"p": sh["payload"]}).json()
    check("Боб открыл ставку Алисы по ссылке", sv["kind"] == "express" and sv["id"] == x1 and sv["name"] == "Алиса" and not sv["me"] and sv["perk"]["perk"] == "boost", sv)
    bad = sh["payload"][:-1] + ("0" if sh["payload"][-1] != "0" else "1")
    check("подделанная подпись → 404", b.get("/api/v2/betcards/shared", params={"p": bad}).status_code == 404)
    check("перебор id с чужой подписью → 404", b.get("/api/v2/betcards/shared", params={"p": sh["payload"].replace(f"e{x1}_", f"e{x2}_")}).status_code == 404)
    # «поставить так же» по ссылке — на открытую ставку на доп. рынок
    m2 = espn_event("Бавария", "Дортмунд", (1.6, 4.2, 5.0), "shr")
    it2 = item(m2, a, "btts", "yes")
    r = a.post("/api/v2/markets/bet", {"event_id": m2, "market": "btts", "pick": "yes", "amount": 1000, "odds": it2["odds"]})
    amb = r.json()["id"]
    sql("UPDATE profiles SET hide_bets=1 WHERE user_id=100")
    p_m = a.post("/api/v2/betcards/share", {"kind": "market", "id": amb}).json()["payload"]
    sv = b.get("/api/v2/betcards/shared", params={"p": p_m}).json()
    check("ставка на рынок по ссылке: можно поставить так же", sv["kind"] == "market" and sv["copyable"] and sv["legs"][0]["event_id"] == m2, sv)
    b0, t0 = b.bal(), last_tx()
    r = b.post("/api/v2/betcards/shared/copy", {"p": p_m, "amount": 700})
    check("поставил так же (автор скрыл ставки, но сам поделился)", r.status_code == 200 and b.bal() == b0 - 700 + bonus(200, t0)
          and sql("select market, pick, amount from market_bets where user_id=200 order by id desc limit 1", fetch=True) == [("btts", "yes", 700)], r.text)
    check("свою ставку по ссылке — 400", a.post("/api/v2/betcards/shared/copy", {"p": p_m, "amount": 100}).status_code == 400)
    sql("UPDATE profiles SET hide_bets=0 WHERE user_id=100")
    p_x = a.post("/api/v2/betcards/share", {"kind": "mx", "id": mx1}).json()["payload"]
    check("закрытая ставка по ссылке: смотреть можно, ставить нельзя", not b.get("/api/v2/betcards/shared", params={"p": p_x}).json()["copyable"]
          and b.post("/api/v2/betcards/shared/copy", {"p": p_x, "amount": 100}).status_code == 400)
    check("лента ставок отдаёт event_id событий (для кнопки «Рынки матча»)", any(l.get("event_id") == m2 for x in b.get("/api/v2/betlog/feed").json() for l in x["legs"]))

    print("\n== «Лента ставок игроков»: ставки всех игроков всех видов")
    betcards._feed_cache.clear()
    fa = b.get("/api/v2/betcards/feed").json()["bets"]
    kinds_all = {x["kind"] for x in fa}
    check("в общей ленте все виды: одинары, экспрессы, рынки, экспрессы+", {"single", "express", "market", "mx"} <= kinds_all, kinds_all)
    check("в ленте и сыгранные ставки (с результатом), а не только открытые", any(x["status"] in ("won", "lost") for x in fa) and any(x["status"] == "pending" for x in fa))
    check("у каждой ставки автор: имя, уровень, uid", all(x.get("name") and x.get("level") and x.get("uid") for x in fa))
    fm = b.get("/api/v2/betcards/feed", params={"f": "markets"}).json()["bets"]
    check("фильтр «Тоталы, форы, счёт»: только рынки и экспрессы+ с рынками", fm and all(x["kind"] == "market" or (x["kind"] == "mx" and any(l["market"] != "1x2" for l in x["legs"])) for x in fm), {x["kind"] for x in fm})
    check("в фильтре рынков — тотал Боба на сыгранный матч (3:1)", any(x["kind"] == "market" and x["id"] == mb and x["legs"][0]["score"] == "3:1" for x in fm))
    check("свои ставки помечены mine (без кнопки «Скопировать»)", any(x["mine"] for x in fm) and all(x["mine"] == (x["uid"] == 200) for x in fm))
    check("фильтр П1/X/П2 — только одинары", all(x["kind"] == "single" for x in b.get("/api/v2/betcards/feed", params={"f": "single"}).json()["bets"]))
    check("фильтр экспрессов", all(x["kind"] in ("express", "mx") for x in b.get("/api/v2/betcards/feed", params={"f": "express"}).json()["bets"]))
    fc = b.get("/api/v2/betcards/feed", params={"f": "copy"}).json()["bets"]
    check("фильтр «Можно скопировать»: только ставки на несыгранные матчи", fc and all(x["copyable"] for x in fc))
    sql("UPDATE profiles SET hide_bets=1 WHERE user_id=200")
    betcards._feed_cache.clear()
    check("игрок скрыл ставки — их нет в ленте", all(x["uid"] != 200 for x in a.get("/api/v2/betcards/feed", params={"f": "markets"}).json()["bets"]))
    sql("UPDATE profiles SET hide_bets=0 WHERE user_id=200")
    betcards._feed_cache.clear()
    check("неизвестный фильтр → все", a.get("/api/v2/betcards/feed", params={"f": "zzz"}).json()["filter"] == "all")

    print("\n== фронтенд")
    js = c.get("/web/wave1.js").text
    check("wave1.js: история, карточка, фрибеты, пропуск, поделиться", all(x in js for x in ("/api/v2/betcards/history", "/api/v2/freebets/use", "/api/v2/pass/claim", "/api/v2/betcards/share", "/api/v2/betcards/shared", "/api/v2/betcards/feed", "/api/v2/freebets/admin/give", "type: 'express'")))
    idx = c.get("/").text
    check("index.html подключает wave1.js и wave1.css после plus.js", idx.find("/web/wave1.js") > idx.find("/web/plus.js") > 0 and "/web/wave1.css" in idx)
    check("старые эндпоинты живы", a.get("/api/history").status_code == 200 and a.get("/api/v2/betlog/feed").status_code == 200)

print(f"\nИтог: {OK} ✓, {FAIL} ✗")
sys.exit(1 if FAIL else 0)
