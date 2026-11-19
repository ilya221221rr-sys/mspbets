import {
  bigint,
  boolean,
  doublePrecision,
  index,
  integer,
  pgTable,
  primaryKey,
  serial,
  text,
  timestamp,
  uniqueIndex,
} from "drizzle-orm/pg-core";

const big = (name: string) => bigint(name, { mode: "number" });

export const users = pgTable(
  "users",
  {
    id: serial("id").primaryKey(),
    tgId: big("tg_id"),
    username: text("username"),
    name: text("name").notNull(),
    balance: big("balance").notNull().default(1000),
    xp: big("xp").notNull().default(0),
    streak: integer("streak").notNull().default(0),
    lastDailyAt: timestamp("last_daily_at"),
    isAdmin: boolean("is_admin").notNull().default(false),
    banned: boolean("banned").notNull().default(false),
    refCode: text("ref_code").notNull(),
    referredBy: integer("referred_by"),
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [uniqueIndex("users_tg_idx").on(t.tgId), uniqueIndex("users_ref_idx").on(t.refCode)],
);

/** Единый журнал баллов: каждое движение баллов записано. */
export const ledger = pgTable(
  "ledger",
  {
    id: serial("id").primaryKey(),
    userId: integer("user_id").notNull(),
    amount: big("amount").notNull(),
    balanceAfter: big("balance_after").notNull(),
    type: text("type").notNull(),
    note: text("note"),
    ref: text("ref"),
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [index("ledger_user_idx").on(t.userId, t.createdAt), index("ledger_created_idx").on(t.createdAt)],
);

export const events = pgTable(
  "events",
  {
    id: serial("id").primaryKey(),
    sport: text("sport").notNull(),
    league: text("league").notNull(),
    home: text("home").notNull(),
    away: text("away").notNull(),
    startsAt: timestamp("starts_at").notNull(),
    status: text("status").notNull().default("upcoming"), // upcoming | live | finished | cancelled
    homeScore: integer("home_score").notNull().default(0),
    awayScore: integer("away_score").notNull().default(0),
    featured: boolean("featured").notNull().default(false),
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [index("events_status_idx").on(t.status, t.startsAt)],
);

export const markets = pgTable(
  "markets",
  {
    id: serial("id").primaryKey(),
    eventId: integer("event_id").notNull(),
    type: text("type").notNull(),
    name: text("name").notNull(),
    line: doublePrecision("line"),
    status: text("status").notNull().default("open"), // open | suspended | settled | void
    sort: integer("sort").notNull().default(0),
  },
  (t) => [index("markets_event_idx").on(t.eventId)],
);

export const outcomes = pgTable(
  "outcomes",
  {
    id: serial("id").primaryKey(),
    marketId: integer("market_id").notNull(),
    key: text("key").notNull(),
    label: text("label").notNull(),
    odds: doublePrecision("odds").notNull(),
    prevOdds: doublePrecision("prev_odds").notNull(),
    result: text("result").notNull().default("pending"), // pending | won | lost | void
  },
  (t) => [index("outcomes_market_idx").on(t.marketId)],
);

export const bets = pgTable(
  "bets",
  {
    id: serial("id").primaryKey(),
    userId: integer("user_id").notNull(),
    type: text("type").notNull(), // single | express
    stake: big("stake").notNull(),
    totalOdds: doublePrecision("total_odds").notNull(),
    status: text("status").notNull().default("pending"), // pending | won | lost | void | cashed
    payout: big("payout").notNull().default(0),
    createdAt: timestamp("created_at").notNull().defaultNow(),
    settledAt: timestamp("settled_at"),
  },
  (t) => [index("bets_user_idx").on(t.userId, t.createdAt), index("bets_status_idx").on(t.status)],
);

export const betSelections = pgTable(
  "bet_selections",
  {
    id: serial("id").primaryKey(),
    betId: integer("bet_id").notNull(),
    outcomeId: integer("outcome_id").notNull(),
    marketId: integer("market_id").notNull(),
    eventId: integer("event_id").notNull(),
    odds: doublePrecision("odds").notNull(),
    status: text("status").notNull().default("pending"), // pending | won | lost | void
  },
  (t) => [index("sel_bet_idx").on(t.betId), index("sel_outcome_idx").on(t.outcomeId), index("sel_market_idx").on(t.marketId)],
);

export const crashRounds = pgTable("crash_rounds", {
  id: integer("id").primaryKey(),
  seed: text("seed").notNull(),
  seedHash: text("seed_hash").notNull(),
  crashPoint: doublePrecision("crash_point").notNull(),
  bettingEndsAt: timestamp("betting_ends_at", { mode: "date", precision: 3 }).notNull(),
  crashAt: timestamp("crash_at", { mode: "date", precision: 3 }).notNull(),
  endsAt: timestamp("ends_at", { mode: "date", precision: 3 }).notNull(),
});

export const crashBets = pgTable(
  "crash_bets",
  {
    id: serial("id").primaryKey(),
    roundId: integer("round_id").notNull(),
    userId: integer("user_id").notNull(),
    amount: big("amount").notNull(),
    autoCashout: doublePrecision("auto_cashout"),
    cashoutMult: doublePrecision("cashout_mult"),
    payout: big("payout").notNull().default(0),
    status: text("status").notNull().default("active"), // active | won | lost
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [uniqueIndex("crash_bets_uq").on(t.roundId, t.userId)],
);

export const minesGames = pgTable(
  "mines_games",
  {
    id: serial("id").primaryKey(),
    userId: integer("user_id").notNull(),
    bet: big("bet").notNull(),
    minesCount: integer("mines_count").notNull(),
    mines: text("mines").notNull(), // json number[]
    revealed: text("revealed").notNull().default("[]"), // json number[]
    status: text("status").notNull().default("active"), // active | won | lost
    payout: big("payout").notNull().default(0),
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [index("mines_user_idx").on(t.userId, t.status)],
);

export const lootCases = pgTable("loot_cases", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  price: big("price").notNull(),
  emoji: text("emoji").notNull(),
  color: text("color").notNull(),
});

export const items = pgTable(
  "items",
  {
    id: serial("id").primaryKey(),
    caseId: integer("case_id").notNull(),
    name: text("name").notNull(),
    rarity: text("rarity").notNull(),
    emoji: text("emoji").notNull(),
    price: big("price").notNull(),
    weight: integer("weight").notNull(),
  },
  (t) => [index("items_case_idx").on(t.caseId)],
);

export const inventory = pgTable(
  "inventory",
  {
    id: serial("id").primaryKey(),
    userId: integer("user_id").notNull(),
    itemId: integer("item_id").notNull(),
    status: text("status").notNull().default("owned"), // owned | sold | upgraded | burned
    createdAt: timestamp("created_at").notNull().defaultNow(),
  },
  (t) => [index("inv_user_idx").on(t.userId, t.status)],
);

export const promoCodes = pgTable("promo_codes", {
  code: text("code").primaryKey(),
  reward: big("reward").notNull(),
  maxUses: integer("max_uses").notNull().default(100),
  uses: integer("uses").notNull().default(0),
  active: boolean("active").notNull().default(true),
});

export const promoUses = pgTable(
  "promo_uses",
  {
    userId: integer("user_id").notNull(),
    code: text("code").notNull(),
  },
  (t) => [primaryKey({ columns: [t.userId, t.code] })],
);

export const adminLogs = pgTable("admin_logs", {
  id: serial("id").primaryKey(),
  adminId: integer("admin_id").notNull(),
  action: text("action").notNull(),
  details: text("details"),
  createdAt: timestamp("created_at").notNull().defaultNow(),
});
