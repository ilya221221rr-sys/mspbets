"use client";
import { useState } from "react";
import { useAuth, useToast } from "@/components/Providers";
import { api, fmt, startLabel, useFetch } from "@/lib/client";

type Sel = { id: number; eventId: number; event: string; market: string; pick: string; odds: number; status: string; eventStatus: string; score: string | null };
type Bet = {
  id: number;
  type: string;
  stake: number;
  totalOdds: number;
  status: string;
  payout: number;
  createdAt: string;
  potential: number;
  cashout: number | null;
  selections: Sel[];
};

const ST: Record<string, { t: string; c: string }> = {
  pending: { t: "В игре", c: "text-gold bg-gold/10" },
  won: { t: "Выиграла", c: "text-neon bg-neon/10" },
  lost: { t: "Проиграла", c: "text-danger bg-danger/10" },
  void: { t: "Возврат", c: "text-muted bg-white/5" },
  cashed: { t: "Кешаут", c: "text-neon bg-neon/10" },
};

export default function Bets() {
  const [tab, setTab] = useState<"active" | "history">("active");
  const { data, loading, reload } = useFetch<{ bets: Bet[] }>("/api/bets", 7000);
  const { setBalance } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState<number | null>(null);
  const bets = (data?.bets ?? []).filter((b) => (tab === "active") === (b.status === "pending"));

  async function cashout(b: Bet) {
    if (!confirm(`Забрать ${fmt(b.cashout ?? 0)} PTS сейчас?`)) return;
    setBusy(b.id);
    try {
      const r = await api<{ value: number; balance: number }>("/api/bets", { action: "cashout", betId: b.id });
      setBalance(r.balance);
      toast(`Кешаут: +${fmt(r.value)} PTS`);
      await reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
      await reload();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <h1 className="px-1 text-xl font-black">Мои ставки</h1>
      <div className="grid grid-cols-2 gap-1 rounded-2xl bg-panel p-1">
        {(["active", "history"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-xl py-2 text-sm font-extrabold ${tab === t ? "bg-panel2 text-neon" : "text-muted"}`}>
            {t === "active" ? "Активные" : "История"}
          </button>
        ))}
      </div>
      {loading && !data ? (
        <div className="skeleton h-40" />
      ) : bets.length === 0 ? (
        <div className="card p-8 text-center text-sm text-muted">{tab === "active" ? "Нет активных ставок. Самое время сделать первую!" : "История пуста"}</div>
      ) : (
        bets.map((b) => {
          const st = ST[b.status];
          return (
            <div key={b.id} className="card fade-up p-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="text-xs font-bold text-muted">
                  {b.type === "express" ? `Экспресс ×${b.selections.length}` : "Ординар"} · #{b.id} · {startLabel(b.createdAt)}
                </div>
                <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-black ${st.c}`}>{st.t}</span>
              </div>
              <div className="space-y-2">
                {b.selections.map((s) => (
                  <div key={s.id} className="flex items-start justify-between gap-2 border-l-2 pl-2.5" style={{ borderColor: s.status === "won" ? "#5dff1f" : s.status === "lost" ? "#ff4d5e" : "#20292c" }}>
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-bold">
                        {s.event} {s.score && <span className="text-muted">({s.score})</span>}
                      </div>
                      <div className="truncate text-xs text-muted">{s.market}: <span className="font-bold text-white">{s.pick}</span></div>
                    </div>
                    <b className="text-sm">{s.odds.toFixed(2)}</b>
                  </div>
                ))}
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 rounded-xl bg-panel2 p-2.5 text-center text-xs">
                <div><div className="text-muted">Ставка</div><b className="text-sm">{fmt(b.stake)}</b></div>
                <div><div className="text-muted">Кф</div><b className="text-sm">{b.totalOdds.toFixed(2)}</b></div>
                <div>
                  <div className="text-muted">{b.status === "pending" ? "Возможно" : "Выплата"}</div>
                  <b className={`text-sm ${b.payout > 0 ? "text-neon" : ""}`}>{fmt(b.status === "pending" ? b.potential : b.payout)}</b>
                </div>
              </div>
              {b.cashout && (
                <button className="btn btn-gold mt-2.5 w-full" disabled={busy === b.id} onClick={() => cashout(b)}>
                  💸 Кешаут {fmt(b.cashout)} PTS
                </button>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
