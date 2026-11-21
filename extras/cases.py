"""
extras/cases.py — кейсы, инвентарь, честный апгрейдер, коллекции.

Честность:
  * шанс предмета = weight / sum(weights); тот же вес используется при выпадении;
  * RTP кейса = sum(weight*price)/sum(weight)/price_кейса (90–95%), показывается игроку;
  * апгрейд: шанс = 95% * сумма_цен_источника / цена_цели (комиссия 5%), округление вниз до 0.01%;
    бросок randrange(10000) < шанс_в_сотых_процента — шанс на экране равен реальному.
  * админские «подкрутки» (forced_skin_id / custom_upgrade_chance) здесь НЕ используются.
"""
import json
import time

from fastapi import APIRouter, Body, HTTPException, Request

from . import core
from .catalog import ALL_CASES as CATALOG_CASES, WEIGHT_TOTAL, build
from .core import RNG, adjust, all_, auth, db, now_iso, one, touch_user, user_lock

router = APIRouter(prefix="/api/v2")

UPGRADE_FEE = 0.05
UP_MIN_BP, UP_MAX_BP = 400, 9000  # 4% .. 90% (в сотых долях процента)
UP_MAX_SOURCES = 5
_cache = {"ts": 0, "data": None}
DESC = {c["name"]: c["desc"] for c in CATALOG_CASES}


IMG_VER = "2"  # меняйте, чтобы сбросить у игроков закэшированные картинки


def _v(url):
    """Для картинок CS2 добавляем ?v= — так клиенты забывают ранее закэшированный 404."""
    if url and url.startswith(("/web/cs2/", "/web/cases/cs2")) and "?" not in url:
        return f"{url}?v={IMG_VER}"
    return url


def seed():
    """Засев каталога. Идемпотентно: можно вызывать при каждом старте.
    При ПЕРВОМ запуске старые кейсы (Dota) скрываются (active=0), данные игроков не удаляются."""
    cat = build()
    conn = ctx_conn()
    try:
        existing = {r[0]: r[1] for r in all_(conn, "SELECT name, id FROM cases")}
        if not any(c["name"] in existing for c in cat):
            conn.execute("UPDATE cases SET active=0")
        for c in cat:
            img = _v(c.get("image") or f"/web/cases/{c['slug']}.svg")
            if c["name"] in existing:
                cid = existing[c["name"]]
                conn.execute("UPDATE cases SET price=?, image_url=?, active=1 WHERE id=?", (c["price"], img, cid))
            else:
                cid = conn.execute(
                    "INSERT INTO cases (name, price, image_url, active) VALUES (?,?,?,1)", (c["name"], c["price"], img)
                ).lastrowid
            for it in c["items"]:
                simg = _v(it.get("image") or f"/web/skins/{it['slug']}.svg")
                row = one(conn, "SELECT id FROM skins WHERE case_id=? AND name=?", (cid, it["name"]))
                if row:
                    conn.execute(
                        "UPDATE skins SET price=?, rarity=?, image_url=?, weight=? WHERE id=?",
                        (it["price"], it["rarity"], simg, it["weight"], row[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO skins (case_id, name, rarity, image_url, price, weight) VALUES (?,?,?,?,?,?)",
                        (cid, it["name"], it["rarity"], simg, it["price"], it["weight"]),
                    )
        conn.commit()
    finally:
        conn.close()
    _cache["ts"] = 0


def ctx_conn():
    return core.ctx.get_db()


def _catalog(conn):
    if _cache["data"] and time.time() - _cache["ts"] < 60:
        return _cache["data"]
    cases = all_(conn, "SELECT id, name, price, image_url FROM cases WHERE active=1 ORDER BY price")
    out = []
    for cid, name, price, img in cases:
        skins = all_(conn, "SELECT id, name, rarity, price, image_url, weight FROM skins WHERE case_id=? AND weight>0 ORDER BY price", (cid,))
        total = sum(s[5] for s in skins) or 1
        rtp = sum(s[5] * s[3] for s in skins) / total / price if price else 0
        out.append(
            {
                "id": cid, "name": name, "price": price, "image_url": img, "desc": DESC.get(name, ""),
                "rtp": round(rtp * 100, 2), "total": total,
                "items": [
                    {"id": s[0], "name": s[1], "rarity": s[2], "price": s[3], "image_url": s[4],
                     "weight": s[5], "chance": round(s[5] / total * 100, 4)}
                    for s in skins
                ],
            }
        )
    _cache.update(ts=time.time(), data=out)
    return out


@router.get("/cases")
def get_cases(request: Request):
    auth(request)
    with db() as conn:
        cases = _catalog(conn)
        recent = []
        for r in all_(
            conn,
            "SELECT g.detail, g.mult, u.first_name FROM game_rounds g JOIN users u ON u.user_id=g.user_id "
            "WHERE g.game='case' ORDER BY g.id DESC LIMIT 12",
        ):
            try:
                d = json.loads(r[0])
                recent.append({"user": r[2] or "Игрок", "name": d["name"], "image_url": d["image_url"], "rarity": d["rarity"], "price": d["price"]})
            except Exception:
                pass
    return {"cases": cases, "recent": recent}


def _pick(items):
    total = sum(i["weight"] for i in items)
    roll = RNG.randrange(total)
    acc = 0
    for it in items:
        acc += it["weight"]
        if roll < acc:
            return it, roll, total
    return items[-1], roll, total


@router.post("/cases/open")
def open_case(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    case_id = int(body.get("case_id", 0))
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        cat = {c["id"]: c for c in _catalog(conn)}
        case = cat.get(case_id)
        if not case:
            raise HTTPException(404, "Кейс не найден")
        item, roll, total = _pick(case["items"])
        bal = adjust(conn, uid, -case["price"], "case", f"Кейс «{case['name']}»", ref=f"case:{case_id}")
        inv_id = conn.execute(
            "INSERT INTO inventory (user_id, skin_id, created_at) VALUES (?,?,?)", (uid, item["id"], now_iso())
        ).lastrowid
        core.on_wager(conn, uid, case["price"], "case")
        core.quest_event(conn, uid, "case_open", 1)
        core.bump(conn, uid, "cases_opened", 1)
        if item["rarity"] in ("Legendary", "Immortal"):
            core.bump(conn, uid, "legend_drops", 1)
        core.log_round(
            conn, uid, "case", case["price"], item["price"], item["price"] / case["price"],
            {"case": case["name"], "name": item["name"], "image_url": item["image_url"], "rarity": item["rarity"], "price": item["price"], "roll": roll, "total": total},
        )
    return {
        "ok": True, "balance": bal, "roll": roll, "total": total, "unlocked": core.pop_unlocked(),
        "skin": {**{k: item[k] for k in ("id", "name", "image_url", "price", "rarity", "chance")}, "inv_id": inv_id},
    }


@router.get("/inventory")
def get_inventory(request: Request):
    user = auth(request)
    with db() as conn:
        rows = all_(
            conn,
            "SELECT i.id, s.id, s.name, s.image_url, s.price, s.rarity FROM inventory i JOIN skins s ON i.skin_id=s.id "
            "WHERE i.user_id=? AND i.status='owned' ORDER BY s.price DESC",
            (int(user["id"]),),
        )
    return [{"inv_id": r[0], "skin_id": r[1], "name": r[2], "image_url": r[3], "price": r[4], "rarity": r[5]} for r in rows]


@router.post("/inventory/sell")
def sell(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    ids = body.get("inv_ids") or ([body["inv_id"]] if "inv_id" in body else [])
    ids = [int(i) for i in ids][:100]
    if not ids:
        raise HTTPException(400, "Выберите предметы")
    total, n = 0, 0
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        for iid in ids:
            row = one(
                conn,
                "SELECT s.price, s.name FROM inventory i JOIN skins s ON s.id=i.skin_id WHERE i.id=? AND i.user_id=? AND i.status='owned'",
                (iid, uid),
            )
            if not row:
                continue
            cur = conn.execute("UPDATE inventory SET status='sold' WHERE id=? AND user_id=? AND status='owned'", (iid, uid))
            if core.changed(cur, conn) == 1:
                total += row[0]
                n += 1
        if n == 0:
            raise HTTPException(400, "Скин не найден")
        bal = adjust(conn, uid, total, "sell", f"Продажа предметов ({n} шт.)")
    return {"ok": True, "sold": n, "amount": total, "balance": bal, "message": f"Продано: {n} шт. за {total} PTS"}


# ---------------------------- Апгрейдер ----------------------------
def _sources(conn, uid, inv_ids):
    inv_ids = [int(i) for i in inv_ids][:UP_MAX_SOURCES]
    if not inv_ids or len(set(inv_ids)) != len(inv_ids):
        raise HTTPException(400, "Выберите скины для апгрейда")
    rows = []
    for iid in inv_ids:
        r = one(
            conn,
            "SELECT i.id, s.price, s.name FROM inventory i JOIN skins s ON s.id=i.skin_id WHERE i.id=? AND i.user_id=? AND i.status='owned'",
            (iid, uid),
        )
        if not r:
            raise HTTPException(400, "Скин не найден")
        rows.append(r)
    return rows


def chance_bp(src_sum, target_price):
    """Шанс в сотых долях процента (4750 = 47.50%). Округление вниз."""
    return int(src_sum * (1 - UPGRADE_FEE) * 10000 // target_price)


@router.get("/upgrade/targets")
def upgrade_targets(request: Request, inv_ids: str = ""):
    user = auth(request)
    ids = [int(x) for x in inv_ids.split(",") if x.strip().isdigit()]
    with db() as conn:
        src = _sources(conn, int(user["id"]), ids)
        src_sum = sum(r[1] for r in src)
        rows = all_(
            conn,
            "SELECT s.id, s.name, s.rarity, s.price, s.image_url FROM skins s JOIN cases c ON c.id=s.case_id "
            "WHERE c.active=1 AND s.weight>0 AND s.price>? ORDER BY s.price",
            (src_sum,),
        )
    out = []
    for r in rows:
        bp = chance_bp(src_sum, r[3])
        if UP_MIN_BP <= bp <= UP_MAX_BP:
            out.append({"id": r[0], "name": r[1], "rarity": r[2], "price": r[3], "image_url": r[4], "chance": bp / 100, "mult": round(r[3] / src_sum, 2)})
    return {"source_sum": src_sum, "fee": UPGRADE_FEE, "targets": out[:150]}


@router.post("/upgrade")
def upgrade(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        src = _sources(conn, uid, body.get("inv_ids") or [body.get("inv_id")])
        src_sum = sum(r[1] for r in src)
        t = one(
            conn,
            "SELECT s.id, s.name, s.rarity, s.price, s.image_url FROM skins s JOIN cases c ON c.id=s.case_id WHERE s.id=? AND c.active=1 AND s.weight>0",
            (int(body.get("target_id", 0)),),
        )
        if not t or t[3] <= src_sum:
            raise HTTPException(400, "Неверная цель апгрейда")
        bp = chance_bp(src_sum, t[3])
        if not (UP_MIN_BP <= bp <= UP_MAX_BP):
            raise HTTPException(400, "Шанс вне допустимого диапазона (4–90%)")
        roll = RNG.randrange(10000)  # 0..9999 (в сотых долях %)
        win = roll < bp
        for r in src:
            cur = conn.execute("UPDATE inventory SET status=? WHERE id=? AND user_id=? AND status='owned'", ("upgraded" if win else "lost", r[0], uid))
            if core.changed(cur, conn) != 1:
                raise HTTPException(400, "Скин уже использован")
        if win:
            conn.execute("INSERT INTO inventory (user_id, skin_id, created_at) VALUES (?,?,?)", (uid, t[0], now_iso()))
            core.bump(conn, uid, "upgrades_won", 1)
        core.on_wager(conn, uid, src_sum, "upgrade")
        core.quest_event(conn, uid, "upgrade", 1)
        core.log_round(conn, uid, "upgrade", src_sum, t[3] if win else 0, (t[3] / src_sum) if win else 0,
                       {"target": t[1], "chance": bp / 100, "roll": roll / 100, "win": win})
    return {
        "ok": True, "success": win, "chance": bp / 100, "roll": roll / 100, "unlocked": core.pop_unlocked(),
        "target": {"id": t[0], "name": t[1], "rarity": t[2], "price": t[3], "image_url": t[4]},
    }


# ---------------------------- Коллекции ----------------------------
COLLECTION_REWARD = 0.25  # от суммы цен всех предметов кейса


@router.get("/collections")
def collections(request: Request):
    user = auth(request)
    uid = int(user["id"])
    with db() as conn:
        cat = _catalog(conn)
        owned = {r[0] for r in all_(conn, "SELECT DISTINCT skin_id FROM inventory WHERE user_id=?", (uid,))}
        claimed = {r[0] for r in all_(conn, "SELECT case_id FROM collection_claims WHERE user_id=?", (uid,))}
    out = []
    for c in cat:
        items = [{**{k: i[k] for k in ("id", "name", "rarity", "price", "image_url")}, "owned": i["id"] in owned} for i in c["items"]]
        got = sum(1 for i in items if i["owned"])
        out.append({
            "case_id": c["id"], "name": c["name"], "image_url": c["image_url"], "items": items, "owned": got,
            "total": len(items), "complete": got == len(items), "claimed": c["id"] in claimed,
            "reward": int(sum(i["price"] for i in items) * COLLECTION_REWARD),
        })
    return out


@router.post("/collections/claim")
def claim_collection(request: Request, body: dict = Body(...)):
    user = auth(request)
    uid = int(user["id"])
    case_id = int(body.get("case_id", 0))
    with user_lock(uid), db() as conn:
        touch_user(conn, user)
        c = next((x for x in _catalog(conn) if x["id"] == case_id), None)
        if not c:
            raise HTTPException(404, "Кейс не найден")
        owned = {r[0] for r in all_(conn, "SELECT DISTINCT skin_id FROM inventory WHERE user_id=?", (uid,))}
        if not all(i["id"] in owned for i in c["items"]):
            raise HTTPException(400, "Коллекция ещё не собрана")
        cur = conn.execute("INSERT OR IGNORE INTO collection_claims (user_id, case_id, claimed_at) VALUES (?,?,?)", (uid, case_id, now_iso()))
        if core.changed(cur, conn) != 1:
            raise HTTPException(400, "Награда уже получена")
        reward = int(sum(i["price"] for i in c["items"]) * COLLECTION_REWARD)
        bal = adjust(conn, uid, reward, "collection", f"Коллекция «{c['name']}» собрана", ref=f"case:{case_id}")
        core.bump(conn, uid, "collections_done", 1)
    return {"ok": True, "reward": reward, "balance": bal, "unlocked": core.pop_unlocked()}
