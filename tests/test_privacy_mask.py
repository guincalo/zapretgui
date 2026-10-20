"""Внешний адрес в отчёте скрыт, а провайдер, сеть и страна остаются."""

from __future__ import annotations

import unittest

from diagnostics import my_network as net
from diagnostics.privacy import mask_ip, mask_prefix, mask_public_ip
from utils.ip_owner import IpOwner


class MaskTests(unittest.TestCase):
    def test_ipv4_hides_only_the_last_part(self) -> None:
        self.assertEqual(mask_ip("109.252.1.7"), "109.252.1.xxx")

    def test_ipv6_keeps_the_first_three_groups(self) -> None:
        self.assertEqual(mask_ip("2001:0db8:1234:5::7"), "2001:db8:1234::xxx")

    def test_not_an_address_stays_as_it_is(self) -> None:
        self.assertEqual(mask_ip("не адрес"), "не адрес")
        self.assertEqual(mask_ip(""), "")

    def test_only_internet_addresses_are_masked(self) -> None:
        self.assertEqual(mask_public_ip("109.252.1.7"), "109.252.1.xxx")
        for kept in ("192.168.1.8", "10.0.0.5", "100.72.4.9", ""):
            self.assertEqual(mask_public_ip(kept), kept)

    def test_wide_prefix_is_shown_and_narrow_one_is_hidden(self) -> None:
        self.assertEqual(mask_prefix("109.252.1.0/24"), "109.252.1.0/24")
        self.assertEqual(mask_prefix("2001:db8::/32"), "2001:db8::/32")
        self.assertEqual(mask_prefix("109.252.1.64/28"), "109.252.1.xxx/28")


class ReportTests(unittest.TestCase):
    OWNER = IpOwner(asn="12389", prefix="109.252.1.64/28", country="RU", owner="ROSTELECOM-AS")

    def test_report_lines_never_carry_the_full_address(self) -> None:
        facts = net.NetworkFacts(external_ip="109.252.1.77", country="RU", owner=self.OWNER, local_ip="109.252.1.77")
        text = " | ".join(f"{line.name}: {line.text}" for line in net.judge(facts))

        self.assertNotIn("109.252.1.77", text)
        self.assertIn("109.252.1.xxx (RU)", text)
        self.assertIn("ROSTELECOM-AS · AS12389", text)
        self.assertIn("Адрес компьютера: 109.252.1.xxx — совпадает с внешним", text)

    def test_network_report_dict_is_masked(self) -> None:
        from diagnostics import sections

        class _Run:
            def dns_cancelled(self) -> bool:
                return False

        facts = net.NetworkFacts(external_ip="109.252.1.77", country="RU", owner=self.OWNER, local_ip="192.168.1.5")
        original = sections.my_network.collect
        sections.my_network.collect = lambda **_kwargs: facts
        try:
            report = sections.check_network(_Run(), ())
        finally:
            sections.my_network.collect = original

        self.assertEqual((report["external_ip"], report["prefix"], report["local_ip"]), ("109.252.1.xxx", "109.252.1.xxx/28", "192.168.1.5"))
        self.assertTrue(report["ip_masked"])
        self.assertNotIn("109.252.1.77", str(report))


if __name__ == "__main__":
    unittest.main()
