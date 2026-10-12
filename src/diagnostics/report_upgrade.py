"""Версия формата отчёта BlockCheck и перевод старых отчётов в новый вид.

Отчёт хранится в файле и в истории, а открывается и через месяц — новой версией
программы. Всё, что знает о старых видах отчёта, живёт только здесь: экран и
карточки работают с отчётом текущей версии и ничего не угадывают.

Версии:

1. Отчёты до появления версии. Строка итога не говорила, о каком она сайте и из
   какого раздела: экран угадывал это по тексту фразы.
2. Строка итога несёт ``site`` (ключ сайта из ``services``) и ``section`` (раздел
   отчёта, из которого она выведена); кнопку «Telegram Proxy» ставит движок.
"""

from __future__ import annotations

import re

__all__ = ["REPORT_VERSION", "upgrade"]

REPORT_VERSION = 2

# Вид проблемы → раздел отчёта, когда сайта у неё нет.
_KIND_SECTIONS = {"voice": "voice", "system": "system", "cut16": "freeze", "quic": "quic"}
_FOR_SITES = re.compile(r"^(.*?) для: (.+)$")
# Виды, при которых закрытому Telegram советовали встроенный прокси.
_TELEGRAM_PROXY_KINDS = frozenset({"ip", "other", "noconnect"})


def upgrade(report: dict | None) -> dict | None:
    """Отчёт текущей версии. Отчёт уже нужной версии возвращается как есть, без копии."""
    if not isinstance(report, dict):
        return report
    try:
        version = int(report.get("report_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= REPORT_VERSION:
        return report
    services = [item for item in report.get("services") or () if isinstance(item, dict)]
    problems = [_problem_v2(item, services, report) for item in report.get("problems") or () if isinstance(item, dict)]
    return {**report, "problems": problems, "report_version": REPORT_VERSION}


def _problem_v2(problem: dict, services: list[dict], report: dict) -> dict:
    """Строка итога версии 1 → 2: сайт и раздел по старым правилам экрана."""
    upgraded = dict(problem)
    kind = str(problem.get("kind") or "other")
    text = str(problem.get("text") or "")
    site = "" if problem.get("site") else _guess_site(problem, services)
    if site:
        upgraded["site"] = site
    if not problem.get("section"):
        if site:
            upgraded["section"] = "services"
        elif kind == "dns":
            upgraded["section"] = "dns_servers" if report.get("dns_servers") else "dns"
        elif kind == "network":
            upgraded["section"] = "ipv6" if text.startswith("IPv6") else "network"
        elif kind in _KIND_SECTIONS:
            upgraded["section"] = _KIND_SECTIONS[kind]
    if not problem.get("action") and kind in _TELEGRAM_PROXY_KINDS:
        if "telegram" in f"{problem.get('title') or ''} {text}".lower():
            upgraded["action"] = "telegram_proxy"
    return upgraded


def _guess_site(problem: dict, services: list[dict]) -> str:
    """Сайт строки старого отчёта: по названию, по адресу или по первому слову фразы."""
    kind = str(problem.get("kind") or "other")
    text = str(problem.get("text") or "")
    target = str(problem.get("target") or "")
    names = {str(problem.get("title") or ""), text.split(" ", 1)[0].rstrip(":,")}
    if kind == "quic":
        # «QUIC … для: YouTube, X. …» — подробности на карточке первого названного сайта.
        match = _FOR_SITES.match(text.partition(". ")[0])
        if match is not None:
            names.add(match.group(2).split(",")[0].strip())
    names.discard("")
    for service in services:
        hosts = {str(item.get("host") or "") for item in service.get("targets") or ()}
        if str(service.get("label") or "") in names or (target and target in hosts):
            return str(service.get("key") or "")
    return ""
