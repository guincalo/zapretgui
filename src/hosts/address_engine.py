"""Умный движок hosts: какие адреса сервиса сейчас рабочие и в каком порядке их писать.

Каталог hosts — снимок на день выпуска, а адреса устаревают. Хуже того,
браузер Chrome из нескольких строк одного имени берёт только первую (проверено
на Windows 2026-10-13): стоит первому адресу умереть — сайт «иногда не
работает», хотя запасные адреса записаны рядом. Движок решает это на
компьютере пользователя и делает ровно столько, сколько можно доказать.

**Любой сервис.** Если у имени несколько адресов, первым ставится тот, что
лучше ответил пробе (``hosts.address_probe``): подтверждённо живой — перед
неясным, неясный — перед молчащим. Ничего не выбрасывается: у многих
сервисов в hosts стоят обходные адреса или адреса игровых серверов, которые
порт 443 и не слушают, — «молчит» для них не значит «мёртв». У профилей DNS
проверяются только адреса посредников: остальные строки профиля — обычные
адреса сайтов, как их отдал DNS поставщика, трогать их незачем.

**Сервис с «паспортом»** (в hosts стоит настоящий адрес сайта, и сайт отвечает
по HTTPS: YouTube, GitHub). Здесь молчание порта — прямой признак, поэтому
мёртвый адрес убирается, а если живых адресов каталога не хватает, свежие
берутся у шифрованного DNS и идут в hosts только после той же пробы. IPv6
пишется, только если адрес по IPv6 на этом компьютере действительно отвечает,
а не просто «у адаптера есть IPv6».

Итог — набор для ``hosts.catalog_overlay``: в нём только сервисы, у которых
строки получились не такими, как в каталоге. Его видят страница, запись в
hosts и сверка при запуске. Сеть сюда приходит снаружи (``probe``,
``resolve``, ``network_ok``): в тестах она подменяется целиком.

Осторожность. Если не ответил ни один адрес и связи с интернетом нет, ничего
не меняется: иначе при обрыве «умерли» бы все адреса разом. Имя никогда не остаётся без адреса: если
убирать пришлось бы всё, остаётся каталог. В фоновой проверке адрес убирается
только после двух неудач подряд в разных проверках; сразу — лишь когда человек
сам переключил сервис и ждёт результата. Порядок не меняется без нужды: пока
первый адрес не хуже остальных, строки остаются как были, и hosts не
переписывается из-за пары миллисекунд разницы.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from hosts.address_probe import ALIVE, DEAD, UNKNOWN, Probe
from hosts.catalog_overlay import clean_overlay
from hosts.catalog_repository import HOSTS_PROFILE_ID, HostsCatalog

__all__ = ["PASSPORTS", "EngineReport", "Passport", "check_selection"]

MAX_WORKERS = 24
# Сколько проб сразу идёт на один адрес. У сервиса бывает два десятка имён на одном адресе;
# на стенде 2026-10-13 восемнадцать приветствий разом сервер растянул на четыре секунды, а одно
# не дождалось ответа — и рабочий адрес выглядел сомнительным.
MAX_PER_ADDRESS = 4
# Сколько адресов каждого вида писать имени сервиса с паспортом: первый берёт браузер, второй — запас.
KEEP_PER_FAMILY = 2
# Сколько свежих адресов каждого вида брать у шифрованного DNS на одно имя.
FRESH_PER_FAMILY = 4


@dataclass(frozen=True, slots=True)
class Passport:
    """Что известно о сервисе, чей настоящий адрес работает в России.

    ``front`` — нейтральное имя того же владельца для приветствия (пусто — имя
    сервиса не режут, приветствие идёт с ним самим). ``fresh`` — свежие адреса
    можно брать у шифрованного DNS.
    """

    front: str = ""
    fresh: bool = True


# Только сервисы, у которых в hosts стоит настоящий адрес сайта. У Discord, Rutor, x.com,
# Supercell адреса обходные или посредника: честный DNS вернул бы адрес, который в России не открывается.
PASSPORTS: dict[str, Passport] = {
    "hosts.youtube": Passport(front="www.google.com"),
    "hosts.github": Passport(),
    "hosts.githubusercontent_ipv6": Passport(),
}


@dataclass(frozen=True, slots=True)
class EngineReport:
    # Была ли связь с интернетом. Нет — ничего не проверялось и не менялось.
    network: bool = True
    checked: int = 0
    # Набор адресов изменился по сравнению с прежним.
    changed: bool = False
    # Сервис → адреса, убранные как неработающие.
    dropped: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Сервис → свежие адреса, взятые у шифрованного DNS.
    added: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Сервисы, у которых не ответил ни один адрес какого-то имени: оставлены адреса каталога.
    silent: tuple[str, ...] = ()
    checked_at: str = ""


def _is_v6(address: str) -> bool:
    return ":" in address


def _profile_rows(catalog: HostsCatalog, service_name: str, profile_id: str) -> list[tuple[str, str]]:
    if profile_id not in catalog.dns_profiles:
        return []
    index = catalog.dns_profiles.index(profile_id)
    return [
        (host.casefold(), values[index].lower())
        for host, values in catalog.service_entries.get(service_name, [])
        if index < len(values) and values[index]
    ]


def _by_host(rows: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for host, address in rows:
        bucket = grouped.setdefault(host, [])
        if address not in bucket:
            bucket.append(address)
    return grouped


def _name_family(host: str) -> str:
    """Две последние части имени: «raw.githubusercontent.com» и «gist.githubusercontent.com» — одно семейство."""
    return ".".join(host.strip(".").split(".")[-2:])


# Места в очереди на первое место: живой — неясный — молчащий.
_RANK_ALIVE, _RANK_UNSURE, _RANK_SILENT = 0, 1, 2


@dataclass(slots=True)
class _Plan:
    """Один выбранный сервис с профилем: что о нём известно и как его проверять."""

    service_id: str
    service_name: str
    profile_id: str
    hosts: dict[str, list[str]]
    passport: Passport | None

    def key(self, host: str, address: str) -> tuple[str, str, str, str]:
        # Посредник и сервер с нейтральным именем отвечают за все имена сервиса разом:
        # такой адрес проверяется один раз. Иначе ответ зависит от имени, но имена одного
        # семейства (avatars0…avatars8.githubusercontent.com) сервер обслуживает одним
        # сертификатом — проба одна на семейство. Проба на каждое имя била по одному адресу
        # двумя десятками соединений: на стенде 2026-10-13 сервер начал отвечать через раз,
        # и рабочий адрес выглядел мёртвым.
        shared = self.profile_id != HOSTS_PROFILE_ID or (self.passport is not None and bool(self.passport.front))
        return self.service_id, self.profile_id, "" if shared else _name_family(host), address

    @property
    def front(self) -> str:
        return self.passport.front if self.passport is not None else ""


def check_selection(
    selection: dict[str, str],
    *,
    catalog: HostsCatalog,
    previous: dict | None,
    probe: Callable[[str, str, str], Probe],
    resolve: Callable[[str, bool], list[str]],
    network_ok: Callable[[], bool],
    ipv6: bool,
    strict: bool,
    relays: Collection[str] = (),
    recheck: Collection[str] | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> tuple[dict | None, EngineReport]:
    """Проверяет адреса выбранных сервисов. Возвращает (новый набор или None, отчёт).

    ``selection`` — «название сервиса → профиль», как его пишет страница.
    ``catalog`` — поставленный каталог без наложений. ``probe(адрес, имя,
    нейтральное имя)`` и ``resolve(имя, нужен_ли_IPv6)`` — сеть. ``relays`` —
    адреса посредников каталога. ``strict`` — человек ждёт результата: мёртвый
    адрес убирается сразу, без второй проверки. ``recheck`` — названия сервисов,
    которые проверить сейчас (None — все): человек переключил один сервис и не
    должен ждать, пока перепроверятся остальные двадцать; у остальных остаются
    адреса прошлой проверки. None вместо набора — менять нечего (нет связи или
    нечего проверять).
    """
    before = clean_overlay(previous)
    same_catalog = before["catalog"] == catalog.content_sha256
    relay_set = {str(address).strip().lower() for address in relays}
    plans: list[_Plan] = []
    carried: dict[tuple[str, str], list[list[str]]] = {}
    selected = 0
    for service_name, profile_id in (selection or {}).items():
        service_id = catalog.service_id_by_name.get(service_name, "")
        rows = _profile_rows(catalog, service_name, profile_id) if service_id and profile_id else []
        if not rows:
            continue
        selected += 1
        if recheck is not None and service_name not in recheck:
            kept = before["services"].get(service_id, {}).get(profile_id) if same_catalog else None
            if kept:
                carried[(service_id, profile_id)] = [list(row) for row in kept]
            continue
        passport = PASSPORTS.get(service_id) if profile_id == HOSTS_PROFILE_ID else None
        plans.append(_Plan(service_id, service_name, profile_id, _by_host(rows), passport))
    if not selected:
        return None, EngineReport()

    # Что проверять. Без паспорта — только там, где есть из чего выбирать первого.
    units: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    for plan in plans:
        for host, addresses in plan.hosts.items():
            for address in addresses:
                if _is_v6(address) and not ipv6:
                    continue
                if plan.passport is None:
                    rivals = [other for other in addresses if _is_v6(other) == _is_v6(address)]
                    if len(rivals) < 2 or (plan.profile_id != HOSTS_PROFILE_ID and address not in relay_set):
                        continue
                units.setdefault(plan.key(host, address), (address, host, plan.front))
    verdicts = _probe_all(units, probe)
    # Есть ли интернет, спрашивается, только когда не ответил вообще никто: отдельная проверка
    # связи стоит секунды, а хоть один ответ (даже «соединился, но молчит») и так всё доказывает.
    if units and not any(found.verdict in (ALIVE, UNKNOWN) for found in verdicts.values()) and not network_ok():
        return None, EngineReport(network=False)

    # Свежие адреса — запас: только сервисам с паспортом и только именам, которым не хватает живых.
    lacking: dict[tuple[int, str], set[bool]] = {}
    for index, plan in enumerate(plans):
        if plan.passport is None or not plan.passport.fresh:
            continue
        for host, addresses in plan.hosts.items():
            for family in {_is_v6(address) for address in addresses}:
                if family and not ipv6:
                    continue
                own = [address for address in addresses if _is_v6(address) == family]
                states = [_verdict_of(verdicts, plan, host, address) for address in own]
                alive = states.count(ALIVE)
                # Запас нужен, когда живого нет вовсе или мёртвых стало слишком много. Каталог с одним
                # отвечающим адресом на имя — не «нехватка», и разовая заминка без ответа — тоже.
                if not alive or len(own) - states.count(DEAD) < min(KEEP_PER_FAMILY, len(own)):
                    lacking.setdefault((index, host), set()).add(family)
    asked = list(dict.fromkeys(host for _index, host in lacking))

    def _answers(host: str) -> list[str]:
        try:
            return [str(item).strip().lower() for item in resolve(host, ipv6)]
        except Exception:
            return []

    answered: dict[str, list[str]] = {}
    if asked:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(asked)), thread_name_prefix="hosts-engine-dns") as pool:
            answered = dict(zip(asked, pool.map(_answers, asked)))
    fresh: dict[tuple[int, str], list[str]] = {}
    fresh_units: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
    for (index, host), families in lacking.items():
        plan = plans[index]
        taken = {False: 0, True: 0}
        for address in answered.get(host, []):
            family = _is_v6(address)
            if family not in families or address in plan.hosts[host] or taken[family] >= FRESH_PER_FAMILY:
                continue
            taken[family] += 1
            fresh.setdefault((index, host), []).append(address)
            key = plan.key(host, address)
            if key not in verdicts:
                fresh_units.setdefault(key, (address, host, plan.front))
    verdicts.update(_probe_all(fresh_units, probe))

    remembered = {
        (service_id, profile_id): [(str(host), str(address)) for host, address in rows]
        for service_id, profiles in before["services"].items()
        for profile_id, rows in profiles.items()
    } if same_catalog else {}
    suspects_before = set(before["suspects"]) if same_catalog else set()
    # Подозрение с адресов, которые сейчас не проверялись, не снимается.
    suspects_after: set[str] = {address for rows in carried.values() for _host, address in rows if address in suspects_before}
    services: dict[str, dict[str, list[list[str]]]] = {}
    for (service_id, profile_id), rows in carried.items():
        services.setdefault(service_id, {})[profile_id] = rows
    dropped: dict[str, set[str]] = {}
    added: dict[str, set[str]] = {}
    silent: list[str] = []
    for index, plan in enumerate(plans):
        full = plan.passport is not None
        result: list[list[str]] = []
        answered_any = False
        for host, addresses in plan.hosts.items():
            ranked: list[tuple[str, int]] = []
            doomed: list[str] = []
            for address in addresses:
                verdict = _verdict_of(verdicts, plan, host, address)
                if verdict == ALIVE:
                    ranked.append((address, _RANK_ALIVE))
                    answered_any = True
                elif verdict == DEAD:
                    ranked.append((address, _RANK_SILENT))
                    if full and (strict or address in suspects_before):
                        doomed.append(address)
                    elif full:
                        # Первая неудача в фоновой проверке: запомнить и перепроверить в следующий раз.
                        suspects_after.add(address)
                else:
                    # Проба ничего не доказала — или адрес не проверялся вовсе (IPv6 без IPv6, не посредник).
                    ranked.append((address, _RANK_UNSURE))
                    answered_any = answered_any or verdict == UNKNOWN
            # Свежий адрес идёт в hosts, только если сам прошёл пробу; быстрый — раньше.
            proven = sorted(
                (probe_of.ms if probe_of.ms is not None else 0.0, address)
                for address in fresh.get((index, host), [])
                if (probe_of := verdicts.get(plan.key(host, address))) is not None and probe_of.verdict == ALIVE
            )
            ranked.extend((address, _RANK_ALIVE) for _ms, address in proven)
            # Имя не остаётся без адреса, которым этот компьютер может пользоваться.
            usable = [address for address, _rank in ranked if address not in doomed and (ipv6 or not _is_v6(address))]
            if not usable:
                doomed = []
            ordered = _order(
                [(address, rank) for address, rank in ranked if address not in doomed],
                remembered=[address for row_host, address in remembered.get((plan.service_id, plan.profile_id), ()) if row_host == host],
                first_v6=_is_v6(addresses[0]),
                keep=KEEP_PER_FAMILY if full else 0,
            )
            if doomed:
                dropped.setdefault(plan.service_name, set()).update(doomed)
            taken_fresh = [address for address in ordered if address not in addresses]
            if taken_fresh:
                added.setdefault(plan.service_name, set()).update(taken_fresh)
            result.extend([host, address] for address in ordered)
        if full and not answered_any and any(key[:2] == (plan.service_id, plan.profile_id) for key in verdicts):
            silent.append(plan.service_name)
        # В наборе — только то, что отличается от каталога: настройки не пухнут сотнями одинаковых строк.
        if result != [[host, address] for host, addresses in plan.hosts.items() for address in addresses]:
            services.setdefault(plan.service_id, {})[plan.profile_id] = result

    # Время проверки — когда проверялись все: частичная проверка одного сервиса его не обновляет.
    partial = recheck is not None and same_catalog and bool(before["checked_at"])
    checked_at = before["checked_at"] if partial else now().isoformat(timespec="seconds")
    after = clean_overlay(
        {"catalog": catalog.content_sha256, "checked_at": checked_at, "suspects": sorted(suspects_after), "services": services}
    )
    report = EngineReport(
        network=True,
        checked=len(verdicts),
        changed=after["services"] != (before["services"] if same_catalog else {}),
        dropped={name: tuple(sorted(items)) for name, items in dropped.items()},
        added={name: tuple(sorted(items)) for name, items in added.items()},
        silent=tuple(silent),
        checked_at=checked_at,
    )
    return after, report


def _probe_all(
    units: dict[tuple[str, str, str, str], tuple[str, str, str]],
    probe: Callable[[str, str, str], Probe],
) -> dict[tuple[str, str, str, str], Probe]:
    if not units:
        return {}
    gates = {unit[0]: threading.BoundedSemaphore(MAX_PER_ADDRESS) for unit in units.values()}

    def _gated(address: str, host: str, front: str) -> Probe:
        with gates[address]:
            return probe(address, host, front)

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(units)), thread_name_prefix="hosts-engine") as pool:
        futures = {key: pool.submit(_gated, *unit) for key, unit in units.items()}
        verdicts: dict[tuple[str, str, str, str], Probe] = {}
        for key, future in futures.items():
            try:
                verdicts[key] = future.result()
            except Exception:
                verdicts[key] = Probe(UNKNOWN, reason="проба не выполнилась")
    return verdicts


def _verdict_of(verdicts: dict, plan: _Plan, host: str, address: str) -> str | None:
    found = verdicts.get(plan.key(host, address))
    return found.verdict if found is not None else None


def _order(ranked: list[tuple[str, int]], *, remembered: list[str], first_v6: bool, keep: int) -> list[str]:
    """Порядок адресов одного имени. ``ranked`` — (адрес, место в очереди) в порядке каталога.

    Прежний порядок сохраняется, пока он годится: иначе блок в hosts переписывался бы при
    каждой проверке. Меняется он только по делу — первым в каждом виде (IPv4, IPv6) должен
    стоять адрес с лучшим ответом из имеющихся: именно первый берёт браузер. ``keep`` —
    сколько адресов каждого вида оставить (0 — все); лишними становятся худшие.
    """

    def family(want_v6: bool) -> list[str]:
        rank = {address: place for address, place in ranked if _is_v6(address) == want_v6}
        if not rank:
            return []
        kept = [address for address in dict.fromkeys(remembered) if address in rank]
        ordered = kept + [address for address in rank if address not in kept]
        best = min(rank.values())
        if rank[ordered[0]] != best:
            head = next(address for address in ordered if rank[address] == best)
            ordered.remove(head)
            ordered.insert(0, head)
        if keep and len(ordered) > keep:
            ordered = [ordered[0]] + sorted(ordered[1:], key=rank.__getitem__)[: keep - 1]
        return ordered

    v4, v6 = family(False), family(True)
    return v6 + v4 if first_v6 else v4 + v6
