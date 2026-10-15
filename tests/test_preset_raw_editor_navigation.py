"""Редактор пресета в журнале экранов: какой пресет открыт — часть экрана.

Раньше все открытия редактора были для журнала одним экраном: «назад» к пресету A
показывал пресет B, переименованный или удалённый пресет оставался в журнале.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from presets.ui.common.preset_subpage_base import PresetRawEditorPage, RawPresetRuntimeActions
from ui.navigation.history import ScreenState


class PresetRawEditorNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _page(self) -> PresetRawEditorPage:
        self.open_back = Mock()
        page = PresetRawEditorPage(
            create_raw_preset_load_worker=lambda *_args, **_kwargs: None,
            create_raw_preset_save_worker=lambda *_args, **_kwargs: None,
            create_raw_preset_activate_worker=lambda *_args, **_kwargs: None,
            create_raw_preset_action_worker=lambda *_args, **_kwargs: None,
            launch_method="zapret2",
            title="Пресет",
            open_back=self.open_back,
            open_root=lambda: None,
            runtime_actions=RawPresetRuntimeActions(
                start=lambda *_args, **_kwargs: None,
                stop=lambda *_args, **_kwargs: None,
                is_available=lambda: True,
            ),
            ui_state_store=None,
        )
        self.addCleanup(page.deleteLater)
        page._load_file = Mock()
        return page

    def _signals(self, page):
        heard = {"changed": [], "renamed": [], "unavailable": 0}
        page.navigation_screen_changed.connect(lambda: heard["changed"].append(page.navigation_screen().key))
        page.navigation_screens_renamed.connect(lambda old, new: heard["renamed"].append((old, new)))

        def unavailable() -> None:
            heard["unavailable"] += 1

        page.navigation_screen_unavailable.connect(unavailable)
        return heard

    def test_screen_is_the_open_preset_file(self) -> None:
        page = self._page()
        heard = self._signals(page)

        page.set_preset_file_name("Default v1.txt")
        page.set_preset_file_name("Мой.txt")

        self.assertEqual(heard["changed"], ["preset:default v1.txt", "preset:мой.txt"])
        screen = page.navigation_screen()
        self.assertEqual(screen.payload, "Мой.txt")
        self.assertEqual(screen.title, "Мой")

    def test_back_to_another_preset_opens_that_preset(self) -> None:
        page = self._page()
        page.set_preset_file_name("A.txt")
        first = page.navigation_screen()
        page.set_preset_file_name("B.txt")

        self.assertTrue(page.restore_navigation_screen(first))

        self.assertEqual(page.navigation_screen().key, first.key)
        self.assertEqual(page._preset_file_name, "A.txt")

    def test_preset_asked_while_saving_is_already_the_current_screen(self) -> None:
        page = self._page()
        page.set_preset_file_name("A.txt")
        # Прежний пресет ещё дописывается: открытие нового ждёт конца записи.
        page._run_after_raw_preset_save = Mock(return_value=False)

        page.set_preset_file_name("B.txt")

        self.assertEqual(page.navigation_screen().key, "preset:b.txt")

    def test_renamed_preset_renames_its_screens(self) -> None:
        page = self._page()
        page.set_preset_file_name("A.txt")
        before = page.navigation_screen()
        heard = self._signals(page)

        page._preset_name = "C"
        page._preset_file_name = "C.txt"

        self.assertEqual(heard["renamed"], [("preset:a.txt", "preset:c.txt")])
        after = page.rename_navigation_screen(before, "preset:a.txt", "preset:c.txt")
        self.assertEqual((after.key, after.payload), ("preset:c.txt", "C.txt"))
        other = ScreenState(key="preset:b.txt", payload="B.txt")
        self.assertIs(page.rename_navigation_screen(other, "preset:a.txt", "preset:c.txt"), other)

    def test_same_file_in_another_case_is_not_a_rename(self) -> None:
        page = self._page()
        page.set_preset_file_name("a.txt")
        heard = self._signals(page)

        page._preset_file_name = "A.txt"

        self.assertEqual(heard["renamed"], [])

    def test_preset_that_failed_to_load_leaves_the_history(self) -> None:
        page = self._page()
        page.set_preset_file_name("Удалён.txt")
        heard = self._signals(page)

        page._on_raw_preset_text_failed(page._raw_load_request_id, "файл не найден")

        self.assertEqual(heard["unavailable"], 1)

    def test_deleted_preset_leaves_the_history_and_list_opens_only_if_still_here(self) -> None:
        page = self._page()
        page.set_preset_file_name("A.txt")
        heard = self._signals(page)
        page._notify_preset_structure_changed = Mock()
        page._show_success = Mock()

        page._on_raw_preset_action_finished(page._raw_action_request_id, "delete", None, {"display_name": "A"})

        self.assertEqual(heard["unavailable"], 1)
        # Страница скрыта: журнал уже увёл человека на соседний экран.
        self.open_back.assert_not_called()


if __name__ == "__main__":
    unittest.main()
