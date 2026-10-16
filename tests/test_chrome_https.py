"""Запрос «как Chrome»: исход по кодам libcurl без сети (настоящий curl подменён).

Настоящие сокеты (молчащий сервер, закрытый порт, сброс при рукопожатии) — в
tests/test_diagnostics_verdict.py::TlsProbeFailureTests и tests/test_run_stop.py.
"""

from __future__ import annotations

import errno
import unittest
from unittest.mock import patch

from diagnostics import tls_probe
from utils import chrome_https
from utils.socket_cancel import SocketCancel

WRITE_ERROR = 0xFFFFFFFF


class _Opt:
    URL = "url"
    RESOLVE = "resolve"
    CAINFO = "cainfo"
    HTTP_CONTENT_DECODING = "decoding"
    CONNECTTIMEOUT_MS = "connect_ms"
    LOW_SPEED_LIMIT = "low_limit"
    LOW_SPEED_TIME = "low_time"
    TIMEOUT_MS = "timeout_ms"
    HEADERFUNCTION = "header_fn"
    WRITEFUNCTION = "write_fn"
    VERBOSE = "verbose"
    DEBUGFUNCTION = "debug_fn"
    NOSIGNAL = "nosignal"
    NOBODY = "nobody"
    HTTPHEADER = "headers"


class _Info:
    OS_ERRNO = "os_errno"
    RESPONSE_CODE = "response_code"
    NUM_CONNECTS = "num_connects"


class _CurlError(Exception):
    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message or f"curl: ({code})")
        self.code = code


class _Script:
    """Что «пришло по сети»: события по порядку и чем закончилось."""

    def __init__(self, *steps, error: int = 0, os_errno: int = 0, message: str = "") -> None:
        self.steps = steps
        self.error = error
        self.message = message
        self.os_errno = os_errno
        self.options: dict = {}
        self.impersonated = ""


def _api(script: _Script):
    class _Curl:
        def impersonate(self, target, default_headers=True):
            script.impersonated = target

        def setopt(self, option, value):
            script.options[option] = value

        def perform(self, clear_headers=True, clear_resolve=True):
            script.cleared = (clear_headers, clear_resolve)
            for kind, value in script.steps:
                if kind == "debug":
                    script.options[_Opt.DEBUGFUNCTION](*value)
                elif kind == "header":
                    script.options[_Opt.HEADERFUNCTION](value)
                elif kind == "body":
                    if script.options[_Opt.WRITEFUNCTION](value) == WRITE_ERROR:
                        raise _CurlError(23)
            if script.error:
                raise _CurlError(script.error, script.message)

        def getinfo(self, info):
            return script.os_errno if info == _Info.OS_ERRNO else 0

        def close(self):
            pass

    return chrome_https._CurlApi(_Curl, _CurlError, _Opt, _Info, WRITE_ERROR)


HELLO = ("debug", (6, b"\x16\x03\x01"))
SECURED = ("debug", (0, b"SSL connection using TLSv1.3 / TLS_AES_128_GCM_SHA256"))
REQUEST = ("debug", (2, b"GET / HTTP/2"))
OK_HEADER = ("header", b"HTTP/2 200 \r\n")


def _get(script: _Script, **kwargs):
    with patch.object(chrome_https, "_curl_api", return_value=_api(script)):
        return chrome_https.chrome_get("example.com", kwargs.pop("ip", "203.0.113.5"), timeout=1.0, **kwargs)


class ChromeHttpsTests(unittest.TestCase):
    def test_request_is_pinned_to_the_address_and_looks_like_chrome(self) -> None:
        script = _Script(HELLO, SECURED, REQUEST, OK_HEADER, ("body", b"x" * 10))
        result = _get(script, ip="2001:db8::5", read_limit=100)

        self.assertEqual(script.impersonated, chrome_https.CHROME_TARGET)
        # Chrome на Windows с русским языком, а не профиль с Mac.
        self.assertIn(b'sec-ch-ua-platform: "Windows"', script.options[_Opt.HTTPHEADER])
        self.assertEqual(script.options[_Opt.RESOLVE], [b"example.com:443:[2001:db8::5]"])
        # Тело не распаковывается: размер — байты на проводе.
        self.assertEqual(script.options[_Opt.HTTP_CONTENT_DECODING], 0)
        self.assertEqual((result.kind, result.status, result.body_size, result.tls_version), ("ok", 200, 10, "TLSv1.3"))
        # Списки заголовков и адреса живут до закрытия соединения, а не до конца запроса.
        self.assertEqual(script.cleared, (False, False))

    def test_page_content_can_be_asked_unpacked(self) -> None:
        script = _Script(HELLO, SECURED, OK_HEADER)
        _get(script, decode_body=True)
        self.assertEqual(script.options[_Opt.HTTP_CONTENT_DECODING], 1)

    def test_reading_stops_at_the_limit_without_calling_it_a_failure(self) -> None:
        result = _get(_Script(HELLO, SECURED, OK_HEADER, ("body", b"x" * 500), ("body", b"y" * 500)), read_limit=400)
        self.assertEqual((result.kind, result.body_cut, result.body_size), ("ok", False, 500))

    def test_reset_in_the_middle_of_the_body_is_a_cut(self) -> None:
        result = _get(_Script(HELLO, SECURED, OK_HEADER, ("body", b"x" * 16_000), error=56), read_limit=40_000)
        self.assertEqual((result.kind, result.body_cut, result.body_size), ("ok", True, 16_000))

    def test_stop_in_the_middle_of_the_body_is_not_a_freeze(self) -> None:
        """Лимит времени снял медленную загрузку после 16 КБ — это не обрыв ТСПУ."""
        cancel = SocketCancel()

        script = _Script(HELLO, SECURED, OK_HEADER, ("body", b"x" * 16_000), error=28)
        original = script.steps

        def steps():
            yield from original[:-1]
            cancel.cancel()
            yield original[-1]

        script.steps = steps()
        result = _get(script, read_limit=64_000, cancel=cancel)
        self.assertEqual(result.kind, tls_probe.KIND_CANCELLED)
        self.assertFalse(result.body_cut)

    def test_stages_of_failure(self) -> None:
        # Сброс посреди рукопожатия: код системы говорит «сброс».
        reset = _get(_Script(HELLO, error=35, os_errno=errno.ECONNRESET))
        self.assertEqual((reset.kind, reset.stage), (tls_probe.KIND_RESET, tls_probe.STAGE_TLS))
        # Рукопожатие не сошлось без сброса — ошибка шифрования.
        tls = _get(_Script(HELLO, error=35))
        self.assertEqual(tls.kind, tls_probe.KIND_TLS)
        # Пустой ответ после рукопожатия — сброс.
        empty = _get(_Script(HELLO, SECURED, REQUEST, error=52))
        self.assertEqual((empty.kind, empty.stage), (tls_probe.KIND_RESET, tls_probe.STAGE_READ))
        # Молчание после приветствия — таймаут шифрования, а не «не подключились».
        silent = _get(_Script(HELLO, error=28))
        self.assertEqual((silent.kind, silent.stage), (tls_probe.KIND_TIMEOUT, tls_probe.STAGE_TLS))

    def test_connect_failures_are_told_apart(self) -> None:
        refused = _get(_Script(error=7, os_errno=errno.ECONNREFUSED))
        self.assertEqual((refused.kind, refused.connect_fail), (tls_probe.KIND_CONNECT, "refused"))
        silent = _get(_Script(error=28))
        self.assertEqual((silent.kind, silent.connect_fail), (tls_probe.KIND_CONNECT, "timeout"))

    def test_wrong_certificate_is_named(self) -> None:
        script = _Script(HELLO, error=60, message="SSL: no alternative certificate subject name matches target hostname")
        result = _get(script)
        self.assertEqual((result.kind, result.cert_problem), (tls_probe.KIND_CERT, "сертификат выдан другому сайту"))

if __name__ == "__main__":
    unittest.main()
