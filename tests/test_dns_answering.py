"""Кто на самом деле отвечает на DNS: короткое имя сети и проверка в три вопроса."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from dns import answering
from utils.dns_wire import STATUS_OK, TYPE_A, DnsQueryResult, DnsRecord
from utils.ip_owner import IpOwner


class ShortOwnerTests(unittest.TestCase):
    def test_owner_is_named_without_network_code_country_and_company_form(self) -> None:
        for raw, short in (
            ("CLOUDFLARENET - Cloudflare, Inc., US", "Cloudflare"),
            ("GOOGLE - Google LLC, US", "Google LLC"),
            ("ROSTELECOM-AS PJSC Rostelecom. Technical Team, RU", "ROSTELECOM-AS PJSC Rostelecom. Technical Team"),
            ("", ""),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(answering.short_owner(IpOwner(asn="1", owner=raw)), short)
        self.assertEqual(answering.short_owner(None), "")


class CheckTests(unittest.TestCase):
    def _check(self, *, egress: tuple[str, ...], owner: IpOwner | None, canary: bool):
        records = tuple(DnsRecord(name="whoami.akamai.net", rtype=TYPE_A, ttl=20, value=ip) for ip in egress)
        whoami = DnsQueryResult(status=STATUS_OK, records=records)
        with (
            patch.object(answering, "query_udp", return_value=whoami) as asked,
            patch.object(answering, "lookup_ip_owner", return_value=owner) as owner_of,
            patch.object(answering, "canary_answered", return_value=canary),
        ):
            result = answering.check_answering("1.1.1.1")
        return result, asked, owner_of

    def test_network_that_resolves_names_is_named(self) -> None:
        result, asked, owner_of = self._check(
            egress=("172.68.1.1",), owner=IpOwner(asn="13335", owner="CLOUDFLARENET - Cloudflare, Inc., US"), canary=False
        )

        self.assertEqual(result, answering.Answering(owner="Cloudflare", asn="13335", intercepted=False))
        self.assertEqual(asked.call_args.args[:2], ("1.1.1.1", answering.WHOAMI_DOMAIN))
        self.assertEqual(owner_of.call_args.args[0], "172.68.1.1")

    def test_interception_is_said_only_by_the_canary(self) -> None:
        result, _asked, owner_of = self._check(egress=(), owner=None, canary=True)

        self.assertEqual(result, answering.Answering(intercepted=True))
        owner_of.assert_not_called()


if __name__ == "__main__":
    unittest.main()
