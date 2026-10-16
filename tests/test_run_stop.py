"""«Стоп» и общий срок прогона срабатывают сразу, чем бы ни был занят главный поток."""

from __future__ import annotations

import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from blockcheck import stun_tester
from diagnostics import sections, udp_burst, voice_check
from diagnostics.run_context import Run
from utils.net_resolve import DNSTimeoutError
from utils.socket_cancel import SocketCancel


class WatcherTests(unittest.TestCase):
    def test_stop_closes_sockets_without_anyone_asking(self) -> None:
        pressed = threading.Event()
        run = Run(pressed.is_set, workers=2)
        self.addCleanup(run.close)

        pressed.set()

        # Никто не зовёт run.stopped(): отмену ставит наблюдатель.
        self.assertTrue(run.probe_cancel._event.wait(1.0))
        self.assertTrue(run.probe_cancel.cancelled)

    def test_deadline_closes_sockets_without_anyone_asking(self) -> None:
        run = Run(None, workers=2, deadline=0.05)
        self.addCleanup(run.close)

        self.assertTrue(run.probe_cancel._event.wait(1.0))
        self.assertTrue(run.timed_out)

    def test_watcher_ends_with_the_run(self) -> None:
        run = Run(None, workers=2)
        watchers = [thread for thread in threading.enumerate() if thread.name == "diag-watch" and thread.is_alive()]
        run.close()

        for thread in watchers:
            thread.join(1.0)
        self.assertFalse(any(thread.is_alive() for thread in watchers))


class RegistryWaitTests(unittest.TestCase):
    def test_stop_ends_the_wait_for_the_registry_at_once(self) -> None:
        hold = threading.Event()
        self.addCleanup(hold.set)
        with (
            patch.object(sections.registry, "refresh", side_effect=lambda *_args: hold.wait(10)),
            patch.object(sections.registry, "load", return_value="на диске"),
        ):
            wait = sections.start_registry()
            cancel = SocketCancel()
            threading.Timer(0.1, cancel.cancel).start()
            started = time.monotonic()
            index = wait(8.0, cancel)

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(index, "на диске")


class StunCancelTests(unittest.TestCase):
    def test_cancelled_stun_says_nothing_about_udp(self) -> None:
        cancel = SocketCancel()
        cancel.cancel()
        address = [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.7", 3478))]
        with patch.object(stun_tester, "resolve_addrinfo", return_value=address):
            result = stun_tester.test_stun("stun.example", cancel=cancel)

        self.assertEqual(result.error_code, "CANCELLED")
        answered, _text, decided = voice_check._describe(result)
        self.assertEqual((answered, decided), (False, False))

    def test_voice_check_hands_the_runs_cancel_to_each_server(self) -> None:
        cancel = SocketCancel()
        seen = []

        def submit(fn, *args, **kwargs):
            seen.append(kwargs.get("cancel"))
            return SimpleNamespace()

        voice_check.check_voice(submit, lambda _future: SimpleNamespace(status="ok", error_code=""), cancel=cancel)

        self.assertTrue(seen)
        self.assertTrue(all(item is cancel for item in seen))


class BurstResolveTests(unittest.TestCase):
    def test_server_name_is_resolved_with_a_deadline(self) -> None:
        with patch.object(udp_burst, "resolve_addrinfo", side_effect=DNSTimeoutError("медленно")) as resolve:
            self.assertIsNone(udp_burst.send_burst("stun.example", 3478))

        self.assertEqual(resolve.call_args.kwargs["timeout"], udp_burst.RESOLVE_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()


class TlsDeadlineTests(unittest.TestCase):
    def test_silent_handshake_ends_by_the_request_deadline(self) -> None:
        """Сервер принял соединение и молчит: запрос «как Chrome» сдаётся в свой срок, не позже."""
        from diagnostics import tls_probe
        from utils.chrome_https import chrome_get

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        result = chrome_get("example.com", "127.0.0.1", timeout=0.3, port=listener.getsockname()[1])

        self.assertEqual((result.kind, result.stage), (tls_probe.KIND_TIMEOUT, tls_probe.STAGE_TLS))
        self.assertLess(result.elapsed_ms, 1500)

    def test_sites_start_one_after_another_with_a_pause(self) -> None:
        """Сайты не стартуют в одну долю секунды: между стартами — пауза из ``SITE_START_GAP_S``."""
        from diagnostics import run_context

        with patch.object(run_context, "SITE_START_GAP_S", (0.15, 0.15)):
            run = Run(None, workers=2)
            self.addCleanup(run.close)
            started = []
            for _site in range(3):
                self.assertTrue(run.wait_start_turn())
                started.append(time.monotonic())
        self.assertGreaterEqual(started[1] - started[0], 0.14)
        self.assertGreaterEqual(started[2] - started[1], 0.14)

    def test_run_warms_the_certificate_store(self) -> None:
        from diagnostics import run_context

        with patch.object(run_context, "client_context") as warm:
            run = Run(None, workers=2)
            run.pool.shutdown(wait=True)
            run.close()

        warm.assert_called_once_with()
