"use client";
import Link from "next/link";
import { useState } from "react";
import { useAuth, useToast } from "@/components/Providers";
import { ApiError, api, fmt, startLabel, useFetch } from "@/lib/client";

type Profile = {
  daily: { available: boolean; nextAt: number; reward: number; streak: number };
  stats: { total: number; won: number; referrals: number };
  history: { id: number; amount: number; type: string; note: string | null; createdAt: string }[];
};

const TYPES: Record<string, string> = {
  signup: "🎉 Бонус", daily: "🎁 Ежедневный бонус", promo: "🏷️ Промокод", referral: "👥 Рефералка", admin: "🛠 Администратор",
  bet_stake: "🎯 Ставка", bet_win: "✅ Выигрыш", bet_refund: "↩ Возврат", bet_cashout: "💸 Кешаут",
  crash_bet: "🚀 Краш", crash_win: "🚀 Краш", mines_bet: "💣 Мины", mines_win: "💣 Мины", dice_bet: "🎲 Дайс", dice_win: "🎲 Дайс",
  case_open: "📦 Кейс", case_sell: "💰 Продажа предмета",
};

export default function ProfilePage() {
  const { user, setBalance, logout, config } = useAuth();
  const toast = useToast();
  const { data, reload } = useFetch<Profile>("/api/profile");
  const [code, setCode] = useState("");
  if (!user) return null;

  const refLink = config?.botName
    ? `https://t.me/${config.botName}?startapp=${user.refCode}`
    : typeof window !== "undefined"
      ? `${window.location.origin}/?ref=${user.refCode}`
      : "";

  async function act(action: string, extra: object = {}) {
    try {
      const r = await api<{ reward?: number; balance: number }>("/api/profile", { action, ...extra });
      setBalance(r.balance);
      toast(`+${fmt(r.reward ?? 0)} PTS 🎉`);
      setCode("");
      await reload();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Ошибка", "err");
    }
  }

  const d = data?.daily;
  return (
    <div className="space-y-3">
      <div className="card fade-up p-4">
        <div className="flex items-center gap-3">
          <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-neon to-neon2 text-2xl font-black text-[#031200]">
            {user.name.slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-lg font-black">{user.name}</div>
            <div className="text-xs font-bold text-muted">Ур. {user.level} · {user.title}</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] font-bold uppercase text-muted">Баланс</div>
            <div className="text-xl font-black">{fmt(user.balance)} <span className="text-xs text-neon">PTS</span></div>
          </div>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-panel2">
          <div className="h-full rounded-full bg-gradient-to-r from-neon2 to-neon transition-all" style={{ width: `${Math.round(user.progress * 100)}%` }} />
        </div>
        <div className="mt-1 text-[11px] text-muted">До следующего уровня: {fmt(user.xpToNext)} XP</div>
      </div>

      <div className="card fade-up p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="font-black">🎁 Ежедневный бонус</div>
          <div className="text-xs font-bold text-muted">серия {d?.streak ?? 0}</div>
        </div>
        <div className="mb-3 flex justify-between gap-1">
          {Array.from({ length: 7 }, (_, i) => (
            <div key={i} className={`grid h-9 flex-1 place-items-center rounded-lg text-xs font-black ${i < (d?.streak ?? 0) % 7 || ((d?.streak ?? 0) >= 7 && (d?.streak ?? 0) % 7 === 0 && !d?.available) ? "bg-neon text-[#031200]" : "bg-panel2 text-muted"}`}>
              {i + 1}
            </div>
          ))}
        </div>
        <button className="btn btn-primary w-full" disabled={!d?.available} onClick={() => act("daily")}>
          {d?.available ? `Забрать ${fmt(d.reward)} PTS` : d?.nextAt ? `Следующий бонус: ${startLabel(new Date(d.nextAt))}` : "…"}
        </button>
      </div>

      <div className="card fade-up p-4">
        <div className="mb-2 font-black">🏷️ Промокод</div>
        <div className="flex gap-2">
          <input placeholder="Введите код" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} />
          <button className="btn btn-primary" disabled={!code} onClick={() => act("promo", { code })}>ОК</button>
        </div>
      </div>

      <div className="card fade-up p-4">
        <div className="mb-1 font-black">👥 Пригласи друга</div>
        <p className="mb-3 text-xs text-muted">Друг получит +200 PTS, а вы — +300 PTS, когда он сделает первую ставку. Приглашено: {data?.stats.referrals ?? 0}</p>
        <div className="flex gap-2">
          <input readOnly value={refLink} />
          <button className="btn btn-ghost" onClick={() => { void navigator.clipboard?.writeText(refLink); toast("Ссылка скопирована"); }}>Копировать</button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        {[
          ["Ставок", data?.stats.total ?? 0],
          ["Выиграно", data?.stats.won ?? 0],
          ["XP", user.xp],
        ].map(([l, v]) => (
          <div key={l as string} className="card p-3 text-center">
            <div className="text-lg font-black">{fmt(v as number)}</div>
            <div className="text-[11px] text-muted">{l}</div>
          </div>
        ))}
      </div>

      <div className="card p-4">
        <div className="mb-3 font-black">История баллов</div>
        <div className="space-y-2.5">
          {(data?.history ?? []).map((h) => (
            <div key={h.id} className="flex items-center justify-between text-sm">
              <div className="min-w-0">
                <div className="truncate font-bold">{TYPES[h.type] ?? h.type}</div>
                <div className="truncate text-[11px] text-muted">{h.note} · {startLabel(h.createdAt)}</div>
              </div>
              <b className={h.amount > 0 ? "text-neon" : "text-danger"}>{h.amount > 0 ? "+" : ""}{fmt(h.amount)}</b>
            </div>
          ))}
          {!data?.history.length && <div className="text-center text-sm text-muted">Пока пусто</div>}
        </div>
      </div>

      <div className="flex gap-2">
        <Link href="/admin" className="btn btn-ghost flex-1">🛠 Админка</Link>
        <button className="btn btn-danger flex-1" onClick={() => logout()}>Выйти</button>
      </div>
      <p className="px-2 pb-2 text-center text-[11px] leading-relaxed text-muted">
        MSPBETS — развлекательный проект. Баллы виртуальные: их нельзя купить, вывести, обменять или получить за них денежный приз. 18+
      </p>
    </div>
  );
}
