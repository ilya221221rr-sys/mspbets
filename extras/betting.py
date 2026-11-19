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
