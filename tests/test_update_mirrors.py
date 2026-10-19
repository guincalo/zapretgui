"""Имена git-сервера проекта: выбор открытого и приведение адресов (zapret_update.mirrors)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zapret_update import mirrors  # noqa: E402


def _document(*names, preferred="git2.zapret.moe"):
    return {
        "schema": 1,
        "families": {
            "git": {
                "preferred": preferred,
                "names": [
                    {"name": name, "blocked": blocked}
                    for name, blocked in names
                ],
            },
            "wiki": {"preferred": "wiki.zapret.moe", "names": [{"name": "wiki.zapret.moe"}]},
        },
    }


class HostAndUrlTests(unittest.TestCase):
    def test_only_project_git_names_are_git_hosts(self) -> None:
        for good in ("git.zapret.moe", "git2.zapret.moe", "GIT12.ZAPRET.MOE"):
            self.assertTrue(mirrors.is_git_host(good), good)
        for bad in ("", None, "evil.example", "git.zapret.moe.evil.example", "xgit2.zapret.moe",
                    "wiki.zapret.moe", "git.example.zapret.moe", "gitx.zapret.moe"):
            self.assertFalse(mirrors.is_git_host(bad), bad)

    def test_canonical_url_maps_any_mirror_back_to_the_main_name(self) -> None:
        path = "/zapretkvn/zapret-kvn/releases/download/v1/a.zip?x=1"
        self.assertEqual(mirrors.canonical_url(f"https://git3.zapret.moe{path}"), f"https://git.zapret.moe{path}")
        self.assertEqual(mirrors.canonical_url(f"https://git.zapret.moe{path}"), f"https://git.zapret.moe{path}")

    def test_foreign_or_unusual_urls_are_never_rewritten(self) -> None:
        for url in (
            "https://evil.example/a",
            "http://git2.zapret.moe/a",
            "https://git2.zapret.moe:8443/a",
            "https://user@git2.zapret.moe/a",
            "https://git2.zapret.moe.evil.example/a",
            "ftp://git2.zapret.moe/a",
            "не адрес",
        ):
            self.assertEqual(mirrors.canonical_url(url), url, url)
            self.assertEqual(mirrors.with_host(url, "git3.zapret.moe"), url, url)

    def test_with_host_accepts_only_git_names(self) -> None:
        url = "https://git.zapret.moe/api/v1/version"
        self.assertEqual(mirrors.with_host(url, "git4.zapret.moe"), "https://git4.zapret.moe/api/v1/version")
        self.assertEqual(mirrors.with_host(url, "evil.example"), url)
        self.assertEqual(mirrors.with_host(url, ""), url)


class OrderedHostsTests(unittest.TestCase):
    def test_preferred_first_blocked_last_unknown_and_foreign_dropped(self) -> None:
        document = _document(
            ("git.zapret.moe", True),
            ("git2.zapret.moe", False),
            ("git3.zapret.moe", False),
            ("git9.zapret.moe", None),
            ("git2.zapret.moe", False),
            ("evil.example", False),
            preferred="git3.zapret.moe",
        )
        self.assertEqual(
            mirrors.ordered_hosts(document),
            ["git3.zapret.moe", "git2.zapret.moe", "git9.zapret.moe", "git.zapret.moe"],
        )

    def test_unreadable_documents_give_nothing(self) -> None:
        for broken in (None, [], {}, {"families": {}}, {"families": {"git": {"names": 5}}}, "text"):
            self.assertEqual(mirrors.ordered_hosts(broken), [], repr(broken))


class MirrorListTests(unittest.TestCase):
    def _list(self, answers: dict, clock=None):
        asked: list[str] = []

        def fetch(url: str):
            asked.append(url)
            value = answers.get(url)
            if isinstance(value, Exception):
                raise value
            return value

        kwargs = {} if clock is None else {"clock": clock}
        return mirrors.MirrorList(fetch, **kwargs), asked

    def test_list_comes_from_the_first_name_that_answers(self) -> None:
        body = json.dumps(_document(("git2.zapret.moe", False), ("git.zapret.moe", True))).encode()
        mirror_list, asked = self._list({"https://git3.zapret.moe/_sites/mirrors.json": body})

        self.assertEqual(mirror_list.hosts(), ["git2.zapret.moe", "git.zapret.moe"])
        self.assertEqual(asked[0], "https://git2.zapret.moe/_sites/mirrors.json")
        self.assertEqual(asked[-1], "https://git3.zapret.moe/_sites/mirrors.json")

    def test_list_is_cached_and_asked_again_after_the_time_is_up(self) -> None:
        now = [0.0]
        body = json.dumps(_document(("git2.zapret.moe", False))).encode()
        mirror_list, asked = self._list({"https://git2.zapret.moe/_sites/mirrors.json": body}, clock=lambda: now[0])

        mirror_list.hosts()
        mirror_list.hosts()
        self.assertEqual(len(asked), 1)

        now[0] = mirrors.LIST_TTL_S + 1
        mirror_list.hosts()
        self.assertEqual(len(asked), 2)

    def test_when_nothing_answers_the_bootstrap_list_is_used_for_a_short_time(self) -> None:
        now = [0.0]
        mirror_list, asked = self._list({}, clock=lambda: now[0])

        self.assertEqual(mirror_list.hosts(), list(mirrors.BOOTSTRAP_HOSTS))
        self.assertEqual(mirrors.BOOTSTRAP_HOSTS[-1], mirrors.CANONICAL_HOST)
        first = len(asked)
        mirror_list.hosts()
        self.assertEqual(len(asked), first)  # повторно сразу не спрашивает

        now[0] = mirrors.FALLBACK_TTL_S + 1
        mirror_list.hosts()
        self.assertGreater(len(asked), first)

    def test_broken_answers_and_errors_are_skipped(self) -> None:
        good = json.dumps(_document(("git4.zapret.moe", False))).encode()
        mirror_list, _asked = self._list(
            {
                "https://git2.zapret.moe/_sites/mirrors.json": OSError("нет связи"),
                "https://git3.zapret.moe/_sites/mirrors.json": "не JSON".encode(),
                "https://git4.zapret.moe/_sites/mirrors.json": good,
            }
        )

        self.assertEqual(mirror_list.hosts(), ["git4.zapret.moe"])

    def test_remembered_name_goes_first_and_foreign_names_are_ignored(self) -> None:
        body = json.dumps(_document(("git2.zapret.moe", False), ("git3.zapret.moe", False))).encode()
        mirror_list, _asked = self._list({"https://git2.zapret.moe/_sites/mirrors.json": body})

        mirror_list.remember("git3.zapret.moe")
        self.assertEqual(mirror_list.hosts(), ["git3.zapret.moe", "git2.zapret.moe"])

        mirror_list.remember("evil.example")
        self.assertEqual(mirror_list.hosts()[0], "git3.zapret.moe")

        mirror_list.forget()
        self.assertEqual(mirror_list.hosts()[0], "git2.zapret.moe")


if __name__ == "__main__":
    unittest.main()
