import { randomInt } from "node:crypto";
import { and, asc, desc, eq } from "drizzle-orm";
import { db } from "@/db";
import { inventory, items, lootCases } from "@/db/schema";
import { AppError, addXp, body, change, handle, intIn, requireUser } from "@/lib/core";
import { ensureSeed } from "@/lib/seed";

export const dynamic = "force-dynamic";

const upgradeChance = (mine: number, target: number) =>
  Math.round(Math.min(90, Math.max(1, (mine / target) * 95)) * 100) / 100;

export async function GET() {
  return handle(async () => {
    const u = await requireUser();
    await ensureSeed();
    const cases = await db.select().from(lootCases).orderBy(asc(lootCases.price));
    const all = await db.select().from(items).orderBy(asc(items.price));
    const inv = await db
      .select({ id: inventory.id, itemId: items.id, name: items.name, emoji: items.emoji, rarity: items.rarity, price: items.price })
      .from(inventory)
      .innerJoin(items, eq(items.id, inventory.itemId))
      .where(and(eq(inventory.userId, u.id), eq(inventory.status, "owned")))
      .orderBy(desc(items.price));
    return {
      balance: u.balance,
      cases: cases.map((c) => {
        const its = all.filter((i) => i.caseId === c.id);
        const total = its.reduce((a, i) => a + i.weight, 0);
        const rtp = its.reduce((a, i) => a + (i.price * i.weight) / total, 0) / c.price;
        return {
          ...c,
          rtp: Math.round(rtp * 1000) / 10,
          items: its.map((i) => ({ ...i, chance: Math.round((i.weight / total) * 10000) / 100 })).sort((a, b) => b.price - a.price),
        };
      }),
      targets: all.map((i) => ({ id: i.id, name: i.name, emoji: i.emoji, rarity: i.rarity, price: i.price })),
      inventory: inv,
    };
  });
}

export async function POST(req: Request) {
  return handle(async () => {
    const u = await requireUser();
    const b = await body<{ action?: string; caseId?: number; invId?: number; targetId?: number }>(req);

    if (b.action === "open") {
      const caseId = intIn(b.caseId, 1, 1_000_000, "Кейс");
      return db.transaction(async (tx) => {
        const [c] = await tx.select().from(lootCases).where(eq(lootCases.id, caseId));
        if (!c) throw new AppError("Кейс не найден", 404);
        const its = await tx.select().from(items).where(eq(items.caseId, caseId));
        const total = its.reduce((a, i) => a + i.weight, 0);
        let roll = randomInt(0, total);
        const won = its.find((i) => (roll -= i.weight) < 0) ?? its[0];
        const balance = await change(tx, u.id, -c.price, "case_open", `Кейс «${c.name}»`);
        const [row] = await tx.insert(inventory).values({ userId: u.id, itemId: won.id }).returning({ id: inventory.id });
        await addXp(tx, u.id, Math.max(1, c.price / 40));
        return { ok: true, balance, invId: row.id, item: won };
      });
    }

    if (b.action === "sell") {
      const invId = intIn(b.invId, 1, 1_000_000_000, "Предмет");
      return db.transaction(async (tx) => {
        const [row] = await tx
          .update(inventory)
          .set({ status: "sold" })
          .where(and(eq(inventory.id, invId), eq(inventory.userId, u.id), eq(inventory.status, "owned")))
          .returning();
        if (!row) throw new AppError("Предмет не найден");
        const [it] = await tx.select().from(items).where(eq(items.id, row.itemId));
        const balance = await change(tx, u.id, it.price, "case_sell", `Продажа: ${it.name}`);
        return { ok: true, balance, price: it.price };
      });
    }

    if (b.action === "upgrade") {
      const invId = intIn(b.invId, 1, 1_000_000_000, "Предмет");
      const targetId = intIn(b.targetId, 1, 1_000_000, "Цель");
      return db.transaction(async (tx) => {
        const [row] = await tx
          .select()
          .from(inventory)
          .where(and(eq(inventory.id, invId), eq(inventory.userId, u.id), eq(inventory.status, "owned")))
          .for("update");
        if (!row) throw new AppError("Предмет не найден");
        const [mine] = await tx.select().from(items).where(eq(items.id, row.itemId));
        const [target] = await tx.select().from(items).where(eq(items.id, targetId));
        if (!target || target.price <= mine.price) throw new AppError("Выберите более дорогую цель");
        const chance = upgradeChance(mine.price, target.price);
        if (target.price > mine.price * 90) throw new AppError("Слишком большой множитель");
        const roll = randomInt(0, 10000) / 100;
        const success = roll < chance;
        await tx.update(inventory).set({ status: success ? "upgraded" : "burned" }).where(eq(inventory.id, row.id));
        let newInv: number | null = null;
        if (success) {
          const [ni] = await tx.insert(inventory).values({ userId: u.id, itemId: target.id }).returning({ id: inventory.id });
          newInv = ni.id;
        }
        await addXp(tx, u.id, Math.max(1, mine.price / 60));
        return { ok: true, success, chance, roll, target, newInv };
      });
    }
    throw new AppError("Неизвестное действие");
  });
}
