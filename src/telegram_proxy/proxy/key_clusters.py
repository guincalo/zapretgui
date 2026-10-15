"""На каком кластере дата-центра живёт ключ медиа-сессии Telegram Desktop.

У каждого DC два кластера: обычный и медиа. Временный ключ шифрования,
созданный на одном, другой не знает и отвечает транспортной ошибкой -404.
Наши пути ведут медиа в разные кластеры: фронты Cloudflare — в обычный (у них
нет хостов kwsN-1), релей kwsN-1, туннель и SOCKS — в медиа. Получив -404,
Desktop выбрасывает ключ и создаёт новый (2–3 с без картинок), а через MTProxy
может уничтожить и ключ всего DC (ZaStoGram, auth.cpp dropMismatchedTemporaryKey).

Номер ключа (первые 8 байт пакета) идёт открыто, поэтому прокси запоминает,
какой кластер принял ключ, ставит его пути первыми и молча переводит
соединение, если сервер ответил -404. Только в памяти: ключи Desktop живут
сутки, а после перезапуска прокси память наполняется заново с первого ответа.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

CLUSTER_REGULAR = "regular"
CLUSTER_MEDIA = "media"

KEY_NOT_FOUND = -404
KEY_NOT_FOUND_PACKET = KEY_NOT_FOUND.to_bytes(4, "little", signed=True)

CAPACITY = 512
# Кластер, принявший ключ не дольше этого назад, считается домом ключа, до
# которого просто не дотянулись: -404 с запасного пути тогда не отдаётся Desktop.
HOME_FRESH_SECONDS = 120.0
# Не чаще одного раза за это время соединение закрывается молча вместо -404.
QUIET_MISS_SECONDS = 60.0


def key_id_of(packet: bytes) -> bytes:
    """Номер ключа из пакета клиента; пустой — пакет без ключа (создание ключа)."""
    key_id = bytes(packet[:8])
    if len(key_id) < 8 or key_id == b"\0" * 8:
        return b""
    return key_id


def is_key_not_found(packet: bytes) -> bool:
    return bytes(packet) == KEY_NOT_FOUND_PACKET


def cluster_title(cluster: str) -> str:
    return "медиа-кластер" if cluster == CLUSTER_MEDIA else "обычный кластер"


@dataclass(slots=True)
class _Home:
    cluster: str
    ok_at: float
    quiet_at: float = -1e9


class KeyClusters:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, capacity: int = CAPACITY) -> None:
        self._clock = clock
        self._capacity = max(1, int(capacity))
        self._homes: OrderedDict[bytes, _Home] = OrderedDict()

    def home(self, key_id: bytes) -> str:
        entry = self._homes.get(key_id) if key_id else None
        return entry.cluster if entry is not None else ""

    def note_ok(self, key_id: bytes, cluster: str) -> None:
        if not key_id or not cluster:
            return
        entry = self._homes.get(key_id)
        if entry is None or entry.cluster != cluster:
            entry = _Home(cluster=cluster, ok_at=self._clock())
        else:
            entry.ok_at = self._clock()
        self._homes[key_id] = entry
        self._homes.move_to_end(key_id)
        while len(self._homes) > self._capacity:
            self._homes.popitem(last=False)

    def forget(self, key_id: bytes) -> None:
        self._homes.pop(key_id, None)

    def may_close_quietly(self, key_id: bytes, rejected_cluster: str) -> bool:
        """Ключ недавно работал на другом кластере, а сейчас тот недоступен.

        Тогда соединение лучше закрыть без -404: Desktop переподключится со
        своим ключом, и путь его кластера, скорее всего, уже ответит. Не чаще
        раза в QUIET_MISS_SECONDS: если дом ключа не возвращается, ключу пора
        переехать, и -404 уходит в Desktop.
        """
        entry = self._homes.get(key_id) if key_id else None
        if entry is None or entry.cluster == rejected_cluster:
            return False
        now = self._clock()
        if now - entry.ok_at > HOME_FRESH_SECONDS or now - entry.quiet_at < QUIET_MISS_SECONDS:
            return False
        entry.quiet_at = now
        return True


__all__ = [
    "CLUSTER_MEDIA",
    "CLUSTER_REGULAR",
    "KEY_NOT_FOUND_PACKET",
    "KeyClusters",
    "cluster_title",
    "is_key_not_found",
    "key_id_of",
]
