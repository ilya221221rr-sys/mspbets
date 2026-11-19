"use client";
import { useAuth } from "./Providers";

export default function BetInput({ value, onChange, label = "Ставка" }: { value: number; onChange: (n: number) => void; label?: string }) {
  const { user } = useAuth();
  const bal = user?.balance ?? 0;
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs font-bold text-muted">
        <span>{label}</span>
        <span>Баланс: {bal.toLocaleString("ru-RU")}</span>
      </div>
      <input type="number" inputMode="numeric" min={10} value={value || ""} onChange={(e) => onChange(Math.max(0, Math.floor(Number(e.target.value))))} />
      <div className="mt-2 flex gap-2">
        <button className="chip flex-1 !px-0 text-center" onClick={() => onChange(10)}>Мин</button>
        <button className="chip flex-1 !px-0 text-center" onClick={() => onChange(Math.max(10, Math.floor(value / 2)))}>½</button>
        <button className="chip flex-1 !px-0 text-center" onClick={() => onChange(Math.min(100000, Math.max(10, value * 2)))}>×2</button>
        <button className="chip flex-1 !px-0 text-center" onClick={() => onChange(Math.min(100000, bal))}>Макс</button>
      </div>
    </div>
  );
}
