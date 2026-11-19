"use client";
import Link from "next/link";
import { use } from "react";
import { OddButton, StatusBadge, TeamBadge } from "@/components/EventCard";
import { Ev, SPORT_META, useFetch } from "@/lib/client";

const RES: Record<string, string> = { won: "✅ Выиграл", lost: "✖ Проиграл", void: "↩ Возврат" };

export default function EventPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, loading, error } = useFetch<{ event: Ev }>(`/api/events/${id}`, 4000);
  const ev = data?.event;
  if (loading && !data) return <div className="skeleton h-64" />;
  if (!ev) return <div className="card p-8 text-center text-sm text-muted">{error || "Матч не найден"}</div>;

  return (
    <div className="space-y-3">
      <Link href="/" className="text-sm font-bold text-muted">‹ Назад к линии</Link>
      <div className="card fade-up relative overflow-hidden p-5">
        <div className="absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-neon/10 to-transparent" />
        <div className="relative mb-4 flex items-center justify-between text-xs font-bold text-muted">
          <span>{SPORT_META[ev.sport]?.emoji} {ev.league}</span>
          <StatusBadge ev={ev} />
        </div>
        <div className="relative flex items-center justify-between gap-2">
          <div className="flex w-[38%] flex-col items-center gap-2 text-center">
            <TeamBadge name={ev.home} size={56} />
            <span className="text-sm font-extrabold leading-tight">{ev.home}</span>
          </div>
          <div className="text-center">
            {ev.status === "upcoming" ? (
              <div className="text-2xl font-black text-muted">VS</div>
            ) : (
              <div className="text-4xl font-black tabular-nums">
                {ev.homeScore}<span className="mx-1 text-muted">:</span>{ev.awayScore}
              </div>
            )}
          </div>
          <div className="flex w-[38%] flex-col items-center gap-2 text-center">
            <TeamBadge name={ev.away} size={56} />
            <span className="text-sm font-extrabold leading-tight">{ev.away}</span>
          </div>
        </div>
      </div>

      {ev.markets.map((m) => {
        const cols = m.outcomes.length === 3 ? "grid-cols-3" : "grid-cols-2";
        const done = m.status === "settled" || m.status === "void";
        return (
          <div key={m.id} className="card fade-up p-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-black">{m.name}</div>
              {m.status === "suspended" && <span className="text-[11px] font-bold text-gold">Приостановлен</span>}
              {m.status === "settled" && <span className="text-[11px] font-bold text-muted">Рассчитан</span>}
              {m.status === "void" && <span className="text-[11px] font-bold text-gold">Возврат</span>}
            </div>
            <div className={`grid ${cols} gap-1.5`}>
              {m.outcomes.map((o) => (
                <div key={o.id} className="flex flex-col gap-1">
                  <div className="flex">
                    <OddButton ev={ev} m={m} o={o} />
                  </div>
                  {done && <div className={`text-center text-[11px] font-bold ${o.result === "won" ? "up" : "text-muted"}`}>{RES[o.result]}</div>}
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
