"""Общий словарь видов сетевого сбоя: вид определяется по коду ошибки, а не по тексту."""

from __future__ import annotations

import socket
import ssl
import unittest

from utils import net_failure as nf


def _win(code: int) -> OSError:
    # Так Windows отдаёт ошибки Winsock, которые Python не перевёл в свои классы.
    error = OSError(None, "сообщение на языке системы")
    error.winerror = code
    return error


class ClassifyTests(unittest.TestCase):
    def test_python_exception_classes(self) -> None:
        self.assertEqual(nf.classify(socket.timeout()), nf.FAILURE_TIMEOUT)
        self.assertEqual(nf.classify(TimeoutError()), nf.FAILURE_TIMEOUT)
        self.assertEqual(nf.classify(ConnectionRefusedError()), nf.FAILURE_REFUSED)
        self.assertEqual(nf.classify(ConnectionResetError()), nf.FAILURE_RESET)
        self.assertEqual(nf.classify(ConnectionAbortedError()), nf.FAILURE_RESET)
        self.assertEqual(nf.classify(BrokenPipeError()), nf.FAILURE_RESET)

    def test_windows_codes_win_over_the_message(self) -> None:
        self.assertEqual(nf.classify(_win(10060)), nf.FAILURE_TIMEOUT)
        self.assertEqual(nf.classify(_win(10061)), nf.FAILURE_REFUSED)
        self.assertEqual(nf.classify(_win(10054)), nf.FAILURE_RESET)
        self.assertEqual(nf.classify(_win(10051)), nf.FAILURE_UNREACHABLE)
        self.assertEqual(nf.classify(_win(10065)), nf.FAILURE_UNREACHABLE)

    def test_tls_failures(self) -> None:
        self.assertEqual(nf.classify(ssl.SSLCertVerificationError()), nf.FAILURE_CERT)
        self.assertEqual(nf.classify(ssl.SSLEOFError()), nf.FAILURE_CLOSED)
        self.assertEqual(nf.classify(ssl.SSLError()), nf.FAILURE_TLS)

    def test_unknown_is_other_not_a_guess(self) -> None:
        self.assertEqual(nf.classify(OSError("непонятно что")), nf.FAILURE_OTHER)
        self.assertEqual(nf.classify(ValueError()), nf.FAILURE_OTHER)

    def test_dns_wire_speaks_the_same_vocabulary(self) -> None:
        from utils import dns_wire

        self.assertIs(dns_wire.FAILURE_TIMEOUT, nf.FAILURE_TIMEOUT)
        self.assertEqual(dns_wire._failure_for(_win(10060)), nf.FAILURE_TIMEOUT)


if __name__ == "__main__":
    unittest.main()


class CancellablePauseTests(unittest.TestCase):
    """Паузы между пробами снимает «Стоп» сразу, а не по окончании паузы."""

    def test_socket_cancel_sleep_ends_at_stop(self) -> None:
        import threading
        import time

        from utils.socket_cancel import SocketCancel

        token = SocketCancel()
        self.assertTrue(token.sleep(0.01))
        threading.Timer(0.05, token.cancel).start()
        started = time.monotonic()
        self.assertFalse(token.sleep(5.0))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(token.sleep(5.0))

    def test_run_pause_notices_an_outside_stop(self) -> None:
        import time

        from diagnostics.run_context import Run

        stop = {"now": False}
        run = Run(lambda: stop["now"], workers=1)
        try:
            self.assertTrue(run.pause(0.01))
            stop["now"] = True
            started = time.monotonic()
            self.assertFalse(run.pause(5.0))
            self.assertLess(time.monotonic() - started, 1.0)
        finally:
            run.close()
