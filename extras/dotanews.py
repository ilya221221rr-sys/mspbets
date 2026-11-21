"""
extras/dotanews.py — лента новостей Dota 2 с запасными источниками.

Почему переписано: RSS dota2.ru (https://dota2.ru/news/rss/) стал отвечать HTTP 500, и лента в приложении
оставалась пустой («Не удалось загрузить новости»). Теперь список собирается по цепочке, пока какой-то источник не отдаст новости:

  1. RSS dota2.ru (если вернётся к жизни) — скачиваем через requests с нормальным User-Agent;
  2. страница https://dota2.ru/news/ — парсим карточки (заголовок, ссылка, картинка, время);
  3. Steam News API (официальные новости Dota 2, appid 570) — запасной вариант, ссылки ведут на внешние сайты.

Результат кэшируется (10 минут). Если все источники упали — отдаём последний удачный список (даже устаревший).
Формат элемента: {title, link, published (ISO-8601 или текст), summary, image, source}.
"""
import html as _html
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("mspbets.dotanews")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
BASE = "https://dota2.ru"
RSS_URLS = ("https://dota2.ru/news/rss/", "https://dota2.ru/rss/news/")
LIST_URL = "https://dota2.ru/news/"
STEAM_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=570&count=12&maxlength=400&format=json"
LIMIT = 12
TTL = 600          # секунд: свежий кэш
RETRY_TTL = 60     # секунд: после неудачи не долбим источники чаще раза в минуту
MSK = timezone(timedelta(hours=3))

_cache = {"ts": 0.0, "ok": 0.0, "data": []}
_lock = threading.Lock()


def _get(url):
    return requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"}, timeout=(5, 10))


def _text(s, n=180):
    s = re.sub(r"<[^>]+>", " ", _html.unescape(str(s or "")))
    s = re.sub(r"\[/?[a-z0-9_=\- ]+\]", " ", s, flags=re.I)  # BBCode из Steam
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


# ------------------------------------------------------------------ 1. RSS
def _from_rss():
    import feedparser

    for url in RSS_URLS:
        try:
            r = _get(url)
            if r.status_code != 200 or b"<item" not in r.content[:200000]:
                continue
            feed = feedparser.parse(r.content)
            out = []
            for e in feed.entries[:LIMIT]:
                link = e.get("link", "")
                if not link:
                    continue
                pub = e.get("published", "")
                try:
                    pub = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
                except Exception:
                    pass
                img = ""
                for enc in e.get("enclosures", []) or []:
                    if str(enc.get("type", "")).startswith("image"):
                        img = enc.get("href", "")
                out.append({"title": _text(e.get("title", ""), 200) or "Без названия", "link": link, "published": pub,
                            "summary": _text(e.get("summary", "")), "image": img, "source": "dota2.ru"})
            if out:
                return out
        except Exception as ex:
            log.info("rss %s failed: %s", url, ex)
    return []


# ------------------------------------------------------------------ 2. HTML dota2.ru/news
_REL = re.compile(r"(\d+)\s*(сек|мин|час|ден|дн|д\.)", re.I)
_DAY = re.compile(r"(сегодня|вчера)\s+в\s+(\d{1,2}):(\d{2})", re.I)


def _when(raw):
    """«23 сек. назад», «сегодня в 11:38», «вчера в 23:36» -> ISO-8601 (UTC). Не распознали — исходный текст."""
    raw = raw.strip()
    now = datetime.now(timezone.utc)
    m = _DAY.search(raw)
    if m:
        msk = now.astimezone(MSK)
        d = msk.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0)
        if m.group(1).lower() == "вчера":
            d -= timedelta(days=1)
        return d.astimezone(timezone.utc).isoformat()
    m = _REL.search(raw)
    if m and "назад" in raw:
        n, u = int(m.group(1)), m.group(2).lower()
        delta = (timedelta(seconds=n) if u == "сек" else timedelta(minutes=n) if u == "мин"
                 else timedelta(hours=n) if u == "час" else timedelta(days=n))
        return (now - delta).isoformat()
    return raw


def _from_site():
    r = _get(LIST_URL)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out, seen = [], set()
    for li in soup.select("li.index__news-item"):
        a = li.select_one("a[href*='/news/']")
        name = li.select_one(".index__news-name")
        if not a or not name:
            continue
        link = urljoin(BASE + "/", a["href"])
        path = urlparse(link).path
        if not re.match(r"^/news/\d+", path) or link in seen:
            continue
        seen.add(link)
        img_tag = li.select_one(".index__news-header img")
        img = ""
        if img_tag:
            img = img_tag.get("src") or img_tag.get("data-src") or ""
            img = urljoin(BASE + "/", img) if img else ""
        stat = li.select_one(".index__news-stat")
        raw_time = ""
        if stat:
            first = next((t for t in stat.strings if t.strip()), "")  # дальше идут счётчики комментариев
            raw_time = first.strip()
            if not _DAY.search(raw_time) and not _REL.search(raw_time):
                raw_time = stat.get_text(" ", strip=True)
        out.append({"title": _text(name.get_text(" ", strip=True), 220), "link": f"{BASE}{path}", "published": _when(raw_time),
                    "summary": "", "image": img, "source": "dota2.ru"})
        if len(out) >= LIMIT:
            break
    return out


# ------------------------------------------------------------------ 3. Steam News API
def _from_steam():
    r = _get(STEAM_URL)
    if r.status_code != 200:
        return []
    items = (r.json().get("appnews") or {}).get("newsitems") or []
    out = []
    for it in items[:LIMIT]:
        link = it.get("url") or ""
        if not link.startswith("http"):
            continue
        img = ""
        m = re.search(r'(?:src="|\[img\])(https?://[^"\]\s]+\.(?:jpg|jpeg|png|webp))', it.get("contents", ""), re.I)
        if m:
            img = m.group(1)
        ts = it.get("date")
        out.append({"title": _text(it.get("title", ""), 200) or "Dota 2",
                    "link": link,
                    "published": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "",
                    "summary": _text(it.get("contents", "")), "image": img, "source": it.get("feedlabel") or "Steam"})
    return out


def _collect():
    for name, fn in (("rss", _from_rss), ("site", _from_site), ("steam", _from_steam)):
        try:
            data = fn()
            if data:
                log.info("dota news: %s -> %d", name, len(data))
                return data
        except Exception as ex:
            log.warning("dota news source %s failed: %s", name, ex)
    return []


def get_news():
    now = time.monotonic()
    with _lock:
        if _cache["data"] and now - _cache["ts"] < (TTL if _cache["ok"] else RETRY_TTL):
            return _cache["data"]
    data = _collect()
    with _lock:
        _cache["ts"] = time.monotonic()
        if data:
            _cache.update(ok=1.0, data=data)
        else:
            _cache["ok"] = 0.0  # оставляем старый список, если он был
        return _cache["data"]
