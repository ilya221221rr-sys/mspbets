import { desc } from "drizzle-orm";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { users } from "@/db/schema";
import { handle, levelInfo, requireUser } from "@/lib/core";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  return handle(async () => {
    const me = await requireUser();
    const type = new URL(req.url).searchParams.get("type") === "balance" ? "balance" : "week";
    if (type === "balance") {
      const rows = await db
        .select({ id: users.id, name: users.name, username: users.username, value: users.balance, xp: users.xp })
        .from(users)
        .orderBy(desc(users.balance))
        .limit(50);
      return { type, me: me.id, rows: rows.map((r) => ({ ...r, level: levelInfo(r.xp).level })) };
    }
    const res = await db.execute(sql`
      select u.id, u.name, u.username, u.xp, coalesce(sum(l.amount),0)::bigint as value
      from users u join ledger l on l.user_id = u.id
      where l.created_at > now() - interval '7 days'
        and l.type not in ('signup','daily','promo','referral','admin')
      group by u.id order by value desc limit 50`);
    const rows = (res.rows as { id: number; name: string; username: string | null; xp: string; value: string }[]).map((r) => ({
      id: r.id,
      name: r.name,
      username: r.username,
      value: Number(r.value),
      level: levelInfo(Number(r.xp)).level,
    }));
    return { type, me: me.id, rows };
  });
}
