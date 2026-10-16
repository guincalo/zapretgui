"""Сайт по четырём дорогам отдельно: «как Chrome», TLS 1.2, TLS 1.3 и обычный HTTP.

Основная проверка сама открывает сайт так, как его открыл бы Chrome
(``utils.chrome_https``) — это строка **«Как Chrome»**, она берётся из её
итога, отдельной пробы для неё нет. Остальные дороги пробуются, только если
сайт не открылся (``utils.probe_gate``: разбор — по сигналу беды):

- **TLS 1.2** и **TLS 1.3** — простое приветствие только этой версией, каким
  ходят программы. Фильтр иногда режет лишь одну из них. А если простое
  приветствие проходит, а браузерное — нет, фильтр режет по «почерку»
  браузера (``CODE_FINGERPRINT``): в Chrome сайт не открывается, хотя к самому
  серверу дорога есть.
- **HTTP** (порт 80, без шифрования) — отвечает ли сайт и не подставляет ли
  провайдер страницу о блокировке.

Выводы осторожные. Отказ самого сервера («такой версии у меня нет») — не
блокировка, а справка. Порт 80 может быть закрыт у самого сайта, поэтому
молчание на нём — «не ответил», а не «заблокирован».
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from diagnostics.block_cause import (
    HELLO_ALERT,
    HELLO_CANCELLED,
    HELLO_CONNECT,
    HELLO_ERROR,
    HELLO_OK,
    HELLO_RESET,
    HELLO_TIMEOUT,
    TLS_1_2,
    TLS_1_3,
    HelloResult,
    HttpFacts,
    STAGGER_S,
    http_probe,
    later,
    stub_reason,
    tls_hello,
)
from utils.socket_cancel import SocketCancel

__all__ = [
    "CODE_FINGERPRINT",
    "HINTS",
    "PROTO_BROWSER",
    "PROTO_HTTP",
    "PROTO_TLS12",
    "PROTO_TLS13",
    "STATE_FAIL",
    "STATE_INFO",
    "STATE_OK",
    "STATE_UNKNOWN",
    "ProtocolFacts",
    "ProtocolLine",
    "chrome_line",
    "collect",
    "judge",
]

PROTO_TLS12 = "tls12"
PROTO_TLS13 = "tls13"
PROTO_BROWSER = "browser"
PROTO_HTTP = "http"

# Простое приветствие проходит, а браузерное — нет: режут по «почерку» браузера.
CODE_FINGERPRINT = "fingerprint"
# Слово строки «Как Chrome», когда соединение оборвали или заглушили (а не, скажем, чужой сертификат).
WORD_CUT = "режется"

STATE_OK = "ok"
STATE_FAIL = "fail"
# Не блокировка и не успех: сервер сам так устроен.
STATE_INFO = "info"
STATE_UNKNOWN = "unknown"

_TITLES = {PROTO_TLS12: "TLS 1.2", PROTO_TLS13: "TLS 1.3", PROTO_BROWSER: "Как Chrome", PROTO_HTTP: "HTTP"}


@dataclass(frozen=True, slots=True)
class ProtocolFacts:
    host: str
    ip: str
    tls12: HelloResult | None = None
    tls13: HelloResult | None = None
    http: HttpFacts | None = None
    # За сколько ответил порт 80. None — не ответил.
    http_ms: float | None = None
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ProtocolLine:
    key: str
    title: str
    state: str
    # Одно-два слова для метки на карточке: «работает», «сброс», «заглушка».
    word: str
    # Полная фраза для подробностей.
    text: str
    ms: float | None = None
    # Особый вывод строки (``CODE_FINGERPRINT``). Пусто — обычная строка.
    code: str = ""


def _timed_http(host: str, ip: str, cancel: SocketCancel) -> tuple[HttpFacts, float | None]:
    started = time.perf_counter()
    facts = http_probe(host, ip, cancel=cancel)
    return facts, ((time.perf_counter() - started) * 1000.0 if facts.status is not None else None)


def collect(host: str, ip: str, *, submit: Callable, cancel: SocketCancel) -> ProtocolFacts:
    """Три пробы к адресу сайта, который не открылся: TLS 1.2, TLS 1.3 и HTTP."""
    # Два шифрованных соединения с одним именем уходят не разом, а с шагом: пачка одновременных
    # соединений к одному сайту сама бывает поводом для фильтра придержать их все.
    tls12 = submit(tls_hello, ip, host, cancel=cancel, version=TLS_1_2)
    tls13 = submit(later, STAGGER_S, cancel, tls_hello, ip, host, cancel=cancel, version=TLS_1_3)
    http = submit(_timed_http, host, ip, cancel)
    http_facts, http_ms = http.result()
    return ProtocolFacts(
        host=host,
        ip=ip,
        tls12=tls12.result(),
        tls13=tls13.result(),
        http=http_facts,
        http_ms=http_ms,
        cancelled=cancel.cancelled,
    )


def _ms(value: float | None) -> str:
    if value is None:
        return ""
    return "меньше 1 мс" if value < 1 else f"{round(value)} мс"


def _tls_line(key: str, result: HelloResult | None) -> ProtocolLine | None:
    title = _TITLES[key]
    if result is None or result.kind == HELLO_CANCELLED:
        return None
    if result.kind == HELLO_OK:
        return ProtocolLine(key, title, STATE_OK, "работает", f"шифрование установилось за {_ms(result.ms)}", result.ms)
    if result.kind == HELLO_ALERT:
        return ProtocolLine(
            key, title, STATE_INFO, "нет у сервера", "сервер ответил отказом: этой версией он не работает — это не блокировка"
        )
    if result.kind == HELLO_RESET:
        return ProtocolLine(
            key, title, STATE_FAIL, "сброс", "соединение оборвано сразу после приветствия — так фильтр рвёт соединения с этим сайтом"
        )
    if result.kind == HELLO_TIMEOUT:
        return ProtocolLine(
            key, title, STATE_FAIL, "молчит", "на приветствие никто не ответил — так фильтр молча глушит соединения с этим сайтом"
        )
    if result.kind == HELLO_CONNECT:
        return ProtocolLine(key, title, STATE_UNKNOWN, "нет соединения", "не удалось соединиться с адресом")
    if result.kind == HELLO_ERROR:
        return ProtocolLine(key, title, STATE_UNKNOWN, "не проверено", "шифрование не установилось, причину определить не удалось")
    return ProtocolLine(key, title, STATE_FAIL, "чужой ответ", "вместо шифрования пришло что-то другое: ответил не сервер")


def chrome_line(result) -> ProtocolLine | None:
    """Строка «Как Chrome» — итог основной проверки (``tls_probe.ProbeResult``), отдельной пробы нет."""
    from diagnostics import tls_probe

    title = _TITLES[PROTO_BROWSER]
    if result is None or result.kind == tls_probe.KIND_CANCELLED:
        return None
    if result.kind == tls_probe.KIND_CERT:
        return ProtocolLine(PROTO_BROWSER, title, STATE_FAIL, "чужой сертификат", f"сервер предъявил чужой сертификат: {result.cert_problem}")
    if result.kind == tls_probe.KIND_OK or result.status is not None:
        return ProtocolLine(
            PROTO_BROWSER, title, STATE_OK, "проходит", f"сайт отвечает так, как ответил бы Chrome, за {_ms(result.elapsed_ms)}", result.elapsed_ms
        )
    if result.kind == tls_probe.KIND_CONNECT:
        return ProtocolLine(PROTO_BROWSER, title, STATE_UNKNOWN, "нет соединения", "не удалось соединиться с адресом")
    if result.kind in (tls_probe.KIND_RESET, tls_probe.KIND_TIMEOUT, tls_probe.KIND_TLS):
        how = "соединение сбрасывают" if result.kind == tls_probe.KIND_RESET else "ответа нет" if result.kind == tls_probe.KIND_TIMEOUT else "шифрование не устанавливается"
        return ProtocolLine(PROTO_BROWSER, title, STATE_FAIL, WORD_CUT, f"на соединение, какое открывает Chrome, {how}")
    return ProtocolLine(PROTO_BROWSER, title, STATE_UNKNOWN, "не проверено", "соединение не удалось, причину определить не удалось")


def _fingerprint(chrome: ProtocolLine, facts: ProtocolFacts) -> ProtocolLine:
    """Chrome режут, а простое приветствие к тому же адресу с тем же именем проходит — режут по «почерку»."""
    plain = [item.kind for item in (facts.tls12, facts.tls13) if item is not None]
    if chrome.word != WORD_CUT or HELLO_OK not in plain:
        return chrome
    return ProtocolLine(
        chrome.key,
        chrome.title,
        STATE_FAIL,
        WORD_CUT,
        f"{chrome.text}, а простое приветствие к тому же адресу с тем же именем проходит. Похоже на блокировку "
        "по «почерку» браузера: фильтр узнаёт Chrome по первому пакету соединения, другие программы "
        "к сайту пускает",
        code=CODE_FINGERPRINT,
    )


def _http_line(facts: ProtocolFacts) -> ProtocolLine | None:
    title = _TITLES[PROTO_HTTP]
    http = facts.http
    if http is None:
        return None
    if http.status is None:
        # Порт 80 у многих сайтов закрыт самим сайтом.
        return ProtocolLine(PROTO_HTTP, title, STATE_UNKNOWN, "не ответил", "порт 80 не ответил — у сайта он может быть закрыт")
    stub = stub_reason(facts.host, http)
    if stub:
        return ProtocolLine(PROTO_HTTP, title, STATE_FAIL, "заглушка", f"страница провайдера о блокировке: {stub}", facts.http_ms)
    took = _ms(facts.http_ms)
    if 300 <= http.status < 400:
        return ProtocolLine(
            PROTO_HTTP, title, STATE_OK, "переход", f"сайт отвечает переходом (код {http.status}) за {took}", facts.http_ms
        )
    return ProtocolLine(PROTO_HTTP, title, STATE_OK, f"код {http.status}", f"сайт отвечает, код {http.status}, за {took}", facts.http_ms)


def judge(facts: ProtocolFacts | None, chrome: ProtocolLine | None = None) -> tuple[ProtocolLine, ...]:
    """Строки по каждой дороге. ``chrome`` — строка основной проверки (``chrome_line``).

    Без проб (сайт открылся или их сняли) остаётся одна строка «Как Chrome».
    """
    if facts is None or facts.cancelled:
        return (chrome,) if chrome is not None else ()
    lines = (
        _tls_line(PROTO_TLS12, facts.tls12),
        _tls_line(PROTO_TLS13, facts.tls13),
        _fingerprint(chrome, facts) if chrome is not None else None,
        _http_line(facts),
    )
    found = tuple(line for line in lines if line is not None)
    states = {line.key: line.state for line in found}
    if states.get(PROTO_TLS12) == STATE_FAIL and states.get(PROTO_TLS13) == STATE_OK:
        found = tuple(_old_hello_only(line) if line.key == PROTO_TLS12 else line for line in found)
    return found


def _old_hello_only(line: ProtocolLine) -> ProtocolLine:
    """TLS 1.2 режут, а TLS 1.3 к тому же сайту проходит: объяснить, кому это мешает и что делать.

    Так выглядит обход, который закрывает не все приветствия (отчёт владельца 2026-10-13:
    простое разрезание пропускало к youtube.com только TLS 1.3). Красной строка остаётся —
    дорога и правда закрыта, — но без пояснения она читалась как «сайт заблокирован».
    """
    how = "молча глушит (ответа нет)" if line.word == "молчит" else "обрывает"
    return ProtocolLine(
        line.key,
        line.title,
        line.state,
        line.word,
        f"приветствие этой версии фильтр {how}, а TLS 1.3 к тому же сайту проходит. Браузеру это не мешает: он берёт "
        "TLS 1.3. Мешает старым программам и телевизорам — им поможет другой пресет или «Подбор стратегии»",
        line.ms,
        line.code,
    )


# Что это за дорога — одной фразой для человека, который видит слово впервые.
HINTS = {
    PROTO_TLS12: "TLS 1.2 — прежняя версия шифрования. Так подключаются старые программы и устройства; браузеры почти всегда берут TLS 1.3.",
    PROTO_TLS13: "TLS 1.3 — нынешняя версия шифрования. Здесь её проверяют простым соединением, каким ходят программы, а не браузер.",
    PROTO_BROWSER: "Основная проверка: сайт открывается так, как его открыл бы Chrome, — с тем же началом соединения. Эта строка ближе всего к тому, что вы увидите в браузере.",
    PROTO_HTTP: "Обычное соединение без шифрования (порт 80). Сайты отвечают на него переходом на защищённый адрес — это норма.",
}
