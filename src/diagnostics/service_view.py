"""Что сказать про сайт: единственное место, где запись сервиса превращается в строки отчёта.

Проверка отдаёт запись сервиса словарём (``report_text.services_report``). Из неё
строятся и карточки BlockCheck, и текст отчёта (окно «Отчёт», копирование, обращение
в поддержку), и итог «Проверки домена». Раньше у каждого был свой набор строк со
своими названиями, и один и тот же факт читался по-разному. Теперь слова, порядок
и уровень каждой строки решаются здесь, а карточка и текст только рисуют готовое:

- ``service_sections(service)`` — разделы записи сервиса: по разделу на адрес, путь запроса
  главного адреса, что делать, DNS, замедление по имени, вид блокировки;
- ``render_text(sections)`` — те же разделы строками текста.

Новое измерение показывают одной правкой в ``target_rows``: оно появится и на карточке,
и в тексте. Модуль не знает ни про окна, ни про пробы — только про словарь отчёта.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from diagnostics.block_kind import KIND_OTHER, KINDS, kind_info

__all__ = [
    "INFO",
    "DNS_ROWS",
    "STAGE_TITLES",
    "ViewRow",
    "ViewSection",
    "name_speed_sections",
    "render_text",
    "service_sections",
    "shown_level",
    "site_kind",
    "site_level",
    "stages_text",
    "target_sections",
]

# Уровень строки: от него зависят значок на экране и в тексте.
OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"
INFO = "info"
_LEVELS = (OK, WARN, FAIL, UNKNOWN, INFO)

TEXT_ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌", UNKNOWN: "❔", INFO: "ℹ️"}

# Исход попытки основного запроса — словом для подробностей.
_TRIED_WORDS = {
    "ok": "открылся",
    "connect": "нет соединения",
    "timeout": "нет ответа",
    "reset": "сброс",
    "tls": "обрыв на шифровании",
    "cert": "чужой сертификат",
    "error": "ошибка",
}
_PROTOCOL_STATES = {"ok": OK, "fail": FAIL, "info": INFO, "unknown": UNKNOWN}
# Ответ DNS: подмена — предупреждение, совпадение с эталоном — норма, свой адрес — справка.
_DNS_LEVELS = {"ok": OK, "spoofed": WARN, "local": INFO, "unknown": UNKNOWN}
# QUIC: блокировка по имени — предупреждение, ответ — норма, молчание — справка (сервер мог не поддерживать QUIC).
_QUIC_LEVELS = {"ok": OK, "blocked_by_name": WARN, "silent": INFO}
_OUTSIDE_WORDS = {
    "up": (INFO, "открывается — значит, закрыт он именно на вашей линии"),
    "down": (INFO, "тоже не открывается — сайт не работает сам"),
}
# Строки адреса, которые относятся к ответу DNS: короткая проверка показывает только их.
DNS_ROWS = ("DNS", "Адреса DNS", "Заметка")
_CONFIDENCE_WORDS = {"low": "низкая", "medium": "средняя", "high": "высокая"}

# Части проверки одного сайта — на что ушло его время.
STAGE_TITLES = {"dns": "адрес", "reach": "соединение", "cause": "причина", "volume": "объём", "roads": "дороги"}


@dataclass(frozen=True, slots=True)
class ViewRow:
    level: str
    title: str
    text: str = ""
    # Данные для экрана, которые текст не показывает (например, находка про DNS-серверы по частям).
    extra: object = None


@dataclass(frozen=True, slots=True)
class ViewSection:
    title: str
    rows: tuple[ViewRow, ...] = ()
    # Текст как есть, моноширинным: таблица узлов или полный отчёт.
    text: str = ""
    # Строки — однотипный перечень: экран может показать его сеткой плиток.
    tiles: bool = False
    tile_icon: str = ""
    # Пояснение «что это за проверка»: на экране оно нужно, в текстовом отчёте повторять его незачем.
    screen_only: bool = False


def shown_level(value: object, default: str = UNKNOWN) -> str:
    """Уровень из отчёта; незнакомое слово показывается как ``default``."""
    text = str(value or "")
    return text if text in _LEVELS else default


def site_level(service: dict) -> str:
    """Уровень сайта — как в отчёте: ни экран, ни текст его не пересчитывают."""
    return shown_level(service.get("level"))


def site_kind(service: dict, level: str | None = None) -> str:
    """Вид блокировки сайта — как в отчёте. Пусто — сайт открывается или вид неизвестен."""
    level = site_level(service) if level is None else level
    kind = str(service.get("kind") or "")
    return kind if level in (FAIL, WARN) and kind in KINDS and kind != KIND_OTHER else ""


def stages_text(stages: dict | None) -> str:
    """«соединение 6 с, причина 3 с» — только части длиннее секунды, от долгих к коротким."""
    parts = sorted(((float(value), name) for name, value in (stages or {}).items() if float(value) >= 1.0), reverse=True)
    return ", ".join(f"{STAGE_TITLES.get(name, name)} {value:.0f} с" for value, name in parts)


def _target_level(item: dict) -> str:
    if item.get("ok"):
        return OK
    return UNKNOWN if str(item.get("state") or "") == UNKNOWN else FAIL


def _registry_text(item: dict) -> str:
    mark = item.get("registry") or {}
    parts = []
    name = str(mark.get("name") or "")
    if name:
        parts.append("сайт значится в реестре" if name == item.get("host") else f"в реестре значится {name}")
    if mark.get("network"):
        parts.append(f"адрес сервера входит в список заблокированных ({mark['network']})")
    if parts:
        return "; ".join(parts)
    # Для открывающегося сайта оговорка лишняя, для закрытого — главное.
    if item.get("ok"):
        return "не значится"
    return "не значится — блокировать могут и без записи в реестре"


def target_rows(item: dict) -> tuple[ViewRow, ...]:
    """Все измерения одного адреса сайта, в одном порядке для экрана и для текста."""
    rows = [ViewRow(_target_level(item), "Соединение", str(item.get("text") or ""))]
    if item.get("address"):
        rows.append(ViewRow(INFO, "Адрес сервера", str(item["address"])))
    tried = list(item.get("tried") or ())
    if len(tried) > 1 or (tried and not item.get("ok")):
        words = ", ".join(f"{step.get('address', '')} — {_TRIED_WORDS.get(str(step.get('result')), 'сбой')}" for step in tried)
        rows.append(ViewRow(INFO, "Какие адреса пробовали", words))
    if item.get("unstable"):
        rows.append(ViewRow(WARN, "Открывается через раз", str(item["unstable"])))
    elif item.get("rechecked") == "opened":
        rows.append(ViewRow(INFO, "Повторная проверка", "открылся со второго раза, поодиночке — первый сбой дала нагрузка самой проверки"))
    elif item.get("rechecked") == "same":
        rows.append(ViewRow(INFO, "Повторная проверка", "поодиночке, когда остальные проверки закончились, — результат тот же"))
    if item.get("hosts_stale"):
        rows.append(
            ViewRow(
                WARN,
                "Файл hosts",
                "записанный в нём адрес не работает. Проверка открыла сайт по настоящему адресу, "
                "а браузер пойдёт по записи и сайт не откроет — обновите её в «Редакторе hosts»",
            )
        )
    cert = item.get("cert") or {}
    if cert:
        rows.append(ViewRow(FAIL, "Чужой сертификат", str(cert.get("text") or "")))
        if cert.get("issuer"):
            rows.append(ViewRow(INFO, "Кем выдан", str(cert["issuer"])))
        if cert.get("names"):
            rows.append(ViewRow(INFO, "Каким сайтам выдан", ", ".join(map(str, cert["names"][:6]))))
        if cert.get("advice"):
            rows.append(ViewRow(INFO, "Что делать", str(cert["advice"])))
    for proto in item.get("protocols") or ():
        rows.append(ViewRow(_PROTOCOL_STATES.get(str(proto.get("state")), UNKNOWN), str(proto.get("title") or ""), str(proto.get("text") or "")))
    if item.get("kind_title"):
        rows.append(ViewRow(INFO, "Вид блокировки", str(item["kind_title"])))
    if item.get("cause_text"):
        rows.append(ViewRow(FAIL, "Как блокируют", str(item["cause_text"])))
    if "registry" in item:
        rows.append(ViewRow(INFO, "Реестр РКН", _registry_text(item)))
    if item.get("seconds"):
        parts = stages_text(item.get("stages"))
        rows.append(ViewRow(INFO, "Проверка заняла", f"{float(item['seconds']):.0f} с" + (f" ({parts})" if parts else "")))
    if item.get("quic_text"):
        rows.append(ViewRow(_QUIC_LEVELS.get(str(item.get("quic")), INFO), "QUIC (UDP 443)", str(item["quic_text"])))
    if item.get("volume_text"):
        rows.append(ViewRow(WARN if item.get("volume") == "cut" else INFO, "Объём загрузки", str(item["volume_text"])))
    transfer = item.get("transfer") or {}
    if transfer.get("text") and transfer.get("code") != "unknown":
        rows.append(ViewRow(shown_level(transfer.get("level")), "Скорость ответа", str(transfer["text"])))
    outside = _OUTSIDE_WORDS.get(str(item.get("outside") or ""))
    if outside:
        rows.append(ViewRow(outside[0], "С сервера Zapret DNS в Нидерландах", outside[1]))
    if item.get("dns_reason"):
        rows.append(ViewRow(_DNS_LEVELS.get(str(item.get("dns_state")), INFO), "DNS", str(item["dns_reason"])))
    if item.get("dns_detail"):
        rows.append(ViewRow(INFO, "Адреса DNS", str(item["dns_detail"])))
    if item.get("note"):
        rows.append(ViewRow(INFO, "Заметка", str(item["note"])))
    return tuple(rows)


def path_section(item: dict) -> ViewSection | None:
    """Путь запроса по шагам — только у главного адреса: у остальных он повторял бы тот же вывод."""
    steps = [step for step in item.get("path") or () if isinstance(step, dict)]
    if not steps or not item.get("main"):
        return None
    rows = []
    for step in steps:
        text = str(step.get("text") or "")
        confidence = _CONFIDENCE_WORDS.get(str(step.get("confidence") or ""))
        if confidence:
            text = f"{text} (уверенность {confidence})"
        rows.append(ViewRow(shown_level(step.get("level")), str(step.get("title") or step.get("key") or ""), text))
    return ViewSection("Путь запроса", tuple(rows))


def target_sections(item: dict) -> tuple[ViewSection, ...]:
    """Разделы одного адреса: его измерения и, у главного, путь запроса."""
    purpose, host = str(item.get("purpose") or ""), str(item.get("host") or "")
    sections = [ViewSection(f"{host} — {purpose}" if purpose else host, target_rows(item))]
    path = path_section(item)
    if path is not None:
        sections.append(path)
    return tuple(sections)


def name_speed_sections(names: dict) -> tuple[ViewSection, ...]:
    """Замедление по имени сайта: скорость одного сервера с его именем и с именами проверенных сайтов."""
    rows = tuple(
        ViewRow(shown_level(item.get("state")), f"{item.get('name', '')} ({item.get('host', '')})", str(item.get("text") or ""))
        for item in names.get("items") or ()
    )
    if not rows:
        return ()
    control = ViewRow(INFO, f"Контроль: {names.get('server', '')} под своим именем", str(names.get("control") or ""))
    return (
        ViewSection(str(names.get("headline") or "Замедление по имени сайта"), (control, *rows)),
        ViewSection(
            "Как проверяется замедление по имени",
            (
                ViewRow(
                    INFO,
                    "С одного и того же постороннего сервера качается один файл: сначала под его собственным именем, "
                    "затем под именем каждого открывшегося сайта. Сервер и дорога те же, отличается только имя, которое "
                    "видит фильтр. Если с именем сайта скорость в разы ниже — замедляют по имени. Медленный замер "
                    "перепроверяется, контроль в конце повторяется.",
                ),
            ),
            screen_only=True,
        ),
    )


def service_sections(service: dict) -> tuple[ViewSection, ...]:
    """Подробности сайта: адреса с путём запроса, что делать, DNS, замедление по имени, вид блокировки."""
    sections: list[ViewSection] = []
    for item in service.get("targets") or ():
        sections.extend(target_sections(item))
    advice = [str(item) for item in service.get("advice") or ()]
    if advice:
        sections.append(ViewSection("Что делать", tuple(ViewRow(INFO, item) for item in advice)))
    if service.get("dns_note"):
        sections.append(ViewSection("DNS", (ViewRow(WARN, str(service["dns_note"])),)))
    if service.get("names"):
        # Замер потока по имени сайта и видеосервера: есть только у проверки одного сайта («Проверка домена»).
        sections.extend(name_speed_sections(service["names"]))
    kind = site_kind(service)
    if kind and kind_info(kind).about:
        sections.append(ViewSection(kind_info(kind).title, (ViewRow(INFO, kind_info(kind).about),)))
    return tuple(sections)


def render_text(sections: Iterable[ViewSection], indent: str = "") -> list[str]:
    """Разделы строками текста: заголовок раздела и под ним строки со значком уровня."""
    lines: list[str] = []
    for section in sections:
        if section.screen_only:
            continue
        lines.append(f"{indent}{section.title}")
        lines.extend(f"{indent}   {line}" for line in section.text.splitlines())
        for row in section.rows:
            body = f"{row.title}: {row.text}" if row.text else row.title
            lines.append(f"{indent}   {TEXT_ICONS.get(row.level, TEXT_ICONS[UNKNOWN])} {body}")
    return lines
