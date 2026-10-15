"""Устройство навигации окна: одно место решает, что считать шагом журнала.

Каждый тест — сценарий ошибки, найденной аудитом навигации 2026-10-15:
стартовая страница не попадала в журнал, «закрыть» добавляло шаг вперёд,
поправка страницы обрезала «вперёд», удалённый предмет оставался в журнале,
экскурсия засоряла журнал, после смены режима «назад» горела впустую,
вкладки страниц не запоминались.
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_SRC = Path(__file__).resolve().parent.parent / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QStackedWidget, QWidget

from app.page_names import PageName
from ui.navigation.history import HistoryEntry, NavigationHistory, ScreenState
from ui.navigation.history_controller import WindowNavigationHistory
from ui.navigation.page_tabs import NavigationTabs
from ui.pages.base_page import BasePage

A, B, C = PageName.ZAPRET2_MODE_CONTROL, PageName.BLOCKCHECK, PageName.NETWORK


def _entry(page: PageName, key: str = "") -> HistoryEntry:
    return HistoryEntry(page, ScreenState(key=key, title=key))


class _Page(QObject):
    """Страница со всеми сигналами навигации; экраны — строки из ``known``."""

    navigation_screen_changed = pyqtSignal()
    navigation_screen_corrected = pyqtSignal()
    navigation_screen_returned = pyqtSignal()
    navigation_screen_unavailable = pyqtSignal()

    def __init__(self, *known: str) -> None:
        super().__init__()
        self.known = {"", *known}
        self.key = ""

    def navigation_screen(self) -> ScreenState:
        return ScreenState(key=self.key, title=self.key)

    def restore_navigation_screen(self, screen: ScreenState) -> bool:
        if screen.key not in self.known:
            return False
        self.key = screen.key
        return True

    def set_navigation_up(self, go_up) -> None:
        self.go_up = go_up


class _Host:
    """Окно в миниатюре: настоящий стек страниц, сигнал которого слушает навигатор."""

    def __init__(self) -> None:
        self.stack = QStackedWidget()
        self.pages: dict[PageName, _Page] = {}
        self.widgets: dict[PageName, QWidget] = {}
        self.allowed = {A, B, C}
        self.navigation_history = WindowNavigationHistory(object(), self)
        for name, known in ((A, ()), (B, ("tab", "card", "card-2")), (C, ())):
            page = _Page(*known)
            widget = QWidget()
            self.pages[name] = page
            self.widgets[name] = widget
            self.stack.addWidget(widget)
            self.navigation_history.attach_page(page)
        self.navigation_history.install(self.stack)
        self.settle()

    @staticmethod
    def settle() -> None:
        QApplication.processEvents()

    def current_page(self):
        widget = self.stack.currentWidget()
        return next((self.pages[name] for name, w in self.widgets.items() if w is widget), None)

    def page_name_of(self, page):
        return next((name for name, loaded in self.pages.items() if loaded is page), None)

    def get_loaded_page(self, page_name):
        return self.pages.get(page_name)

    def is_page_allowed(self, page_name) -> bool:
        return page_name in self.allowed

    def show_page(self, page_name, *, allow_internal: bool = False) -> bool:
        if page_name not in self.allowed:
            return False
        self.stack.setCurrentWidget(self.widgets[page_name])
        return True

    # Действия человека.
    def open(self, page_name: PageName) -> None:
        self.show_page(page_name)
        self.settle()

    def open_inside(self, key: str) -> None:
        page = self.current_page()
        page.key = key
        page.navigation_screen_changed.emit()
        self.settle()

    def where(self) -> tuple[PageName | None, str]:
        page = self.current_page()
        return self.page_name_of(page), page.key

    def keys(self) -> list[tuple[str, str]]:
        return [(e.page.name, e.screen.key) for e in self.navigation_history.history.entries()]


class _Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])


class HistoryModelTests(unittest.TestCase):
    def test_replace_current_keeps_the_forward_part(self) -> None:
        history = NavigationHistory()
        for entry in (_entry(A), _entry(B, "tab"), _entry(C)):
            history.visit(entry)
        history.move_to(1)

        history.replace_current(_entry(B, "card"))

        self.assertEqual(history.entries(), (_entry(A), _entry(B, "card"), _entry(C)))
        self.assertEqual(history.index, 1)

    def test_prune_drops_unavailable_pages_and_keeps_the_pointer_near(self) -> None:
        history = NavigationHistory()
        for entry in (_entry(A), _entry(B), _entry(C), _entry(B, "x")):
            history.visit(entry)
        history.move_to(2)

        history.prune(lambda entry: entry.page != C)

        self.assertEqual(history.entries(), (_entry(A), _entry(B), _entry(B, "x")))
        self.assertEqual(history.index, 1)


class NavigatorTests(_Case):
    def test_start_page_is_the_first_step(self) -> None:
        host = _Host()
        self.assertEqual(host.keys(), [("ZAPRET2_MODE_CONTROL", "")])

        host.open(B)

        self.assertTrue(host.navigation_history.can_go_back())

    def test_page_switch_bypassing_show_page_is_still_recorded(self) -> None:
        # Боковое меню библиотеки переключает стек напрямую (switchTo), мимо show_page.
        host = _Host()
        host.stack.setCurrentWidget(host.widgets[C])
        host.settle()

        self.assertEqual(host.keys()[-1], ("NETWORK", ""))

    def test_correction_by_the_page_does_not_cut_forward(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open_inside("card")
        host.open(C)
        nav.go_back()
        host.settle()
        self.assertEqual(host.where(), (B, "card"))

        # Данные пришли, и страница сама уточнила экран: это не шаг человека.
        page = host.pages[B]
        page.key = "card-2"
        page.navigation_screen_corrected.emit()
        host.settle()

        self.assertEqual(host.keys(), [("ZAPRET2_MODE_CONTROL", ""), ("BLOCKCHECK", ""), ("BLOCKCHECK", "card-2"), ("NETWORK", "")])
        self.assertTrue(nav.can_go_forward())

    def test_unavailable_screen_is_dropped_and_the_step_goes_on(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open_inside("card")
        host.open(C)
        nav.go_back()
        host.settle()

        # Предмета больше нет (профиль удалён): страница говорит об этом журналу.
        host.pages[B].navigation_screen_unavailable.emit()
        host.settle()

        self.assertEqual(host.where(), (B, ""))
        self.assertNotIn(("BLOCKCHECK", "card"), host.keys())

    def test_link_up_to_the_page_you_came_from_is_a_step_back(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(C)
        host.open(B)

        # Строка пути «Настройка DNS» на вложенной странице.
        nav.navigate_up(C, lambda: host.show_page(C))
        host.settle()

        self.assertEqual(host.where(), (C, ""))
        self.assertEqual(host.keys(), [("ZAPRET2_MODE_CONTROL", ""), ("NETWORK", ""), ("BLOCKCHECK", "")])
        self.assertTrue(nav.can_go_forward())

    def test_link_up_passes_over_the_screens_of_the_page_being_left(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(C)
        host.open(B)
        host.open_inside("tab")
        host.open_inside("card")

        # С вложенного экрана страницы — строка пути на страницу, откуда пришли.
        nav.navigate_up(C, lambda: host.show_page(C))
        host.settle()

        self.assertEqual(host.where(), (C, ""))
        self.assertEqual(nav.history.index, 1)
        self.assertEqual(len(nav.history.entries()), 5)
        # «Вперёд» возвращает внутрь страницы, с которой ушли.
        self.assertTrue(nav.go_forward())
        host.settle()
        self.assertEqual(host.where(), (B, ""))

    def test_link_up_elsewhere_is_a_new_step(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)

        nav.navigate_up(C, lambda: host.show_page(C))
        host.settle()

        self.assertEqual(host.keys()[-1], ("NETWORK", ""))
        self.assertFalse(nav.can_go_forward())

    def test_closing_an_inner_screen_steps_back_instead_of_forward(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open_inside("tab")
        host.open_inside("card")
        page = host.pages[B]

        def close_card() -> None:
            page.key = "tab"
            page.navigation_screen_changed.emit()

        page.go_up(None, close_card)
        host.settle()

        self.assertEqual(host.where(), (B, "tab"))
        # Карточка осталась впереди, а не стала новой записью позади вкладки.
        self.assertEqual(host.keys()[-2:], [("BLOCKCHECK", "tab"), ("BLOCKCHECK", "card")])
        self.assertTrue(nav.can_go_forward())

    def test_return_to_the_screen_just_behind_moves_the_pointer_back(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open_inside("tab")
        host.open_inside("card")

        # Esc внутри отчёта: страница сама вернулась на уровень выше.
        page = host.pages[B]
        page.key = "tab"
        page.navigation_screen_returned.emit()
        host.settle()

        self.assertEqual(host.keys()[-2:], [("BLOCKCHECK", "tab"), ("BLOCKCHECK", "card")])
        self.assertEqual(nav.history.index, 2)
        self.assertTrue(nav.can_go_forward())

    def test_return_to_a_screen_not_behind_is_a_new_step(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open_inside("card")

        page = host.pages[B]
        page.key = "tab"
        page.navigation_screen_returned.emit()
        host.settle()

        self.assertEqual(host.keys()[-1], ("BLOCKCHECK", "tab"))
        self.assertFalse(nav.can_go_forward())

    def test_tour_steps_are_not_recorded(self) -> None:
        host = _Host()
        nav = host.navigation_history
        nav.suspend()
        host.open(B)
        host.open_inside("card")
        host.open(C)
        self.assertFalse(nav.go_back())
        nav.resume()
        host.settle()

        # Экскурсия не оставила следов, а человек вернулся туда, откуда её начал.
        self.assertEqual(host.keys(), [("ZAPRET2_MODE_CONTROL", "")])
        self.assertEqual(host.where(), (A, ""))

    def test_after_the_tour_the_inner_screen_it_started_from_is_reopened(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open_inside("card")
        nav.suspend()
        host.open(C)
        host.pages[B].key = ""
        nav.resume()
        host.settle()

        self.assertEqual(host.where(), (B, "card"))
        self.assertEqual(host.keys()[-1], ("BLOCKCHECK", "card"))
        self.assertFalse(nav.can_go_forward())

    def test_mode_change_forgets_pages_of_the_old_mode_at_once(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open(C)

        host.allowed.discard(B)
        host.allowed.discard(A)
        nav.prune_unavailable()

        self.assertEqual(host.keys(), [("NETWORK", "")])
        self.assertFalse(nav.can_go_back())

    def test_history_list_choice_is_matched_by_index(self) -> None:
        host = _Host()
        nav = host.navigation_history
        host.open(B)
        host.open(C)

        self.assertTrue(nav.go_to_index(0))
        host.settle()

        self.assertEqual(host.where(), (A, ""))
        self.assertEqual(nav.history.index, 0)


class _TabbedPage(BasePage):
    def __init__(self) -> None:
        super().__init__("Вкладки")
        self.stack = QStackedWidget(self.content)
        for _ in range(3):
            self.stack.addWidget(QWidget(self.stack))
        self.add_widget(self.stack)
        self.bind_navigation_tabs(
            ("one", "two", "three"),
            current_index=self.stack.currentIndex,
            open_index=self.stack.setCurrentIndex,
            changed=self.stack.currentChanged,
        )


class NavigationTabsTests(_Case):
    def test_tab_switch_from_anywhere_is_reported_and_restorable(self) -> None:
        page = _TabbedPage()
        self.addCleanup(page.deleteLater)
        heard = []
        page.navigation_screen_changed.connect(lambda: heard.append(page.navigation_screen().key))

        page.stack.setCurrentIndex(2)

        self.assertEqual(heard, ["tab:three"])
        self.assertTrue(page.restore_navigation_screen(ScreenState(key="tab:two")))
        self.assertEqual(page.stack.currentIndex(), 1)
        self.assertFalse(page.restore_navigation_screen(ScreenState(key="tab:gone")))

    def test_history_list_names_a_tab_in_the_current_language(self) -> None:
        labels = {"one": "Логи"}
        tabs = NavigationTabs(("one",), current_index=lambda: 0, open_index=lambda _i: None, title=lambda i: labels["one"])
        recorded = tabs.screen()

        labels["one"] = "Logs"

        self.assertEqual(recorded.title, "Логи")
        self.assertEqual(tabs.screen_title(recorded), "Logs")

    def test_describe_asks_the_page_for_the_title(self) -> None:
        host = _Host()
        page = host.pages[B]
        page.navigation_screen_title = lambda screen: "Logs" if screen.key == "tab" else ""
        nav = host.navigation_history
        with unittest.mock.patch("ui.navigation.text_sync.get_nav_label", return_value="BlockCheck"):
            self.assertEqual(nav.describe(HistoryEntry(B, ScreenState(key="tab", title="Логи"))), "BlockCheck — Logs")
            self.assertEqual(nav.describe(HistoryEntry(B, ScreenState(key="card", title="Карточка"))), "BlockCheck — Карточка")

    def test_pending_tab_is_the_current_screen(self) -> None:
        tabs = NavigationTabs(("a", "b"), current_index=lambda: 0, open_index=lambda _i: None, pending_key=lambda: "b")
        self.assertEqual(tabs.screen().key, "tab:b")


# Переключатели вкладок, по которым видно страницу с вкладками.
_TAB_BAR_CLASSES = {"SegmentedWidget", "Pivot", "TabBar"}
# Страницы, где такой переключатель — не вкладки, а выбор значения: экраном он не является.
_NOT_TABS = {
    "dns/ui/page.py": "переключатель — фильтр списка серверов",
    "ui/pages/appearance_page.py": "переключатели — настройки вида (режим отображения, значки меню)",
}


def _modules_with_tab_bars() -> list[Path]:
    from ui.page_registry import PAGE_CLASS_SPECS

    found = []
    for module_name, _class_name in set(PAGE_CLASS_SPECS.values()):
        path = PROJECT_SRC / (module_name.replace(".", "/") + ".py")
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        if names & _TAB_BAR_CLASSES or {"tabs_pivot", "_tabs_pivot", "_pivot", "pivot"} & attrs:
            found.append(path)
    return found


class TabbedPagesTakePartInHistoryTests(unittest.TestCase):
    """Страж: страница с переключателем вкладок обязана вести их в журнале экранов.
    Иначе «назад» перепрыгивает вкладку, а «вперёд» на неё не вернёт."""

    def test_every_page_with_a_tab_bar_reports_its_tabs(self) -> None:
        modules = _modules_with_tab_bars()
        self.assertTrue(modules, "страницы с вкладками не найдены — проверка сломалась")
        missing = []
        for path in modules:
            if str(path.relative_to(PROJECT_SRC)) in _NOT_TABS:
                continue
            text = path.read_text(encoding="utf-8")
            if "bind_navigation_tabs(" not in text and "NavigationTabs(" not in text and "def navigation_screen" not in text:
                missing.append(str(path.relative_to(PROJECT_SRC)))
        self.assertEqual(missing, [], "эти страницы с вкладками не сообщают журналу, какая вкладка открыта")


if __name__ == "__main__":
    unittest.main()
