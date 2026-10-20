"""Качество передачи по замерам запросов: пауза, медленный ответ, медленное тело, без сети."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from diagnostics import transfer_probe as tp
from diagnostics import volume_probe
from diagnostics.levels import OK, UNKNOWN, WARN
from utils import chrome_https

S = tp.RequestSample


def _fast(*millis: float) -> list[tp.RequestSample]:
    return [S(received=5_000, ttfb_ms=ms) for ms in millis]


class JudgeTests(unittest.TestCase):
    def test_real_ttk_series_is_a_pause(self) -> None:
        """Запись 63 из октября 2026: ответы за 7–42 мс и один, ждавший 2,86 с."""
        samples = [S(ttfb_ms=300, fresh=True), *_fast(9, 42, 7, 12, 9, 9), S(received=3_000, ttfb_ms=2_861)]
        verdict = tp.judge(samples)

        self.assertEqual((verdict.code, verdict.level), (tp.TRANSFER_PAUSE, WARN))
        self.assertEqual(verdict.samples, 7)
        self.assertEqual(round(verdict.ttfb_max_ms), 2_861)
        self.assertIn("1 ждал из 7", verdict.text.replace("запросов ", ""))
        self.assertIn("2.9 с", verdict.text)

    def test_repeated_pause_is_trusted_more_than_a_single_one(self) -> None:
        once = tp.judge(_fast(9, 9, 9, 9, 9, 9, 3_000))
        twice = tp.judge(_fast(9, 9, 9, 9, 9, 3_000, 3_100))

        self.assertEqual(once.code, tp.TRANSFER_PAUSE)
        self.assertEqual(twice.code, tp.TRANSFER_PAUSE)
        self.assertEqual(twice.confidence, tp.CONFIDENCE_HIGH)
        self.assertNotEqual(once.confidence, tp.CONFIDENCE_HIGH)
        # Одна пауза при малом числе замеров — вывод с низкой уверенностью.
        self.assertEqual(tp.judge(_fast(9, 9, 9, 3_000)).confidence, tp.CONFIDENCE_LOW)

    def test_fast_answers_are_ok_with_confidence_from_the_sample_count(self) -> None:
        few = tp.judge(_fast(20, 25, 30))
        many = tp.judge(_fast(20, 25, 30, 22, 24, 21))

        self.assertEqual((few.code, few.level, few.confidence), (tp.TRANSFER_OK, OK, tp.CONFIDENCE_MEDIUM))
        self.assertEqual(many.confidence, tp.CONFIDENCE_HIGH)

    def test_first_request_on_the_connection_is_not_counted(self) -> None:
        """В его время входит рукопожатие: долгое рукопожатие — не пауза и не медленный ответ."""
        verdict = tp.judge([S(ttfb_ms=4_000, fresh=True), *_fast(20, 25, 30)])

        self.assertEqual((verdict.code, verdict.samples), (tp.TRANSFER_OK, 3))

    def test_too_few_samples_give_no_verdict(self) -> None:
        for samples in ([], _fast(10), _fast(10, 20), [S(ttfb_ms=300, fresh=True), *_fast(10, 20)]):
            with self.subTest(samples=len(samples)):
                verdict = tp.judge(samples)
                self.assertEqual((verdict.code, verdict.level), (tp.TRANSFER_UNKNOWN, UNKNOWN))

    def test_requests_without_an_answer_are_not_samples(self) -> None:
        verdict = tp.judge([S(ttfb_ms=0.0, failed=True), *_fast(20, 25, 30)])

        self.assertEqual((verdict.code, verdict.samples), (tp.TRANSFER_OK, 3))

    def test_far_server_is_slow_everywhere_not_a_pause(self) -> None:
        """Все ответы по ~400 мс — далёкий сервер: ни паузы, ни медленного ответа."""
        verdict = tp.judge(_fast(400, 420, 390, 410, 405, 395))

        self.assertEqual(verdict.code, tp.TRANSFER_OK)

    def test_a_slightly_longer_wait_is_not_a_pause(self) -> None:
        """2,6 с при обычных 300 мс — это не в 20 раз дольше: линия колеблется, паузой это не назвать."""
        verdict = tp.judge(_fast(300, 280, 320, 290, 310, 2_600))

        self.assertNotEqual(verdict.code, tp.TRANSFER_PAUSE)

    def test_slow_replies_everywhere(self) -> None:
        verdict = tp.judge(_fast(1_500, 1_500, 1_500, 1_500))

        self.assertEqual((verdict.code, verdict.level), (tp.TRANSFER_SLOW_REPLY, WARN))
        self.assertIn("около 1.5 с", verdict.text)

    def test_slow_body_is_told_by_speed_of_large_answers(self) -> None:
        # 64 КБ за 1 с — около 64 КБ/с: тело идёт медленно, хотя первый байт приходит быстро.
        samples = [S(received=65_536, ttfb_ms=30, body_ms=1_000) for _ in range(4)]
        verdict = tp.judge(samples)

        self.assertEqual((verdict.code, verdict.level), (tp.TRANSFER_SLOW_BODY, WARN))
        self.assertAlmostEqual(verdict.speed_kbps, 64.0, places=0)

    def test_short_answers_do_not_make_a_speed(self) -> None:
        """У ответа в несколько КБ «скорость» — это задержка линии, её не считаем."""
        samples = [S(received=4_000, ttfb_ms=30, body_ms=500) for _ in range(4)]

        self.assertEqual(tp.speed_kbps(samples), 0.0)
        self.assertEqual(tp.judge(samples).code, tp.TRANSFER_OK)

    def test_fast_large_answers_are_ok(self) -> None:
        samples = [S(received=262_144, ttfb_ms=25, body_ms=400) for _ in range(3)]
        verdict = tp.judge(samples)

        self.assertEqual(verdict.code, tp.TRANSFER_OK)
        self.assertGreater(verdict.speed_kbps, 500)

    def test_report_form_is_rounded_and_complete(self) -> None:
        data = tp.judge(_fast(9, 42, 7, 12, 9, 2_861.4)).as_dict()

        self.assertEqual(
            sorted(data),
            ["code", "confidence", "level", "samples", "speed_kbps", "text", "ttfb_max_ms", "ttfb_median_ms"],
        )
        self.assertEqual((data["ttfb_max_ms"], data["samples"]), (2_861, 6))

    def test_plural_in_the_text(self) -> None:
        for count, word in ((1, "ждал"), (2, "ждали"), (5, "ждали"), (11, "ждали"), (21, "ждал")):
            with self.subTest(count=count):
                self.assertEqual(tp._waited(count), f"{count} {word}")


class ExchangeTimingTests(unittest.TestCase):
    def _exchange(self, **times) -> chrome_https.Exchange:
        progress = chrome_https._Progress(body=bytearray(b"x" * 10), **times)
        return chrome_https.Exchange(progress)

    def test_times_are_derived_from_the_marks(self) -> None:
        exchange = self._exchange(started_at=10.0, first_header_at=10.2, first_body_at=10.25, last_body_at=11.25, ended_at=11.3)

        self.assertAlmostEqual(exchange.ttfb_ms, 200.0, places=3)
        self.assertAlmostEqual(exchange.elapsed_ms, 1_300.0, places=3)
        self.assertAlmostEqual(exchange.body_ms, 1_000.0, places=3)

    def test_no_answer_means_zero_not_garbage(self) -> None:
        exchange = self._exchange(started_at=10.0, ended_at=14.0)

        self.assertEqual((exchange.ttfb_ms, exchange.body_ms), (0.0, 0.0))
        self.assertAlmostEqual(exchange.elapsed_ms, 4_000.0, places=3)

    def test_a_single_chunk_has_no_body_time(self) -> None:
        exchange = self._exchange(started_at=1.0, first_header_at=1.1, first_body_at=1.5, last_body_at=1.5, ended_at=1.6)

        self.assertEqual(exchange.body_ms, 0.0)

    def test_untimed_exchange_is_all_zeros(self) -> None:
        exchange = chrome_https.Exchange(chrome_https._Progress(body=bytearray()))

        self.assertEqual((exchange.ttfb_ms, exchange.elapsed_ms, exchange.body_ms), (0.0, 0.0, 0.0))


class VolumeSamplesTests(unittest.TestCase):
    """``volume_probe.download`` отдаёт замер каждого запроса — без новых запросов в сеть."""

    def _connection(self, exchanges):
        queue = list(exchanges)

        class _Connection:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def request(self, path, *, method="GET", enough=lambda body: False):
                return queue.pop(0)

            def close(self) -> None:
                pass

        return _Connection

    @staticmethod
    def _exchange(received: int, *, ttfb: float, stopped: bool = False) -> chrome_https.Exchange:
        progress = chrome_https._Progress(
            body=bytearray(b"x" * received),
            status=200,
            started_at=1.0,
            first_header_at=1.0 + ttfb / 1000,
            first_body_at=1.0 + ttfb / 1000,
            last_body_at=1.0 + ttfb / 1000 + 0.2,
            ended_at=1.5,
            stopped_by_us=stopped,
        )
        return chrome_https.Exchange(progress)

    def test_each_request_leaves_a_sample(self) -> None:
        exchanges = [self._exchange(20_000, ttfb=120), self._exchange(20_000, ttfb=9), self._exchange(20_000, ttfb=2_900, stopped=True)]
        with patch.object(chrome_https, "ChromeConnection", self._connection(exchanges)):
            run = volume_probe.download("h.test", "203.0.113.5", "/", target=60_000)

        self.assertEqual(run.kind, volume_probe.RUN_PASSED)
        self.assertEqual(len(run.samples), 3)
        self.assertEqual([s.fresh for s in run.samples], [True, False, False])
        self.assertAlmostEqual(run.samples[2].ttfb_ms, 2_900, places=3)
        self.assertGreater(run.samples[1].body_ms, 0)

    def test_old_three_field_run_still_builds(self) -> None:
        run = volume_probe.VolumeRun(volume_probe.RUN_PASSED, 49_500, 4)

        self.assertEqual(run.samples, ())


if __name__ == "__main__":
    unittest.main()
