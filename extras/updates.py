"""
extras/updates.py — «Что нового»: админ пишет пост об обновлении приложения,
а игрок при заходе видит это сообщение во всплывающем окне (один раз).

Игроки:
  GET  /api/v2/updates/unseen     — непрочитанные посты (новые первыми)
  POST /api/v2/updates/seen       — {"ids": [..]} отметить прочитанными
  GET  /api/v2/updates            — последние посты (история)
Админ:
  GET  /api/v2/admin/updates            — список всех постов + сколько игроков прочитали
  POST /api/v2/admin/updates            — {"title": "...", "body": "..."} создать пост
  POST /api/v2/admin/updates/delete     — {"id": 1} удалить пост
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .core import all_, auth, db, now_iso, one, touch_user

log = logging.getLogger("mspbets.updates")
router = APIRouter(prefix="/api/v2")

TITLE_MAX = 80
BODY_MAX = 2000
SHOW_DAYS = 14  # посты старше N дней новым и вернувшимся игрокам уже не показываем
UNSEEN_LIMIT = 5


def _need_admin(request):
    user = auth(request)
    uid = int(user["id"])
    if uid not in core.ctx.admin_ids:
        raise HTTPException(403, "Только для админа")
    return uid


def _row(r):
    return {"id": r[0], "title": r[1], "body": r[2], "created_at": r[3]}


@router.get("/updates/unseen")
def updates_unseen(request: Request):
    user = auth(request)
    since = (datetime.now(timezone.utc) - timedelta(days=SHOW_DAYS)).isoformat()
    with db() as conn:
        uid = touch_user(conn, user)
        rows = all_(
            conn,
            "SELECT id, title, body, created_at FROM app_updates "
            "WHERE created_at>=? AND id NOT IN (SELECT update_id FROM update_seen WHERE user_id=?) "
            "ORDER BY id DESC LIMIT ?",
            (since, uid, UNSEEN_LIMIT),
        )
    return [_row(r) for r in rows]


@router.post("/updates/seen")
def updates_seen(request: Request, body: dict = Body(...)):
    user = auth(request)
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(400, "Неверный список")
    clean = []
    for i in ids[:50]:
        try:
            clean.append(int(i))
        except (TypeError, ValueError):
            continue
    with db() as conn:
        uid = touch_user(conn, user)
        for i in clean:
            conn.execute(
                "INSERT OR IGNORE INTO update_seen (user_id, update_id, seen_at) "
                "SELECT ?, id, ? FROM app_updates WHERE id=?",
                (uid, now_iso(), i),
            )
    return {"ok": True}


@router.get("/updates")
def updates_history(request: Request):
    auth(request)
    with db() as conn:
        rows = all_(conn, "SELECT id, title, body, created_at FROM app_updates ORDER BY id DESC LIMIT 20")
    return [_row(r) for r in rows]


# --------------------------- Админка ---------------------------
@router.get("/admin/updates")
def admin_updates(request: Request):
    _need_admin(request)
    with db() as conn:
        rows = all_(
            conn,
            "SELECT u.id, u.title, u.body, u.created_at, "
            "(SELECT COUNT(*) FROM update_seen s WHERE s.update_id=u.id) "
            "FROM app_updates u ORDER BY u.id DESC LIMIT 50",
        )
        total = one(conn, "SELECT COUNT(*) FROM users")[0]
    return {"total_users": total, "items": [dict(_row(r), seen=r[4]) for r in rows]}


@router.post("/admin/updates")
def admin_create_update(request: Request, body: dict = Body(...)):
    _need_admin(request)
    title = str(body.get("title") or "").strip()[:TITLE_MAX] or "Обновление приложения"
    text = str(body.get("body") or "").strip()
    if not text:
        raise HTTPException(400, "Напишите текст обновления")
    if len(text) > BODY_MAX:
        raise HTTPException(400, f"Слишком длинно (максимум {BODY_MAX} символов)")
    with db() as conn:
        uid = conn.execute(
            "INSERT INTO app_updates (title, body, created_at) VALUES (?,?,?)", (title, text, now_iso())
        ).lastrowid
    return {"ok": True, "id": uid, "message": "Опубликовано: игроки увидят это при следующем заходе"}


@router.post("/admin/updates/delete")
def admin_delete_update(request: Request, body: dict = Body(...)):
    _need_admin(request)
    try:
        uid = int(body.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Неверный id")
    with db() as conn:
        if not one(conn, "SELECT 1 FROM app_updates WHERE id=?", (uid,)):
            raise HTTPException(404, "Пост не найден")
        conn.execute("DELETE FROM app_updates WHERE id=?", (uid,))
        conn.execute("DELETE FROM update_seen WHERE update_id=?", (uid,))
    return {"ok": True, "message": "Удалено"}
