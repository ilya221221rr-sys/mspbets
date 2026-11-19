import { NextResponse } from "next/server";
import { tgSend } from "@/lib/telegram";

export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const secret = process.env.WEBHOOK_SECRET;
  if (secret && req.headers.get("x-telegram-bot-api-secret-token") !== secret) {
    return NextResponse.json({ ok: false }, { status: 403 });
  }
  try {
    const update = (await req.json()) as { message?: { text?: string; chat?: { id: number } } };
    const msg = update.message;
    if (msg?.text?.startsWith("/start") && msg.chat) {
      await tgSend(
        msg.chat.id,
        "🏆 <b>MSPBETS</b> — букмекер на виртуальные баллы\n\n" +
          "Ставки на Dota 2, CS2 и футбол, краш, мины, кейсы и апгрейды.\n" +
          "Баллы не имеют денежной стоимости: их нельзя купить, вывести или обменять.\n\n" +
          "📢 Канал: https://t.me/mspbetsprizi",
        true,
      );
    }
  } catch {
    /* ignore */
  }
  return NextResponse.json({ ok: true });
}
