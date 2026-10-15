"""Освобождение памяти, пока окно программы долго скрыто в трее или свёрнуто.

Когда окно убрано дольше IDLE_TRIM_DELAY_MS, собираем мусор и возвращаем
системе пустые блоки кучи — это настоящая экономия памяти.

Рабочий набор (страницы в оперативной памяти) и кэш картинок Qt не трогаем.
Раньше здесь всё выгружалось принудительно, и окно открывалось из трея
медленно: Qt заново рисовал значки, а Windows подтягивала страницы обратно.
Выгрузка ничего не экономила: Windows и сама за пару минут отдаёт страницы
скрытого окна другим программам (замер на win10: 48 → 8 МБ без нашей
команды), а занятая память программы от этого не меняется.
"""

from __future__ import annotations

import gc
import sys
import time

from PyQt6.QtCore import QEvent, QObject, QTimer

from log.log import log


# Сколько окно должно пробыть скрытым, прежде чем чистить память.
IDLE_TRIM_DELAY_MS = 2 * 60 * 1000
# Пока окно скрыто, фоновая работа (оркестратор, прокси) снова копит
# мусор — повторяем очистку с таким интервалом.
IDLE_TRIM_REPEAT_MS = 30 * 60 * 1000

_WATCHED_EVENTS = (QEvent.Type.Hide, QEvent.Type.Show, QEvent.Type.WindowStateChange)


def _working_set_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return None


def _release_free_heap_to_windows() -> None:
    import ctypes

    # Возвращает системе пустые блоки кучи C-рантайма после крупных освобождений.
    ctypes.cdll.ucrtbase._heapmin()


def trim_process_memory() -> None:
    """Собирает мусор и возвращает системе пустые блоки кучи."""
    started = time.perf_counter()
    before = _working_set_mb()
    collected = gc.collect()
    if sys.platform == "win32":
        try:
            _release_free_heap_to_windows()
        except Exception as exc:
            log(f"Не удалось вернуть память системе: {exc}", "DEBUG")
    after = _working_set_mb()
    elapsed_ms = (time.perf_counter() - started) * 1000
    if before is not None and after is not None:
        log(
            f"Окно скрыто: память {before:.0f} → {after:.0f} МБ "
            f"(объектов собрано: {collected}, {elapsed_ms:.0f} мс)",
            "INFO",
        )


class IdleMemoryTrimmer(QObject):
    """Следит за окном и чистит память, когда оно долго скрыто или свёрнуто."""

    def __init__(
        self,
        window,
        *,
        delay_ms: int = IDLE_TRIM_DELAY_MS,
        repeat_ms: int = IDLE_TRIM_REPEAT_MS,
        trim=trim_process_memory,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._delay_ms = int(delay_ms)
        self._repeat_ms = int(repeat_ms)
        self._trim = trim
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        window.installEventFilter(self)
        self._sync()

    def _window_is_idle(self) -> bool:
        window = self._window
        return not window.isVisible() or window.isMinimized()

    def _sync(self) -> None:
        if self._window_is_idle():
            if not self._timer.isActive():
                self._timer.start(self._delay_ms)
        else:
            self._timer.stop()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if obj is self._window and event.type() in _WATCHED_EVENTS:
            self._sync()
        return False

    def _on_timeout(self) -> None:
        if not self._window_is_idle():
            return
        try:
            self._trim()
        except Exception as exc:
            log(f"Ошибка очистки памяти скрытого окна: {exc}", "DEBUG")
        self._timer.start(self._repeat_ms)


def install_idle_memory_trim(window) -> IdleMemoryTrimmer:
    return IdleMemoryTrimmer(window)


__all__ = ["IdleMemoryTrimmer", "install_idle_memory_trim", "trim_process_memory"]
