import { createHash, randomBytes } from "node:crypto";
import { and, desc, eq } from "drizzle-orm";
import { db } from "@/db";
import { crashBets, crashRounds, users } from "@/db/schema";
import { AppError, MAX_STAKE, MIN_STAKE, addXp, change, intIn } from "./core";

export const GROWTH = 0.1; // mult(t) = e^(0.1 * t_sec)
export const BET_MS = 7000;
export const COOL_MS = 4000;

export const multAt = (ms: number) => Math.max(1, Math.floor(Math.exp((GROWTH * ms) / 1000) * 100) / 100);
const flightMs = (cp: number) => Math.ceil((Math.log(cp) / GROWTH) * 1000);

/** Provably fair: crashPoint выводится из seed; sha256(seed) публикуется до раунда. */
export function crashPointFromSeed(seed: string) {
  const h = parseInt(createHash("sha256").update(seed).digest("hex").slice(0, 13), 16);
  const e = 2 ** 52;
  if (h % 25 === 0) return 1; // ~4% — маржа
  return Math.min(1000, Math.max(1, Math.floor((100 * e - h) / (e - h)) / 100));
}

export async function currentRound() {
  const now = Date.now();
  let [last] = await db.select().from(crashRounds).orderBy(desc(crashRounds.id)).limit(1);
  if (!last || last.endsAt.getTime() <= now) {
    const seed = randomBytes(24).toString("hex");
    const cp = crashPointFromSeed(seed);
    const bettingEndsAt = new Date(now + BET_MS);
    const crashAt = new Date(bettingEndsAt.getTime() + flightMs(cp));
    await db
      .insert(crashRounds)
      .values({
        id: (last?.id ?? 0) + 1,
        seed,
        seedHash: createHash("sha256").update(seed).digest("hex"),
        crashPoint: cp,
        bettingEndsAt,
        crashAt,
        endsAt: new Date(crashAt.getTime() + COOL_MS),
      })
      .onConflictDoNothing();
    [last] = await db.select().from(crashRounds).orderBy(desc(crashRounds.id)).limit(1);
  }
  return last;
}

type Round = typeof crashRounds.$inferSelect;

export function phaseOf(r: Round, now = Date.now()) {
  if (now < r.bettingEndsAt.getTime()) return "betting" as const;
  if (now < r.crashAt.getTime()) return "flying" as const;
  return "crashed" as const;
}

export function liveMult(r: Round, now = Date.now()) {
  const ph = phaseOf(r, now);
  if (ph === "betting") return 1;
  if (ph === "crashed") return r.crashPoint;
  return Math.min(multAt(now - r.bettingEndsAt.getTime()), r.crashPoint);
}

async function payBet(betId: number, userId: number, amount: number, mult: number) {
  await db.transaction(async (tx) => {
    const payout = Math.floor(amount * mult);
    const [row] = await tx
      .update(crashBets)
      .set({ status: "won", cashoutMult: mult, payout })
      .where(and(eq(crashBets.id, betId), eq(crashBets.status, "active")))
      .returning({ id: crashBets.id });
    if (row) await change(tx, userId, payout, "crash_win", `Краш x${mult}`, `crash:${betId}`);
  });
}

/** Авто-вывод и проигрыши — вычисляются лениво при каждом обращении. */
export async function processRound(r: Round) {
  const now = Date.now();
  const ph = phaseOf(r, now);
  if (ph === "betting") return;
  const mult = liveMult(r, now);
  const active = await db.select().from(crashBets).where(and(eq(crashBets.roundId, r.id), eq(crashBets.status, "active")));
  for (const b of active) {
    if (b.autoCashout && b.autoCashout <= mult) await payBet(b.id, b.userId, b.amount, b.autoCashout);
    else if (ph === "crashed") {
      await db.update(crashBets).set({ status: "lost" }).where(and(eq(crashBets.id, b.id), eq(crashBets.status, "active")));
    }
  }
}

export async function placeCrashBet(userId: number, amountIn: unknown, autoIn: unknown) {
  const amount = intIn(amountIn, MIN_STAKE, MAX_STAKE, "Ставка");
  let auto: number | null = null;
  if (autoIn !== undefined && autoIn !== null && autoIn !== "" && Number(autoIn) > 0) {
    auto = Math.round(Number(autoIn) * 100) / 100;
    if (!(auto >= 1.01 && auto <= 1000)) throw new AppError("Авто-вывод: от 1.01 до 1000");
  }
  const r = await currentRound();
  if (phaseOf(r) !== "betting") throw new AppError("Приём ставок закрыт, дождитесь следующего раунда");
  return db.transaction(async (tx) => {
    const balance = await change(tx, userId, -amount, "crash_bet", `Краш, раунд #${r.id}`, `crashround:${r.id}`);
    try {
      await tx.insert(crashBets).values({ roundId: r.id, userId, amount, autoCashout: auto });
    } catch {
      throw new AppError("Вы уже сделали ставку в этом раунде");
    }
    await addXp(tx, userId, Math.max(1, amount / 40));
    return { ok: true, balance };
  });
}

export async function cashoutCrash(userId: number) {
  const r = await currentRound();
  await processRound(r);
  const now = Date.now();
  if (phaseOf(r, now) !== "flying") throw new AppError("Раунд не идёт");
  const [b] = await db
    .select()
    .from(crashBets)
    .where(and(eq(crashBets.roundId, r.id), eq(crashBets.userId, userId)));
  if (!b || b.status !== "active") throw new AppError("Нет активной ставки");
  const mult = liveMult(r, now);
  await payBet(b.id, userId, b.amount, mult);
  const [u] = await db.select({ balance: users.balance }).from(users).where(eq(users.id, userId));
  return { ok: true, mult, payout: Math.floor(b.amount * mult), balance: u.balance };
}
