from __future__ import annotations

"""Видно ли окно программы прямо сейчас.

Окно знает об этом само (показали, свернули, убрали в трей) и отмечает здесь;
читать можно из любого потока, не трогая Qt. Нужно автообновлению: вместе с
вопросом «есть новее?» программа называет серверу, открыта она или висит в
трее, — на сайте виден общий счёт, чем заняты программы.
"""

import threading
import time

_lock = threading.Lock()
_window_shown: bool | None = None
# Когда окно последний раз стало видимым и сколько до этого пробыло скрытым:
# после долгого фона программа и память «остывшие», и фоновой работе лучше
# переждать первые секунды после возврата (см. ``return_settle_remaining_ms``).
_shown_since: float | None = None
_hidden_since: float = time.monotonic()
_hidden_for_s: float = 0.0

# Окно, пробывшее скрытым меньше этого, «остывшим» не считается.
RETURN_MIN_AWAY_S = 30.0
# Сколько после возврата фоновая работа программы ждёт.
RETURN_SETTLE_S = 10.0


def note_window_shown(shown: bool, *, now: float | None = None) -> None:
    global _window_shown, _shown_since, _hidden_since, _hidden_for_s
    moment = time.monotonic() if now is None else float(now)
    with _lock:
        shown = bool(shown)
        if shown and not _window_shown:
            _hidden_for_s = max(0.0, moment - _hidden_since)
            _shown_since = moment
        elif not shown and _window_shown is not False:
            _hidden_since = moment
            _shown_since = None
        _window_shown = shown


def window_shown() -> bool | None:
    """True — окно на экране, False — свёрнуто или в трее, None — ещё не показывалось."""
    with _lock:
        return _window_shown


def return_settle_remaining_ms(*, now: float | None = None) -> int:
    """Сколько ещё миллисекунд после возврата окна из долгого фона ждать фоновой работе; 0 — не надо.

    Сразу после возврата поток окна и так тянет с диска свою память и строит
    первый кадр. Отложенные сборки страниц, опросы процессов и подобное, начатые
    в эти секунды, делят с ним диск и замок Python — окно замирает дольше.
    """
    moment = time.monotonic() if now is None else float(now)
    with _lock:
        if not _window_shown or _shown_since is None or _hidden_for_s < RETURN_MIN_AWAY_S:
            return 0
        left = RETURN_SETTLE_S - (moment - _shown_since)
    return max(0, int(round(left * 1000.0)))


__all__ = ["RETURN_MIN_AWAY_S", "RETURN_SETTLE_S", "note_window_shown", "return_settle_remaining_ms", "window_shown"]
