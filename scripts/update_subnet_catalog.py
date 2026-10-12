"""Пересобирает diagnostics/subnet_catalog.py: адреса российских и зарубежных сетей для проверки «белого списка».

Запуск из public_zapretgui на машине с обычным интернетом (не в мобильной сети):

    PYTHONPATH=src python3 scripts/update_subnet_catalog.py > src/diagnostics/subnet_catalog.py

Адреса узнаются шифрованным DNS (DoH к 1.1.1.1 и 8.8.8.8), в каталог попадают
только те, что сейчас отвечают на TCP 443, — по два-три из разных /24 на
сервис. Подход (российские сети против зарубежных хостингов) взят из
hyperion-cs/dpi-checkers (Apache-2.0); состав сервисов свой.
"""

from __future__ import annotations

import ipaddress
import socket
import sys

from utils.dns_reference import reference_ask
from utils.dns_wire import TYPE_A, query_doh
from utils.ip_owner import lookup_ip_owner
from utils.socket_cancel import SocketCancel

# (подпись, «ru» или «foreign», имена, по которым узнаются адреса сети).
SERVICES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Яндекс", "ru", ("ya.ru", "yandex.ru", "dzen.ru")),
    ("VK", "ru", ("vk.com", "vk.ru")),
    ("Mail.ru", "ru", ("mail.ru", "ok.ru")),
    ("Max", "ru", ("max.ru", "web.max.ru")),
    ("Госуслуги", "ru", ("gosuslugi.ru", "esia.gosuslugi.ru")),
    ("Сбер", "ru", ("sberbank.ru", "online.sberbank.ru")),
    ("Т-Банк", "ru", ("tbank.ru", "www.tbank.ru")),
    ("ВТБ", "ru", ("vtb.ru", "online.vtb.ru")),
    ("Альфа-Банк", "ru", ("alfabank.ru", "web.alfabank.ru")),
    ("Ozon", "ru", ("ozon.ru", "www.ozon.ru")),
    ("Wildberries", "ru", ("wildberries.ru", "www.wildberries.ru")),
    ("Авито", "ru", ("avito.ru", "www.avito.ru")),
    ("МТС", "ru", ("mts.ru", "www.mts.ru")),
    ("МегаФон", "ru", ("megafon.ru", "moscow.megafon.ru")),
    ("Билайн", "ru", ("beeline.ru", "moskva.beeline.ru")),
    ("Т2", "ru", ("t2.ru", "msk.t2.ru")),
    ("Ростелеком", "ru", ("rt.ru", "moscow.rt.ru")),
    ("ФНС", "ru", ("nalog.gov.ru", "lkfl2.nalog.ru")),
    ("Мос.ру", "ru", ("mos.ru", "www.mos.ru")),
    ("Rutube", "ru", ("rutube.ru", "www.rutube.ru")),
    ("Hetzner", "foreign", ("speed.hetzner.de", "ash-speed.hetzner.com", "hel1-speed.hetzner.com")),
    ("DigitalOcean", "foreign", ("speedtest-ams3.digitalocean.com", "speedtest-fra1.digitalocean.com", "speedtest-nyc1.digitalocean.com")),
    ("OVH", "foreign", ("proof.ovh.net", "ovhcloud.com", "www.ovh.com")),
    ("Vultr", "foreign", ("ams-nl-ping.vultr.com", "fra-de-ping.vultr.com", "par-fr-ping.vultr.com")),
    ("Linode", "foreign", ("speedtest.frankfurt.linode.com", "speedtest.london.linode.com", "speedtest.newark.linode.com")),
    ("Leaseweb", "foreign", ("speedtest.ams1.nl.leaseweb.net", "speedtest.fra1.de.leaseweb.net", "speedtest.wdc2.us.leaseweb.net")),
    ("Cloudflare", "foreign", ("cloudflare.com", "one.one.one.one", "dash.cloudflare.com")),
    ("Amazon", "foreign", ("ec2.eu-central-1.amazonaws.com", "s3.eu-central-1.amazonaws.com", "dynamodb.us-east-1.amazonaws.com")),
    ("Google", "foreign", ("google.com", "www.google.com", "dns.google")),
    ("GitHub", "foreign", ("github.com", "api.github.com")),
    ("Microsoft", "foreign", ("login.microsoftonline.com", "outlook.office.com", "microsoft.com")),
    ("Fastly", "foreign", ("fastly.com", "www.fastly.com", "pypi.org")),
    ("Wikimedia", "foreign", ("wikipedia.org", "en.wikipedia.org", "upload.wikimedia.org")),
    ("Apple", "foreign", ("gateway.icloud.com", "idmsa.apple.com", "apple.com")),
    ("Oracle", "foreign", ("oracle.com", "www.oracle.com")),
    ("Stack Overflow", "foreign", ("stackoverflow.com", "stackexchange.com")),
)
PER_SERVICE = 3
TIMEOUT_S = 3.0


def _addresses(name: str) -> tuple[str, ...]:
    found: list[str] = []
    for server in ("1.1.1.1", "8.8.8.8"):
        for ip in query_doh(server, name, TYPE_A, timeout_s=5.0).values(TYPE_A):
            if ip not in found and ipaddress.ip_address(ip).is_global:
                found.append(ip)
    return tuple(found)


def _answers(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 443), timeout=TIMEOUT_S):
            return True
    except OSError:
        return False


def _country(ip: str) -> str:
    owner = lookup_ip_owner(ip, reference_ask(SocketCancel()))
    return owner.country.upper() if owner is not None else ""


def _pick(region: str, names: tuple[str, ...], taken: set[str]) -> tuple[str, ...]:
    """До трёх отвечающих адресов, по одному на /24 и без чужих /24.

    Страна сети по Team Cymru должна совпадать с областью группы: зарубежный
    сайт, который отдаётся с узла CDN внутри России, проверке не годится.
    """
    picked: list[str] = []
    for name in names:
        for ip in _addresses(name):
            net = str(ipaddress.ip_network(f"{ip}/24", strict=False))
            if net in taken or not _answers(ip):
                continue
            if (_country(ip) == "RU") != (region == "ru"):
                continue
            taken.add(net)
            picked.append(ip)
            if len(picked) >= PER_SERVICE:
                return tuple(picked)
    return tuple(picked)


def main() -> int:
    # По очереди: одна /24 не должна попасть в две группы.
    taken: set[str] = set()
    picked = [_pick(region, names, taken) for _label, region, names in SERVICES]
    print('"""Адреса российских и зарубежных сетей для проверки «белого списка» (diagnostics.subnet_whitelist).')
    print()
    print("Собран скриптом scripts/update_subnet_catalog.py: каждый адрес отвечал на TCP 443")
    print("с обычной линии. Подход взят из hyperion-cs/dpi-checkers (Apache-2.0), состав свой.")
    print('"""')
    print()
    print("from __future__ import annotations")
    print()
    print("REGION_RU = \"ru\"")
    print("REGION_FOREIGN = \"foreign\"")
    print()
    print("# (подпись сети, область, адреса из разных /24).")
    print("SUBNET_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (")
    for (label, region, _names), ips in zip(SERVICES, picked):
        if not ips:
            print(f"Нет отвечающих адресов: {label}", file=sys.stderr)
            continue
        constant = "REGION_RU" if region == "ru" else "REGION_FOREIGN"
        print(f"    ({label!r}, {constant}, {ips!r}),")
    print(")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
