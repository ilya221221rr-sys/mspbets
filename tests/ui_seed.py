"""Матчи для UI-проверок (plus2_ui.js, wave1_ui.js): запускать ПОСЛЕ старта tests/serve_local.py на свежей базе.
    LOCAL_DB=/tmp/ui/p2.db python tests/ui_seed.py
id 1–3 и 5 — матчи ESPN (есть доп. рынки), 4 и 6 — киберспорт (только П1/X/П2)."""
import datetime
import os
import sqlite3

DB = os.environ.get("LOCAL_DB") or "/tmp/ui/p2.db"
c = sqlite3.connect(DB)
st = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
EVENTS = [
    ("Футбол", "Арсенал", "Челси", (2.1, 3.4, 3.5), "soccer/eng.1", "u1"),
    ("Баскетбол", "Lakers", "Celtics", (1.8, None, 2.05), "basketball/nba", "u2"),
    ("Футбол", "Реал", "Барселона", (2.4, 3.5, 2.8), "soccer/esp.1", "u3"),
    ("Dota 2", "Team Spirit", "Gaimin", (1.6, None, 2.3), None, None),
    ("Футбол", "Ювентус", "Милан", (1.9, 3.3, 4.0), "soccer/ita.1", "u5"),
    ("CS2", "NAVI", "Vitality", (1.5, None, 2.6), None, None),
]
for sport, home, away, o, path, ext in EVENTS:
    eid = c.execute("INSERT INTO events (sport, title, starts_at, status, odds_1, odds_x, odds_2, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (sport, f"{home} — {away}", st, "open", o[0], o[1], o[2], now)).lastrowid
    if path:
        c.execute("INSERT INTO feed_events (event_id, source, ext_id, league, sport_path, home, away, starts_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (eid, "espn", ext, "Лига", path, home, away, st, now))
c.commit()
print("seeded", len(EVENTS))
