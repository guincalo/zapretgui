"""Журнал экранов окна: по нему ходят кнопки «назад» и «вперёд».

Экран — это страница вместе с тем, что открыто внутри неё: вкладка, отчёт
карточки, подробности стратегии. Журнал устроен как в браузере: список
посещённых экранов и указатель на текущий. «Назад» и «вперёд» двигают
указатель, а переход на новый экран отрезает всё, что было правее.

Здесь только сам список. Показом страниц занимается
``ui.navigation.history_controller``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.page_names import PageName


# Столько экранов помнит журнал; самые старые вытесняются.
HISTORY_LIMIT = 50


@dataclass(frozen=True)
class ScreenState:
    """Что открыто внутри страницы.

    ``key`` отличает один экран страницы от другого; пустой — страница в
    исходном виде. ``title`` — подпись для списка истории. ``payload`` — то,
    что нужно странице, чтобы открыть этот экран заново.
    """

    key: str = ""
    title: str = ""
    payload: object = None


@dataclass(frozen=True)
class HistoryEntry:
    page: PageName
    screen: ScreenState = ScreenState()

    def same_screen(self, other: "HistoryEntry | None") -> bool:
        return other is not None and self.page == other.page and self.screen.key == other.screen.key


class NavigationHistory:
    def __init__(self, *, limit: int = HISTORY_LIMIT) -> None:
        self._limit = max(2, int(limit))
        self._entries: list[HistoryEntry] = []
        self._index = -1
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    @property
    def index(self) -> int:
        return self._index

    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)

    def current(self) -> HistoryEntry | None:
        return self._entries[self._index] if 0 <= self._index < len(self._entries) else None

    def can_go_back(self) -> bool:
        return self._index > 0

    def can_go_forward(self) -> bool:
        return 0 <= self._index < len(self._entries) - 1

    def back_entries(self) -> tuple[tuple[int, HistoryEntry], ...]:
        """Экраны позади текущего, ближайший первым — так их показывает список истории."""
        return tuple((index, self._entries[index]) for index in range(self._index - 1, -1, -1))

    def forward_entries(self) -> tuple[tuple[int, HistoryEntry], ...]:
        return tuple((index, self._entries[index]) for index in range(self._index + 1, len(self._entries)))

    def visit(self, entry: HistoryEntry) -> None:
        """Человек перешёл на экран. Тот же экран, что уже текущий, только обновляется."""
        if entry.same_screen(self.current()):
            if self._entries[self._index] != entry:
                self._entries[self._index] = entry
                self._changed()
            return
        del self._entries[self._index + 1 :]
        self._entries.append(entry)
        overflow = len(self._entries) - self._limit
        if overflow > 0:
            del self._entries[:overflow]
        self._index = len(self._entries) - 1
        self._changed()

    def replace_current(self, entry: HistoryEntry) -> None:
        """Страница сама уточнила экран, на котором человек уже стоит (пришли данные,
        открытый предмет исчез): это та же запись, а не новый шаг. Правая часть
        журнала («вперёд») остаётся."""
        if self.current() is None:
            self.visit(entry)
            return
        if self._entries[self._index] == entry:
            return
        self._entries[self._index] = entry
        self._merge_equal_neighbours()
        self._changed()

    def prune(self, keep: Callable[[HistoryEntry], bool]) -> None:
        """Убирает записи, которые больше нельзя открыть (страница недоступна в
        новом режиме). Указатель остаётся на текущей записи, а если убрана она —
        на ближайшей позади."""
        if all(keep(entry) for entry in self._entries):
            return
        kept: list[HistoryEntry] = []
        index = -1
        for position, entry in enumerate(self._entries):
            if keep(entry):
                kept.append(entry)
            if position == self._index:
                index = len(kept) - 1
        self._entries = kept
        self._index = index if kept else -1
        if kept and self._index < 0:
            self._index = 0
        self._merge_equal_neighbours()
        self._changed()

    def move_to(self, index: int) -> HistoryEntry | None:
        """Ставит указатель на запись; сам экран показывает вызывающий."""
        if not 0 <= index < len(self._entries):
            return None
        if index != self._index:
            self._index = index
            self._changed()
        return self._entries[index]

    def drop(self, index: int) -> None:
        """Убирает запись, экран которой больше нельзя открыть."""
        if not 0 <= index < len(self._entries):
            return
        del self._entries[index]
        if index < self._index or self._index >= len(self._entries):
            self._index -= 1
        # После удаления соседями могли оказаться две записи одного экрана.
        self._merge_equal_neighbours()
        self._changed()

    def rewrite(self, transform: Callable[[HistoryEntry], HistoryEntry]) -> None:
        """Переписывает записи, когда экран сменил имя, оставаясь тем же экраном.

        Так бывает, когда у открытого на странице предмета сменился ключ: профиль
        после правки получил новую ссылку. Без этого журнал держал бы один и тот
        же экран под двумя именами, и шаг «назад» с нового имени на старое
        визуально ничего бы не менял.
        """
        entries = [transform(entry) for entry in self._entries]
        if entries == self._entries:
            return
        self._entries = entries
        self._merge_equal_neighbours()
        self._changed()

    def _merge_equal_neighbours(self) -> None:
        position = 1
        while position < len(self._entries):
            if self._entries[position].same_screen(self._entries[position - 1]):
                del self._entries[position]
                if position <= self._index:
                    self._index -= 1
            else:
                position += 1


__all__ = ["HISTORY_LIMIT", "HistoryEntry", "NavigationHistory", "ScreenState"]
