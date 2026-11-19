"use client";
import Link from "next/link";
import { Ev, Market, Outcome, SPORT_META, odd, startLabel } from "@/lib/client";
import { useSlip } from "./Providers";

export function isBettable(ev: Ev, m: Market) {
  return m.status === "open" && (ev.status === "live" || (ev.status === "upcoming" && new Date(ev.startsAt).getTime() > Date.now()));
}

export function TeamBadge({ name, size = 34 }: { name: string; size?: number }) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 360;
  const ini = name
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <span
      className="grid shrink-0 place-items-center rounded-full font-black text-white"
      style={{ width: size, height: size, fontSize: size * 0.36, background: `linear-gradient(135deg, hsl(${h} 70% 38%), hsl(${(h + 50) % 360} 70% 22%))`, border: "1px solid rgba(255,255,255,.12)" }}
    >
      {ini}
    </span>
  );
}

const SHORT: Record<string, string> = { h: "П1", d: "Х", a: "П2" };

export function OddButton({ ev, m, o, short }: { ev: Ev; m: Market; o: Outcome; short?: boolean }) {
  const slip = useSlip();
  const ok = isBettable(ev, m);
  const sel = slip.has(o.id);
  const label = short && (m.type === "1x2" || m.type === "ml") ? SHORT[o.key] : o.label;
  const diff = o.odds - o.prevOdds;
  return (
    <button
      disabled={!ok}
      className={`odd ${sel ? "sel" : ""}`}
      onClick={() =>
        slip.toggle({ outcomeId: o.id, eventId: ev.id, odds: o.odds, event: `${ev.home} — ${ev.away}`, market: m.name, pick: o.label })
      }
    >
      <div className="lbl">{label}</div>
      <div className="val">
        {ok ? (
          <>
            {odd(o.odds)}
            {diff > 0.001 && <span className="up ml-0.5 text-[9px]">▲</span>}
            {diff < -0.001 && <span className="down ml-0.5 text-[9px]">▼</span>}
          </>
        ) : (
          "🔒"
        )}
      </div>
    </button>
  );
}

export function StatusBadge({ ev }: { ev: Ev }) {
  if (ev.status === "live")
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-danger/15 px-2 py-0.5 text-[11px] font-black text-danger">
        <span className="live-dot" /> LIVE
      </span>
    );
  if (ev.status === "finished") return <span className="text-[11px] font-bold text-muted">Завершён</span>;
  if (ev.status === "cancelled") return <span className="text-[11px] font-bold text-gold">Отменён</span>;
  return <span className="text-[11px] font-bold text-muted">{startLabel(ev.startsAt)}</span>;
}

export default function EventCard({ ev }: { ev: Ev }) {
  const main = ev.markets[0];
  const showScore = ev.status !== "upcoming";
  return (
    <div className="card fade-up overflow-hidden">
      <Link href={`/event/${ev.id}`} className="block p-3 pb-2">
        <div className="mb-2.5 flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-1.5 text-[11.5px] font-bold text-muted">
            <span>{SPORT_META[ev.sport]?.emoji}</span>
            <span className="truncate">{ev.league}</span>
            {ev.featured && ev.status === "upcoming" && <span className="rounded bg-gold/15 px-1.5 text-[10px] font-black text-gold">ТОП</span>}
          </div>
          <StatusBadge ev={ev} />
        </div>
        {[
          [ev.home, ev.homeScore],
          [ev.away, ev.awayScore],
        ].map(([name, score]) => (
          <div key={name as string} className="flex items-center justify-between py-1">
            <div className="flex min-w-0 items-center gap-2.5">
              <TeamBadge name={name as string} />
              <span className="truncate text-[15px] font-extrabold">{name}</span>
            </div>
            {showScore && <span className="text-xl font-black tabular-nums">{score}</span>}
          </div>
        ))}
      </Link>
      {main && ev.status !== "finished" && ev.status !== "cancelled" && (
        <div className="flex gap-1.5 px-3 pb-3">
          {main.outcomes.map((o) => (
            <OddButton key={o.id} ev={ev} m={main} o={o} short />
          ))}
        </div>
      )}
      <Link href={`/event/${ev.id}`} className="flex items-center justify-between border-t border-line px-3 py-2 text-xs font-bold text-muted">
        <span>Все рынки · {ev.markets.length}</span>
        <span>›</span>
      </Link>
    </div>
  );
}

export function EventSkeleton({ n = 3 }: { n?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="skeleton h-40" />
      ))}
    </div>
  );
}
