"""Парный запуск BlockCheck: проверка с обходом и без него одной кнопкой.

Сравнение «С Zapret и без» (``diagnostics.compare``) строится по двум проверкам в
противоположном состоянии обхода. Раньше пользователь делал их сам: проверял, выключал
Zapret, проверял снова и не забывал включить обратно. Здесь это порядок действий без
окон: первая проверка идёт как есть, затем обход переключается в противоположное
состояние, идёт вторая, и обход возвращается, как был.

Модуль ничего не знает про экран: ему дают три действия (узнать, работает ли обход,
запустить его и остановить), а страница ведёт кнопки, таймер ожидания и статусы.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["PHASE_FIRST", "PHASE_IDLE", "PHASE_SECOND", "PHASE_SWITCHING", "PairedRun"]

PHASE_IDLE = "idle"
PHASE_FIRST = "first"
PHASE_SWITCHING = "switching"
PHASE_SECOND = "second"

# Сколько ждать, пока обход действительно сменит состояние, и пауза после этого, чтобы перехват встал или ушёл.
SETTLE_WAIT_SECONDS = 30.0
SETTLE_PAUSE_SECONDS = 1.5


class PairedRun:
    def __init__(self, *, is_running: Callable[[], bool], start: Callable[[], object], stop: Callable[[], object]) -> None:
        self._is_running = is_running
        self._start = start
        self._stop = stop
        self.phase = PHASE_IDLE
        # Работал ли обход до парного запуска и переключали ли мы его.
        self.was_running = False
        self.flipped = False

    @property
    def active(self) -> bool:
        return self.phase != PHASE_IDLE

    @property
    def first_label(self) -> str:
        """В каком состоянии идёт первая проверка."""
        return "с обходом" if self.was_running else "без обхода"

    @property
    def second_label(self) -> str:
        return "без обхода" if self.was_running else "с обходом"

    def begin(self) -> None:
        """Запоминает исходное состояние обхода; первая проверка идёт в нём."""
        self.was_running = bool(self._is_running())
        self.flipped = False
        self.phase = PHASE_FIRST

    def switch(self) -> str:
        """Переключает обход в противоположное состояние. Пустая строка — команда дана; иначе причина отказа."""
        self.phase = PHASE_SWITCHING
        try:
            done = self._stop() if self.was_running else self._start()
        except Exception as error:  # переключение — действие пользователя, его сбой не должен ронять страницу
            self.finish()
            return str(error) or "не удалось переключить Zapret"
        self.flipped = done is not False
        if done is False:
            self.finish()
            return "Zapret не выключился" if self.was_running else "Zapret не запустился: проверьте способ запуска в «Управлении Zapret 2»"
        return ""

    def settled(self) -> bool:
        """Обход перешёл в противоположное состояние."""
        return bool(self._is_running()) != self.was_running

    def second_started(self) -> None:
        self.phase = PHASE_SECOND

    def finish(self) -> str:
        """Возвращает обход в исходное состояние и заканчивает парный запуск. Пустая строка — всё в порядке."""
        problem = ""
        if self.flipped:
            try:
                restored = self._start() if self.was_running else self._stop()
                if restored is False:
                    problem = "Zapret не вернулся в прежнее состояние: проверьте его на странице «Управление Zapret 2»"
            except Exception as error:
                problem = str(error) or "Zapret не вернулся в прежнее состояние"
        self.flipped = False
        self.phase = PHASE_IDLE
        return problem
