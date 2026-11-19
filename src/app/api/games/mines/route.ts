import { randomInt } from "node:crypto";
import { and, eq } from "drizzle-orm";
import { db } from "@/db";
import { minesGames } from "@/db/schema";
import { AppError, MAX_STAKE, MIN_STAKE, Tx, addXp, body, change, handle, intIn, requireUser } from "@/lib/core";

export const dynamic = "force-dynamic";
const CELLS = 25;
const EDGE = 0.97;

function minesMult(safe: number, mines: number) {
  let m = 1;
  for (let i = 0; i < safe; i++) m *= (CELLS - i) / (CELLS - mines - i);
  return Math.floor(m * EDGE * 100) / 100;
}

type Game = typeof minesGames.$inferSelect;
function view(g: Game, reveal = false) {
  const revealed = JSON.parse(g.revealed) as number[];
  const mult = revealed.length ? minesMult(revealed.length, g.minesCount) : 1;
  return {
    id: g.id,
    bet: g.bet,
    minesCount: g.minesCount,
    revealed,
    status: g.status,
    mult,
    nextMult: minesMult(revealed.length + 1, g.minesCount),
    payout: g.payout,
    mines: reveal || g.status !== "active" ? (JSON.parse(g.mines) as number[]) : null,
  };
}

async function cash(tx: Tx, g: Game) {
  const revealed = JSON.parse(g.revealed) as number[];
  const payout = Math.floor(g.bet * minesMult(revealed.length, g.minesCount));
  const [upd] = await tx
    .update(minesGames)
    .set({ status: "won", payout })
    .where(and(eq(minesGames.id, g.id), eq(minesGames.status, "active")))
    .returning();
  if (!upd) throw new AppError("Игра уже завершена");
  const balance = await change(tx, g.userId, payout, "mines_win", `Мины x${minesMult(revealed.length, g.minesCount)}`, `mines:${g.id}`);
  return { game: view(upd), balance };
}

export async function GET() {
  return handle(async () => {
    const u = await requireUser();
    const [g] = await db
      .select()
      .from(minesGames)
      .where(and(eq(minesGames.userId, u.id), eq(minesGames.status, "active")))
      .limit(1);
    return { game: g ? view(g) : null, balance: u.balance };
  });
}

export async function POST(req: Request) {
  return handle(async () => {
    const u = await requireUser();
    const b = await body<{ action?: string; bet?: number; mines?: number; tile?: number }>(req);

    if (b.action === "start") {
      const bet = intIn(b.bet, MIN_STAKE, MAX_STAKE, "Ставка");
      const count = intIn(b.mines, 1, 24, "Мин");
      return db.transaction(async (tx) => {
        const active = await tx
          .select({ id: minesGames.id })
          .from(minesGames)
          .where(and(eq(minesGames.userId, u.id), eq(minesGames.status, "active")));
        if (active.length) throw new AppError("Сначала завершите текущую игру");
        const balance = await change(tx, u.id, -bet, "mines_bet", `Мины (${count})`);
        const cells = Array.from({ length: CELLS }, (_, i) => i);
        for (let i = CELLS - 1; i > 0; i--) {
          const j = randomInt(0, i + 1);
          [cells[i], cells[j]] = [cells[j], cells[i]];
        }
        const [g] = await tx
          .insert(minesGames)
          .values({ userId: u.id, bet, minesCount: count, mines: JSON.stringify(cells.slice(0, count)) })
          .returning();
        await addXp(tx, u.id, Math.max(1, bet / 40));
        return { game: view(g), balance };
      });
    }

    if (b.action === "reveal" || b.action === "cashout") {
      return db.transaction(async (tx) => {
        const [g] = await tx
          .select()
          .from(minesGames)
          .where(and(eq(minesGames.userId, u.id), eq(minesGames.status, "active")))
          .for("update");
        if (!g) throw new AppError("Нет активной игры");
        const revealed = JSON.parse(g.revealed) as number[];
        if (b.action === "cashout") {
          if (!revealed.length) throw new AppError("Откройте хотя бы одну клетку");
          return cash(tx, g);
        }
        const tile = intIn(b.tile, 0, CELLS - 1, "Клетка");
        if (revealed.includes(tile)) throw new AppError("Клетка уже открыта");
        const mines = JSON.parse(g.mines) as number[];
        if (mines.includes(tile)) {
          const [upd] = await tx
            .update(minesGames)
            .set({ status: "lost", revealed: JSON.stringify([...revealed, tile]) })
            .where(eq(minesGames.id, g.id))
            .returning();
          return { game: view(upd), boom: tile };
        }
        const next = [...revealed, tile];
        const [upd] = await tx
          .update(minesGames)
          .set({ revealed: JSON.stringify(next) })
          .where(eq(minesGames.id, g.id))
          .returning();
        if (next.length === CELLS - g.minesCount) return cash(tx, upd);
        return { game: view(upd) };
      });
    }
    throw new AppError("Неизвестное действие");
  });
}
