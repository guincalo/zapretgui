from __future__ import annotations

"""Общие понятия скачивания: что качаем, откуда, отмена и виды ошибок."""

import threading
from dataclasses import dataclass
from urllib.parse import urlsplit


class UpdatePipelineError(RuntimeError):
    """Понятная пользователю ошибка обновления.

    ``code`` — короткая причина для сервера очереди (``why`` в вопросе
    слушателя): по ним видно, на каком шаге обновления срываются у людей.
    Пустой код — сообщать нечего (обновление отменили).
    """

    code = "other"


class UpdateCancelled(UpdatePipelineError):
    """Обновление остановлено по запросу приложения."""

    code = ""


class UpdateIntegrityError(UpdatePipelineError):
    """Файл не прошёл проверку размера или SHA-256."""

    code = "hash"


class LocalWriteError(UpdatePipelineError):
    """Файл не записать на диск: другие источники тут не помогут."""

    code = "disk"


class ReleaseLookupError(UpdatePipelineError):
    """Не удалось узнать, какой файл качать: данные выпуска не получены."""

    code = "meta"


class DownloadFailedError(UpdatePipelineError):
    """Ни один источник не отдал файл целиком."""

    code = "net"


class UpdateSignatureError(UpdatePipelineError):
    """Установщик не подтверждён подписанным списком выпуска: ставить нельзя.

    ``code`` берётся из причины отказа списка: ``sign`` (подпись или версия не
    сошлись), ``nosig`` (источник не отдал список), ``stale`` (версия не новее)…
    """

    def __init__(self, message: str, code: str = "sign") -> None:
        super().__init__(message)
        self.code = code


class UpdateNotNeeded(UpdatePipelineError):
    """Эта версия уже стоит или новее: ставить нечего, и это не неудача."""

    code = ""


class HandoffError(UpdatePipelineError):
    """Файл скачан, но передать его установщику не вышло."""

    code = "handoff"


@dataclass(frozen=True, slots=True)
class DownloadSource:
    """Один сервер с файлом выпуска.

    ``fallback_url`` — тот же файл на том же сервере по запасному адресу
    (у зеркал это HTTP). Он пробуется, только если основной адрес не дал
    соединиться.
    """

    url: str
    verify_ssl: bool
    name: str = ""
    fallback_url: str = ""

    @property
    def label(self) -> str:
        """Имя для журнала и сообщений: заданное или адрес сервера."""
        return self.name or urlsplit(self.url).netloc or self.url


@dataclass(frozen=True, slots=True)
class UpdateArtifact:
    version: str
    file_name: str
    expected_size: int
    expected_sha256: str
    sources: tuple[DownloadSource, ...]
    # Проверенный подписанный список выпуска (``release.signed.SignedRelease``):
    # по нему ставится обновление по файлам.
    signed: object = None


class CancellationToken:
    """Потокобезопасный признак отмены. Дочерний признак видит отмену родителя."""

    def __init__(self, parent: "CancellationToken | None" = None) -> None:
        self._event = threading.Event()
        self._parent = parent

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set() or bool(self._parent is not None and self._parent.is_cancelled)

    def checkpoint(self) -> None:
        if self.is_cancelled:
            raise UpdateCancelled("Обновление остановлено")


__all__ = [
    "CancellationToken",
    "DownloadFailedError",
    "DownloadSource",
    "HandoffError",
    "LocalWriteError",
    "ReleaseLookupError",
    "UpdateArtifact",
    "UpdateCancelled",
    "UpdateIntegrityError",
    "UpdateNotNeeded",
    "UpdateSignatureError",
    "UpdatePipelineError",
]
