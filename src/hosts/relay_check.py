"""Отвечает ли сервер-посредник профиля из сети пользователя.

Профиль в «Редакторе hosts» направляет имена сервиса на адрес посредника. Если
провайдер заблокировал этот адрес, запись в hosts не откроет сервис, а сломает
его: сайт перестанет открываться совсем. Поэтому перед записью программа
пробует соединиться с каждым посредником и не пишет строки, если он молчит.

Какие адреса считать посредниками, решает каталог (`get_relay_addresses`): это
адреса, через которые профиль ведёт много сервисов сразу. Угадывать посредник по
числу имён нельзя: настоящий адрес сайта тоже бывает общим для десятка имён и
при этом из России не отвечает — так ошибались версии 21.1.7.116–119.
"""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor

CONNECT_TIMEOUT_S = 3.0
RELAY_PORT = 443
# Одна потеря — ещё не блокировка: молчащий посредник пробуется второй раз.
RETRY_PAUSE_S = 0.5
# Контроль «интернет вообще есть»: российские адреса, которые не блокируют. Если молчат
# и они, вина не на провайдере, а на связи — винить посредника было бы неправдой.
CONTROL_ADDRESSES = ("77.88.8.8", "87.250.250.242")


def relay_addresses(rows: list[tuple[str, str]], known_relays: set[str]) -> list[str]:
    """Посредники среди адресов строк hosts, по порядку первого появления."""
    known = {str(address).strip().casefold() for address in known_relays}
    found: list[str] = []
    for _domain, ip in rows:
        address = str(ip or "").strip()
        if address and address.casefold() in known and address not in found:
            found.append(address)
    return found


def _connect_once(address: str) -> bool:
    try:
        with socket.create_connection((address, RELAY_PORT), timeout=CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


def _connects(address: str) -> bool:
    """Соединяется ли адрес: две попытки с паузой."""
    if _connect_once(address):
        return True
    time.sleep(RETRY_PAUSE_S)
    return _connect_once(address)


def internet_reachable() -> bool:
    """Есть ли связь с интернетом: отвечает хоть один контрольный адрес."""
    with ThreadPoolExecutor(max_workers=len(CONTROL_ADDRESSES), thread_name_prefix="hosts-control") as pool:
        return any(pool.map(_connect_once, CONTROL_ADDRESSES))


def unreachable_relays(rows: list[tuple[str, str]], known_relays: set[str], *, ipv6: bool = True) -> list[str]:
    """Посредники из строк hosts, с которыми не удалось соединиться.

    ``ipv6=False`` — у компьютера нет IPv6: такие посредники не проверяются и
    недоступными не считаются (запись IPv6 в hosts и так отфильтруется).
    """
    relays = [address for address in relay_addresses(rows, known_relays) if ipv6 or ":" not in address]
    if not relays:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(relays)), thread_name_prefix="hosts-relay") as pool:
        alive = list(pool.map(_connects, relays))
    return [address for address, ok in zip(relays, alive) if not ok]


__all__ = ["internet_reachable", "relay_addresses", "unreachable_relays"]
