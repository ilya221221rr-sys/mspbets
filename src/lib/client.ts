"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  status: number;
  data: Record<string, unknown>;
  constructor(message: string, status: number, data: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function api<T = any>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error || "Ошибка", res.status, data);
  return data as T;
}

export const fmt = (n: number) => Math.round(n).toLocaleString("ru-RU");
export const odd = (n: number) => n.toFixed(2);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useFetch<T = any>(url: string | null, interval = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const urlRef = useRef(url);
  urlRef.current = url;

  const load = useCallback(async () => {
    const u = urlRef.current;
    if (!u) return;
    try {
      const d = await api<T>(u);
      if (urlRef.current === u) {
        setData(d);
        setError(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void load();
    if (!interval || !url) return;
    const t = setInterval(() => {
      if (!document.hidden) void load();
    }, interval);
    return () => clearInterval(t);
  }, [url, interval, load]);

  return { data, error, loading, reload: load, setData };
}

export function startLabel(iso: string | Date) {
  const d = new Date(iso);
  const now = new Date();
  const t = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400000);
  if (diff === 0) return `Сегодня, ${t}`;
  if (diff === 1) return `Завтра, ${t}`;
  return `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })}, ${t}`;
}

export const SPORT_META: Record<string, { name: string; emoji: string }> = {
  dota2: { name: "Dota 2", emoji: "🛡️" },
  cs2: { name: "CS2", emoji: "🔫" },
  football: { name: "Футбол", emoji: "⚽" },
};

export type Outcome = { id: number; marketId: number; key: string; label: string; odds: number; prevOdds: number; result: string };
export type Market = { id: number; eventId: number; type: string; name: string; line: number | null; status: string; outcomes: Outcome[] };
export type Ev = {
  id: number;
  sport: string;
  league: string;
  home: string;
  away: string;
  startsAt: string;
  status: string;
  homeScore: number;
  awayScore: number;
  featured: boolean;
  markets: Market[];
};

export function getInitData(): string {
  if (typeof window === "undefined") return "";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const tg = (window as any).Telegram?.WebApp;
  if (tg?.initData) return tg.initData as string;
  const h = new URLSearchParams(window.location.hash.slice(1));
  return h.get("tgWebAppData") || "";
}
