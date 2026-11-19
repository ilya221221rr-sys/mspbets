import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { and, eq, gte, sql } from "drizzle-orm";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { db } from "@/db";
import { ledger, users } from "@/db/schema";

export type Tx = Parameters<Parameters<typeof db.transaction>[0]>[0];
export type User = typeof users.$inferSelect;

export class AppError extends Error {
  status: number;
  data?: Record<string, unknown>;
  constructor(message: string, status = 400, data?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export const START_BALANCE = 1000;
export const MIN_STAKE = 10;
export const MAX_STAKE = 100_000;
export const MAX_PAYOUT = 5_000_000;

/** Единственный способ менять баланс: атомарно + запись в журнал. */
export async function change(
  tx: Tx,
  userId: number,
  delta: number,
  type: string,
  note?: string,
  ref?: string,
): Promise<number> {
  delta = Math.trunc(delta);
  const cond = delta < 0 ? and(eq(users.id, userId), gte(users.balance, -delta)) : eq(users.id, userId);
  const [row] = await tx
    .update(users)
    .set({ balance: sql`${users.balance} + ${delta}` })
    .where(cond)
    .returning({ balance: users.balance });
  if (!row) throw new AppError(delta < 0 ? "Недостаточно баллов" : "Игрок не найден", 400);
  if (delta !== 0) await tx.insert(ledger).values({ userId, amount: delta, balanceAfter: row.balance, type, note, ref });
  return row.balance;
}

export function intIn(v: unknown, min: number, max: number, label = "Значение"): number {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n) || !Number.isInteger(n)) throw new AppError(`${label}: нужно целое число`);
  if (n < min || n > max) throw new AppError(`${label}: от ${min} до ${max}`);
  return n;
}

export async function body<T = Record<string, unknown>>(req: Request): Promise<T> {
  try {
    return (await req.json()) as T;
  } catch {
    return {} as T;
  }
}

export async function handle(fn: () => Promise<unknown>) {
  try {
    const data = await fn();
    return NextResponse.json(data ?? { ok: true });
  } catch (e) {
    if (e instanceof AppError) return NextResponse.json({ error: e.message, ...(e.data ?? {}) }, { status: e.status });
    console.error(e);
    return NextResponse.json({ error: "Ошибка сервера" }, { status: 500 });
  }
}

/* ---------------- Сессия ---------------- */
const COOKIE = "msp_session";
const secret = () => process.env.SESSION_SECRET || process.env.BOT_TOKEN || "mspbets-dev-secret-change-me";
const sign = (v: string) => createHmac("sha256", secret()).update(v).digest("base64url");

export async function startSession(userId: number) {
  const exp = Date.now() + 30 * 24 * 3600 * 1000;
  const payload = `${userId}.${exp}`;
  const jar = await cookies();
  jar.set(COOKIE, `${payload}.${sign(payload)}`, {
    httpOnly: true,
    sameSite: "lax",
    secure: false,
    path: "/",
    maxAge: 30 * 24 * 3600,
  });
}

export async function endSession() {
  (await cookies()).delete(COOKIE);
}

export const adminIds = () =>
  (process.env.ADMIN_IDS || "")
    .split(",")
    .map((x) => Number(x.trim()))
    .filter(Boolean);

export async function sessionUser(): Promise<User | null> {
  const raw = (await cookies()).get(COOKIE)?.value;
  if (!raw) return null;
  const [id, exp, sig] = raw.split(".");
  const payload = `${id}.${exp}`;
  const good = sign(payload);
  if (!sig || sig.length !== good.length || !timingSafeEqual(Buffer.from(sig), Buffer.from(good))) return null;
  if (Number(exp) < Date.now()) return null;
  const [u] = await db.select().from(users).where(eq(users.id, Number(id))).limit(1);
  if (!u || u.banned) return null;
  if (u.tgId && adminIds().includes(u.tgId) && !u.isAdmin) {
    await db.update(users).set({ isAdmin: true }).where(eq(users.id, u.id));
    u.isAdmin = true;
  }
  return u;
}

export async function requireUser(): Promise<User> {
  const u = await sessionUser();
  if (!u) throw new AppError("Нужно войти", 401);
  return u;
}

export async function requireAdmin(): Promise<User> {
  const u = await requireUser();
  if (!u.isAdmin) throw new AppError("Нет доступа", 403);
  return u;
}

/* ---------------- Пользователи ---------------- */
export function newRefCode() {
  return randomBytes(4).toString("hex");
}

export async function createUser(input: {
  tgId?: number | null;
  username?: string | null;
  name: string;
  ref?: string | null;
}): Promise<User> {
  return db.transaction(async (tx) => {
    const [{ c }] = await tx.select({ c: sql<number>`count(*)::int` }).from(users);
    const noAdminConfig = adminIds().length === 0 && !process.env.ADMIN_KEY;
    const isAdmin = (input.tgId ? adminIds().includes(input.tgId) : false) || (c === 0 && noAdminConfig);
    let referredBy: number | null = null;
    if (input.ref) {
      const [r] = await tx.select({ id: users.id }).from(users).where(eq(users.refCode, input.ref)).limit(1);
      if (r) referredBy = r.id;
    }
    const [u] = await tx
      .insert(users)
      .values({
        tgId: input.tgId ?? null,
        username: input.username ?? null,
        name: input.name.slice(0, 40) || "Игрок",
        balance: 0,
        refCode: newRefCode(),
        referredBy,
        isAdmin,
      })
      .returning();
    await change(tx, u.id, START_BALANCE, "signup", "Стартовый бонус");
    if (referredBy) await change(tx, u.id, 200, "referral", "Бонус за приглашение");
    const [fresh] = await tx.select().from(users).where(eq(users.id, u.id));
    return fresh;
  });
}

/* ---------------- Уровни ---------------- */
export const LEVEL_NAMES = ["Новичок", "Любитель", "Аналитик", "Профи", "Эксперт", "Легенда"];
export function levelInfo(xp: number) {
  const level = Math.floor(Math.sqrt(xp / 60)) + 1;
  const cur = 60 * (level - 1) ** 2;
  const next = 60 * level ** 2;
  return {
    level,
    title: LEVEL_NAMES[Math.min(LEVEL_NAMES.length - 1, Math.floor((level - 1) / 2))],
    progress: Math.min(1, (xp - cur) / (next - cur)),
    xpToNext: next - xp,
  };
}

export async function addXp(tx: Tx, userId: number, amount: number) {
  if (amount <= 0) return;
  await tx.update(users).set({ xp: sql`${users.xp} + ${Math.floor(amount)}` }).where(eq(users.id, userId));
}

export function publicUser(u: User) {
  return {
    id: u.id,
    name: u.name,
    username: u.username,
    balance: u.balance,
    xp: u.xp,
    streak: u.streak,
    isAdmin: u.isAdmin,
    refCode: u.refCode,
    ...levelInfo(u.xp),
  };
}
