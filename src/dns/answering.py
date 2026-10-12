"""Кто на самом деле отвечает на DNS-запросы компьютера — для панели «Сейчас DNS».

На адаптере может стоять 1.1.1.1, а отвечать — провайдер: он перехватывает
обычные DNS-запросы по пути. Проверка из трёх вопросов:

1. выбранному серверу — служебное имя ``whoami.akamai.net``: в ответе адрес,
   с которого к Akamai пришёл запрос, то есть сеть того, кто на самом деле
   разбирает имена;
2. чья это сеть — по DNS-службе Team Cymru, но шифрованным путём через эталоны:
   иначе о владельце ответил бы тот же перехватчик;
3. канарейка — вопрос адресу, где DNS-сервера нет: ответ на него доказывает
   перехват (``utils.dns_interception``).

Жёсткий вывод «перехват» делается только по канарейке. Несовпадение сети с
выбранным сервером фактом не подаётся: у anycast-сервисов оно бывает и без
перехвата.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from dns.server_check import UDP_TIMEOUT_S, WHOAMI_DOMAIN
from utils.dns_interception import canary_answered
from utils.dns_reference import reference_ask
from utils.dns_wire import TYPE_A, query_udp
from utils.ip_owner import IpOwner, lookup_ip_owner
from utils.socket_cancel import SocketCancel

__all__ = ["BUDGET_S", "Answering", "check_answering", "short_owner"]

# Общий срок проверки: эталоны бывают закрыты и молчат подолгу.
BUDGET_S = 8.0


@dataclass(frozen=True, slots=True)
class Answering:
    # Короткое имя сети, которая разбирает имена; пусто — не узнали.
    owner: str = ""
    asn: str = ""
    # Ответил адрес, где DNS-сервера нет: обычные запросы перехватывают по пути.
    intercepted: bool = False


def short_owner(owner: IpOwner | None) -> str:
    """«CLOUDFLARENET - Cloudflare, Inc., US» → «Cloudflare»: без кода сети, страны и формы компании."""
    if owner is None or not owner.owner:
        return ""
    name = owner.owner.strip()
    if " - " in name:
        name = name.split(" - ", 1)[1]
    head, _comma, tail = name.rpartition(",")
    if head and len(tail.strip()) == 2 and tail.strip().isalpha():
        name = head
    return name.split(",", 1)[0].strip()


def check_answering(address: str, *, cancel: SocketCancel | None = None) -> Answering:
    """Проверка для адреса DNS-сервера, который сейчас стоит на адаптере."""
    token = cancel or SocketCancel()
    finished = threading.Event()

    def _watch() -> None:
        if not finished.wait(BUDGET_S):
            token.cancel()

    threading.Thread(target=_watch, name="dns-answering-budget", daemon=True).start()
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dns-answering") as pool:
            canary = pool.submit(canary_answered, cancel=token)
            whoami = query_udp(address, WHOAMI_DOMAIN, TYPE_A, timeout_s=UDP_TIMEOUT_S, cancel=token)
            egress = whoami.values(TYPE_A)
            owner = lookup_ip_owner(egress[0], reference_ask(token)) if egress else None
            intercepted = bool(canary.result())
    finally:
        finished.set()
    return Answering(owner=short_owner(owner), asn=owner.asn if owner is not None else "", intercepted=intercepted)
