"""
extras/support.py — поддержка: игрок пишет в приложении (или прямо боту), админ получает
обращение в Telegram-бота И в админку, и отвечает оттуда же.

Игрок:
  POST /api/v2/support/send       {"text": "..."}   — написать в поддержку
  GET  /api/v2/support/thread     — переписка (и отметка «прочитано»)
  GET  /api/v2/support/unread     — сколько непрочитанных ответов
Админ (приложение):
  GET  /api/v2/admin/support                 — список обращений (новые сверху)
  GET  /api/v2/admin/support/{id}            — переписка по обращению
  POST /api/v2/admin/support/reply           — {"ticket_id": 1, "text": "..."} ответить игроку
  POST /api/v2/admin/support/close           — {"ticket_id": 1} закрыть обращение
Админ (бот):
  • reply (ответ) на пересланное ботом обращение — ответ уйдёт игроку;
  • /reply <номер> текст — ответить по номеру;  /close <номер> — закрыть;  /tickets — открытые.
Любой обычный текст, который игрок пишет боту, тоже становится обращением в поддержку.
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import all_, auth, db, now_iso, one, touch_user

log = logging.getLogger("mspbets.support")
router = APIRouter(prefix="/api/v2")

TEXT_MAX = 1500
RATE_LIMIT = 8        # сообщений игрока ...
RATE_WINDOW_MIN = 10  # ... за столько минут


# --------------------------------------------------------------------------
# Работа с БД
# --------------------------------------------------------------------------
def _clean(text):
    text = (text or "").replace("\r", "").strip()
    if not text:
        raise HTTPException(400, "Напишите сообщение")
    return text[:TEXT_MAX]


def _need_admin(request):
    user = auth(request)
    uid = int(user["id"])
    if uid not in core.ctx.admin_ids:
        raise HTTPException(403, "Только для админа")
    return uid


def _person(conn, uid):
    r = one(conn, "SELECT first_name, username FROM users WHERE user_id=?", (uid,))
    return (r[0] or "Игрок", r[1] or "") if r else ("Игрок", "")


def add_user_message(conn, uid, text):
    """Сообщение игрока: в открытое обращение или в новое. -> (ticket_id, is_new)."""
    since = (datetime.now(timezone.utc) - timedelta(minutes=RATE_WINDOW_MIN)).isoformat()
    n = one(
        conn,
        "SELECT COUNT(*) FROM support_messages m JOIN support_tickets t ON t.id=m.ticket_id "
        "WHERE t.user_id=? AND m.sender='user' AND m.created_at>=?",
        (uid, since),
    )[0]
    if n >= RATE_LIMIT:
        raise HTTPException(429, "Слишком часто. Подождите пару минут и напишите снова.")
    now = now_iso()
    row = one(conn, "SELECT id FROM support_tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1", (uid,))
    is_new = row is None
    if is_new:
        tid = conn.execute(
            "INSERT INTO support_tickets (user_id, status, created_at, updated_at, unread_admin, unread_user) "
            "VALUES (?, 'open', ?, ?, 0, 0)",
            (uid, now, now),
        ).lastrowid
    else:
        tid = row[0]
    conn.execute(
        "INSERT INTO support_messages (ticket_id, sender, text, created_at) VALUES (?, 'user', ?, ?)", (tid, text, now)
    )
    conn.execute("UPDATE support_tickets SET updated_at=?, unread_admin=unread_admin+1 WHERE id=?", (now, tid))
    return tid, is_new


def add_admin_message(conn, ticket_id, admin_id, text):
    """Ответ админа. -> user_id игрока."""
    t = one(conn, "SELECT user_id FROM support_tickets WHERE id=?", (ticket_id,))
    if not t:
        raise HTTPException(404, "Обращение не найдено")
    now = now_iso()
    conn.execute(
        "INSERT INTO support_messages (ticket_id, sender, admin_id, text, created_at) VALUES (?, 'admin', ?, ?, ?)",
        (ticket_id, admin_id, text, now),
    )
    conn.execute(
        "UPDATE support_tickets SET status='open', updated_at=?, unread_admin=0, unread_user=unread_user+1 WHERE id=?",
        (now, ticket_id),
    )
    return int(t[0])


def close_ticket(conn, ticket_id):
    t = one(conn, "SELECT user_id, status FROM support_tickets WHERE id=?", (ticket_id,))
    if not t:
        raise HTTPException(404, "Обращение не найдено")
    conn.execute("UPDATE support_tickets SET status='closed', unread_admin=0, updated_at=? WHERE id=?", (now_iso(), ticket_id))
    return int(t[0]), t[1]


def _msg(r):
    return {"id": r[0], "ticket_id": r[1], "from": r[2], "text": r[3], "at": r[4]}


# --------------------------------------------------------------------------
# Уведомления (админам — в бота с привязкой к обращению; игроку — в бота)
# --------------------------------------------------------------------------
def _save_map(chat_id, message_id, ticket_id):
    try:
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO support_tg_map (chat_id, message_id, ticket_id) VALUES (?,?,?)",
                (chat_id, message_id, ticket_id),
            )
    except Exception as e:
        log.warning("support map save failed: %s", e)


async def _forward(ticket_id, uid, name, username, text, is_new):
    """Каждому админу — в бота. Ответ (reply) на это сообщение уйдёт игроку."""
    bot = core.ctx.bot
    if not bot:
        return
    who = html.escape(name) + (f" (@{html.escape(username)})" if username else "")
    head = "📩 <b>Новое обращение" if is_new else "💬 <b>Сообщение в обращении"
    body = (
        f"{head} #{ticket_id}</b>\n"
        f"👤 {who} · ID <code>{uid}</code>\n\n"
        f"{html.escape(text)}\n\n"
        f"↩️ <i>Ответьте (reply) на это сообщение — ответ уйдёт игроку. "
        f"Или: /reply {ticket_id} текст · /close {ticket_id}</i>"
    )
    for aid in core.ctx.admin_ids:
        try:
            m = await bot.send_message(chat_id=aid, text=body, parse_mode="HTML", disable_web_page_preview=True)
            await asyncio.to_thread(_save_map, aid, m.message_id, ticket_id)
        except Exception as e:
            log.warning("support forward to %s failed: %s", aid, e)


def forward_threadsafe(ticket_id, uid, name, username, text, is_new):
    if not core.ctx.loop:
        return
    try:
        asyncio.run_coroutine_threadsafe(_forward(ticket_id, uid, name, username, text, is_new), core.ctx.loop)
    except Exception:
        pass


def _notify_reply(uid, ticket_id, text):
    core.notify(
        uid,
        f"💬 <b>Ответ поддержки</b> · обращение #{ticket_id}\n\n{html.escape(text)}\n\n"
        "<i>Чтобы ответить — просто напишите сюда или откройте приложение: Профиль → Поддержка.</i>",
    )


# --------------------------------------------------------------------------
# API игрока
# --------------------------------------------------------------------------
@router.post("/support/send")
def support_send(request: Request, body: dict = Body(...)):
    user = auth(request)
    text = _clean(str(body.get("text", "")))
    with db() as conn:
        uid = touch_user(conn, user)
        tid, is_new = add_user_message(conn, uid, text)
        name, username = _person(conn, uid)
    forward_threadsafe(tid, uid, name, username, text, is_new)
    return {"ok": True, "ticket_id": tid, "message": "Обращение отправлено. Ответ придёт сюда и в бота."}


@router.get("/support/thread")
def support_thread(request: Request):
    user = auth(request)
    with db() as conn:
        uid = touch_user(conn, user)
        rows = all_(
            conn,
            "SELECT m.id, m.ticket_id, m.sender, m.text, m.created_at FROM support_messages m "
            "JOIN support_tickets t ON t.id=m.ticket_id WHERE t.user_id=? ORDER BY m.id DESC LIMIT 80",
            (uid,),
        )
        conn.execute("UPDATE support_tickets SET unread_user=0 WHERE user_id=? AND unread_user>0", (uid,))
        open_t = one(conn, "SELECT id FROM support_tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1", (uid,))
    return {"messages": [_msg(r) for r in reversed(rows)], "open_ticket": open_t[0] if open_t else None}


@router.get("/support/unread")
def support_unread(request: Request):
    user = auth(request)
    with db() as conn:
        n = one(conn, "SELECT COALESCE(SUM(unread_user),0) FROM support_tickets WHERE user_id=?", (int(user["id"]),))[0]
    return {"unread": int(n or 0)}


# --------------------------------------------------------------------------
# API админа
# --------------------------------------------------------------------------
@router.get("/admin/support")
def admin_list(request: Request, status: str = "all"):
    _need_admin(request)
    where = "WHERE t.status='open'" if status == "open" else ""
    with db() as conn:
        rows = all_(
            conn,
            "SELECT t.id, t.user_id, t.status, t.updated_at, t.unread_admin, u.first_name, u.username, "
            "(SELECT text FROM support_messages WHERE ticket_id=t.id ORDER BY id DESC LIMIT 1), "
            "(SELECT sender FROM support_messages WHERE ticket_id=t.id ORDER BY id DESC LIMIT 1) "
            f"FROM support_tickets t LEFT JOIN users u ON u.user_id=t.user_id {where} "
            "ORDER BY CASE WHEN t.status='open' THEN 0 ELSE 1 END, t.updated_at DESC LIMIT 100",
        )
    return {
        "items": [
            {"id": r[0], "user_id": r[1], "status": r[2], "updated_at": r[3], "unread": r[4],
             "name": r[5] or "Игрок", "username": r[6] or "", "last": (r[7] or "")[:120], "last_from": r[8]}
            for r in rows
        ],
        "unread_total": sum(int(r[4] or 0) for r in rows if r[2] == "open"),
    }


@router.get("/admin/support/{ticket_id}")
def admin_thread(ticket_id: int, request: Request):
    _need_admin(request)
    with db() as conn:
        t = one(
            conn,
            "SELECT t.id, t.user_id, t.status, u.first_name, u.username FROM support_tickets t "
            "LEFT JOIN users u ON u.user_id=t.user_id WHERE t.id=?",
            (ticket_id,),
        )
        if not t:
            raise HTTPException(404, "Обращение не найдено")
        rows = all_(
            conn,
            "SELECT id, ticket_id, sender, text, created_at FROM support_messages WHERE ticket_id=? ORDER BY id LIMIT 300",
            (ticket_id,),
        )
        conn.execute("UPDATE support_tickets SET unread_admin=0 WHERE id=?", (ticket_id,))
    return {
        "ticket": {"id": t[0], "user_id": t[1], "status": t[2], "name": t[3] or "Игрок", "username": t[4] or ""},
        "messages": [_msg(r) for r in rows],
    }


@router.post("/admin/support/reply")
def admin_reply(request: Request, body: dict = Body(...)):
    aid = _need_admin(request)
    text = _clean(str(body.get("text", "")))
    tid = int(body.get("ticket_id", 0))
    with db() as conn:
        uid = add_admin_message(conn, tid, aid, text)
    _notify_reply(uid, tid, text)
    return {"ok": True, "message": "Ответ отправлен игроку"}


@router.post("/admin/support/close")
def admin_close(request: Request, body: dict = Body(...)):
    _need_admin(request)
    tid = int(body.get("ticket_id", 0))
    with db() as conn:
        uid, was = close_ticket(conn, tid)
    if was != "closed":
        core.notify(uid, f"✅ Обращение #{tid} закрыто. Если вопрос остался — просто напишите сюда ещё раз.")
    return {"ok": True, "message": "Обращение закрыто"}


# --------------------------------------------------------------------------
# Бот: обычный текст игрока -> обращение; ответы админа
# --------------------------------------------------------------------------
def _ticket_by_reply(chat_id, message_id):
    with db() as conn:
        r = one(conn, "SELECT ticket_id FROM support_tg_map WHERE chat_id=? AND message_id=?", (chat_id, message_id))
    return r[0] if r else None


def _user_msg_sync(from_user, text):
    user = {"id": from_user.id, "username": from_user.username, "first_name": from_user.first_name or "Игрок"}
    with db() as conn:
        uid = touch_user(conn, user)
        tid, is_new = add_user_message(conn, uid, text)
        name, username = _person(conn, uid)
    return uid, tid, is_new, name, username


def _admin_reply_sync(admin_id, tid, text):
    with db() as conn:
        return add_admin_message(conn, tid, admin_id, text)


def _open_list_sync():
    with db() as conn:
        return all_(
            conn,
            "SELECT t.id, u.first_name, t.unread_admin, "
            "(SELECT text FROM support_messages WHERE ticket_id=t.id ORDER BY id DESC LIMIT 1) "
            "FROM support_tickets t LEFT JOIN users u ON u.user_id=t.user_id "
            "WHERE t.status='open' ORDER BY t.updated_at DESC LIMIT 15",
        )


async def _do_admin_reply(message, tid, text):
    text = (text or "").strip()[:TEXT_MAX]
    if not text:
        await message.answer("❌ Пустой ответ.")
        return
    try:
        uid = await asyncio.to_thread(_admin_reply_sync, message.from_user.id, tid, text)
    except HTTPException as e:
        await message.answer(f"❌ {html.escape(str(e.detail))}")
        return
    _notify_reply(uid, tid, text)
    await message.answer(f"✅ Ответ на обращение #{tid} отправлен игроку.")


async def bot_text(message):
    """Обычный текст в чате с ботом."""
    if not message.from_user or message.chat.type != "private":
        return
    text = message.text or ""
    if message.from_user.id in core.ctx.admin_ids:
        rep = message.reply_to_message
        if rep:
            tid = await asyncio.to_thread(_ticket_by_reply, message.chat.id, rep.message_id)
            if tid:
                await _do_admin_reply(message, tid, text)
                return
        await message.answer(
            "ℹ️ Чтобы ответить игроку — сделайте <b>reply</b> на его обращение, либо "
            "<code>/reply номер текст</code>. Открытые обращения: /tickets"
        )
        return
    try:
        uid, tid, is_new, name, username = await asyncio.to_thread(_user_msg_sync, message.from_user, text[:TEXT_MAX])
    except HTTPException as e:
        await message.answer(f"⏳ {html.escape(str(e.detail))}")
        return
    await _forward(tid, uid, name, username, text[:TEXT_MAX], is_new)
    await message.answer(
        f"✅ Обращение #{tid} принято. Ответ поддержки придёт сюда." if is_new
        else f"✅ Дополнение к обращению #{tid} отправлено."
    )


async def bot_reply_cmd(message):
    if not message.from_user or message.from_user.id not in core.ctx.admin_ids:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Используйте: <code>/reply номер текст</code>")
        return
    await _do_admin_reply(message, int(parts[1]), parts[2])


async def bot_close_cmd(message):
    if not message.from_user or message.from_user.id not in core.ctx.admin_ids:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Используйте: <code>/close номер</code>")
        return
    tid = int(parts[1])

    def _do():
        with db() as conn:
            return close_ticket(conn, tid)

    try:
        uid, was = await asyncio.to_thread(_do)
    except HTTPException as e:
        await message.answer(f"❌ {html.escape(str(e.detail))}")
        return
    if was != "closed":
        core.notify(uid, f"✅ Обращение #{tid} закрыто. Если вопрос остался — просто напишите сюда ещё раз.")
    await message.answer(f"✅ Обращение #{tid} закрыто.")


async def bot_tickets_cmd(message):
    if not message.from_user or message.from_user.id not in core.ctx.admin_ids:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    rows = await asyncio.to_thread(_open_list_sync)
    if not rows:
        await message.answer("📭 Открытых обращений нет.")
        return
    lines = [
        f"{'🔴' if r[2] else '⚪'} <b>#{r[0]}</b> {html.escape(r[1] or 'Игрок')}: {html.escape((r[3] or '')[:60])}"
        for r in rows
    ]
    await message.answer("📋 <b>Открытые обращения</b>\n\n" + "\n".join(lines) + "\n\nОтвет: <code>/reply номер текст</code>")
