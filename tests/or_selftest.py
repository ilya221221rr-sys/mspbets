"""
Проверка клиента ИИ для OpenRouter: перебор моделей, фото, роль system, пустые ответы, живой каталог моделей.
    python tests/or_selftest.py
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); os.chdir(ROOT)
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", AI_API_KEY="k", AI_BASE_URL="https://openrouter.ai/api/v1",
                  AI_MODEL="some/dead-model:free")
import requests
from extras import support

OK = FAIL = 0
def check(name, cond, extra=""):
    global OK, FAIL
    if cond: OK += 1; print("  ✓", name)
    else: FAIL += 1; print("  ✗ FAIL:", name, extra)

class R:
    def __init__(s, code, data=None, text=""):
        s.status_code, s._d, s.text = code, data, text or str(data)
    def json(s): return s._d
    def raise_for_status(s): pass
def ok(txt): return R(200, {"choices": [{"message": {"content": txt}}]})

# каталог моделей (подменяем сеть)
CATALOG = {"data": [
    {"id": "google/gemma-4-31b-it:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 262144,
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
    {"id": "openai/gpt-oss-120b:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 131072,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
    {"id": "google/lyria-3-pro-preview", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 1000000,
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text", "audio"]}},
    {"id": "paid/model", "pricing": {"prompt": "0.1", "completion": "0.1"}, "context_length": 1000,
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
]}
requests.get = lambda url, **k: R(200, CATALOG)
log = []
script = {}
def fake_post(url, headers=None, json=None, timeout=None):
    model = json["model"]; log.append((model, json["messages"], headers))
    return script[model](json) if model in script else ok("fallback-ok")
requests.post = fake_post

print("== каталог и порядок моделей")
text_c, vis_c = support._candidates(False), support._candidates(True)
check("текстовые: основная первой, потом бесплатные", text_c[0] == "some/dead-model:free" and "openai/gpt-oss-120b:free" in text_c, text_c)
check("платные и аудио-модели отфильтрованы", "paid/model" not in text_c and "google/lyria-3-pro-preview" not in text_c, text_c)
check("для фото — только модели с картинками", vis_c == ["google/gemma-4-31b-it:free"], vis_c)

print("== перебор моделей")
script["some/dead-model:free"] = lambda p: R(404, {"error": {"message": "No endpoints found"}}, "No endpoints found")
script["google/gemma-4-31b-it:free"] = lambda p: R(429, text="rate limited")
script["openai/gpt-oss-120b:free"] = lambda p: ok("  ")  # пустой ответ
log.clear()
try:
    support.ai_complete([{"role": "user", "content": "hi"}])
    check("все модели провалились -> AIError", False)
except support.AIError as e:
    check("все модели провалились -> AIError с причинами", "dead-model" in str(e) and "empty answer" in str(e), str(e))
check("обращение к OpenRouter с атрибуцией", log and log[0][2].get("X-Title") == "MSPBETS" and "HTTP-Referer" in log[0][2])

script["google/gemma-4-31b-it:free"] = lambda p: ok("привет!")
check("404 -> следующая модель отвечает", support.ai_complete([{"role": "user", "content": "hi"}]) == "привет!")
check("запомнили модель", support.LAST_AI_MODEL == "google/gemma-4-31b-it:free")

print("== роль system не поддерживается (Gemma)")
def gemma(p):
    if any(m["role"] == "system" for m in p["messages"]):
        return R(400, text="Developer instruction is not enabled for models/gemma")
    return ok("без system ок")
script["google/gemma-4-31b-it:free"] = gemma
msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "вопрос"}]
log.clear()
check("system вклеивается в сообщение игрока", support.ai_complete(msgs) == "без system ок" and "SYS" in log[-1][1][0]["content"])

print("== фото")
rows = [("user", "что это?", 7)]
m = support._build_messages(rows, ("image/png", b"\x89PNG"))
check("фото в сообщении игрока", isinstance(m[1]["content"], list) and m[1]["content"][1]["type"] == "image_url")
log.clear()
script["google/gemma-4-31b-it:free"] = lambda p: ok("вижу картинку")
check("фото уходит vision-модели (не основной текстовой)", support.ai_complete(m) == "вижу картинку" and log[0][0] == "google/gemma-4-31b-it:free", log[0][0])
rows2 = [("user", "что это?", 7), ("ai", "красный квадрат", None), ("user", "а какого он цвета?", None)]
m2 = support._build_messages(rows2, ("image/png", b"x"))
check("уточняющий вопрос: картинка остаётся у сообщения с фото", isinstance(m2[1]["content"], list) and isinstance(m2[3]["content"], str))

print("== валидатор ответа")
seq = iter(["не json", '{"reasons":["a"]}'])
script["google/gemma-4-31b-it:free"] = lambda p: ok(next(seq))
import json as _j
def valid(t):
    if not _j.loads(t).get("reasons"): raise ValueError
script["some/dead-model:free"] = lambda p: ok("плохо")
script["openai/gpt-oss-120b:free"] = lambda p: ok('{"reasons":["ok"]}')
check("плохой ответ -> следующая модель", support.ai_complete([{"role": "user", "content": "x"}], validate=valid) is not None)

print(f"\nИтого: {OK} ок, {FAIL} ошибок")
sys.exit(1 if FAIL else 0)
