"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useState } from "react";
import { fmt } from "@/lib/client";
import { useAuth, useSlip, useToast } from "./Providers";
import SlipSheet from "./SlipSheet";

const NAV = [
  { href: "/", label: "Линия", icon: "M3 12l9-8 9 8v8a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" },
  { href: "/live", label: "Live", icon: "M12 3v2m0 14v2M5 12H3m18 0h-2M7.8 7.8L6.4 6.4m11.2 11.2l-1.4-1.4M7.8 16.2l-1.4 1.4m11.2-11.2l-1.4 1.4M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" },
  { href: "/bets", label: "Ставки", icon: "M7 3h10a2 2 0 0 1 2 2v16l-3-2-2 2-2-2-2 2-2-2-3 2V5a2 2 0 0 1 2-2zm1 5h8m-8 4h8" },
  { href: "/games", label: "Игры", icon: "M6 9h12a4 4 0 0 1 4 4v1a3 3 0 0 1-5.2 2L15 14H9l-1.8 2A3 3 0 0 1 2 14v-1a4 4 0 0 1 4-4zm3 2v2m-1-1h2m6-.5h.01m2 1h.01" },
  { href: "/profile", label: "Профиль", icon: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-8 9a8 8 0 0 1 16 0" },
];

function Login() {
  const { login, config } = useAuth();
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-5">
      <div className="fade-up text-center">
        <div className="text-5xl font-black italic tracking-tight">
          MSP<span className="text-neon [text-shadow:0_0_24px_rgba(93,255,31,.55)]">BETS</span>
        </div>
        <p className="mt-3 text-lg font-extrabold">Здесь начинается победа.</p>
        <p className="mt-1 text-sm text-muted">Ставки на Dota 2, CS2 и футбол. Краш, мины, кейсы и апгрейды — на виртуальные баллы.</p>
      </div>
      <div className="card fade-up mt-8 p-5">
        {config?.guest !== false ? (
          <>
            <div className="mb-2 text-sm font-bold">Войти как гость</div>
            <input value={name} maxLength={24} placeholder="Ваш ник" onChange={(e) => setName(e.target.value)} />
            <button
              className="btn btn-primary mt-3 w-full"
              disabled={busy || name.trim().length < 2}
              onClick={async () => {
                setBusy(true);
                try {
                  await login(name.trim());
                } catch (e) {
                  toast(e instanceof Error ? e.message : "Ошибка", "err");
                } finally {
                  setBusy(false);
                }
              }}
            >
              Начать с 1 000 PTS
            </button>
            {config?.telegram && <p className="mt-3 text-center text-xs text-muted">В Telegram вход происходит автоматически</p>}
          </>
        ) : (
          <p className="text-center text-sm">Откройте приложение через Telegram-бота, чтобы войти.</p>
        )}
      </div>
      <p className="mt-6 text-center text-[11px] leading-relaxed text-muted">
        Баллы виртуальные: их нельзя купить, вывести, обменять или получить за них денежный приз. 18+
      </p>
    </div>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const slip = useSlip();
  const path = usePathname();

  if (loading) {
    return (
      <div className="mx-auto max-w-xl space-y-3 p-4">
        <div className="skeleton h-14" />
        <div className="skeleton h-36" />
        <div className="skeleton h-36" />
      </div>
    );
  }
  if (!user) return <Login />;

  const total = slip.sels.reduce((a, s) => a * s.odds, 1);
  return (
    <div className="mx-auto min-h-dvh max-w-xl pb-28">
      <header className="sticky top-0 z-40 flex items-center justify-between border-b border-line bg-ink/80 px-4 py-3 backdrop-blur-xl">
        <Link href="/" className="text-xl font-black italic tracking-tight">
          MSP<span className="text-neon [text-shadow:0_0_14px_rgba(93,255,31,.5)]">BETS</span>
        </Link>
        <div className="flex items-center gap-2">
          <Link href="/rating" className="chip !px-3" aria-label="Рейтинг">🏅</Link>
          <Link href="/profile" className="flex items-center gap-2 rounded-xl border border-line bg-panel px-3 py-1.5">
            <span className="text-[10px] font-bold uppercase text-muted">Баланс</span>
            <b className="text-[15px] font-black">{fmt(user.balance)}</b>
            <span className="text-[11px] font-black text-neon">PTS</span>
          </Link>
        </div>
      </header>

      <main className="px-3 pt-3">{children}</main>

      {slip.sels.length > 0 && !slip.open && (
        <button
          onClick={() => slip.setOpen(true)}
          className="pop btn btn-primary fixed bottom-[78px] left-1/2 z-40 w-[calc(100%-1.5rem)] max-w-[34rem] -translate-x-1/2 !justify-between !py-3.5"
        >
          <span className="flex items-center gap-2">
            <span className="grid h-6 min-w-6 place-items-center rounded-full bg-black/80 px-1.5 text-xs text-neon">{slip.sels.length}</span>
            Купон
          </span>
          <span>общий кф {slip.sels.length > 1 ? total.toFixed(2) : slip.sels[0].odds.toFixed(2)}</span>
        </button>
      )}

      <nav className="fixed inset-x-0 bottom-0 z-50 border-t border-line bg-ink/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl">
        <div className="mx-auto flex max-w-xl">
          {NAV.map((n) => {
            const active = n.href === "/" ? path === "/" || path.startsWith("/event") : path.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={`relative flex flex-1 flex-col items-center gap-1 py-2.5 text-[11px] font-bold ${active ? "text-neon" : "text-muted"}`}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d={n.icon} />
                </svg>
                {n.label}
                {active && <span className="absolute top-0 h-0.5 w-8 rounded-full bg-neon shadow-[0_0_10px_#5dff1f]" />}
              </Link>
            );
          })}
        </div>
      </nav>
      <SlipSheet />
    </div>
  );
}
