"use client";
import { useEffect, useState } from "react";
import { ApiError, Ev, api, fmt } from "@/lib/client";
import { useAuth, useSlip, useToast } from "./Providers";

const MAX_STAKE = 100000;

export default function SlipSheet() {
  const { sels, open, setOpen, remove, clear, update } = useSlip();
  const { user, setBalance } = useAuth();
  const toast = useToast();
  const [mode, setMode] = useState<"single" | "express">("single");
  const [stake, setStake] = useState(100);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (sels.length < 2) setMode("single");
    else if (sels.length === 2 && !open) setMode("express");
  }, [sels.length, open]);

  useEffect(() => {
    if (!open || !sels.length) return;
    let dead = false;
    (async () => {
      const ids = [...new Set(sels.map((s) => s.eventId))];
      const evs = (await Promise.all(ids.map((id) => api<{ event: Ev }>(`/api/events/${id}`).catch(() => null)))).filter(Boolean) as { event: Ev }[];
      if (dead) return;
      update((cur) =>
        cur.map((s) => {
          const ev = evs.find((e) => e.event.id === s.eventId)?.event;
          if (!ev) return s;
          const m = ev.markets.find((m) => m.outcomes.some((o) => o.id === s.outcomeId));
          const o = m?.outcomes.find((o) => o.id === s.outcomeId);
          if (!m || !o) return { ...s, closed: true };
          const closed =
            m.status !== "open" ||
            !(ev.status === "live" || (ev.status === "upcoming" && new Date(ev.startsAt).getTime() > Date.now()));
          return { ...s, closed, oldOdds: o.odds !== s.odds ? s.odds : s.oldOdds, odds: o.odds };
        }),
      );
    })();
    return () => {
      dead = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;
  const express = mode === "express" && sels.length > 1;
  const prod = Math.min(1000, sels.reduce((a, s) => a * s.odds, 1));
  const totalStake = express ? stake : stake * sels.length;
  const potential = express ? Math.floor(stake * prod) : sels.reduce((a, s) => a + Math.floor(stake * s.odds), 0);
  const hasClosed = sels.some((s) => s.closed);

  async function place() {
    setBusy(true);
    try {
      const r = await api<{ balance: number; bets: number[] }>("/api/bets", {
        mode: express ? "express" : "single",
        stake,
        items: sels.map((s) => ({ outcomeId: s.outcomeId, odds: s.odds })),
      });
      setBalance(r.balance);
      toast(r.bets.length > 1 ? `Принято ставок: ${r.bets.length}` : "Ставка принята! Удачи 🍀");
      clear();
      setOpen(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        const changed = (e.data.changed as { outcomeId: number; odds: number }[] | undefined) ?? [];
        const closed = (e.data.closed as number[] | undefined) ?? [];
        update((cur) =>
          cur.map((s) => {
            const c = changed.find((x) => x.outcomeId === s.outcomeId);
            return {
              ...s,
              closed: closed.includes(s.outcomeId) ? true : s.closed,
              oldOdds: c ? s.odds : s.oldOdds,
              odds: c ? c.odds : s.odds,
            };
          }),
        );
        toast(changed.length ? "Коэффициенты изменились — проверьте купон" : e.message, "err");
      } else toast(e instanceof Error ? e.message : "Ошибка", "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 backdrop-blur-sm" onClick={() => setOpen(false)}>
      <div className="sheet max-h-[88dvh] w-full max-w-xl overflow-y-auto rounded-t-3xl border border-line bg-panel p-4 pb-6" onClick={(e) => e.stopPropagation()}>
        <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-line" />
        <div className="mb-3 flex items-center justify-between">
          <div className="text-lg font-black">Купон</div>
          <button className="text-xs font-bold text-muted" onClick={() => clear()}>Очистить</button>
        </div>

        {sels.length > 1 && (
          <div className="mb-3 grid grid-cols-2 gap-1 rounded-2xl bg-panel2 p-1">
            {(["single", "express"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`rounded-xl py-2 text-sm font-extrabold ${mode === m ? "bg-neon text-[#031200]" : "text-muted"}`}
              >
                {m === "single" ? "Ординар" : "Экспресс"}
              </button>
            ))}
          </div>
        )}

        <div className="space-y-2">
          {sels.map((s) => (
            <div key={s.outcomeId} className={`rounded-2xl border p-3 ${s.closed ? "border-danger/50 bg-danger/5" : "border-line bg-panel2"}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-bold">{s.event}</div>
                  <div className="truncate text-xs text-muted">{s.market}</div>
                  <div className="mt-1 text-sm font-extrabold text-neon">{s.pick}</div>
                  {s.closed && <div className="mt-1 text-xs font-bold text-danger">Ставки на это событие закрыты</div>}
                </div>
                <div className="text-right">
                  <div className="text-lg font-black">{s.odds.toFixed(2)}</div>
                  {s.oldOdds && (
                    <div className={`text-[11px] font-bold ${s.odds > s.oldOdds ? "up" : "down"}`}>
                      было {s.oldOdds.toFixed(2)} {s.odds > s.oldOdds ? "▲" : "▼"}
                    </div>
                  )}
                  <button className="mt-1 text-[11px] text-muted underline" onClick={() => remove(s.outcomeId)}>убрать</button>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4">
          <div className="mb-1 flex justify-between text-xs font-bold text-muted">
            <span>{express ? "Сумма ставки" : sels.length > 1 ? "Сумма на каждое событие" : "Сумма ставки"}</span>
            <span>Баланс: {fmt(user?.balance ?? 0)}</span>
          </div>
          <input type="number" inputMode="numeric" value={stake || ""} min={10} onChange={(e) => setStake(Math.max(0, Math.floor(Number(e.target.value))))} />
          <div className="mt-2 flex gap-2">
            {[50, 100, 500, 1000].map((v) => (
              <button key={v} className="chip flex-1 !px-0 text-center" onClick={() => setStake(v)}>{v}</button>
            ))}
            <button className="chip flex-1 !px-0 text-center" onClick={() => setStake(Math.min(MAX_STAKE, user?.balance ?? 0))}>Макс</button>
          </div>
        </div>

        <div className="mt-4 space-y-1.5 rounded-2xl bg-panel2 p-3 text-sm">
          {express && (
            <div className="flex justify-between"><span className="text-muted">Общий коэффициент</span><b>{prod.toFixed(2)}</b></div>
          )}
          <div className="flex justify-between"><span className="text-muted">Списание</span><b>{fmt(totalStake)} PTS</b></div>
          <div className="flex justify-between"><span className="text-muted">Возможный выигрыш</span><b className="text-neon">{fmt(potential)} PTS</b></div>
        </div>

        <button className="btn btn-primary mt-4 w-full !py-4 text-base" disabled={busy || hasClosed || stake < 10 || totalStake > (user?.balance ?? 0)} onClick={place}>
          {busy ? "Отправка…" : totalStake > (user?.balance ?? 0) ? "Недостаточно баллов" : stake < 10 ? "Минимум 10 PTS" : "Сделать ставку"}
        </button>
        <p className="mt-3 text-center text-[11px] text-muted">Только виртуальные баллы. Мин. ставка 10, макс. {fmt(MAX_STAKE)} PTS.</p>
      </div>
    </div>
  );
}
