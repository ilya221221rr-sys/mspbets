"use client";
import EventCard, { EventSkeleton } from "@/components/EventCard";
import { Ev, useFetch } from "@/lib/client";

export default function Live() {
  const { data, loading } = useFetch<{ events: Ev[] }>("/api/events?status=live", 4000);
  const events = data?.events ?? [];
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <span className="live-dot" />
        <h1 className="text-xl font-black">Live</h1>
        <span className="text-xs text-muted">коэффициенты обновляются по ходу матча</span>
      </div>
      {loading && !data ? (
        <EventSkeleton n={2} />
      ) : events.length === 0 ? (
        <div className="card p-8 text-center text-sm text-muted">Сейчас нет матчей в прямом эфире</div>
      ) : (
        events.map((e) => <EventCard key={e.id} ev={e} />)
      )}
    </div>
  );
}
