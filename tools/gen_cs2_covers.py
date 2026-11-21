"""Генерирует обложки CS2-кейсов (web/cases/cs2_*.webp): веер из 3 скинов на цветном фоне.
Запуск (нужен pillow):  python tools/gen_cs2_covers.py"""
import colorsys
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERS = {
    "cs2_popular": (210, ["ak47_redline", "awp_asiimov", "m4a1s_hyper_beast"]),
    "cs2_legend": (28, ["karambit_doppler", "ak47_fire_serpent", "butterfly_fade"]),
}
S = 256


def hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
    return int(r * 255), int(g * 255), int(b * 255)


def rounded_mask(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], r, fill=255)
    return m


for name, (hue, items) in COVERS.items():
    bg = Image.new("RGB", (S, S))
    px = bg.load()
    c1, c2 = hsl(hue, 45, 17), hsl(hue, 55, 6)
    glow = hsl(hue, 85, 50)
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S)
            base = [c1[i] + (c2[i] - c1[i]) * t for i in range(3)]
            d = ((x - S * .5) ** 2 + (y - S * .6) ** 2) ** .5 / (S * .6)
            a = max(0, 1 - d) * .7
            px[x, y] = tuple(int(base[i] * (1 - a) + glow[i] * a) for i in range(3))
    bg = bg.convert("RGBA")
    specs = [(-12, 14, 40, 110), (12, 118, 40, 110), (0, 40, 108, 176)]  # угол, x, y, ширина
    for it, (ang, x, y, w) in zip(items, specs):
        im = Image.open(os.path.join(ROOT, "web", "cs2", it + ".webp")).convert("RGBA")
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        h = int(w * im.height / im.width)
        im = im.resize((w, h), Image.LANCZOS)
        card = Image.new("RGBA", (w + 16, h + 16), (0, 0, 0, 0))
        card.alpha_composite(im, (8, 8))
        card = card.rotate(ang, expand=True, resample=Image.BICUBIC)
        bg.alpha_composite(card, (x, y))
    mask = rounded_mask((S, S), 22)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    ImageDraw.Draw(out).rounded_rectangle([1, 1, S - 2, S - 2], 21, outline=(255, 255, 255, 50), width=2)
    out.save(os.path.join(ROOT, "web", "cases", name + ".webp"), "WEBP", quality=90)
    print("ok", name)
