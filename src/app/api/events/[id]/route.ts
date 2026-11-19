import { AppError, handle } from "@/lib/core";
import { tickEvents } from "@/lib/betting";
import { eventById } from "@/lib/events";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  return handle(async () => {
    const { id } = await ctx.params;
    await tickEvents();
    const ev = await eventById(Number(id));
    if (!ev) throw new AppError("Матч не найден", 404);
    return { event: ev };
  });
}
