"""Скачивает картинки скинов CS2 в web/cs2/<имя>.webp и собирает обложки кейсов.

Почему это нужно: каталог (extras/catalog.py) ссылается на /web/cs2/<имя>.webp, но самих файлов
в репозитории не было — поэтому в кейсах CS2 были пустые картинки.

Источник — открытая база CS2-скинов ByMykel/CSGO-API (картинки лежат на CDN Steam).
Запуск (нужны Python 3.11+, requests, pillow):
    pip install requests pillow
    python tools/fetch_cs2_images.py
Скрипт можно запускать повторно: уже скачанные файлы пропускаются (--force — перекачать).
"""
import io
import os
import re
import subprocess
import sys

import requests
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_cases():
    """Грузим extras/catalog.py напрямую (без пакета extras, чтобы не требовать FastAPI)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("catalog", os.path.join(ROOT, "extras", "catalog.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CS2_CASES


CS2_CASES = _load_cases()

API = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/skins.json"
OUT = os.path.join(ROOT, "web", "cs2")
WIDTH = 320


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower().replace("★", "").replace("stattrak™", ""))


def main():
    force = "--force" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    print("Загружаю каталог скинов…")
    data = requests.get(API, timeout=120).json()
    by_name = {}
    for s in data:
        by_name.setdefault(norm(s["name"]), s)  # у Doppler несколько фаз — берём первую

    wanted = {}
    for case in CS2_CASES:
        for name, _usd, img in case["items"]:
            wanted[img] = name

    miss = []
    for img, name in wanted.items():
        path = os.path.join(OUT, img + ".webp")
        if os.path.exists(path) and not force:
            continue
        s = by_name.get(norm(name))
        if not s:
            miss.append(name)
            print("  НЕ НАЙДЕН в каталоге:", name)
            continue
        r = requests.get(s["image"], timeout=60)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGBA")
        bbox = im.getbbox()
        if bbox:
            im = im.crop(bbox)
        h = max(1, round(im.height * WIDTH / im.width))
        im = im.resize((WIDTH, h), Image.LANCZOS)
        im.save(path, "WEBP", quality=88, method=6)
        print("  ok", img)

    if miss:
        print("Не найдены:", miss)
        sys.exit(1)
    print("Собираю обложки кейсов…")
    subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "gen_cs2_covers.py")])


if __name__ == "__main__":
    main()
