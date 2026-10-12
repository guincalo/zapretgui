"""Виды сетевого сбоя — один словарь для всех проверок сети.

Проверка, которой не удалось соединиться или получить ответ, записывает не
текст ошибки, а её вид: «сервер молчит», «порт закрыт», «соединение оборвано»
и т. д. Выводы опираются только на вид — текст ошибки на Windows зависит от
языка системы и версии Python, а вид нет.

Вид определяется по исключению (``classify``). Сначала смотрится номер ошибки
Windows (``winerror``), потом номер ошибки системы (``errno``), потом класс
исключения: Python переводит в свои классы не все коды Windows, а текст
сообщения для разбора не годится вовсе. Так же устроен словарь ошибок OONI
(``df-007-errors``), откуда и взяты сами виды.
"""

from __future__ import annotations

import errno
import socket
import ssl

__all__ = [
    "FAILURE_CANCELLED",
    "FAILURE_CERT",
    "FAILURE_CLOSED",
    "FAILURE_HTTP",
    "FAILURE_MALFORMED",
    "FAILURE_OTHER",
    "FAILURE_REFUSED",
    "FAILURE_RESET",
    "FAILURE_TIMEOUT",
    "FAILURE_TLS",
    "FAILURE_UNREACHABLE",
    "classify",
]

FAILURE_TIMEOUT = "timeout"          # сервер молчит, пакеты пропадают
FAILURE_REFUSED = "refused"          # на этом порту никто не слушает
FAILURE_RESET = "reset"              # соединение оборвано по пути или сервером
FAILURE_CLOSED = "closed"            # сервер закрыл соединение, не ответив
FAILURE_UNREACHABLE = "unreachable"  # до адреса нет дороги
FAILURE_TLS = "tls"                  # шифрование не установилось
FAILURE_CERT = "cert"                # сервер предъявил чужой сертификат
FAILURE_HTTP = "http"                # сервер ответил ошибкой HTTP
FAILURE_MALFORMED = "malformed"      # пришло не то, что должно
FAILURE_CANCELLED = "cancelled"      # проверку остановили
FAILURE_OTHER = "other"

# Коды Winsock. Python переводит в TimeoutError/ConnectionResetError не все из них.
_WINERROR = {
    10060: FAILURE_TIMEOUT,      # WSAETIMEDOUT
    10061: FAILURE_REFUSED,      # WSAECONNREFUSED
    10054: FAILURE_RESET,        # WSAECONNRESET (у UDP — ICMP «порт закрыт»)
    10053: FAILURE_RESET,        # WSAECONNABORTED
    10050: FAILURE_UNREACHABLE,  # WSAENETDOWN
    10051: FAILURE_UNREACHABLE,  # WSAENETUNREACH
    10065: FAILURE_UNREACHABLE,  # WSAEHOSTUNREACH
}
_ERRNO = {
    errno.ETIMEDOUT: FAILURE_TIMEOUT,
    errno.ECONNREFUSED: FAILURE_REFUSED,
    errno.ECONNRESET: FAILURE_RESET,
    errno.ECONNABORTED: FAILURE_RESET,
    errno.EPIPE: FAILURE_RESET,
    errno.ENETDOWN: FAILURE_UNREACHABLE,
    errno.ENETUNREACH: FAILURE_UNREACHABLE,
    errno.EHOSTUNREACH: FAILURE_UNREACHABLE,
}


def classify(exc: BaseException) -> str:
    """Вид сбоя по исключению. Частные случаи идут раньше общих."""
    # Сертификат и разрыв посреди рукопожатия — тоже SSLError, поэтому раньше неё.
    if isinstance(exc, ssl.SSLCertVerificationError):
        return FAILURE_CERT
    if isinstance(exc, (ssl.SSLEOFError, ssl.SSLZeroReturnError)):
        # Соединение закрыли посреди рукопожатия: это не «шифры не сошлись».
        return FAILURE_CLOSED
    if isinstance(exc, ssl.SSLError):
        return FAILURE_TLS
    if isinstance(exc, OSError):
        by_code = _WINERROR.get(getattr(exc, "winerror", None)) or _ERRNO.get(exc.errno)
        if by_code:
            return by_code
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return FAILURE_TIMEOUT
    if isinstance(exc, ConnectionRefusedError):
        return FAILURE_REFUSED
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return FAILURE_RESET
    return FAILURE_OTHER
