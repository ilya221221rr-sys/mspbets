import { randomInt } from "node:crypto";
import { db } from "@/db";
import { AppError, MAX_STAKE, MIN_STAKE, addXp, body, change, handle, intIn, requireUser } from "@/lib/core";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  return handle(async () => {
    const u = await requireUser();
    const b = await body<{ bet?: number; chance?: number; dir?: string }>(req);
    const bet = intIn(b.bet, MIN_STAKE, MAX_STAKE, "Ставка");
    const chance = Math.round(Number(b.chance) * 100) / 100;
    if (!(chance >= 1 && chance <= 95)) throw new AppError("Шанс: от 1% до 95%");
    const dir = b.dir === "over" ? "over" : "under";
    const roll = randomInt(0, 10000) / 100; // 0.00 – 99.99
    const win = dir === "under" ? roll < chance : roll >= 100 - chance;
    const mult = Math.floor((97 / chance) * 100) / 100;
    const payout = win ? Math.floor(bet * mult) : 0;
    return db.transaction(async (tx) => {
      let balance = await change(tx, u.id, -bet, "dice_bet", `Дайс ${dir} ${chance}%`);
      if (win) balance = await change(tx, u.id, payout, "dice_win", `Дайс x${mult}`);
      await addXp(tx, u.id, Math.max(1, bet / 40));
      return { ok: true, roll, win, mult, payout, balance };
    });
  });
}
