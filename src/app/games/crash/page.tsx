"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import BetInput from "@/components/BetInput";
import { useAuth, useToast } from "@/components/Providers";
import { api, fmt } from "@/lib/client";

type State = {
  serverNow: number;
  balance: number;
  round: { id: number; phase: "betting" | "flying" | "crashed"; seedHash: string; bettingEndsAt: number; endsAt: number; mult: number; crashPoint: number | null; seed: string | null };
  bets: { name: string; amount: number; status: string; cashoutMult: number | null; payout: number; me: boolean }[];
  mine: { amount: number; status: string; cashoutMult: number | null; payout: number } | null;
  history: { id: number; crashPoint: number; seed: string; seedHash: string }[];
};

const GROWTH = 0.1;
const chipColor = (m: number) => (m < 2 ? "text-danger" : m < 10 ? "text-neon" : "text-gold");

export default function Crash() {
  const { setBalance } = useAuth();
  const toast = useToast();
  const [data, setData] = useState<State | null>(null);
  const dataRef = useRef<State | null>(null);
  const offset = useRef(0);
  const cv = useRef<HTMLCanvasElement>(null);
  const [mult, setMult] = useState(1);
  const [phase, setPhase] = useState<"betting" | "flying" | "crashed">("betting");
  const [count, setCount] = useState(0);
  const [amount, setAmount] = useState(100);
  const [auto, setAuto] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let dead = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const t0 = Date.now();
        const d = await api<State>("/api/games/crash");
        if (dead) return;
        offset.current = d.serverNow - (t0 + Date.now()) / 2;
        dataRef.current = d;
        setData(d);
        setBalance(d.balance);
      } catch {
        /* ignore */
      }
      timer = setTimeout(tick, dataRef.current?.round.phase === "flying" ? 300 : 600);
    };
    void tick();
    return () => {
      dead = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let raf = 0;
    let lastM = 0;
    const frame = () => {
      const d = dataRef.current;
      const canvas = cv.current;
      if (d && canvas) {
        const now = Date.now() + offset.current;
        const r = d.round;
        let ph: "betting" | "flying" | "crashed" = "betting";
        let m = 1;
        let elapsed = 0;
        if (r.phase === "crashed") {
          ph = "crashed";
          m = r.crashPoint ?? 1;
          elapsed = Math.log(m) / GROWTH;
        } else if (now >= r.bettingEndsAt) {
          ph = "flying";
          elapsed = (now - r.bettingEndsAt) / 1000;
          m = Math.max(1, Math.floor(Math.exp(GROWTH * elapsed) * 100) / 100);
        }
        if (m !== lastM) {
          lastM = m;
          setMult(m);
        }
        setPhase((p) => (p === ph ? p : ph));
        setCount(Math.max(0, Math.ceil((r.bettingEndsAt - now) / 1000)));

        const dpr = window.devicePixelRatio || 1;
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        if (canvas.width !== w * dpr) {
          canvas.width = w * dpr;
          canvas.height = h * dpr;
        }
        const ctx = canvas.getContext("2d")!;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        ctx.strokeStyle = "rgba(255,255,255,.05)";
        ctx.lineWidth = 1;
        for (let i = 1; i < 5; i++) {
          ctx.beginPath();
          ctx.moveTo(0, (h / 5) * i);
          ctx.lineTo(w, (h / 5) * i);
          ctx.stroke();
        }
        if (ph !== "betting") {
          const T = Math.max(elapsed, 6);
          const ymax = Math.max(2, m * 1.15);
          const col = ph === "crashed" ? "#ff4d5e" : "#5dff1f";
          ctx.beginPath();
          const pad = 14;
          const px = (t: number) => pad + (t / T) * (w - pad * 2);
          const py = (v: number) => h - pad - ((v - 1) / (ymax - 1)) * (h - pad * 2);
          ctx.moveTo(px(0), py(1));
          for (let t = 0; t <= elapsed; t += elapsed / 80 || 1) ctx.lineTo(px(t), py(Math.exp(GROWTH * t)));
          ctx.lineTo(px(elapsed), py(Math.exp(GROWTH * elapsed)));
          ctx.strokeStyle = col;
          ctx.lineWidth = 3.5;
          ctx.shadowColor = col;
          ctx.shadowBlur = 14;
          ctx.stroke();
          ctx.shadowBlur = 0;
          ctx.lineTo(px(elapsed), h - pad);
          ctx.lineTo(px(0), h - pad);
          const g = ctx.createLinearGradient(0, 0, 0, h);
          g.addColorStop(0, ph === "crashed" ? "rgba(255,77,94,.25)" : "rgba(93,255,31,.25)");
          g.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = g;
          ctx.fill();
          ctx.beginPath();
          ctx.arc(px(elapsed), py(Math.exp(GROWTH * elapsed)), 6, 0, 7);
          ctx.fillStyle = col;
          ctx.fill();
        }
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  async function bet() {
    setBusy(true);
    try {
      const r = await api<{ balance: number }>("/api/games/crash", { action: "bet", amount, auto: auto ? Number(auto) : undefined });
      setBalance(r.balance);
      toast("Ставка принята");
      dataRef.current = null;
      const d = await api<State>("/api/games/crash");
      dataRef.current = d;
      setData(d);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
    } finally {
      setBusy(false);
    }
  }
  async function cashout() {
    setBusy(true);
    try {
      const r = await api<{ balance: number; mult: number; payout: number }>("/api/games/crash", { action: "cashout" });
      setBalance(r.balance);
      toast(`Забрано x${r.mult.toFixed(2)}: +${fmt(r.payout)} PTS 🚀`);
      const d = await api<State>("/api/games/crash");
      dataRef.current = d;
      setData(d);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
    } finally {
      setBusy(false);
    }
  }

  const mine = data?.mine;
  const active = mine?.status === "active";
  const lastSeed = data?.history[0];
  const bigColor = phase === "crashed" ? "text-danger" : phase === "flying" ? "text-neon" : "text-white";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Link href="/games" className="text-muted">‹</Link>
          <h1 className="text-xl font-black">🚀 Краш</h1>
        </div>
        <span className="text-xs font-bold text-muted">раунд #{data?.round.id ?? "…"}</span>
      </div>

      <div className="flex gap-1.5 overflow-x-auto pb-1">
        {data?.history.map((h) => (
          <span key={h.id} className={`chip !px-2.5 ${chipColor(h.crashPoint)}`}>{h.crashPoint.toFixed(2)}x</span>
        ))}
      </div>

      <div className="card relative overflow-hidden">
        <canvas ref={cv} className="block h-56 w-full" />
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          {phase === "betting" ? (
            <>
              <div className="text-sm font-bold text-muted">Приём ставок</div>
              <div className="text-6xl font-black tabular-nums">{count}</div>
            </>
          ) : (
            <>
              <div className={`text-6xl font-black tabular-nums ${bigColor} ${phase === "crashed" ? "shake" : ""}`}>{mult.toFixed(2)}x</div>
              {phase === "crashed" && <div className="mt-1 text-sm font-black text-danger">РАУНД ЗАВЕРШЁН</div>}
            </>
          )}
        </div>
      </div>

      <div className="card space-y-3 p-4">
        <BetInput value={amount} onChange={setAmount} />
        <div>
          <div className="mb-1 text-xs font-bold text-muted">Авто-вывод на множителе (необязательно)</div>
          <input type="number" step="0.1" min="1.01" placeholder="например 2.00" value={auto} onChange={(e) => setAuto(e.target.value)} />
        </div>
        {phase === "flying" && active ? (
          <button className="btn btn-gold w-full !py-4 text-base" disabled={busy} onClick={cashout}>
            Забрать {fmt(Math.floor((mine?.amount ?? 0) * mult))} PTS
          </button>
        ) : phase === "betting" && !mine ? (
          <button className="btn btn-primary w-full !py-4 text-base" disabled={busy || amount < 10} onClick={bet}>
            Поставить {fmt(amount)} PTS
          </button>
        ) : (
          <button className="btn btn-ghost w-full !py-4" disabled>
            {mine
              ? mine.status === "won"
                ? `✅ Выигрыш +${fmt(mine.payout)} (x${mine.cashoutMult?.toFixed(2)})`
                : mine.status === "lost"
                  ? "💥 Ставка проиграна"
                  : phase === "betting"
                    ? `Ставка ${fmt(mine.amount)} принята`
                    : "Ждём результат…"
              : "Ожидайте следующий раунд"}
          </button>
        )}
      </div>

      <div className="card p-4">
        <div className="mb-2 text-sm font-black">Ставки раунда · {data?.bets.length ?? 0}</div>
        <div className="space-y-1.5">
          {data?.bets.map((b, i) => (
            <div key={i} className={`flex items-center justify-between text-sm ${b.me ? "font-black text-neon" : ""}`}>
              <span className="truncate">{b.name}</span>
              <span>
                {fmt(b.amount)}
                {b.status === "won" && <span className="up ml-2">x{b.cashoutMult?.toFixed(2)} · +{fmt(b.payout)}</span>}
                {b.status === "lost" && <span className="down ml-2">✖</span>}
              </span>
            </div>
          ))}
          {!data?.bets.length && <div className="text-sm text-muted">Пока ставок нет — будьте первым</div>}
        </div>
      </div>

      <details className="card p-4 text-xs text-muted">
        <summary className="cursor-pointer text-sm font-black text-white">🔒 Provably fair</summary>
        <p className="mt-2 leading-relaxed">
          Результат раунда фиксируется до старта. Хеш текущего раунда: <code className="break-all text-neon">{data?.round.seedHash}</code>
        </p>
        {lastSeed && (
          <p className="mt-2 break-all leading-relaxed">
            Раунд #{lastSeed.id}: сид <code className="text-white">{lastSeed.seed}</code>, sha256(сид) = <code>{lastSeed.seedHash}</code>, точка
            краша <b className="text-white">{lastSeed.crashPoint.toFixed(2)}x</b>.
          </p>
        )}
        <p className="mt-2 leading-relaxed">Формула: h = первые 52 бита sha256(сид); если h mod 25 = 0 → 1.00x, иначе floor((100·2⁵² − h)/(2⁵² − h))/100. Маржа игры ≈ 4%.</p>
      </details>
    </div>
  );
}
