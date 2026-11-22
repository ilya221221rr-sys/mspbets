"""
extras/accaperks.py — бонус к экспрессу и страховка экспресса (как ACCA Boost / ACCA Insurance в БК).

Бонус к экспрессу
  Выигравший экспресс (обычный или экспресс+) получает надбавку к ЧИСТОМУ выигрышу:
  3 события → +5%, 4 → +10%, 5 → +15% … 12 → +50% (BOOST).
  Считаются только сыгравшие события с кэфом от BOOST_MIN_ODDS (1.30); отменённые не считаются.
  Бонус = чистый выигрыш × процент, но не больше BOOST_CAP. Начисляется отдельной строкой журнала
  («acca_boost»), сам расчёт экспресса не меняется.

Страховка экспресса
  Экспресс от INS_MIN_LEGS (4) событий, где у КАЖДОГО события кэф от INS_MIN_ODDS (1.30),
  проиграл ровно из-за одного события (остальные сыграли или отменены, но живых событий
  осталось не меньше 4) → ставка возвращается фрибетом (не больше INS_CAP), срок FB_DAYS.
  Ставки, сделанные фрибетом, не страхуются.

Работает фоновой сверкой раз в минуту (wave1.scheduler) по уже рассчитанным ставкам; учитываются
только ставки, сделанные после запуска функции (курсор в plus_meta). Каждая ставка обрабатывается
один раз — PRIMARY KEY (kind, bet_id) в w1_perks.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from . import core, freebets, markets
from .core import all_, auth, db, money, now_iso, one, user_lock

log = logging.getLogger("mspbets.accaperks")
router = APIRouter(prefix="/api/v2/perks")

BOOST = {3: 5, 4: 10, 5: 15, 6: 20, 7: 25, 8: 30, 9: 35, 10: 40, 11: 45, 12: 50}
BOOST_MIN_ODDS = 1.30
BOOST_CAP = int(os.getenv("ACCA_BOOST_CAP", "1000000"))
INS_MIN_LEGS = 4
INS_MIN_ODDS = 1.30
INS_CAP = int(os.getenv("ACCA_INSURANCE_CAP", "100000"))
WAIT_DAYS = 6  # сколько ждём счёт по остальным событиям проигравшего экспресса

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS w1_perks (
        kind TEXT NOT NULL, bet_id INTEGER NOT NULL, user_id INTEGER NOT NULL, perk TEXT NOT NULL,
        pct INTEGER NOT NULL DEFAULT 0, amount INTEGER NOT NULL DEFAULT 0, legs INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, PRIMARY KEY (kind, bet_id))""",
    "CREATE INDEX IF NOT EXISTS idx_w1perks_user ON w1_perks(user_id)",
]

TABLE = {"express": "bets", "mx": "mx_bets"}


def rules():
    return {"boost": BOOST, "boost_min_odds": BOOST_MIN_ODDS, "boost_cap": BOOST_CAP,
            "ins_min_legs": INS_MIN_LEGS, "ins_min_odds": INS_MIN_ODDS, "ins_cap": INS_CAP, "fb_days": freebets.FB_DAYS}


def boost_pct(n):
    return BOOST[min(int(n), max(BOOST))] if n >= min(BOOST) else 0


def insurable(odds_list):
    return len(odds_list) >= INS_MIN_LEGS and all(float(o) >= INS_MIN_ODDS for o in odds_list)


@router.get("/rules")
def get_rules(request: Request):
    auth(request)
    return rules()


def perk_map(conn, uid):
    """{'kind:id': {perk, pct, amount}} — начисленные бонусы и страховки игрока (для истории)."""
    return {f"{k}:{b}": {"perk": p, "pct": pct, "amount": a}
            for k, b, p, pct, a in all_(conn, "SELECT kind, bet_id, perk, pct, amount FROM w1_perks WHERE user_id=? AND perk!='none' ORDER BY rowid DESC LIMIT 300", (uid,))}


# ------------------------------------------------------------------
# События ставки
# ------------------------------------------------------------------
def _ev_leg(ev_status, result, choice):
    if ev_status != "settled":
        return "pending"
    if result == "void":
        return "void"
    return "won" if result == choice else "lost"


def legs_of(conn, kind, bid):
    """[(odds, status, event_id, market, pick, ev_status, starts_at)] — статусы событий, даже если экспресс
    уже закрыт по первому проигрышу (у экспресса+ остальные события тогда не рассчитываются)."""
    out = []
    if kind == "express":
        for choice, odds, eid, st, res, starts in all_(conn, """SELECT be.choice, be.locked_odds, be.event_id, e.status, e.result, e.starts_at
                                                                FROM bet_events be LEFT JOIN events e ON e.id=be.event_id WHERE be.bet_id=? ORDER BY be.id""", (bid,)):
            out.append((odds, "void" if st is None else _ev_leg(st, res, choice), eid, "1x2", choice, st, starts))
        return out
    for odds, lst, eid, market, pick, st, res, starts, h, a in all_(conn, """SELECT l.odds, l.status, l.event_id, l.market, l.pick, e.status, e.result, e.starts_at, s.home, s.away
                                                                            FROM mx_legs l LEFT JOIN events e ON e.id=l.event_id
                                                                            LEFT JOIN match_scores s ON s.event_id=l.event_id WHERE l.bet_id=? ORDER BY l.id""", (bid,)):
        if lst != "pending":
            status = lst
        elif st is None or (st == "settled" and res == "void"):
            status = "void"
        elif st != "settled":
            status = "pending"
        elif market == "1x2":
            status = "won" if pick == res else "lost"
        elif h is not None:
            status = markets.grade(market, pick, h, a)
        else:
            status = "pending"  # нужен счёт
        out.append((odds, status, eid, market, pick, st, starts))
    return out


def _too_old(starts_list, now):
    for s in starts_list:
        try:
            if s and now - datetime.fromisoformat(s) < timedelta(days=WAIT_DAYS):
                return False
        except Exception:
            pass
    return True


def _mark(conn, kind, bid, uid, perk, pct=0, amount=0, legs=0):
    cur = conn.execute("INSERT OR IGNORE INTO w1_perks (kind, bet_id, user_id, perk, pct, amount, legs, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (kind, bid, uid, perk, pct, amount, legs, now_iso()))
    return core.changed(cur, conn) == 1


def decide(conn, kind, bid, now):
    """Обработать одну рассчитанную ставку. Возвращает уведомление (uid, text), 'wait' или None."""
    tbl = TABLE[kind]
    pay_col = "payout" if kind == "mx" else "CAST(amount * total_odds AS INTEGER)"
    b = one(conn, f"SELECT user_id, amount, status, total_odds, {pay_col} FROM {tbl} WHERE id=?", (bid,))
    if not b or b[2] == "pending":
        return "wait"
    uid, amount, status, total, payout = b
    if one(conn, "SELECT 1 FROM w1_perks WHERE kind=? AND bet_id=?", (kind, bid)):
        return None
    legs = legs_of(conn, kind, bid)
    if status == "won":
        n = sum(1 for l in legs if l[1] == "won" and float(l[0]) >= BOOST_MIN_ODDS)
        pct = boost_pct(n)
        profit = int(payout or 0) - int(amount)
        bonus = min(BOOST_CAP, profit * pct // 100) if pct and profit > 0 else 0
        if bonus <= 0:
            _mark(conn, kind, bid, uid, "none", legs=n)
            return None
        if not _mark(conn, kind, bid, uid, "boost", pct, bonus, n):
            return None
        core.adjust(conn, uid, bonus, "acca_boost", f"Бонус к экспрессу +{pct}% ({n} соб.)", ref=f"{kind}:{bid}")
        return uid, f"🔥 <b>Бонус к экспрессу +{pct}%</b>\nЗа {n} событий с кэфом от {BOOST_MIN_ODDS:.2f}: <b>+{money(bonus)} PTS</b>"
    if status != "lost" or not insurable([l[0] for l in legs]) or freebets.is_freebet(conn, kind, bid):
        _mark(conn, kind, bid, uid, "none", legs=len(legs))
        return None
    lost = sum(1 for l in legs if l[1] == "lost")
    if lost >= 2:
        _mark(conn, kind, bid, uid, "none", legs=len(legs))
        return None
    if any(l[1] == "pending" for l in legs):
        if _too_old([l[6] for l in legs], now):
            _mark(conn, kind, bid, uid, "none", legs=len(legs))
            return None
        return "wait"
    live = sum(1 for l in legs if l[1] != "void")
    if lost != 1 or live < INS_MIN_LEGS:
        _mark(conn, kind, bid, uid, "none", legs=len(legs))
        return None
    refund = min(int(amount), INS_CAP)
    if not _mark(conn, kind, bid, uid, "insurance", 0, refund, len(legs)):
        return None
    if not freebets.issue(conn, uid, refund, "insurance", f"Экспресс #{bid}: не зашло одно событие"):
        conn.execute("UPDATE w1_perks SET perk='none', amount=0 WHERE kind=? AND bet_id=?", (kind, bid))
        return None
    return uid, (f"🛡 <b>Страховка экспресса</b>\nЭкспресс не зашёл из-за одного события — возвращаем ставку фрибетом "
                 f"<b>{money(refund)} PTS</b> (действует {freebets.FB_DAYS} дн.).")


def init(conn, meta_get, meta_set):
    """Ставки, сделанные до запуска функции, не пересчитываем."""
    for kind, tbl in TABLE.items():
        key = f"w1_perks_start_{kind}"
        if meta_get(conn, key) is None:
            meta_set(conn, key, one(conn, f"SELECT COALESCE(MAX(id), 0) FROM {tbl}")[0])


def _fetch_scores(need):
    """Подтянуть итоговые счета ESPN для событий экспрессов+, по которым их ещё нет (сеть — без открытой транзакции)."""
    if not need:
        return
    evs = []
    with db() as conn:
        for eid in need:
            ev = markets._event_info(conn, eid)
            if ev and ev["status"] == "settled" and ev["source"] == "espn":
                evs.append(ev)
    cache, found = {}, []
    for ev in evs:
        try:
            sc = markets._espn_score(None, ev, cache)
        except Exception as e:
            log.info("perks score %s: %s", ev["id"], e)
            sc = None
        if sc:
            found.append((ev["id"], sc))
    if found:
        with db() as conn:
            for eid, sc in found:
                conn.execute("INSERT OR IGNORE INTO match_scores (event_id, home, away, source, created_at) VALUES (?,?,?,?,?)",
                             (eid, sc[0], sc[1], "espn", now_iso()))


def process(meta_get):
    """Фоновая сверка: бонусы к выигравшим экспрессам и страховка проигравших. Возвращает число начислений."""
    cand = []
    with db() as conn:
        for kind, tbl in TABLE.items():
            start = int(meta_get(conn, f"w1_perks_start_{kind}") or 0)
            cand += [(kind, r[0], r[1]) for r in all_(conn, f"""SELECT b.id, b.user_id FROM {tbl} b WHERE b.id>? AND b.status!='pending'
                     AND NOT EXISTS (SELECT 1 FROM w1_perks p WHERE p.kind=? AND p.bet_id=b.id) ORDER BY b.id LIMIT 150""", (start, kind))]
        need = set()
        for kind, bid, _ in cand:
            if kind == "mx":
                need.update(l[2] for l in legs_of(conn, kind, bid) if l[1] == "pending" and l[5] == "settled")
    try:
        _fetch_scores(need)
    except Exception as e:
        log.info("perks scores: %s", e)
    now, paid = datetime.now(timezone.utc), 0
    for kind, bid, uid in cand:
        try:
            with user_lock(uid), db() as conn:
                res = decide(conn, kind, bid, now)
                if isinstance(res, tuple):
                    paid += 1
                    core.notify_many(conn, [res])
        except Exception as e:
            log.warning("perks %s:%s: %s", kind, bid, e)
    return paid
