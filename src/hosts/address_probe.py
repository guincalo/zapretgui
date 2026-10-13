"""Жив ли адрес и отдаёт ли он нужный сервис — пробы для умного движка hosts.

Одного «порт открылся» мало в обе стороны (замеры 2026-10-13 со стенда и из
российских сетей): посредник может принять соединение и молчать на
приветствие, а настоящий сервер — жить, пока его имя режет фильтр. Поэтому
проба идёт лесенкой:

1. соединение с портом 443 — отказ или двойное молчание значит «мёртв»;
2. шифрованное приветствие с проверкой сертификата. Чужой сертификат —
   адрес занят не тем сервисом («мёртв» для нас). Молчание здесь ничего не
   доказывает: без обхода фильтр глушит само имя сайта, адрес при этом жив;
3. для сервисов, у которых есть «нейтральное имя» владельца (у Google это
   www.google.com), приветствие идёт с ним, а имя сервиса называется уже
   внутри шифрованного соединения. Сервер Google отвечает на такое
   страницей YouTube, а чужой адрес — отказом. Так проверка не зависит от
   того, запущен ли обход.

Вывод «мёртв» делается только по прямому признаку; всё неясное — «не узнали»,
и такой адрес движок не выбрасывает.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from dataclasses import dataclass

__all__ = ["ALIVE", "DEAD", "UNKNOWN", "Probe", "check_address"]

ALIVE = "alive"
DEAD = "dead"
UNKNOWN = "unknown"

PORT = 443
CONNECT_TIMEOUT_S = 2.5
HELLO_TIMEOUT_S = 3.0
# Одна потеря — ещё не смерть: молчащий адрес пробуется второй раз.
RETRY_PAUSE_S = 0.5


@dataclass(frozen=True, slots=True)
class Probe:
    verdict: str
    # За сколько адрес прошёл всю пробу.
    ms: float | None = None
    reason: str = ""
    # За сколько установилось соединение — то же, что показывает «пинг»: по этому числу
    # движок сравнивает живые адреса. Вся проба для сравнения не годится: у адреса с
    # нейтральным именем в неё входит ещё и запрос страницы.
    connect_ms: float | None = None


_context_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def _verified_context() -> ssl.SSLContext:
    """Один на всех клиент с проверкой сертификата: в Windows его создание читает хранилище сертификатов."""
    global _context
    with _context_lock:
        if _context is None:
            _context = ssl.create_default_context()
        return _context


def _connect(ip: str) -> tuple[socket.socket | None, str]:
    """(сокет, исход): ok / refused / timeout / error."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_S)
    try:
        sock.connect((ip, PORT))
    except (socket.timeout, TimeoutError):
        sock.close()
        return None, "timeout"
    except ConnectionRefusedError:
        sock.close()
        return None, "refused"
    except OSError:
        sock.close()
        return None, "error"
    return sock, "ok"


def _base(name: str) -> str:
    """Две последние части имени: по ним «www.youtube.com» и «youtube.com» — одно семейство."""
    return ".".join(name.strip(".").lower().split(".")[-2:])


def _serves(host: str, head: bytes) -> bool:
    """Ответ сервера на запрос с именем сервиса: страница или переход внутри того же семейства имён."""
    lines = head.decode("latin-1", "replace").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2 or not parts[0].startswith("HTTP/") or not parts[1].isdigit():
        return False
    status = int(parts[1])
    if 200 <= status < 300:
        return True
    if not 300 <= status < 400:
        return False
    location = next((line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("location:")), "")
    if not location.lower().startswith(("http://", "https://")):
        # Переход без полного адреса остаётся на том же имени.
        return True
    target = location.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return _base(target) == _base(host)


def check_address(ip: str, host: str, *, front: str = "") -> Probe:
    """Проба одного адреса для имени ``host``. ``front`` — нейтральное имя владельца, если оно известно."""
    started = time.perf_counter()
    sock, outcome = _connect(ip)
    if sock is None and outcome != "refused":
        time.sleep(RETRY_PAUSE_S)
        started = time.perf_counter()
        sock, outcome = _connect(ip)
    if sock is None:
        return Probe(DEAD, reason="порт закрыт" if outcome == "refused" else "не отвечает")
    connect_ms = (time.perf_counter() - started) * 1000.0
    hello_name = front or host
    try:
        sock.settimeout(HELLO_TIMEOUT_S)
        with _verified_context().wrap_socket(sock, server_hostname=hello_name) as tls:
            if not front:
                # Сертификат проверен для самого имени сервиса: адрес его и отдаёт.
                return Probe(ALIVE, (time.perf_counter() - started) * 1000.0, connect_ms=connect_ms)
            tls.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii", "ignore"))
            head = b""
            while b"\r\n\r\n" not in head and len(head) < 16384:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                head += chunk
            if _serves(host, head):
                return Probe(ALIVE, (time.perf_counter() - started) * 1000.0, connect_ms=connect_ms)
            return Probe(DEAD, reason="адрес занят другим сервисом")
    except ssl.SSLCertVerificationError:
        return Probe(DEAD, reason="чужой сертификат")
    except (socket.timeout, TimeoutError, ssl.SSLError, OSError):
        # Соединение есть, а приветствие не прошло: так выглядит и фильтр по имени, и молчащий посредник.
        return Probe(UNKNOWN, reason="на приветствие нет ответа")
    finally:
        try:
            sock.close()
        except OSError:
            pass
