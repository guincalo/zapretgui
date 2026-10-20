"""Быстрые карточки известных сервисов: данные, логотипы, выбор одним нажатием."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from blockcheck.quick_services import QUICK_SERVICES, service_for_target  # noqa: E402
from blockcheck.ui.brand_icons import site_brand  # noqa: E402


class ModelTests(unittest.TestCase):
    def test_every_card_has_a_brand_logo_and_a_unique_target(self) -> None:
        for service in QUICK_SERVICES:
            self.assertIsNotNone(site_brand(service.key), service.key)
        targets = [item.target for item in QUICK_SERVICES]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(len({item.key for item in QUICK_SERVICES}), len(QUICK_SERVICES))

    def test_the_best_known_services_come_first(self) -> None:
        self.assertEqual([item.key for item in QUICK_SERVICES[:3]], ["youtube", "discord", "telegram"])

    def test_youtube_card_checks_the_video_server_of_this_network(self) -> None:
        youtube = QUICK_SERVICES[0]

        self.assertEqual(youtube.target, "googlevideo.com")
        self.assertIn("именно вашей сети", youtube.hint)

    def test_card_is_found_by_the_address_in_the_field(self) -> None:
        self.assertEqual(service_for_target("https://Discord.com/").key, "discord")
        self.assertEqual(service_for_target("googlevideo.com").key, "youtube")
        self.assertIsNone(service_for_target("example.org"))
        self.assertIsNone(service_for_target(""))


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_click_reports_the_address_and_target_marks_one_card(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        widget = QuickServiceCards()
        picked: list[str] = []
        widget.picked.connect(picked.append)
        discord = next(card for card in widget.cards() if card.service.key == "discord")

        discord.click()
        widget.set_target("discord.com")

        self.assertEqual(picked, ["discord.com"])
        self.assertEqual(widget.selected_key(), "discord")
        widget.set_target("example.org")
        self.assertEqual(widget.selected_key(), "")

    def test_one_card_per_service_with_logo_and_name(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        widget = QuickServiceCards()

        self.assertEqual(len(widget.cards()), len(QUICK_SERVICES))
        for card in widget.cards():
            self.assertTrue(card.logo.icon_name())
            self.assertEqual(card.name.text(), card.service.title)

    def test_card_tells_screen_readers_whether_it_is_chosen(self) -> None:
        from blockcheck.ui.quick_service_cards import QuickServiceCards

        widget = QuickServiceCards()
        widget.set_target("telegram.org")
        telegram = next(card for card in widget.cards() if card.service.key == "telegram")

        self.assertIn("Telegram, выбрано", telegram.accessibleName())
        self.assertIn("не выбрано", widget.cards()[0].accessibleName())


class DomainPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        from unittest.mock import Mock

        from dns.ui.domain_lookup_page import DomainLookupPage

        page = DomainLookupPage(dns_feature=Mock(), embedded=True)
        self.addCleanup(page.deleteLater)
        return page

    def test_card_fills_the_field_and_starts_the_check_of_the_site_page(self) -> None:
        from unittest.mock import patch

        page = self._page()
        youtube = next(card for card in page.quick_cards.cards() if card.service.key == "youtube")

        with patch.object(page, "start_lookup") as start:
            youtube.click()

        # Для проверки домена у YouTube берётся страница сайта: видеосерверы добавляются к ней сами.
        self.assertEqual(page.target_input.text(), "www.youtube.com")
        start.assert_called_once()
        self.assertEqual(page.quick_cards.selected_key(), "youtube")

    def test_cards_are_off_while_a_check_is_running(self) -> None:
        page = self._page()

        page._set_running(True)
        self.assertFalse(page.quick_cards.isEnabled())
        page._set_running(False)
        self.assertTrue(page.quick_cards.isEnabled())


if __name__ == "__main__":
    unittest.main()
