"use client";
import Link from "next/link";
import { useState } from "react";
import EventCard, { EventSkeleton } from "@/components/EventCard";
import { Ev, SPORT_META, useFetch } from "@/lib/client";

export default function Home() {
  const [sport, setSport] = useState("all");
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"line" | "results">("line");
  const url = `/api/events?status=${tab}&sport=${sport}${q.trim() ? `&q=${encodeURIComponent(q.trim())}` : ""}`;
  const { data, loading } = useFetch<{ events: Ev[] }>(url, tab === "line" ? 8000 : 0);
  const events = data?.events ?? [];
  const top = tab === "line" && !q ? events.filter((e) => e.featured && e.status === "upcoming").slice(0, 4) : [];

  const groups: Record<string, Ev[]> = {};
  for (const e of events) (groups[`${SPORT_META[e.sport]?.emoji ?? ""} ${e.league}`] ??= []).push(e);

  return (
    <div className="space-y-4">
      <div className="card fade-up relative overflow-hidden p-5">
        <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-neon/20 blur-3xl" />
        <div className="text-[11px] font-black uppercase tracking-[3px] text-neon">MSPBETS</div>
        <h1 className="mt-1 text-[26px] font-black italic leading-[1.05]">
          Здесь начинается <span className="text-neon">победа.</span>
        </h1>
        <p className="mt-2 text-[13px] text-muted">Ставь на киберспорт и футбол, забирай бонусы и поднимайся в рейтинге.</p>
        <div className="mt-4 flex gap-2">
          <Link href="/profile" className="btn btn-primary !py-2.5">🎁 Ежедневный бонус</Link>
          <Link href="/games" className="btn btn-ghost !py-2.5">🎮 Игры</Link>
        </div>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        <button className={`chip ${sport === "all" ? "on" : ""}`} onClick={() => setSport("all")}>Все</button>
        {Object.entries(SPORT_META).map(([k, v]) => (
          <button key={k} className={`chip ${sport === k ? "on" : ""}`} onClick={() => setSport(k)}>
            {v.emoji} {v.name}
          </button>
        ))}
      </div>

      <input placeholder="🔍  Поиск команды или турнира" value={q} onChange={(e) => setQ(e.target.value)} />

      <div className="grid grid-cols-2 gap-1 rounded-2xl bg-panel p-1">
        {(["line", "results"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`rounded-xl py-2 text-sm font-extrabold ${tab === t ? "bg-panel2 text-neon" : "text-muted"}`}>
            {t === "line" ? "Линия" : "Результаты"}
          </button>
        ))}
      </div>

      {loading && !data ? (
        <EventSkeleton />
      ) : events.length === 0 ? (
        <div className="card p-8 text-center text-sm text-muted">Событий не найдено</div>
      ) : (
        <>
          {top.length > 0 && (
            <section>
              <div className="mb-2 text-sm font-black">🔥 Топ-события</div>
              <div className="flex gap-3 overflow-x-auto pb-1">
                {top.map((e) => (
                  <div key={e.id} className="w-[85%] shrink-0">
                    <EventCard ev={e} />
                  </div>
                ))}
              </div>
            </section>
          )}
          {Object.entries(groups).map(([league, list]) => (
            <section key={league} className="space-y-2.5">
              <div className="px-1 pt-1 text-[13px] font-black text-muted">{league}</div>
              {list.map((e) => (
                <EventCard key={e.id} ev={e} />
              ))}
            </section>
          ))}
        </>
      )}
    </div>
  );
}
