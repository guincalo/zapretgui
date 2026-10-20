"""Что скрывается в отчёте, которым делятся: внешний адрес пользователя.

Отчёт копируют в чат поддержки и прикладывают к обращению, поэтому точный адрес
пользователя в нём не нужен: для вывода хватает провайдера, номера сети (AS) и подсети.
Скрывается только последняя часть адреса, а страна, провайдер и сеть остаются, так что
по-прежнему видно, чья это сеть и где. Один модуль решает, как выглядит скрытый адрес,
чтобы карточка, текст и JSON показывали его одинаково.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from utils.address_kinds import AddressKind, address_kind

__all__ = ["MASK_NOTE", "mask_ip", "mask_prefix", "mask_public_ip"]

MASK_NOTE = "последние цифры скрыты, чтобы отчётом можно было делиться"
_HIDDEN = "xxx"
# Подсеть длиннее этих границ почти назвала бы сам адрес — её показывают скрытой.
# Домашняя сеть, общий адрес провайдера и свой компьютер ничего не выдают.
_KEPT_KINDS = (AddressKind.LOCAL, AddressKind.CARRIER, AddressKind.SELF, AddressKind.INVALID)
_V4_VISIBLE = 24
_V6_VISIBLE = 48


def mask_ip(ip: str) -> str:
    """«203.0.113.7» → «203.0.113.xxx», «2001:db8:1234:5::7» → «2001:db8:1234::xxx». Не адрес — как есть."""
    text = str(ip or "").strip()
    try:
        address = ip_address(text)
    except ValueError:
        return text
    if isinstance(address, IPv4Address):
        return ".".join([*text.split(".")[:3], _HIDDEN])
    assert isinstance(address, IPv6Address)
    groups = address.exploded.split(":")[:3]
    return ":".join(group.lstrip("0") or "0" for group in groups) + f"::{_HIDDEN}"


def mask_public_ip(ip: str) -> str:
    """Скрывает только адрес из интернета: у домашней сети (192.168.x.x) и общего адреса провайдера скрывать нечего."""
    return str(ip or "") if address_kind(ip) in _KEPT_KINDS else mask_ip(ip)


def mask_prefix(prefix: str) -> str:
    """Подсеть провайдера: видна, пока она широкая; узкая показывается со скрытым концом."""
    text = str(prefix or "").strip()
    try:
        network = ip_network(text, strict=False)
    except ValueError:
        return text
    limit = _V4_VISIBLE if network.version == 4 else _V6_VISIBLE
    if network.prefixlen <= limit:
        return text
    return f"{mask_ip(str(network.network_address))}/{network.prefixlen}"
