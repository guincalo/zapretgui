"""Динамическое обнаружение GoogleVideo CDN через сеть пользователя."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from blockcheck.googlevideo_discovery import (  # noqa: E402
    extract_googlevideo_hosts,
    discover_googlevideo_host,
)


class GoogleVideoDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        # Эти тесты — про страницу ролика: redirector «не отвечает».
        patcher = patch("blockcheck.googlevideo_discovery._fetch_report_mapping", return_value="")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_extracts_encoded_rr_hosts_and_rejects_service_hosts(self) -> None:
        page = (
            "https%3A%2F%2Frr8---sn-user-a.googlevideo.com%2Fvideoplayback "
            "https://redirector.googlevideo.com/report_mapping "
            "https://rr3---sn-user-b.googlevideo.com/videoplayback"
        )

        self.assertEqual(
            extract_googlevideo_hosts(page),
            (
                "rr8---sn-user-a.googlevideo.com",
                "rr3---sn-user-b.googlevideo.com",
            ),
        )

    def test_uses_third_control_video_when_first_two_have_no_stream_host(self) -> None:
        pages = [
            "YouTube page without streams",
            "Another YouTube page without streams",
            "https://rr4---sn-fresh-user.googlevideo.com/videoplayback",
        ]

        with patch(
            "blockcheck.googlevideo_discovery._fetch_watch_page",
            side_effect=pages,
        ) as fetch:
            result = discover_googlevideo_host()

        self.assertEqual(result.host, "rr4---sn-fresh-user.googlevideo.com")
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(
            fetch.call_args_list[-1].args[0],
            "https://www.youtube.com/watch?v=Qr1zDbHATw0&t=8s&hl=en",
        )

    def test_each_run_discovers_again_instead_of_reusing_old_host(self) -> None:
        pages = [
            "https://rr1---sn-first-user.googlevideo.com/videoplayback",
            "https://rr9---sn-second-user.googlevideo.com/videoplayback",
        ]

        with patch(
            "blockcheck.googlevideo_discovery._fetch_watch_page",
            side_effect=pages,
        ) as fetch:
            first = discover_googlevideo_host()
            second = discover_googlevideo_host()

        self.assertEqual(first.host, "rr1---sn-first-user.googlevideo.com")
        self.assertEqual(second.host, "rr9---sn-second-user.googlevideo.com")
        self.assertEqual(fetch.call_count, 2)

    def test_cancelled_run_does_not_start_discovery_request(self) -> None:
        with patch("blockcheck.googlevideo_discovery._fetch_watch_page") as fetch:
            result = discover_googlevideo_host(cancelled=lambda: True)

        self.assertIsNone(result.host)
        self.assertEqual(result.detail, "проверка остановлена")
        fetch.assert_not_called()

    def test_two_control_videos_share_one_timeout_budget(self) -> None:
        def slow_empty_page(*_args) -> str:
            time.sleep(0.12)
            return "YouTube page without streams"

        with patch(
            "blockcheck.googlevideo_discovery._fetch_watch_page",
            side_effect=slow_empty_page,
        ) as fetch:
            result = discover_googlevideo_host(timeout=0.1)

        self.assertIn("лимит времени", result.detail)
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class ReportMappingTests(unittest.TestCase):
    def test_node_name_becomes_video_server_name(self) -> None:
        from blockcheck.googlevideo_discovery import cluster_to_host, parse_report_mapping

        # Проверено вживую 15.10.2026: имя отвечает адресом кэша Google.
        self.assertEqual(cluster_to_host("muc12s10"), "rr1---sn-h0jelne7.googlevideo.com")
        self.assertEqual(
            parse_report_mapping("198.51.100.23 => muc12s10 (198.51.100.0/24)\n"),
            "rr1---sn-h0jelne7.googlevideo.com",
        )
        # Страница-заглушка провайдера или мусор — не видеосервер.
        self.assertEqual(parse_report_mapping("<html>Доступ ограничен</html>"), "")
        self.assertEqual(cluster_to_host("muc_12"), "")

    def test_the_players_streaming_node_comes_first(self) -> None:
        page = (
            '"formats":[{"url":"https://rr2---sn-aaa.googlevideo.com/videoplayback?x"}],'
            '"serverAbrStreamingUrl":"https:\\/\\/rr9---sn-zzz.googlevideo.com\\/videoplayback?sabr=1"'
        )

        self.assertEqual(
            extract_googlevideo_hosts(page),
            ("rr9---sn-zzz.googlevideo.com", "rr2---sn-aaa.googlevideo.com"),
        )

    def test_redirector_answer_is_used_before_the_youtube_page(self) -> None:
        with (
            patch(
                "blockcheck.googlevideo_discovery._fetch_report_mapping",
                return_value="1.2.3.4 => muc12s10 (1.2.3.0/24)",
            ),
            patch("blockcheck.googlevideo_discovery._fetch_watch_page") as page,
        ):
            result = discover_googlevideo_host()

        self.assertEqual(result.host, "rr1---sn-h0jelne7.googlevideo.com")
        page.assert_not_called()

    def test_failed_redirector_falls_back_to_the_youtube_page(self) -> None:
        with (
            patch("blockcheck.googlevideo_discovery._fetch_report_mapping", side_effect=OSError("сброс")),
            patch(
                "blockcheck.googlevideo_discovery._fetch_watch_page",
                return_value="https://rr4---sn-fresh-user.googlevideo.com/videoplayback",
            ),
        ):
            result = discover_googlevideo_host()

        self.assertEqual(result.host, "rr4---sn-fresh-user.googlevideo.com")
