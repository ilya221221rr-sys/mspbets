"""
tools/gen_art.py — генерирует SVG-арты скинов и кейсов в web/skins и web/cases.
Запуск (из корня репозитория):  python tools/gen_art.py
Арты уже сгенерированы и лежат в репозитории; скрипт нужен, если добавите новые предметы.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from extras.catalog import build  # noqa: E402

RARITY = {  # цвет, свечение
    "Common": ("#9FB0C8", "#5B6B85"),
    "Rare": ("#4EA8FF", "#1D5FD1"),
    "Epic": ("#B26BFF", "#6A2BD1"),
    "Legendary": ("#FFB627", "#D9650B"),
    "Immortal": ("#FF5577", "#C4153F"),
}

# Иконки (viewBox 0 0 64 64), только линии/заливки без шрифтов
ICONS = {
    "ball": '<circle cx="32" cy="32" r="21"/><path d="M32 21l9.5 6.9-3.6 11.2H26.1l-3.6-11.2z"/><path d="M32 21V11M41.5 27.9l10-3.2M37.9 39.1l6.2 8.5M26.1 39.1l-6.2 8.5M22.5 27.9l-10-3.2"/>',
    "boot": '<path d="M13 14h15v18l19 6c4.5 1.4 6.5 4 6.5 8v3H11z"/><path d="M19 52v6M29 52v6M39 52v6M49 52v6"/><path d="M19 22h6M19 27h6"/>',
    "whistle": '<circle cx="24" cy="39" r="14"/><circle cx="24" cy="39" r="4.5"/><path d="M34 29l20-5v10l-16 4"/><path d="M40 18l2-6M47 20l4-5"/>',
    "glove": '<path d="M19 52V31l-4.5-8.5L19 20l5 8V13h4.5v15V11h4.5v17V14h4.5v16V20h4.5v23c0 9-6 13-13 13h-7z"/>',
    "cup": '<path d="M20 11h24v15c0 9-5.5 15-12 15s-12-6-12-15z"/><path d="M20 16h-8c0 8 3.5 12 8.5 12M44 16h8c0 8-3.5 12-8.5 12M32 41v8M25 49h14M21 55h22"/>',
    "medal": '<path d="M21 6l11 17 11-17"/><circle cx="32" cy="41" r="15"/><path d="M32 33l2.6 5.4 5.9.8-4.3 4.1 1 5.9-5.2-2.8-5.2 2.8 1-5.9-4.3-4.1 5.9-.8z"/>',
    "crown": '<path d="M9 47L7 19l15 12 10-18 10 18 15-12-2 28z"/><path d="M11 54h42"/><circle cx="32" cy="38" r="2.5"/>',
    "star": '<path d="M32 7l7.6 16.2 17.4 2.2-12.8 12.1 3.3 17.4L32 46.4 16.5 55l3.3-17.4L7 25.4l17.4-2.2z"/>',
    "bolt": '<path d="M37 5L13 37h17l-4 22 25-33H34z"/>',
    "flame": '<path d="M32 5c2.5 11 15 15 15 29a15 15 0 0 1-30 0c0-9 5-13 9-17 0 6 3 9 6 9 0-8-2-15 0-21z"/><path d="M32 54a7 7 0 0 1-7-7c0-5 4-7 7-12 3 5 7 7 7 12a7 7 0 0 1-7 7z"/>',
    "shield": '<path d="M32 6l22 8v16c0 14-9.500 22-22 28C19.500 52 10 44 10 30V14z"/><path d="M32 17v32M20 28h24"/>',
    "gem": '<path d="M17 10h30l10 14-25 33L7 24z"/><path d="M7 24h50M25 10l-4 14 11 33 11-33-4-14"/>',
    "skull": '<path d="M32 7c-12.500 0-21 8-21 20 0 7.500 3 11.500 8 14.500V50h26v-8.500c5-3 8-7 8-14.500C53 15 44.500 7 32 7z"/><circle cx="23.500" cy="29" r="5"/><circle cx="40.500" cy="29" r="5"/><path d="M32 36l-3 5h6zM26 50v5M32 50v5M38 50v5"/>',
    "rocket": '<path d="M32 5c10.500 8 15 21 10.500 36h-21C17 26 21.500 13 32 5z"/><circle cx="32" cy="24" r="4.500"/><path d="M21.500 41L12 52l12-2.500M42.500 41L52 52l-12-2.500M27 47l5 12 5-12"/>',
    "clover": '<circle cx="23" cy="23" r="10"/><circle cx="41" cy="23" r="10"/><circle cx="23" cy="39" r="10"/><circle cx="41" cy="39" r="10"/><path d="M32 31c0 12 4 20 12 26"/>',
    "controller": '<path d="M14 21h36c7 0 11 7 11 17.500S57 54 53 54s-6.500-6-11-6H22c-4.500 0-7 6-11 6S3 49 3 38.500 7 21 14 21z"/><path d="M20 30v9M15.500 34.500h9"/><circle cx="43" cy="32" r="2"/><circle cx="49" cy="38" r="2"/>',
    "headset": '<path d="M11 40v-7c0-12.500 8.500-21 21-21s21 8.500 21 21v7"/><rect x="9" y="35" width="10" height="16" rx="4"/><rect x="45" y="35" width="10" height="16" rx="4"/><path d="M53 51c0 6-6 8-15 8"/>',
    "clock": '<circle cx="32" cy="33" r="21"/><path d="M32 19v15l10 6M26 7h12M32 7v5"/>',
    "target": '<circle cx="30" cy="34" r="22"/><circle cx="30" cy="34" r="13"/><circle cx="30" cy="34" r="4"/><path d="M30 34L54 10M46 10h8v8"/>',
    "dice": '<rect x="10" y="10" width="44" height="44" rx="10"/><circle cx="22" cy="22" r="3"/><circle cx="42" cy="22" r="3"/><circle cx="32" cy="32" r="3"/><circle cx="22" cy="42" r="3"/><circle cx="42" cy="42" r="3"/>',
    "heart": '<path d="M32 56C9 40 7 26 7 21a13 13 0 0 1 25-4 13 13 0 0 1 25 4c0 5-2 19-25 35z"/>',
}


def skin_svg(name, icon, rarity, hue):
    c, glow = RARITY[rarity]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl({hue},45%,17%)"/><stop offset="1" stop-color="hsl({hue},55%,8%)"/></linearGradient>
<radialGradient id="gl" cx=".5" cy=".46" r=".6"><stop offset="0" stop-color="{glow}" stop-opacity=".75"/><stop offset="1" stop-color="{glow}" stop-opacity="0"/></radialGradient>
<linearGradient id="st" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="{c}"/></linearGradient>
</defs>
<rect width="128" height="128" rx="22" fill="url(#bg)"/>
<rect width="128" height="128" rx="22" fill="url(#gl)"/>
<g opacity=".5" fill="{c}"><circle cx="20" cy="22" r="1.6"/><circle cx="108" cy="30" r="1.2"/><circle cx="102" cy="100" r="1.8"/><circle cx="24" cy="98" r="1.2"/></g>
<g transform="translate(24 20) scale(1.25)" fill="{c}" fill-opacity=".22" stroke="url(#st)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">{ICONS[icon]}</g>
<rect x="14" y="116" width="100" height="4" rx="2" fill="{c}"/>
<rect x=".75" y=".75" width="126.5" height="126.5" rx="21.5" fill="none" stroke="{c}" stroke-opacity=".5" stroke-width="1.5"/>
</svg>'''


def case_svg(icon, hue):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl({hue},45%,16%)"/><stop offset="1" stop-color="hsl({hue},55%,7%)"/></linearGradient>
<radialGradient id="gl" cx=".5" cy=".5" r=".6"><stop offset="0" stop-color="hsl({hue},95%,60%)" stop-opacity=".55"/><stop offset="1" stop-color="hsl({hue},95%,60%)" stop-opacity="0"/></radialGradient>
<linearGradient id="body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl({hue},60%,45%)"/><stop offset="1" stop-color="hsl({hue},65%,24%)"/></linearGradient>
<linearGradient id="lid" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl({hue},70%,62%)"/><stop offset="1" stop-color="hsl({hue},65%,40%)"/></linearGradient>
</defs>
<rect width="128" height="128" rx="22" fill="url(#bg)"/>
<rect width="128" height="128" rx="22" fill="url(#gl)"/>
<ellipse cx="64" cy="108" rx="40" ry="6" fill="#000" opacity=".35"/>
<rect x="24" y="56" width="80" height="50" rx="8" fill="url(#body)"/>
<rect x="19" y="38" width="90" height="24" rx="9" fill="url(#lid)"/>
<rect x="57" y="38" width="14" height="68" fill="#FFD66B" opacity=".92"/>
<g transform="translate(40 62) scale(.75)" fill="#fff" fill-opacity=".2" stroke="#fff" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round">{ICONS[icon]}</g>
<rect x="58" y="70" width="12" height="12" rx="3" fill="#FFB627" stroke="#7A4A00" stroke-width="1.5"/>
<path d="M30 20l2 5 5 2-5 2-2 5-2-5-5-2 5-2z" fill="#fff" opacity=".8"/>
<rect x=".75" y=".75" width="126.5" height="126.5" rx="21.5" fill="none" stroke="#fff" stroke-opacity=".18" stroke-width="1.5"/>
</svg>'''


def main():
    os.makedirs(os.path.join(ROOT, "web", "skins"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "web", "cases"), exist_ok=True)
    n = 0
    for case in build():
        if case.get("real"):  # Dota-кейсы используют настоящие иконки из web/dota и web/cases/dota_*.webp
            continue
        with open(os.path.join(ROOT, "web", "cases", f"{case['slug']}.svg"), "w", encoding="utf-8") as f:
            f.write(case_svg(case["icon"], case["hue"]))
        for it in case["items"]:
            with open(os.path.join(ROOT, "web", "skins", f"{it['slug']}.svg"), "w", encoding="utf-8") as f:
                f.write(skin_svg(it["name"], it["icon"], it["rarity"], case["hue"]))
            n += 1
    print(f"Готово: {n} скинов и {len([c for c in build() if not c.get('real')])} кейсов")


if __name__ == "__main__":
    main()
