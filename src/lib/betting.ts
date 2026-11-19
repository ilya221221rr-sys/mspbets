import { and, desc, eq, inArray, lte, sql } from "drizzle-orm";
import { db } from "@/db";
import { betSelections, bets, events, markets, outcomes, users } from "@/db/schema";
import { AppError, MAX_PAYOUT, MAX_STAKE, MIN_STAKE, Tx, addXp, change, intIn } from "./core";
import type { Notice } from "./telegram";

export const SPORTS: Record<string, { name: string; emoji: string; esport: boolean }> = {
  dota2: { name: "Dota 2", emoji: "🛡️", esport: true },
  cs2: { name: "CS2", emoji: "🔫", esport: true },
  football: { name: "Футбол", emoji: "⚽", esport: false },
};

export const MARGIN = 0.06;
export const CASHOUT_FACTOR = 0.92;
const r2 = (n: number) => Math.round(n * 100) / 100;
const clamp = (x: number, a = 0.08, b = 0.92) => Math.min(b, Math.max(a, x));

/** Коэффициенты из вероятностей с маржой букмекера. */
export function priceOdds(probs: number[], margin = MARGIN): number[] {
  const s = probs.reduce((a, b) => a + b, 0);
  return probs.map((p) => Math.max(1.01, r2(1 / ((p / s) * (1 + margin)))));
}

type Tpl = { type: string; name: string; line?: number; outcomes: { key: string; label: string; p: number }[] };

export function buildMarkets(sport: string, home: string, away: string, p: number): Tpl[] {
  if (sport === "football") {
    const pd = 0.26;
    const over = clamp(0.52 + Math.abs(p - 0.5) * 0.4);
    return [
      {
        type: "1x2",
        name: "Исход матча",
        outcomes: [
          { key: "h", label: home, p: p * (1 - pd) },
          { key: "d", label: "Ничья", p: pd },
          { key: "a", label: away, p: (1 - p) * (1 - pd) },
        ],
      },
      {
        type: "total",
        name: "Тотал голов",
        line: 2.5,
        outcomes: [
          { key: "over", label: "Больше 2.5", p: over },
          { key: "under", label: "Меньше 2.5", p: 1 - over },
        ],
      },
      {
        type: "btts",
        name: "Обе команды забьют",
        outcomes: [
          { key: "yes", label: "Да", p: 0.54 },
          { key: "no", label: "Нет", p: 0.46 },
        ],
      },
    ];
  }
  const over = clamp(0.62 - Math.abs(p - 0.5) * 0.9);
  const hc = clamp(p * p);
  const list: Tpl[] = [
    {
      type: "ml",
      name: "Победитель матча",
      outcomes: [
        { key: "h", label: home, p },
        { key: "a", label: away, p: 1 - p },
      ],
    },
    {
      type: "total",
      name: "Тотал карт",
      line: 2.5,
      outcomes: [
        { key: "over", label: "Больше 2.5", p: over },
        { key: "under", label: "Меньше 2.5", p: 1 - over },
      ],
    },
    {
      type: "hcap",
      name: "Фора по картам",
      line: -1.5,
      outcomes: [
        { key: "h", label: `${home} (-1.5)`, p: hc },
        { key: "a", label: `${away} (+1.5)`, p: 1 - hc },
      ],
    },
  ];
  const mapP = clamp(0.5 + (p - 0.5) * 0.8);
  list.push({
    type: sport === "dota2" ? "firstblood" : "map1",
    name: sport === "dota2" ? "Первая кровь" : "Победитель 1-й карты",
    outcomes: [
      { key: "h", label: home, p: mapP },
      { key: "a", label: away, p: 1 - mapP },
    ],
  });
  return list;
}

export async function createEvent(
  tx: Tx,
  e: {
    sport: string;
    league: string;
    home: string;
    away: string;
    startsAt: Date;
    p: number;
    status?: string;
    homeScore?: number;
    awayScore?: number;
    featured?: boolean;
  },
) {
  const [ev] = await tx
    .insert(events)
    .values({
      sport: e.sport,
      league: e.league,
      home: e.home,
      away: e.away,
      startsAt: e.startsAt,
      status: e.status ?? "upcoming",
      homeScore: e.homeScore ?? 0,
      awayScore: e.awayScore ?? 0,
      featured: e.featured ?? false,
    })
    .returning({ id: events.id });
  const tpls = buildMarkets(e.sport, e.home, e.away, e.p);
  let sort = 0;
  for (const t of tpls) {
    const [m] = await tx
      .insert(markets)
      .values({ eventId: ev.id, type: t.type, name: t.name, line: t.line ?? null, sort: sort++ })
      .returning({ id: markets.id });
    const odds = priceOdds(t.outcomes.map((o) => o.p));
    await tx.insert(outcomes).values(
      t.outcomes.map((o, i) => ({ marketId: m.id, key: o.key, label: o.label, odds: odds[i], prevOdds: odds[i] })),
    );
  }
  return ev.id;
}

/** Автоматический исход рынка по счёту. null — нужен ручной расчёт. */
export function autoResolve(type: string, line: number | null, hs: number, as: number): string | null {
  switch (type) {
    case "ml":
      return hs === as ? "void" : hs > as ? "h" : "a";
    case "1x2":
      return hs > as ? "h" : hs < as ? "a" : "d";
    case "total":
      return hs + as > (line ?? 2.5) ? "over" : "under";
    case "btts":
      return hs > 0 && as > 0 ? "yes" : "no";
    case "hcap":
      return hs + (line ?? -1.5) > as ? "h" : "a";
    default:
      return null;
  }
}

/** Закрытие ставок по времени старта: upcoming → live, рынки приостанавливаются. */
export async function tickEvents() {
  const moved = await db
    .update(events)
    .set({ status: "live" })
    .where(and(eq(events.status, "upcoming"), lte(events.startsAt, new Date())))
    .returning({ id: events.id });
  if (moved.length) {
    await db
      .update(markets)
      .set({ status: "suspended" })
      .where(and(inArray(markets.eventId, moved.map((m) => m.id)), eq(markets.status, "open")));
  }
}

/* ---------------- Расчёт ---------------- */
async function evaluateBets(tx: Tx, betIds: number[]): Promise<Notice[]> {
  const notices: Notice[] = [];
  if (!betIds.length) return notices;
  const list = await tx
    .select()
    .from(bets)
    .where(and(inArray(bets.id, betIds), eq(bets.status, "pending")))
    .for("update");
  for (const b of list) {
    const sels = await tx.select().from(betSelections).where(eq(betSelections.betId, b.id));
    const [u] = await tx.select({ tgId: users.tgId }).from(users).where(eq(users.id, b.userId));
    const now = new Date();
    if (sels.some((s) => s.status === "lost")) {
      await tx.update(bets).set({ status: "lost", payout: 0, settledAt: now }).where(eq(bets.id, b.id));
      notices.push({ tgId: u?.tgId ?? null, text: `❌ Ставка #${b.id} не зашла. Ставка: ${b.stake} PTS` });
      continue;
    }
    if (sels.some((s) => s.status === "pending")) continue;
    const won = sels.filter((s) => s.status === "won");
    if (!won.length) {
      await change(tx, b.userId, b.stake, "bet_refund", `Возврат по ставке #${b.id}`, `bet:${b.id}`);
      await tx.update(bets).set({ status: "void", payout: b.stake, settledAt: now }).where(eq(bets.id, b.id));
      notices.push({ tgId: u?.tgId ?? null, text: `↩️ Ставка #${b.id}: возврат ${b.stake} PTS` });
      continue;
    }
    const odds = r2(won.reduce((a, s) => a * s.odds, 1));
    const payout = Math.min(MAX_PAYOUT, Math.floor(b.stake * odds));
    await change(tx, b.userId, payout, "bet_win", `Выигрыш по ставке #${b.id}`, `bet:${b.id}`);
    await tx.update(bets).set({ status: "won", payout, totalOdds: odds, settledAt: now }).where(eq(bets.id, b.id));
    notices.push({ tgId: u?.tgId ?? null, text: `✅ Ставка #${b.id} выиграла! +${payout.toLocaleString("ru-RU")} PTS (кф ${odds})` });
  }
  return notices;
}

export async function settleMarket(tx: Tx, marketId: number, key: string): Promise<Notice[]> {
  const [m] = await tx.select().from(markets).where(eq(markets.id, marketId)).for("update");
  if (!m || m.status === "settled" || m.status === "void") return [];
  const outs = await tx.select().from(outcomes).where(eq(outcomes.marketId, marketId));
  if (key !== "void" && !outs.some((o) => o.key === key)) throw new AppError("Неверный исход");
  for (const o of outs) {
    const r = key === "void" ? "void" : o.key === key ? "won" : "lost";
    await tx.update(outcomes).set({ result: r }).where(eq(outcomes.id, o.id));
    await tx.update(betSelections).set({ status: r }).where(eq(betSelections.outcomeId, o.id));
  }
  await tx.update(markets).set({ status: key === "void" ? "void" : "settled" }).where(eq(markets.id, marketId));
  const ids = await tx
    .selectDistinct({ id: betSelections.betId })
    .from(betSelections)
    .where(eq(betSelections.marketId, marketId));
  return evaluateBets(tx, ids.map((i) => i.id));
}

export async function finishEvent(tx: Tx, eventId: number): Promise<{ notices: Notice[]; manual: number }> {
  const [ev] = await tx.select().from(events).where(eq(events.id, eventId)).for("update");
  if (!ev) throw new AppError("Матч не найден", 404);
  if (ev.status === "finished" || ev.status === "cancelled") throw new AppError("Матч уже завершён");
  const ms = await tx.select().from(markets).where(eq(markets.eventId, eventId));
  const notices: Notice[] = [];
  let manual = 0;
  for (const m of ms) {
    if (m.status === "settled" || m.status === "void") continue;
    const key = autoResolve(m.type, m.line, ev.homeScore, ev.awayScore);
    if (!key) {
      manual++;
      await tx.update(markets).set({ status: "suspended" }).where(eq(markets.id, m.id));
      continue;
    }
    notices.push(...(await settleMarket(tx, m.id, key)));
  }
  await tx.update(events).set({ status: "finished" }).where(eq(events.id, eventId));
  return { notices, manual };
}

export async function cancelEvent(tx: Tx, eventId: number): Promise<Notice[]> {
  const ms = await tx.select().from(markets).where(eq(markets.eventId, eventId));
  const notices: Notice[] = [];
  for (const m of ms) {
    if (m.status === "settled" || m.status === "void") continue;
    notices.push(...(await settleMarket(tx, m.id, "void")));
  }
  await tx.update(events).set({ status: "cancelled" }).where(eq(events.id, eventId));
  return notices;
}

/** Live: пересчёт коэффициентов по счёту. */
export async function repriceLive(tx: Tx, eventId: number) {
  const [ev] = await tx.select().from(events).where(eq(events.id, eventId));
  if (!ev) return;
  const d = ev.homeScore - ev.awayScore;
  const ms = await tx.select().from(markets).where(eq(markets.eventId, eventId));
  for (const m of ms) {
    if (m.status === "settled" || m.status === "void") continue;
    const outs = await tx.select().from(outcomes).where(eq(outcomes.marketId, m.id));
    let probs: Record<string, number> | null = null;
    if (m.type === "1x2") {
      const ph = clamp(0.38 + 0.2 * d, 0.04, 0.9);
      const pd = Math.max(0.05, 0.28 - 0.08 * Math.abs(d));
      probs = { h: ph, d: pd, a: Math.max(0.03, 1 - ph - pd) };
    } else if (m.type === "ml") {
      const ph = clamp(0.5 + 0.22 * d, 0.05, 0.95);
      probs = { h: ph, a: 1 - ph };
    }
    if (m.type === "total" && ev.homeScore + ev.awayScore > (m.line ?? 2.5)) {
      await tx.update(markets).set({ status: "suspended" }).where(eq(markets.id, m.id));
    }
    if (m.type === "btts" && ev.homeScore > 0 && ev.awayScore > 0) {
      await tx.update(markets).set({ status: "suspended" }).where(eq(markets.id, m.id));
    }
    if (probs) {
      const odds = priceOdds(outs.map((o) => probs![o.key] ?? 0.3));
      for (let i = 0; i < outs.length; i++) {
        await tx.update(outcomes).set({ prevOdds: outs[i].odds, odds: odds[i] }).where(eq(outcomes.id, outs[i].id));
      }
    }
  }
}

/* ---------------- Купон ---------------- */
export async function placeBets(
  userId: number,
  input: { mode?: string; stake?: unknown; items?: { outcomeId: number; odds: number }[] },
) {
  const stake = intIn(input.stake, MIN_STAKE, MAX_STAKE, "Сумма ставки");
  const mode = input.mode === "express" ? "express" : "single";
  const items = Array.isArray(input.items) ? input.items : [];
  if (!items.length) throw new AppError("Купон пуст");
  if (items.length > 10) throw new AppError("Максимум 10 событий в купоне");
  if (mode === "express" && items.length < 2) throw new AppError("Для экспресса нужно минимум 2 события");

  return db.transaction(async (tx) => {
    const ids = items.map((i) => Number(i.outcomeId));
    const rows = await tx
      .select({ o: outcomes, m: markets, e: events })
      .from(outcomes)
      .innerJoin(markets, eq(outcomes.marketId, markets.id))
      .innerJoin(events, eq(markets.eventId, events.id))
      .where(inArray(outcomes.id, ids));
    if (rows.length !== new Set(ids).size) throw new AppError("Исход не найден");
    const now = Date.now();
    const changed: { outcomeId: number; odds: number }[] = [];
    for (const r of rows) {
      const open =
        r.m.status === "open" &&
        ((r.e.status === "upcoming" && r.e.startsAt.getTime() > now) || r.e.status === "live");
      if (!open) throw new AppError(`Ставки закрыты: ${r.e.home} — ${r.e.away}`, 409, { closed: [r.o.id] });
      const it = items.find((i) => Number(i.outcomeId) === r.o.id);
      if (it && Math.abs(Number(it.odds) - r.o.odds) > 0.001) changed.push({ outcomeId: r.o.id, odds: r.o.odds });
    }
    if (changed.length) throw new AppError("Коэффициенты изменились", 409, { changed });
    if (mode === "express" && new Set(rows.map((r) => r.e.id)).size !== rows.length) {
      throw new AppError("В экспрессе нельзя выбрать два исхода одного матча");
    }

    const [me] = await tx.select().from(users).where(eq(users.id, userId));
    const [{ c: prior }] = await tx.select({ c: sql<number>`count(*)::int` }).from(bets).where(eq(bets.userId, userId));

    const total = mode === "single" ? stake * rows.length : stake;
    const balance = await change(tx, userId, -total, "bet_stake", mode === "single" ? "Ординар" : "Экспресс");

    const created: number[] = [];
    const insertSel = async (betId: number, r: (typeof rows)[number]) =>
      tx.insert(betSelections).values({
        betId,
        outcomeId: r.o.id,
        marketId: r.m.id,
        eventId: r.e.id,
        odds: r.o.odds,
      });
    if (mode === "single") {
      for (const r of rows) {
        const [b] = await tx
          .insert(bets)
          .values({ userId, type: "single", stake, totalOdds: r.o.odds })
          .returning({ id: bets.id });
        await insertSel(b.id, r);
        created.push(b.id);
      }
    } else {
      const totalOdds = Math.min(1000, r2(rows.reduce((a, r) => a * r.o.odds, 1)));
      const [b] = await tx.insert(bets).values({ userId, type: "express", stake, totalOdds }).returning({ id: bets.id });
      for (const r of rows) await insertSel(b.id, r);
      created.push(b.id);
    }
    await addXp(tx, userId, Math.max(1, total / 20));
    if (prior === 0 && me?.referredBy) {
      await change(tx, me.referredBy, 300, "referral", `Первая ставка приглашённого #${userId}`);
    }
    return { ok: true, balance, bets: created };
  });
}

export function cashoutCalc(stake: number, betOdds: number, curOdds: number) {
  return Math.floor(((stake * betOdds) / curOdds) * CASHOUT_FACTOR);
}

export async function cashoutBet(userId: number, betId: number) {
  return db.transaction(async (tx) => {
    const [b] = await tx
      .select()
      .from(bets)
      .where(and(eq(bets.id, betId), eq(bets.userId, userId)))
      .for("update");
    if (!b || b.status !== "pending") throw new AppError("Ставка недоступна для кешаута");
    if (b.type !== "single") throw new AppError("Кешаут доступен только для ординаров");
    const [r] = await tx
      .select({ o: outcomes, m: markets, e: events })
      .from(betSelections)
      .innerJoin(outcomes, eq(betSelections.outcomeId, outcomes.id))
      .innerJoin(markets, eq(outcomes.marketId, markets.id))
      .innerJoin(events, eq(markets.eventId, events.id))
      .where(eq(betSelections.betId, betId));
    if (!r || r.m.status !== "open" || !["upcoming", "live"].includes(r.e.status)) {
      throw new AppError("Кешаут сейчас недоступен");
    }
    const value = cashoutCalc(b.stake, b.totalOdds, r.o.odds);
    if (value < 1) throw new AppError("Кешаут сейчас недоступен");
    await tx.update(bets).set({ status: "cashed", payout: value, settledAt: new Date() }).where(eq(bets.id, b.id));
    await tx.update(betSelections).set({ status: "void" }).where(eq(betSelections.betId, b.id));
    const balance = await change(tx, userId, value, "bet_cashout", `Кешаут ставки #${b.id}`, `bet:${b.id}`);
    return { ok: true, value, balance };
  });
}

export async function listBets(userId: number) {
  const list = await db.select().from(bets).where(eq(bets.userId, userId)).orderBy(desc(bets.id)).limit(60);
  if (!list.length) return [];
  const rows = await db
    .select({ s: betSelections, o: outcomes, m: markets, e: events })
    .from(betSelections)
    .innerJoin(outcomes, eq(betSelections.outcomeId, outcomes.id))
    .innerJoin(markets, eq(outcomes.marketId, markets.id))
    .innerJoin(events, eq(markets.eventId, events.id))
    .where(inArray(betSelections.betId, list.map((b) => b.id)));
  return list.map((b) => {
    const sels = rows
      .filter((r) => r.s.betId === b.id)
      .map((r) => ({
        id: r.s.id,
        eventId: r.e.id,
        event: `${r.e.home} — ${r.e.away}`,
        sport: r.e.sport,
        market: r.m.name,
        pick: r.o.label,
        odds: r.s.odds,
        status: r.s.status,
        eventStatus: r.e.status,
        score: r.e.status === "upcoming" ? null : `${r.e.homeScore}:${r.e.awayScore}`,
      }));
    let cashout: number | null = null;
    if (b.status === "pending" && b.type === "single") {
      const r = rows.find((x) => x.s.betId === b.id);
      if (r && r.m.status === "open" && ["upcoming", "live"].includes(r.e.status)) {
        const v = cashoutCalc(b.stake, b.totalOdds, r.o.odds);
        cashout = v >= 1 ? v : null;
      }
    }
    return {
      id: b.id,
      type: b.type,
      stake: b.stake,
      totalOdds: b.totalOdds,
      status: b.status,
      payout: b.payout,
      createdAt: b.createdAt,
      potential: Math.min(MAX_PAYOUT, Math.floor(b.stake * b.totalOdds)),
      cashout,
      selections: sels,
    };
  });
}
