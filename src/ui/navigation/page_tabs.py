"""Вкладки страницы как экраны журнала: одно объявление вместо ручной работы.

Страница с вкладками объявляет их один раз (``BasePage.bind_navigation_tabs``):
ключи вкладок, как узнать открытую, как открыть нужную и какой сигнал говорит,
что вкладка сменилась. Дальше журнал сам видит каждую смену вкладки — откуда бы
её ни переключили (щелчок, поиск, ссылка внутри страницы) — и «назад» возвращает
на ту вкладку, где человек был.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ui.navigation.history import ScreenState


TAB_PREFIX = "tab:"


class NavigationTabs:
    def __init__(
        self,
        keys: Sequence[str],
        *,
        current_index: Callable[[], int],
        open_index: Callable[[int], object],
        title: Callable[[int], str] | None = None,
        pending_key: Callable[[], str | None] | None = None,
    ) -> None:
        self.keys = tuple(str(key) for key in keys)
        self._current_index = current_index
        self._open_index = open_index
        self._title = title
        # Вкладку могли заказать до того, как страница достроилась: она и есть текущий экран.
        self._pending_key = pending_key

    def current_key(self) -> str:
        pending = self._pending_key() if self._pending_key is not None else None
        if pending in self.keys:
            return str(pending)
        index = int(self._current_index())
        return self.keys[index] if 0 <= index < len(self.keys) else self.keys[0]

    def screen(self) -> ScreenState:
        key = self.current_key()
        title = ""
        if self._title is not None:
            try:
                title = str(self._title(self.keys.index(key)) or "")
            except Exception:
                title = ""
        return ScreenState(key=f"{TAB_PREFIX}{key}", title=title)

    def restore(self, screen: ScreenState) -> bool:
        if not screen.key.startswith(TAB_PREFIX):
            return False
        key = screen.key[len(TAB_PREFIX):]
        if key not in self.keys:
            return False
        self._open_index(self.keys.index(key))
        return True


def pivot_item_text(pivot, key: str) -> str:
    """Подпись вкладки переключателя (Pivot/SegmentedWidget) — заголовок экрана в списке истории."""
    items = getattr(pivot, "items", None) or {}
    item = items.get(key) if isinstance(items, dict) else None
    text = getattr(item, "text", None)
    return str(text() or "").strip() if callable(text) else ""


__all__ = ["NavigationTabs", "TAB_PREFIX", "pivot_item_text"]
