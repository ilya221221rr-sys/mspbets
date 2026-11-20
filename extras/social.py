"""
extras/social.py — профиль, уровни/VIP, ежедневный бонус и streak, задания, достижения,
рефералка, турниры, журнал операций, лента и копирование ставок, подписка на кэфы.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

from fastapi import APIRouter, Body, HTTPException, Request

from . import betting, core
from .core import ACH, QUESTS, all_, auth, db, money, now_iso, one, touch_user, user_lock

log = logging.getLogger("mspbets.social")
router = APIRouter(prefix="/api/v2")

DAILY_CYCLE = [b * core.REWARD_MULT for b in (50, 75, 100, 125, 150, 200, 400)]  # награды по дням серии (цикл 7 дней), x70


def _level_info(xp):
    lvl = core.level_for(xp)
    base, nxt = core.xp_for_level(lvl), core.xp_for_level(lvl + 1)
    return {"level": lvl, "xp": xp, "xp_base": base, "xp_next": nxt, "rank": core.rank_for(lvl), "vip": core.vip_for(lvl)}


# Картинки-аватары (файлы лежат в web/avatars/<key>.webp). Доступны всем игрокам.
AVATARS = [
    ("pudge", "Pudge"), ("axe", "Axe"), ("tidehunter", "Tidehunter"), ("wolf", "Кибер-волк"), ("astrocat", "Кот-космонавт"),
    ("dragon", "Золотой дракон"), ("robot", "Робот"), ("fox", "Самурай-лис"), ("skull", "Король-череп"),
]
AVATAR_KEYS = {a[0] for a in AVATARS}


def unlocked_frames(level, ach_keys):
    fr = [r[3] for r in core.RANKS if level >= r[0]]
    if "streak_7" in ach_keys:
        fr.append("streak")
    return fr


def _daily_status(p):
    t = core.today()
    y = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    last, streak = p["last_daily"], p["streak"]
    claimed = last == t
    if not claimed and last != y:
        streak_now = 0  # серия прервана
    else:
        streak_now = streak
    nxt_streak = streak_now + (0 if claimed else 1)
    idx = (max(nxt_streak, 1) - 1) % 7
    return {"claimed": claimed, "streak": streak_now, "next_index": idx, "cycle": DAILY_CYCLE, "best": p["best_streak"]}


def _profile_row(conn, uid):
    r = one(conn, "SELECT xp, wagered, bets_count, streak, best_streak, last_daily, frame, badge, bio, notify, hide_bets, referrer_id, ref_paid, COALESCE(avatar, '') FROM profiles WHERE user_id=?", (uid,))
    keys = ["xp", "wagered", "bets_count", "streak", "best_streak", "last_daily", "frame", "badge", "bio", "notify", "hide_bets", "referrer_id", "ref_paid", "avatar"]
    return dict(zip(keys, r))


def _start_param(request):
    try:
        return dict(parse_qsl(request.headers.get("X-Telegram-Init-Data", ""))).get("start_param", "")
    except Exception:
        return ""


@router.get("/me")
def me(request: Request):
    user = auth(request)
    uid = int(user["id"])
    sp = _start_param(request)
    with db() as conn:
        touch_user(conn, user)
        if sp.startswith("ref_") and sp[4:].isdigit():
            core.register_referral(conn, uid, int(sp[4:]))
        p = _profile_row(conn, uid)
        bal = core.balance_of(conn, uid)
        ach = {r[0] for r in all_(conn, "SELECT key FROM achievements WHERE user_id=?", (uid,))}
        day = core.today()
        qrows = {r[0]: (r[1], r[2]) for r in all_(conn, "SELECT key, progress, claimed FROM quest_progress WHERE user_id=? AND day=?", (uid, day))}
    info = _level_info(p["xp"])
    claimable = sum(1 for k in core.quests_for(uid, day) if qrows.get(k, (0, 0))[0] >= QUESTS[k][2] and not qrows.get(k, (0, 0))[1])
    d = _daily_status(p)
    return {
        "user_id": uid, "name": core.display_name(user), "first_name": user.get("first_name"), "username": user.get("username"), "balance": bal, **info,
        "frame": p["frame"], "avatar": p["avatar"], "avatars": [{"key": k, "title": t} for k, t in AVATARS], "badge": p["badge"], "bio": p["bio"], "notify": bool(p["notify"]), "hide_bets": bool(p["hide_bets"]),
        "frames": unlocked_frames(info["level"], ach), "daily": d, "quests_claimable": claimable,
        "ref_link": f"https://t.me/{core.ctx.bot_username}?start=ref_{uid}", "is_admin": uid in core.ctx.admin_ids,
        "stats": {"wagered": p["wagered"], "bets": p["bets_count"]},
    }


@router.post("/profile/settings")
def settings(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        touch_user(conn, user)
        p = _profile_row(conn, uid)
        ach = {r[0] for r in all_(conn, "SELECT key FROM achievements WHERE user_id=?", (uid,))}
        lvl = core.level_for(p["xp"])
        if "frame" in body:
            f = str(body["frame"] or "")
            if f and f not in unlocked_frames(lvl, ach):
                raise HTTPException(400, "Рамка ещё не открыта")
            conn.execute("UPDATE profiles SET frame=? WHERE user_id=?", (f, uid))
        if "avatar" in body:
            a = str(body["avatar"] or "")
            if a and a not in AVATAR_KEYS:
                raise HTTPException(400, "Такого аватара нет")
            conn.execute("UPDATE profiles SET avatar=? WHERE user_id=?", (a, uid))
        if "badge" in body:
            b = str(body["badge"] or "")
            if b and b not in ach:
                raise HTTPException(400, "Бейдж ещё не получен")
            conn.execute("UPDATE profiles SET badge=? WHERE user_id=?", (b, uid))
        if "bio" in body:
            conn.execute("UPDATE profiles SET bio=? WHERE user_id=?", (str(body["bio"] or "")[:80], uid))
        if "notify" in body:
            conn.execute("UPDATE profiles SET notify=? WHERE user_id=?", (1 if body["notify"] else 0, uid))
        if "hide_bets" in body:
            conn.execute("UPDATE profiles SET hide_bets=? WHERE user_id=?", (1 if body["hide_bets"] else 0, uid))
    return {"ok": True}


# --------------------------- Ежедневный бонус ---------------------------
@router.post("/daily/claim")
def daily_claim(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        p = _profile_row(conn, uid)
        t = core.today()
        y = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        if p["last_daily"] == t:
            raise HTTPException(400, "Бонус уже получен сегодня")
        streak = p["streak"] + 1 if p["last_daily"] == y else 1
        cur = conn.execute(
            "UPDATE profiles SET streak=?, best_streak=MAX(best_streak, ?), last_daily=? WHERE user_id=? AND (last_daily IS NULL OR last_daily<>?)",
            (streak, streak, t, uid, t),
        )
        if core.changed(cur, conn) != 1:
            raise HTTPException(400, "Бонус уже получен сегодня")
        base = DAILY_CYCLE[(streak - 1) % 7]
        vip = core.vip_for(core.level_for(p["xp"]))
        reward = int(base * (1 + 0.25 * vip))
        bal = core.adjust(conn, uid, reward, "daily", f"Ежедневный бонус · серия {streak}", ref=t)
        core.set_max(conn, uid, "streak", streak)
    return {"ok": True, "reward": reward, "streak": streak, "vip_bonus": vip, "balance": bal, "unlocked": core.pop_unlocked()}


# --------------------------- Задания ---------------------------
@router.get("/quests")
def quests(request: Request):
    user = auth(request)
    uid = int(user["id"])
    day = core.today()
    with db() as conn:
        rows = {r[0]: (r[1], r[2]) for r in all_(conn, "SELECT key, progress, claimed FROM quest_progress WHERE user_id=? AND day=?", (uid, day))}
    out = []
    for k in core.quests_for(uid, day):
        title, ev, target, reward = QUESTS[k]
        pr, cl = rows.get(k, (0, 0))
        out.append({"key": k, "title": title, "target": target, "progress": min(pr, target), "reward": reward, "claimed": bool(cl)})
    return out


@router.post("/quests/claim")
def quest_claim(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    key = str(body.get("key"))
    day = core.today()
    if key not in core.quests_for(uid, day):
        raise HTTPException(400, "Задание недоступно")
    title, ev, target, reward = QUESTS[key]
    with user_lock(uid), db() as conn:
        cur = conn.execute(
            "UPDATE quest_progress SET claimed=1 WHERE user_id=? AND day=? AND key=? AND claimed=0 AND progress>=?", (uid, day, key, target)
        )
        if core.changed(cur, conn) != 1:
            raise HTTPException(400, "Задание ещё не выполнено или награда получена")
        bal = core.adjust(conn, uid, reward, "quest", f"Задание: {title}", ref=key)
    return {"ok": True, "reward": reward, "balance": bal}


# --------------------------- Достижения ---------------------------
@router.get("/achievements")
def achievements(request: Request, uid: int = 0):
    user = auth(request)
    target = uid or int(user["id"])
    with db() as conn:
        got = {r[0]: r[1] for r in all_(conn, "SELECT key, unlocked_at FROM achievements WHERE user_id=?", (target,))}
        cnt = {r[0]: r[1] for r in all_(conn, "SELECT key, value FROM counters WHERE user_id=?", (target,))}
        p = one(conn, "SELECT xp, badge FROM profiles WHERE user_id=?", (target,))
    lvl = core.level_for(p[0]) if p else 1
    cnt["level"] = max(cnt.get("level", 0), lvl)
    out = []
    for k in ACH:
        i = core.ach_info(k)
        i.update(unlocked=k in got, at=got.get(k), progress=min(cnt.get(i["counter"], 0), i["target"]))
        out.append(i)
    return {"items": out, "badge": p[1] if p else ""}


# --------------------------- Журнал операций ---------------------------
@router.get("/ledger")
def ledger(request: Request, before: int = 0, limit: int = 40):
    user = auth(request)
    limit = max(1, min(limit, 100))
    with db() as conn:
        if before:
            rows = all_(conn, "SELECT id, amount, balance_after, kind, note, created_at FROM transactions WHERE user_id=? AND id<? ORDER BY id DESC LIMIT ?", (int(user["id"]), before, limit))
        else:
            rows = all_(conn, "SELECT id, amount, balance_after, kind, note, created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user["id"]), limit))
    return [{"id": r[0], "amount": r[1], "balance_after": r[2], "kind": r[3], "note": r[4], "at": r[5]} for r in rows]


# --------------------------- Рефералка ---------------------------
@router.get("/referral")
def referral(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        rows = all_(
            conn,
            "SELECT p.user_id, u.first_name, p.bets_count, p.wagered, p.ref_paid FROM profiles p LEFT JOIN users u ON u.user_id=p.user_id WHERE p.referrer_id=? ORDER BY p.created_at DESC LIMIT 50",
            (uid,),
        )
        earned = one(conn, "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND kind='referral'", (uid,))[0]
    return {
        "link": f"https://t.me/{core.ctx.bot_username}?start=ref_{uid}",
        "reward_you": core.REF_REWARD_REFERRER, "reward_friend": core.REF_REWARD_FRIEND,
        "need_bets": core.REF_MIN_BETS, "need_wager": core.REF_MIN_WAGER, "earned": earned,
        "friends": [{"uid": r[0], "name": r[1] or "Игрок", "bets": r[2], "wagered": r[3], "paid": bool(r[4])} for r in rows],
    }


# --------------------------- Турниры ---------------------------
TOURNAMENTS = [  # kind, название, мин. уровень, призовой фонд
    ("weekly_open", "Недельный забег", 1, 5000 * core.REWARD_MULT),
    ("weekly_gold", "Золотой кубок · VIP", 20, 25000 * core.REWARD_MULT),
    ("weekly_diamond", "Алмазный клуб · закрытый", 45, 80000 * core.REWARD_MULT),
]
PRIZE_SPLIT = [0.5, 0.3, 0.2]


def tournament_tick():
    """Создаёт турниры текущей недели и выплачивает призы завершённых (вызывать периодически)."""
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = monday + timedelta(days=7)
    notes = []
    with db() as conn:
        for kind, name, lvl, pool in TOURNAMENTS:
            if not one(conn, "SELECT 1 FROM tournaments WHERE kind=? AND starts_at=?", (kind, monday.isoformat())):
                conn.execute(
                    "INSERT INTO tournaments (kind, name, min_level, prize_pool, starts_at, ends_at) VALUES (?,?,?,?,?,?)",
                    (kind, name, lvl, pool, monday.isoformat(), end.isoformat()),
                )
            else:  # турнир недели создан до повышения наград — подтягиваем фонд до актуального
                conn.execute(
                    "UPDATE tournaments SET prize_pool=? WHERE kind=? AND starts_at=? AND status='active' AND prize_pool<>?",
                    (pool, kind, monday.isoformat(), pool),
                )
        for tid, name, pool in all_(conn, "SELECT id, name, prize_pool FROM tournaments WHERE status='active' AND ends_at<=?", (now.isoformat(),)):
            cur = conn.execute("UPDATE tournaments SET status='paid' WHERE id=? AND status='active'", (tid,))
            if core.changed(cur, conn) != 1:
                continue
            top = all_(conn, "SELECT user_id, score FROM tournament_scores WHERE tournament_id=? AND score>0 ORDER BY score DESC LIMIT 3", (tid,))
            for place, (uid, score) in enumerate(top):
                prize = int(pool * PRIZE_SPLIT[place])
                core.adjust(conn, uid, prize, "tournament", f"Турнир «{name}»: {place + 1} место", ref=f"tour:{tid}")
                notes.append((uid, f"🏆 <b>Турнир «{name}» завершён</b>\nТвоё место: <b>{place + 1}</b>\nПриз: <b>+{money(prize)} PTS</b>"))
        core.notify_many(conn, notes)
    core.invalidate_tournaments()


@router.get("/tournaments")
def tournaments(request: Request):
    user = auth(request)
    uid = int(user["id"])
    out = []
    with db() as conn:
        lvl = core.level_for((one(conn, "SELECT xp FROM profiles WHERE user_id=?", (uid,)) or [0])[0])
        rows = all_(conn, "SELECT id, kind, name, min_level, prize_pool, ends_at, status FROM tournaments ORDER BY status='active' DESC, id DESC LIMIT 6")
        for tid, kind, name, ml, pool, ends, status in rows:
            mine = one(conn, "SELECT score FROM tournament_scores WHERE tournament_id=? AND user_id=?", (tid, uid))
            top = all_(
                conn,
                "SELECT s.user_id, u.first_name, s.score FROM tournament_scores s LEFT JOIN users u ON u.user_id=s.user_id WHERE s.tournament_id=? AND s.score>0 ORDER BY s.score DESC LIMIT 10",
                (tid,),
            )
            rank = None
            if mine and mine[0] > 0:
                rank = one(conn, "SELECT COUNT(*)+1 FROM tournament_scores WHERE tournament_id=? AND score>?", (tid, mine[0]))[0]
            out.append({
                "id": tid, "kind": kind, "name": name, "min_level": ml, "prize_pool": pool, "ends_at": ends, "status": status,
                "joined": bool(mine), "score": mine[0] if mine else 0, "rank": rank, "locked": lvl < ml, "closed": ml > 1,
                "prizes": [int(pool * s) for s in PRIZE_SPLIT],
                "top": [{"uid": r[0], "name": r[1] or "Игрок", "score": r[2]} for r in top],
            })
    return out


@router.post("/tournaments/join")
def tournament_join(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    tid = int(body.get("id", 0))
    with db() as conn:
        touch_user(conn, user)
        t = one(conn, "SELECT min_level, status, ends_at FROM tournaments WHERE id=?", (tid,))
        if not t or t[1] != "active" or t[2] <= now_iso():
            raise HTTPException(400, "Турнир недоступен")
        lvl = core.level_for(one(conn, "SELECT xp FROM profiles WHERE user_id=?", (uid,))[0])
        if lvl < t[0]:
            raise HTTPException(403, f"Закрытый турнир: нужен {t[0]} уровень")
        conn.execute("INSERT OR IGNORE INTO tournament_scores (tournament_id, user_id, score) VALUES (?,?,0)", (tid, uid))
    return {"ok": True}


# --------------------------- Профили и копирование ставок ---------------------------
@router.get("/player/{uid}")
def player(uid: int, request: Request):
    viewer = auth(request)
    with db() as conn:
        u = one(conn, "SELECT first_name, username, balance, created_at FROM users WHERE user_id=?", (uid,))
        p = one(conn, "SELECT xp, wagered, bets_count, streak, best_streak, frame, badge, bio, hide_bets, COALESCE(avatar, '') FROM profiles WHERE user_id=?", (uid,))
        if not u:
            raise HTTPException(404, "Игрок не найден")
        xp = p[0] if p else 0
        ach = all_(conn, "SELECT key FROM achievements WHERE user_id=?", (uid,))
        w = one(conn, "SELECT SUM(status='won'), SUM(status='lost'), COUNT(*) FROM predictions WHERE user_id=?", (uid,))
        best = one(conn, "SELECT value FROM counters WHERE user_id=? AND key='biggest_win'", (uid,))
        bets = []
        hidden = bool(p and p[8])
        if not hidden:
            for r in all_(
                conn,
                "SELECT p.id, e.id, e.title, p.choice, p.locked_odds, p.points, p.status, e.status FROM predictions p JOIN events e ON e.id=p.event_id WHERE p.user_id=? ORDER BY p.id DESC LIMIT 12",
                (uid,),
            ):
                bets.append({"pred_id": r[0], "event_id": r[1], "title": r[2], "choice": r[3], "odds": r[4], "points": r[5], "status": r[6], "open": r[7] == "open"})
    info = _level_info(xp)
    won, lost = int(w[0] or 0), int(w[1] or 0)
    return {
        "uid": uid, "name": u[0] or u[1] or "Игрок", "username": u[1], "since": u[3], **info,
        "frame": p[5] if p else "", "avatar": p[9] if p else "", "badge": p[6] if p else "", "bio": p[7] if p else "",
        "stats": {"bets": p[2] if p else 0, "wagered": p[1] if p else 0, "streak": p[3] if p else 0, "best_streak": p[4] if p else 0,
                  "won": won, "lost": lost, "winrate": round(won * 100 / (won + lost), 1) if won + lost else None, "biggest_win": best[0] if best else 0},
        "badges": [core.ach_info(a[0]) for a in ach if a[0] in ACH][:24],
        "bets": bets, "hidden": hidden, "me": uid == int(viewer["id"]),
    }


@router.get("/social/feed")
def feed(request: Request):
    auth(request)
    with db() as conn:
        rows = all_(
            conn,
            "SELECT p.id, p.user_id, u.first_name, p.event_id, e.title, p.choice, p.locked_odds, p.points, e.odds_1, e.odds_x, e.odds_2, e.sport, pr.xp, COALESCE(pr.avatar, ''), COALESCE(pr.frame, '') "
            "FROM predictions p JOIN events e ON e.id=p.event_id JOIN users u ON u.user_id=p.user_id "
            "LEFT JOIN profiles pr ON pr.user_id=p.user_id "
            "WHERE e.status='open' AND p.status='pending' AND COALESCE(pr.hide_bets,0)=0 ORDER BY p.id DESC LIMIT 30",
        )
    out = []
    for r in rows:
        now = {"1": r[8], "X": r[9], "2": r[10]}.get(r[5])
        out.append({"pred_id": r[0], "uid": r[1], "name": r[2] or "Игрок", "event_id": r[3], "title": r[4], "choice": r[5], "odds": r[6],
                    "points": r[7], "odds_now": now, "sport": r[11], "level": core.level_for(r[12] or 0), "avatar": r[13], "frame": r[14]})
    return out


@router.post("/social/copy")
def copy_bet(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    pid = int(body.get("pred_id", 0))
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        r = one(
            conn,
            "SELECT p.user_id, p.event_id, p.choice, COALESCE(pr.hide_bets,0) FROM predictions p LEFT JOIN profiles pr ON pr.user_id=p.user_id WHERE p.id=?",
            (pid,),
        )
        if not r or r[3]:
            raise HTTPException(404, "Ставка недоступна")
        if r[0] == uid:
            raise HTTPException(400, "Это ваша ставка")
        res = betting.place_single(conn, uid, r[1], r[2], body.get("amount"), note_prefix="Копия ставки")
        bal = core.balance_of(conn, uid)
        core.notify_many(conn, [(r[0], f"👥 Игрок <b>{core.display_name(user)}</b> скопировал твою ставку.")])
    return {"ok": True, "odds": res["odds"], "balance": bal, "unlocked": core.pop_unlocked()}


# --------------------------- Подписка на рост кэфов ---------------------------
@router.get("/watch")
def watch_list(request: Request):
    user = auth(request)
    with db() as conn:
        rows = all_(conn, "SELECT event_id, choice FROM odds_watch WHERE user_id=?", (int(user["id"]),))
    return [{"event_id": r[0], "choice": r[1]} for r in rows]


@router.post("/watch")
def watch_toggle(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    ev_id, choice, on = int(body.get("event_id", 0)), str(body.get("choice")), bool(body.get("on", True))
    with db() as conn:
        touch_user(conn, user)
        if on:
            ev = one(conn, "SELECT odds_1, odds_x, odds_2, status FROM events WHERE id=?", (ev_id,))
            odds = {"1": ev[0], "X": ev[1], "2": ev[2]}.get(choice) if ev else None
            if not ev or ev[3] != "open" or not odds:
                raise HTTPException(400, "Матч недоступен")
            n = one(conn, "SELECT COUNT(*) FROM odds_watch WHERE user_id=?", (uid,))[0]
            if n >= 30:
                raise HTTPException(400, "Максимум 30 подписок")
            conn.execute("INSERT OR REPLACE INTO odds_watch (user_id, event_id, choice, last_odds) VALUES (?,?,?,?)", (uid, ev_id, choice, odds))
        else:
            conn.execute("DELETE FROM odds_watch WHERE user_id=? AND event_id=? AND choice=?", (uid, ev_id, choice))
    return {"ok": True, "on": on}


async def scheduler():
    """Фоновая задача: турниры (раз в 5 минут)."""
    while True:
        try:
            await asyncio.to_thread(tournament_tick)
        except Exception as e:
            log.exception("tournament tick failed: %s", e)
        await asyncio.sleep(300)
