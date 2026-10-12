"""Разбор адресов целей BlockCheck — единственное место, где живёт эта логика.

Раньше один и тот же ``re.sub(r"^https?://", ...)`` был скопирован в нескольких
местах, и расхождения между копиями ломали сопоставление цели с её адресом.
"""

from __future__ import annotations

import re

__all__ = [
    "PSEUDO_SCHEMES",
    "host_of",
    "is_pseudo_target",
]


# Значения целей вида ``PING:1.1.1.1`` / ``STUN:...`` / ``TCP:16-20KB`` — не URL.
PSEUDO_SCHEMES = ("PING:", "STUN:", "TCP:")

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

def is_pseudo_target(value: str) -> bool:
    """True для служебных значений (PING:/STUN:/TCP:), которые не являются URL."""
    return str(value or "").startswith(PSEUDO_SCHEMES)


def host_of(value: str) -> str:
    """Имя хоста из значения цели: URL, ``host:port`` или голое имя.

    Служебные значения (``PING:``/``STUN:``/``TCP:``) возвращаются как есть —
    вызывающий обязан отсеять их через :func:`is_pseudo_target`.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_pseudo_target(raw):
        return raw

    raw = _SCHEME_RE.sub("", raw)
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]

    if raw.startswith("["):  # IPv6-литерал в квадратных скобках
        right = raw.find("]")
        if right > 0:
            return raw[1:right].strip().lower()

    # Порт отрезаем только у имён; голый IPv6 без скобок содержит много ":".
    if raw.count(":") == 1:
        raw = raw.rsplit(":", 1)[0]

    return raw.rstrip(".").lower().strip()
