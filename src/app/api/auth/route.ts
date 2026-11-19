import { eq } from "drizzle-orm";
import { db } from "@/db";
import { users } from "@/db/schema";
import { AppError, adminIds, body, createUser, endSession, handle, publicUser, sessionUser, startSession } from "@/lib/core";
import { validateInitData } from "@/lib/telegram";
import { ensureSeed } from "@/lib/seed";

export const dynamic = "force-dynamic";

// GET — текущий пользователь
export async function GET() {
  return handle(async () => {
    await ensureSeed();
    const u = await sessionUser();
    return {
      user: u ? publicUser(u) : null,
      config: { telegram: !!process.env.BOT_TOKEN, guest: process.env.DISABLE_GUEST !== "true", botName: process.env.BOT_USERNAME || "" },
    };
  });
}

// POST — вход: { initData } | { guestName } | { action: "logout" }
export async function POST(req: Request) {
  return handle(async () => {
    const b = await body<{ initData?: string; guestName?: string; ref?: string; action?: string }>(req);
    if (b.action === "logout") {
      await endSession();
      return { ok: true };
    }
    if (b.initData) {
      const v = validateInitData(b.initData);
      if (!v) throw new AppError("Не удалось проверить Telegram-авторизацию", 401);
      const name = v.user.first_name || v.user.username || "Игрок";
      let [u] = await db.select().from(users).where(eq(users.tgId, v.user.id)).limit(1);
      if (u?.banned) throw new AppError("Аккаунт заблокирован", 403);
      if (!u) u = await createUser({ tgId: v.user.id, username: v.user.username ?? null, name, ref: b.ref || v.startParam });
      else {
        const isAdmin = u.isAdmin || adminIds().includes(v.user.id);
        await db.update(users).set({ name, username: v.user.username ?? null, isAdmin }).where(eq(users.id, u.id));
        u = { ...u, name, isAdmin };
      }
      await startSession(u.id);
      return { user: publicUser(u) };
    }
    if (process.env.DISABLE_GUEST === "true") throw new AppError("Откройте приложение через Telegram", 403);
    const name = String(b.guestName || "").trim().slice(0, 24);
    if (name.length < 2) throw new AppError("Введите имя (минимум 2 символа)");
    const u = await createUser({ name, ref: b.ref });
    await startSession(u.id);
    return { user: publicUser(u) };
  });
}
