import { and, eq, gte, ilike, inArray, or, sql } from "drizzle-orm";
import { events } from "@/db/schema";
import { handle } from "@/lib/core";
import { ensureSeed } from "@/lib/seed";
import { tickEvents } from "@/lib/betting";
import { loadEvents } from "@/lib/events";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  return handle(async () => {
    await ensureSeed();
    await tickEvents();
    const url = new URL(req.url);
    const sport = url.searchParams.get("sport");
    const q = url.searchParams.get("q")?.trim();
    const status = url.searchParams.get("status") || "line";
    const filters = [];
    if (sport && sport !== "all") filters.push(eq(events.sport, sport));
    if (q) filters.push(or(ilike(events.home, `%${q}%`), ilike(events.away, `%${q}%`), ilike(events.league, `%${q}%`)));
    if (status === "live") filters.push(eq(events.status, "live"));
    else if (status === "results") {
      filters.push(inArray(events.status, ["finished", "cancelled"]));
      filters.push(gte(events.startsAt, sql`now() - interval '3 days'`));
    } else if (status === "all") {
      filters.push(gte(events.startsAt, sql`now() - interval '3 days'`));
    } else filters.push(inArray(events.status, ["live", "upcoming"]));
    const list = await loadEvents(and(...filters), status === "results" || status === "all" ? "desc" : "asc");
    if (status === "line") list.sort((a, b) => Number(b.status === "live") - Number(a.status === "live"));
    return { events: list };
  });
}
