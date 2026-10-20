"""Качество передачи: как быстро сайт начинает отвечать и не отвечает ли он паузами.

Рукопожатие и обрыв после 16 КБ (``diagnostics.volume_probe``) не говорят, быстро ли
идут данные. Разбор сбоя YouTube у абонентов ТТК в октябре 2026 показал, чего не
хватало: соединение с кэшем Google устанавливалось за 5 мс и проходило все проверки,
но один запрос из восьми получал первый байт только через 2,9 с, а дальше данные шли
порциями около 1 Мбит/с. Видео при этом замирало, а проверка оставалась зелёной.

Здесь по уже снятым замерам запросов (время до первого байта и скорость тела)
решается, нет ли пауз и медленной передачи. Новых запросов модуль не делает: замеры
берутся из тех, что и так идут по соединению (``volume_probe.download``).

Признаки:

- **пауза** — один-два запроса ждали первого байта секунды, а остальные получили
  ответ за доли секунды. Так выглядит узел, которому для отдельных запросов приходится
  ждать данные откуда-то ещё, или очередь на канале;
- **медленный ответ** — обычный запрос ждёт больше секунды: медленна линия или сервер;
- **медленное тело** — ответ идёт медленнее нескольких Мбит/с.

Первый запрос по новому соединению не считается: в его время входят подключение и
рукопожатие. Сбор (замеры) и вывод (``judge``) разделены, вывод проверяется без сети.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from diagnostics.levels import OK, UNKNOWN, WARN
from diagnostics.limits import (
    TRANSFER_MIN_SAMPLES,
    TRANSFER_PAUSE_MS,
    TRANSFER_PAUSE_RATIO,
    TRANSFER_SLOW_KBPS,
    TRANSFER_SLOW_MEDIAN_MS,
    TRANSFER_SPEED_MIN_BODY,
    TRANSFER_SPEED_MIN_MS,
)

__all__ = [
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "TRANSFER_OK",
    "TRANSFER_PAUSE",
    "TRANSFER_SLOW_BODY",
    "TRANSFER_SLOW_REPLY",
    "TRANSFER_UNKNOWN",
    "RequestSample",
    "TransferVerdict",
    "judge",
    "shaky_reason",
]

TRANSFER_OK = "ok"
TRANSFER_PAUSE = "pause"
TRANSFER_SLOW_REPLY = "slow_reply"
TRANSFER_SLOW_BODY = "slow_body"
TRANSFER_UNKNOWN = "unknown"

# Насколько можно доверять выводу: сколько замеров его подтверждает.
CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_HIGH = "high"

# Столько замеров по готовому соединению подтверждают вывод уверенно.
_HIGH_SAMPLES = 6


@dataclass(frozen=True, slots=True)
class RequestSample:
    """Замер одного запроса по соединению."""

    # Байты ответа: заголовки и тело.
    received: int = 0
    # От отправки запроса до первого байта ответа; 0 — ответ не начался.
    ttfb_ms: float = 0.0
    elapsed_ms: float = 0.0
    # Сколько шло тело: от первого до последнего куска; 0 — кусков меньше двух.
    body_ms: float = 0.0
    # Запрос открыл соединение: в его время входят подключение и рукопожатие.
    fresh: bool = False
    # Ответ оборвался или замер.
    failed: bool = False


@dataclass(frozen=True, slots=True)
class TransferVerdict:
    code: str
    level: str
    text: str
    # Сколько замеров по готовому соединению участвовало в выводе.
    samples: int = 0
    ttfb_median_ms: float = 0.0
    ttfb_max_ms: float = 0.0
    # Скорость тела, КБ/с; 0 — не считалась (нет ответов с телом нужного размера).
    speed_kbps: float = 0.0
    confidence: str = CONFIDENCE_LOW

    def as_dict(self) -> dict:
        """Вид для отчёта: числа округлены, текст — для человека."""
        return {
            "code": self.code,
            "level": self.level,
            "text": self.text,
            "samples": self.samples,
            "ttfb_median_ms": round(self.ttfb_median_ms),
            "ttfb_max_ms": round(self.ttfb_max_ms),
            "speed_kbps": round(self.speed_kbps),
            "confidence": self.confidence,
        }


def _waited(count: int) -> str:
    """«1 ждал» / «2 ждали»: по числу запросов."""
    return f"{count} ждал" if count % 10 == 1 and count % 100 != 11 else f"{count} ждали"


def speed_kbps(samples: list[RequestSample]) -> float:
    """Скорость тела по ответам, у которых она что-то значит; 0 — таких нет."""
    body = [s for s in samples if s.received >= TRANSFER_SPEED_MIN_BODY and s.body_ms >= TRANSFER_SPEED_MIN_MS]
    if not body:
        return 0.0
    total_bytes = sum(s.received for s in body)
    total_ms = sum(s.body_ms for s in body)
    return (total_bytes / 1024) / (total_ms / 1000)


def judge(samples: list[RequestSample] | tuple[RequestSample, ...]) -> TransferVerdict:
    """Вывод о передаче по замерам запросов одного соединения."""
    ready = [s for s in samples if not s.fresh and s.ttfb_ms > 0]
    if len(ready) < TRANSFER_MIN_SAMPLES:
        return TransferVerdict(
            TRANSFER_UNKNOWN,
            UNKNOWN,
            "по этому соединению мало замеров, чтобы судить о скорости ответа",
            samples=len(ready),
        )
    times = sorted(s.ttfb_ms for s in ready)
    median = statistics.median(times)
    longest = times[-1]
    confidence = CONFIDENCE_HIGH if len(ready) >= _HIGH_SAMPLES else CONFIDENCE_MEDIUM
    kbps = speed_kbps(list(ready))

    # Пауза: ответы идут быстро, но одни-два запроса ждали секунды.
    fast = [t for t in times if t < TRANSFER_PAUSE_MS]
    paused = [t for t in times if t >= TRANSFER_PAUSE_MS and t >= TRANSFER_PAUSE_RATIO * max(1.0, statistics.median(fast or times))]
    if paused and len(fast) >= TRANSFER_MIN_SAMPLES - 1 and median < TRANSFER_SLOW_MEDIAN_MS:
        # Одна пауза могла быть случайной, повторная — уже закономерность.
        pause_confidence = CONFIDENCE_HIGH if len(paused) >= 2 else CONFIDENCE_MEDIUM if len(ready) >= _HIGH_SAMPLES else CONFIDENCE_LOW
        return TransferVerdict(
            TRANSFER_PAUSE,
            WARN,
            f"{_waited(len(paused))} из {len(ready)} запросов ответа до {longest / 1000:.1f} с, остальные — "
            f"около {statistics.median(fast or times):.0f} мс: сервер отвечает с паузами",
            samples=len(ready),
            ttfb_median_ms=median,
            ttfb_max_ms=longest,
            speed_kbps=kbps,
            confidence=pause_confidence,
        )
    if median >= TRANSFER_SLOW_MEDIAN_MS:
        return TransferVerdict(
            TRANSFER_SLOW_REPLY,
            WARN,
            f"обычный запрос ждёт ответа около {median / 1000:.1f} с: медленна линия или сам сервер",
            samples=len(ready),
            ttfb_median_ms=median,
            ttfb_max_ms=longest,
            speed_kbps=kbps,
            confidence=confidence,
        )
    if kbps and kbps < TRANSFER_SLOW_KBPS:
        return TransferVerdict(
            TRANSFER_SLOW_BODY,
            WARN,
            f"ответ идёт медленно: около {kbps:.0f} КБ/с",
            samples=len(ready),
            ttfb_median_ms=median,
            ttfb_max_ms=longest,
            speed_kbps=kbps,
            confidence=confidence,
        )
    return TransferVerdict(
        TRANSFER_OK,
        OK,
        f"ответы идут быстро: обычно около {median:.0f} мс до первого байта",
        samples=len(ready),
        ttfb_median_ms=median,
        ttfb_max_ms=longest,
        speed_kbps=kbps,
        confidence=confidence,
    )


def shaky_reason(verdict: TransferVerdict) -> str:
    """Что сказать про сайт, который «открывается через раз» из-за пауз; пусто — паузы нет или она не подтверждена.

    Пауза, которая повторилась или подтверждена достаточным числом замеров, делает сайт
    нестабильным: страница откроется, а видео и крупные файлы пойдут рывками. Единичная
    пауза при малом числе замеров остаётся строкой отчёта и сайт нестабильным не делает.
    """
    if verdict.code != TRANSFER_PAUSE or verdict.confidence == CONFIDENCE_LOW:
        return ""
    return f"{verdict.text}; видео и крупные файлы с такого сервера идут рывками"
