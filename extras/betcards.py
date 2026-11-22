"""
extras/betcards.py — ставки на новые рынки в общей «Истории ставок», карточка ставки и «Поделиться».

  * GET  /api/v2/betcards/history — ставки игрока на доп. рынки и экспрессы+ (в формате общей истории)
    + отметки для ВСЕХ видов ставок: сделана фрибетом, бонус к экспрессу, страховка.
    Старый /api/history (ординары и экспрессы) не меняется — фронт склеивает два списка по времени.
  * POST /api/v2/betcards/share — ссылка «t.me/<бот>?startapp=bet_…» на свою ставку (любой вид).
    Ссылка подписана HMAC: по ней нельзя перебором открыть чужие ставки.
  * GET  /api/v2/betcards/shared?p=… — открыть ставку по ссылке (карточка + «Поставить так же»).
  * POST /api/v2/betcards/shared/copy — поставить так же по текущим коэффициентам (даже если автор
    скрыл свои ставки в профиле: он сам поделился ссылкой).
"""
import hashlib
import hmac
import html
import os
import re

from fastapi import APIRouter, Body, HTTPException, Request

from . import accaperks, betlog, betting, core, freebets, markets, mexpress
from .core import all_, auth, db, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/betcards")

KIND_CODE = {"single": "s", "express": "e", "market": "m", "mx": "x"}
CODE_KIND = {v: k for k, v in KIND_CODE.items()}
TABLE = {"single": "predictions", "express": "bets", "market": "market_bets", "mx": "mx_bets"}
_SECRET = hashlib.sha256(("w1-share:" + os.getenv("BOT_TOKEN", "local")).encode()).digest()
_P = re.compile(r"^bet_([semx])(\d{1,12})_([0-9a-f]{12})$")


def sign(kind, bid):
    return hmac.new(_SECRET, f"{kind}:{int(bid)}".encode(), hashlib.sha256).hexdigest()[:12]


def payload(kind, bid):
    return f"bet_{KIND_CODE[kind]}{int(bid)}_{sign(kind, bid)}"


def parse(p):
    m = _P.match(str(p or ""))
    if not m:
        raise HTTPException(404, "Ссылка на ставку повреждена")
    kind, bid = CODE_KIND[m.group(1)], int(m.group(2))
    if not hmac.compare_digest(sign(kind, bid), m.group(3)):
        raise HTTPException(404, "Ссылка на ставку повреждена")
    return kind, bid


def _one_bet(conn, kind, bid):
    """Ставка любого вида в формате ленты (betlog.collect), или None."""
    owner = one(conn, f"SELECT user_id FROM {TABLE[kind]} WHERE id=?", (bid,))
    if not owner:
        return None
    # collect по игроку отдаёт последние ставки; ищем нужную, а если она старая — берём шире
    for lim in (60, 400):
        for b in betlog.collect(conn, owner[0], lim):
            if b["kind"] == kind and b["id"] == bid:
                return b
    return None


def my_new_bets(conn, uid, limit):
    """Одинары на доп. рынки + экспрессы+ игрока (формат как в betlog.collect)."""
    out = []
    for r in all_(conn, """SELECT p.id, p.event_id, p.market, p.pick, p.label, p.amount, p.odds, p.status, p.payout, p.score, p.created_at,
                                  e.title, e.status, e.starts_at, e.sport
                           FROM market_bets p LEFT JOIN events e ON e.id=p.event_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT ?""", (uid, limit)):
        out.append({"kind": "market", "id": r[0], "amount": r[5], "odds": r[6], "status": r[7], "payout": r[8], "created_at": r[10],
                    "sport": r[14] or "", "legs": [{"event_id": r[1], "title": r[11] or f"Матч #{r[1]}", "market": r[2], "pick": r[3],
                                                    "label": markets.full_label(r[2], r[4]), "odds": r[6], "status": r[7], "score": r[9],
                                                    "open": betlog._open(r[12], r[13])}]})
    for b in mexpress.bet_rows(conn, "user_id=?", (uid,), limit):
        b.pop("user_id", None)
        out.append({"kind": "mx", **b, "sport": "Экспресс+"})
    out.sort(key=lambda x: x["created_at"] or "", reverse=True)
    out = out[:limit]
    _fill_closed_legs(conn, out)
    for b in out:
        b["copyable"] = b["status"] == "pending" and bool(b["legs"]) and all(l["open"] for l in b["legs"])
        b["express"] = b["kind"] == "mx"
    return out


def _fill_closed_legs(conn, bets):
    """Экспресс+ закрывается по первому проигрышу, остальные события в нём остаются «ожидает».
    Для карточки досчитываем их по результату матча / итоговому счёту одним запросом."""
    need = {l["event_id"] for b in bets if b["kind"] == "mx" and b["status"] != "pending" for l in b["legs"] if l["status"] == "pending"}
    if not need:
        return
    ids = list(need)
    info = {r[0]: r[1:] for r in all_(conn, f"""SELECT e.id, e.status, e.result, s.home, s.away FROM events e
                                               LEFT JOIN match_scores s ON s.event_id=e.id WHERE e.id IN ({','.join('?' * len(ids))})""", ids)}
    for b in bets:
        if b["kind"] != "mx" or b["status"] == "pending":
            continue
        for l in b["legs"]:
            if l["status"] != "pending":
                continue
            st, res, h, a = info.get(l["event_id"], (None, None, None, None))
            if st is None or (st == "settled" and res == "void"):
                l["status"] = "void"
            elif st == "settled" and l["market"] == "1x2":
                l["status"] = "won" if l["pick"] == res else "lost"
            elif st == "settled" and h is not None:
                l["status"], l["score"] = markets.grade(l["market"], l["pick"], h, a), f"{h}:{a}"


@router.get("/history")
def history(request: Request, limit: int = 50):
    uid = int(auth(request)["id"])
    limit = max(1, min(int(limit), 100))
    with db() as conn:
        bets = my_new_bets(conn, uid, limit)
        fb = freebets.bet_map(conn, uid)
        perks = accaperks.perk_map(conn, uid)
    for b in bets:
        b["title"] = b["legs"][0]["title"] if b["kind"] == "market" and b["legs"] else f"Экспресс+ из {len(b['legs'])} событий"
    return {"bets": bets, "freebets": fb, "perks": perks, "rules": accaperks.rules()}


@router.post("/share")
def share(request: Request, body: dict = Body(...)):
    uid = int(auth(request)["id"])
    kind = str(body.get("kind") or "")
    if kind not in TABLE:
        raise HTTPException(400, "Неверный вид ставки")
    try:
        bid = int(body.get("id"))
    except Exception:
        raise HTTPException(400, "Неверная ставка")
    with db() as conn:
        r = one(conn, f"SELECT user_id FROM {TABLE[kind]} WHERE id=?", (bid,))
    if not r or r[0] != uid:
        raise HTTPException(404, "Ставка не найдена")
    p = payload(kind, bid)
    return {"payload": p, "link": f"https://t.me/{core.ctx.bot_username or 'mspbetsbot'}?startapp={p}"}


@router.get("/shared")
def shared(request: Request, p: str = ""):
    viewer = int(auth(request)["id"])
    kind, bid = parse(p)
    with db() as conn:
        b = _one_bet(conn, kind, bid)
        if not b:
            raise HTTPException(404, "Ставка не найдена")
        u = one(conn, """SELECT COALESCE(u.first_name, u.username, 'Игрок'), COALESCE(pr.xp,0), COALESCE(pr.avatar,''), COALESCE(pr.frame,'')
                         FROM users u LEFT JOIN profiles pr ON pr.user_id=u.user_id WHERE u.user_id=?""", (b["user_id"],))
        fb = freebets.is_freebet(conn, kind, bid)
        perk = one(conn, "SELECT perk, pct, amount FROM w1_perks WHERE kind=? AND bet_id=? AND perk!='none'", (kind, bid))
    owner = b.pop("user_id")
    b.update({"uid": owner, "name": u[0] if u else "Игрок", "level": core.level_for(u[1]) if u else 1,
              "avatar": u[2] if u else "", "frame": u[3] if u else "", "me": owner == viewer, "freebet": fb,
              "perk": {"perk": perk[0], "pct": perk[1], "amount": perk[2]} if perk else None, "p": p})
    return b


def place_copy(conn, uid, kind, bid, amount):
    """Поставить так же, как в ставке kind/bid, по текущим коэффициентам. Возвращает итоговый кэф."""
    if kind == "single":
        amount = core.check_amount(amount)
        ev_id, choice = one(conn, "SELECT event_id, choice FROM predictions WHERE id=?", (bid,))
        return betting.place_single(conn, uid, ev_id, choice, amount, note_prefix="Ставка по ссылке")["odds"]
    if kind == "market":
        amount = core.check_amount(amount, 1, markets.MAX_BET)
        ev_id, market, pick = one(conn, "SELECT event_id, market, pick FROM market_bets WHERE id=?", (bid,))
        return markets.place_market_bet(conn, uid, ev_id, market, pick, amount, note_prefix="Ставка по ссылке")[1]["odds"]
    if kind == "express":
        amount = core.check_amount(amount)
        items = [{"event_id": e, "choice": c} for e, c in all_(conn, "SELECT event_id, choice FROM bet_events WHERE bet_id=? ORDER BY id", (bid,))]
        return betting.place_express(conn, uid, amount, items)["total_odds"]
    amount = core.check_amount(amount, 1, mexpress.MAX_BET)
    legs = [{"event_id": e, "market": m, "pick": pk, "seen": None}
            for e, m, pk in all_(conn, "SELECT event_id, market, pick FROM mx_legs WHERE bet_id=? ORDER BY id", (bid,))]
    return mexpress.place(conn, uid, amount, legs, check_odds=False, note_prefix="Экспресс+ по ссылке")[1]


@router.post("/shared/copy")
def shared_copy(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    kind, bid = parse(body.get("p"))
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        r = one(conn, f"SELECT user_id FROM {TABLE[kind]} WHERE id=?", (bid,))
        if not r:
            raise HTTPException(404, "Ставка не найдена")
        if r[0] == uid:
            raise HTTPException(400, "Это ваша ставка")
        odds = place_copy(conn, uid, kind, bid, body.get("amount"))
        bal = core.balance_of(conn, uid)
        core.notify_many(conn, [(r[0], f"🔗 Игрок <b>{html.escape(core.display_name(user))}</b> поставил так же по твоей ссылке.")])
    return {"ok": True, "odds": odds, "balance": bal, "unlocked": core.pop_unlocked()}
