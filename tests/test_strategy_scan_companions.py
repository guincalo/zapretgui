"""Подбор стратегии для сервиса из нескольких имён: профиль и «Применить» знают все имена."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from blockcheck import strategy_scan_apply as apply


def _profile(*names: str, port_filter: str = "--filter-tcp=443"):
    return SimpleNamespace(
        match=SimpleNamespace(
            hostlist_lines=(),
            hostlist_domains_lines=(f"--hostlist-domains={','.join(names)}",),
            filter_lines=(port_filter,),
        )
    )


class OwnProfileTests(unittest.TestCase):
    def test_profile_with_every_name_of_youtube_is_its_own(self) -> None:
        profile = _profile("youtube.com", "googlevideo.com", "ytimg.com")

        self.assertTrue(apply._is_targets_own_profile(profile, "www.youtube.com"))

    def test_old_single_name_profile_is_not_taken_for_the_whole_service(self) -> None:
        # Старый профиль только на googlevideo.com не закрывает страницу: «Применить» заведёт полный.
        self.assertFalse(apply._is_targets_own_profile(_profile("googlevideo.com"), "www.youtube.com"))

    def test_single_site_keeps_working_as_before(self) -> None:
        self.assertTrue(apply._is_targets_own_profile(_profile("discord.com"), "discord.com"))
        self.assertFalse(apply._is_targets_own_profile(_profile("discord.com", "other.com"), "discord.com"))

    def test_applied_profile_is_named_by_all_names(self) -> None:
        class _Feature:
            pass

        from unittest.mock import patch

        with patch.object(apply, "apply_profile_to_selected_preset", return_value=("preset.txt", "added", [])):
            result = apply.apply_strategy(
                profile_feature=_Feature(),
                strategy_args="--lua-desync=fake",
                strategy_name="fake",
                scan_target="www.youtube.com",
                scan_protocol="tcp_https",
                apply_lines=("--hostlist-domains=youtube.com,googlevideo.com,ytimg.com",),
            )

        self.assertEqual(result.applied_profile, "youtube.com, googlevideo.com, ytimg.com")


class WorkerCompanionsTests(unittest.TestCase):
    def _resolve(self, target: str, protocol: str = "tcp_https", discovered: str = "rr1---sn-test.googlevideo.com"):
        from unittest.mock import patch

        from blockcheck.googlevideo_discovery import GoogleVideoDiscoveryResult
        from blockcheck.strategy_scan_worker import StrategyScanWorker

        worker = SimpleNamespace(
            _scan_protocol=protocol,
            _target=target,
            _GOOGLEVIDEO_DISCOVERY_TIMEOUT=1.0,
            log=lambda _text: None,
            is_cancelled=lambda: False,
        )
        result = GoogleVideoDiscoveryResult(host=discovered or None, detail="тест")
        with patch("blockcheck.googlevideo_discovery.discover_googlevideo_host", return_value=result):
            return StrategyScanWorker._resolve_companions(worker)

    def test_youtube_gets_the_video_server_of_this_network_and_the_previews(self) -> None:
        self.assertEqual(
            self._resolve("www.youtube.com"),
            (("googlevideo.com", "rr1---sn-test.googlevideo.com"), ("ytimg.com", "i.ytimg.com")),
        )

    def test_video_server_that_cannot_be_found_is_skipped(self) -> None:
        self.assertEqual(self._resolve("www.youtube.com", discovered=""), (("ytimg.com", "i.ytimg.com"),))

    def test_other_targets_and_protocols_have_no_companions(self) -> None:
        self.assertEqual(self._resolve("discord.com"), ())
        self.assertEqual(self._resolve("googlevideo.com"), ())
        self.assertEqual(self._resolve("www.youtube.com", protocol="stun_voice"), ())


if __name__ == "__main__":
    unittest.main()
