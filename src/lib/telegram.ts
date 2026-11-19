import { createHmac, timingSafeEqual } from "node:crypto";

export function validateInitData(initData: string) {
  const token = process.env.BOT_TOKEN;
  if (!token || !initData) return null;
  try {
    const params = new URLSearchParams(initData);
    const hash = params.get("hash");
    if (!hash) return null;
    params.delete("hash");
    const check = [...params.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}=${v}`)
      .join("\n");
    const key = createHmac("sha256", "WebAppData").update(token).digest();
    const calc = createHmac("sha256", key).update(check).digest("hex");
    if (calc.length !== hash.length || !timingSafeEqual(Buffer.from(calc), Buffer.from(hash))) return null;
    const authDate = Number(params.get("auth_date") || 0);
    if (Math.abs(Date.now() / 1000 - authDate) > 86400) return null;
    const user = JSON.parse(params.get("user") || "{}") as { id?: number; username?: string; first_name?: string };
    const startParam = params.get("start_param");
    return user.id ? { user: { ...user, id: user.id }, startParam } : null;
  } catch {
    return null;
  }
}

export async function tgSend(chatId: number, text: string, webAppButton = false) {
  const token = process.env.BOT_TOKEN;
  if (!token) return false;
  try {
    const url = process.env.WEBAPP_URL;
    const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: "HTML",
        disable_web_page_preview: true,
        ...(webAppButton && url
          ? { reply_markup: { inline_keyboard: [[{ text: "🎯 Открыть MSPBETS", web_app: { url } }]] } }
          : {}),
      }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export type Notice = { tgId: number | null; text: string };

export function sendNotices(list: Notice[]) {
  for (const n of list) if (n.tgId) void tgSend(n.tgId, n.text, true);
}
