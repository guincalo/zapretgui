"""Уровни итога проверки как строки отчёта — одно правило для движка, истории и экрана.

В отчёте и в истории уровень лежит строкой (``"ok"``, ``"warn"``, ``"fail"``,
``"unknown"``) — значением ``diagnostics.verdict.Level``. Что считать «сайт
открывается» и в каком порядке показывать уровни, решается только здесь: раньше
те же правила были переписаны в истории, сравнении, итоге и таблице прошлых
проверок, и стоило одной копии разойтись — счётчики показывали разное.

Модуль без зависимостей: его берут и лёгкие модули (история, настройки), и экран.
"""

from __future__ import annotations

__all__ = ["FAIL", "LEVELS", "OK", "OPEN_LEVELS", "UNKNOWN", "WARN", "is_open", "level_rank", "worst"]

OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"

# По важности: сначала то, что сломано, потом оговорки, потом неизвестное.
LEVELS = (FAIL, WARN, UNKNOWN, OK)
_RANK = {level: rank for rank, level in enumerate(LEVELS)}

# Сайт открывается: всё хорошо или открывается с оговорками.
OPEN_LEVELS = frozenset({OK, WARN})


def _text(level: object) -> str:
    # Принимает и строку отчёта, и ``Level`` движка.
    return str(getattr(level, "value", level) or "")


def level_rank(level: object) -> int:
    """Место уровня в порядке важности; неизвестное значение — в самый конец."""
    return _RANK.get(_text(level), len(LEVELS))


def is_open(level: object) -> bool:
    return _text(level) in OPEN_LEVELS


def worst(levels) -> str:
    """Самый важный из уровней; пустой список — ``ok``."""
    present = [_text(level) for level in levels if _text(level) in _RANK]
    return min(present, key=level_rank) if present else OK
