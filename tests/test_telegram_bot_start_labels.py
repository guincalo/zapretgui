"""Каждая кликабельная ссылка на бота @zapretvpns_bot несёт свою рекламную метку start."""

from __future__ import annotations

import os
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if "winreg" not in sys.modules:  # на Linux-хосте модуля нет, а telegram_links его импортирует
    sys.modules["winreg"] = types.ModuleType("winreg")

from config import telegram_links

BOT = "zapretvpns_bot"
START_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
README = Path(__file__).resolve().parents[1] / "docs" / "README.md"


class TelegramUrlsTests(unittest.TestCase):
    def test_start_is_added_to_both_url_forms(self) -> None:
        tg_url, https_url = telegram_links.telegram_urls(BOT, start="gui_about_kvn")
        self.assertEqual(tg_url, f"tg://resolve?domain={BOT}&start=gui_about_kvn")
        self.assertEqual(https_url, f"https://t.me/{BOT}?start=gui_about_kvn")

    def test_without_start_urls_are_unchanged(self) -> None:
        self.assertEqual(
            telegram_links.telegram_urls("zaprethelp"),
            ("tg://resolve?domain=zaprethelp", "https://t.me/zaprethelp"),
        )
        self.assertEqual(
            telegram_links.telegram_urls("chan", post=5),
            ("tg://resolve?domain=chan&post=5", "https://t.me/chan/5"),
        )
        self.assertEqual(
            telegram_links.telegram_urls("", slug="abc"),
            ("tg://addlist?slug=abc", "https://t.me/addlist/abc"),
        )

    def test_invalid_start_is_rejected(self) -> None:
        for bad in ("", "ref_123", "a b", "a?b=c", "a&b", "я", "x" * 65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                telegram_links.telegram_urls(BOT, start=bad)

    def test_open_uses_tg_scheme_when_telegram_installed(self) -> None:
        with patch.object(telegram_links, "is_telegram_installed", return_value=True), patch(
            "webbrowser.open"
        ) as opener:
            telegram_links.open_telegram_link(BOT, start="gui_premium_buy")
        opener.assert_called_once_with(f"tg://resolve?domain={BOT}&start=gui_premium_buy")

    def test_open_uses_https_when_telegram_missing(self) -> None:
        with patch.object(telegram_links, "is_telegram_installed", return_value=False), patch(
            "webbrowser.open"
        ) as opener:
            telegram_links.open_telegram_link(BOT, start="gui_premium_buy")
        opener.assert_called_once_with(f"https://t.me/{BOT}?start=gui_premium_buy")


class ClickLabelsTests(unittest.TestCase):
    def test_premium_buy_button_label(self) -> None:
        from donater import commands

        with patch("config.telegram_links.open_telegram_link") as open_link:
            result = commands.open_extend_bot()
        self.assertTrue(result.ok)
        open_link.assert_called_once_with(BOT, start="gui_premium_buy")

    def test_premium_buy_fallback_label(self) -> None:
        from donater import commands

        with patch("config.telegram_links.open_telegram_link", side_effect=RuntimeError("x")), patch(
            "webbrowser.open"
        ) as opener:
            result = commands.open_extend_bot()
        self.assertTrue(result.ok)
        opener.assert_called_once_with(f"https://t.me/{BOT}?start=gui_premium_buy")

    def test_about_commands_pass_start(self) -> None:
        from about import commands

        with patch("config.telegram_links.open_telegram_link") as open_link:
            commands.open_telegram(BOT, start="gui_about_kvn")
        open_link.assert_called_once_with(BOT, post=None, start="gui_about_kvn")

    def test_about_kvn_button_label(self) -> None:
        from app.page_names import PageName
        from ui.page_deps.system import build_about_page_kwargs

        class _Feature:
            def __init__(self) -> None:
                self.actions: dict[str, object] = {}

            def create_external_action_worker(self, request_id, *, action_name, action_fn, parent=None):
                self.actions[action_name] = action_fn
                return None

        feature = _Feature()
        kwargs = build_about_page_kwargs(
            page_name=PageName.ABOUT,
            external_actions_feature=feature,
            updater_feature=object(),
            show_page=lambda *_args, **_kwargs: None,
            ui_state_store=None,
        )
        kwargs["create_open_action_worker"](0, action_name="kvn_bot")
        with patch("config.telegram_links.open_telegram_link") as open_link:
            feature.actions["kvn_bot"]()
        open_link.assert_called_once_with(BOT, post=None, start="gui_about_kvn")


class ReadmeLabelsTests(unittest.TestCase):
    def test_every_bot_link_in_readme_has_label(self) -> None:
        text = README.read_text(encoding="utf-8")
        links = re.findall(rf"https://(?:t|telegram)\.me/{BOT}[^\s\"')]*", text)
        self.assertEqual(len(links), 2)
        labels = []
        for link in links:
            match = re.fullmatch(rf"https://(?:t|telegram)\.me/{BOT}\?start=(.+)", link)
            self.assertIsNotNone(match, link)
            label = match.group(1)
            self.assertRegex(label, START_RE)
            self.assertTrue(label.startswith("gui_"))
            labels.append(label)
        self.assertEqual(sorted(labels), ["gui_readme_badge", "gui_readme_vpn"])


if __name__ == "__main__":
    unittest.main()
