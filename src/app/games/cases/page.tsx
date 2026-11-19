"use client";
import Link from "next/link";
import { useRef, useState } from "react";
import { useAuth, useToast } from "@/components/Providers";
import { api, fmt, useFetch } from "@/lib/client";
import { InvItem, rarClass } from "@/lib/rarity";

type CItem = { id: number; name: string; emoji: string; rarity: string; price: number; chance: number };
type Case = { id: number; name: string; price: number; emoji: string; color: string; rtp: number; items: CItem[] };
type Data = { cases: Case[]; inventory: InvItem[] };

const STEP = 104;
const WIN_IDX = 38;

export default function Cases() {
  const { data, reload } = useFetch<Data>("/api/games/cases");
  const { setBalance } = useAuth();
  const toast = useToast();
  const [sel, setSel] = useState<Case | null>(null);
  const [busy, setBusy] = useState(false);
  const [reel, setReel] = useState<CItem[] | null>(null);
  const [x, setX] = useState(0);
  const [won, setWon] = useState<{ invId: number; item: CItem } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  async function open(c: Case) {
    setBusy(true);
    setWon(null);
    try {
      const r = await api<{ balance: number; invId: number; item: CItem }>("/api/games/cases", { action: "open", caseId: c.id });
      setBalance(r.balance);
      const pick = () => {
        let roll = Math.random() * 100;
        return c.items.find((i) => (roll -= i.chance) < 0) ?? c.items[c.items.length - 1];
      };
      const strip = Array.from({ length: 48 }, pick);
      strip[WIN_IDX] = { ...r.item, chance: 0 };
      setX(0);
      setReel(strip);
      const w = box.current?.clientWidth ?? 320;
      const jitter = (Math.random() - 0.5) * 60;
      setTimeout(() => setX(-(WIN_IDX * STEP + 48 - w / 2 + jitter)), 60);
      setTimeout(() => {
        setWon({ invId: r.invId, item: { ...r.item, chance: 0 } });
        setBusy(false);
        void reload();
      }, 5300);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
      setBusy(false);
    }
  }

  async function sell(invId: number) {
    try {
      const r = await api<{ balance: number; price: number }>("/api/games/cases", { action: "sell", invId });
      setBalance(r.balance);
      toast(`Продано за ${fmt(r.price)} PTS`);
      if (won?.invId === invId) setWon(null);
      await reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <Link href="/games" className="text-muted">‹</Link>
        <h1 className="text-xl font-black">📦 Кейсы</h1>
      </div>

      {reel && (
        <div className="card relative overflow-hidden p-3">
          <div ref={box} className="relative overflow-hidden">
            <div className="absolute inset-y-0 left-1/2 z-10 w-0.5 -translate-x-1/2 bg-neon shadow-[0_0_12px_#5dff1f]" />
            <div className="flex gap-2" style={{ transform: `translateX(${x}px)`, transition: x ? "transform 5s cubic-bezier(.08,.7,.1,1)" : "none" }}>
              {reel.map((it, i) => (
                <div key={i} className={`item ${rarClass(it.rarity)} grid h-24 w-24 shrink-0 place-items-center text-center`}>
                  <div className="text-3xl">{it.emoji}</div>
                  <div className="px-1 text-[10px] font-bold leading-tight">{fmt(it.price)}</div>
                </div>
              ))}
            </div>
          </div>
          {won && (
            <div className={`pop item ${rarClass(won.item.rarity)} mt-3 p-4 text-center`}>
              <div className="text-5xl">{won.item.emoji}</div>
              <div className="mt-1 font-black">{won.item.name}</div>
              <div className="text-xs font-bold text-muted">{won.item.rarity} · {fmt(won.item.price)} PTS</div>
              <div className="mt-3 grid grid-cols-3 gap-2">
                <button className="btn btn-gold !px-2 !text-xs" onClick={() => sell(won.invId)}>Продать</button>
                <Link href={`/games/upgrade?inv=${won.invId}`} className="btn btn-ghost !px-2 !text-xs">Улучшить</Link>
                <button className="btn btn-ghost !px-2 !text-xs" onClick={() => { setWon(null); setReel(null); }}>Оставить</button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        {data?.cases.map((c) => (
          <button key={c.id} onClick={() => setSel(c)} className="card fade-up p-4 text-center active:scale-[0.98]" style={{ boxShadow: `inset 0 -40px 60px -40px ${c.color}33` }}>
            <div className="text-5xl drop-shadow-[0_0_16px_var(--c)]" style={{ ["--c" as string]: c.color }}>{c.emoji}</div>
            <div className="mt-2 font-black">{c.name}</div>
            <div className="mt-1 text-sm font-black text-neon">{fmt(c.price)} PTS</div>
          </button>
        ))}
      </div>

      <div className="card p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="font-black">Инвентарь · {data?.inventory.length ?? 0}</div>
          <Link href="/games/upgrade" className="text-xs font-bold text-neon">Апгрейд ›</Link>
        </div>
        {data?.inventory.length ? (
          <div className="grid grid-cols-3 gap-2">
            {data.inventory.map((it) => (
              <div key={it.id} className={`item ${rarClass(it.rarity)} p-2 text-center`}>
                <div className="text-3xl">{it.emoji}</div>
                <div className="truncate text-[10px] font-bold">{it.name}</div>
                <button className="mt-1.5 w-full rounded-lg bg-black/40 py-1 text-[11px] font-black text-gold" onClick={() => sell(it.id)}>
                  {fmt(it.price)} · продать
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center text-sm text-muted">Пока пусто — откройте первый кейс</div>
        )}
      </div>

      {sel && (
        <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/70 backdrop-blur-sm" onClick={() => setSel(null)}>
          <div className="sheet max-h-[88dvh] w-full max-w-xl overflow-y-auto rounded-t-3xl border border-line bg-panel p-4 pb-6" onClick={(e) => e.stopPropagation()}>
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-line" />
            <div className="flex items-center gap-3">
              <div className="text-5xl">{sel.emoji}</div>
              <div>
                <div className="text-lg font-black">Кейс «{sel.name}»</div>
                <div className="text-xs text-muted">Возврат игрокам (RTP): {sel.rtp}%</div>
              </div>
            </div>
            <div className="mt-3 space-y-1.5">
              {sel.items.map((i) => (
                <div key={i.id} className={`item ${rarClass(i.rarity)} flex items-center justify-between px-3 py-2`}>
                  <div className="flex items-center gap-2.5">
                    <span className="text-2xl">{i.emoji}</span>
                    <div>
                      <div className="text-[13px] font-bold">{i.name}</div>
                      <div className="text-[11px] text-muted">{i.rarity} · {fmt(i.price)} PTS</div>
                    </div>
                  </div>
                  <b className="text-sm">{i.chance}%</b>
                </div>
              ))}
            </div>
            <button className="btn btn-primary mt-4 w-full !py-4 text-base" disabled={busy} onClick={() => { const c = sel; setSel(null); void open(c); }}>
              Открыть за {fmt(sel.price)} PTS
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
