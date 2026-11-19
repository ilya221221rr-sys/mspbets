"use client";
import { useState } from "react";
import { fmt, useFetch } from "@/lib/client";

type Row = { id: number; name: string; username: string | null; value: number; level: number };

export default function Rating() {
  const [type, setType] = useState<"week" | "balance">("week");
  const { data, loading } = useFetch<{ rows: Row[]; me: number }>(`/api/rating?type=${type}`, 20000);
  const rows = data?.rows ?? [];
  const medal = ["🥇", "🥈", "🥉"];
  return (
    <div className="space-y-3">
      <h1 className="px-1 text-xl font-black">Рейтинг игроков</h1>
      <div className="grid grid-cols-2 gap-1 rounded-2xl bg-panel p-1">
        <button onClick={() => setType("week")} className={`rounded-xl py-2 text-sm font-extrabold ${type === "week" ? "bg-panel2 text-neon" : "text-muted"}`}>Прибыль за неделю</button>
        <button onClick={() => setType("balance")} className={`rounded-xl py-2 text-sm font-extrabold ${type === "balance" ? "bg-panel2 text-neon" : "text-muted"}`}>Баланс</button>
      </div>
      <p className="px-1 text-xs text-muted">
        {type === "week" ? "Считается результат ставок и игр за 7 дней. Бонусы и промокоды не учитываются." : "Текущий баланс игроков."}
      </p>
      {loading && !data ? (
        <div className="skeleton h-60" />
      ) : rows.length === 0 ? (
        <div className="card p-8 text-center text-sm text-muted">Пока никого нет</div>
      ) : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={r.id} className={`card fade-up flex items-center gap-3 p-3 ${r.id === data?.me ? "!border-neon/60" : ""}`}>
              <div className="w-8 text-center text-lg font-black">{medal[i] ?? i + 1}</div>
              <div className="min-w-0 flex-1">
                <div className="truncate font-extrabold">{r.name} {r.id === data?.me && <span className="text-xs text-neon">(вы)</span>}</div>
                <div className="text-[11px] text-muted">Ур. {r.level}{r.username ? ` · @${r.username}` : ""}</div>
              </div>
              <b className={r.value < 0 ? "text-danger" : "text-neon"}>{type === "week" && r.value > 0 ? "+" : ""}{fmt(r.value)}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
