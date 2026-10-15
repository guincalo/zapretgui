"""Подписки снимаются по квитанции connect(), а не по обработчику.

В сборке Nuitka метод объекта при каждом обращении — новый объект, PyQt его
не узнаёт, и ``signal.disconnect(self.on_x)`` молча оставлял обработчик.
Здесь это изображает обработчик, который каждый раз новый: квитанция
снимает подписку и тогда.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal

from utils.qt_signal_links import SignalLinks, disconnect_link


class _Source(QObject):
    fired = pyqtSignal()


class QtSignalLinksTests(unittest.TestCase):
    def test_link_removes_handler_that_cannot_be_recognized_again(self) -> None:
        source = _Source()
        calls = []
        link = source.fired.connect(lambda: calls.append(1))
        # Обработчик «по имени» снять нечем: тот же код — уже другой объект.
        with self.assertRaises(TypeError):
            source.fired.disconnect(lambda: calls.append(1))

        disconnect_link(link)
        source.fired.emit()

        self.assertEqual(calls, [])
        self.assertEqual(source.receivers(source.fired), 0)

    def test_empty_or_repeated_link_is_not_an_error(self) -> None:
        source = _Source()
        link = source.fired.connect(lambda: None)
        disconnect_link(link)
        disconnect_link(link)
        disconnect_link(None)
        self.assertEqual(source.receivers(source.fired), 0)

    def test_group_removes_only_its_own_links(self) -> None:
        source = _Source()
        calls = []
        links = SignalLinks()
        links.connect(source.fired, lambda: calls.append("mine"))
        links.connect(source.fired, lambda: calls.append("mine too"))
        source.fired.connect(lambda: calls.append("someone else"))

        links.disconnect_all()
        source.fired.emit()

        self.assertEqual(calls, ["someone else"])
        self.assertEqual(len(links), 0)


if __name__ == "__main__":
    unittest.main()
