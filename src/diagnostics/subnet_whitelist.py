"""Подтверждение режима «белых списков» по подсетям: какие сети вообще пропускают.

Признак по контрольным сайтам (``problems``: российские открываются, зарубежные
нет) держится на трёх-четырёх адресах. Его мог дать и обычный провайдер,
закрывший именно эти сайты. Здесь тот же вопрос задаётся десяткам сетей
(``subnet_catalog``): двум десяткам российских — банки, операторы, госсервисы,
которые в белых списках, — и полутора десяткам зарубежных хостингов и CDN.

Сеть «открыта», если хоть один её адрес ответил на TCP 443 — согласием или
отказом (RST): пакет дошёл. Молчание — закрыта. Закрытые спрашиваются ещё раз
после паузы: одна потеря — не вывод.

Сама по себе проверка вывод не делает: обычная линия, где РКН закрыл диапазоны
зарубежных хостингов, похожа на белый список. Она только подтверждает или
снимает уже найденный признак. Подход взят из hyperion-cs/dpi-checkers
(Apache-2.0).
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass

from diagnostics.subnet_catalog import REGION_FOREIGN, REGION_RU, SUBNET_GROUPS
from utils.socket_cancel import SocketCancel, close_quietly

__all__ = [
    "CODE_CONFIRMED",
    "CODE_REFUTED",
    "CODE_UNCLEAR",
    "SubnetFacts",
    "SubnetVerdict",
    "collect",
    "judge",
]

TIMEOUT_S = 3.0
RETRY_PAUSE_S = 1.0
# Белый список подтверждён: открыто не меньше этой доли российских сетей…
RU_OPEN_MIN = 0.6
# …и не больше этой доли зарубежных.
FOREIGN_OPEN_MAX = 0.1
# Зарубежных открыто столько — это точно не белый список.
FOREIGN_OPEN_REFUTES = 0.3

CODE_CONFIRMED = "confirmed"
CODE_REFUTED = "refuted"
CODE_UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class SubnetFacts:
    # (подпись сети, область, ответил ли хоть один адрес).
    groups: tuple[tuple[str, str, bool], ...] = ()
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class SubnetVerdict:
    code: str
    ru_open: int
    ru_total: int
    foreign_open: int
    foreign_total: int

    @property
    def text(self) -> str:
        return (
            f"из российских сетей отвечают {self.ru_open} из {self.ru_total}, "
            f"из зарубежных — {self.foreign_open} из {self.foreign_total}"
        )


def connect(ip: str, *, cancel: SocketCancel) -> bool:
    """Дошёл ли пакет до адреса: соединение или отказ (RST) — да, молчание и «нет дороги» — нет."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not cancel.track(sock):
            return False
        sock.settimeout(TIMEOUT_S)
        sock.connect((ip, 443))
        return True
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    finally:
        cancel.release(sock)
        close_quietly(sock)


def collect(*, submit: Callable, cancel: SocketCancel, groups=SUBNET_GROUPS) -> SubnetFacts:
    """Все сети разом (через ``submit`` — общий предел прогона); закрытые ещё раз после паузы."""

    def ask(chosen) -> list[bool]:
        futures = [[submit(connect, ip, cancel=cancel) for ip in ips] for _label, _region, ips in chosen]
        return [any(future.result() for future in group) for group in futures]

    opened = ask(groups)
    closed = [index for index, answered in enumerate(opened) if not answered]
    if closed and not cancel.cancelled and cancel.sleep(RETRY_PAUSE_S):
        for index, answered in zip(closed, ask([groups[index] for index in closed])):
            opened[index] = answered
    return SubnetFacts(
        groups=tuple((label, region, answered) for (label, region, _ips), answered in zip(groups, opened)),
        cancelled=cancel.cancelled,
    )


def judge(facts: SubnetFacts) -> SubnetVerdict | None:
    """Подтверждает белый список, снимает его или «неясно». None — проверку сняли или сетей нет."""
    if facts.cancelled:
        return None
    ru = [answered for _label, region, answered in facts.groups if region == REGION_RU]
    foreign = [answered for _label, region, answered in facts.groups if region == REGION_FOREIGN]
    if not ru or not foreign:
        return None
    ru_share = sum(ru) / len(ru)
    foreign_share = sum(foreign) / len(foreign)
    if ru_share >= RU_OPEN_MIN and foreign_share <= FOREIGN_OPEN_MAX:
        code = CODE_CONFIRMED
    elif foreign_share >= FOREIGN_OPEN_REFUTES:
        code = CODE_REFUTED
    else:
        code = CODE_UNCLEAR
    return SubnetVerdict(code, sum(ru), len(ru), sum(foreign), len(foreign))
