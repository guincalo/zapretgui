"""Видеосерверы YouTube, которые эта сеть получает на самом деле.

YouTube выбирает кэш Google для ролика по адресу того, кто смотрит (см. заметку про GGC), поэтому
список серверов нельзя взять из встроенных таблиц: проверять нужно именно те узлы, что вернулись
пользователю, который запустил проверку. Источников два, и они дополняют друг друга:

- страница ролика на youtube.com — то, что получил бы плеер, обычно несколько узлов;
- ``redirector.googlevideo.com/report_mapping`` по обычному HTTP — ближайший к вам узел. Этот ответ
  приходит и тогда, когда сам youtube.com закрыт, и тогда, когда страница грузится слишком медленно.

Список — сначала узлы плеера, затем узел редиректора; если нет ни тех, ни другого, остаётся общий
адрес redirector.googlevideo.com. У каждого узла помнится, откуда он взят, чтобы отчёт мог сказать,
на чём держится вывод. Как и остальные «collect», модуль ничего не решает о блокировке: он только находит узлы.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from diagnostics import net_access
from diagnostics.limits import DISCOVERY_READ_BUDGET_S, DISCOVERY_TIMEOUT, VIDEO_SERVERS
from diagnostics.services import GOOGLEVIDEO_FALLBACK_HOST, YOUTUBE_HOST

__all__ = [
    "SOURCE_FALLBACK",
    "SOURCE_PLAYER",
    "SOURCE_REDIRECTOR",
    "VideoNodes",
    "combine",
    "discover",
    "owner_note",
    "spare_note",
]

SOURCE_PLAYER = "player"
SOURCE_REDIRECTOR = "redirector"
SOURCE_FALLBACK = "fallback"

_WATCH_PAGE = "/watch?v=jNQXAC9IVRw&hl=en"
_WATCH_PAGE_MAX_BYTES = 2_000_000
# Сколько ждать ответ редиректора: это одна короткая строка, живой ответ приходит за доли секунды.
MAPPING_TIMEOUT_S = 3.0

# Кто из владельцев сетей — сам Google: узел в такой сети не «кэш у провайдера».
_GOOGLE_ASNS = frozenset({"15169", "36040", "43515", "19527", "396982"})


@dataclass(frozen=True, slots=True)
class VideoNodes:
    # Видеосерверы по порядку проверки и то, откуда взят каждый (``SOURCE_*``).
    hosts: tuple[str, ...]
    sources: tuple[str, ...]
    # Пояснение для отчёта: откуда список и чем он ограничен.
    note: str

    def source_of(self, host: str) -> str:
        return dict(zip(self.hosts, self.sources, strict=False)).get(host, "")


def combine(
    player: Sequence[str], mapped: str, *, page_failure: str = "", limit: int = VIDEO_SERVERS
) -> VideoNodes:
    """Узлы плеера, затем узел редиректора (если он не повторяет их), иначе общий адрес.

    ``page_failure`` — почему страницы нет («страница YouTube не открылась»): без него узлы плеера
    описываются как полученные от YouTube.
    """
    hosts = list(dict.fromkeys(player))[:limit]
    sources = [SOURCE_PLAYER] * len(hosts)
    if mapped and mapped not in hosts:
        hosts.append(mapped)
        sources.append(SOURCE_REDIRECTOR)
    if player and mapped:
        note = (
            "адрес видеосервера получен от YouTube и совпал с ответом redirector.googlevideo.com"
            if mapped in player
            else "адрес видеосервера получен от YouTube; узел сети по redirector.googlevideo.com добавлен запасным"
        )
    elif player:
        note = "адрес видеосервера получен от YouTube"
    elif mapped:
        note = f"{page_failure or 'YouTube не назвал видеосервер'}; взят узел вашей сети по ответу redirector.googlevideo.com"
    else:
        return VideoNodes(
            (GOOGLEVIDEO_FALLBACK_HOST,),
            (SOURCE_FALLBACK,),
            f"{page_failure or 'YouTube не назвал видеосервер'}, поэтому проверяем общий адрес видеосерверов",
        )
    return VideoNodes(tuple(hosts), tuple(sources), note)


def _mapped_host(run) -> str:
    """Узел вашей сети по ответу редиректора. Пусто — не ответил или ответ не похож на настоящий."""
    from blockcheck.googlevideo_discovery import fetch_mapped_host

    return fetch_mapped_host(timeout=MAPPING_TIMEOUT_S, cancelled=run.dns_cancelled)


def _player_hosts(run) -> tuple[tuple[str, ...], str]:
    """Узлы со страницы ролика. (узлы, почему страницы нет — пусто, если она была)."""
    from blockcheck.googlevideo_discovery import extract_googlevideo_hosts

    found: list[str] = []
    # Когда начали читать страницу и вышел ли срок чтения.
    reading: list[float] = []
    late: list[bool] = []

    def _done(body: bytes) -> bool:
        if b"googlevideo" in body:
            hosts = extract_googlevideo_hosts(body.decode("utf-8", errors="ignore"))
            if hosts:
                found.extend(hosts[:VIDEO_SERVERS])
                return True
        if not reading:
            reading.append(time.monotonic())
        if time.monotonic() - reading[0] >= DISCOVERY_READ_BUDGET_S:
            late.append(True)
            return True
        return False

    # Адрес YouTube — тот, которым воспользовался бы браузер.
    candidates = list(net_access.browser_ipv4(run, YOUTUBE_HOST))
    for ip in candidates[:1]:
        # Имя видеосервера ищется в самой странице: её нужно распаковать.
        page = dict(timeout=DISCOVERY_TIMEOUT, read_limit=_WATCH_PAGE_MAX_BYTES, body_done=_done, decode_body=True)
        result = net_access.fetch(run, YOUTUBE_HOST, ip, _WATCH_PAGE, **page)
        if found:
            return tuple(found), ""
        if late:
            return (), "страница YouTube грузилась слишком медленно"
        if result.ok:
            return (), "YouTube не назвал видеосервер"
    return (), "страница YouTube не открылась"


def discover(run, *, player: Callable | None = None, mapped: Callable | None = None) -> VideoNodes:
    """Узлы, которые YouTube отдаёт этой сети. ``player`` и ``mapped`` — подмена источников для тестов."""
    mapped_host = (mapped or _mapped_host)(run)
    hosts, failure = (player or _player_hosts)(run)
    return combine(hosts, mapped_host, page_failure=failure)


def owner_note(run, ip: str) -> str:
    """Чья сеть у узла: кэш Google внутри провайдера или сам Google. Пусто — владельца узнать не удалось."""
    from utils.ip_owner import lookup_ip_owner

    try:
        owner = lookup_ip_owner(ip, lambda name, rtype: net_access.doh_ask(run, name, rtype))
    except Exception:
        return ""
    if owner is None or not owner.asn:
        return ""
    name = f"AS{owner.asn}" + (f" ({owner.owner})" if owner.owner else "")
    if owner.asn in _GOOGLE_ASNS:
        return f"узел в сети Google, {name}"
    return f"узел в сети провайдера, {name}: это кэш Google внутри сети, а не сам Google"


def spare_note(first, spare) -> str:
    """Сравнение двух узлов: если первый отвечает с паузами, а запасной быстро, дело в самом узле."""
    first_pause = first.transfer is not None and first.transfer.level == "warn"
    spare_pause = spare.transfer is not None and spare.transfer.level == "warn"
    if spare.transfer is None or spare.transfer.code == "unknown":
        return ""
    if first_pause and not spare_pause:
        return f"запасной видеосервер {spare.host} отвечает быстро — дело в первом узле, а не в вашей линии"
    if first_pause and spare_pause:
        return f"запасной видеосервер {spare.host} тоже отвечает медленно — дело не в одном узле"
    return ""
