"""
extras/plusdb.py — короткие транзакции Turso для модулей plus.

Почему это важно. В Turso (libsql/Hrana) транзакция начинается с первой записи и держит блокировку
записи на всю базу. Пока она открыта, остальные запросы с записью ждут; если транзакция затянулась
(медленные чтения, сетевой запрос), Turso откатывает её с ошибкой
  «SQLITE_BUSY: interactive transaction was rolled back because the stream was idle for too long»,
а приложение у игрока «зависает» (так было с разделом «Бонусы»).

Правило для plus-модулей: сначала все чтения и проверки, запись — последней и короткой.
`touch_user(conn, user)` в начале обработчика нарушает правило: его INSERT OR IGNORE открывает
транзакцию сразу. Вместо него — ensure_user(user): если игрок уже есть, это одно чтение без транзакции,
а регистрация нового игрока идёт отдельной мгновенной транзакцией.
"""
import threading

from . import core
from .core import db, one

_known = set()
_guard = threading.Lock()


def ensure_user(user):
    """Гарантировать строки users и profiles (баланс 1000 при первом входе), не открывая долгих транзакций."""
    uid = int(user["id"])
    if uid in _known:
        return uid
    with db() as conn:
        if not one(conn, "SELECT 1 FROM users u JOIN profiles p ON p.user_id=u.user_id WHERE u.user_id=?", (uid,)):
            core.touch_user(conn, user)
    with _guard:
        if len(_known) > 50_000:
            _known.clear()
        _known.add(uid)
    return uid


def forget(uid=None):
    """Сбросить кэш известных игроков (для тестов)."""
    with _guard:
        if uid is None:
            _known.clear()
        else:
            _known.discard(int(uid))
