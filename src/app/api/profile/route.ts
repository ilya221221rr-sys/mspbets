import { and, desc, eq, sql } from "drizzle-orm";
import { db } from "@/db";
import { bets, ledger, promoCodes, promoUses, users } from "@/db/schema";
import { AppError, addXp, body, change, handle, levelInfo, publicUser, requireUser } from "@/lib/core";

export const dynamic = "force-dynamic";

const DAY = 24 * 3600 * 1000;
function dailyInfo(u: { lastDailyAt: Date | null; streak: number; xp: number }) {
  const now = Date.now();
  const last = u.lastDailyAt?.getTime() ?? 0;
  const broken = last && now - last > 2 * DAY;
  const streak = broken ? 0 : u.streak;
  const reward = 100 + 50 * Math.min(streak, 6) + levelInfo(u.xp).level * 10;
  return { available: !last || now - last >= DAY, nextAt: last ? last + DAY : 0, reward, streak };
}

export async function GET() {
  return handle(async () => {
    const u = await requireUser();
    const history = await db.select().from(ledger).where(eq(ledger.userId, u.id)).orderBy(desc(ledger.id)).limit(40);
    const [st] = await db
      .select({
        total: sql<number>`count(*)::int`,
        won: sql<number>`count(*) filter (where ${bets.status} in ('won','cashed'))::int`,
      })
      .from(bets)
      .where(eq(bets.userId, u.id));
    const [inv] = await db
      .select({ c: sql<number>`count(*)::int` })
      .from(users)
      .where(eq(users.referredBy, u.id));
    return { user: publicUser(u), history, daily: dailyInfo(u), stats: { ...st, referrals: inv.c } };
  });
}

export async function POST(req: Request) {
  return handle(async () => {
    const u = await requireUser();
    const b = await body<{ action?: string; code?: string }>(req);
    if (b.action === "daily") {
      return db.transaction(async (tx) => {
        const [me] = await tx.select().from(users).where(eq(users.id, u.id)).for("update");
        const d = dailyInfo(me);
        if (!d.available) throw new AppError("Бонус уже получен, возвращайтесь позже");
        const streak = d.streak + 1;
        await tx.update(users).set({ lastDailyAt: new Date(), streak }).where(eq(users.id, u.id));
        const balance = await change(tx, u.id, d.reward, "daily", `Ежедневный бонус, серия ${streak}`);
        await addXp(tx, u.id, 10);
        return { ok: true, reward: d.reward, streak, balance };
      });
    }
    if (b.action === "promo") {
      const code = String(b.code || "").trim().toUpperCase();
      if (!code) throw new AppError("Введите промокод");
      return db.transaction(async (tx) => {
        const [p] = await tx.select().from(promoCodes).where(eq(promoCodes.code, code)).for("update");
        if (!p || !p.active) throw new AppError("Промокод не найден");
        if (p.uses >= p.maxUses) throw new AppError("Промокод закончился");
        const used = await tx
          .select()
          .from(promoUses)
          .where(and(eq(promoUses.userId, u.id), eq(promoUses.code, code)));
        if (used.length) throw new AppError("Вы уже использовали этот промокод");
        await tx.insert(promoUses).values({ userId: u.id, code });
        await tx.update(promoCodes).set({ uses: sql`${promoCodes.uses} + 1` }).where(eq(promoCodes.code, code));
        const balance = await change(tx, u.id, p.reward, "promo", `Промокод ${code}`);
        return { ok: true, reward: p.reward, balance };
      });
    }
    throw new AppError("Неизвестное действие");
  });
}
