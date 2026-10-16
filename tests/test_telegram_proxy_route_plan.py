from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_proxy.proxy.cloudflare import AUTO_CLOUDFLARE_DOMAINS, CloudflareFallbackConfig
from telegram_proxy.proxy.health import RouteHealth, address_key, cdn_key
from telegram_proxy.proxy.key_clusters import CLUSTER_MEDIA, CLUSTER_REGULAR, KeyClusters
from telegram_proxy.proxy import routes as routes_module
from telegram_proxy.proxy.route_catalog import CDN_FRONTS, TUNNEL_HOSTS
from telegram_proxy.proxy.routes import (
    KIND_DIRECT,
    KIND_FRONT,
    KIND_RELAY,
    KIND_TUNNEL,
    KIND_UPSTREAM,
    KIND_USER_DOMAIN,
    KIND_USER_WORKER,
    TUNNELS_PER_CONNECTION,
    PlanInput,
    build_plan,
)
from telegram_proxy.proxy.routing import UpstreamProxyConfig


PRESET = UpstreamProxyConfig(enabled=True, host="1.2.3.4", port=1080, mode="fallback", preset_id="ee")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# Сколько воркеров-туннелей попадает в план одного соединения.
TUNNELS = [KIND_TUNNEL] * min(len(TUNNEL_HOSTS), TUNNELS_PER_CONNECTION)


def kinds(plan) -> list[str]:
    return [route.kind for route in plan]


class RoutePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.health = RouteHealth(clock=self.clock, front_count=len(CDN_FRONTS))

    def test_dc2_goes_relay_fronts_tunnel_then_country_socks(self) -> None:
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=PRESET), self.health)
        self.assertEqual(kinds(plan), [KIND_RELAY, KIND_FRONT, KIND_FRONT, KIND_FRONT, *TUNNELS, KIND_UPSTREAM])
        relay = plan[0]
        self.assertEqual((relay.connect_host, relay.sni), ("149.154.167.220", "kws2.web.telegram.org"))
        self.assertTrue(plan[1].sni.startswith("kws2."))
        self.assertEqual(plan[4].path, "/apiws?dst=149.154.167.51&dc=2")

    def test_media_uses_minus_one_relay_domain(self) -> None:
        plan = build_plan(PlanInput(dc=4, is_media=True, target_host="149.154.164.250"), self.health)
        self.assertEqual(plan[0].sni, "kws4-1.web.telegram.org")

    def test_media_fronts_land_on_regular_cluster_and_the_rest_on_media(self) -> None:
        plan = build_plan(PlanInput(dc=2, is_media=True, target_host="149.154.167.222", upstream=PRESET), self.health)
        clusters = {route.kind: route.cluster for route in plan}
        self.assertEqual(clusters[KIND_FRONT], CLUSTER_REGULAR)
        for kind in (KIND_RELAY, KIND_TUNNEL, KIND_UPSTREAM):
            self.assertEqual(clusters[kind], CLUSTER_MEDIA)
        plain = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=PRESET), self.health)
        self.assertEqual({route.cluster for route in plain}, {CLUSTER_REGULAR})

    def test_key_living_on_regular_cluster_puts_fronts_before_relay(self) -> None:
        request = PlanInput(
            dc=2, is_media=True, target_host="149.154.167.222", upstream=PRESET, key_cluster=CLUSTER_REGULAR
        )
        plan = build_plan(request, self.health)
        # Запасные пути (воркеры, SOCKS) ради кластера вперёд не встают.
        self.assertEqual(kinds(plan), [KIND_FRONT, KIND_FRONT, KIND_FRONT, KIND_RELAY, *TUNNELS, KIND_UPSTREAM])
        media_home = build_plan(
            PlanInput(dc=2, is_media=True, target_host="149.154.167.222", upstream=PRESET, key_cluster=CLUSTER_MEDIA),
            self.health,
        )
        self.assertEqual(kinds(media_home)[0], KIND_RELAY)

    def test_dc1_dc3_dc5_use_relay_on_their_own_ingress(self) -> None:
        for dc, address in ((1, "149.154.174.100"), (3, "149.154.174.100"), (5, "149.154.170.100")):
            with self.subTest(dc=dc):
                plan = build_plan(PlanInput(dc=dc, is_media=False, target_host="149.154.175.50"), self.health)
                self.assertEqual(kinds(plan), [KIND_RELAY, KIND_FRONT, KIND_FRONT, KIND_FRONT, *TUNNELS, KIND_DIRECT])
                self.assertEqual((plan[0].connect_host, plan[0].sni), (address, f"kws{dc}.web.telegram.org"))

    def test_dc203_uses_only_tunnel_before_fallbacks(self) -> None:
        plan = build_plan(PlanInput(dc=203, is_media=False, target_host="91.105.192.100", upstream=PRESET), self.health)
        self.assertEqual(kinds(plan), [*TUNNELS, KIND_UPSTREAM])
        self.assertLessEqual({route.sni for route in plan[:-1]}, set(TUNNEL_HOSTS))

    def test_tunnels_are_tried_in_a_circle_from_this_install_start(self) -> None:
        hosts = ("a.workers.dev", "b.workers.dev", "c.workers.dev", "d.workers.dev")
        health = RouteHealth(clock=self.clock, tunnel_start=2 + 4 * 10)
        request = PlanInput(dc=203, is_media=False, target_host="91.105.192.100", upstream=PRESET)
        with patch.object(routes_module, "TUNNEL_HOSTS", hosts):
            plan = build_plan(request, health)
            self.assertEqual([route.sni for route in plan[:-1]], ["c.workers.dev", "d.workers.dev", "a.workers.dev"])
            self.assertEqual([route.health_key for route in plan[:-1]], [route.sni for route in plan[:-1]])
            self.assertEqual(plan[-1].kind, KIND_UPSTREAM)

            # Воркер с исчерпанным лимитом пропускается, его место занимает следующий.
            self.assertTrue(health.note_failure("c.workers.dev", threshold=1))
            plan = build_plan(request, health)
            self.assertEqual([route.sni for route in plan[:-1]], ["d.workers.dev", "a.workers.dev", "b.workers.dev"])

    def test_every_tunnel_suppressed_leaves_dc203_on_socks(self) -> None:
        hosts = ("a.workers.dev", "b.workers.dev")
        with patch.object(routes_module, "TUNNEL_HOSTS", hosts):
            for host in hosts:
                self.assertTrue(self.health.note_failure(host, threshold=1))
            plan = build_plan(
                PlanInput(dc=203, is_media=False, target_host="91.105.192.100", upstream=PRESET), self.health
            )
        self.assertEqual(kinds(plan), [KIND_UPSTREAM])

    def test_manual_always_server_takes_all_traffic(self) -> None:
        manual = UpstreamProxyConfig(enabled=True, host="5.6.7.8", port=1080, mode="always")
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=manual), self.health)
        self.assertEqual(kinds(plan), [KIND_UPSTREAM])

    def test_country_preset_toggle_all_tcp_through_socks_is_honoured(self) -> None:
        preset_always = UpstreamProxyConfig(enabled=True, host="1.2.3.4", port=1080, mode="always", preset_id="ee")
        plan = build_plan(PlanInput(dc=2, is_media=False, target_host="149.154.167.51", upstream=preset_always), self.health)
        self.assertEqual(kinds(plan), [KIND_UPSTREAM])

    def test_user_domain_and_worker_come_before_builtin_fronts(self) -> None:
        cloudflare = CloudflareFallbackConfig(
            enabled=True,
            domains=("my.example.com", *AUTO_CLOUDFLARE_DOMAINS),
            worker_enabled=True,
            worker_domains=("w.example.dev",),
        )
        plan = build_plan(PlanInput(dc=1, is_media=False, target_host="149.154.175.50", cloudflare=cloudflare), self.health)
        self.assertEqual(kinds(plan)[:4], [KIND_RELAY, KIND_USER_DOMAIN, KIND_USER_WORKER, KIND_FRONT])
        self.assertEqual(plan[1].sni, "kws1.my.example.com")
        self._suppress("kws1.web.telegram.org")
        plan = build_plan(PlanInput(dc=1, is_media=False, target_host="149.154.175.50", cloudflare=cloudflare), self.health)
        self.assertEqual(kinds(plan)[:3], [KIND_USER_DOMAIN, KIND_USER_WORKER, KIND_FRONT])
        self.assertEqual(plan[0].sni, "kws1.my.example.com")

    def test_suppressed_relay_address_switches_to_the_other_ingress_then_dns(self) -> None:
        request = PlanInput(dc=2, is_media=False, target_host="149.154.167.51")
        self._suppress(address_key("149.154.167.220"))
        plan = build_plan(request, self.health)
        self.assertEqual(plan[0].connect_host, "149.154.167.99")
        self.assertEqual(plan[0].address_health_key, address_key("149.154.167.99"))
        self.assertEqual(plan[0].pool_key, "relay:kws2.web.telegram.org@149.154.167.99")
        self._suppress(address_key("149.154.167.99"))
        plan = build_plan(request, self.health)
        self.assertEqual(plan[0].connect_host, "kws2.web.telegram.org")
        self.assertEqual(plan[0].address_health_key, "")
        self.assertEqual(plan[0].pool_key, "relay:kws2.web.telegram.org@dns")

    def test_failed_ingress_yields_to_the_other_and_answer_keeps_it_first(self) -> None:
        dc2 = PlanInput(dc=2, is_media=False, target_host="149.154.167.51")
        dc4 = PlanInput(dc=4, is_media=True, target_host="149.154.164.250")
        addresses = ("149.154.167.220", "149.154.167.99")
        # Один отказ .220 — и все имена DC2/DC4 сразу идут на .99, без DNS.
        self.health.note_relay_address_failed(addresses, "149.154.167.220")
        self.assertEqual(build_plan(dc2, self.health)[0].connect_host, "149.154.167.99")
        self.assertEqual(build_plan(dc4, self.health)[0].connect_host, "149.154.167.99")
        # .99 ответил: он остаётся первым и после того, как отказ .220 забыт.
        self.health.note_relay_address_answered(addresses, "149.154.167.99")
        self.clock.now += 600
        self.assertEqual(build_plan(dc2, self.health)[0].connect_host, "149.154.167.99")
        # Отказал и он — первым снова идёт .220, который давно не отказывал.
        self.health.note_relay_address_failed(addresses, "149.154.167.99")
        self.assertEqual(build_plan(dc2, self.health)[0].connect_host, "149.154.167.220")

    def test_all_wss_suppressed_goes_socks_then_fronts_anyway(self) -> None:
        self._suppress("kws1.web.telegram.org")
        self._suppress(cdn_key(1))
        for host in TUNNEL_HOSTS:
            self._suppress(host)
        plan = build_plan(PlanInput(dc=1, is_media=False, target_host="149.154.175.50", upstream=PRESET), self.health)
        self.assertEqual(kinds(plan), [KIND_UPSTREAM, KIND_FRONT, KIND_FRONT, KIND_FRONT])

    def _suppress(self, key: str) -> None:
        for _ in range(6):
            self.health.note_failure(key, threshold=6)
            self.clock.now += 3
        self.assertTrue(self.health.is_suppressed(key))


class KeyClustersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.keys = KeyClusters(clock=self.clock, capacity=2)

    def test_home_is_remembered_and_oldest_key_is_dropped(self) -> None:
        self.keys.note_ok(b"k1" * 4, CLUSTER_MEDIA)
        self.keys.note_ok(b"k2" * 4, CLUSTER_REGULAR)
        self.keys.note_ok(b"k1" * 4, CLUSTER_MEDIA)
        self.keys.note_ok(b"k3" * 4, CLUSTER_REGULAR)
        self.assertEqual(self.keys.home(b"k1" * 4), CLUSTER_MEDIA)
        self.assertEqual(self.keys.home(b"k2" * 4), "")
        self.assertEqual(self.keys.home(b""), "")

    def test_quiet_close_only_for_a_fresh_home_and_once_a_minute(self) -> None:
        key = b"k1" * 4
        self.assertFalse(self.keys.may_close_quietly(key, CLUSTER_MEDIA))
        self.keys.note_ok(key, CLUSTER_REGULAR)
        self.assertFalse(self.keys.may_close_quietly(key, CLUSTER_REGULAR))
        self.assertTrue(self.keys.may_close_quietly(key, CLUSTER_MEDIA))
        self.clock.now += 30
        self.assertFalse(self.keys.may_close_quietly(key, CLUSTER_MEDIA))
        self.clock.now += 100
        # Дом ключа не отвечал больше двух минут: ключу пора переехать.
        self.assertFalse(self.keys.may_close_quietly(key, CLUSTER_MEDIA))


class RouteHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.health = RouteHealth(clock=self.clock, front_count=len(CDN_FRONTS))

    def _fail(self, key: str, times: int, **kwargs) -> None:
        for _ in range(times):
            self.health.note_failure(key, **kwargs)
            self.clock.now += 3

    def test_three_failures_suppress_and_duration_doubles(self) -> None:
        self._fail("kws2.web.telegram.org", 2)
        self.assertFalse(self.health.is_suppressed("kws2.web.telegram.org"))
        self._fail("kws2.web.telegram.org", 1)
        self.assertAlmostEqual(self.health.suppressed_for("kws2.web.telegram.org"), 117, delta=1)
        self.clock.now += 200
        self._fail("kws2.web.telegram.org", 3)
        self.assertAlmostEqual(self.health.suppressed_for("kws2.web.telegram.org"), 237, delta=1)

    def test_failures_in_one_burst_count_once(self) -> None:
        for _ in range(5):
            self.health.note_failure("k")
            self.clock.now += 0.5
        self.assertFalse(self.health.is_suppressed("k"))

    def test_lost_syn_ignored_when_address_recently_connected(self) -> None:
        self.health.note_tcp_connected("149.154.167.220")
        self._fail("k", 5, tcp_reached=False, address="149.154.167.220")
        self.assertFalse(self.health.is_suppressed("k"))

    def test_answer_breaks_series_but_only_proof_clears_suppressions(self) -> None:
        self._fail("k", 2)
        self.health.note_answer("k")
        self._fail("k", 2)
        self.assertFalse(self.health.is_suppressed("k"))
        self._fail("k", 1)
        self.assertTrue(self.health.is_suppressed("k"))
        self.health.note_proven("k")
        self.assertFalse(self.health.is_suppressed("k"))

    def test_suppression_survives_restart_but_capped_to_ten_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            wall = [5000.0]
            health = RouteHealth(path=path, clock=self.clock, wall_clock=lambda: wall[0], front_count=20)
            for _ in range(4):  # 2 + 4 + 8 + 16 минут
                for _ in range(3):
                    health.note_failure("k")
                    self.clock.now += 3
            self.assertIn("k", json.loads(path.read_text())["routes"])
            restored = RouteHealth(path=path, clock=self.clock, wall_clock=lambda: wall[0], front_count=20)
            self.assertTrue(restored.is_suppressed("k"))
            self.assertLessEqual(restored.suppressed_for("k"), 600)

    def test_working_front_address_family_is_kept(self) -> None:
        index, family = self.health.next_front_slots(1)[0]
        self.health.note_front_result(index, family, tcp_ok=True, answered=False)
        slots = self.health.next_front_slots(3)
        self.assertEqual({slot_family for _i, slot_family in slots}, {family})
        self.assertEqual(len({i for i, _f in slots}), 3)
        self.assertNotEqual(slots[0][0], index)


if __name__ == "__main__":
    unittest.main()
