"use client";
import Link from "next/link";
import { useState } from "react";
import BetInput from "@/components/BetInput";
import { useAuth, useToast } from "@/components/Providers";
import { api, fmt } from "@/lib/client";

type Roll = { roll: number; win: boolean; mult: number; payout: number };

export default function Dice() {
  const { setBalance } = useAuth();
  const toast = useToast();
  const [bet, setBet] = useState(100);
  const [chance, setChance] = useState(50);
  const [dir, setDir] = useState<"under" | "over">("under");
  const [last, setLast] = useState<Roll | null>(null);
  const [hist, setHist] = useState<Roll[]>([]);
  const [busy, setBusy] = useState(false);
  const mult = Math.floor((97 / chance) * 100) / 100;

  async function roll() {
    setBusy(true);
    try {
      const r = await api<Roll & { balance: number }>("/api/games/dice", { bet, chance, dir });
      setBalance(r.balance);
      setLast(r);
      setHist((h) => [r, ...h].slice(0, 12));
      if (r.win) toast(`Выигрыш +${fmt(r.payout)} PTS 🎲`);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
    } finally {
      setBusy(false);
    }
  }

  const zoneStyle = dir === "under" ? { left: 0, width: `${chance}%` } : { right: 0, width: `${chance}%` };
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <Link href="/games" className="text-muted">‹</Link>
        <h1 className="text-xl font-black">🎲 Дайс</h1>
      </div>

      <div className="card p-5">
        <div className={`text-center text-6xl font-black tabular-nums ${last ? (last.win ? "text-neon pop" : "text-danger shake") : "text-muted"}`} key={hist.length}>
          {last ? last.roll.toFixed(2) : "00.00"}
        </div>
        <div className="relative mt-6 h-4 rounded-full bg-danger/30">
          <div className="absolute inset-y-0 rounded-full bg-neon/70" style={zoneStyle} />
          {last && (
            <div className="absolute -top-2 h-8 w-1.5 -translate-x-1/2 rounded bg-white shadow-[0_0_12px_#fff] transition-all duration-500" style={{ left: `${last.roll}%` }} />
          )}
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-muted"><span>0</span><span>50</span><span>100</span></div>
      </div>

      <div className="card space-y-4 p-4">
        <div className="grid grid-cols-2 gap-1 rounded-2xl bg-panel2 p-1">
          {(["under", "over"] as const).map((d) => (
            <button key={d} onClick={() => setDir(d)} className={`rounded-xl py-2 text-sm font-extrabold ${dir === d ? "bg-neon text-[#031200]" : "text-muted"}`}>
              {d === "under" ? `Меньше ${chance}` : `Больше ${(100 - chance).toFixed(2)}`}
            </button>
          ))}
        </div>
        <div>
          <div className="mb-1 flex justify-between text-xs font-bold text-muted"><span>Шанс выигрыша</span><b className="text-white">{chance}%</b></div>
          <input type="range" min={1} max={95} step={1} value={chance} onChange={(e) => setChance(Number(e.target.value))} />
        </div>
        <div className="grid grid-cols-2 gap-2 text-center">
          <div className="rounded-xl bg-panel2 p-2.5"><div className="text-[11px] text-muted">Множитель</div><b className="text-lg text-neon">x{mult.toFixed(2)}</b></div>
          <div className="rounded-xl bg-panel2 p-2.5"><div className="text-[11px] text-muted">Выигрыш</div><b className="text-lg">{fmt(Math.floor(bet * mult))}</b></div>
        </div>
        <BetInput value={bet} onChange={setBet} />
        <button className="btn btn-primary w-full !py-4 text-base" disabled={busy || bet < 10} onClick={roll}>Бросить кубик</button>
      </div>

      {hist.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {hist.map((h, i) => (
            <span key={i} className={`chip !px-2.5 ${h.win ? "text-neon" : "text-danger"}`}>{h.roll.toFixed(2)}</span>
          ))}
        </div>
      )}
    </div>
  );
}
