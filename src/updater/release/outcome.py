from __future__ import annotations

"""Чем кончилось обновление, которое программа поставила сама.

Сервер раздаёт новую версию по ступеням: сначала малой доле программ, и
открывает следующую ступень, только когда эти программы после обновления
снова вышли на связь. Поэтому новая версия один раз сообщает ему: «дошла, с
такой-то версии, от разрешения до запуска столько-то секунд». Если же
установщик не справился и работает прежняя версия, она сообщает: «не вышло».
По этим сообщениям сервер сам останавливает раздачу сломанной версии.

К «не вышло» добавляется короткая причина (``why``): код возврата
установщика, «файл не тот», «версия не сменилась» и т.п. — так видно, на каком
шаге обновление срывается у людей.

Сорвавшееся скачивание неудачей версии не считается: виновата обычно сеть, и
раздачу оно не держит. О нём сообщается отдельно (``miss`` с причиной): если
вдруг у многих не сходится контрольная сумма файла, это видно на сервере.

Сообщение — это несколько слов в адресе обычного вопроса слушателя выпусков
(``updater.release.watch``); ничего, кроме номеров версий, числа секунд и
кода причины, в нём нет.

Модуль не импортирует Qt.
"""

import re
import threading
import time

from config.build_info import APP_VERSION
from log.log import log

from ..versions import compare_versions

OUTCOME_STARTED = "started"
OUTCOME_FAILED = "failed"
OUTCOME_MISSED = "missed"
# Вид кода причины: его же принимает сервер очереди.
WHY_RE = re.compile(r"\A[a-z][a-z0-9]{0,15}\Z")
# Дольше недели от разрешения до запуска — это уже не «время обновления».
MAX_TOOK_SECONDS = 7 * 86400

_lock = threading.Lock()
# Когда сервер разрешил версию (время компьютера) — в этом запуске программы.
_granted_at: dict[str, float] = {}


def note_granted(version: str, now: float | None = None) -> None:
    """Сервер разрешил обновиться до версии: отсюда считается время обновления."""
    with _lock:
        _granted_at.setdefault(str(version or ""), float(now if now is not None else time.time()))


def granted_at(version: str) -> float:
    with _lock:
        return float(_granted_at.get(str(version or ""), 0.0))


def _clean_why(why: str) -> str:
    text = str(why or "").strip().lower()
    return text if WHY_RE.fullmatch(text) else ""


def note_attempt_failed(version: str, why: str = "") -> None:
    """Установщик версии не справился: об этом и о причине узнает сервер."""
    from settings import store as settings_store

    try:
        settings_store.set_auto_install_outcome(str(version or ""), OUTCOME_FAILED, _clean_why(why))
    except Exception as exc:
        log(f"Не удалось запомнить исход обновления: {exc}", "WARNING")


def note_download_failed(version: str, why: str) -> None:
    """Версия, которую программа взялась ставить сама, не скачалась.

    Пишется только поверх начатой попытки этой версии: сорвавшаяся ручная
    установка и уже известный исход (например, «не вышло» установщика) не
    перезаписываются.
    """
    code = _clean_why(why)
    if not code:
        return
    from settings import store as settings_store

    try:
        state = settings_store.get_auto_install_state()
        if str(state.get("version") or "") != str(version or "") or state.get("outcome") != OUTCOME_STARTED:
            return
        settings_store.set_auto_install_outcome(str(version or ""), OUTCOME_MISSED, code)
    except Exception as exc:
        log(f"Не удалось запомнить, почему обновление не скачалось: {exc}", "WARNING")


def pending_report(*, current_version: str = APP_VERSION, now: float | None = None) -> dict[str, str]:
    """Что добавить к вопросу серверу: ``prev``/``took``, ``fail``/``miss`` с ``why`` либо ничего."""
    from settings import store as settings_store

    try:
        state = settings_store.get_auto_install_state()
        version = str(state.get("version") or "")
        outcome = str(state.get("outcome") or "")
        if not version or not outcome:
            return {}
        installed = compare_versions(current_version, version) >= 0
        if not installed:
            kind = {OUTCOME_FAILED: "fail", OUTCOME_MISSED: "miss"}.get(outcome)
            if kind is None:
                return {}
            report = {kind: version}
            why = _clean_why(str(state.get("why") or ""))
            if why:
                report["why"] = why
            return report
        previous = str(state.get("from_version") or "")
        if not previous or compare_versions(previous, current_version) >= 0:
            # Откуда обновлялись, неизвестно: сообщать нечего, вопрос закрыт.
            settings_store.set_auto_install_outcome(version, "")
            return {}
        report = {"prev": previous}
        granted = float(state.get("granted_at") or 0.0)
        took = float(now if now is not None else time.time()) - granted
        if granted > 0 and 0 <= took <= MAX_TOOK_SECONDS:
            report["took"] = str(int(took))
        return report
    except Exception:
        return {}


def report_delivered(report: dict[str, str]) -> None:
    """Сервер получил сообщение: второй раз его не шлём."""
    if not report:
        return
    from settings import store as settings_store

    try:
        state = settings_store.get_auto_install_state()
        settings_store.set_auto_install_outcome(str(state.get("version") or ""), "")
    except Exception as exc:
        log(f"Не удалось закрыть сообщение об обновлении: {exc}", "WARNING")


__all__ = [
    "granted_at",
    "note_attempt_failed",
    "note_download_failed",
    "note_granted",
    "pending_report",
    "report_delivered",
]
