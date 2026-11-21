"""Проверка модели кэфов киберспорта: python tests/esports_selftest.py (без сети и БД)."""
import importlib.util
import pathlib
import random

p = pathlib.Path(__file__).resolve().parents[1] / "extras" / "esports_odds.py"
sp = importlib.util.spec_from_file_location("esports_odds", p)
eo = importlib.util.module_from_spec(sp)
sp.loader.exec_module(eo)

ok = True


def check(name, cond):
    global ok
    print(("✅" if cond else "❌"), name)
    ok = ok and cond


def book(odds):
    return sum(1 / o for o in odds if o)


# равные команды
o = eo.match_odds({}, {}, 1, 2, 3)
check("равные команды bo3 ≈ 1.85–1.92", 1.85 <= o[0] == o[2] <= 1.92)
# маржа 5–9%
for bo in (1, 3, 5):
    r = {1: 1580, 2: 1500}; g = {1: 80, 2: 80}
    m = book(eo.match_odds(r, g, 1, 2, bo)) - 1
    check(f"маржа bo{bo} 5–9% ({m:.1%})", 0.05 <= m <= 0.09)
# фаворит в bo3 короче, чем в bo1
r = {1: 1600, 2: 1500}; g = {1: 80, 2: 80}
check("фаворит в bo3 дешевле, чем в bo1", eo.match_odds(r, g, 1, 2, 3)[0] < eo.match_odds(r, g, 1, 2, 1)[0])
# bo2 → ничья
o = eo.match_odds({}, {}, 1, 2, 2)
check("bo2 даёт X", o[1] is not None and 1.7 < o[1] < 2.3)
# границы
o = eo.match_odds({1: 2400, 2: 800}, {1: 99, 2: 99}, 1, 2, 5)
check("границы 1.04..12", o[0] >= 1.04 and o[2] <= 12.0)
# неизвестная команда не становится фаворитом
o = eo.match_odds({1: 1800}, {1: 2}, 1, 2, 3)
check("новичок с 2 картами не фаворит 1.1", o[0] > 1.5)
# рейтинги из истории: сильная команда должна получить низкий кэф
random.seed(1)
past = []
for i in range(120):
    a_win = random.random() < 0.8
    past.append({"opponents": [{"opponent": {"id": 1}}, {"opponent": {"id": 2}}], "winner_id": 1 if a_win else 2,
                 "results": [{"team_id": 1, "score": 2 if a_win else 1}, {"team_id": 2, "score": 1 if a_win else 2}],
                 "end_at": f"2026-01-{1 + i % 28:02d}T12:00:00Z", "tournament": {"tier": "a"}})
rt, gm = eo.build_ratings(past)
o = eo.match_odds(rt, gm, 1, 2, 3)
check(f"по истории фаворит короче аутсайдера {o}", o[0] < 1.5 < 2.0 < o[2])
print("ОК" if ok else "ЕСТЬ ОШИБКИ")
raise SystemExit(0 if ok else 1)
