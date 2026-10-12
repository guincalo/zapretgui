"""Общая кнопка «Стоп» для сетевых запросов из разных потоков.

Блокирующий вызов сокета нельзя прервать снаружи иначе, чем закрыв сам
сокет. Каждый запрос отдаёт свой сокет в ``SocketCancel.track``; ``cancel``
закрывает их все из другого потока, и ожидающие вызовы сразу возвращаются с
ошибкой. Запрос отличает отмену от настоящего сбоя по ``cancelled``.
"""

from __future__ import annotations

import socket
import threading

__all__ = ["SocketCancel", "close_quietly"]


def close_quietly(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class SocketCancel:
    """Набор открытых сокетов одной пачки запросов и признак отмены."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sockets: set[socket.socket] = set()
        self._cancelled = False
        # То же «Стоп», но его можно ждать: паузы между пробами снимаются сразу.
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            sockets = list(self._sockets)
            self._sockets.clear()
        self._event.set()
        for sock in sockets:
            close_quietly(sock)

    def sleep(self, seconds: float) -> bool:
        """Пауза, которую «Стоп» обрывает сразу. False — проверку сняли."""
        return not self._event.wait(max(0.0, float(seconds)))

    def track(self, sock: socket.socket) -> bool:
        """Берёт сокет под отмену. False — отмена уже была, запрос начинать нельзя."""
        with self._lock:
            if self._cancelled:
                return False
            self._sockets.add(sock)
            return True

    def release(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(sock)
