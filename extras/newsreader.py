"""
extras/newsreader.py — чтение новостей Dota 2 прямо в приложении.

Берёт страницу статьи с dota2.ru, достаёт из неё тело статьи и превращает в БЕЗОПАСНЫЙ HTML
(белый список тегов, ссылки превращаются в обычный текст, скрипты/iframe/формы выбрасываются),
чтобы игроку не надо было уходить на сайт.
"""
import html as _html
import re
import threading
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

ALLOWED_HOSTS = {"dota2.ru", "www.dota2.ru"}
BASE = "https://dota2.ru"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MAX_BYTES = 3_000_000
MAX_HTML = 80_000
CACHE_TTL = 1800
CACHE_MAX = 60

_cache = {}
_lock = threading.Lock()

SKIP = {
    "script", "style", "iframe", "video", "audio", "form", "button", "svg", "noscript", "meta", "link", "input",
    "select", "textarea", "canvas", "object", "embed", "template", "head", "nav", "aside", "footer",
}
SKIP_CLASS = re.compile(r"(^|[\s_-])(adv|ads|banner|share|social|subscribe|comment|spoiler-btn|js-ads)|esport-(tabs|tournament|match|bracket|grid)|global-content__btn", re.I)
INLINE = {"strong": "strong", "b": "strong", "em": "em", "i": "em", "u": "u"}
BLOCK = {"p": "p", "ul": "ul", "ol": "ol", "li": "li", "blockquote": "blockquote", "table": "table", "thead": "thead",
         "tbody": "tbody", "tr": "tr", "th": "th", "td": "td"}
HEADINGS = {"h1": "h3", "h2": "h3", "h3": "h3", "h4": "h4", "h5": "h4", "h6": "h4"}


class NewsError(Exception):
    pass


def clean_url(url):
    """Проверяет, что это https-адрес статьи на dota2.ru, и убирает utm-хвост."""
    try:
        u = urlparse(str(url or "").strip())
    except Exception:
        raise NewsError("Неверная ссылка")
    if u.scheme not in ("http", "https") or (u.hostname or "").lower() not in ALLOWED_HOSTS:
        raise NewsError("Читать в приложении можно только новости dota2.ru")
    return f"https://dota2.ru{u.path}"


def _img(tag):
    cls = " ".join(tag.get("class") or [])
    if "flag" in cls or "emoji" in cls:
        return ""
    src = tag.get("src") or tag.get("data-src") or ""
    if not src or src.startswith("data:"):
        src = tag.get("data-src") or tag.get("data-original") or ""
    if not src:
        return ""
    src = urljoin(BASE + "/", src)
    p = urlparse(src)
    if p.scheme not in ("http", "https"):
        return ""
    for dim in ("width", "height"):
        try:
            if int(str(tag.get(dim, "100")).replace("px", "")) <= 24:
                return ""  # иконки/смайлы/пиксели
        except ValueError:
            pass
    return f'<img src="{_html.escape(src, quote=True)}" alt="" loading="lazy" referrerpolicy="no-referrer">'


def _render(node):
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return _html.escape(str(node))
    if not isinstance(node, Tag):
        return ""
    name = (node.name or "").lower()
    if name in SKIP:
        return ""
    if SKIP_CLASS.search(" ".join(node.get("class") or [])):
        return ""
    if re.search(r"display\s*:\s*none", node.get("style") or "", re.I):
        return ""
    if name == "img":
        return _img(node)
    if name == "br":
        return "<br>"
    if name == "hr":
        return "<hr>"
    inner = "".join(_render(c) for c in node.children)
    if name in HEADINGS:
        t = HEADINGS[name]
        return f"<{t}>{inner}</{t}>" if inner.strip() else ""
    if name in BLOCK:
        t = BLOCK[name]
        return f"<{t}>{inner}</{t}>"
    if name in INLINE:
        t = INLINE[name]
        return f"<{t}>{inner}</{t}>" if inner.strip() else inner
    # a, span, div, section, figure, s и т.п. — оставляем только содержимое
    return inner


def _tidy(h):
    h = re.sub(r"<p>(\s|&nbsp;|&#160;|\xa0|<br>)*</p>", "", h)
    h = re.sub(r"(<br>\s*){3,}", "<br><br>", h)
    h = re.sub(r"\s{3,}", "\n", h)
    h = h.strip()
    # убираем «висящие» в конце заголовки и разделители (после вырезанных виджетов)
    prev = None
    while prev != h:
        prev = h
        h = re.sub(r"(<hr>|<h[34]>[^<]*</h[34]>|<br>)\s*$", "", h).strip()
    return h


def parse_article(page_html):
    soup = BeautifulSoup(page_html, "lxml")
    body = (
        soup.select_one('[itemprop="articleBody"]')
        or soup.select_one("div.p-content.js-text-errors-wrap")
        or soup.select_one("div.p-content")
    )
    if body is None:
        raise NewsError("Не удалось найти текст новости")
    h1 = soup.find("h1")
    og_title = soup.find("meta", property="og:title")
    og_img = soup.find("meta", property="og:image")
    title = (h1.get_text(" ", strip=True) if h1 else "") or (og_title.get("content", "") if og_title else "")
    out = _tidy("".join(_render(c) for c in body.children))
    if len(re.sub(r"<[^>]+>", "", out).strip()) < 40:
        raise NewsError("Текст новости пуст")
    if len(out) > MAX_HTML:
        out = out[:MAX_HTML]
        out = out[: out.rfind("</p>") + 4] if "</p>" in out else out
    image = ""
    if og_img and og_img.get("content"):
        image = urljoin(BASE + "/", og_img["content"])
        if urlparse(image).scheme not in ("http", "https"):
            image = ""
    # если первая картинка статьи совпадает с обложкой — не дублируем
    if image and f'src="{_html.escape(image, quote=True)}"' in out:
        image = ""
    return {"title": title, "image": image, "html": out}


def _download(url):
    try:
        with requests.get(url, headers={"User-Agent": UA, "Accept-Language": "ru"}, timeout=(5, 10), stream=True) as r:
            if r.status_code != 200:
                raise NewsError("Сайт новостей сейчас недоступен")
            if (urlparse(r.url).hostname or "").lower() not in ALLOWED_HOSTS:
                raise NewsError("Неверный адрес новости")
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) > MAX_BYTES:
                    break
            enc = r.encoding or "utf-8"
            return bytes(buf).decode(enc, errors="ignore")
    except NewsError:
        raise
    except Exception:
        raise NewsError("Не удалось загрузить новость")


def get_article(url):
    url = clean_url(url)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = parse_article(_download(url))
    with _lock:
        if len(_cache) >= CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
        _cache[url] = (now, data)
    return data
