"""Самопроверка компьютера: не принимает ли что-то на нём все соединения подряд.

Адрес 192.0.2.1 зарезервирован для документации (RFC 5737): в интернете его
нет, и соединение с ним честно не устанавливается — пакеты пропадают или
приходит «сети нет». Если же соединение «установилось», ответил не интернет, а
программа на этом компьютере: антивирус с проверкой HTTPS, прозрачный прокси,
VPN в режиме fake-ip. Тогда и остальные пробы видят её, а не сеть, и выводы о
блокировках ненадёжны. Так же перед проверкой делает ByeByeVPN (preflight).

Тревога — только при установившемся соединении. Молчание и отказ — норма:
по ним ничего не утверждается.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from diagnostics.system_state import LEVEL_WARN, SystemItem
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = ["DOCUMENTATION_ADDRESS", "SelfFacts", "collect", "judge"]

DOCUMENTATION_ADDRESS = "192.0.2.1"
PORT = 443
TIMEOUT_S = 1.5


@dataclass(frozen=True, slots=True)
class SelfFacts:
    # Соединение с несуществующим адресом установилось. None — проверку сняли.
    accepted: bool | None


def collect(*, cancel: SocketCancel | None = None) -> SelfFacts:
    token = cancel or SocketCancel()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not token.track(sock):
            return SelfFacts(None)
        sock.settimeout(TIMEOUT_S)
        sock.connect((DOCUMENTATION_ADDRESS, PORT))
        return SelfFacts(True)
    except OSError:
        return SelfFacts(None if token.cancelled else False)
    finally:
        token.release(sock)
        close_quietly(sock)


def judge(facts: SelfFacts) -> SystemItem | None:
    """Строка раздела «Компьютер». None — всё честно или проверку сняли."""
    if not facts.accepted:
        return None
    return SystemItem(
        "self_check",
        "Перехват соединений",
        LEVEL_WARN,
        f"соединение с адресом, которого нет в интернете ({DOCUMENTATION_ADDRESS}), «установилось» — его "
        "перехватывает программа на этом компьютере: антивирус с проверкой HTTPS, прокси или VPN. "
        "Выводы о блокировках могут быть неверны",
        "Отключите такую программу на время проверки и повторите её.",
    )
