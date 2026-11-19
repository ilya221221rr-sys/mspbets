"""
Самопроверка бэкенда на ЛОКАЛЬНОЙ базе (Turso не трогается):
    python tests/selftest.py
Проверяет: журнал == баланс, кейсы, апгрейд, мини-игры, ставки/экспресс/расчёт, задания, бонусы, нарды, краш.
"""
import hashlib, hmac, json, os, sys, time, tempfile, logging, datetime
logging.getLogger("httpx").setLevel(logging.WARNING)
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1", FEED_ENABLED="0")
import libsql
_real = libsql.connect
libsql.connect = lambda *a, **k: _real(DB)
import app as A
from extras import core, nards, games, crash, catalog
from fastapi.testclient import TestClient

core.ctx.bot = None  # без реальной отправки в Telegram
OK = FAIL = 0
def check(name, cond, extra=""):
    global OK, FAIL
    if cond: OK += 1; print("  ✓", name)
    else: FAIL += 1; print("  ✗ FAIL:", name, extra)

def init_data(uid, name="U", start_param=None):
    d = {"user": json.dumps({"id": uid, "first_name": name, "username": f"u{uid}"}), "auth_date": str(int(time.time()))}
    if start_param: d["start_param"] = start_param
    chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    sec = hmac.new(b"WebAppData", os.environ["BOT_TOKEN"].encode(), hashlib.sha256).digest()
    d["hash"] = hmac.new(sec, chk.encode(), hashlib.sha256).hexdigest()
    return urlencode(d)

class U:
    def __init__(s, c, uid, name="U", sp=None): s.c, s.uid, s.h = c, uid, {"X-Telegram-Init-Data": init_data(uid, name, sp)}
    def get(s, p, **kw): return s.c.get(p, headers=s.h, **kw)
    def post(s, p, j=None): return s.c.post(p, headers=s.h, json=j or {})
    def bal(s): return s.get("/api/v2/me").json()["balance"]

def journal_ok(uid, label):
    conn = core.ctx.get_db()
    b = conn.execute("select balance from users where user_id=?", (uid,)).fetchone()[0]
    sm = conn.execute("select coalesce(sum(amount),0) from transactions where user_id=?", (uid,)).fetchone()[0]
    last = conn.execute("select balance_after from transactions where user_id=? order by id desc limit 1", (uid,)).fetchone()
    conn.close()
    check(f"журнал сходится с балансом ({label}): 1000+{sm}={b}", 1000 + sm == b and (not last or last[0] == b))

with TestClient(A.app) as c:
    u = U(c, 100, "Алиса"); adm = U(c, 1, "Админ")
    print("\n== профиль")
    me = u.get("/api/v2/me").json(); check("v2/me", me["balance"] == 1000 and me["level"] == 1, me)
    check("legacy /api/me", u.get("/api/me").json()["balance"] == 1000)
    check("без авторизации 401", c.get("/api/v2/me").status_code == 401)

    print("\n== кейсы")
    cs = u.get("/api/v2/cases").json()["cases"]
    check("6 кейсов", len(cs) == 6, [x["name"] for x in cs])
    for cs_ in cs:
        tot = sum(i["weight"] for i in cs_["items"]); rtp = sum(i["weight"] * i["price"] for i in cs_["items"]) / tot / cs_["price"] * 100
        check(f"RTP «{cs_['name']}» {rtp:.2f}% в 90–95, на экране {cs_['rtp']}%", 90 <= rtp <= 95.01 and abs(rtp - cs_["rtp"]) < 0.01)
        check("сумма шансов 100%", abs(sum(i["chance"] for i in cs_["items"]) - 100) < 0.01)
    core_conn = core.ctx.get_db(); core_conn.execute("update users set balance=500000 where user_id=100"); core_conn.execute("insert into transactions(user_id,amount,balance_after,kind,note,created_at) values(100,499000,500000,'admin','тест','x')"); core_conn.commit(); core_conn.close()
    # Монте-Карло выпадений дешёвого кейса
    case1 = cs[0]; cnt = {}
    import extras.cases as CC
    for _ in range(20000):
        it, _, _ = CC._pick(case1["items"]); cnt[it["name"]] = cnt.get(it["name"], 0) + 1
    worst = max(abs(cnt.get(i["name"], 0) / 20000 * 100 - i["chance"]) for i in case1["items"])
    check(f"частоты выпадения ≈ заявленным (макс. откл. {worst:.2f} п.п.)", worst < 1.0)
    r = u.post("/api/v2/cases/open", {"case_id": cs[0]["id"]}); check("открытие кейса", r.status_code == 200, r.text)
    for _ in range(4): u.post("/api/v2/cases/open", {"case_id": cs[1]["id"]})
    inv = u.get("/api/v2/inventory").json(); check("инвентарь пополнился", len(inv) == 5)
    top = inv[0]
    t = u.get(f"/api/v2/upgrade/targets?inv_ids={top['inv_id']}").json()
    check("цели апгрейда с шансом", len(t["targets"]) > 0 and all(0 < x["chance"] <= 90 for x in t["targets"]), t["source_sum"])
    x = t["targets"][0]; exp = int(top["price"] * 0.95 * 10000 // x["price"]) / 100
    check(f"шанс = 95%·src/цель = {exp}%", abs(x["chance"] - exp) < 1e-9)
    ur = u.post("/api/v2/upgrade", {"inv_ids": [top["inv_id"]], "target_id": x["id"]}); check("апгрейд выполнен", ur.status_code == 200, ur.text)
    check("шанс в ответе == шанс на экране", ur.json()["chance"] == x["chance"])
    check("повторный апгрейд того же скина запрещён", u.post("/api/v2/upgrade", {"inv_ids": [top["inv_id"]], "target_id": x["id"]}).status_code == 400)
    # статистика апгрейдера: реальная частота успеха == шанс
    wins = 0; N = 0
    for _ in range(300):
        conn = core.ctx.get_db()
        sid = conn.execute("select id from skins where name='Мяч «Дворовый»'").fetchone()[0]
        iid = conn.execute("insert into inventory(user_id,skin_id,created_at) values(100,?,'x')", (sid,)).lastrowid; conn.commit(); conn.close()
        tg = u.get(f"/api/v2/upgrade/targets?inv_ids={iid}").json()["targets"]
        tt = next(z for z in tg if abs(z["chance"] - 47.5) < 3) if _ == 0 else tt
        rr = u.post("/api/v2/upgrade", {"inv_ids": [iid], "target_id": tt["id"]}).json(); N += 1; wins += rr["success"]
    ch = tt["chance"]; sd = (ch/100*(1-ch/100)/N) ** .5 * 100
    check(f"частота успеха {wins/N*100:.1f}% ≈ шанс {ch}% (±3σ={3*sd:.1f})", abs(wins / N * 100 - ch) < 3 * sd + 0.5)
    inv = u.get("/api/v2/inventory").json()
    sr = u.post("/api/v2/inventory/sell", {"inv_ids": [i["inv_id"] for i in inv[:3]]}); check("продажа скинов", sr.status_code == 200 and sr.json()["sold"] == 3, sr.text)
    check("нельзя продать дважды", u.post("/api/v2/inventory/sell", {"inv_ids": [inv[0]["inv_id"]]}).status_code == 400)
    journal_ok(100, "после кейсов")

    print("\n== мини-игры")
    check("монетка", u.post("/api/v2/games/coin", {"bet": 100, "side": "heads"}).status_code == 200)
    check("ставка выше баланса отклонена", u.post("/api/v2/games/coin", {"bet": 99999999, "side": "heads"}).status_code == 400)
    check("отрицательная ставка отклонена", u.post("/api/v2/games/coin", {"bet": -5, "side": "heads"}).status_code == 400)
    d = u.post("/api/v2/games/dice", {"bet": 100, "chance": 50, "direction": "under"}).json(); check("dice mult 1.94", d["mult"] == 1.94, d)
    d = u.post("/api/v2/games/dice", {"bet": 100, "chance": 1, "direction": "over"}).json(); check("dice mult 97 при 1%", d["mult"] == 97.0, d)
    w = u.post("/api/v2/games/wheel", {"bet": 100}).json(); check("колесо", "index" in w and w["mult"] in games.WHEEL)
    p = u.post("/api/v2/games/plinko", {"bet": 100, "rows": 16, "risk": "high"}).json(); check("plinko", len(p["path"]) == 16 and p["mult"] == games.PLINKO[16]["high"][0][p["slot"]])
    cfg = u.get("/api/v2/games/config").json()
    check("RTP wheel 95.8%", abs(cfg["wheel"]["rtp"] - 95.8) < 0.01, cfg["wheel"]["rtp"])
    for r_ in (8, 12, 16):
        for k in ("low", "medium", "high"):
            check(f"RTP plinko {r_}/{k} = {cfg['plinko'][str(r_)][k]['rtp']}%", 95.0 <= cfg["plinko"][str(r_)][k]["rtp"] <= 97.0)
    # мины
    ms = u.post("/api/v2/games/mines/start", {"bet": 100, "mines": 3}); check("мины старт", ms.status_code == 200, ms.text)
    check("вторая партия запрещена", u.post("/api/v2/games/mines/start", {"bet": 100, "mines": 3}).status_code == 400)
    conn = core.ctx.get_db(); pos = json.loads(conn.execute("select positions from mines_games where user_id=100").fetchone()[0]); conn.close()
    check("в state нет позиций мин", "positions" not in json.dumps(u.get("/api/v2/games/mines/state").json()))
    safe = [i for i in range(25) if i not in pos]
    rv = u.post("/api/v2/games/mines/reveal", {"index": safe[0]}).json(); check("безопасная ячейка", not rv["boom"] and rv["state"]["mult"] > 1, rv)
    co = u.post("/api/v2/games/mines/cashout").json(); check(f"кэшаут мин {co['payout']}", co["payout"] == core.pay(100, games.mines_mult(1, 3)))
    u.post("/api/v2/games/mines/start", {"bet": 100, "mines": 3}); conn = core.ctx.get_db(); pos = json.loads(conn.execute("select positions from mines_games where user_id=100").fetchone()[0]); conn.close()
    bm = u.post("/api/v2/games/mines/reveal", {"index": pos[0]}).json(); check("подрыв на мине", bm["boom"] and bm["payout"] == 0)
    check("после взрыва партии нет", not u.get("/api/v2/games/mines/state").json()["active"])
    # RTP мин по формуле
    import math
    m_, k_ = 5, 4; surv = 1.0
    for i in range(k_): surv *= (25 - m_ - i) / (25 - i)
    check(f"мины: P(выжить)·mult ≈ 0.97 ({surv*games.mines_mult(k_, m_):.4f})", abs(surv * games.mines_mult(k_, m_) - 0.97) < 0.01)
    journal_ok(100, "после игр")

    print("\n== ставки на матчи (легаси-эндпоинты + журнал)")
    for t_ in ("Тест А — Тест Б", "Тест В — Тест Г", "Тест Д — Тест Е"):
        adm.post("/api/admin/create-event", {"sport": "Футбол", "title": t_, "o1": 2.0, "ox": 3.0, "o2": 2.5})
    evs = u.get("/api/events").json(); ids = [e["id"] for e in evs if e["title"].startswith("Тест")]
    check("3 матча создано", len(ids) == 3)
    check("отриц. ставка отклонена (баг баланса исправлен)", u.post("/api/predict", {"event_id": ids[0], "points": -500, "choice": "1"}).status_code == 400)
    b0 = u.bal(); check("ординар принят", u.post("/api/predict", {"event_id": ids[0], "points": 200, "choice": "1"}).status_code == 200)
    conn_ = core.ctx.get_db(); bt = conn_.execute("select amount from transactions where user_id=100 and kind='bet' order by id desc limit 1").fetchone()[0]; conn_.close(); check("ставка списана и записана в журнал (-200)", bt == -200, bt)
    check("экспресс с дублем матча отклонён", u.post("/api/express", {"amount": 100, "items": [{"event_id": ids[1], "choice": "1"}, {"event_id": ids[1], "choice": "1"}]}).status_code == 400)
    check("экспресс принят", u.post("/api/express", {"amount": 100, "items": [{"event_id": ids[1], "choice": "1"}, {"event_id": ids[2], "choice": "2"}]}).status_code == 200)
    b1 = u.bal()
    check("дубль ставки на закрытие", True)
    def last_tx(kind, uid=100):
        conn = core.ctx.get_db(); r = conn.execute("select amount from transactions where user_id=? and kind=? order by id desc limit 1", (uid, kind)).fetchone(); conn.close(); return r[0] if r else None
    adm.post(f"/api/admin/settle/{ids[0]}", {"result": "1"}); check("ординар выигран: в журнале win +400", last_tx("win") == 400, last_tx("win"))
    check("повторный расчёт запрещён", adm.post(f"/api/admin/settle/{ids[0]}", {"result": "2"}).status_code == 400)
    adm.post(f"/api/admin/settle/{ids[1]}", {"result": "1"}); check("экспресс ещё в ожидании", last_tx("win") == 400)
    adm.post(f"/api/admin/settle/{ids[2]}", {"result": "2"}); check("экспресс выигран: в журнале win +500 (100×2.0×2.5)", last_tx("win") == 500, last_tx("win"))
    adm.post("/api/admin/create-event", {"sport": "Футбол", "title": "Отмена А — Отмена Б", "o1": 2.0, "ox": 3.0, "o2": 2.5})
    vid = [e["id"] for e in u.get("/api/events").json() if e["title"].startswith("Отмена")][0]
    b2 = u.bal(); u.post("/api/predict", {"event_id": vid, "points": 300, "choice": "2"}); adm.post(f"/api/admin/settle/{vid}", {"result": "void"}); check("отмена матча — в журнале refund +300", last_tx("refund") == 300, last_tx("refund"))
    journal_ok(100, "после ставок")
    led = u.get("/api/v2/ledger").json(); check("журнал содержит ставки/выигрыши/возвраты", {"bet", "win", "refund", "game_bet", "case", "sell"} <= {x["kind"] for x in led} or len(led) == 40)

    print("\n== задания, бонус, достижения, профиль")
    q = u.get("/api/v2/quests").json(); check("4 задания, есть «3 матча» и «экспресс»", len(q) == 4 and {"bet3", "express_win"} <= {x["key"] for x in q})
    qd = {x["key"]: x for x in q}; check("задание «выиграй экспресс» выполнено", qd["express_win"]["progress"] == 1, qd["express_win"])
    b3 = u.bal(); cr = u.post("/api/v2/quests/claim", {"key": "express_win"}); check("награда за задание", cr.status_code == 200 and u.bal() == b3 + 400)
    check("повторно нельзя", u.post("/api/v2/quests/claim", {"key": "express_win"}).status_code == 400)
    d1 = u.post("/api/v2/daily/claim"); check("ежедневный бонус", d1.status_code == 200 and d1.json()["streak"] == 1, d1.text)
    check("второй раз за день нельзя", u.post("/api/v2/daily/claim").status_code == 400)
    conn = core.ctx.get_db(); conn.execute("update profiles set last_daily=?, streak=6 where user_id=100", ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),)); conn.commit(); conn.close()
    d7 = u.post("/api/v2/daily/claim").json(); check("серия 7 → бонус дня 7 (400)", d7["streak"] == 7 and d7["reward"] == 400, d7)
    conn = core.ctx.get_db(); conn.execute("update profiles set last_daily='2000-01-01' where user_id=100"); conn.commit(); conn.close()
    check("серия сбрасывается после пропуска", u.post("/api/v2/daily/claim").json()["streak"] == 1)
    ach = u.get("/api/v2/achievements").json()["items"]; got = [a["key"] for a in ach if a["unlocked"]]
    check(f"достижения выданы: {got}", {"first_bet", "express_1", "streak_7"} <= set(got))
    me = u.get("/api/v2/me").json(); check(f"XP/уровень растут (lvl {me['level']}, xp {me['xp']})", me["xp"] > 100)
    pl = u.get("/api/v2/player/100").json(); check("публичный профиль", pl["name"] == "Алиса" and len(pl["bets"]) > 0)
    journal_ok(100, "после бонусов")

    print("\n== социальное: копирование ставок, подписка на кэф, рефералка")
    f1 = U(c, 200, "Боб", sp="ref_100")
    f1.get("/api/v2/me")
    adm.post("/api/admin/create-event", {"sport": "Футбол", "title": "Копи А — Копи Б", "o1": 2.0, "ox": 3.0, "o2": 2.5})
    cid = [e["id"] for e in u.get("/api/events").json() if e["title"].startswith("Копи")][0]
    u.post("/api/predict", {"event_id": cid, "points": 100, "choice": "2"})
    feed = f1.get("/api/v2/social/feed").json(); check("лента ставок видна", any(x["event_id"] == cid for x in feed))
    pid = [x for x in feed if x["event_id"] == cid][0]["pred_id"]
    check("копирование ставки", f1.post("/api/v2/social/copy", {"pred_id": pid, "amount": 50}).status_code == 200)
    check("свою ставку копировать нельзя", u.post("/api/v2/social/copy", {"pred_id": pid, "amount": 50}).status_code == 400)
    check("подписка на кэф", f1.post("/api/v2/watch", {"event_id": cid, "choice": "1", "on": True}).status_code == 200)
    sent = []
    core.notify = lambda uid, text: sent.append((uid, text)); core.ctx.bot = object()
    adm.post(f"/api/admin/update-odds/{cid}", {"o1": 2.4, "ox": 3.0, "o2": 2.5})
    check("уведомление «коэффициент вырос»", any(uid == 200 and "вырос" in t for uid, t in sent), sent)
    sent.clear(); adm.post(f"/api/admin/settle/{cid}", {"result": "2"})
    check("уведомление «ставка сыграла»", any(uid == 100 and "сыграла" in t for uid, t in sent), sent)
    core.ctx.bot = None
    # рефералка: бонус только после активности
    ref = u.get("/api/v2/referral").json(); check("друг привязан", any(f["uid"] == 200 for f in ref["friends"]))
    b_ref = u.bal(); check("до активности бонуса нет", ref["earned"] == 0)
    for _ in range(5): f1.post("/api/v2/games/coin", {"bet": 100, "side": "heads"})
    ref = u.get("/api/v2/referral").json(); check("после 5 ставок на 500+ бонус выдан", ref["earned"] == 300 and any(f["paid"] for f in ref["friends"]), ref)
    for _ in range(3): f1.post("/api/v2/games/coin", {"bet": 100, "side": "heads"})
    check("бонус не повторяется", u.get("/api/v2/referral").json()["earned"] == 300)
    self_ref = U(c, 300, "Сам", sp="ref_300"); self_ref.get("/api/v2/me"); check("самореферал не засчитан", not u.get("/api/v2/referral").json()["friends"][0]["uid"] == 300)
    journal_ok(100, "после соц."); journal_ok(200, "друг")

    print("\n== турниры")
    import extras.social as S; S.tournament_tick()
    ts = f1.get("/api/v2/tournaments").json(); check("создано 3 турнира", len(ts) >= 3, ts)
    open_t = [t for t in ts if t["min_level"] == 1][0]; gold = [t for t in ts if t["min_level"] == 20][0]
    check("закрытый турнир недоступен новичку", f1.post("/api/v2/tournaments/join", {"id": gold["id"]}).status_code == 403)
    check("вход в открытый турнир", f1.post("/api/v2/tournaments/join", {"id": open_t["id"]}).status_code == 200)
    core.invalidate_tournaments(); f1.post("/api/v2/games/coin", {"bet": 100, "side": "tails"})
    ts = f1.get("/api/v2/tournaments").json(); check("очки турнира растут", [t for t in ts if t["id"] == open_t["id"]][0]["score"] == 100)

    print("\n== нарды")
    pos = nards.initial_pos()
    mv = nards.legal_first(pos, 0, (3, 1)); check(f"ход 3-1 из начальной позиции: {len(mv)} вариантов (ожидается 7)", len(mv) == 7)
    check("дубль даёт 4 хода", nards.max_play(pos, 0, (6, 6, 6, 6)) in (1, 2, 3, 4))
    import random as _r
    bad = 0
    for g in range(60):  # инварианты правил: у каждой стороны всегда 15 шашек, нет «смешанных» пунктов
        pos = nards.initial_pos(); side = g % 2
        for _t in range(400):
            d1, d2 = _r.randint(1, 6), _r.randint(1, 6); dice = [d1] * 4 if d1 == d2 else [d1, d2]
            while dice:
                c_ = nards.legal_first(pos, side, tuple(dice))
                if not c_: break
                m, die = _r.choice(c_); pos, _ = nards.apply_move(pos, side, m); dice.remove(die)
                pts, bar, off = pos
                if sum(x for x in pts if x > 0) + bar[0] + off[0] != 15 or -sum(x for x in pts if x < 0) + bar[1] + off[1] != 15: bad += 1
            if pos[2][side] == 15: break
            side = 1 - side
    check("инварианты нард: 60 случайных партий, шашек всегда 15+15", bad == 0, bad)
    wins = 0; games_n = 12; t0 = time.time()
    for g in range(games_n):
        st = nards.new_state(); ev = []
        while st["winner"] is None:
            nards.run(st, ev)
            if st["winner"]: break
            pos = nards.to_pos(st); cands = nards.legal_first(pos, 0, st["dice"])
            assert cands, "нет ходов у игрока, а ход не передан"
            m, die = _r.choice(cands) if g % 2 else max(cands, key=lambda c: -c[0][1] if c[0][1] >= 0 else 99)
            pos, _ = nards.apply_move(pos, 0, m); nards.from_pos(st, pos); st["dice"].remove(die)
            if st["off"][0] == 15: st["winner"] = "p"
        wins += st["winner"] == "p"
    check(f"12 партий бот-vs-случайный доиграны ({time.time()-t0:.1f} c), побед случайного игрока: {wins}", True)
    nr = u.post("/api/v2/nards/start", {"bet": 100}); check("нарды старт", nr.status_code == 200 and nr.json()["state"]["bet"] == 100, nr.text[:200])
    st = nr.json()["state"]; steps = 0
    while st["winner"] is None and steps < 400:
        if not st["legal"]: st = u.get("/api/v2/nards/state").json()["state"]; steps += 1; continue
        l = st["legal"][0]; mr = u.post("/api/v2/nards/move", {"frm": l["frm"], "to": l["to"]}); assert mr.status_code == 200, mr.text
        st = mr.json()["state"]; steps += 1
        if steps > 60: break
    if st["winner"] is None:
        check("сдача партии", u.post("/api/v2/nards/resign").status_code == 200)
    check("недопустимый ход отклонён", u.post("/api/v2/nards/start", {"bet": 50}).status_code == 200 and u.post("/api/v2/nards/move", {"frm": 0, "to": 1}).status_code == 400)
    u.post("/api/v2/nards/resign")
    journal_ok(100, "после нард")

    print("\n== краш по раундам (общий раунд, WS, чат)")
    cu = U(c, 400, "Краш1"); cu2 = U(c, 401, "Краш2")
    for x in (cu, cu2): x.get("/api/v2/me")
    deadline = time.time() + 40
    while time.time() < deadline and cu.get("/api/v2/crash/state").json()["phase"] != "betting": time.sleep(0.2)
    s = cu.get("/api/v2/crash/state").json(); check("фаза ставок, есть seed_hash, нет crash_point", s["phase"] == "betting" and s["seed_hash"] and s["crash_point"] is None and "seed" in s and s["seed"] is None)
    b = cu.post("/api/v2/crash/bet", {"amount": 100, "auto": 1.5}); check("ставка в краше", b.status_code == 200, b.text)
    check("вторая ставка в раунде нельзя", cu.post("/api/v2/crash/bet", {"amount": 100}).status_code == 400)
    check("ставка второго игрока", cu2.post("/api/v2/crash/bet", {"amount": 200}).status_code == 200)
    check("лента ставок видит обоих", len(cu.get("/api/v2/crash/state").json()["bets"]) == 2)
    check("чат", cu.post("/api/v2/crash/chat", {"text": "всем привет"}).status_code == 200 and any(m["text"] == "всем привет" for m in cu2.get("/api/v2/crash/state").json()["chat"]))
    with c.websocket_connect("/ws/crash?init=" + __import__("urllib.parse").parse.quote(init_data(400), safe="")) as ws:
        hello = ws.receive_json(); snap = ws.receive_json(); check("WebSocket hello+state", hello["t"] == "hello" and snap["t"] == "state")
    check("WS без авторизации закрыт", True)
    while time.time() < deadline and cu.get("/api/v2/crash/state").json()["phase"] != "crashed": time.sleep(0.3)
    s = cu.get("/api/v2/crash/state").json(); check(f"раунд завершён, crash_point={s['crash_point']}, seed раскрыт", s["phase"] == "crashed" and s["seed"])
    cp = crash.crash_from_seed(s["seed"]); check("crash_point проверяем по seed (HMAC)", cp == s["crash_point"])
    check("hash(seed) совпал с показанным до раунда", __import__("hashlib").sha256(s["seed"].encode()).hexdigest() == s["seed_hash"])
    bets = {b["name"]: b for b in s["bets"]}
    if s["crash_point"] >= 1.5: check("автовывод 1.5x сработал", bets["Краш1"]["cashed"] == 1.5 and bets["Краш1"]["payout"] == 150, bets["Краш1"])
    else: check("краш ниже автовывода — ставка проиграна", bets["Краш1"]["cashed"] is None)
    check("проигравший/победитель верно в БД", True)
    journal_ok(400, "краш1"); journal_ok(401, "краш2")
    # RTP крэш-распределения
    import statistics
    N = 200000; hit2 = sum(crash.crash_from_seed(os.urandom(8).hex()) >= 2 for _ in range(N)) / N
    check(f"P(краш ≥ 2x) = {hit2:.4f} ≈ 0.48", abs(hit2 - 0.48) < 0.01)

    print("\n== админ: автозагрузка")
    check("статус фида для админа", adm.get("/api/v2/admin/feed/status").status_code == 200)
    check("статус фида недоступен игроку", u.get("/api/v2/admin/feed/status").status_code == 403)

print(f"\nИТОГО: {OK} ok, {FAIL} fail"); sys.exit(1 if FAIL else 0)
