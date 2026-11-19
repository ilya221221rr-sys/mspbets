import { sql } from "drizzle-orm";
import { db } from "@/db";
import { events, items, lootCases, promoCodes } from "@/db/schema";
import { createEvent } from "./betting";

const TEAMS: Record<string, { leagues: string[]; teams: string[] }> = {
  dota2: {
    leagues: ["The International", "DreamLeague", "ESL One", "BLAST Slam"],
    teams: ["Team Spirit", "Tundra Esports", "Team Falcons", "Gaimin Gladiators", "BetBoom Team", "Xtreme Gaming", "Team Liquid", "Aurora", "Natus Vincere", "PARIVISION", "Heroic", "OG"],
  },
  cs2: {
    leagues: ["IEM Katowice", "BLAST Premier", "PGL Major", "ESL Pro League"],
    teams: ["NAVI", "FaZe Clan", "Vitality", "G2 Esports", "Spirit", "MOUZ", "Team Liquid", "Virtus.pro", "The MongolZ", "Astralis", "Cloud9", "Heroic"],
  },
  football: {
    leagues: ["Лига чемпионов", "АПЛ", "Ла Лига", "РПЛ", "Серия А"],
    teams: ["Реал Мадрид", "Барселона", "Манчестер Сити", "Ливерпуль", "Арсенал", "Бавария", "ПСЖ", "Интер", "Ювентус", "Милан", "Боруссия Д", "Атлетико", "Зенит", "Спартак", "ЦСКА", "Динамо"],
  },
};

const pick = <T,>(a: T[]) => a[Math.floor(Math.random() * a.length)];

export async function generateLine(count = 14, withLive = true) {
  await db.transaction(async (tx) => {
    const sports = Object.keys(TEAMS);
    for (let i = 0; i < count; i++) {
      const sport = sports[i % sports.length];
      const t = TEAMS[sport];
      const home = pick(t.teams);
      let away = pick(t.teams);
      while (away === home) away = pick(t.teams);
      await createEvent(tx, {
        sport,
        league: pick(t.leagues),
        home,
        away,
        startsAt: new Date(Date.now() + (20 + Math.random() * 60 * 70) * 60000),
        p: 0.3 + Math.random() * 0.4,
        featured: i < 3,
      });
    }
    if (withLive) {
      for (const [sport, hs, as] of [["football", 1, 0], ["cs2", 1, 1], ["dota2", 0, 1]] as const) {
        const t = TEAMS[sport];
        const home = pick(t.teams);
        let away = pick(t.teams);
        while (away === home) away = pick(t.teams);
        const id = await createEvent(tx, {
          sport,
          league: pick(t.leagues),
          home,
          away,
          startsAt: new Date(Date.now() - 20 * 60000),
          p: 0.35 + Math.random() * 0.3,
          status: "live",
          homeScore: hs,
          awayScore: as,
          featured: true,
        });
        void id;
      }
    }
  });
}

const TIERS = [
  { mult: 0.2, weight: 500, rarity: "Обычный", emoji: "🪙", noun: "Жетон" },
  { mult: 0.5, weight: 270, rarity: "Обычный", emoji: "🎟️", noun: "Купон" },
  { mult: 1, weight: 100, rarity: "Необычный", emoji: "🧤", noun: "Перчатки" },
  { mult: 2, weight: 70, rarity: "Редкий", emoji: "🔪", noun: "Клинок" },
  { mult: 5, weight: 35, rarity: "Эпический", emoji: "👑", noun: "Корона" },
  { mult: 15, weight: 12, rarity: "Легендарный", emoji: "🐉", noun: "Дракон" },
  { mult: 50, weight: 2, rarity: "Мифический", emoji: "🌟", noun: "Звезда" },
];
// RTP = Σ(mult*weight)/Σweight ≈ 0.94

const CASES = [
  { name: "Старт", price: 100, emoji: "📦", color: "#7dd3fc" },
  { name: "Неон", price: 500, emoji: "🧪", color: "#55ff00" },
  { name: "Кибер", price: 1000, emoji: "💾", color: "#a78bfa" },
  { name: "Шейх", price: 2500, emoji: "💎", color: "#fbbf24" },
  { name: "Легенда", price: 5000, emoji: "🏆", color: "#f472b6" },
];

export async function seedAll() {
  const [{ c }] = await db.select({ c: sql<number>`count(*)::int` }).from(events);
  if (c === 0) await generateLine(14, true);
  const [{ c: cc }] = await db.select({ c: sql<number>`count(*)::int` }).from(lootCases);
  if (cc === 0) {
    for (const cs of CASES) {
      const [row] = await db.insert(lootCases).values(cs).returning({ id: lootCases.id });
      await db.insert(items).values(
        TIERS.map((t) => ({
          caseId: row.id,
          name: `${t.noun} «${cs.name}»`,
          rarity: t.rarity,
          emoji: t.emoji,
          price: Math.round(cs.price * t.mult),
          weight: t.weight,
        })),
      );
    }
  }
  await db
    .insert(promoCodes)
    .values([
      { code: "WELCOME", reward: 500, maxUses: 1000 },
      { code: "MSPBETS", reward: 300, maxUses: 1000 },
    ])
    .onConflictDoNothing();
}

let seeding: Promise<void> | null = null;
export function ensureSeed() {
  if (!seeding) {
    seeding = seedAll().catch((e) => {
      seeding = null;
      console.error("seed failed", e);
    });
  }
  return seeding;
}
