"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import BetInput from "@/components/BetInput";
import { useAuth, useToast } from "@/components/Providers";
import { api, fmt } from "@/lib/client";

type Game = { id: number; bet: number; minesCount: number; revealed: number[]; status: string; mult: number; nextMult: number; payout: number; mines: number[] | null };

export default function Mines() {
  const { setBalance } = useAuth();
  const toast = useToast();
  const [game, setGame] = useState<Game | null>(null);
  const [bet, setBet] = useState(100);
  const [count, setCount] = useState(3);
  const [busy, setBusy] = useState(false);
  const [boom, setBoom] = useState<number | null>(null);

  useEffect(() => {
    api<{ game: Game | null }>("/api/games/mines").then((r) => setGame(r.game)).catch(() => {});
  }, []);

  async function call(body: object) {
    setBusy(true);
    try {
      const r = await api<{ game: Game; balance?: number; boom?: number }>("/api/games/mines", body);
      setGame(r.game);
      if (r.balance !== undefined) setBalance(r.balance);
      setBoom(r.boom ?? null);
      if (r.game.status === "won") toast(`Выигрыш +${fmt(r.game.payout)} PTS 💎`);
      if (r.game.status === "lost") toast("Мина! Ставка проиграна", "err");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
    } finally {
      setBusy(false);
    }
  }

  const active = game?.status === "active";
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 px-1">
        <Link href="/games" className="text-muted">‹</Link>
        <h1 className="text-xl font-black">💣 Мины</h1>
      </div>

      {game && (
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="card p-2.5"><div className="text-[11px] text-muted">Множитель</div><b className="text-lg text-neon">x{game.mult.toFixed(2)}</b></div>
          <div className="card p-2.5"><div className="text-[11px] text-muted">Следующий</div><b className="text-lg">x{game.nextMult.toFixed(2)}</b></div>
          <div className="card p-2.5"><div className="text-[11px] text-muted">Забрать</div><b className="text-lg">{fmt(game.status === "active" ? Math.floor(game.bet * game.mult) : game.payout)}</b></div>
        </div>
      )}

      <div className="card p-3">
        <div className="grid grid-cols-5 gap-2">
          {Array.from({ length: 25 }, (_, i) => {
            const opened = game?.revealed.includes(i);
            const isMine = game?.mines?.includes(i);
            const exploded = boom === i;
            return (
              <button
                key={i}
                disabled={!active || busy || !!opened}
                onClick={() => call({ action: "reveal", tile: i })}
                className={`grid aspect-square place-items-center rounded-xl border text-2xl transition ${
                  exploded
                    ? "shake border-danger bg-danger/25"
                    : opened
                      ? "pop border-neon/50 bg-neon/10"
                      : isMine
                        ? "border-line bg-panel opacity-70"
                        : "border-line bg-panel2 active:scale-95"
                }`}
              >
                {exploded ? "💥" : opened ? "💎" : isMine ? "💣" : ""}
              </button>
            );
          })}
        </div>
      </div>

      {active ? (
        <button className="btn btn-gold w-full !py-4 text-base" disabled={busy || !game.revealed.length} onClick={() => call({ action: "cashout" })}>
          Забрать {fmt(Math.floor(game.bet * game.mult))} PTS
        </button>
      ) : (
        <div className="card space-y-3 p-4">
          <BetInput value={bet} onChange={setBet} />
          <div>
            <div className="mb-1 text-xs font-bold text-muted">Количество мин</div>
            <div className="flex gap-2">
              {[1, 3, 5, 10, 15, 24].map((n) => (
                <button key={n} className={`chip flex-1 !px-0 text-center ${count === n ? "on" : ""}`} onClick={() => setCount(n)}>{n}</button>
              ))}
            </div>
          </div>
          <button className="btn btn-primary w-full !py-4 text-base" disabled={busy || bet < 10} onClick={() => { setBoom(null); void call({ action: "start", bet, mines: count }); }}>
            {game ? "Играть снова" : "Начать игру"} · {fmt(bet)} PTS
          </button>
          <p className="text-center text-[11px] text-muted">Множитель растёт с каждой безопасной клеткой. Маржа 3%.</p>
        </div>
      )}
    </div>
  );
}
