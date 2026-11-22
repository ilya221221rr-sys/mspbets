"""
extras/mexpress.py — экспресс с доп. рынками («Экспресс+»).

В одном купоне можно смешивать основной исход (П1 / X / П2) любых открытых матчей и доп. рынки матчей
автозагрузки: тоталы, форы, двойной шанс, обе забьют, индивидуальные тоталы, точный счёт.

Правила (как в настоящих БК):
  * от 2 до 12 событий, с одного матча — только одно событие (коррелированные исходы вроде
    «П1 + фора −1.5» в одном купоне дали бы нечестно завышенный общий кэф);
  * общий коэффициент — произведение коэффициентов, не больше MEXPRESS_MAX_ODDS (1000);
  * если коэффициент любого события изменился, пока игрок собирал купон, — 409 с новыми кэфами;
  * экспресс проигрывает, как только проиграло любое событие (не ждём остальных матчей);
  * отменённый матч (или матч без счёта за 5 дней) в экспрессе считается с кэфом 1;
    если отменены все — ставка возвращается.

Расчёт — фоновая задача (plus.scheduler): основной исход берём из рассчитанного матча,
доп. рынки — по итоговому счёту (match_scores или ESPN, как у одинаров на рынки).
Старые таблицы экспрессов (bets / bet_events) не трогаются: свои таблицы mx_bets / mx_legs.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import core, markets, plusdb
from .core import all_, auth, db, money, now_iso, one, user_lock

log = logging.getLogger("mspbets.mexpress")
router = APIRouter(prefix="/api/v2/mexpress")

MIN_LEGS = 2
MAX_LEGS = 12
MAX_TOTAL = float(os.getenv("MEXPRESS_MAX_ODDS", "1000"))
MAX_BET = int(os.getenv("MARKETS_MAX_BET", "1000000"))
NO_SCORE_DAYS = 5

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS mx_bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
        total_odds REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', payout INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, settled_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_mx_user ON mx_bets(user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_mx_status ON mx_bets(status)",
    """CREATE TABLE IF NOT EXISTS mx_legs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bet_id INTEGER NOT NULL, event_id INTEGER NOT NULL,
        market TEXT NOT NULL, pick TEXT NOT NULL, label TEXT NOT NULL, odds REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', score TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_mxl_bet ON mx_legs(bet_id)",
    "CREATE INDEX IF NOT EXISTS idx_mxl_event ON mx_legs(event_id, status)",
]


def _parse_legs(raw):
    if not isinstance(raw, list) or len(raw) < MIN_LEGS:
        raise HTTPException(400, f"В экспрессе минимум {MIN_LEGS} события")
    if len(raw) > MAX_LEGS:
        raise HTTPException(400, f"Максимум {MAX_LEGS} событий в экспрессе")
    legs, seen = [], set()
    for it in raw:
        try:
            eid = int(it["event_id"])
            market, pick = str(it.get("market") or "1x2"), str(it["pick"])
            odds = float(it["odds"]) if it.get("odds") is not None else None
        except Exception:
            raise HTTPException(400, "Неверные данные события")
        if eid in seen:
            raise HTTPException(400, "С одного матча — только одно событие в экспрессе")
        seen.add(eid)
        legs.append({"event_id": eid, "market": market, "pick": pick, "seen": odds})
    return legs


def place(conn, uid, amount, legs, check_odds=True, note_prefix="Экспресс+"):
    """Сделать экспресс (внутри транзакции и user_lock вызывающего). Возвращает (id, total, legs)."""
    changed, priced, total = [], [], 1.0
    for lg in legs:
        ev = markets._event_info(conn, lg["event_id"])
        if not ev or ev["status"] != "open" or markets._started(ev["starts_at"]):
            raise HTTPException(400, f"Матч «{ev['title'] if ev else lg['event_id']}» уже закрыт для ставок")
        item = markets.find_item(ev, lg["market"], lg["pick"])
        if not item:
            raise HTTPException(400, f"Событие недоступно: {ev['title']}")
        if check_odds and lg["seen"] is not None and abs(lg["seen"] - item["odds"]) > 0.004:
            changed.append({"event_id": lg["event_id"], "market": lg["market"], "pick": lg["pick"], "odds": item["odds"]})
        total *= item["odds"]
        priced.append({**lg, "label": markets.full_label(lg["market"], item["label"]), "odds": item["odds"], "title": ev["title"]})
    if changed:
        raise HTTPException(409, "Коэффициенты изменились: " + ", ".join(f"{c['odds']:.2f}" for c in changed))
    total = round(total, 2)
    if total > MAX_TOTAL:
        raise HTTPException(400, f"Общий коэффициент не больше {MAX_TOTAL:.0f} (сейчас {total:.2f})")
    bid = conn.execute("INSERT INTO mx_bets (user_id, amount, total_odds, created_at) VALUES (?,?,?,?)",
                       (uid, amount, total, now_iso())).lastrowid
    for p in priced:
        conn.execute("INSERT INTO mx_legs (bet_id, event_id, market, pick, label, odds) VALUES (?,?,?,?,?,?)",
                     (bid, p["event_id"], p["market"], p["pick"], p["label"], p["odds"]))
    core.adjust(conn, uid, -amount, "bet", f"{note_prefix} из {len(priced)} событий (кф {total:.2f})", ref=f"mx:{bid}")
    core.on_wager(conn, uid, amount, "express")
    core.quest_event(conn, uid, "match_bet", len(priced))
    return bid, total, priced


@router.post("/bet")
def bet(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    legs = _parse_legs(body.get("legs"))
    amount = core.check_amount(body.get("amount"), 1, MAX_BET)
    with user_lock(uid), db() as conn:
        plusdb.ensure_user(user)
        bid, total, priced = place(conn, uid, amount, legs)  # 409 → фронт запросит /quote и обновит купон
        bal = core.balance_of(conn, uid)
    return {"ok": True, "id": bid, "total_odds": total, "potential": core.pay(amount, total), "balance": bal,
            "legs": [{k: p[k] for k in ("event_id", "market", "pick", "label", "odds", "title")} for p in priced],
            "unlocked": core.pop_unlocked()}


@router.post("/quote")
def quote(request: Request, body: dict = Body(...)):
    """Свежие коэффициенты для купона (после 409 или при открытии купона)."""
    auth(request)
    raw = body.get("legs") or []
    out = []
    with db() as conn:
        for it in raw[:MAX_LEGS]:
            try:
                eid, market, pick = int(it["event_id"]), str(it.get("market") or "1x2"), str(it["pick"])
            except Exception:
                continue
            ev = markets._event_info(conn, eid)
            ok_ = bool(ev) and ev["status"] == "open" and not markets._started(ev["starts_at"])
            item = markets.find_item(ev, market, pick) if ok_ else None
            out.append({"event_id": eid, "market": market, "pick": pick, "odds": item["odds"] if item else None,
                        "label": markets.full_label(market, item["label"]) if item else None, "open": bool(item)})
    return {"legs": out}


def bet_rows(conn, where, params, limit=50):
    """Экспрессы+ с событиями (для «Моих ставок», профиля и ленты)."""
    rows = all_(conn, f"SELECT id, user_id, amount, total_odds, status, payout, created_at, settled_at FROM mx_bets WHERE {where} ORDER BY id DESC LIMIT ?",
                (*params, int(limit)))
    if not rows:
        return []
    ids = [r[0] for r in rows]
    marks = ",".join("?" * len(ids))
    legs = {}
    for r in all_(conn, f"""SELECT l.bet_id, l.event_id, l.market, l.pick, l.label, l.odds, l.status, l.score, e.title, e.status, e.starts_at
                            FROM mx_legs l LEFT JOIN events e ON e.id=l.event_id WHERE l.bet_id IN ({marks}) ORDER BY l.id""", ids):
        legs.setdefault(r[0], []).append({
            "event_id": r[1], "market": r[2], "pick": r[3], "label": r[4], "odds": r[5], "status": r[6], "score": r[7],
            "title": r[8] or f"Матч #{r[1]}", "open": r[9] == "open" and not markets._started(r[10]),
        })
    return [{"id": r[0], "user_id": r[1], "amount": r[2], "odds": r[3], "status": r[4], "payout": r[5],
             "created_at": r[6], "settled_at": r[7], "legs": legs.get(r[0], [])} for r in rows]


@router.get("/my")
def my(request: Request, limit: int = 30):
    user = auth(request)
    with db() as conn:
        return {"bets": bet_rows(conn, "user_id=?", (int(user["id"]),), max(1, min(int(limit), 100)))}


# ------------------------------------------------------------------
# Расчёт
# ------------------------------------------------------------------
def _score(conn, ev, cache):
    """Счёт из match_scores. С ESPN его заранее, вне транзакции, подтягивает markets.prefetch_scores."""
    sc = one(conn, "SELECT home, away FROM match_scores WHERE event_id=?", (ev["id"],))
    return (sc[0], sc[1]) if sc else None


def _grade_event_legs(conn, eid, cache, now):
    """Рассчитать ожидающие события экспрессов по одному матчу. Возвращает id затронутых экспрессов."""
    legs = all_(conn, "SELECT id, bet_id, market, pick FROM mx_legs WHERE event_id=? AND status='pending'", (eid,))
    if not legs:
        return set()
    ev = markets._event_info(conn, eid)
    res = {}  # leg_id -> (status, score)
    if not ev or (ev["status"] == "settled" and ev["result"] == "void"):
        res = {lid: ("void", None) for lid, *_ in legs}
    elif ev["status"] == "settled":
        sc = None
        if any(m != "1x2" for _, _, m, _ in legs):
            sc = _score(conn, ev, cache)
        too_old = False
        try:
            too_old = bool(ev["starts_at"]) and now - datetime.fromisoformat(ev["starts_at"]) > timedelta(days=NO_SCORE_DAYS)
        except Exception:
            pass
        for lid, _, market, pick in legs:
            if market == "1x2":
                res[lid] = ("won" if pick == ev["result"] else "lost", f"{sc[0]}:{sc[1]}" if sc else None)
            elif sc:
                res[lid] = (markets.grade(market, pick, sc[0], sc[1]), f"{sc[0]}:{sc[1]}")
            elif too_old:
                res[lid] = ("void", None)
    touched = set()
    for lid, bid, *_ in legs:
        if lid in res:
            st, score = res[lid]
            cur = conn.execute("UPDATE mx_legs SET status=?, score=? WHERE id=? AND status='pending'", (st, score, lid))
            if core.changed(cur, conn) == 1:
                touched.add(bid)
    return touched


def settle_bet(conn, bid):
    """Закрыть экспресс, если уже можно. Возвращает уведомление (uid, text) или None."""
    b = one(conn, "SELECT user_id, amount, status FROM mx_bets WHERE id=?", (bid,))
    if not b or b[2] != "pending":
        return None
    uid, amount = b[0], b[1]
    legs = all_(conn, "SELECT l.status, l.odds, l.label, COALESCE(e.title, '') FROM mx_legs l LEFT JOIN events e ON e.id=l.event_id WHERE l.bet_id=?", (bid,))
    lost = next((l for l in legs if l[0] == "lost"), None)
    if lost:
        cur = conn.execute("UPDATE mx_bets SET status='lost', payout=0, settled_at=? WHERE id=? AND status='pending'", (now_iso(), bid))
        if core.changed(cur, conn) == 1:
            return uid, f"❌ <b>Экспресс+ #{bid} не сыграл</b>\nПодвело событие: {lost[3]} — {lost[2]}\nСтавка: {money(amount)} PTS"
        return None
    if any(l[0] == "pending" for l in legs):
        return None
    live = [l for l in legs if l[0] == "won"]
    if not live:
        cur = conn.execute("UPDATE mx_bets SET status='void', payout=?, settled_at=? WHERE id=? AND status='pending'", (amount, now_iso(), bid))
        if core.changed(cur, conn) == 1:
            core.adjust(conn, uid, amount, "refund", f"Возврат экспресса+ #{bid} (все матчи отменены)", ref=f"mx:{bid}")
            return uid, f"↩️ Экспресс+ #{bid} отменён, ставка <b>{money(amount)} PTS</b> возвращена."
        return None
    total = 1.0
    for l in live:
        total *= l[1]
    total = round(total, 2)
    payout = core.pay(amount, total)
    cur = conn.execute("UPDATE mx_bets SET status='won', payout=?, total_odds=?, settled_at=? WHERE id=? AND status='pending'",
                       (payout, total, now_iso(), bid))
    if core.changed(cur, conn) != 1:
        return None
    core.adjust(conn, uid, payout, "win", f"Выигрыш экспресса+ #{bid} (кф {total:.2f})", ref=f"mx:{bid}")
    core.quest_event(conn, uid, "express_won", 1)
    core.bump(conn, uid, "express_won", 1)
    core.set_max(conn, uid, "biggest_win", payout)
    return uid, f"🚀 <b>Экспресс+ #{bid} сыграл!</b>\nКоэффициент {total:.2f}\nВыигрыш: <b>+{money(payout)} PTS</b>"


def settle_pending():
    """Фоновая задача: рассчитать экспрессы+ по рассчитанным матчам."""
    with db() as conn:
        eids = [r[0] for r in all_(conn, """SELECT DISTINCT l.event_id FROM mx_legs l JOIN mx_bets b ON b.id=l.bet_id
                                          WHERE l.status='pending' AND b.status='pending'""")]
    cache, now, done = {}, datetime.now(timezone.utc), 0
    if eids:
        markets.prefetch_scores(eids, cache)  # сеть — до транзакций
    for eid in eids:
        try:
            with db() as conn:
                notes = [n for n in (settle_bet(conn, bid) for bid in _grade_event_legs(conn, eid, cache, now)) if n]
                if notes:
                    done += len(notes)
                    core.notify_many(conn, notes)
        except Exception as e:
            log.warning("mexpress settle %s: %s", eid, e)
    return done
