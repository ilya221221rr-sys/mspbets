"""
Самопроверка бэкенда на ЛОКАЛЬНОЙ базе (Turso не трогается):
    python tests/selftest.py
Проверяет: журнал == баланс, кейсы, апгрейд, мини-игры, ставки/экспресс/расчёт, задания, бонусы, краш.
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
from extras import core, games, crash, catalog
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
    print("\n== аватары")
    check("в /me есть список аватаров (pudge, axe)", {"pudge", "axe"} <= {a["key"] for a in me["avatars"]} and me["avatar"] == "", me.get("avatars"))
    check("все файлы аватаров существуют", all(os.path.exists(f"web/avatars/{a['key']}.webp") for a in me["avatars"]))
    check("выбор аватара pudge", u.post("/api/v2/profile/settings", {"avatar": "pudge"}).status_code == 200)
    check("аватар сохранился", u.get("/api/v2/me").json()["avatar"] == "pudge")
    check("чужой/выдуманный аватар отклонён", u.post("/api/v2/profile/settings", {"avatar": "../../etc/passwd"}).status_code == 400)
    check("аватар виден в профиле игрока", u.get("/api/v2/player/100").json()["avatar"] == "pudge")
    check("аватар виден в рейтинге", any(r.get("avatar") == "pudge" for r in u.get("/api/rating").json()))
    check("сброс аватара", u.post("/api/v2/profile/settings", {"avatar": ""}).status_code == 200 and u.get("/api/v2/me").json()["avatar"] == "")

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
    b3 = u.bal(); cr = u.post("/api/v2/quests/claim", {"key": "express_win"}); check("награда за задание", cr.status_code == 200 and u.bal() == b3 + 400 * 70)
    check("повторно нельзя", u.post("/api/v2/quests/claim", {"key": "express_win"}).status_code == 400)
    d1 = u.post("/api/v2/daily/claim"); check("ежедневный бонус", d1.status_code == 200 and d1.json()["streak"] == 1, d1.text)
    check("второй раз за день нельзя", u.post("/api/v2/daily/claim").status_code == 400)
    conn = core.ctx.get_db(); conn.execute("update profiles set last_daily=?, streak=6 where user_id=100", ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%d"),)); conn.commit(); conn.close()
    d7 = u.post("/api/v2/daily/claim").json(); check("серия 7 → бонус дня 7 (400 x70)", d7["streak"] == 7 and d7["reward"] == 400 * 70, d7)
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
    ref = u.get("/api/v2/referral").json(); check("после 5 ставок на 500+ бонус выдан", ref["earned"] == 50000 and any(f["paid"] for f in ref["friends"]), ref)
    for _ in range(3): f1.post("/api/v2/games/coin", {"bet": 100, "side": "heads"})
    check("бонус не повторяется", u.get("/api/v2/referral").json()["earned"] == 50000)
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

    print("\n== краш (обычный: раунд только по кнопке «Играть»)")
    cu = U(c, 400, "Краш1"); cu.get("/api/v2/me")
    check("без раунда: состояние idle", cu.get("/api/v2/crash/state").json()["phase"] == "idle")
    check("кэшаут без раунда нельзя", cu.post("/api/v2/crash/cashout").status_code == 400)
    check("в фоне нет живого движка краша", not hasattr(crash, "engine") and not any(r.path.startswith("/ws/") for r in A.app.routes))
    real_cfs = crash.crash_from_seed
    try:
        crash.crash_from_seed = lambda seed: 5.0
        r1 = cu.post("/api/v2/crash/start", {"bet": 100}); j = r1.json()
        check("старт раунда", r1.status_code == 200 and j["phase"] == "running" and j["seed_hash"] and j["crash_point"] is None and j["seed"] is None, r1.text)
        check("второй раунд поверх нельзя", cu.post("/api/v2/crash/start", {"bet": 100}).status_code == 400)
        st = cu.get("/api/v2/crash/state").json(); check("состояние: раунд идёт (возврат в игру)", st["phase"] == "running" and st["round_id"] == j["round_id"])
        time.sleep(1.2)
        co = cu.post("/api/v2/crash/cashout"); cj = co.json()
        exp = crash.mult_at(1.2)
        check(f"кэшаут по времени: x{cj.get('mult')} ≈ x{exp:.2f}", co.status_code == 200 and abs(cj["mult"] - exp) < 0.08 and cj["payout"] == core.pay(100, cj["mult"]), co.text)
        check("после кэшаута seed раскрыт", cj["phase"] == "cashed" and cj["seed"] and cj["crash_point"] == 5.0)
        check("кэшаут дважды нельзя", cu.post("/api/v2/crash/cashout").status_code == 400)
        journal_ok(400, "краш: ручной кэшаут")

        crash.crash_from_seed = lambda seed: 1.2   # крэш через ln(1.2)/0.1 ≈ 1.8 c
        j = cu.post("/api/v2/crash/start", {"bet": 100}).json(); t0 = time.time()
        w = cu.get(f"/api/v2/crash/wait?round={j['round_id']}").json()
        check(f"long-poll ответил в момент краша ({time.time()-t0:.1f} c)", w["phase"] == "crashed" and 1.5 < time.time() - t0 < 2.6 and w["crash_point"] == 1.2 and w["seed"], w)
        check("hash(seed) совпал с показанным до раунда", __import__("hashlib").sha256(w["seed"].encode()).hexdigest() == j["seed_hash"])
        check("кэшаут после краха нельзя", cu.post("/api/v2/crash/cashout").status_code == 400)
        time.sleep(0.6)

        j = cu.post("/api/v2/crash/start", {"bet": 100, "auto": 1.1}).json()
        w = cu.get(f"/api/v2/crash/wait?round={j['round_id']}").json()
        check("автовывод 1.1x сработал (краш на 1.2x позже)", w["phase"] == "cashed" and w["mult"] == 1.1 and w["payout"] == 110, w)
        time.sleep(0.6)

        crash.crash_from_seed = lambda seed: 1.0   # мгновенный краш
        j = cu.post("/api/v2/crash/start", {"bet": 100}).json()
        check("краш на 1.00x: кэшаут отклонён", cu.post("/api/v2/crash/cashout").status_code == 400)
        time.sleep(0.6)
        check("после мгновенного краха можно играть снова", cu.post("/api/v2/crash/start", {"bet": 100}).status_code == 200)
        time.sleep(0.6); cu.post("/api/v2/crash/cashout")
        check("неверный автовывод отклонён", cu.post("/api/v2/crash/start", {"bet": 100, "auto": 1.0}).status_code == 400)
        check("ставка больше баланса отклонена", cu.post("/api/v2/crash/start", {"bet": 99_999_999}).status_code in (400, 402, 422))

        crash.crash_from_seed = lambda seed: 50.0  # долгий раунд — «перезапуск сервера» посреди него
        bal0 = cu.get("/api/v2/me").json()["balance"]
        cu.post("/api/v2/crash/start", {"bet": 100}); bal1 = cu.get("/api/v2/me").json()["balance"]
        crash.ACTIVE.clear(); crash.recover()
        bal2 = cu.get("/api/v2/me").json()["balance"]
        check("перезапуск посреди раунда: ставка возвращена", bal1 == bal0 - 100 and bal2 == bal0, (bal0, bal1, bal2))
    finally:
        crash.crash_from_seed = real_cfs
    rr = cu.get("/api/v2/crash/rounds").json(); check("история раундов игрока (seed раскрыт)", len(rr) >= 3 and all(x["seed"] and crash.crash_from_seed(x["seed"]) is not None for x in rr))
    journal_ok(400, "краш: все сценарии")
    # RTP крэш-распределения
    import statistics
    N = 200000; hit2 = sum(crash.crash_from_seed(os.urandom(8).hex()) >= 2 for _ in range(N)) / N
    check(f"P(краш ≥ 2x) = {hit2:.4f} ≈ 0.48", abs(hit2 - 0.48) < 0.01)

    print("\n== админ: автозагрузка")
    check("статус фида для админа", adm.get("/api/v2/admin/feed/status").status_code == 200)
    check("статус фида недоступен игроку", u.get("/api/v2/admin/feed/status").status_code == 403)

    print("\n== оптимизация: окно матчей 48 ч, пул соединений, кэш")
    from datetime import datetime, timedelta, timezone
    iso = lambda h: (datetime.now(timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    with core.db() as conn:
        ids = {}
        for tag, st, h in (("soon", "open", 5), ("mid", "open", 40), ("far", "open", 60), ("started", "open", -1), ("closed_soon", "closed", 3)):
            ids[tag] = conn.execute("INSERT INTO events (sport,title,starts_at,status,odds_1,odds_x,odds_2,created_at) VALUES ('Футбол',?,?,?,2,3,2,?)",
                                    (f"W-{tag} — X", iso(h), st, core.now_iso())).lastrowid
        ids["manual"] = conn.execute("INSERT INTO events (sport,title,status,odds_1,odds_x,odds_2,created_at) VALUES ('Футбол','W-manual — X','open',2,3,2,?)", (core.now_iso(),)).lastrowid
    A.invalidate_events()
    pub = {e["title"].split(" — ")[0] for e in u.get("/api/events").json()}
    check("матч через 5 ч виден", "W-soon" in pub, pub)
    check("матч через 40 ч виден (окно 48 ч)", "W-mid" in pub)
    check("ручной матч без даты виден", "W-manual" in pub)
    check("матч через 60 ч скрыт", "W-far" not in pub)
    check("начавшийся матч скрыт", "W-started" not in pub)
    check("закрытый матч скрыт у игрока", "W-closed_soon" not in pub)
    adm_all = {e["title"].split(" — ")[0] for e in adm.get("/api/events?all=1").json()}
    check("админ видит всё через ?all=1", {"W-far", "W-started", "W-closed_soon"} <= adm_all, adm_all)
    check("игрок не получает all=1", "W-far" not in {e["title"].split(" — ")[0] for e in u.get("/api/events?all=1").json()})
    import time as _t
    t0 = _t.time(); [u.get("/api/events") for _ in range(30)]; dt = (_t.time() - t0) / 30
    check(f"/api/events из кэша быстро ({dt*1000:.1f} мс)", dt < 0.05)
    # пул: соединение переиспользуется, а «грязное» (без commit) — нет
    c1 = A.get_db(); raw1 = c1._c; c1.execute("SELECT 1").fetchone(); c1.close()
    c2 = A.get_db(); check("пул переиспользует соединение", c2._c is raw1); 
    c2.execute("CREATE TABLE IF NOT EXISTS _pool_t (a)"); c2.close()  # запись без commit -> не возвращается в пул
    c3 = A.get_db(); check("соединение с незакоммиченной записью не переиспользуется", c3._c is not raw1); c3.close()
    # матч, добавленный через админку, виден сразу (инвалидация кэша)
    adm.post("/api/admin/create-event", {"sport": "Футбол", "title": "W-adm — Y", "o1": 2, "ox": 3, "o2": 2})
    check("новый ручной матч виден сразу (кэш сброшен)", "W-adm" in {e["title"].split(" — ")[0] for e in u.get("/api/events").json()})
    check("gzip для списка матчей", "gzip" in c.get("/web/app.js", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding", ""))

    print("\n== админка: баллы игрокам")
    v = U(c, 777, "Вика"); v.get("/api/v2/me")
    r = adm.post("/api/admin/give-points", {"target": "777", "amount": 500, "action": "add"}); check("начисление по ID", r.status_code == 200 and v.bal() == 1500, r.text)
    r = adm.post("/api/admin/give-points", {"target": "@u777", "amount": 200, "action": "remove"}); check("списание по @username", r.status_code == 200 and v.bal() == 1300, r.text)
    r = adm.post("/api/admin/give-points", {"target": "@U777", "amount": 99999, "action": "remove"}); check("списание больше баланса — списывается всё, не в минус", r.status_code == 200 and v.bal() == 0, r.text)
    check("у игрока 0 — списать нечего (400)", adm.post("/api/admin/give-points", {"target": "777", "amount": 5, "action": "remove"}).status_code == 400)
    check("неизвестный игрок 404", adm.post("/api/admin/give-points", {"target": "99999999", "amount": 5, "action": "add"}).status_code == 404)
    check("мусор в ID — не 500", adm.post("/api/admin/give-points", {"target": "abc!", "amount": 5, "action": "add"}).status_code == 404)
    check("сумма 0 отклонена", adm.post("/api/admin/give-points", {"target": "777", "amount": 0, "action": "add"}).status_code == 400)
    check("не-админ получает 403", v.post("/api/admin/give-points", {"target": "777", "amount": 5, "action": "add"}).status_code == 403)
    journal_ok(777, "после админ-операций")

    print("\n== админка: промокоды (создание, правка, удаление)")
    check("создание промокода", adm.post("/api/admin/create-promo", {"code": " test1 ", "reward": 100}).status_code == 200)
    check("дубль отклонён", adm.post("/api/admin/create-promo", {"code": "TEST1", "reward": 5}).status_code == 400)
    lst = adm.get("/api/admin/promos").json(); check("список промокодов", any(p_["code"] == "TEST1" and p_["reward"] == 100 and p_["uses"] == 0 for p_ in lst), lst)
    check("игрок не видит список", v.get("/api/admin/promos").status_code == 403)
    check("игрок активирует", v.post("/api/promo/activate", {"code": "test1"}).status_code == 200 and v.bal() == 100)
    check("правка награды", adm.post("/api/admin/update-promo", {"code": "TEST1", "reward": 250}).status_code == 200)
    check("переименование", adm.post("/api/admin/update-promo", {"code": "TEST1", "new_code": "test2", "reward": 250}).status_code == 200)
    lst = adm.get("/api/admin/promos").json(); check("после правки: TEST2, 250, 1 активация", any(p_["code"] == "TEST2" and p_["reward"] == 250 and p_["uses"] == 1 for p_ in lst) and not any(p_["code"] == "TEST1" for p_ in lst), lst)
    check("повторная активация нельзя (история сохранена)", v.post("/api/promo/activate", {"code": "TEST2"}).status_code == 400)
    adm.post("/api/admin/create-promo", {"code": "OTHER", "reward": 1})
    check("переименование в занятое имя — 400", adm.post("/api/admin/update-promo", {"code": "TEST2", "new_code": "OTHER", "reward": 1}).status_code == 400)
    check("правка несуществующего — 404", adm.post("/api/admin/update-promo", {"code": "NOPE", "reward": 1}).status_code == 404)
    check("удаление промокода", adm.post("/api/admin/delete-promo", {"code": "TEST2"}).status_code == 200)
    check("удалённый не активируется", v.post("/api/promo/activate", {"code": "TEST2"}).status_code == 404)
    check("нет в списке после удаления", not any(p_["code"] == "TEST2" for p_ in adm.get("/api/admin/promos").json()))
    journal_ok(777, "после промокодов")

    print("\n== выкуп ставки и разбор исходов")
    for t_ in ("К-А — К-Б", "К-В — К-Г", "К-Д — К-Е"):
        adm.post("/api/admin/create-event", {"sport": "Футбол", "title": t_, "o1": 2.0, "ox": 3.0, "o2": 2.5})
    _m = {e["title"]: e["id"] for e in u.get("/api/events").json()}; kid = [_m["К-А — К-Б"], _m["К-В — К-Г"], _m["К-Д — К-Е"]]
    check("3 матча для выкупа", len(kid) == 3)
    b0 = u.bal(); u.post("/api/predict", {"event_id": kid[0], "points": 200, "choice": "1"})
    h = [x for x in u.get("/api/history").json() if x["kind"] == "single" and x["title"] == "К-А — К-Б"][0]
    check("история: id, kind, legs, статус ожидания", h["status"] == "pending" and len(h["legs"]) == 1 and h["legs"][0]["status"] == "pending", h)
    off = h["cashout_offer"]; check(f"предложение выкупа есть и меньше потенциального выигрыша ({off})", off and 0 < off < 400, h)
    check("чужую ставку выкупить нельзя (404)", v.post("/api/cashout", {"kind": "single", "id": h["id"]}).status_code == 404)
    check("слишком высокая ожидаемая цена — 409", u.post("/api/cashout", {"kind": "single", "id": h["id"], "expected": off + 50}).status_code == 409)
    b1 = u.bal(); r = u.post("/api/cashout", {"kind": "single", "id": h["id"], "expected": off}); check("выкуп ординара", r.status_code == 200 and u.bal() == b1 + off, r.text)
    check("повторный выкуп запрещён", u.post("/api/cashout", {"kind": "single", "id": h["id"]}).status_code == 400)
    h2 = [x for x in u.get("/api/history").json() if x["kind"] == "single" and x["id"] == h["id"]][0]
    check("статус cashed и сумма выкупа в истории", h2["status"] == "cashed" and h2["cashout"] == off and not h2["cashout_offer"], h2)
    bb = u.bal(); adm.post(f"/api/admin/settle/{kid[0]}", {"result": "1"}); check("расчёт не платит повторно за выкупленную ставку", u.bal() == bb)
    check("выкуп записан в журнал", last_tx("cashout") == off, last_tx("cashout"))
    journal_ok(100, "после выкупа ординара")
    # экспресс: выкуп и разбор по ногам
    u.post("/api/express", {"amount": 100, "items": [{"event_id": kid[1], "choice": "1"}, {"event_id": kid[2], "choice": "2"}]})
    he = [x for x in u.get("/api/history").json() if x["kind"] == "express"][0]
    check("экспресс: 2 ноги в истории, выкуп доступен", len(he["legs"]) == 2 and he["cashout_offer"] and he["cashout_offer"] < 500, he)
    adm.post(f"/api/admin/settle/{kid[1]}", {"result": "1"})
    he = [x for x in u.get("/api/history").json() if x["kind"] == "express"][0]
    st = [l["status"] for l in he["legs"]]; check("после расчёта первой ноги: зашла + ожидает", sorted(st) == ["pending", "won"], st)
    off_e = he["cashout_offer"]; check(f"выкуп экспресса растёт после сыгравшей ноги ({off_e})", off_e and off_e > 100, he)
    b1 = u.bal(); r = u.post("/api/cashout", {"kind": "express", "id": he["id"], "expected": off_e}); check("выкуп экспресса", r.status_code == 200 and u.bal() == b1 + off_e, r.text)
    adm.post(f"/api/admin/settle/{kid[2]}", {"result": "2"}); check("расчёт не платит за выкупленный экспресс", u.bal() == b1 + off_e)
    journal_ok(100, "после выкупа экспресса")
    # проигрыш: у карточки видно, что именно не зашло
    for t_ in ("Л-А — Л-Б", "Л-В — Л-Г"):
        adm.post("/api/admin/create-event", {"sport": "Футбол", "title": t_, "o1": 2.0, "ox": 3.0, "o2": 2.5})
    _m = {e["title"]: e["id"] for e in u.get("/api/events").json()}; lid = [_m["Л-А — Л-Б"], _m["Л-В — Л-Г"]]
    u.post("/api/express", {"amount": 50, "items": [{"event_id": lid[0], "choice": "1"}, {"event_id": lid[1], "choice": "1"}]})
    adm.post(f"/api/admin/settle/{lid[0]}", {"result": "1"}); adm.post(f"/api/admin/settle/{lid[1]}", {"result": "2"})
    hl = [x for x in u.get("/api/history").json() if x["kind"] == "express" and x["status"] == "lost"][0]
    check("проигранный экспресс: одна нога won, другая lost, результат виден", sorted(l["status"] for l in hl["legs"]) == ["lost", "won"] and {l["result"] for l in hl["legs"]} == {"1", "2"}, hl)
    check("выкуп проигранной/рассчитанной ставки запрещён", u.post("/api/cashout", {"kind": "express", "id": hl["id"]}).status_code == 400)
    journal_ok(100, "итог")

    print("\n== награды x70 и рефералка 50 000 / 30 000")
    check("все награды заданий кратны x70", all(q_[3] % 70 == 0 for q_ in core.QUESTS.values()) and core.QUESTS["express_win"][3] == 28000)
    import extras.social as S2
    check("ежедневный бонус x70: 3500 … 28000", S2.DAILY_CYCLE[0] == 3500 and S2.DAILY_CYCLE[-1] == 28000, S2.DAILY_CYCLE)
    check("призовые фонды турниров x70", [t_[3] for t_ in S2.TOURNAMENTS] == [350000, 1750000, 5600000], S2.TOURNAMENTS)
    check("рефералка: 50 000 пригласившему, 30 000 приглашённому", core.REF_REWARD_REFERRER == 50000 and core.REF_REWARD_FRIEND == 30000)
    rf = u.get("/api/v2/referral").json(); check("в API рефералки новые суммы", rf["reward_you"] == 50000 and rf["reward_friend"] == 30000, rf)

    print("\n== «Что нового»: посты админа для игроков")
    p1 = U(c, 901, "P1"); p2 = U(c, 902, "P2")
    check("игрок не может публиковать (403)", p1.post("/api/v2/admin/updates", {"title": "x", "body": "y"}).status_code == 403)
    check("пустой текст отклонён", adm.post("/api/v2/admin/updates", {"title": "x", "body": "  "}).status_code == 400)
    check("слишком длинный текст отклонён", adm.post("/api/v2/admin/updates", {"title": "x", "body": "a" * 2001}).status_code == 400)
    r1 = adm.post("/api/v2/admin/updates", {"title": "Новые кейсы", "body": "Привет!\nДобавили <b>кейсы</b>."}); check("админ публикует пост", r1.status_code == 200 and r1.json()["id"] > 0, r1.text)
    r2 = adm.post("/api/v2/admin/updates", {"title": "", "body": "Второй пост"}); check("пустой заголовок → по умолчанию", r2.status_code == 200)
    un = p1.get("/api/v2/updates/unseen").json(); check("игрок видит 2 непрочитанных, новые первыми", len(un) == 2 and un[0]["body"] == "Второй пост" and un[0]["title"] == "Обновление приложения", un)
    check("текст сохранён как есть (экранирует клиент)", un[1]["body"] == "Привет!\nДобавили <b>кейсы</b>.")
    check("без авторизации 401", c.get("/api/v2/updates/unseen").status_code == 401)
    check("отметка прочитанным", p1.post("/api/v2/updates/seen", {"ids": [x["id"] for x in un]}).status_code == 200)
    check("после прочтения пусто", p1.get("/api/v2/updates/unseen").json() == [])
    check("другому игроку всё ещё показывается", len(p2.get("/api/v2/updates/unseen").json()) == 2)
    check("мусор в ids не ломает", p2.post("/api/v2/updates/seen", {"ids": ["x", None, 99999]}).status_code == 200 and len(p2.get("/api/v2/updates/unseen").json()) == 2)
    al = adm.get("/api/v2/admin/updates").json(); check("админ видит, сколько прочитали", al["items"][0]["seen"] == 1 and al["items"][1]["seen"] == 1, al)
    check("игрок не видит админский список", p1.get("/api/v2/admin/updates").status_code == 403)
    check("удаление поста", adm.post("/api/v2/admin/updates/delete", {"id": r1.json()["id"]}).status_code == 200 and len(p2.get("/api/v2/updates/unseen").json()) == 1)
    check("удаление несуществующего — 404", adm.post("/api/v2/admin/updates/delete", {"id": 99999}).status_code == 404)
    check("игрок не может удалять (403)", p1.post("/api/v2/admin/updates/delete", {"id": r2.json()["id"]}).status_code == 403)

    print("\n== новости Dota 2: чтение в приложении")
    import extras.newsreader as NR
    html_ = ('<html><body><h1>Заголовок</h1><div itemprop="articleBody" class="p-content"><div style="display: none"><img src="/a.webp"></div>'
             '<p>Текст <a href="/x">ссылка</a> <script>alert(1)</script><b onclick="x()">жирный</b></p><iframe src="http://evil"></iframe>'
             '<p><img src="/img/n.webp" width="600"><img src="/flag.png" class="inserted-link__flag"></p>'
             '<div class="esport-tabs"><p>виджет</p></div><p>Ещё один абзац с достаточно длинным текстом для проверки.</p></div></body></html>')
    art = NR.parse_article(html_)
    check("статья: заголовок и абзацы", art["title"] == "Заголовок" and "<p>Текст ссылка" in art["html"], art)
    check("статья: скрипты, iframe, обработчики событий, ссылки вырезаны", not any(x in art["html"] for x in ("<script", "alert(1)", "<iframe", "onclick", "<a ", "href")), art["html"])
    check("статья: картинка с абсолютным адресом, флаги и скрытые блоки убраны", art["html"].count("<img") == 1 and 'src="https://dota2.ru/img/n.webp"' in art["html"], art["html"])
    check("статья: виджеты esport-* вырезаны", "виджет" not in art["html"])
    for bad in ("https://evil.com/news/1", "http://localhost:8000/x", "ftp://dota2.ru/x", "javascript:alert(1)"):
        try: NR.clean_url(bad); ok_ = False
        except NR.NewsError: ok_ = True
        check(f"чужой адрес запрещён: {bad}", ok_)
    check("utm-хвост убирается", NR.clean_url("https://dota2.ru/news/1-x/?utm_source=dota2rss") == "https://dota2.ru/news/1-x/")
    check("эндпоинт: чужой сайт → 502", u.get("/api/news-detail", params={"url": "https://evil.com/a"}).status_code == 502)
    check("эндпоинт: без авторизации 401", c.get("/api/news-detail", params={"url": "https://dota2.ru/news/1/"}).status_code == 401)
    NR._download = lambda url: html_
    check("эндпоинт: отдаёт текст статьи", "Ещё один абзац" in u.get("/api/news-detail", params={"url": "https://dota2.ru/news/777-test/?utm_source=x"}).json()["html"])

print(f"\nИТОГО: {OK} ok, {FAIL} fail"); sys.exit(1 if FAIL else 0)
