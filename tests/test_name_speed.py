"""Замедление по имени сайта: один сервер, один файл, разные имена в приветствии."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from diagnostics import name_speed as ns
from diagnostics import sections


def _run(name: str, kbps: float | None, kind: str = ns.RUN_OK) -> ns.NameRun:
    if kbps is None:
        return ns.NameRun(name, kind)
    return ns.NameRun(name, ns.RUN_OK, int(kbps * 1024), 1.0)


class _Line:
    """Линия: скорость по имени; список — замеры по очереди."""

    def __init__(self, **speeds) -> None:
        self.speeds = {name.replace("_", "."): value for name, value in speeds.items()}
        self.asked: list[str] = []

    def fetch(self, name: str) -> ns.NameRun:
        self.asked.append(name)
        value = self.speeds.get(name, 5000.0)
        if isinstance(value, list):
            value = value.pop(0) if len(value) > 1 else value[0]
        if isinstance(value, str):
            return ns.NameRun(name, value)
        return _run(name, value)


def _verdicts(line: _Line, names) -> tuple[float | None, dict[str, ns.NameVerdict]]:
    control, verdicts = ns.judge(ns.collect(names, line.fetch))
    return control, {item.name: item for item in verdicts}


class NameSpeedTests(unittest.TestCase):
    def test_name_that_is_slow_twice_is_throttled(self) -> None:
        line = _Line(youtube_com=30.0)
        control, verdicts = _verdicts(line, ["youtube.com", "github.com"])

        self.assertEqual(control, 5000.0)
        self.assertEqual(verdicts["youtube.com"].code, ns.SLOW)
        self.assertIn("замедляют", verdicts["youtube.com"].text)
        self.assertEqual(verdicts["github.com"].code, ns.FINE)
        # Медленное имя перепроверено, и контроль в конце повторён.
        self.assertEqual(line.asked.count("youtube.com"), 2)
        self.assertEqual(line.asked[-1], ns.SERVER_HOST)

    def test_single_slow_measurement_is_not_believed(self) -> None:
        line = _Line(youtube_com=[30.0, 4000.0])
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.FINE)

    def test_line_that_sagged_gives_no_verdict(self) -> None:
        # Контроль в начале быстрый, в конце — такой же медленный, как имя: просела сама линия.
        line = _Line(**{"mirror_yandex_ru": [5000.0, 5000.0, 40.0], "youtube_com": 30.0})
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.UNKNOWN)
        self.assertIn("просела", verdicts["youtube.com"].text)

    def test_cold_first_control_does_not_hide_throttling(self) -> None:
        # Первая загрузка идёт медленнее («холодная») — в счёт идёт лучший из двух контролей.
        line = _Line(**{"mirror_yandex_ru": [300.0, 5000.0, 5000.0], "youtube_com": 30.0})
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.SLOW)

    def test_slow_but_not_much_slower_than_control_is_fine(self) -> None:
        # Медленный тариф: с именем сайта не быстрее и не медленнее, чем без него.
        line = _Line(**{"mirror_yandex_ru": 200.0, "youtube_com": 150.0})
        _control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.FINE)
        self.assertEqual(line.asked.count("youtube.com"), 1)

    def test_failed_control_gives_no_verdicts_and_asks_nothing(self) -> None:
        line = _Line(**{"mirror_yandex_ru": ns.RUN_CONNECT})
        control, verdicts = _verdicts(line, ["youtube.com"])

        self.assertEqual((control, verdicts), (None, {}))
        self.assertNotIn("youtube.com", line.asked)

    def test_name_that_does_not_connect_is_unknown_not_slow(self) -> None:
        line = _Line(youtube_com=ns.RUN_CONNECT, discord_com=ns.RUN_REFUSED)
        _control, verdicts = _verdicts(line, ["youtube.com", "discord.com"])

        self.assertEqual(verdicts["youtube.com"].code, ns.UNKNOWN)
        self.assertIn("не прошло", verdicts["youtube.com"].text)
        self.assertIn("не отдал", verdicts["discord.com"].text)

    def test_number_of_names_is_limited_and_repeats_are_dropped(self) -> None:
        line = _Line()
        names = [f"site{index}.example" for index in range(ns.MAX_NAMES + 4)]
        ns.collect([names[0], *names], line.fetch)

        asked = [name for name in line.asked if name != ns.SERVER_HOST]
        self.assertEqual(asked, names[: ns.MAX_NAMES])

    def test_stop_ends_collection(self) -> None:
        line = _Line()
        facts = ns.collect(["a.example", "b.example"], line.fetch, should_stop=lambda: len(line.asked) >= 3)

        self.assertEqual([runs[0].name for runs in facts.names], ["a.example"])

    def test_little_data_over_the_whole_time_is_a_speed_not_a_failure(self) -> None:
        crawl = ns.NameRun("x", ns.RUN_OK, 9_000, ns.SAMPLE_SECONDS)
        early = ns.NameRun("x", ns.RUN_OK, 9_000, 0.1)

        self.assertAlmostEqual(crawl.kbps, 9_000 / 1024 / ns.SAMPLE_SECONDS)
        self.assertIsNone(early.kbps)


class CutStreamTests(unittest.TestCase):
    """Поток, который пошёл и оборвался, — не «быстрая загрузка»."""

    CONTROL = ns.NameRun(ns.SERVER_HOST, ns.RUN_OK, ns.ENOUGH_BYTES, 0.2)

    def _fetch(self, by_name):
        calls: list[str] = []

        def fetch(name: str) -> ns.NameRun:
            calls.append(name)
            if name == ns.SERVER_HOST:
                return self.CONTROL
            queue = by_name[name]
            return queue.pop(0) if len(queue) > 1 else queue[0]

        return fetch, calls

    def test_stream_cut_twice_is_a_cut_by_name(self) -> None:
        # Сброс после 24 КБ за 0,05 с раньше читался как «быстро»: байты за короткое время.
        fetch, calls = self._fetch({"rr1.googlevideo.com": [ns.NameRun("rr1.googlevideo.com", ns.RUN_CUT, 24_000, 0.05)]})

        _control, verdicts = ns.judge(ns.collect(["rr1.googlevideo.com"], fetch))

        self.assertEqual(calls.count("rr1.googlevideo.com"), 2)
        self.assertEqual(verdicts[0].code, ns.CUT)
        self.assertIn("поток дважды оборвался (самое большее после 23 КБ)", verdicts[0].text)
        self.assertIsNone(ns.NameRun("x", ns.RUN_CUT, 24_000, 0.05).kbps)

    def test_single_cut_followed_by_a_full_stream_is_not_a_finding(self) -> None:
        fine = ns.NameRun("a.example", ns.RUN_OK, ns.ENOUGH_BYTES, 0.2)
        fetch, _calls = self._fetch({"a.example": [ns.NameRun("a.example", ns.RUN_CUT, 24_000, 0.05), fine]})

        _control, verdicts = ns.judge(ns.collect(["a.example"], fetch))

        self.assertEqual(verdicts[0].code, ns.FINE)

    def test_cut_while_the_line_itself_fails_is_not_blamed_on_the_name(self) -> None:
        cut = ns.NameRun("a.example", ns.RUN_CUT, 24_000, 0.05)
        facts = ns.NameFacts((self.CONTROL,), ((cut, cut),), (ns.NameRun(ns.SERVER_HOST, ns.RUN_CONNECT),))

        _control, verdicts = ns.judge(facts)

        self.assertEqual(verdicts[0].code, ns.UNKNOWN)

    def test_download_reports_a_reset_in_the_middle_as_a_cut(self) -> None:
        import socket
        import ssl
        import subprocess
        import tempfile
        import threading
        from pathlib import Path

        if not __import__("shutil").which("openssl"):
            self.skipTest("нет openssl для пробного сертификата")
        with tempfile.TemporaryDirectory() as folder:
            key, cert = Path(folder) / "k.pem", Path(folder) / "c.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                 "-days", "1", "-subj", "/CN=test"],
                check=True, capture_output=True,
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(str(cert), str(key))
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            port = listener.getsockname()[1]

            def serve() -> None:
                for _ in range(1):
                    raw, _addr = listener.accept()
                    try:
                        tls = context.wrap_socket(raw, server_side=True)
                        tls.recv(4096)
                        tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 9000000\r\n\r\n" + b"x" * 30_000)
                        # Жёсткий сброс посреди потока — так фильтр рвёт соединение после первых килобайт.
                        raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, __import__("struct").pack("ii", 1, 0))
                        raw.close()
                    except OSError:
                        pass

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            original = socket.socket.connect

            def connect(sock, address):
                return original(sock, (address[0], port))

            with patch.object(socket.socket, "connect", connect):
                run = ns.download("127.0.0.1", "rr1.googlevideo.com", seconds=2.0)
            thread.join(2)
            listener.close()

        self.assertEqual(run.kind, ns.RUN_CUT)
        self.assertGreater(run.received, 20_000)


class NameChoiceTests(unittest.TestCase):
    """Какие имена идут на замер: открывшиеся, не контрольные, сначала по одному с сервиса."""

    def test_main_host_of_each_service_first_then_second_hosts(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import patch

        from diagnostics.verdict import ReachState

        def probe(host: str, *, main: bool = False, video: bool = False, state=ReachState.OK):
            return SimpleNamespace(
                host=host, reach_state=state, target=SimpleNamespace(main=main, discover_googlevideo=video)
            )

        services = {
            "yt": SimpleNamespace(label="YouTube", control=False),
            "dc": SimpleNamespace(label="Discord", control=False),
            "ya": SimpleNamespace(label="Яндекс", control=True),
        }
        collected = {
            # Порядок адресов — как у сервиса на самом деле: сайт, превью, видеосервер.
            "yt": [probe("www.youtube.com", main=True), probe("i.ytimg.com"), probe("rr1.googlevideo.com", video=True)],
            "dc": [probe("discord.com", main=True), probe("gateway.discord.gg", state=ReachState.DPI)],
            "ya": [probe("ya.ru", main=True)],
        }
        asked: list[str] = []

        def collect(names, fetch, **_kwargs):
            asked.extend(names)
            return ns.NameFacts((ns.NameRun(ns.SERVER_HOST, ns.RUN_CONNECT),))

        run = SimpleNamespace(dns_cancelled=lambda: False, pause=lambda _seconds: True, probe_cancel=None)
        with (
            patch.object(sections.net_access, "known_address", return_value="192.0.2.1"),
            patch.object(sections.name_speed, "collect", side_effect=collect),
        ):
            self.assertIsNone(sections._name_speed(run, collected, services, lambda _line: None))

        # Закрытый адрес и контрольный сайт не меряются; видеосервер YouTube — после главных адресов.
        self.assertEqual(asked, ["www.youtube.com", "discord.com", "rr1.googlevideo.com"])


class SpeedProblemsTests(unittest.TestCase):
    SPEED = {
        "names": {
            "items": [
                {"name": "YouTube", "host": "www.youtube.com", "state": "warn", "text": "0.2 Мбит/с против 40 Мбит/с"},
                {"name": "GitHub", "host": "github.com", "state": "ok", "text": "40 Мбит/с"},
            ]
        }
    }

    def test_throttled_site_becomes_a_problem(self) -> None:
        found = sections.speed_problems(self.SPEED, zapret_running=False)

        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["level"], found[0]["target"], found[0]["action"]), ("warn", "www.youtube.com", "start_zapret"))
        self.assertIn("YouTube открывается, но медленно", found[0]["text"])

    def test_with_zapret_running_the_preset_is_named_not_zapret(self) -> None:
        found = sections.speed_problems(self.SPEED, zapret_running=True)

        self.assertEqual(found[0]["action"], "strategy")
        self.assertIn("выбранный пресет", found[0]["advice"][0])

    def test_no_speed_section_no_problems(self) -> None:
        self.assertEqual(sections.speed_problems(None, zapret_running=False), [])
        self.assertEqual(sections.speed_problems({"names": None}, zapret_running=False), [])


if __name__ == "__main__":
    unittest.main()
