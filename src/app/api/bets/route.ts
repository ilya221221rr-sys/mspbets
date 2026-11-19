import { body, handle, requireUser } from "@/lib/core";
import { cashoutBet, listBets, placeBets } from "@/lib/betting";

export const dynamic = "force-dynamic";

export async function GET() {
  return handle(async () => {
    const u = await requireUser();
    return { bets: await listBets(u.id) };
  });
}

export async function POST(req: Request) {
  return handle(async () => {
    const u = await requireUser();
    const b = await body<{
      action?: string;
      betId?: number;
      mode?: string;
      stake?: number;
      items?: { outcomeId: number; odds: number }[];
    }>(req);
    if (b.action === "cashout") return cashoutBet(u.id, Number(b.betId));
    return placeBets(u.id, b);
  });
}
