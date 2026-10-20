"""Доля повторных передач TCP: теряет ли линия пакеты, пока идёт загрузка.

Скорость и паузы (``transfer_probe``) не говорят, почему данные идут медленно: сервер далеко, канал
узкий или линия теряет пакеты и TCP досылает потерянное. Здесь за время загрузки сравниваются два счётчика
системы: сколько сегментов TCP отправлено и сколько из них — повторы. Доля повторов — признак потерь на линии.

Счётчики общие на весь компьютер, а не на одно соединение (отдельные соединения libcurl отдаёт в ``ESTATS``
только администратору), поэтому доля показательна, когда загрузка идёт одна, как в шаге «Скорость», где
серверы качаются по очереди. Windows читает ``GetTcpStatistics``, Linux (и Android) — ``/proc/net/snmp``.
Сбор (счётчики) и вывод (``judge``) разделены, вывод проверяется без системы.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

__all__ = ["Counters", "LossVerdict", "LOSS_NONE", "LOSS_SMALL", "LOSS_HEAVY", "LOSS_UNKNOWN", "judge", "measure", "read_counters"]

LOSS_NONE = "none"
LOSS_SMALL = "small"
LOSS_HEAVY = "heavy"
LOSS_UNKNOWN = "unknown"

# Меньше сегментов — доля случайна: одна потеря на сотню пакетов не вывод.
MIN_SEGMENTS = 300
SMALL_SHARE = 0.01
HEAVY_SHARE = 0.03


@dataclass(frozen=True, slots=True)
class Counters:
    out_segments: int
    retransmitted: int


@dataclass(frozen=True, slots=True)
class LossVerdict:
    code: str
    text: str
    segments: int = 0
    retransmitted: int = 0
    share: float = 0.0

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "text": self.text,
            "segments": self.segments,
            "retransmitted": self.retransmitted,
            "share": round(self.share, 4),
        }


def _read_windows() -> Counters | None:
    import ctypes
    from ctypes import wintypes

    class MibTcpStats(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "dwRtoAlgorithm", "dwRtoMin", "dwRtoMax", "dwMaxConn", "dwActiveOpens", "dwPassiveOpens", "dwAttemptFails",
            "dwEstabResets", "dwCurrEstab", "dwInSegs", "dwOutSegs", "dwRetransSegs", "dwInErrs", "dwOutRsts", "dwNumConns",
        )]

    stats = MibTcpStats()
    try:
        status = ctypes.windll.iphlpapi.GetTcpStatistics(ctypes.byref(stats))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    return Counters(int(stats.dwOutSegs), int(stats.dwRetransSegs)) if status == 0 else None


def parse_snmp(text: str) -> Counters | None:
    """Счётчики из ``/proc/net/snmp``: строка ``Tcp:`` с названиями и следующая с числами."""
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("Tcp:"):
            continue
        parts = line.split()[1:]
        if header is None:
            header = parts
            continue
        values = dict(zip(header, parts, strict=False))
        try:
            return Counters(int(values["OutSegs"]), int(values["RetransSegs"]))
        except (KeyError, ValueError):
            return None
    return None


def _read_linux() -> Counters | None:
    try:
        with open("/proc/net/snmp", encoding="ascii", errors="replace") as handle:
            return parse_snmp(handle.read())
    except OSError:
        return None


def read_counters() -> Counters | None:
    """Счётчики TCP системы. ``None`` — прочитать нельзя (нет доступа или система другая)."""
    return _read_windows() if sys.platform == "win32" else _read_linux()


@dataclass(slots=True)
class _Window:
    before: Counters | None = None
    after: Counters | None = None

    def verdict(self) -> LossVerdict:
        if self.before is None or self.after is None:
            return judge(0, 0)
        return judge(self.after.out_segments - self.before.out_segments, self.after.retransmitted - self.before.retransmitted)


@contextmanager
def measure(read: Callable[[], Counters | None] = read_counters):
    """``with measure() as window: …загрузка…; window.verdict()`` — доля повторов за время загрузки."""
    window = _Window(before=read())
    try:
        yield window
    finally:
        window.after = read()


def judge(segments: int, retransmitted: int) -> LossVerdict:
    """Вывод по разнице счётчиков за загрузку. Новых запросов не делает."""
    segments = max(0, int(segments))
    retransmitted = max(0, int(retransmitted))
    if segments < MIN_SEGMENTS:
        return LossVerdict(LOSS_UNKNOWN, "слишком мало данных для вывода о потерях", segments, retransmitted)
    share = retransmitted / segments
    percent = f"{share * 100:.1f}".replace(".", ",")
    detail = f"повторов {percent} % ({retransmitted} из {segments} сегментов)"
    if share >= HEAVY_SHARE:
        return LossVerdict(LOSS_HEAVY, f"заметные потери: {detail} — линия теряет пакеты, TCP досылает их", segments, retransmitted, share)
    if share >= SMALL_SHARE:
        return LossVerdict(LOSS_SMALL, f"небольшие потери: {detail}", segments, retransmitted, share)
    return LossVerdict(LOSS_NONE, f"потерь почти нет: {detail}", segments, retransmitted, share)
