import { desc, eq, ilike, or, sql } from "drizzle-orm";
import { db } from "@/db";
import { adminLogs, bets, markets, outcomes, promoCodes, users, events } from "@/db/schema";
import { AppError, body, change, handle, intIn, requireAdmin, requireUser } from "@/lib/core";
import { cancelEvent, createEvent, finishEvent, repriceLive, settleMarket } from "@/lib/betting";
import { generateLine } from "@/lib/seed";
import { sendNotices, tgSend, type Notice } from "@/lib/telegram";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  return handle(async () => {
    await requireAdmin();
    const url = new URL(req.url);
    const view = url.searchParams.get("view") || "stats";
    if (view === "users") {
      const q = url.searchParams.get("q")?.trim();
      const rows = await db
        .select({
          id: users.id,
          name: users.name,
          username: users.username,
          tgId: users.tgId,
          balance: users.balance,
          xp: users.xp,
          banned: users.banned,
          isAdmin: users.isAdmin,
          createdAt: users.createdAt,
        })
        .from(users)
        .where(q ? or(ilike(users.name, `%${q}%`), ilike(users.username, `%${q}%`), sql`${users.id}::text = ${q}`) : undefined)
        .orderBy(desc(users.createdAt))
        .limit(100);
      return { users: rows };
    }
    if (view === "logs") {
      const rows = await db
        .select({ id: adminLogs.id, action: adminLogs.action, details: adminLogs.details, createdAt: adminLogs.createdAt, admin: users.name })
        .from(adminLogs)
        .leftJoin(users, eq(users.id, adminLogs.adminId))
        .orderBy(desc(adminLogs.id))
        .limit(80);
      return { logs: rows };
    }
    if (view === "promos") {
      return { promos: await db.select().from(promoCodes).orderBy(desc(promoCodes.uses)) };
    }
    const [u] = await db.select({ c: sql<number>`count(*)::int`, bal: sql<number>`coalesce(sum(balance),0)::bigint`, day: sql<number>`count(*) filter (where created_at > now() - interval '1 day')::int` }).from(users);
    const [b] = await db
      .select({
        c: sql<number>`count(*)::int`,
        pending: sql<number>`count(*) filter (where status='pending')::int`,
        pendingStake: sql<number>`coalesce(sum(stake) filter (where status='pending'),0)::bigint`,
        turnover: sql<number>`coalesce(sum(stake) filter (where status <> 'void'),0)::bigint`,
        paid: sql<number>`coalesce(sum(payout) filter (where status in ('won','cashed')),0)::bigint`,
      })
      .from(bets);
    const byType = await db.execute(
      sql`select type, count(*)::int as n, sum(amount)::bigint as sum from ledger group by type order by type`,
    );
    const [ev] = await db.select({ c: sql<number>`count(*) filter (where status in ('upcoming','live'))::int`, live: sql<number>`count(*) filter (where status='live')::int` }).from(events);
    return {
      users: { total: Number(u.c), today: Number(u.day), balance: Number(u.bal) },
      bets: Object.fromEntries(Object.entries(b).map(([k, v]) => [k, Number(v)])),
      events: ev,
      byType: (byType.rows as { type: string; n: number; sum: string }[]).map((r) => ({ type: r.type, n: r.n, sum: Number(r.sum) })),
    };
  });
}

export async function POST(req: Request) {
  return handle(async () => {
    const b = await body<Record<string, unknown>>(req);
    const action = String(b.action || "");

    if (action === "unlock") {
      const me = await requireUser();
      const key = process.env.ADMIN_KEY;
      if (!key || String(b.key) !== key) throw new AppError("Неверный ключ", 403);
      await db.update(users).set({ isAdmin: true }).where(eq(users.id, me.id));
      return { ok: true };
    }

    const admin = await requireAdmin();
    const log = (a: string, d: string) => db.insert(adminLogs).values({ adminId: admin.id, action: a, details: d });
    let notices: Notice[] = [];
    let result: Record<string, unknown> = { ok: true };

    switch (action) {
      case "createEvent": {
        const sport = String(b.sport);
        if (!["dota2", "cs2", "football"].includes(sport)) throw new AppError("Неверный спорт");
        const home = String(b.home || "").trim();
        const away = String(b.away || "").trim();
        const league = String(b.league || "").trim() || "Турнир";
        if (!home || !away) throw new AppError("Укажите команды");
        const startsAt = new Date(String(b.startsAt));
        if (isNaN(startsAt.getTime())) throw new AppError("Неверная дата");
        const p = intIn(b.p ?? 50, 10, 90, "Шанс первой команды") / 100;
        const id = await db.transaction((tx) => createEvent(tx, { sport, league, home, away, startsAt, p, featured: !!b.featured }));
        await log("createEvent", `#${id} ${home} — ${away}`);
        result = { ok: true, id };
        break;
      }
      case "generateLine": {
        await generateLine(intIn(b.count ?? 9, 1, 30, "Количество"), false);
        await log("generateLine", `Сгенерировано матчей: ${b.count ?? 9}`);
        break;
      }
      case "setOdds": {
        const odds = Number(b.odds);
        if (!(odds >= 1.01 && odds <= 1000)) throw new AppError("Коэффициент: от 1.01 до 1000");
        const [o] = await db.select().from(outcomes).where(eq(outcomes.id, Number(b.outcomeId)));
        if (!o) throw new AppError("Исход не найден", 404);
        await db.update(outcomes).set({ prevOdds: o.odds, odds: Math.round(odds * 100) / 100 }).where(eq(outcomes.id, o.id));
        await log("setOdds", `${o.label}: ${o.odds} → ${odds}`);
        break;
      }
      case "marketStatus": {
        const st = String(b.status);
        if (!["open", "suspended"].includes(st)) throw new AppError("Неверный статус");
        const [m] = await db.select().from(markets).where(eq(markets.id, Number(b.marketId)));
        if (!m || ["settled", "void"].includes(m.status)) throw new AppError("Рынок уже рассчитан");
        await db.update(markets).set({ status: st }).where(eq(markets.id, m.id));
        await log("marketStatus", `${m.name} #${m.id} → ${st}`);
        break;
      }
      case "startEvent": {
        await db.update(events).set({ status: "live" }).where(eq(events.id, Number(b.eventId)));
        await log("startEvent", `#${b.eventId}`);
        break;
      }
      case "bump": {
        const id = Number(b.eventId);
        const side = b.side === "away" ? events.awayScore : events.homeScore;
        await db.transaction(async (tx) => {
          await tx.update(events).set(b.side === "away" ? { awayScore: sql`${side} + 1` } : { homeScore: sql`${side} + 1` }).where(eq(events.id, id));
          await repriceLive(tx, id);
        });
        await log("bump", `#${id} ${b.side}`);
        break;
      }
      case "setScore": {
        const id = Number(b.eventId);
        const h = intIn(b.home, 0, 99, "Счёт");
        const a = intIn(b.away, 0, 99, "Счёт");
        await db.transaction(async (tx) => {
          await tx.update(events).set({ homeScore: h, awayScore: a }).where(eq(events.id, id));
          await repriceLive(tx, id);
        });
        await log("setScore", `#${id} ${h}:${a}`);
        break;
      }
      case "finish": {
        const r = await db.transaction((tx) => finishEvent(tx, Number(b.eventId)));
        notices = r.notices;
        result = { ok: true, manual: r.manual };
        await log("finish", `#${b.eventId}, ручных рынков: ${r.manual}`);
        break;
      }
      case "cancel": {
        notices = await db.transaction((tx) => cancelEvent(tx, Number(b.eventId)));
        await log("cancel", `#${b.eventId}`);
        break;
      }
      case "settleMarket": {
        notices = await db.transaction((tx) => settleMarket(tx, Number(b.marketId), String(b.key)));
        await log("settleMarket", `#${b.marketId} → ${b.key}`);
        break;
      }
      case "adjust": {
        const amount = intIn(b.amount, -10_000_000, 10_000_000, "Сумма");
        const reason = String(b.reason || "").trim();
        if (!amount) throw new AppError("Сумма не может быть нулевой");
        if (reason.length < 3) throw new AppError("Укажите причину");
        const uid = Number(b.userId);
        await db.transaction((tx) => change(tx, uid, amount, "admin", reason));
        await log("adjust", `user #${uid}: ${amount} (${reason})`);
        break;
      }
      case "ban": {
        await db.update(users).set({ banned: !!b.banned }).where(eq(users.id, Number(b.userId)));
        await log("ban", `user #${b.userId} → ${!!b.banned}`);
        break;
      }
      case "createPromo": {
        const code = String(b.code || "").trim().toUpperCase();
        if (!/^[A-Z0-9_-]{3,20}$/.test(code)) throw new AppError("Код: 3–20 символов, латиница и цифры");
        const reward = intIn(b.reward, 1, 1_000_000, "Награда");
        const maxUses = intIn(b.maxUses ?? 100, 1, 1_000_000, "Лимит");
        await db.insert(promoCodes).values({ code, reward, maxUses }).onConflictDoUpdate({ target: promoCodes.code, set: { reward, maxUses, active: true } });
        await log("createPromo", `${code} +${reward} x${maxUses}`);
        break;
      }
      case "broadcast": {
        const text = String(b.text || "").trim();
        if (text.length < 3) throw new AppError("Введите текст");
        const rows = await db.select({ tgId: users.tgId }).from(users).where(sql`${users.tgId} is not null and not ${users.banned}`);
        void (async () => {
          for (const r of rows) {
            if (r.tgId) await tgSend(r.tgId, text, true);
            await new Promise((res) => setTimeout(res, 40));
          }
        })();
        await log("broadcast", `${rows.length} получателей: ${text.slice(0, 80)}`);
        result = { ok: true, recipients: rows.length };
        break;
      }
      case "setWebhook": {
        const token = process.env.BOT_TOKEN;
        const url = process.env.WEBAPP_URL;
        if (!token || !url) throw new AppError("Нужны BOT_TOKEN и WEBAPP_URL");
        const res = await fetch(`https://api.telegram.org/bot${token}/setWebhook`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: `${url.replace(/\/$/, "")}/api/telegram`, secret_token: process.env.WEBHOOK_SECRET || undefined }),
        });
        result = (await res.json()) as Record<string, unknown>;
        break;
      }
      default:
        throw new AppError("Неизвестное действие");
    }
    if (notices.length) sendNotices(notices);
    return result;
  });
}
