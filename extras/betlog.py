"""
extras/betlog.py — единая лента ставок игрока: все виды ставок в одном списке по времени.

Старый профиль игрока и «Лента ставок» показывали только одинары П1/X/П2 (таблица predictions),
поэтому ставки на тоталы, форы, точный счёт и экспрессы там не появлялись — и если игрок в последнее
время ставил только их, «последних ставок» у него будто не было. Здесь собираются вместе:
  * single  — одинар на исход матча (predictions);
  * express — старый экспресс (bets / bet_events);
  * market  — одинар на доп. рынок (market_bets);
  * mx      — экспресс+ с доп. рынками (mx_bets / mx_legs).
Учитывается настройка «скрыть мои ставки». Копирование — по текущим коэффициентам.
Старые эндпоинты и таблицы не меняются.
"""
import html

from fastapi import APIRouter, Body, HTTPException, Request

from . import betting, core, markets, mexpress
from .core import all_, auth, db, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2/betlog")

CH = {"1": "П1", "X": "Ничья", "2": "П2"}


def _open(status, starts_at):
    return status == "open" and not markets._started(starts_at)


def _pick_label(title, choice):
    parts = [x.strip() for x in str(title or "").replace("–", "—").split("—")]
    if choice == "1" and len(parts) == 2:
        return f"П1 · {parts[0]}"
    if choice == "2" and len(parts) == 2:
        return f"П2 · {parts[1]}"
    return CH.get(choice, choice)


def _leg_status(ev_status, result, choice):
    if ev_status != "settled":
        return "pending"
    if result == "void":
        return "void"
    return "won" if result == choice else "lost"


def collect(conn, uid=None, limit=20, pending_only=False, exclude_hidden=False):
    """Последние ставки всех видов. uid=None — по всем игрокам (лента)."""
    lim = int(limit)
    out = []
    who = "AND p.user_id=?" if uid is not None else ""
    hid = "AND COALESCE(pr.hide_bets,0)=0" if exclude_hidden else ""
    args = (uid,) if uid is not None else ()

    # одинары П1/X/П2
    pend = "AND p.status='pending'" if pending_only else ""
    for r in all_(conn, f"""SELECT p.id, p.user_id, p.event_id, p.choice, p.points, p.locked_odds, p.status, p.created_at,
                                   e.title, e.status, e.starts_at, e.result
                            FROM predictions p LEFT JOIN events e ON e.id=p.event_id LEFT JOIN profiles pr ON pr.user_id=p.user_id
                            WHERE 1=1 {who} {pend} {hid} ORDER BY p.id DESC LIMIT ?""", (*args, lim)):
        title = r[8] or f"Матч #{r[2]}"
        out.append({"kind": "single", "id": r[0], "user_id": r[1], "amount": r[4], "odds": r[5], "status": r[6], "created_at": r[7],
                    "payout": core.pay(r[4], r[5]) if r[6] == "won" else (r[4] if r[6] == "void" else 0),
                    "legs": [{"event_id": r[2], "title": title, "market": "1x2", "pick": r[3], "choice": r[3],
                              "label": _pick_label(title, r[3]), "odds": r[5], "status": r[6] if r[6] in ("won", "lost", "void") else _leg_status(r[9], r[11], r[3]),
                              "open": _open(r[9], r[10])}]})

    # старые экспрессы
    pend = "AND p.status='pending'" if pending_only else ""
    rows = all_(conn, f"""SELECT p.id, p.user_id, p.amount, p.total_odds, p.status, p.created_at FROM bets p
                          LEFT JOIN profiles pr ON pr.user_id=p.user_id WHERE 1=1 {who} {pend} {hid} ORDER BY p.id DESC LIMIT ?""", (*args, lim))
    if rows:
        ids = [r[0] for r in rows]
        legs = {}
        for l in all_(conn, f"""SELECT be.bet_id, be.event_id, be.choice, be.locked_odds, e.title, e.status, e.starts_at, e.result
                                FROM bet_events be LEFT JOIN events e ON e.id=be.event_id
                                WHERE be.bet_id IN ({','.join('?' * len(ids))}) ORDER BY be.id""", ids):
            title = l[4] or f"Матч #{l[1]}"
            legs.setdefault(l[0], []).append({"event_id": l[1], "title": title, "market": "1x2", "pick": l[2], "choice": l[2],
                                              "label": _pick_label(title, l[2]), "odds": l[3], "status": _leg_status(l[5], l[7], l[2]),
                                              "open": _open(l[5], l[6])})
        for r in rows:
            out.append({"kind": "express", "id": r[0], "user_id": r[1], "amount": r[2], "odds": r[3], "status": r[4], "created_at": r[5],
                        "payout": core.pay(r[2], r[3]) if r[4] == "won" else (r[2] if r[4] == "void" else 0), "legs": legs.get(r[0], [])})

    # одинары на доп. рынки
    pend = "AND p.status='pending'" if pending_only else ""
    for r in all_(conn, f"""SELECT p.id, p.user_id, p.event_id, p.market, p.pick, p.label, p.amount, p.odds, p.status, p.payout, p.score, p.created_at,
                                   e.title, e.status, e.starts_at
                            FROM market_bets p LEFT JOIN events e ON e.id=p.event_id LEFT JOIN profiles pr ON pr.user_id=p.user_id
                            WHERE 1=1 {who} {pend} {hid} ORDER BY p.id DESC LIMIT ?""", (*args, lim)):
        out.append({"kind": "market", "id": r[0], "user_id": r[1], "amount": r[6], "odds": r[7], "status": r[8], "payout": r[9], "created_at": r[11],
                    "legs": [{"event_id": r[2], "title": r[12] or f"Матч #{r[2]}", "market": r[3], "pick": r[4], "label": markets.full_label(r[3], r[5]), "odds": r[7],
                              "status": r[8], "score": r[10], "open": _open(r[13], r[14])}]})

    # экспрессы+
    conds, params = ["1=1"], []
    if uid is not None:
        conds.append("user_id=?")
        params.append(uid)
    if pending_only:
        conds.append("status='pending'")
    if exclude_hidden:
        conds.append("user_id NOT IN (SELECT user_id FROM profiles WHERE COALESCE(hide_bets,0)=1)")
    for b in mexpress.bet_rows(conn, " AND ".join(conds), tuple(params), lim):
        out.append({"kind": "mx", "id": b["id"], "user_id": b["user_id"], "amount": b["amount"], "odds": b["odds"], "status": b["status"],
                    "payout": b["payout"], "created_at": b["created_at"], "legs": b["legs"]})

    out.sort(key=lambda x: x["created_at"] or "", reverse=True)
    out = out[:lim]
    for b in out:
        b["copyable"] = b["status"] == "pending" and bool(b["legs"]) and all(l["open"] for l in b["legs"])
        b["express"] = b["kind"] in ("express", "mx")
    return out


@router.get("/player/{uid}")
def player(uid: int, request: Request, limit: int = 20):
    viewer = int(auth(request)["id"])
    with db() as conn:
        p = one(conn, "SELECT COALESCE(hide_bets,0) FROM profiles WHERE user_id=?", (uid,))
        hidden = bool(p and p[0]) and uid != viewer
        bets = [] if hidden else collect(conn, uid, max(1, min(int(limit), 50)))
    for b in bets:
        b.pop("user_id", None)
    return {"hidden": hidden, "me": uid == viewer, "bets": bets}


@router.get("/feed")
def feed(request: Request):
    """Ставки игроков на матчи, которые ещё не начались (все виды), — для «Ленты ставок»."""
    auth(request)
    with db() as conn:
        rows = [b for b in collect(conn, None, 60, pending_only=True, exclude_hidden=True) if b["copyable"]][:30]
        uids = list({b["user_id"] for b in rows})
        info = {}
        if uids:
            for r in all_(conn, f"""SELECT u.user_id, COALESCE(u.first_name, u.username, 'Игрок'), COALESCE(pr.xp,0), COALESCE(pr.avatar,''), COALESCE(pr.frame,'')
                                    FROM users u LEFT JOIN profiles pr ON pr.user_id=u.user_id WHERE u.user_id IN ({','.join('?' * len(uids))})""", uids):
                info[r[0]] = {"name": r[1], "level": core.level_for(r[2]), "avatar": r[3], "frame": r[4]}
    for b in rows:
        b.update(info.get(b["user_id"], {"name": "Игрок", "level": 1, "avatar": "", "frame": ""}))
        b["uid"] = b.pop("user_id")
    return rows


@router.post("/copy")
def copy(request: Request, body: dict = Body(...)):
    """Скопировать любую ставку другого игрока (одинар, рынок, экспресс, экспресс+) по текущим коэффициентам."""
    user = auth(request)
    uid = int(user["id"])
    kind = str(body.get("kind") or "")
    try:
        bid = int(body.get("id"))
    except Exception:
        raise HTTPException(400, "Неверная ставка")
    table = {"single": "predictions", "express": "bets", "market": "market_bets", "mx": "mx_bets"}.get(kind)
    if not table:
        raise HTTPException(400, "Неверный вид ставки")
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        r = one(conn, f"SELECT t.user_id, COALESCE(pr.hide_bets,0) FROM {table} t LEFT JOIN profiles pr ON pr.user_id=t.user_id WHERE t.id=?", (bid,))
        if not r or r[1]:
            raise HTTPException(404, "Ставка недоступна")
        owner = r[0]
        if owner == uid:
            raise HTTPException(400, "Это ваша ставка")
        if kind == "single":
            amount = core.check_amount(body.get("amount"))
            ev_id, choice = one(conn, "SELECT event_id, choice FROM predictions WHERE id=?", (bid,))
            odds = betting.place_single(conn, uid, ev_id, choice, amount, note_prefix="Копия ставки")["odds"]
        elif kind == "market":
            amount = core.check_amount(body.get("amount"), 1, markets.MAX_BET)
            ev_id, market, pick = one(conn, "SELECT event_id, market, pick FROM market_bets WHERE id=?", (bid,))
            odds = markets.place_market_bet(conn, uid, ev_id, market, pick, amount, note_prefix="Копия ставки")[1]["odds"]
        elif kind == "express":
            amount = core.check_amount(body.get("amount"))
            items = [{"event_id": e, "choice": c} for e, c in all_(conn, "SELECT event_id, choice FROM bet_events WHERE bet_id=? ORDER BY id", (bid,))]
            odds = betting.place_express(conn, uid, amount, items)["total_odds"]
        else:
            amount = core.check_amount(body.get("amount"), 1, mexpress.MAX_BET)
            legs = [{"event_id": e, "market": m, "pick": p, "seen": None}
                    for e, m, p in all_(conn, "SELECT event_id, market, pick FROM mx_legs WHERE bet_id=? ORDER BY id", (bid,))]
            odds = mexpress.place(conn, uid, amount, legs, check_odds=False, note_prefix="Копия экспресса+")[1]
        bal = core.balance_of(conn, uid)
        core.notify_many(conn, [(owner, f"👥 Игрок <b>{html.escape(core.display_name(user))}</b> скопировал твою ставку.")])
    return {"ok": True, "odds": odds, "balance": bal, "unlocked": core.pop_unlocked()}
