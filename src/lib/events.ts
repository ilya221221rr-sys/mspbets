import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { db } from "@/db";
import { events, markets, outcomes } from "@/db/schema";

export async function loadEvents(where: ReturnType<typeof and>, order: "asc" | "desc" = "asc", limit = 80) {
  const evs = await db
    .select()
    .from(events)
    .where(where)
    .orderBy(order === "asc" ? asc(events.startsAt) : desc(events.startsAt))
    .limit(limit);
  if (!evs.length) return [];
  const ms = await db
    .select()
    .from(markets)
    .where(inArray(markets.eventId, evs.map((e) => e.id)))
    .orderBy(asc(markets.sort));
  const os = ms.length
    ? await db.select().from(outcomes).where(inArray(outcomes.marketId, ms.map((m) => m.id))).orderBy(asc(outcomes.id))
    : [];
  return evs.map((e) => ({
    ...e,
    markets: ms
      .filter((m) => m.eventId === e.id)
      .map((m) => ({ ...m, outcomes: os.filter((o) => o.marketId === m.id) })),
  }));
}

export const eventById = (id: number) => loadEvents(eq(events.id, id), "asc", 1).then((l) => l[0] ?? null);
export { desc };
