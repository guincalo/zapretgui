"""Проверенные адреса поверх каталога hosts.

Каталог (``hosts_catalog.sqlite3``) — снимок адресов на день выпуска. Адреса
живут своей жизнью: сервер переезжает, фронт перестаёт отвечать. «Умный
движок» (``hosts.address_engine``) проверяет адреса на компьютере пользователя
и запоминает, какие из них сейчас рабочие и в каком порядке их писать. Этот
модуль накладывает такой набор на каталог: дальше вся программа — страница,
запись в hosts, сверка при запуске — видит уже проверенные адреса и не знает,
откуда они взялись.

Набор — простые данные::

    {"catalog": "<content_sha256 каталога>",
     "checked_at": "<когда проверяли>",
     "suspects": ["<адрес, не ответивший в прошлой проверке>", ...],
     "services": {"<service_id>": {"<profile_id>": [["имя", "адрес"], ...]}}}

Порядок строк важен: браузер Chrome из нескольких строк одного имени берёт
только первую (проверено на Windows 2026-10-13), поэтому первым должен стоять
адрес, который точно отвечает.

Наложение осторожное. Набор для пары «сервис, профиль» принимается, только
если он покрывает ровно те же имена, что и каталог: профиль, в котором
пропало имя, страница перестала бы предлагать. Набор от другого каталога
(программа обновилась) отбрасывается целиком.
"""

from __future__ import annotations

from dataclasses import replace
from ipaddress import ip_address

from hosts.catalog_repository import HostsCatalog

__all__ = ["apply_overlay", "clean_overlay", "overlay_rows"]


def _valid_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def clean_overlay(raw: object) -> dict:
    """Набор из настроек в строгом виде; мусор и пустые части отбрасываются."""
    data = raw if isinstance(raw, dict) else {}
    services: dict[str, dict[str, list[list[str]]]] = {}
    for service_id, profiles in (data.get("services") if isinstance(data.get("services"), dict) else {}).items():
        kept: dict[str, list[list[str]]] = {}
        for profile_id, rows in (profiles if isinstance(profiles, dict) else {}).items():
            pairs: list[list[str]] = []
            for row in rows if isinstance(rows, list) else ():
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    continue
                host, address = str(row[0]).strip().lower(), str(row[1]).strip().lower()
                if host and _valid_ip(address) and [host, address] not in pairs:
                    pairs.append([host, address])
            if pairs:
                kept[str(profile_id)] = pairs
        if kept:
            services[str(service_id)] = kept
    suspects = [
        str(item).strip().lower()
        for item in (data.get("suspects") if isinstance(data.get("suspects"), list) else ())
        if _valid_ip(str(item).strip())
    ]
    # Копилка: адреса, которые программа сама нашла и проверила, — запас сверх каталога.
    pool: dict[str, dict[str, list[str]]] = {}
    for service_id, hosts in (data.get("pool") if isinstance(data.get("pool"), dict) else {}).items():
        found: dict[str, list[str]] = {}
        for host, addresses in (hosts if isinstance(hosts, dict) else {}).items():
            clean = list(
                dict.fromkeys(
                    str(item).strip().lower()
                    for item in (addresses if isinstance(addresses, list) else ())
                    if _valid_ip(str(item).strip())
                )
            )
            if clean and str(host).strip():
                found[str(host).strip().lower()] = clean
        if found:
            pool[str(service_id)] = found
    return {
        "catalog": str(data.get("catalog") or ""),
        "checked_at": str(data.get("checked_at") or ""),
        "suspects": sorted(set(suspects)),
        "services": services,
        "pool": pool,
    }


def overlay_rows(overlay: dict, service_id: str, profile_id: str) -> list[tuple[str, str]]:
    rows = ((overlay.get("services") or {}).get(service_id) or {}).get(profile_id) or []
    return [(str(host), str(address)) for host, address in rows]


def _catalog_hosts(entries: list[tuple[str, list[str]]], index: int) -> list[str]:
    hosts: list[str] = []
    for host, values in entries:
        if index < len(values) and values[index] and host.casefold() not in hosts:
            hosts.append(host.casefold())
    return hosts


def _replace_profile(
    entries: list[tuple[str, list[str]]], index: int, width: int, rows: list[tuple[str, str]]
) -> list[tuple[str, list[str]]]:
    """Строки сервиса, где адреса одного профиля заменены на ``rows`` (с сохранением порядка имён каталога)."""
    display: dict[str, str] = {}
    lanes: dict[str, list[list[str]]] = {}
    order: list[str] = []
    for host, values in entries:
        key = host.casefold()
        if key not in lanes:
            lanes[key], display[key] = [], host
            order.append(key)
        lane = list(values) + [""] * (width - len(values))
        lane[index] = ""
        lanes[key].append(lane)
    wanted: dict[str, list[str]] = {}
    for host, address in rows:
        wanted.setdefault(host.casefold(), []).append(address)
    for key, addresses in wanted.items():
        for position, address in enumerate(addresses):
            if position >= len(lanes[key]):
                lanes[key].append([""] * width)
            lanes[key][position][index] = address
    return [(display[key], lane) for key in order for lane in lanes[key] if any(lane)]


def apply_overlay(catalog: HostsCatalog, overlay: object) -> HostsCatalog:
    """Каталог с проверенными адресами. Без набора или с чужим набором — тот же каталог."""
    data = clean_overlay(overlay)
    if not data["services"] or not catalog.content_sha256 or data["catalog"] != catalog.content_sha256:
        return catalog
    entries = {name: list(items) for name, items in catalog.service_entries.items()}
    changed = False
    for service_id, profiles in data["services"].items():
        name = catalog.service_name_by_id.get(service_id)
        if not name or not entries.get(name):
            continue
        for profile_id, pairs in profiles.items():
            if profile_id not in catalog.dns_profiles:
                continue
            index = catalog.dns_profiles.index(profile_id)
            rows = [(host, address) for host, address in pairs]
            # Набор обязан покрывать те же имена, что каталог: иначе профиль перестал бы быть полным.
            if sorted({host for host, _address in rows}) != sorted(_catalog_hosts(entries[name], index)):
                continue
            entries[name] = _replace_profile(entries[name], index, len(catalog.dns_profiles), rows)
            changed = True
    if not changed:
        return catalog
    services = {
        name: {host: list(values) for host, values in items} for name, items in entries.items()
    }
    return replace(catalog, service_entries=entries, services=services)
