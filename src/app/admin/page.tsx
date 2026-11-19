"use client";
import { useState } from "react";
import { StatusBadge } from "@/components/EventCard";
import { useAuth, useToast } from "@/components/Providers";
import { Ev, SPORT_META, api, fmt, startLabel, useFetch } from "@/lib/client";

const TABS = [
  ["stats", "Дашборд"],
  ["line", "Линия"],
  ["users", "Игроки"],
  ["promo", "Промо"],
  ["logs", "Лог"],
] as const;

function useRun(reload?: () => void) {
  const toast = useToast();
  return async (body: object, ok = "Готово") => {
    try {
      const r = await api<Record<string, unknown>>("/api/admin", body);
      toast(ok);
      reload?.();
      return r;
    } catch (e) {
      toast(e instanceof Error ? e.message : "Ошибка", "err");
      return null;
    }
  };
}

export default function Admin() {
  const { user, refresh } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("stats");
  const [key, setKey] = useState("");

  if (!user?.isAdmin) {
    return (
      <div className="card mt-6 space-y-3 p-5">
        <div className="text-lg font-black">🛠 Админ-панель</div>
        <p className="text-sm text-muted">Доступ только для администраторов. Если у вас есть ключ администратора, введите его.</p>
        <input type="password" placeholder="ADMIN_KEY" value={key} onChange={(e) => setKey(e.target.value)} />
        <button
          className="btn btn-primary w-full"
          onClick={async () => {
            try {
              await api("/api/admin", { action: "unlock", key });
              await refresh();
              toast("Доступ открыт");
            } catch (e) {
              toast(e instanceof Error ? e.message : "Ошибка", "err");
            }
          }}
        >
          Войти
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h1 className="px-1 text-xl font-black">🛠 Админ-панель</h1>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map(([k, l]) => (
          <button key={k} className={`chip ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === "stats" && <Stats />}
      {tab === "line" && <Line />}
      {tab === "users" && <Users />}
      {tab === "promo" && <Promo />}
      {tab === "logs" && <Logs />}
    </div>
  );
}

/* ---------------- Дашборд ---------------- */
type StatsData = {
  users: { total: number; today: number; balance: number };
  bets: { c: number; pending: number; pendingStake: number; turnover: number; paid: number };
  events: { c: number; live: number };
  byType: { type: string; n: number; sum: number }[];
};
function Stats() {
  const { data, reload } = useFetch<StatsData>("/api/admin?view=stats");
  const run = useRun(reload);
  const [text, setText] = useState("");
  if (!data) return <div className="skeleton h-40" />;
  const sumOf = (p: string) => data.byType.filter((t) => t.type.startsWith(p)).reduce((a, t) => a + t.sum, 0);
  const sports = data.bets.turnover - data.bets.paid - data.bets.pendingStake;
  const games = -(sumOf("crash_") + sumOf("mines_") + sumOf("dice_") + sumOf("case_"));
  const cards: [string, string, string?][] = [
    ["Игроков", fmt(data.users.total), `+${data.users.today} за сутки`],
    ["Баллов в обороте", fmt(data.users.balance)],
    ["Ставок", fmt(data.bets.c), `в игре: ${data.bets.pending}`],
    ["Оборот ставок", fmt(data.bets.turnover)],
    ["Маржа по ставкам", fmt(sports), "оборот − выплаты"],
    ["Результат игр", fmt(games), "краш/мины/дайс/кейсы"],
    ["Матчей в линии", fmt(data.events.c), `live: ${data.events.live}`],
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        {cards.map(([l, v, s]) => (
          <div key={l} className="card p-3">
            <div className="text-[11px] text-muted">{l}</div>
            <div className="text-xl font-black">{v}</div>
            {s && <div className="text-[11px] text-muted">{s}</div>}
          </div>
        ))}
      </div>
      <div className="card p-4">
        <div className="mb-2 font-black">Движение баллов по типам</div>
        <div className="space-y-1 text-sm">
          {data.byType.map((t) => (
            <div key={t.type} className="flex justify-between">
              <span className="text-muted">{t.type} <span className="text-[11px]">×{t.n}</span></span>
              <b className={t.sum >= 0 ? "text-neon" : "text-danger"}>{fmt(t.sum)}</b>
            </div>
          ))}
        </div>
      </div>
      <div className="card space-y-2 p-4">
        <div className="font-black">📣 Рассылка в Telegram</div>
        <textarea rows={3} placeholder="Текст (поддерживается HTML)" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn btn-primary w-full" disabled={text.length < 3} onClick={async () => { const r = await run({ action: "broadcast", text }, "Рассылка запущена"); if (r) setText(""); }}>Отправить всем</button>
        <button className="btn btn-ghost w-full" onClick={() => run({ action: "setWebhook" }, "Webhook обновлён")}>Установить webhook бота</button>
      </div>
    </div>
  );
}

/* ---------------- Линия ---------------- */
function Line() {
  const { data, reload } = useFetch<{ events: Ev[] }>("/api/events?status=all", 6000);
  const run = useRun(reload);
  const [f, setF] = useState({ sport: "dota2", league: "", home: "", away: "", p: 50, featured: false, startsAt: "" });
  const dt = () => {
    const d = new Date(Date.now() + 3600_000);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 16);
  };
  return (
    <div className="space-y-3">
      <div className="card space-y-2 p-4">
        <div className="font-black">Новый матч</div>
        <div className="grid grid-cols-2 gap-2">
          <select value={f.sport} onChange={(e) => setF({ ...f, sport: e.target.value })}>
            {Object.entries(SPORT_META).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
          </select>
          <input placeholder="Турнир" value={f.league} onChange={(e) => setF({ ...f, league: e.target.value })} />
          <input placeholder="Команда 1" value={f.home} onChange={(e) => setF({ ...f, home: e.target.value })} />
          <input placeholder="Команда 2" value={f.away} onChange={(e) => setF({ ...f, away: e.target.value })} />
        </div>
        <input type="datetime-local" value={f.startsAt || dt()} onChange={(e) => setF({ ...f, startsAt: e.target.value })} />
        <div>
          <div className="mb-1 flex justify-between text-xs text-muted"><span>Шанс победы команды 1 (для расчёта кф с маржой 6%)</span><b className="text-white">{f.p}%</b></div>
          <input type="range" min={10} max={90} value={f.p} onChange={(e) => setF({ ...f, p: Number(e.target.value) })} />
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="!w-auto" checked={f.featured} onChange={(e) => setF({ ...f, featured: e.target.checked })} /> Топ-событие</label>
        <div className="flex gap-2">
          <button className="btn btn-primary flex-1" onClick={async () => { const r = await run({ action: "createEvent", ...f, startsAt: new Date(f.startsAt || dt()).toISOString() }, "Матч создан"); if (r) setF({ ...f, home: "", away: "" }); }}>Создать</button>
          <button className="btn btn-ghost flex-1" onClick={() => run({ action: "generateLine", count: 9 }, "Линия сгенерирована")}>+9 матчей авто</button>
        </div>
      </div>
      {data?.events.map((ev) => <AdminEvent key={ev.id} ev={ev} run={run} />)}
    </div>
  );
}

function AdminEvent({ ev, run }: { ev: Ev; run: (b: object, ok?: string) => Promise<unknown> }) {
  const [open, setOpen] = useState(false);
  const done = ev.status === "finished" || ev.status === "cancelled";
  return (
    <div className="card overflow-hidden">
      <button className="flex w-full items-center justify-between p-3 text-left" onClick={() => setOpen(!open)}>
        <div className="min-w-0">
          <div className="truncate text-sm font-extrabold">{SPORT_META[ev.sport]?.emoji} {ev.home} — {ev.away}</div>
          <div className="text-[11px] text-muted">#{ev.id} · {ev.league} · {startLabel(ev.startsAt)} · {ev.homeScore}:{ev.awayScore}</div>
        </div>
        <StatusBadge ev={ev} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-line p-3">
          {!done && (
            <div className="flex flex-wrap gap-2">
              {ev.status === "upcoming" && <button className="btn btn-ghost !px-3 !py-2 !text-xs" onClick={() => run({ action: "startEvent", eventId: ev.id }, "Матч начался")}>▶ Старт</button>}
              {ev.status === "live" && (
                <>
                  <button className="btn btn-ghost !px-3 !py-2 !text-xs" onClick={() => run({ action: "bump", eventId: ev.id, side: "home" }, "Счёт обновлён")}>+1 {ev.home.slice(0, 10)}</button>
                  <button className="btn btn-ghost !px-3 !py-2 !text-xs" onClick={() => run({ action: "bump", eventId: ev.id, side: "away" }, "Счёт обновлён")}>+1 {ev.away.slice(0, 10)}</button>
                </>
              )}
              <button className="btn btn-primary !px-3 !py-2 !text-xs" onClick={() => confirm("Завершить матч и рассчитать ставки?") && run({ action: "finish", eventId: ev.id }, "Матч рассчитан")}>🏁 Завершить</button>
              <button className="btn btn-danger !px-3 !py-2 !text-xs" onClick={() => confirm("Отменить матч и вернуть ставки?") && run({ action: "cancel", eventId: ev.id }, "Матч отменён, ставки возвращены")}>Отмена</button>
            </div>
          )}
          {ev.markets.map((m) => {
            const closed = m.status === "settled" || m.status === "void";
            return (
              <div key={m.id} className="rounded-xl bg-panel2 p-2.5">
                <div className="mb-2 flex items-center justify-between">
                  <div className="text-[13px] font-bold">{m.name} <span className="text-[11px] text-muted">· {m.status}</span></div>
                  {!closed && (
                    <button className="text-[11px] font-bold text-gold" onClick={() => run({ action: "marketStatus", marketId: m.id, status: m.status === "open" ? "suspended" : "open" }, m.status === "open" ? "Рынок приостановлен" : "Рынок открыт")}>
                      {m.status === "open" ? "⏸ Остановить" : "▶ Открыть"}
                    </button>
                  )}
                </div>
                <div className="flex gap-1.5">
                  {m.outcomes.map((o) => (
                    <div key={o.id} className="min-w-0 flex-1">
                      <div className="truncate text-[10px] text-muted">{o.label} {o.result !== "pending" && `· ${o.result}`}</div>
                      <input
                        type="number"
                        step="0.01"
                        disabled={closed}
                        defaultValue={o.odds}
                        key={`${o.id}-${o.odds}`}
                        className="!px-2 !py-1.5 !text-sm"
                        onBlur={(e) => Number(e.target.value) !== o.odds && run({ action: "setOdds", outcomeId: o.id, odds: Number(e.target.value) }, "Коэффициент обновлён")}
                      />
                    </div>
                  ))}
                </div>
                {!closed && (
                  <select className="mt-2 !py-1.5 !text-xs" value="" onChange={(e) => e.target.value && confirm("Рассчитать рынок?") && run({ action: "settleMarket", marketId: m.id, key: e.target.value }, "Рынок рассчитан")}>
                    <option value="">Рассчитать вручную…</option>
                    {m.outcomes.map((o) => <option key={o.id} value={o.key}>Победил: {o.label}</option>)}
                    <option value="void">Возврат (void)</option>
                  </select>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------- Игроки ---------------- */
type U = { id: number; name: string; username: string | null; tgId: number | null; balance: number; banned: boolean; isAdmin: boolean; createdAt: string };
function Users() {
  const [q, setQ] = useState("");
  const { data, reload } = useFetch<{ users: U[] }>(`/api/admin?view=users&q=${encodeURIComponent(q)}`);
  const run = useRun(reload);
  const [sel, setSel] = useState<number | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  return (
    <div className="space-y-3">
      <input placeholder="Поиск по имени, @username или ID" value={q} onChange={(e) => setQ(e.target.value)} />
      {data?.users.map((u) => (
        <div key={u.id} className="card p-3">
          <button className="flex w-full items-center justify-between text-left" onClick={() => setSel(sel === u.id ? null : u.id)}>
            <div className="min-w-0">
              <div className="truncate font-extrabold">{u.name} {u.isAdmin && "🛠"} {u.banned && <span className="text-danger">· бан</span>}</div>
              <div className="text-[11px] text-muted">#{u.id}{u.username ? ` · @${u.username}` : ""}{u.tgId ? ` · tg ${u.tgId}` : " · гость"}</div>
            </div>
            <b>{fmt(u.balance)}</b>
          </button>
          {sel === u.id && (
            <div className="mt-3 space-y-2 border-t border-line pt-3">
              <div className="grid grid-cols-2 gap-2">
                <input type="number" placeholder="± сумма" value={amount} onChange={(e) => setAmount(e.target.value)} />
                <input placeholder="Причина" value={reason} onChange={(e) => setReason(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <button className="btn btn-primary flex-1 !py-2" disabled={!amount || reason.length < 3} onClick={async () => { const r = await run({ action: "adjust", userId: u.id, amount: Number(amount), reason }, "Баланс изменён"); if (r) { setAmount(""); setReason(""); } }}>Применить</button>
                <button className={`btn flex-1 !py-2 ${u.banned ? "btn-ghost" : "btn-danger"}`} onClick={() => run({ action: "ban", userId: u.id, banned: !u.banned }, u.banned ? "Разблокирован" : "Заблокирован")}>{u.banned ? "Разбанить" : "Забанить"}</button>
              </div>
              <p className="text-[11px] text-muted">Отрицательное число — списание. Каждая операция записывается в журнал игрока и в лог админов.</p>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/* ---------------- Промо ---------------- */
function Promo() {
  const { data, reload } = useFetch<{ promos: { code: string; reward: number; maxUses: number; uses: number }[] }>("/api/admin?view=promos");
  const run = useRun(reload);
  const [f, setF] = useState({ code: "", reward: "500", maxUses: "100" });
  return (
    <div className="space-y-3">
      <div className="card space-y-2 p-4">
        <div className="font-black">Новый промокод</div>
        <input placeholder="КОД" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} />
        <div className="grid grid-cols-2 gap-2">
          <input type="number" placeholder="Награда" value={f.reward} onChange={(e) => setF({ ...f, reward: e.target.value })} />
          <input type="number" placeholder="Лимит" value={f.maxUses} onChange={(e) => setF({ ...f, maxUses: e.target.value })} />
        </div>
        <button className="btn btn-primary w-full" onClick={() => run({ action: "createPromo", code: f.code, reward: Number(f.reward), maxUses: Number(f.maxUses) }, "Промокод сохранён")}>Сохранить</button>
      </div>
      {data?.promos.map((p) => (
        <div key={p.code} className="card flex items-center justify-between p-3">
          <b>{p.code}</b>
          <span className="text-sm text-muted">+{fmt(p.reward)} · {p.uses}/{p.maxUses}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------- Лог ---------------- */
function Logs() {
  const { data } = useFetch<{ logs: { id: number; action: string; details: string; createdAt: string; admin: string }[] }>("/api/admin?view=logs");
  return (
    <div className="space-y-2">
      {data?.logs.map((l) => (
        <div key={l.id} className="card p-3 text-sm">
          <div className="flex justify-between"><b>{l.action}</b><span className="text-[11px] text-muted">{startLabel(l.createdAt)}</span></div>
          <div className="text-xs text-muted">{l.admin}: {l.details}</div>
        </div>
      ))}
      {data && !data.logs.length && <div className="card p-6 text-center text-sm text-muted">Действий пока нет</div>}
    </div>
  );
}
