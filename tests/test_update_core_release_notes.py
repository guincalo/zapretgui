from __future__ import annotations

"""Правило пасхалки «Удалена буква «О»»: одно на Zapret 2 GUI и Zapret KVN."""

import random

from zapret_update import release_notes


def _lucky(channel: str, count: int, head: str = "21.1.7") -> list[int]:
    return [n for n in range(count) if release_notes.lucky(channel, f"{head}.{n}")]


def test_lucky_releases_are_about_one_in_six() -> None:
    share = len(_lucky("dev", 3000)) / 3000

    assert 0.12 < share < 0.22


def test_joke_never_comes_back_sooner_than_after_the_gap() -> None:
    for channel in ("dev", "stable"):
        jokes = _lucky(channel, 800)
        assert all(b - a > release_notes.GAP for a, b in zip(jokes, jokes[1:]))


def test_the_155_158_streak_cannot_repeat() -> None:
    # Все четыре вышли с шуткой подряд — с перерывом так больше не бывает.
    assert sum(release_notes.lucky("dev", f"21.1.7.{n}") for n in range(155, 159)) <= 1


def test_only_a_one_item_list_gets_the_joke() -> None:
    version = f"21.1.7.{_lucky('dev', 1000)[0]}"

    assert release_notes.wants_joke("dev", version, 1)
    assert not release_notes.wants_joke("dev", version, 2)
    assert not release_notes.wants_joke("dev", version, 0)


def test_answer_depends_only_on_channel_and_version() -> None:
    version = f"21.1.7.{_lucky('dev', 1000)[0]}"
    random.seed(12345)  # чужое состояние случайных чисел ни на что не влияет

    assert all(release_notes.lucky("dev", version) for _ in range(5))
    assert release_notes.lucky("stable", "0.8.5") == release_notes.lucky("stable", "0.8.5")


def test_fast_count_matches_the_plain_rule() -> None:
    def plain(channel: str, version: str) -> bool:
        head, _, last = version.rpartition(".")
        since, joke = release_notes.GAP, False
        for n in range(int(last) + 1):
            joke = since >= release_notes.GAP and release_notes._roll(channel, f"{head}.{n}")
            since = 0 if joke else since + 1
        return joke

    for channel, head in (("dev", "21.1.7"), ("stable", "0.8"), ("dev", "9.9")):
        assert [release_notes.lucky(channel, f"{head}.{n}") for n in range(300)] == [plain(channel, f"{head}.{n}") for n in range(300)]
