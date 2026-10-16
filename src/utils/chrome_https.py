"""Запрос «как Chrome»: настоящее рукопожатие и HTTP/2 браузера Chrome.

Зачем. Фильтр провайдера различает программы по первому пакету шифрованного
соединения (ClientHello): его размеру, набору и порядку расширений, ключам.
Модуль ``ssl`` Python шлёт приветствие OpenSSL — втрое короче браузерного, в
один пакет вместо двух, с ALPN только ``http/1.1``. По такому почерку
проверку легко отличить от человека. Хуже того, стратегия обхода режет и
подделывает именно этот пакет: проверенная на приветствии Python, она может
не сработать в браузере, и наоборот.

Здесь запрос идёт через ``curl_cffi``: libcurl с BoringSSL — той же
библиотекой шифрования, что у Chrome, — и её отпечатком: перемешанный
порядок расширений, постквантовый ключ, ALPS, сжатие сертификатов, HTTP/2 с
настройками Chrome и его заголовками. Что именно изображается, задаёт
``CHROME_TARGET``: менять вместе с версией ``curl_cffi``.

Исход описывается тем же ``diagnostics.tls_probe.ProbeResult``, что и у
прежней пробы, поэтому выводы диагностики и подбора стратегий не меняются.

Адрес задаётся явно (``CURLOPT_RESOLVE``): диагностика сама решает, к какому
IP идти, а имя сайта уходит в SNI и в заголовок ``:authority``, как у
браузера. Сжатый ответ по умолчанию не распаковывается: размер тела — это
байты на проводе, а по ним судят об обрыве по объёму. Кому нужно само
содержимое страницы, просит ``decode_body=True``.

Отмена: ``SocketCancel`` здесь не держит сокет (им владеет libcurl), поэтому
отмена замечается на каждом куске ответа; подключение и рукопожатие
ограничены сроком ``timeout``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from utils.net_failure import FAILURE_OTHER, FAILURE_RESET, FAILURE_TIMEOUT, classify_code
from utils.socket_cancel import SocketCancel

__all__ = ["CHROME_TARGET", "chrome_get"]

# Какой браузер изображать: самая новая версия Chrome, которую знает curl_cffi 0.16.
CHROME_TARGET = "chrome150"

# Коды ошибок libcurl (CURLcode), по которым судим об исходе.
_E_COULDNT_CONNECT = 7
_E_WRITE_ERROR = 23  # мы сами прервали чтение: набрали сколько нужно или «Стоп»
_E_TIMEOUT = 28
_E_SSL_CONNECT = 35
_E_GOT_NOTHING = 52
_E_SEND = 55
_E_RECV = 56
_E_PEER_VERIFY = 60
_E_HTTP2 = 16
_E_HTTP2_STREAM = 92
_E_SSL_CERTPROBLEM = 58
_E_PARTIAL_FILE = 18


# Виды отладочных событий libcurl (curl_infotype), по которым виден этап соединения.
_DEBUG_TEXT = 0
_DEBUG_HEADER_OUT = 2
_DEBUG_SSL_DATA_OUT = 6


@dataclass(slots=True)
class _Progress:
    """Что успело произойти: от этого зависит, чем считать сбой.

    Замеры времени libcurl при сбое обнуляются, поэтому этап берётся из его
    отладочных событий: ушёл первый пакет шифрования — соединение есть и идёт
    рукопожатие; ушёл заголовок запроса — шифрование установлено.
    """

    status: int | None = None
    body: bytearray | None = None
    # Чтение прервали мы сами: набрали сколько нужно (``stopped_by_us``) или нажали «Стоп».
    stopped_by_us: bool = False
    cancelled: bool = False
    tls_started: bool = False
    secured: bool = False
    tls_version: str = ""

    def on_debug(self, kind: int, data: bytes) -> None:
        if kind == _DEBUG_SSL_DATA_OUT:
            self.tls_started = True
        elif kind == _DEBUG_HEADER_OUT:
            self.secured = True
        elif kind == _DEBUG_TEXT and data.startswith(b"SSL connection using "):
            self.secured = True
            self.tls_version = data[len(b"SSL connection using ") :].split(b" ", 1)[0].decode("ascii", "replace")


def _cert_problem(message: str) -> str:
    text = message.lower()
    if "hostname" in text or "subject" in text or "name" in text and "match" in text:
        return "сертификат выдан другому сайту"
    if "expired" in text or "not yet valid" in text:
        return "сертификат просрочен или ещё не действует (проверьте часы Windows)"
    if "self signed" in text or "self-signed" in text:
        return "самодельный сертификат"
    if "issuer" in text or "unable to get local" in text:
        return "сертификат выдан неизвестным центром"
    return "сертификат не прошёл проверку"


@dataclass(frozen=True, slots=True)
class _CurlApi:
    """Нужная часть ``curl_cffi`` одним набором: тесты подменяют её целиком, без сети."""

    Curl: type
    CurlError: type
    CurlOpt: type
    CurlInfo: type
    write_error: int


def _curl_api() -> _CurlApi:
    from curl_cffi import Curl, CurlError
    from curl_cffi.const import CurlInfo, CurlOpt
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR

    return _CurlApi(Curl, CurlError, CurlOpt, CurlInfo, CURL_WRITEFUNC_ERROR)


def chrome_get(
    host: str,
    ip: str,
    path: str = "/",
    *,
    timeout: float,
    port: int = 443,
    read_limit: int = 0,
    read_timeout: float = 3.0,
    body_done: Callable[[bytes], bool] | None = None,
    decode_body: bool = False,
    cancel: SocketCancel | None = None,
):
    """GET ``https://host/path`` по адресу ``ip`` с рукопожатием и заголовками Chrome.

    Параметры и исход — как у ``diagnostics.tls_probe.https_get``: ``timeout`` —
    подключение, шифрование и первый ответ; ``read_limit`` — сколько байт тела
    читать (0 — только заголовок); ``read_timeout`` — сколько ждать следующих
    данных, замершее на середине тело видно как ``body_cut``. ``body_done`` получает
    тело (без заголовков) и говорит, что читать дальше незачем; конец ответа libcurl
    видит сам. ``decode_body`` — распаковать сжатое тело (нужно содержимое страницы);
    размер тогда уже не байты на проводе и для суждения об обрыве по объёму не годится.
    """
    from diagnostics import tls_probe

    api = _curl_api()
    CurlOpt, CurlInfo = api.CurlOpt, api.CurlInfo

    token = cancel or SocketCancel()
    started = time.perf_counter()
    progress = _Progress(body=bytearray())
    curl = api.Curl()

    def _elapsed() -> float:
        return (time.perf_counter() - started) * 1000

    def _on_header(line: bytes) -> int:
        if line.startswith(b"HTTP/"):
            parts = line.split()
            # Перед окончательным ответом бывают промежуточные (100, 103): ждём настоящий.
            if len(parts) >= 2 and parts[1].isdigit() and not parts[1].startswith(b"1"):
                progress.status = int(parts[1])
        return len(line)

    def _on_body(chunk: bytes) -> int:
        body = progress.body
        body.extend(chunk)
        if token.cancelled:
            progress.cancelled = True
            return api.write_error
        if len(body) >= max(read_limit, 1) or (body_done is not None and body_done(bytes(body))):
            progress.stopped_by_us = True
            return api.write_error
        return len(chunk)

    def _result(kind: str, detail: str = "", **extra):
        if token.cancelled and (kind != tls_probe.KIND_OK or extra.get("body_cut")):
            kind = tls_probe.KIND_CANCELLED
            extra.pop("body_cut", None)
        return tls_probe.ProbeResult(
            ip=ip,
            kind=kind,
            stage=stage,
            status=progress.status,
            body_size=len(progress.body),
            tls_version=tls_version,
            elapsed_ms=_elapsed(),
            detail=detail,
            body=bytes(progress.body),
            **extra,
        )

    address = f"[{ip}]" if ":" in ip else ip
    stage = tls_probe.STAGE_CONNECT
    tls_version = ""
    try:
        if not token.cancelled:
            curl.impersonate(CHROME_TARGET, default_headers=True)
            curl.setopt(CurlOpt.URL, f"https://{host}:{int(port)}{path or '/'}".encode())
            curl.setopt(CurlOpt.RESOLVE, [f"{host}:{int(port)}:{address}".encode()])
            curl.setopt(CurlOpt.CAINFO, _ca_bundle().encode())
            # Заголовок Accept-Encoding остаётся браузерным; тело распаковывается, только
            # если нужно содержимое: иначе размер — это байты на проводе.
            curl.setopt(CurlOpt.HTTP_CONTENT_DECODING, 1 if decode_body else 0)
            curl.setopt(CurlOpt.CONNECTTIMEOUT_MS, int(max(0.2, float(timeout)) * 1000))
            # Тело замерло дольше ``read_timeout`` — обрыв (как ``body_cut`` у прежней пробы).
            curl.setopt(CurlOpt.LOW_SPEED_LIMIT, 1)
            curl.setopt(CurlOpt.LOW_SPEED_TIME, max(1, round(float(read_timeout))))
            curl.setopt(CurlOpt.TIMEOUT_MS, int((max(0.2, float(timeout)) + 60.0) * 1000))
            curl.setopt(CurlOpt.HEADERFUNCTION, _on_header)
            curl.setopt(CurlOpt.WRITEFUNCTION, _on_body)
            curl.setopt(CurlOpt.VERBOSE, 1)
            curl.setopt(CurlOpt.DEBUGFUNCTION, progress.on_debug)
            curl.setopt(CurlOpt.NOSIGNAL, 1)
        if token.cancelled:
            return _result(tls_probe.KIND_CANCELLED)
        error_code = 0
        message = ""
        try:
            curl.perform()
        except api.CurlError as error:
            error_code = int(getattr(error, "code", 0) or 0)
            message = str(error)
        # Код ошибки системы: отказ, тишина или нет дороги — текст libcurl этого не различает.
        os_failure = classify_code(int(curl.getinfo(CurlInfo.OS_ERRNO) or 0)) if error_code else ""
        stage = (
            tls_probe.STAGE_READ
            if progress.secured
            else tls_probe.STAGE_TLS
            if progress.tls_started
            else tls_probe.STAGE_CONNECT
        )
        tls_version = progress.tls_version
        if progress.status is None and error_code == 0:
            progress.status = int(curl.getinfo(CurlInfo.RESPONSE_CODE) or 0) or None
        return _classify(error_code, os_failure, message, progress, stage, _result, tls_probe)
    finally:
        curl.close()


def _classify(error_code: int, os_failure: str, message: str, progress: _Progress, stage: str, result, tls_probe):
    """Исход по коду libcurl, коду ошибки системы и тому, что успело прийти."""
    got_answer = progress.status is not None
    if progress.cancelled:
        return result(tls_probe.KIND_CANCELLED)
    if error_code == 0 or (error_code == _E_WRITE_ERROR and progress.stopped_by_us):
        if not got_answer:
            return result(tls_probe.KIND_RESET, "сервер закрыл соединение без ответа")
        return result(tls_probe.KIND_OK)
    if error_code in (_E_PEER_VERIFY, _E_SSL_CERTPROBLEM):
        return result(tls_probe.KIND_CERT, message, cert_problem=_cert_problem(message))
    if got_answer:
        # Заголовок пришёл, а тело оборвалось: сброс, замирание или обрыв HTTP/2.
        return result(tls_probe.KIND_OK, message, body_cut=True)
    if stage == tls_probe.STAGE_CONNECT:
        if error_code == _E_TIMEOUT:
            return result(tls_probe.KIND_CONNECT, "сервер не отвечает на подключение", connect_fail=FAILURE_TIMEOUT)
        return result(tls_probe.KIND_CONNECT, message, connect_fail=os_failure or FAILURE_OTHER)
    if error_code == _E_TIMEOUT or os_failure == FAILURE_TIMEOUT:
        return result(tls_probe.KIND_TIMEOUT, message)
    if os_failure == FAILURE_RESET or error_code in _RESET_CODES:
        return result(tls_probe.KIND_RESET, message)
    if error_code == _E_SSL_CONNECT:
        return result(tls_probe.KIND_TLS, message)
    return result(tls_probe.KIND_ERROR, message)


# Соединение оборвали: пустой ответ, ошибка приёма или отправки, обрыв потока HTTP/2.
_RESET_CODES = frozenset({_E_RECV, _E_SEND, _E_GOT_NOTHING, _E_HTTP2, _E_HTTP2_STREAM, _E_PARTIAL_FILE})


def _ca_bundle() -> str:
    """Корневые сертификаты: BoringSSL не читает хранилище Windows, берём certifi."""
    import certifi

    return certifi.where()
