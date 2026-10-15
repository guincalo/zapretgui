from __future__ import annotations

"""Занят ли человек делом, которое обновление оборвало бы.

Обновление закрывает программу и на несколько секунд останавливает Zapret.
Посреди игры на весь экран это обрыв связи, посреди проверки сети или
подбора стратегии — потерянный результат. Поэтому программа, которая
обновляется сама, сначала спрашивает здесь, удобно ли сейчас.

На обновление по кнопке это не влияет: человек сам решил, что ему удобно.

Модуль не импортирует Qt: его зовут и из потока слушателя выпусков.
"""

from zapret_update import screen

BUSY_FULLSCREEN = "fullscreen"

# Что на экране — общее ядро (одно правило с Zapret KVN).
FOREGROUND_FULL = screen.FOREGROUND_FULL
FOREGROUND_BARE = screen.FOREGROUND_BARE
FOREGROUND_PART = screen.FOREGROUND_PART
FOREGROUND_SHELL = screen.FOREGROUND_SHELL
FOREGROUND_OWN = screen.FOREGROUND_OWN
is_fullscreen = screen.is_fullscreen
screen_state = screen.screen_state
start_sampler = screen.start_sampler


def fullscreen_app_active() -> bool:
    """На экране игра, видео или презентация во весь экран — сейчас или недавно."""
    return screen.fullscreen_active()


def busy_reason() -> str:
    """Чем занят человек: название дела либо пустая строка — можно обновляться."""
    from core.runtime.long_tasks import active_long_tasks

    tasks = active_long_tasks()
    if tasks:
        return tasks[0]
    if fullscreen_app_active():
        return BUSY_FULLSCREEN
    return ""


ACTIVITY_WINDOW = "window"
ACTIVITY_TRAY = "tray"


def activity() -> str:
    """Чем занята программа: дело, которое нельзя обрывать, либо «окно открыто» / «в трее».

    Это слово уходит серверу вместе с вопросом о новой версии: на сайте
    показан общий счёт, чем заняты программы. Пустая строка — неизвестно.
    """
    from core.runtime.presence import window_shown

    reason = busy_reason()
    if reason:
        return reason
    shown = window_shown()
    if shown is None:
        return ""
    return ACTIVITY_WINDOW if shown else ACTIVITY_TRAY


__all__ = [
    "ACTIVITY_TRAY",
    "ACTIVITY_WINDOW",
    "BUSY_FULLSCREEN",
    "activity",
    "busy_reason",
    "fullscreen_app_active",
    "is_fullscreen",
    "screen_state",
    "start_sampler",
]
