import Link from "next/link";

const GAMES = [
  { href: "/games/crash", t: "Краш", d: "Общий раунд, живой множитель. Забери до взрыва!", e: "🚀", g: "from-[#2a0f14] to-[#0e1213]", rtp: "RTP 96%" },
  { href: "/games/mines", t: "Мины", d: "Открывай клетки и увеличивай множитель.", e: "💣", g: "from-[#14240a] to-[#0e1213]", rtp: "RTP 97%" },
  { href: "/games/dice", t: "Дайс", d: "Выбери шанс — получи множитель.", e: "🎲", g: "from-[#0f1a2a] to-[#0e1213]", rtp: "RTP 97%" },
  { href: "/games/cases", t: "Кейсы", d: "Открывай кейсы, шансы всех предметов открыты.", e: "📦", g: "from-[#241a0a] to-[#0e1213]", rtp: "RTP ~94%" },
  { href: "/games/upgrade", t: "Апгрейд", d: "Улучши предмет. Шанс на экране — реальный.", e: "⬆️", g: "from-[#1d0f2a] to-[#0e1213]", rtp: "честный шанс" },
];

export default function Games() {
  return (
    <div className="space-y-3">
      <h1 className="px-1 text-xl font-black">Игры</h1>
      <div className="grid grid-cols-2 gap-3">
        {GAMES.map((g) => (
          <Link key={g.href} href={g.href} className={`card fade-up relative overflow-hidden bg-gradient-to-br ${g.g} p-4 active:scale-[0.98]`}>
            <div className="text-4xl">{g.e}</div>
            <div className="mt-3 text-lg font-black">{g.t}</div>
            <div className="mt-1 text-xs leading-snug text-muted">{g.d}</div>
            <div className="mt-3 inline-block rounded-full bg-neon/10 px-2 py-0.5 text-[10px] font-black text-neon">{g.rtp}</div>
          </Link>
        ))}
      </div>
      <div className="card p-4 text-xs leading-relaxed text-muted">
        🔒 <b className="text-white">Честная математика.</b> Любое движение баллов записывается в журнал. В краше результат раунда
        фиксируется заранее: показывается хеш, а после раунда — исходный сид для проверки. Шансы кейсов и апгрейдов
        показываются как есть — без скрытых подкруток.
      </div>
    </div>
  );
}
