from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class DNSCheckStartPlan:
    status_text: str
    status_tone: str
    check_enabled: bool
    save_enabled: bool
    progress_visible: bool


@dataclass(slots=True)
class DNSCheckFinishPlan:
    status_text: str
    status_tone: str
    check_enabled: bool
    save_enabled: bool
    progress_visible: bool


@dataclass(slots=True)
class DNSSaveResultPlan:
    success: bool
    title: str
    content: str

def build_start_plan() -> DNSCheckStartPlan:
    return DNSCheckStartPlan(
        status_text="🔄 Выполняется проверка DNS...",
        status_tone="accent",
        check_enabled=False,
        save_enabled=False,
        progress_visible=True,
    )

# Итог → строка состояния под кнопками. Та же сводка, что у панели итога: зелёного
# «завершена» при сбое или остановке больше нет.
_FINISH_STATUS = {
    "spoofed": ("⚠️ Обнаружена DNS подмена!", "error"),
    "ok": ("✅ Проверка завершена", "success"),
    "partial": ("⚠️ Проверка завершена, но не всё удалось проверить", "warning"),
    "stopped": ("⏹️ Проверка остановлена", "muted"),
    "error": ("❌ Проверка не удалась", "error"),
}


def build_finish_plan(results: dict) -> DNSCheckFinishPlan:
    kind = summarize_dns_results(results)["kind"]
    status_text, status_tone = _FINISH_STATUS[kind]
    return DNSCheckFinishPlan(
        status_text=status_text,
        status_tone=status_tone,
        check_enabled=True,
        # Сохранять есть что, только если проверка дала строки по доменам.
        save_enabled=bool((results or {}).get("domains")),
        progress_visible=False,
    )


def build_save_default_filename() -> str:
    return f"dns_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"


def _reference_note(results: dict) -> str:
    """Что с эталонными серверами, с которыми сравнивались ответы. Пусто — все ответили."""
    reference = [dict(item) for item in results.get("reference") or () if isinstance(item, dict)]
    down = [item for item in reference if not item.get("ok")]
    if not reference or not down:
        return ""
    if len(down) == len(reference):
        return "Ни один эталонный сервер не ответил — сравнивать ответы было не с чем."
    names = ", ".join(
        f"{item.get('label', '')} ({item.get('address', '')})" + (f" — {item['reason']}" if item.get("reason") else "")
        for item in down
    )
    return f"Не ответили эталонные серверы: {names}. Сравнение сделано по остальным."


UNVERIFIED_TITLE = "Проверить DNS не удалось"
def summarize_dns_results(results: dict | None) -> dict:
    """Итог проверки для панели: вид, заголовок, объяснение и число подмен."""
    summary = _summarize(results or {})
    note = _reference_note(results or {})
    if note and summary["kind"] not in ("stopped", "error"):
        reference = list((results or {}).get("reference") or ())
        if summary["kind"] == "ok" and all(not item.get("ok") for item in reference):
            # Без эталона «честный DNS» утверждать нельзя.
            summary = {"kind": "partial", "title": UNVERIFIED_TITLE, "detail": ""}
        summary["detail"] = f"{summary['detail']} {note}".strip()
    return summary


def _summarize(results: dict) -> dict:
    if results.get("stopped"):
        return {"kind": "stopped", "title": "Проверка остановлена", "detail": ""}
    domains = dict(results.get("domains") or {})
    if not domains:
        return {
            "kind": "error",
            "title": "Проверка не удалась",
            "detail": "Подробности — в подробном логе ниже. Проверьте подключение и попробуйте ещё раз.",
        }
    spoofed = [host for host, item in domains.items() if item.get("state") == "spoofed"]
    unknown = [host for host, item in domains.items() if item.get("state") == "unknown"]
    if spoofed:
        noun = "сайта" if len(spoofed) == 1 else "сайтов"
        return {
            "kind": "spoofed",
            "title": f"Поймали провайдера за руку: подменяет адреса {len(spoofed)} {noun}",
            "detail": (
                "Программы, которые спрашивают адрес у Windows, получают неверный ответ. "
                "Включите DNS с шифрованием (DoH) на странице «Настройка DNS» — его провайдер перехватить не сможет."
            ),
            "count": len(spoofed),
        }
    if unknown:
        return {
            "kind": "partial",
            "title": "Явной подмены нет",
            "detail": (
                "Часть адресов отличается от эталона, но проверить их не удалось — для CDN это обычно нормально."
            ),
        }
    if all(item.get("state") == "local" for item in domains.values()):
        # Ни один адрес не пришёл от провайдера: сравнивать было нечего, «честный DNS» не доказан.
        return {
            "kind": "partial",
            "title": "Адреса берутся не у провайдера",
            "detail": (
                "Все проверенные адреса заданы на этом компьютере или в вашей сети — файл hosts, VPN, "
                "домашний фильтр, — поэтому подмену у провайдера по ним не проверить."
            ),
        }
    return {
        "kind": "ok",
        "title": "Ваш DNS честный 👍",
        "detail": "Адреса совпадают с эталоном. Если сайты не открываются — дело не в DNS, а в блокировке соединения.",
    }
