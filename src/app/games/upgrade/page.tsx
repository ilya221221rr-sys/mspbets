"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useToast } from "@/components/Providers";
import { api, fmt, useFetch } from "@/lib/client";
import { InvItem, Item, rarClass } from "@/lib/rarity";

type Data = { inventory: InvItem[]; targets: Item[] };
const chanceOf = (mine: number, target: number) => Math.round(Math.min(90, Math.max(1, (mine / target) * 95)) * 100) / 100;

export default function Upgrade() {
  const { data, reload } = useFetch<Data>("/api/games/cases");
  const toast = useToast();
  const [invId, setInvId] = useState<number | null>(null);
  const [targetId, setTargetId] = useState<number | null>(null);
  const [angle, setAngle] = useState(0);
  const [anim, setAnim] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ success: boolean; name: string } | null>(null);

  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get("inv");
    if (p) setInvId(Number(p));
  }, []);

  const mine = data?.inventory.find((i) => i.id === invId) ?? null;
  const targets = mine ? (data?.targets ?? []).filter((t) => t.price > mine.price && t.price <= mine.price * 60) : [];
  const target = targets.find((t) => t.id === targetId) ?? null;
  const chance = mine && target ? chanceOf(mine.price, target.price) : 0;

  async function go() {
    if (!mine || !target) return;
    setBusy(true);
    setRes(null);
    try {
      const r = await api<{ success: boolean; roll: number }>("/api/games/cases", { action: "upgrade", invId: mine.id, targetId: target.id });
      setAnim(false);
      setAngle(0);
      setTimeout(() => {
        setAnim(true);
        setAngle(360 * 5 + r.roll * 3.6);
      }, 50);
      setTimeout(async () => {
        setRes({ success: r.success, name: target.name });
        setInvId(null);
        setTargetId(null);
        await reload();
        setBusy(false);
      }, 4200);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <Link href="/games" className="text-muted">‹</Link>
        <h1 className="text-xl font-black">⬆️ Апгрейд</h1>
      </div>

      <div className="card p-5">
        <div className="relative mx-auto h-56 w-56">
          <div className="absolute inset-0 rounded-full" style={{ background: `conic-gradient(#5dff1f 0 ${chance}%, #1b2326 ${chance}% 100%)` }} />
          <div className="absolute inset-3 rounded-full bg-panel" />
          <div className="absolute inset-0" style={{ transform: `rotate(${angle}deg)`, transition: anim ? "transform 4s cubic-bezier(.12,.7,.1,1)" : "none" }}>
            <div className="absolute left-1/2 top-0 h-1/2 w-1 origin-bottom -translate-x-1/2 rounded bg-white shadow-[0_0_10px_#fff]" />
          </div>
          <div className="absolute inset-0 grid place-items-center text-center">
            <div>
              <div className="text-4xl font-black text-neon">{chance ? `${chance}%` : "—"}</div>
              <div className="text-[11px] font-bold text-muted">шанс успеха</div>
            </div>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-center">
          <div className={`item ${mine ? rarClass(mine.rarity) : ""} p-2`}>
            <div className="text-3xl">{mine?.emoji ?? "❔"}</div>
            <div className="truncate text-[11px] font-bold">{mine?.name ?? "Ваш предмет"}</div>
            {mine && <div className="text-[11px] text-muted">{fmt(mine.price)}</div>}
          </div>
          <div className="text-xl text-neon">➜</div>
          <div className={`item ${target ? rarClass(target.rarity) : ""} p-2`}>
            <div className="text-3xl">{target?.emoji ?? "❔"}</div>
            <div className="truncate text-[11px] font-bold">{target?.name ?? "Цель"}</div>
            {target && <div className="text-[11px] text-muted">{fmt(target.price)}</div>}
          </div>
        </div>
        <button className="btn btn-primary mt-4 w-full !py-4 text-base" disabled={!mine || !target || busy} onClick={go}>
          {busy ? "Крутим…" : "Улучшить"}
        </button>
        <p className="mt-2 text-center text-[11px] text-muted">Шанс = цена предмета ÷ цена цели × 95%. Показанный шанс — реальный. При неудаче предмет сгорает.</p>
        {res && (
          <div className={`pop mt-3 rounded-2xl p-3 text-center font-black ${res.success ? "bg-neon/15 text-neon" : "bg-danger/15 text-danger"}`}>
            {res.success ? `Успех! Вы получили «${res.name}»` : "Неудача — предмет сгорел"}
          </div>
        )}
      </div>

      <div className="card p-4">
        <div className="mb-2 font-black">1. Выберите предмет</div>
        {data?.inventory.length ? (
          <div className="grid grid-cols-4 gap-2">
            {data.inventory.map((it) => (
              <button key={it.id} onClick={() => { setInvId(it.id); setTargetId(null); setRes(null); }} className={`item ${rarClass(it.rarity)} p-1.5 text-center ${invId === it.id ? "!border-neon ring-2 ring-neon/50" : ""}`}>
                <div className="text-2xl">{it.emoji}</div>
                <div className="text-[10px] font-bold">{fmt(it.price)}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="text-sm text-muted">Инвентарь пуст. <Link href="/games/cases" className="font-bold text-neon">Открыть кейс</Link></div>
        )}
      </div>

      {mine && (
        <div className="card p-4">
          <div className="mb-2 font-black">2. Выберите цель</div>
          <div className="space-y-1.5">
            {targets.map((t) => (
              <button key={t.id} onClick={() => setTargetId(t.id)} className={`item ${rarClass(t.rarity)} flex w-full items-center justify-between px-3 py-2 ${targetId === t.id ? "!border-neon ring-2 ring-neon/50" : ""}`}>
                <span className="flex items-center gap-2 text-left"><span className="text-xl">{t.emoji}</span><span className="text-[13px] font-bold">{t.name}</span></span>
                <span className="text-right text-xs"><b>{fmt(t.price)}</b> <span className="text-neon">{chanceOf(mine.price, t.price)}%</span></span>
              </button>
            ))}
            {!targets.length && <div className="text-sm text-muted">Для этого предмета нет доступных целей</div>}
          </div>
        </div>
      )}
    </div>
  );
}
