import { desc, eq, lt } from "drizzle-orm";
import { db } from "@/db";
import { crashBets, crashRounds, users } from "@/db/schema";
import { AppError, body, handle, requireUser } from "@/lib/core";
import { cashoutCrash, currentRound, liveMult, phaseOf, placeCrashBet, processRound } from "@/lib/crash";

export const dynamic = "force-dynamic";

export async function GET() {
  return handle(async () => {
    const u = await requireUser();
    const r = await currentRound();
    await processRound(r);
    const now = Date.now();
    const phase = phaseOf(r, now);
    const bets = await db
      .select({
        userId: crashBets.userId,
        name: users.name,
        amount: crashBets.amount,
        status: crashBets.status,
        cashoutMult: crashBets.cashoutMult,
        payout: crashBets.payout,
      })
      .from(crashBets)
      .innerJoin(users, eq(users.id, crashBets.userId))
      .where(eq(crashBets.roundId, r.id))
      .orderBy(desc(crashBets.amount))
      .limit(30);
    const history = await db
      .select({ id: crashRounds.id, crashPoint: crashRounds.crashPoint, seed: crashRounds.seed, seedHash: crashRounds.seedHash })
      .from(crashRounds)
      .where(lt(crashRounds.crashAt, new Date(now)))
      .orderBy(desc(crashRounds.id))
      .limit(14);
    const mine = bets.find((b) => b.userId === u.id) ?? null;
    const [me] = await db.select({ balance: users.balance }).from(users).where(eq(users.id, u.id));
    return {
      serverNow: now,
      balance: me.balance,
      round: {
        id: r.id,
        phase,
        seedHash: r.seedHash,
        bettingEndsAt: r.bettingEndsAt.getTime(),
        endsAt: r.endsAt.getTime(),
        mult: liveMult(r, now),
        crashPoint: phase === "crashed" ? r.crashPoint : null,
        seed: phase === "crashed" ? r.seed : null,
      },
      bets: bets.map((b) => ({ ...b, userId: undefined, me: b.userId === u.id })),
      mine: mine && { amount: mine.amount, status: mine.status, cashoutMult: mine.cashoutMult, payout: mine.payout },
      history,
    };
  });
}

export async function POST(req: Request) {
  return handle(async () => {
    const u = await requireUser();
    const b = await body<{ action?: string; amount?: number; auto?: number }>(req);
    if (b.action === "bet") return placeCrashBet(u.id, b.amount, b.auto);
    if (b.action === "cashout") return cashoutCrash(u.id);
    throw new AppError("Неизвестное действие");
  });
}
