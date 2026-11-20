"""
extras/betting.py — ставки на матчи (ординар / экспресс), расчёт, уведомления.
Используется и старыми эндпоинтами app.py, и автозагрузкой матчей (feed.py).
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from . import core
from .core import adjust, all_, one, now_iso, money

CHOICE_RU = {"1": "П1", "X": "Ничья", "2": "П2"}


def _odds_of(row, choice):
    # row = (odds_1, odds_x, odds_2)
    return {"1": row[0], "X": row[1], "2": row[2]}.get(choice)


def _started(starts_at):
    if not starts_at:
        return False
    try:
        return datetime.fromisoformat(starts_at) <= datetime.now(timezone.utc)
    except Exception:
        return False


def place_single(conn, uid, event_id, choice, points, note_prefix="Ставка"):
    points = core.check_amount(points)
    ev = one(conn, "SELECT status, odds_1, odds_x, odds_2, title, starts_at FROM events WHERE id=?", (int(event_id),))
    if not ev or ev[0] != "open" or _started(ev[5]):
        raise HTTPException(400, "Приём ставок закрыт")
    odds = _odds_of(ev[1:4], str(choice))
    if not odds:
        raise HTTPException(400, "Неверный исход")
    adjust(conn, uid, -points, "bet", f"{note_prefix}: {ev[4]} ({CHOICE_RU.get(choice, choice)})", ref=f"event:{event_id}")
    cur = conn.execute(
        "INSERT INTO predictions (user_id, event_id, choice, points, locked_odds, created_at) VALUES (?,?,?,?,?,?)",
        (uid, int(event_id), str(choice), points, odds, now_iso()),
    )
    core.on_wager(conn, uid, points, "match")
    core.quest_event(conn, uid, "match_bet", 1)
    return {"id": cur.lastrowid, "odds": odds}


def place_express(conn, uid, amount, items):
    amount = core.check_amount(amount)
    if not isinstance(items, list) or len(items) < 2:
        raise HTTPException(400, "Мин. 2 события и сумма > 0")
    if len(items) > 12:
        raise HTTPException(400, "Максимум 12 событий в экспрессе")
    seen, legs, total = set(), [], 1.0
    for it in items:
        ev_id, choice = int(it["event_id"]), str(it["choice"])
        if ev_id in seen:
            raise HTTPException(400, "Нельзя ставить на один матч дважды в экспрессе")
        seen.add(ev_id)
        ev = one(conn, "SELECT status, odds_1, odds_x, odds_2, title, starts_at FROM events WHERE id=?", (ev_id,))
        if not ev or ev[0] != "open" or _started(ev[5]):
            raise HTTPException(400, f"Событие {ev_id} закрыто")
        odds = _odds_of(ev[1:4], choice)
        if not odds:
            raise HTTPException(400, f"Неверный исход в {ev_id}")
        total *= odds
        legs.append((ev_id, choice, odds))
    total = round(total, 4)
    adjust(conn, uid, -amount, "bet", f"Экспресс из {len(legs)} событий (кф {total:.2f})")
    cur = conn.execute(
        "INSERT INTO bets (user_id, amount, total_odds, created_at) VALUES (?,?,?,?)", (uid, amount, total, now_iso())
    )
    bet_id = cur.lastrowid
    for ev_id, choice, odds in legs:
        conn.execute(
            "INSERT INTO bet_events (bet_id, event_id, choice, locked_odds) VALUES (?,?,?,?)", (bet_id, ev_id, choice, odds)
        )
    core.on_wager(conn, uid, amount, "express")
    core.quest_event(conn, uid, "match_bet", len(legs))
    return {"bet_id": bet_id, "total_odds": total}


def settle_event_core(conn, event_id, result):
    """Рассчитать матч. result: '1' | 'X' | '2' | 'void' (отмена — возврат ставок).
    Возвращает список уведомлений [(uid, text)] — отправить ПОСЛЕ commit."""
    result = str(result)
    if result not in ("1", "X", "2", "void"):
        raise HTTPException(400, "Неверный результат")
    ev = one(conn, "SELECT title, status FROM events WHERE id=?", (int(event_id),))
    if not ev:
        raise HTTPException(404, "Матч не найден")
    if ev[1] == "settled":
        raise HTTPException(400, "Матч уже рассчитан")
    title = ev[0]
    notes = []
    conn.execute("UPDATE events SET status='settled', result=? WHERE id=?", (result, event_id))

    # ---- ординары ----
    for pid, uid, points, choice, odds in all_(
        conn, "SELECT id, user_id, points, choice, locked_odds FROM predictions WHERE event_id=? AND status='pending'", (event_id,)
    ):
        if result == "void":
            adjust(conn, uid, points, "refund", f"Возврат (матч отменён): {title}", ref=f"pred:{pid}")
            conn.execute("UPDATE predictions SET status='void' WHERE id=?", (pid,))
            notes.append((uid, f"↩️ <b>Матч отменён</b>\n{title}\nСтавка <b>{money(points)} PTS</b> возвращена."))
        elif choice == result:
            win = int(points * odds)
            adjust(conn, uid, win, "win", f"Выигрыш: {title}", ref=f"pred:{pid}")
            conn.execute("UPDATE predictions SET status='won' WHERE id=?", (pid,))
            core.quest_event(conn, uid, "single_won", 1)
            core.bump(conn, uid, "single_won", 1)
            core.set_max(conn, uid, "biggest_win", win)
            notes.append((uid, f"✅ <b>Ставка сыграла!</b>\n{title}\nИсход: {CHOICE_RU.get(choice, choice)} · кф {odds:.2f}\nВыигрыш: <b>+{money(win)} PTS</b>"))
        else:
            conn.execute("UPDATE predictions SET status='lost' WHERE id=?", (pid,))
            notes.append((uid, f"❌ <b>Ставка не сыграла</b>\n{title}\nИсход: {CHOICE_RU.get(choice, choice)} · {money(points)} PTS"))

    # ---- экспрессы ----
    bet_ids = [
        r[0]
        for r in all_(
            conn,
            "SELECT DISTINCT be.bet_id FROM bet_events be JOIN bets b ON b.id=be.bet_id WHERE be.event_id=? AND b.status='pending'",
            (event_id,),
        )
    ]
    for bet_id in bet_ids:
        legs = all_(
            conn,
            "SELECT be.choice, be.locked_odds, e.status, e.result, e.title FROM bet_events be JOIN events e ON e.id=be.event_id WHERE be.bet_id=?",
            (bet_id,),
        )
        b = one(conn, "SELECT user_id, amount FROM bets WHERE id=?", (bet_id,))
        uid, amount = b
        lost_leg = next((l for l in legs if l[2] == "settled" and l[3] != "void" and l[0] != l[3]), None)
        if lost_leg:
            conn.execute("UPDATE bets SET status='lost' WHERE id=?", (bet_id,))
            notes.append((uid, f"❌ <b>Экспресс не сыграл</b>\nПодвёл матч: {lost_leg[4]}\nСтавка: {money(amount)} PTS"))
        elif all(l[2] == "settled" for l in legs):
            live = [l for l in legs if l[3] != "void"]
            if not live:
                adjust(conn, uid, amount, "refund", "Возврат экспресса (все матчи отменены)", ref=f"bet:{bet_id}")
                conn.execute("UPDATE bets SET status='void' WHERE id=?", (bet_id,))
                notes.append((uid, f"↩️ Экспресс отменён, ставка <b>{money(amount)} PTS</b> возвращена."))
            else:
                total = 1.0
                for l in live:
                    total *= l[1]
                win = int(amount * total)
                adjust(conn, uid, win, "win", f"Выигрыш экспресса (кф {total:.2f})", ref=f"bet:{bet_id}")
                conn.execute("UPDATE bets SET status='won', total_odds=? WHERE id=?", (round(total, 4), bet_id))
                core.quest_event(conn, uid, "express_won", 1)
                core.bump(conn, uid, "express_won", 1)
                core.set_max(conn, uid, "biggest_win", win)
                notes.append((uid, f"🚀 <b>Экспресс сыграл!</b>\nКоэффициент {total:.2f}\nВыигрыш: <b>+{money(win)} PTS</b>"))
    return notes


def odds_changed(conn, event_id, new):
    """Коэффициенты матча изменились: уведомить тех, кто подписан на «кэф вырос» (🔔).
    new = (odds_1, odds_x, odds_2). Возвращает [(uid, text)]."""
    ev = one(conn, "SELECT title FROM events WHERE id=?", (event_id,))
    if not ev:
        return []
    notes = []
    for uid, choice, last in all_(conn, "SELECT user_id, choice, last_odds FROM odds_watch WHERE event_id=?", (event_id,)):
        cur = _odds_of(new, choice)
        if not cur:
            continue
        if cur >= last * 1.03:
            notes.append((uid, f"📈 <b>Коэффициент вырос!</b>\n{ev[0]}\n{CHOICE_RU.get(choice, choice)}: {last:.2f} → <b>{cur:.2f}</b>"))
            conn.execute("UPDATE odds_watch SET last_odds=? WHERE user_id=? AND event_id=? AND choice=?", (cur, uid, event_id, choice))
        elif cur < last:
            conn.execute("UPDATE odds_watch SET last_odds=? WHERE user_id=? AND event_id=? AND choice=?", (cur, uid, event_id, choice))
    return notes


# ==========================================================================
#  ВЫКУП СТАВКИ (cash-out) и подробности ставки для карточки
# ==========================================================================
CASHOUT_MARGIN = 0.07  # «комиссия» выкупа: предложение = справедливая цена × (1 − 7%)


def _win_prob(choice, o1, ox, o2):
    """Вероятность исхода по ТЕКУЩИМ кэфам (с вычетом маржи БК: 1/кф нормируется на сумму)."""
    inv = {k: 1.0 / float(v) for k, v in (("1", o1), ("X", ox), ("2", o2)) if v}
    c = inv.get(str(choice))
    if not c:
        return None
    return min(0.97, c / sum(inv.values()))


def leg_status(ev_status, ev_result, choice):
    """won / lost / void / pending для одной ноги (ординар = одна нога)."""
    if ev_status == "settled":
        if ev_result == "void":
            return "void"
        return "won" if str(choice) == str(ev_result) else "lost"
    return "pending"


def single_offer(points, locked, choice, ev_status, starts_at, o1, ox, o2):
    """Сумма выкупа ординара или None, если выкуп сейчас недоступен."""
    if ev_status != "open" or _started(starts_at):
        return None
    p = _win_prob(choice, o1, ox, o2)
    if not p:
        return None
    offer = int(int(points) * float(locked) * p * (1 - CASHOUT_MARGIN))
    return offer if offer >= 1 else None


def express_offer(amount, legs):
    """Сумма выкупа экспресса. legs: [{choice, odds, ev_status, ev_result, starts_at, o1, ox, o2}].
    Уже сыгравшие ноги дают свой кф целиком, неразыгранные — вероятность по текущим кэфам."""
    mult, prob, pending = 1.0, 1.0, 0
    for l in legs:
        st = leg_status(l["ev_status"], l["ev_result"], l["choice"])
        if st == "lost":
            return None
        if st == "void":
            continue
        mult *= float(l["odds"])
        if st == "pending":
            if l["ev_status"] != "open" or _started(l["starts_at"]):
                return None
            p = _win_prob(l["choice"], l["o1"], l["ox"], l["o2"])
            if not p:
                return None
            prob *= p
            pending += 1
    if not pending:
        return None
    offer = int(int(amount) * mult * prob * (1 - CASHOUT_MARGIN))
    return offer if offer >= 1 else None


_LEG_SQL = (
    "e.title, e.sport, e.status, e.result, e.starts_at, e.odds_1, e.odds_x, e.odds_2"
)


def _leg_dict(choice, odds, row):
    title, sport, ev_status, ev_result, starts_at, o1, ox, o2 = row
    return {
        "title": title, "sport": sport, "choice": choice, "odds": odds,
        "ev_status": ev_status, "ev_result": ev_result, "starts_at": starts_at,
        "o1": o1, "ox": ox, "o2": o2, "status": leg_status(ev_status, ev_result, choice),
    }


def public_leg(l):
    return {"title": l["title"], "sport": l["sport"], "choice": l["choice"], "odds": l["odds"],
            "result": l["ev_result"], "status": l["status"]}


def history_for(conn, uid, limit=50):
    """История ставок игрока (ординары + экспрессы) с исходами по каждому событию и ценой выкупа."""
    items = []
    for r in all_(
        conn,
        f"SELECT p.id, p.choice, p.points, p.locked_odds, p.status, p.created_at, p.cashout, {_LEG_SQL} "
        "FROM predictions p JOIN events e ON p.event_id = e.id WHERE p.user_id = ? ORDER BY p.id DESC LIMIT ?",
        (uid, limit),
    ):
        pid, choice, points, odds, status, created, cashout = r[:7]
        leg = _leg_dict(choice, odds, r[7:])
        offer = single_offer(points, odds, choice, leg["ev_status"], leg["starts_at"], leg["o1"], leg["ox"], leg["o2"]) if status == "pending" else None
        items.append({
            "id": pid, "kind": "single", "choice": choice, "points": points, "locked_odds": odds,
            "status": status, "created_at": created, "title": leg["title"], "sport": leg["sport"],
            "result": leg["ev_result"], "legs": [public_leg(leg)], "cashout": cashout, "cashout_offer": offer,
        })
    bets = all_(
        conn, "SELECT id, amount, total_odds, status, created_at, cashout FROM bets WHERE user_id = ? ORDER BY id DESC LIMIT ?", (uid, limit)
    )
    legs_by_bet = {}
    if bets:
        marks = ",".join("?" * len(bets))
        for r in all_(
            conn,
            f"SELECT be.bet_id, be.choice, be.locked_odds, {_LEG_SQL} FROM bet_events be JOIN events e ON be.event_id = e.id "
            f"WHERE be.bet_id IN ({marks}) ORDER BY be.id",
            tuple(b[0] for b in bets),
        ):
            legs_by_bet.setdefault(r[0], []).append(_leg_dict(r[1], r[2], r[3:]))
    for bid, amount, total, status, created, cashout in bets:
        legs = legs_by_bet.get(bid, [])
        offer = express_offer(amount, legs) if status == "pending" else None
        items.append({
            "id": bid, "kind": "express", "choice": "Экспресс", "points": amount, "locked_odds": total,
            "status": status, "created_at": created, "title": ", ".join(l["title"] for l in legs),
            "sport": "Экспресс", "result": None, "legs": [public_leg(l) for l in legs],
            "cashout": cashout, "cashout_offer": offer,
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]


def _load_bet(conn, uid, kind, bet_id):
    """Возвращает (статус, ставка, предложение выкупа, название). Бросает 404, если ставка не найдена."""
    bet_id = int(bet_id)
    if kind == "express":
        b = one(conn, "SELECT amount, status FROM bets WHERE id=? AND user_id=?", (bet_id, uid))
        if not b:
            raise HTTPException(404, "Ставка не найдена")
        legs = [
            _leg_dict(r[0], r[1], r[2:])
            for r in all_(
                conn,
                f"SELECT be.choice, be.locked_odds, {_LEG_SQL} FROM bet_events be JOIN events e ON be.event_id = e.id WHERE be.bet_id=?",
                (bet_id,),
            )
        ]
        return b[1], b[0], express_offer(b[0], legs), f"Экспресс из {len(legs)} событий"
    r = one(
        conn,
        f"SELECT p.points, p.status, p.locked_odds, p.choice, {_LEG_SQL} FROM predictions p JOIN events e ON p.event_id=e.id "
        "WHERE p.id=? AND p.user_id=?",
        (bet_id, uid),
    )
    if not r:
        raise HTTPException(404, "Ставка не найдена")
    leg = _leg_dict(r[3], r[2], r[4:])
    offer = single_offer(r[0], r[2], r[3], leg["ev_status"], leg["starts_at"], leg["o1"], leg["ox"], leg["o2"])
    return r[1], r[0], offer, leg["title"]


def get_offer(conn, uid, kind, bet_id):
    status, _, offer, _ = _load_bet(conn, uid, kind, bet_id)
    return offer if status == "pending" else None


def cashout(conn, uid, kind, bet_id, expected=None):
    """Выкупить ставку: ставка закрывается, игрок получает текущее предложение.
    expected — сумма, которую игрок видел на экране; если цена упала — вернём 409 с новой ценой."""
    kind = "express" if kind == "express" else "single"
    status, _, offer, title = _load_bet(conn, uid, kind, bet_id)
    if status != "pending":
        raise HTTPException(400, "Ставка уже рассчитана или выкуплена")
    if not offer:
        raise HTTPException(400, "Выкуп сейчас недоступен (матч начался или закрыт)")
    if expected is not None and offer < int(expected):
        raise HTTPException(409, f"Цена выкупа изменилась: теперь {money(offer)} PTS. Откройте ставку заново.")
    table = "bets" if kind == "express" else "predictions"
    cur = conn.execute(
        f"UPDATE {table} SET status='cashed', cashout=? WHERE id=? AND user_id=? AND status='pending'", (offer, int(bet_id), uid)
    )
    if core.changed(cur, conn) != 1:
        raise HTTPException(400, "Ставка уже рассчитана или выкуплена")
    bal = adjust(conn, uid, offer, "cashout", f"Выкуп ставки: {title}", ref=f"{table}:{bet_id}")
    return {"amount": offer, "balance": bal}
