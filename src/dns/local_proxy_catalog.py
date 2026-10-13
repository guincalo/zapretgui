"""Серверы для шифрованного DNS через встроенный dnscrypt-proxy.

Windows сама умеет только обычный DNS и DoH, поэтому для DNSCrypt и ODoH
программа запускает на компьютере маленький посредник dnscrypt-proxy: он
принимает обычные запросы на адресе 127.0.0.1 и шифрует их по дороге.

Режимы:
- MODE_DNSCRYPT — DNSCrypt: запрос зашифрован, имени сервера в открытом
  виде нет, поэтому его не подменить и не закрыть по имени, как DoH;
- MODE_ANONYMIZED — анонимный DNSCrypt: зашифрованный запрос идёт через
  посредника (relay), и DNS-сервер не видит адрес пользователя;
- MODE_ODOH — Oblivious DoH: то же самое, но поверх HTTPS. Посредников в
  мире всего два, сами авторы считают поддержку пробной;
- MODE_DOH — обычный DoH к одному серверу из списка программы. Отдельной
  плитки у режима нет: его включает плитка самого сервера там, где Windows
  шифровать DNS не умеет (Windows 10), — иначе запросы к серверу шли бы
  открытым текстом и ответ можно было бы подменить по дороге. Серверы этого
  режима не записаны здесь, а собираются из данных плитки (doh_upstreams).

Каждый сервер записан своим «штампом» (sdns://…) из официальных списков
DNSCrypt/dnscrypt-resolvers (v3: public-resolvers, relays, odoh-servers,
odoh-relays): в штампе адрес и ключ сервера, поэтому списки из сети не
скачиваются и запуск не зависит от посторонних сайтов. Все серверы — без
записи запросов, без фильтров и с проверкой DNSSEC; каждый отвечал при
проверке с Windows 2026-10-07. Движок сам выбирает самый быстрый из живых.

operator — кто держит сервер. В анонимных режимах посредник и сервер
обязаны быть у разных владельцев, иначе один владелец видит и адрес
пользователя, и запрос: routes() это соблюдает. Quad9 через посредников не
работает, поэтому в анонимный режим не входит.
"""

from __future__ import annotations

from dataclasses import dataclass

MODE_DNSCRYPT = "dnscrypt"
MODE_ANONYMIZED = "anonymized"
MODE_ODOH = "odoh"
MODE_DOH = "doh"
MODES = (MODE_DNSCRYPT, MODE_ANONYMIZED, MODE_ODOH, MODE_DOH)

# Обычный DNS для первого шага: узнать адреса ODoH-серверов, записанных именем.
BOOTSTRAP_RESOLVERS = ("9.9.9.11:53", "77.88.8.8:53")


@dataclass(frozen=True, slots=True)
class Upstream:
    name: str
    operator: str
    stamp: str


# DNSCrypt-серверы.
DNSCRYPT_SERVERS: tuple[Upstream, ...] = (
    Upstream('quad9-dnscrypt-ip4-nofilter-pri', 'quad9', 'sdns://AQcAAAAAAAAADTkuOS45LjEwOjg0NDMgZ8hHuMh1jNEgJFVDvnVnRt803x2EwAuMRwNo34Idhj4ZMi5kbnNjcnlwdC1jZXJ0LnF1YWQ5Lm5ldA'),
    Upstream('scaleway-fr', 'scaleway', 'sdns://AQcAAAAAAAAADjIxMi40Ny4yMjguMTM2IOgBuE6mBr-wusDOQ0RbsV66ZLAvo8SqMa4QY2oHkDJNHzIuZG5zY3J5cHQtY2VydC5mci5kbnNjcnlwdC5vcmc'),
    Upstream('scaleway-ams', 'scaleway', 'sdns://AQcAAAAAAAAADTUxLjE1LjEyMi4yNTAg6Q3ZfapcbHgiHKLF7QFoli0Ty1Vsz3RXs1RUbxUrwZAcMi5kbnNjcnlwdC1jZXJ0LnNjYWxld2F5LWFtcw'),
    Upstream('dct-de', 'dct', 'sdns://AQcAAAAAAAAADzE5NC4xNjQuMTk0LjIxNiACT6z2dYj94msaKqctjIpBaeDHG2JVOfPTqDH_0KzZkBYyLmRuc2NyeXB0LWNlcnQuZGN0LWRl'),
    Upstream('dct-fr', 'dct', 'sdns://AQcAAAAAAAAADTQ1LjE0Ny45OC4yMjMgxrLxuUBUIK0uhJptc75BSbkhou5kHDMi2p4AHf0zHgMWMi5kbnNjcnlwdC1jZXJ0LmRjdC1mcg'),
    Upstream('nwps.fi', 'nwps', 'sdns://AQcAAAAAAAAAEzk1LjIxNi4xMzguMTQxOjg0NDMguorzbtc_JWEU0KBhGLZWuvInIeGd-R5CcEHYS-SIz7cXMi5kbnNjcnlwdC1jZXJ0Lm53cHMuZmk'),
    Upstream('serbica', 'serbica', 'sdns://AQcAAAAAAAAAEzE4NS42Ni4xNDMuMTc4OjUzNTMg-Y2MQmGOXiggAEKulN-ITGEn_Kj3TIP1UK1X2wh3o7wXMi5kbnNjcnlwdC1jZXJ0LnNlcmJpY2E'),
    Upstream('dnscry.pt-moscow-ipv4', 'dnscry.pt', 'sdns://AQcAAAAAAAAADjkzLjE4My4xMDYuMjIyIBQ6uCceRUNVJGFB1kGltuW_Jr2Nsizvc06BfMI30iIBGTIuZG5zY3J5cHQtY2VydC5kbnNjcnkucHQ'),
    Upstream('dnscry.pt-frankfurt-ipv4', 'dnscry.pt', 'sdns://AQcAAAAAAAAADDQ1LjgyLjEyMC42MSD79MPkuIliP7zrXMgYVK5wcSD_shP7dPfHx9haFaux6RkyLmRuc2NyeXB0LWNlcnQuZG5zY3J5LnB0'),
    Upstream('dnscry.pt-helsinki-ipv4', 'dnscry.pt', 'sdns://AQcAAAAAAAAADjM3LjIyOC4xMjkuMTYwIPlYPWSML8DlYbkp1ycL3CBER_3aJHp7GLvX_TRvbojGGTIuZG5zY3J5cHQtY2VydC5kbnNjcnkucHQ'),
    Upstream('dnscry.pt-amsterdam-ipv4', 'dnscry.pt', 'sdns://AQcAAAAAAAAADjE5OC4xNDAuMTQxLjQ2IFqbafOxgXuKwOgYxQ6XUqHWkMUt_5LI2nDkdVFU5hm7GTIuZG5zY3J5cHQtY2VydC5kbnNjcnkucHQ'),
    Upstream('dnscry.pt-warsaw-ipv4', 'dnscry.pt', 'sdns://AQcAAAAAAAAADTE5NS4zLjIyMS4xNjIg9OBpbJKxZJGY-YUI3xWNXp-k_MgEzf9NFZruBsmXR7oZMi5kbnNjcnlwdC1jZXJ0LmRuc2NyeS5wdA'),
    Upstream('dnscry.pt-istanbul-ipv4', 'dnscry.pt', 'sdns://AQcAAAAAAAAADzE4OC4xMzIuMTkyLjE2OCBcrSjt8C0Ztuqwxafp4VzylDf9N_disPrgL1m4GNX6XRkyLmRuc2NyeXB0LWNlcnQuZG5zY3J5LnB0'),
    Upstream('cs-de', 'cryptostorm', 'sdns://AQcAAAAAAAAACzE0Ni43MC44Mi4zIDEzcq1ZVjLCQWuHLwmPhRvduWUoTGy-mk8ZCWQw26laHjIuZG5zY3J5cHQtY2VydC5jcnlwdG9zdG9ybS5pcw'),
    Upstream('cs-finland', 'cryptostorm', 'sdns://AQcAAAAAAAAADTgzLjE0My4yNDIuNDMgMTNyrVlWMsJBa4cvCY-FG925ZShMbL6aTxkJZDDbqVoeMi5kbnNjcnlwdC1jZXJ0LmNyeXB0b3N0b3JtLmlz'),
    Upstream('cs-nl', 'cryptostorm', 'sdns://AQcAAAAAAAAADTE4NS4xMDcuODAuODQgMTNyrVlWMsJBa4cvCY-FG925ZShMbL6aTxkJZDDbqVoeMi5kbnNjcnlwdC1jZXJ0LmNyeXB0b3N0b3JtLmlz'),
)

# Посредники анонимного DNSCrypt.
DNSCRYPT_RELAYS: tuple[Upstream, ...] = (
    Upstream('anon-cs-nl', 'cryptostorm', 'sdns://gRExODUuMTA3LjgwLjg0OjQ0Mw'),
    Upstream('anon-cs-finland', 'cryptostorm', 'sdns://gRE4My4xNDMuMjQyLjQzOjQ0Mw'),
    Upstream('anon-cs-de', 'cryptostorm', 'sdns://gQ8xNDYuNzAuODIuMzo0NDM'),
    Upstream('anon-cs-poland', 'cryptostorm', 'sdns://gREzNy4xMjAuMjExLjkxOjQ0Mw'),
    Upstream('anon-scaleway', 'scaleway', 'sdns://gRIyMTIuNDcuMjI4LjEzNjo0NDM'),
    Upstream('anon-scaleway-ams', 'scaleway', 'sdns://gRE1MS4xNS4xMjIuMjUwOjQ0Mw'),
    Upstream('anon-serbica', 'serbica', 'sdns://gRMxODUuNjYuMTQzLjE3ODo1MzUz'),
    Upstream('anon-kama', 'kama', 'sdns://gQ4xMzcuNzQuMjIzLjIzNA'),
    Upstream('anon-dnswarden-swiss', 'dnswarden', 'sdns://gRQxODguMjQ0LjExNy4xMTQ6MTQ0Mw'),
    Upstream('dnscry.pt-anon-frankfurt02-ipv4', 'dnscry.pt', 'sdns://gQ00NS4xNDcuNTEuMTIz'),
    Upstream('dnscry.pt-anon-helsinki-ipv4', 'dnscry.pt', 'sdns://gQ4zNy4yMjguMTI5LjE2MA'),
    Upstream('dnscry.pt-anon-amsterdam-ipv4', 'dnscry.pt', 'sdns://gQ4xOTguMTQwLjE0MS40Ng'),
    Upstream('dnscry.pt-anon-riga-ipv4', 'dnscry.pt', 'sdns://gQ8xOTUuMTIzLjIxMi4yMDA'),
)

# ODoH-серверы.
ODOH_SERVERS: tuple[Upstream, ...] = (
    Upstream('odoh-cloudflare', 'cloudflare', 'sdns://BQcAAAAAAAAAF29kb2guY2xvdWRmbGFyZS1kbnMuY29tCi9kbnMtcXVlcnk'),
    Upstream('odoh-crypto-sx', 'crypto.sx', 'sdns://BQcAAAAAAAAADm9kb2guY3J5cHRvLnN4Ci9kbnMtcXVlcnk'),
    Upstream('odoh-snowstorm', 'snowstorm', 'sdns://BQcAAAAAAAAAE2RvcGUuc25vd3N0b3JtLmxvdmUKL2Rucy1xdWVyeQ'),
)

# Посредники ODoH.
ODOH_RELAYS: tuple[Upstream, ...] = (
    Upstream('odohrelay-crypto-sx', 'crypto.sx', 'sdns://hQcAAAAAAAAAAAAab2RvaC1yZWxheS5lZGdlY29tcHV0ZS5hcHABLw'),
    Upstream('odohrelay-numa', 'numa', 'sdns://hQcAAAAAAAAAAAASb2RvaC1yZWxheS5udW1hLnJzBi9yZWxheQ'),
)

# Через посредников Quad9 не отвечает.
# Свойства сервера в штампе (первый байт набора): проверяет подписи DNSSEC.
_PROP_DNSSEC = 1
_STAMP_DOH = 0x02


def _lp(data: bytes) -> bytes:
    """Строка штампа: байт длины и сама строка."""
    if len(data) > 255:
        raise ValueError("слишком длинная строка для штампа DNS")
    return bytes([len(data)]) + data


def doh_stamp(address: str, host: str, path: str = "/dns-query", *, port: int = 443, dnssec: bool = False) -> str:
    """Штамп sdns:// сервера DoH: адрес, имя для сертификата и путь запроса.

    Адрес записан в самом штампе, поэтому имя сервера узнавать обычным DNS не
    нужно: движок сразу идёт на адрес, а имя называет только для сертификата.
    """
    import base64

    target = address if int(port) == 443 else f"{address}:{int(port)}"
    props = (_PROP_DNSSEC if dnssec else 0).to_bytes(8, "little")
    # Отпечатков сертификата нет: один пустой элемент списка.
    raw = bytes([_STAMP_DOH]) + props + _lp(target.encode("ascii")) + b"\x00" + _lp(host.encode("idna")) + _lp(path.encode("ascii"))
    return "sdns://" + base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def doh_upstreams(doh_url: str, addresses, *, dnssec: bool = False) -> tuple[Upstream, ...]:
    """Серверы режима MODE_DOH для одной плитки: по одному на каждый её адрес IPv4.

    Пусто — у плитки нет шаблона DoH или адреса IPv4 (движок ходит по IPv4),
    значит через посредника её не поставить.
    """
    from ipaddress import ip_address
    from urllib.parse import urlsplit

    parts = urlsplit(str(doh_url or "").strip())
    if parts.scheme != "https" or not parts.hostname:
        return ()
    result: list[Upstream] = []
    for address in addresses or ():
        try:
            if ip_address(str(address).strip()).version != 4:
                continue
        except ValueError:
            continue
        stamp = doh_stamp(str(address).strip(), parts.hostname, parts.path or "/dns-query", port=parts.port or 443, dnssec=dnssec)
        result.append(Upstream(f"doh-{len(result) + 1}", parts.hostname, stamp))
    return tuple(result)


_NOT_ANONYMIZABLE = frozenset({"quad9"})


def servers(mode: str, upstreams: tuple[Upstream, ...] = ()) -> tuple[Upstream, ...]:
    """Серверы режима. У MODE_DOH своих нет: их передают (upstreams)."""
    if mode == MODE_DNSCRYPT:
        return DNSCRYPT_SERVERS
    if mode == MODE_ANONYMIZED:
        return tuple(item for item in DNSCRYPT_SERVERS if item.operator not in _NOT_ANONYMIZABLE)
    if mode == MODE_ODOH:
        return ODOH_SERVERS
    if mode == MODE_DOH:
        if not upstreams:
            raise ValueError("Для режима DoH не задан сервер")
        return tuple(upstreams)
    raise ValueError(f"Неизвестный режим шифрованного DNS: {mode}")


def relays(mode: str) -> tuple[Upstream, ...]:
    """Посредники режима; у обычного DNSCrypt их нет."""
    if mode == MODE_ANONYMIZED:
        return DNSCRYPT_RELAYS
    if mode == MODE_ODOH:
        return ODOH_RELAYS
    if mode in (MODE_DNSCRYPT, MODE_DOH):
        return ()
    raise ValueError(f"Неизвестный режим шифрованного DNS: {mode}")


def routes(mode: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(сервер, его посредники): посредник всегда другого владельца."""
    if mode == MODE_DOH:
        return ()
    result = []
    for server in servers(mode):
        via = tuple(relay.name for relay in relays(mode) if relay.operator != server.operator)
        if via:
            result.append((server.name, via))
    return tuple(result)


__all__ = [
    "BOOTSTRAP_RESOLVERS",
    "DNSCRYPT_RELAYS",
    "DNSCRYPT_SERVERS",
    "MODE_ANONYMIZED",
    "MODE_DNSCRYPT",
    "MODE_ODOH",
    "MODE_DOH",
    "MODES",
    "ODOH_RELAYS",
    "ODOH_SERVERS",
    "Upstream",
    "doh_stamp",
    "doh_upstreams",
    "relays",
    "routes",
    "servers",
]
