"""Соседи известных адресов: где ещё у владельца сайта стоят такие же серверы.

Крупные сервисы раздают один и тот же сайт с множества адресов, устроенных по
общему образцу. У Google «номер сервера» — младшая часть адреса — один и тот
же во всех соседних подсетях: 64.233.162.198 и 64.233.165.198,
2a00:1450:4010:c08::88 и 2a00:1450:4010:c0a::88 (обратные имена lg-in-x88 и
lm-in-x88.1e100.net). В одной только подсети 2a00:1450:4010::/48 отвечает
больше трёх тысяч адресов, а DNS называет из них четыре.

Отсюда приём: взять уже известные адреса имени и собрать из них соседей — те
же номера серверов в ближайших подсетях. Это только список кандидатов: каждый
из них умный движок сначала пробует соединением, а в hosts пускает лишь после
полной проверки, что адрес отдаёт именно этот сайт (``hosts.address_probe``).

Перебирать подсеть целиком незачем и нельзя: в /48 адресов больше, чем можно
опросить, и почти все они принадлежат другим сервисам владельца.
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address

__all__ = ["V4_SPREAD", "V6_SPREAD", "neighbours"]

# На сколько подсетей в каждую сторону от известной смотреть: /24 у IPv4, /64 у IPv6.
V4_SPREAD = 4
V6_SPREAD = 32


def _steps(spread: int) -> list[int]:
    """0, +1, -1, +2, -2 …: ближние подсети раньше дальних."""
    order = [0]
    for distance in range(1, spread + 1):
        order.extend((distance, -distance))
    return order


def neighbours(known: list[str], *, limit: int) -> list[str]:
    """Кандидаты рядом с известными адресами, ближние раньше; известные в список не входят.

    IPv4 ``a.b.c.d``: подсеть — ``a.b.c``, номер сервера — ``d``. IPv6: подсеть — первые 64
    бита, номер сервера — младшие 64. Номера берутся только у адресов той же большой сети
    (``a.b`` или первые 48 бит): у другой сети того же владельца нумерация своя.
    """
    seen: set[str] = set()
    seeds: list[tuple[int, int, int, int]] = []  # (вид, большая сеть, подсеть, номер сервера)
    for raw in known:
        try:
            address = ip_address(str(raw).strip())
        except ValueError:
            continue
        seen.add(str(address))
        value = int(address)
        if address.version == 4:
            seed = (4, value >> 16, (value >> 8) & 0xFF, value & 0xFF)
        else:
            seed = (6, value >> 80, (value >> 64) & 0xFFFF, value & ((1 << 64) - 1))
        if seed not in seeds:
            seeds.append(seed)

    found: list[str] = []
    for version, spread, top in ((4, V4_SPREAD, 0xFF), (6, V6_SPREAD, 0xFFFF)):
        family = [seed for seed in seeds if seed[0] == version]
        for step in _steps(spread):
            for _version, network, subnet, _host in dict.fromkeys((seed[0], seed[1], seed[2], 0) for seed in family):
                target = subnet + step
                if not 0 <= target <= top:
                    continue
                for host in dict.fromkeys(seed[3] for seed in family if seed[1] == network):
                    if version == 4:
                        candidate = str(IPv4Address((network << 16) | (target << 8) | host))
                    else:
                        candidate = str(IPv6Address((network << 80) | (target << 64) | host))
                    if candidate not in seen:
                        seen.add(candidate)
                        found.append(candidate)
    # По очереди от каждого вида: IPv4 не должен вытеснить IPv6 из отведённого числа, и наоборот.
    v4 = [address for address in found if ":" not in address]
    v6 = [address for address in found if ":" in address]
    mixed: list[str] = []
    for index in range(max(len(v4), len(v6))):
        mixed.extend(lane[index] for lane in (v4, v6) if index < len(lane))
    return mixed[: max(0, limit)]
