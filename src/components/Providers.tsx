"use client";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getInitData } from "@/lib/client";

export type Me = {
  id: number;
  name: string;
  username: string | null;
  balance: number;
  xp: number;
  streak: number;
  isAdmin: boolean;
  refCode: string;
  level: number;
  title: string;
  progress: number;
  xpToNext: number;
};
type Config = { telegram: boolean; guest: boolean; botName: string };

type AuthCtx = {
  user: Me | null;
  config: Config | null;
  loading: boolean;
  refresh: () => Promise<void>;
  setBalance: (n: number) => void;
  login: (name: string) => Promise<void>;
  logout: () => Promise<void>;
};
const Auth = createContext<AuthCtx>(null as unknown as AuthCtx);
export const useAuth = () => useContext(Auth);

export type Sel = {
  outcomeId: number;
  eventId: number;
  odds: number;
  oldOdds?: number;
  event: string;
  market: string;
  pick: string;
  closed?: boolean;
};
type SlipCtx = {
  sels: Sel[];
  toggle: (s: Sel) => void;
  remove: (outcomeId: number) => void;
  clear: () => void;
  has: (outcomeId: number) => boolean;
  update: (fn: (s: Sel[]) => Sel[]) => void;
  open: boolean;
  setOpen: (b: boolean) => void;
};
const Slip = createContext<SlipCtx>(null as unknown as SlipCtx);
export const useSlip = () => useContext(Slip);

type ToastCtx = (msg: string, kind?: "ok" | "err") => void;
const Toast = createContext<ToastCtx>(() => {});
export const useToast = () => useContext(Toast);

export default function Providers({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [loading, setLoading] = useState(true);
  const [sels, setSels] = useState<Sel[]>([]);
  const [open, setOpen] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; msg: string; kind: string }[]>([]);

  const toast = useCallback<ToastCtx>((msg, kind = "ok") => {
    const id = Math.random();
    setToasts((t) => [...t.slice(-2), { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  }, []);

  const refresh = useCallback(async () => {
    const r = await api("/api/auth");
    setUser(r.user);
    setConfig(r.config);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const tgInit = () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const tg = (window as any).Telegram?.WebApp;
      tg?.ready?.();
      tg?.expand?.();
      tg?.setHeaderColor?.("#070909");
      tg?.setBackgroundColor?.("#070909");
    };
    tgInit();
    const t = setTimeout(tgInit, 1200);
    (async () => {
      try {
        const r = await api("/api/auth");
        setConfig(r.config);
        if (r.user) setUser(r.user);
        else {
          const init = getInitData();
          if (init) {
            try {
              const ref = new URLSearchParams(window.location.search).get("ref");
              const l = await api("/api/auth", { initData: init, ref });
              setUser(l.user);
            } catch {
              /* показываем форму входа */
            }
          }
        }
      } finally {
        setLoading(false);
      }
    })();
    return () => clearTimeout(t);
  }, []);

  const authValue = useMemo<AuthCtx>(
    () => ({
      user,
      config,
      loading,
      refresh,
      setBalance: (n) => setUser((u) => (u ? { ...u, balance: n } : u)),
      login: async (name) => {
        const ref = new URLSearchParams(window.location.search).get("ref");
        const r = await api("/api/auth", { guestName: name, ref });
        setUser(r.user);
      },
      logout: async () => {
        await api("/api/auth", { action: "logout" });
        setUser(null);
        setSels([]);
      },
    }),
    [user, config, loading, refresh],
  );

  const slipValue = useMemo<SlipCtx>(
    () => ({
      sels,
      toggle: (s) =>
        setSels((cur) => {
          if (cur.some((x) => x.outcomeId === s.outcomeId)) return cur.filter((x) => x.outcomeId !== s.outcomeId);
          return [...cur.filter((x) => x.eventId !== s.eventId), s];
        }),
      remove: (id) => setSels((cur) => cur.filter((x) => x.outcomeId !== id)),
      clear: () => setSels([]),
      has: (id) => sels.some((x) => x.outcomeId === id),
      update: (fn) => setSels(fn),
      open,
      setOpen,
    }),
    [sels, open],
  );

  return (
    <Auth.Provider value={authValue}>
      <Toast.Provider value={toast}>
        <Slip.Provider value={slipValue}>
          {children}
          <div className="pointer-events-none fixed inset-x-0 top-3 z-[100] flex flex-col items-center gap-2 px-4">
            {toasts.map((t) => (
              <div
                key={t.id}
                className={`pop pointer-events-auto max-w-md rounded-2xl border px-4 py-2.5 text-sm font-semibold shadow-2xl backdrop-blur ${
                  t.kind === "err"
                    ? "border-danger/50 bg-[#2a1215]/95 text-[#ffb3bb]"
                    : "border-neon/40 bg-[#0f1f0a]/95 text-neon"
                }`}
              >
                {t.msg}
              </div>
            ))}
          </div>
        </Slip.Provider>
      </Toast.Provider>
    </Auth.Provider>
  );
}
