"""Локальный запуск для проверки интерфейса (без Turso): python tests/serve_local.py  → http://127.0.0.1:8123"""
import os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); os.chdir(ROOT)
DB = os.environ.get("LOCAL_DB") or os.path.join(tempfile.mkdtemp(), "ui.db")
os.environ.update(BOT_TOKEN="123456:TESTTOKEN", TURSO_DATABASE_URL="x", TURSO_AUTH_TOKEN="y", ADMIN_IDS="1", FEED_ENABLED=os.environ.get("FEED_ENABLED", "0"))
import libsql; _r = libsql.connect; libsql.connect = lambda *a, **k: _r(DB)
import uvicorn, app
from extras import core
core.ctx.bot = None
uvicorn.run(app.app, host="127.0.0.1", port=8123, log_level="warning")
