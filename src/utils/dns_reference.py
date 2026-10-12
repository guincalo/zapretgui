"""Эталонные DNS-серверы: кому программа верит, когда сравнивает ответы.

Запрос к ним идёт шифрованным (DNS поверх HTTPS) и прямо по адресу, без имени:
системный DNS в этом не участвует, а сертификат сервера проверяется по его
адресу. Такой ответ провайдер не может незаметно подменить по пути.

Серверов несколько и у разных владельцев: провайдер может закрыть любой из
них (так уже бывало с 8.8.8.8), и эталон не должен пропадать вместе с ним.
Проверки сообщают, какой именно сервер недоступен.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from utils.dns_wire import FAILURE_CANCELLED, DnsQueryResult, query_doh
from utils.socket_cancel import SocketCancel

__all__ = ["REFERENCE_RESOLVERS", "ReferenceResolver", "reference_ask"]


@dataclass(frozen=True, slots=True)
class ReferenceResolver:
    label: str
    address: str


REFERENCE_RESOLVERS: tuple[ReferenceResolver, ...] = (
    ReferenceResolver("Cloudflare", "1.1.1.1"),
    ReferenceResolver("Google", "8.8.8.8"),
    ReferenceResolver("AdGuard", "94.140.14.140"),
    ReferenceResolver("OpenDNS", "208.67.222.222"),
)


def reference_ask(cancel: SocketCancel, *, timeout_s: float = 4.0) -> Callable[[str, int], DnsQueryResult | None]:
    """Служебный вопрос шифрованным путём, к эталонам по очереди до первого ответа.

    Нужен там, где о чём-то спрашивают «через голову» сети: о владельце адреса
    не должен отвечать перехватчик DNS.
    """

    def ask(name: str, rtype: int) -> DnsQueryResult | None:
        last = None
        for resolver in REFERENCE_RESOLVERS:
            last = query_doh(resolver.address, name, rtype, timeout_s=timeout_s, cancel=cancel)
            if last.answered or last.failure == FAILURE_CANCELLED:
                return last
        return last

    return ask
