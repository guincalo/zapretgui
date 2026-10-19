"""Очередь тяжёлых задач GUI-потока, которые ждут паузы пользователя.

Скрытую страницу нельзя собрать в фоновом потоке: виджеты Qt создаются только
в GUI-потоке, и на это время интерфейс замирает (30–120 мс на быстром
компьютере, в разы больше на обычном). Раньше такие сборки срабатывали по
таймеру — через 1, 2, 3, 5, 6 и 7 секунд после запуска, — то есть ровно тогда,
когда человек уже водит мышью и нажимает кнопки. Замер рывков показал 15
задержек кадра за первые десять секунд, большая часть — эти сборки.

Очередь выполняет те же задачи, но:

* по одной и с паузой между ними, чтобы окно успело перерисоваться;
* только когда пользователь не трогает мышь и клавиатуру;
* чем дольше длилась прошлая задача, тем длиннее пауза нужна для следующей —
  на медленном компьютере очередь сама становится осторожнее;
* только когда молчит фон: общая дорожка фоновых задач запуска
  (main.post_startup_threading) пуста и обход не запускается. Сборка страницы
  тысячи раз отпускает общий замок Python (каждый addWidget, каждое
  подключение сигнала) и рядом с занятым фоновым потоком идёт в полтора-четыре
  раза дольше (72 мс → 113 мс рядом с одним, 254 мс рядом с тремя; замер на
  win10), а окно на это время замирает. Пока страница собирается, дорожка в
  свою очередь не начинает следующую задачу;
* пока окно свёрнуто или убрано в трей, страницы «про запас» ждут: память и
  процессор при входе в Windows нужны другим. Но запуск в трей не должен
  означать, что первое открытие окна построит всё разом (в журнале 21.1.7.183:
  первый показ после трея — 5,6 с замирания, шесть страниц по 100–500 мс), и
  код, который ни разу не выполнялся, никакое удержание памяти не сохранит.
  Поэтому через HIDDEN_WARM_AFTER_S после старта, когда человек за компьютером
  давно ничего не делает (HIDDEN_WARM_IDLE_MS), такие страницы собираются и при
  скрытом окне — по одной, с паузой HIDDEN_WARM_GAP_MS между ними. Это
  решение пользователя от 2026-10-20 (цена: около 50–80 МБ памяти в трее).

Страница, которую пользователь открыл раньше очереди, строится как обычно —
по клику; задача очереди для неё превращается в пустую.

Задачи бывают двух видов. Обычная нужна сама по себе (окно «Что нового»,
страница с окном обновления) и ждёт только короткой паузы. Сборка страницы
«про запас» (speculative=True) никому не обещана: её цена — заминка окна на
50–120 мс, и человек, который смотрит на программу, видит её как рывок
анимации. Поэтому про запас страницы собираются, только когда этого некому
заметить: человек работает в другой программе или давно не трогает мышь и
клавиатуру. Замер на win10: шесть таких сборок в первые восемь секунд после
запуска давали шесть рывков по 46–122 мс.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QCoreApplication, QObject, Qt, QTimer
from PyQt6.QtGui import QGuiApplication

from core.runtime.presence import return_settle_remaining_ms
from log.log import log
from main import startup_audit
from main.post_startup_threading import gui_build_turn, is_local_lane_busy
from ui.user_idle import user_idle_ms


# Сколько пользователь должен ничего не делать, чтобы задача началась.
IDLE_REQUIRED_MS = 600
# После долгой задачи пауза нужна длиннее: во столько раз больше её длительности.
IDLE_AFTER_TASK_FACTOR = 4
IDLE_REQUIRED_MAX_MS = 3_000
# Перерыв между задачами: окно успевает перерисоваться и принять ввод.
TASK_GAP_MS = 400
# Как часто проверять, не наступила ли пауза.
BUSY_POLL_MS = 200
# Пока окно скрыто, спешить некуда — проверяем редко, чтобы не будить процесс.
HIDDEN_POLL_MS = 2_000
# Скрытое окно: страницы про запас собираются не раньше чем через столько после
# старта, только когда человек не трогал мышь и клавиатуру столько же, и с такой
# паузой между сборками (чтобы не занимать диск и процессор в трее надолго).
HIDDEN_WARM_AFTER_S = 120.0
HIDDEN_WARM_IDLE_MS = 60_000
HIDDEN_WARM_GAP_MS = 3_000
# Сколько человек должен не трогать мышь и клавиатуру, чтобы страницу можно
# было собрать про запас при открытом и активном окне: он, скорее всего, отошёл.
SPECULATIVE_IDLE_MS = 20_000
# Как часто проверять, не отошёл ли человек.
SPECULATIVE_POLL_MS = 1_000
# Дольше этого задача занятый фон не ждёт: через очередь идут и окна «Что
# нового» и обновления, и зависшая фоновая задача не должна спрятать их навсегда.
BACKGROUND_WAIT_MAX_MS = 20_000


@dataclass(slots=True)
class _IdleTask:
    ready_at: float
    order: int
    name: str
    callback: Callable[[], None]
    needs_shown_window: bool
    speculative: bool


class IdleUiTaskQueue(QObject):
    """Выполняет задачи GUI-потока по одной и только в паузах пользователя."""

    def __init__(
        self,
        *,
        is_alive: Callable[[], bool],
        is_window_shown: Callable[[], bool],
        idle_ms: Callable[[], int | None] = user_idle_ms,
        is_background_busy: Callable[[], bool] = is_local_lane_busy,
        is_app_active: Callable[[], bool] | None = None,
        mouse_pressed: Callable[[], bool] | None = None,
        return_settle_ms: Callable[[], int] = return_settle_remaining_ms,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_alive = is_alive
        self._is_window_shown = is_window_shown
        self._idle_ms = idle_ms
        self._is_background_busy = is_background_busy
        self._is_app_active = is_app_active or _is_app_active
        self._mouse_pressed = mouse_pressed or _is_mouse_pressed
        self._return_settle_ms = return_settle_ms
        self._clock = clock
        self._started_at = clock()
        self._tasks: list[_IdleTask] = []
        self._order = 0
        self._last_task_ms = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

    # ── public ───────────────────────────────────────────────────────────────

    def add(
        self,
        name: str,
        callback: Callable[[], None],
        *,
        delay_ms: int = 0,
        needs_shown_window: bool = True,
        speculative: bool = False,
    ) -> None:
        """Ставит задачу. Раньше delay_ms она не начнётся.

        needs_shown_window=False — задача нужна и при окне в трее (например,
        подготовка окна обновления); паузы пользователя она всё равно ждёт.
        speculative=True — работа про запас (сборка страницы, которую ещё не
        открывали): выполняется, только когда заминку окна некому заметить.
        """
        delay = max(0, int(delay_ms))
        startup_audit.audit_timer_queued(str(name), delay)
        self._order += 1
        self._tasks.append(
            _IdleTask(
                ready_at=self._clock() + delay / 1000.0,
                order=self._order,
                name=str(name),
                callback=callback,
                needs_shown_window=bool(needs_shown_window),
                speculative=bool(speculative),
            )
        )
        self._tasks.sort(key=lambda task: (task.ready_at, task.order))
        self._arm(self._ms_until(self._tasks[0].ready_at))

    def pending_names(self) -> tuple[str, ...]:
        return tuple(task.name for task in self._tasks)

    def required_idle_ms(self) -> int:
        """Нужная пауза пользователя с учётом длительности прошлой задачи."""
        scaled = int(self._last_task_ms * IDLE_AFTER_TASK_FACTOR)
        return max(IDLE_REQUIRED_MS, min(IDLE_REQUIRED_MAX_MS, scaled))

    def stop(self) -> None:
        self._timer.stop()
        self._tasks.clear()

    # ── scheduling ───────────────────────────────────────────────────────────

    def _ms_until(self, moment: float) -> int:
        return max(0, int(round((moment - self._clock()) * 1000.0)))

    def _arm(self, delay_ms: int) -> None:
        self._timer.start(max(0, int(delay_ms)))

    def _on_timer(self) -> None:
        if not self._tasks:
            return
        if not self._safe(self._is_alive, default=False):
            self.stop()
            return

        # Выполняется первая по очереди задача, которой сейчас можно: задача
        # про запас, ждущая ухода человека, не держит окно «Что нового».
        next_check_ms: int | None = None
        for index, task in enumerate(self._tasks):
            wait_ms = self._ms_until(task.ready_at)
            if wait_ms > 0:
                # Список отсортирован по времени: дальше все ещё не готовы.
                next_check_ms = wait_ms if next_check_ms is None else min(next_check_ms, wait_ms)
                break
            retry_ms = self._retry_delay_ms(task)
            if retry_ms > 0:
                next_check_ms = retry_ms if next_check_ms is None else min(next_check_ms, retry_ms)
                continue
            self._tasks.pop(index)
            self._run(task)
            if self._tasks:
                hidden = not self._safe(self._is_window_shown, default=True)
                gap_ms = HIDDEN_WARM_GAP_MS if hidden else TASK_GAP_MS
                self._arm(max(gap_ms, self._ms_until(self._tasks[0].ready_at)))
            return
        if next_check_ms is not None:
            self._arm(next_check_ms)

    def _retry_delay_ms(self, task: _IdleTask) -> int:
        """0 — задачу можно выполнять сейчас, иначе через сколько проверить снова."""
        waited_ms = (self._clock() - task.ready_at) * 1000.0
        if waited_ms < BACKGROUND_WAIT_MAX_MS and self._safe(self._is_background_busy, default=False):
            # Рядом с занятым фоновым потоком сборка идёт в разы дольше, и
            # окно замирает на всё это время — даже скрытое окно задержало
            # бы тогда сам запуск.
            return BUSY_POLL_MS
        shown = self._safe(self._is_window_shown, default=True)
        if not shown:
            if not task.needs_shown_window:
                # Скрытое окно никто не видит: рывок незаметен, ждать паузы незачем.
                return 0
            if task.speculative and self._hidden_warm_allowed():
                return 0
            return HIDDEN_POLL_MS
        # Окно только что вернулось из долгого фона: первые секунды оно и так
        # тянет память с диска и строит кадр — сборки «про запас» и прочие
        # отложенные задачи делят с ним диск и замок Python. Раньше они начинались
        # сразу и шли подряд (в журнале пользователя шесть страниц по 100–500 мс
        # в первые секунды после показа: «жёстко лагает после открытия»).
        settle_ms = int(self._safe(self._return_settle_ms, default=0) or 0)
        if settle_ms > 0:
            return max(BUSY_POLL_MS, settle_ms)
        if self._safe(self._mouse_pressed, default=False):
            return BUSY_POLL_MS
        if not self._safe(self._is_app_active, default=True):
            # Окно видно, но человек работает в другой программе.
            return 0
        idle = self._safe(self._idle_ms, default=None)
        if idle is None:
            return 0
        if task.speculative:
            # Человек смотрит на окно: заминку он увидит как рывок анимации.
            # Страница соберётся по щелчку или когда он отойдёт.
            return 0 if int(idle) >= SPECULATIVE_IDLE_MS else SPECULATIVE_POLL_MS
        return 0 if int(idle) >= self.required_idle_ms() else BUSY_POLL_MS

    def _hidden_warm_allowed(self) -> bool:
        """Можно ли собрать страницу про запас при скрытом окне: давно после старта и человек давно ушёл."""
        if self._clock() - self._started_at < HIDDEN_WARM_AFTER_S:
            return False
        idle = self._safe(self._idle_ms, default=None)
        return idle is not None and int(idle) >= HIDDEN_WARM_IDLE_MS

    def _run(self, task: _IdleTask) -> None:
        startup_audit.audit_timer_fired(task.name, 0)
        started_at = time.perf_counter()
        try:
            with gui_build_turn():
                task.callback()
        except Exception as exc:
            log(f"Отложенная задача интерфейса {task.name} не выполнена: {exc}", "DEBUG")
        self._last_task_ms = (time.perf_counter() - started_at) * 1000.0

    @staticmethod
    def _safe(fn, *, default):
        try:
            return fn()
        except Exception:
            return default


def _is_app_active() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return True
    return app.applicationState() == Qt.ApplicationState.ApplicationActive


def _is_mouse_pressed() -> bool:
    return QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton


def build_idle_ui_task_queue(startup_host, *, is_launch_busy: Callable[[], bool] | None = None) -> IdleUiTaskQueue:
    """is_launch_busy — обход сейчас запускается или останавливается."""

    def _is_background_busy() -> bool:
        if is_local_lane_busy():
            return True
        return bool(is_launch_busy()) if is_launch_busy is not None else False

    # Родитель — приложение: очередь живёт, пока крутится цикл событий, а не
    # пока на неё случайно ссылается чьё-то замыкание.
    return IdleUiTaskQueue(
        is_alive=startup_host.is_alive,
        is_window_shown=startup_host.is_window_shown,
        is_background_busy=_is_background_busy,
        parent=QCoreApplication.instance(),
    )


__all__ = [
    "BACKGROUND_WAIT_MAX_MS",
    "BUSY_POLL_MS",
    "HIDDEN_POLL_MS",
    "HIDDEN_WARM_AFTER_S",
    "HIDDEN_WARM_GAP_MS",
    "HIDDEN_WARM_IDLE_MS",
    "IDLE_AFTER_TASK_FACTOR",
    "IDLE_REQUIRED_MAX_MS",
    "IDLE_REQUIRED_MS",
    "IdleUiTaskQueue",
    "SPECULATIVE_IDLE_MS",
    "SPECULATIVE_POLL_MS",
    "TASK_GAP_MS",
    "build_idle_ui_task_queue",
]
