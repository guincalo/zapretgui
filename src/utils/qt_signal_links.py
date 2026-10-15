"""Подписки на сигналы Qt, которые потом снимаются.

Снимать подписку по самому обработчику (``signal.disconnect(self.on_x)``)
нельзя: в сборке Nuitka метод объекта при каждом обращении — новый объект
другого типа (``compiled_method``), PyQt его не узнаёт, отписка падает с
«is not connected», а обработчик остаётся. Ошибку обычно глотают, и в сборке
обработчики копятся, хотя из исходников всё работает. Так стрелка «назад»
делала два шага сразу и стояла на месте (15.10.2026; проверено на win10:
из исходников 1 обработчик, из собранного модуля — 2).

Поэтому подписка снимается по своей «квитанции» — объекту соединения,
который возвращает ``connect()``. Отписку по обработчику запрещает проверка
``app.architecture_checks``; снять ВСЕ подписки сигнала (``disconnect()``
без аргументов) можно — так делают со своими или с чужими сигналами, когда
владелец подписки — библиотека.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject


def disconnect_link(link) -> None:
    """Снимает подписку по её квитанции; пустая или уже снятая — не ошибка."""
    if link is None:
        return
    try:
        QObject.disconnect(link)
    except (TypeError, RuntimeError):
        # Объект сигнала уже удалён: вместе с ним ушла и подписка.
        pass


class SignalLinks:
    """Набор подписок одного владельца, которые снимаются вместе."""

    def __init__(self) -> None:
        self._links: list = []

    def connect(self, signal, slot):
        link = signal.connect(slot)
        self._links.append(link)
        return link

    def disconnect_all(self) -> None:
        links, self._links = self._links, []
        for link in links:
            disconnect_link(link)

    def __len__(self) -> int:
        return len(self._links)


__all__ = ["SignalLinks", "disconnect_link"]
