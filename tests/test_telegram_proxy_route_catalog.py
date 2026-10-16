from __future__ import annotations

import unittest


class TelegramProxyRouteCatalogTests(unittest.TestCase):
    def test_relays_cover_dc1_to_dc5_on_their_own_ingress(self) -> None:
        from telegram_proxy.proxy.route_catalog import RELAY_INGRESS, relay_addresses, relay_domain

        self.assertEqual(sorted(RELAY_INGRESS), [1, 2, 3, 4, 5])
        self.assertEqual(relay_addresses(1), ("149.154.174.100",))
        self.assertEqual(relay_addresses(3), ("149.154.174.100",))
        self.assertEqual(relay_addresses(5), ("149.154.170.100",))
        self.assertEqual(relay_addresses(203), ())
        self.assertEqual(relay_domain(1, is_media=False), "kws1.web.telegram.org")
        self.assertEqual(relay_domain(4, is_media=True), "kws4-1.web.telegram.org")

    def test_dc2_and_dc4_have_hardcoded_and_dns_ingress(self) -> None:
        from telegram_proxy.proxy.route_catalog import relay_addresses

        # .220 — зашитый во все клиенты, .99 — его отдаёт DNS; оба несут DC2/DC4.
        for dc in (2, 4):
            with self.subTest(dc=dc):
                self.assertEqual(relay_addresses(dc), ("149.154.167.220", "149.154.167.99"))

    def test_cdn_fronts_have_two_cloudflare_addresses_each(self) -> None:
        from telegram_proxy.proxy.route_catalog import CDN_FRONTS, TUNNEL_HOSTS

        self.assertEqual(len(CDN_FRONTS), 20)
        self.assertEqual(len({front.domain for front in CDN_FRONTS}), 20)
        for front in CDN_FRONTS:
            with self.subTest(front=front.domain):
                self.assertTrue(front.domain.endswith(".co.uk"))
                self.assertTrue(front.addresses[0].startswith("104.21."))
                self.assertTrue(front.addresses[1].startswith("172.67."))
        self.assertEqual(CDN_FRONTS[0].host_for(2), "kws2.pclead.co.uk")
        self.assertIn("edge.amberwick.workers.dev", TUNNEL_HOSTS)
        self.assertEqual(len(set(TUNNEL_HOSTS)), len(TUNNEL_HOSTS))
        for host in TUNNEL_HOSTS:
            with self.subTest(tunnel=host):
                self.assertTrue(host.endswith(".workers.dev"))
                self.assertEqual(host, host.strip().lower())

if __name__ == "__main__":
    unittest.main()
