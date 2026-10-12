"""«Жив ли сайт снаружи»: разбор ответа сервера и случаи, когда вывода нет."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from diagnostics import outside_check as oc
from utils.socket_cancel import SocketCancel


def _answer(*results, self_ok=True) -> bytes:
    return json.dumps({"v": 1, "results": list(results), "self_ok": self_ok}).encode()


class AskTests(unittest.TestCase):
    def _ask(self, hosts, answer, ips=("83.217.211.149",)):
        with patch.object(oc, "_post", return_value=answer) as post:
            return oc.ask(hosts, server_ips=lambda: ips, cancel=SocketCancel()), post

    def test_alive_and_dead_sites(self) -> None:
        found, post = self._ask(
            ["discord.com", "dead.example", "discord.com"],
            _answer({"host": "discord.com", "dns": "ok", "up": True}, {"host": "dead.example", "dns": "ok", "up": False}),
        )

        self.assertEqual(found, {"discord.com": oc.OUTSIDE_UP, "dead.example": oc.OUTSIDE_DOWN})
        # Повтор имени не отправляется дважды.
        self.assertEqual(json.loads(post.call_args.args[1])["hosts"], ["discord.com", "dead.example"])

    def test_no_conclusion_without_servers_own_network_or_dns(self) -> None:
        found, _post = self._ask(["discord.com"], _answer({"host": "discord.com", "dns": "ok", "up": False}, self_ok=False))
        self.assertEqual(found, {})

        found, _post = self._ask(["discord.com"], _answer({"host": "discord.com", "dns": "fail", "up": False}))
        self.assertEqual(found, {})

    def test_broken_or_missing_answer_gives_nothing(self) -> None:
        for answer in (None, b"", b"not json", b"[]"):
            with self.subTest(answer=answer):
                self.assertEqual(self._ask(["discord.com"], answer)[0], {})
        self.assertEqual(self._ask(["discord.com"], _answer(), ips=())[0], {})

    def test_no_more_than_eight_sites_go_to_the_server(self) -> None:
        hosts = [f"site{index}.example" for index in range(12)]
        _found, post = self._ask(hosts, _answer())

        self.assertEqual(len(json.loads(post.call_args.args[1])["hosts"]), oc.MAX_HOSTS)


if __name__ == "__main__":
    unittest.main()
