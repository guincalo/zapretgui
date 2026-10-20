"""Что сказать про отдельные проверки (не сайты): сеть, компьютер, хостинги, звонки, DNS, фильтр, скорость.

Как и ``service_view`` для сайтов, это единственное место, где словарь отчёта каждой
проверки превращается в строки. Результат — ``BlockView``: слово итога, уровень, короткие
строки для сетки карточек и разделы с подробностями. Карточка BlockCheck берёт их и
добавляет только то, что есть лишь на экране (значок, точки хостингов, плитки), а
текстовый отчёт печатает те же разделы через ``render_block_text``. Поэтому цифра и
формулировка не могут разойтись между окном и текстом для поддержки.

Модуль не знает про окна и пробы, только про словари, которые отдаёт проверка.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from diagnostics.levels import level_rank
from diagnostics.service_view import (
    FAIL,
    INFO,
    OK,
    UNKNOWN,
    WARN,
    ViewRow,
    ViewSection,
    render_text,
    shown_level,
)

__all__ = [
    "FILTER_MARK",
    "BlockView",
    "build_block",
    "build_blocks",
    "COMPARE_STATES",
    "one_preset_note",
    "text_lines",
    "registry_rows",
    "render_block_text",
]

FILTER_MARK = "здесь стоит фильтр"


@dataclass(frozen=True, slots=True)
class BlockView:
    key: str
    title: str
    level: str
    # Слово итога: «Работают: 2 из 3», «Мешает работе: 1».
    status: str
    # Короткие строки для сетки карточек; в текстовый отчёт не идут.
    preview: tuple[ViewRow, ...] = ()
    sections: tuple[ViewSection, ...] = ()


def _capital(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}"


def _advice_section(items, title: str = "Что делать") -> ViewSection | None:
    rows = tuple(ViewRow(INFO, str(item)) for item in items or () if str(item).strip())
    return ViewSection(title, rows) if rows else None


def _explain(text: str, title: str = "Что это за проверка") -> ViewSection:
    return ViewSection(title, (ViewRow(INFO, text),), screen_only=True)


def _sections(*items: ViewSection | None) -> tuple[ViewSection, ...]:
    return tuple(item for item in items if item is not None)


# ---------------------------------------------------------------------------
# Ваша сеть, этот компьютер, IPv6
# ---------------------------------------------------------------------------


def network_block(network: dict) -> BlockView:
    lines = tuple(
        ViewRow(shown_level(item.get("state"), INFO), str(item.get("name") or ""), str(item.get("text") or ""))
        for item in network.get("lines") or ()
    )
    provider = str(network.get("provider") or "")
    level = WARN if any(line.level == WARN for line in lines) else (INFO if network.get("external_ip") else UNKNOWN)
    return BlockView(
        "network",
        "Ваша сеть",
        level,
        provider or ("Провайдер не определён" if network.get("external_ip") else "Не удалось узнать"),
        lines,
        _sections(
            ViewSection("Под каким адресом вас видит интернет", lines),
            _explain(
                "Блокировки у провайдеров разные. Если здесь чужая сеть (VPN, прокси), "
                "вся проверка описывает сеть вместе с ней, а не вашего провайдера.",
                "Зачем это",
            ),
        ),
    )


def system_block(items: list[dict]) -> BlockView:
    lines = [ViewRow(shown_level(item.get("level")), str(item.get("title") or ""), str(item.get("text") or "")) for item in items]
    failed = [line for line in lines if line.level == FAIL]
    warned = [line for line in lines if line.level == WARN]
    if failed:
        level, status = FAIL, f"Мешает работе: {len(failed)}"
    elif warned:
        level, status = WARN, f"Замечаний: {len(warned)}"
    elif lines and all(line.level == UNKNOWN for line in lines):
        level, status = UNKNOWN, "Не проверено"
    else:
        level, status = OK, f"В порядке: {sum(1 for line in lines if line.level == OK)} из {len(lines)}"
    advice = tuple(
        ViewRow(INFO, str(item.get("title") or ""), str(item.get("advice") or ""))
        for item in items
        if item.get("advice") and item.get("level") in (FAIL, WARN)
    )
    return BlockView(
        "system",
        "Этот компьютер",
        level,
        status,
        tuple(sorted(lines, key=lambda line: level_rank(line.level))),
        _sections(ViewSection("Что делать", advice) if advice else None, ViewSection("Что проверено на компьютере", tuple(lines))),
    )


# Состояние IPv6 → (уровень, слово). «Нет в сети» — норма, поэтому не зелёное и не красное.
_IPV6 = {
    "ok": (OK, "Работает"),
    "absent": (UNKNOWN, "Нет в этой сети"),
    "broken": (WARN, "Не работает"),
    "unknown": (UNKNOWN, "Не проверено"),
}


def ipv6_block(ipv6: dict) -> BlockView:
    level, word = _IPV6.get(str(ipv6.get("state") or ""), _IPV6["unknown"])
    row = ViewRow(level, f"IPv6 {ipv6.get('text', '')}".strip())
    return BlockView("ipv6", "IPv6", level, word, (row,), (ViewSection("IPv6", (row,)),))


# ---------------------------------------------------------------------------
# Хостинги, звонки, Telegram
# ---------------------------------------------------------------------------

_SERVER_STATE = {"ok": OK, "freeze": FAIL, "fail": FAIL, "unknown": UNKNOWN}


def _with_seconds(text: str, seconds: object) -> str:
    if isinstance(seconds, (int, float)) and seconds > 0:
        return f"{text} · {seconds:.1f} с"
    return text


def hostings_block(freeze: dict) -> BlockView:
    servers = list(freeze.get("servers") or ())
    by_provider: dict[str, list[dict]] = {}
    for item in servers:
        by_provider.setdefault(str(item.get("provider") or "Без названия"), []).append(item)

    def _count(state: str, direction: str = "") -> int:
        return sum(1 for item in servers if item.get("state") == state and (not direction or item.get("direction") == direction))

    cut_down, cut_up = _count("freeze", "download"), _count("freeze", "upload")
    fine, unknown = _count("ok"), _count("unknown")
    cut = cut_down + cut_up
    if cut:
        status = f"Обрыв у {cut} из {len(servers)}"
    elif fine:
        status = f"Обрыва нет: {fine} из {len(servers)}"
    else:
        status = "Не удалось проверить"
    preview = [ViewRow(OK, "Проходит без обрыва", str(fine))]
    if cut_down:
        preview.append(ViewRow(FAIL, "Обрыв загрузки на 16–20 КБ", str(cut_down)))
    if cut_up:
        preview.append(ViewRow(FAIL, "Обрыв отправки", str(cut_up)))
    if unknown:
        preview.append(ViewRow(UNKNOWN, "Сервер не ответил", str(unknown)))
    providers = tuple(
        ViewSection(
            f"{name} — обрыв у {sum(1 for item in items if item.get('state') == 'freeze')} из {len(items)}",
            tuple(
                ViewRow(
                    _SERVER_STATE.get(str(item.get("state")), UNKNOWN),
                    " · ".join(part for part in (str(item.get("id") or ""), str(item.get("host") or "")) if part),
                    _with_seconds(str(item.get("text") or ""), item.get("seconds")),
                )
                for item in items
            ),
        )
        for name, items in by_provider.items()
    )
    return BlockView(
        "hostings",
        "Зарубежные хостинги",
        shown_level(freeze.get("level")),
        status,
        tuple(preview),
        _sections(
            _explain(
                "С каждого сервера качается около 32 КБ. Если загрузка прошла, на тот же сервер "
                "отправляется 64 КБ одним потоком и 64 байта мелкими пакетами. Обрыв засчитывается, "
                "только когда короткий запрос тот же сервер принимает."
            ),
            _advice_section(freeze.get("advice")),
            *providers,
        ),
    )


_VOICE_STATUS = {OK: "Работают", WARN: "С перебоями", FAIL: "Не работают", UNKNOWN: "Не удалось проверить"}


def voice_block(voice: dict) -> BlockView:
    items = list(voice.get("items") or ())
    level = shown_level(voice.get("level"))
    lines = tuple(
        ViewRow(_SERVER_STATE.get(str(item.get("state")), UNKNOWN), str(item.get("name") or ""), str(item.get("text") or ""))
        for item in items
    )
    answered = sum(1 for item in items if item.get("ok"))
    sections = [ViewSection(str(voice.get("headline") or "Голосовые серверы"), lines)]
    burst = voice.get("burst") or {}
    if burst.get("text"):
        burst_level = {"ok": OK, "freeze": WARN}.get(str(burst.get("state")), UNKNOWN)
        rows = [ViewRow(burst_level, "Тридцать пакетов подряд", _capital(str(burst["text"])))]
        rows += [ViewRow(INFO, str(item.get("name") or ""), "ответов: " + ", ".join(item.get("series") or ())) for item in burst.get("servers") or ()]
        sections.append(ViewSection("Не замирает ли UDP", tuple(rows)))
        if burst_level == WARN:
            lines = lines + (ViewRow(WARN, "UDP замирает", _capital(str(burst["text"]))),)
            level = WARN if level == OK else level
    advice = _advice_section(voice.get("advice"))
    return BlockView(
        "voice",
        "Голосовые звонки (UDP)",
        level,
        f"{_VOICE_STATUS[level]}: {answered} из {len(items)}" if items else _VOICE_STATUS[level],
        lines,
        _sections(*sections, advice),
    )


def telegram_block(telegram: dict) -> BlockView:
    items = list(telegram.get("items") or ())
    lines = tuple(
        ViewRow(
            _SERVER_STATE.get(str(item.get("state")), UNKNOWN),
            f"{item.get('name', '')} · {item.get('address', '')}",
            str(item.get("text") or ""),
        )
        for item in items
    )
    answered = sum(1 for item in items if item.get("state") == "ok")
    return BlockView(
        "telegram",
        "Telegram: дата-центры",
        shown_level(telegram.get("level")),
        f"Отвечают {answered} из {len(items)}" if items else "Не проверено",
        lines,
        _sections(
            ViewSection(str(telegram.get("headline") or "Дата-центры Telegram"), lines),
            _explain(
                "Приложение Telegram ходит не на сайт, а прямо по адресам своих дата-центров. "
                "Здесь с каждым из них устанавливается обычное соединение; неудачное повторяется после паузы."
            ),
            _advice_section(telegram.get("advice")),
        ),
    )


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------


def dns_block(report: dict) -> BlockView | None:
    reference = list(report.get("reference") or ())
    spoofed = [str(item) for item in report.get("spoofed_hosts") or ()]
    if not reference and not spoofed:
        return None
    lines = [
        ViewRow(
            OK if item.get("ok") else FAIL,
            f"{item.get('label', '')} ({item.get('address', '')})",
            "отвечает" if item.get("ok") else str(item.get("reason") or "не отвечает"),
        )
        for item in reference
    ]
    answering = sum(1 for item in reference if item.get("ok"))
    if spoofed:
        level, status = WARN, f"Подмена адресов: {len(spoofed)}"
    elif reference and answering < len(reference):
        level, status = WARN, f"Эталоны: {answering} из {len(reference)}"
    else:
        level, status = OK, "Адреса не подменяются"
    sections = []
    if spoofed:
        sections.append(ViewSection("DNS подменяет адреса", tuple(ViewRow(WARN, host) for host in spoofed)))
        lines.insert(0, ViewRow(WARN, "Подменены адреса", ", ".join(spoofed)))
    sections.append(ViewSection("Эталонные серверы: по ним сверяются адреса сайтов", tuple(lines[1:] if spoofed else lines)))
    return BlockView("dns", "Подмена DNS", level, status, tuple(lines), tuple(sections))


_DNS_SERVERS_STATUS = {OK: "В порядке", WARN: "Есть замечания", FAIL: "Есть проблемы", UNKNOWN: "Не проверено"}


def dns_servers_block(dns_servers: dict) -> BlockView:
    level = shown_level(dns_servers.get("level"))
    findings = tuple(
        ViewRow(shown_level(item.get("level"), INFO), str(item.get("text") or ""), extra=item)
        for item in dns_servers.get("findings") or ()
    )
    text = str(dns_servers.get("text") or "")
    return BlockView(
        "dns_servers",
        "DNS-серверы",
        level,
        _DNS_SERVERS_STATUS[level],
        findings or (ViewRow(level, "Замечаний нет"),),
        _sections(ViewSection("Выводы", findings) if findings else None, ViewSection("Все серверы и способы связи", text=text) if text else None),
    )


# ---------------------------------------------------------------------------
# Скорость, несколько соединений
# ---------------------------------------------------------------------------

# Потери на линии: почти нет — норма, небольшие — справка, заметные — предупреждение.
_LOSS_LEVELS = {"none": OK, "small": INFO, "heavy": WARN}
_SPEED_STATUS = {OK: "Разницы нет", WARN: "Зарубежные медленнее", UNKNOWN: "Не удалось сравнить"}


def speed_block(speed: dict) -> BlockView:
    from diagnostics.service_view import name_speed_sections

    names = speed.get("names") or {}
    slow_names = [str(item.get("name") or "") for item in names.get("items") or () if item.get("state") == "warn"]
    level = shown_level(speed.get("level"))
    lines = tuple(
        ViewRow(
            shown_level(item.get("state"), INFO),
            f"{item.get('name', '')} (Россия)" if item.get("domestic") else str(item.get("name") or ""),
            str(item.get("text") or ""),
        )
        for item in speed.get("items") or ()
    )
    losses = tuple(
        ViewRow(
            _LOSS_LEVELS.get(str(item["retransmits"].get("code")), INFO),
            str(item.get("name") or ""),
            str(item["retransmits"].get("text") or ""),
        )
        for item in speed.get("items") or ()
        if isinstance(item.get("retransmits"), dict) and item["retransmits"].get("code") != "unknown"
    )
    sections = _sections(
        ViewSection(str(speed.get("headline") or "Скорость загрузки"), lines),
        ViewSection("Потери на линии (повторные передачи TCP)", losses) if losses else None,
        *name_speed_sections(names),
        _explain(
            "С каждого сервера качается два-три мегабайта, не дольше нескольких секунд. Сама цифра "
            "зависит от тарифа, поэтому зарубежные серверы сравниваются с российскими. "
            "«Медленнее» говорится, только когда медленны все зарубежные сразу и разница — в разы. "
            "Потери считаются по повторным передачам TCP за время загрузки: если они есть и у российских серверов, "
            "виновата ближняя линия (Wi-Fi, роутер), если только у зарубежных — канал за границу."
        ),
    )
    if slow_names:
        # Замедление по имени важнее общей скорости: оно ставит итог и идёт в разделах первым.
        named = name_speed_sections(names)
        rest = tuple(item for item in sections if item not in named)
        return BlockView("speed", "Скорость", WARN, f"Замедляют: {', '.join(slow_names)}", lines, (*named, *rest))
    return BlockView("speed", "Скорость", level, _SPEED_STATUS.get(level, "Не проверено"), lines, sections)


_CROWD_ADVICE_TITLE = "Что это значит и что делать"


def crowd_block(crowd: dict) -> BlockView:
    """«Несколько соединений сразу»: замирает ли сайт, когда к нему открывают пачку соединений."""
    level = shown_level(crowd.get("level"))
    text = str(crowd.get("text") or "")
    advice = str(crowd.get("advice") or "")
    lines = (ViewRow(level, text),) + ((ViewRow(INFO, advice),) if advice else ())
    facts = [ViewRow(INFO, "Сайт", " · ".join(part for part in (str(crowd.get("label") or ""), str(crowd.get("host") or "")) if part))]
    if crowd.get("address"):
        facts.append(ViewRow(INFO, "Адрес сервера", str(crowd["address"])))
    rows = [ViewRow(shown_level(row.get("state")), str(row.get("title") or ""), str(row.get("text") or "")) for row in crowd.get("rows") or ()]
    facts += rows
    if not rows:
        # Отчёт, сохранённый до появления готовых строк: остаются только числа.
        if crowd.get("recovered_s") is not None:
            facts.append(ViewRow(INFO, "Сайт вернулся через", f"{crowd['recovered_s']} с"))
        if crowd.get("waited_s"):
            facts.append(ViewRow(INFO, "Ждали после пачки", f"{crowd['waited_s']} с"))
    return BlockView(
        "crowd",
        "Несколько соединений сразу",
        level,
        str(crowd.get("status") or text),
        lines,
        _sections(
            ViewSection("Вывод", (ViewRow(level, text),)),
            ViewSection(_CROWD_ADVICE_TITLE, (ViewRow(INFO, advice),)) if advice else None,
            ViewSection("Что проверяли", tuple(facts)),
        ),
    )


# ---------------------------------------------------------------------------
# Откат QUIC
# ---------------------------------------------------------------------------

_FALLBACK_LEVELS = {"none": OK, "cost": WARN, "broken": FAIL, "unknown": UNKNOWN}
_FALLBACK_STATUS = {"none": "Почти бесплатный", "cost": "Стоит времени", "broken": "С QUIC не открывается", "unknown": "Не измерено"}


def quic_fallback_block(data: dict) -> BlockView:
    """«Откат QUIC»: сколько браузер теряет, уходя с QUIC на TCP, когда UDP 443 режут."""
    code = str(data.get("code") or "unknown")
    level = _FALLBACK_LEVELS.get(code, UNKNOWN)
    text = _capital(str(data.get("text") or ""))
    host = " · ".join(part for part in (str(data.get("label") or ""), str(data.get("host") or "")) if part)
    facts = [ViewRow(INFO, "Сайт", host)] if host else []
    if data.get("quic_ms"):
        facts.append(ViewRow(INFO, "Браузер с QUIC", f"{int(data['quic_ms']) / 1000:.1f} с"))
    if data.get("tcp_ms"):
        facts.append(ViewRow(INFO, "Браузер без QUIC", f"{int(data['tcp_ms']) / 1000:.1f} с"))
    advice = (
        ViewSection(
            "Что делать",
            (
                ViewRow(
                    INFO,
                    "Закрыть QUIC к этому сайту: в Zapret 2 правилом drop для UDP 443, в winws — правилом брандмауэра. "
                    "Тогда браузер сразу пойдёт по TCP и не будет ждать ответа по UDP.",
                ),
            ),
        )
        if code in ("cost", "broken")
        else None
    )
    return BlockView(
        "quic_fallback",
        "Откат QUIC",
        level,
        _FALLBACK_STATUS.get(code, "Не измерено"),
        (ViewRow(level, text),),
        _sections(
            ViewSection("Вывод", (ViewRow(level, text),)),
            ViewSection("Что измеряли", tuple(facts)) if facts else None,
            advice,
            _explain(
                "Один и тот же сайт открывается настоящим браузером без окна с QUIC и без него, по два раза каждым способом, "
                "и сравнивается время. Когда провайдер молча режет UDP 443, браузер сначала ждёт ответа по QUIC и только потом "
                "уходит на TCP: сайт «открывается», но каждый раз с задержкой."
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Место фильтра и как он работает
# ---------------------------------------------------------------------------


def _hop_time(rtt: object) -> str:
    if not isinstance(rtt, (int, float)):
        return ""
    return "< 1 мс" if rtt < 1 else f"{round(rtt)} мс"


def filter_block(place: dict) -> BlockView:
    """«Место фильтра»: вывод, срок жизни для стратегий, сайты, по которым искали, и вся дорога с владельцами узлов."""
    found = bool(place.get("found"))
    state = str(place.get("state") or ("found" if found else "not_found"))
    text = str(place.get("text") or "")
    hop = place.get("hop") if found else None
    level = WARN if found or state in ("disagree", "disturbed") else (INFO if state == "none" else UNKNOWN)
    status = {
        "found": "В роутере или на компьютере" if hop == 1 else f"Между узлами {(hop or 1) - 1} и {hop}",
        "disagree": "Узлы не совпали",
        "disturbed": "Мешает программа обхода",
        "none": "Блокировок по имени нет",
    }.get(state, "Не найдено")
    # Одного места нет, но по отдельным способам блокировки оно найдено: это и есть главный итог.
    located = [item for item in place.get("mechanisms") or () if item.get("hop")]
    if not found and located:
        hops = sorted({int(item["hop"]) for item in located})
        level = WARN
        status = f"По способам: {'узел' if len(hops) == 1 else 'узлы'} {', '.join(map(str, hops))}"
    preview = [ViewRow(WARN, str(item.get("title") or ""), _capital(str(item.get("text") or ""))) for item in located] if not found else []
    preview.append(ViewRow(level, _capital(text)))
    if place.get("ttl_advice"):
        preview.append(ViewRow(INFO, "Для стратегий", str(place["ttl_advice"])))
    sites = list(place.get("sites") or ())
    if sites:
        preview.append(ViewRow(INFO, "Искали по сайтам", ", ".join(str(item.get("host") or "") for item in sites)))
    elif place.get("host"):
        preview.append(ViewRow(INFO, "Дорога показана до", str(place["host"])))

    sections = [ViewSection("Вывод", tuple(line for line in preview if not line.text or line.title == "Для стратегий")[:2])]
    mechanisms = [
        ViewRow(WARN if item.get("hop") else UNKNOWN, str(item.get("title") or ""), _capital(str(item.get("text") or "")))
        for item in place.get("mechanisms") or ()
    ]
    if mechanisms:
        if place.get("same_place"):
            mechanisms.append(ViewRow(INFO, "Один фильтр или несколько", str(place["same_place"])))
        sections.append(ViewSection("Чем и где режут", tuple(mechanisms)))
    unmeasured = [ViewRow(INFO, str(item.get("title") or ""), str(item.get("text") or "")) for item in place.get("unmeasured") or ()]
    if unmeasured and mechanisms:
        sections.append(ViewSection("Место не измеряется", tuple(unmeasured)))
    site_rows: list[ViewRow] = []
    for item in sites:
        distance = f" · до сервера {item['distance']} узлов" if item.get("distance") else ""
        site_rows.append(
            ViewRow(
                # Найденное по сайту — свидетельство, а не отдельное замечание: замечание одно, в выводе.
                INFO if item.get("found") else UNKNOWN,
                f"{item.get('host', '')} ({item.get('address', '')}{distance})",
                _capital(str(item.get("text") or "")),
            )
        )
        for method in item.get("methods") or ():
            site_rows.append(ViewRow(INFO, f"   способ {method.get('title', '')}", str(method.get("text") or "")))
    if site_rows:
        sections.append(ViewSection("По каким сайтам искали", tuple(site_rows)))
    if place.get("reasons"):
        sections.append(ViewSection("На чём основан вывод", tuple(ViewRow(INFO, str(reason)) for reason in place["reasons"])))
    hop_rows = []
    for item in place.get("hops") or ():
        if item.get("ttl") == hop:
            hop_rows.append(ViewRow(FAIL, f"── {FILTER_MARK} ──"))
        answered = bool(item.get("address"))
        hop_rows.append(
            ViewRow(
                INFO,
                f"Узел {item.get('ttl', '')}",
                " · ".join(
                    part
                    for part in (str(item.get("address") or "не ответил"), _hop_time(item.get("rtt_ms")), str(item.get("owner") or "") if answered else "")
                    if part
                ),
            )
        )
    if hop_rows:
        sections.append(ViewSection(f"Дорога до {place.get('host', '')} ({place.get('address', '')})", tuple(hop_rows)))
    return BlockView("filter", "Место фильтра", level, status, tuple(preview), tuple(sections))


_WAY_STATES = {"passed": OK, "cut": FAIL}
_HABIT_STATUS = {
    "tcp": "Не склеивает пакеты",
    "timer": "Склеивает только подряд",
    "records": "Не разбирает записи TLS",
    "none": "Собирает приветствие целиком",
    "not_by_name": "По имени не режет",
}


def _habits_status(habits: dict, disturbed: list[str], ech_state: str) -> str:
    """Слово результата: вывод о фильтре, если он один на все сайты."""
    if disturbed:
        return "Мешает обход"
    codes = {str(site.get("code")) for site in habits.get("sites") or ()} - {"unknown"}
    if len(codes) == 1:
        return _HABIT_STATUS.get(codes.pop(), "Проверено дробление")
    if len(codes) > 1:
        return "По сайтам по-разному"
    if habits.get("sites"):
        return "Вывода нет"
    return "ECH режется" if ech_state == WARN else "ECH проходит"


def habits_block(habits: dict) -> BlockView:
    """«Как работает фильтр»: какое дробление приветствия проходит и режется ли ECH."""
    from diagnostics.filter_habits import NOT_RUN, way_text

    ech = habits.get("ech") or {}
    ech_state = {"blocked": WARN, "fine": OK}.get(str(ech.get("state")), UNKNOWN)
    disturbed = [str(name) for name in habits.get("disturbed") or ()]
    lines = [ViewRow(WARN if disturbed else INFO, _capital(str(habits.get("headline") or "")))]
    if habits.get("advice"):
        lines.append(ViewRow(INFO, "Что делать" if disturbed else "Какие стратегии пробовать", str(habits["advice"])))
    if ech.get("text"):
        lines.append(ViewRow(ech_state, "ECH", _capital(str(ech["text"]))))
    sections = [ViewSection("Что делает фильтр", tuple(lines[:2]))]
    for site in habits.get("sites") or ():
        rows = [ViewRow(INFO, "Вывод", _capital(str(site.get("text") or "")))]
        if site.get("advice"):
            rows.append(ViewRow(INFO, "Какие стратегии пробовать", str(site["advice"])))
        for way in site.get("ways") or ():
            if way.get("state") == NOT_RUN:
                continue
            # Строка способа: что вышло с именем сайта, что с безобидным именем и что это значит.
            rows.append(ViewRow(_WAY_STATES.get(str(way.get("state")), UNKNOWN), str(way.get("title") or ""), way_text(way)))
        sections.append(ViewSection(f"{site.get('host', '')} ({site.get('address', '')})", tuple(rows)))
    if habits.get("sites") and habits.get("limits"):
        sections.append(ViewSection("Чего эта проверка не видит", (ViewRow(INFO, str(habits["limits"])),)))
    if ech.get("text"):
        rows = [ViewRow(ech_state, "Шифрованное имя сайта (ECH)", _capital(str(ech["text"])))]
        if ech.get("advice"):
            rows.append(ViewRow(INFO, "Что делать", str(ech["advice"])))
        sections.append(ViewSection("Cloudflare и ECH", tuple(rows)))
    return BlockView(
        "habits",
        "Как работает фильтр",
        WARN if ech_state == WARN or disturbed else INFO,
        _habits_status(habits, disturbed, ech_state),
        tuple(lines),
        tuple(sections),
    )


# ---------------------------------------------------------------------------
# С Zapret и без
# ---------------------------------------------------------------------------

COMPARE_STATES = {"helped": OK, "not_helped": FAIL, "broken": WARN, "fine_anyway": INFO}
_COMPARE_STATUS = {OK: "Обход справляется", WARN: "Помогает не всем", FAIL: "Пресет не помог"}


def one_preset_note(preset: str) -> str:
    """Оговорка, без которой сравнение читают неверно: это итог одного пресета, а не Zapret вообще."""
    name = f"«{preset}»" if preset else "выбранного сейчас"
    return (
        f"Это итог одного пресета — {name}. Пресет решает, для каких сайтов и какой обход включён: "
        "с другим пресетом или после «Подбора стратегии» результат может быть другим."
    )


def compare_block(compare: dict) -> BlockView:
    """«С Zapret и без»: что дала пара проверок — эта и прошлая в противоположном состоянии обхода."""
    from diagnostics.compare import GROUP_TITLES
    from diagnostics.history import format_time

    level = shown_level(compare.get("level"))
    preset = str(compare.get("preset") or "")
    groups = [(key, [str(name) for name in compare.get(key) or ()]) for key in COMPARE_STATES]
    summary = tuple(ViewRow(COMPARE_STATES[key], GROUP_TITLES[key], ", ".join(names)) for key, names in groups if names)
    other = "без Zapret" if compare.get("zapret_in") == "current" else "с Zapret"
    facts = [ViewRow(INFO, f"Прошлая проверка {other}", format_time(str(compare.get("other_time") or "")))]
    if compare.get("preset"):
        facts.append(ViewRow(INFO, "Пресет", str(compare["preset"])))
    # Первой строкой — что это итог одного пресета: без этого «не помог» читают как «Zapret не поможет».
    sections = [ViewSection(str(compare.get("headline") or "С Zapret и без"), (ViewRow(INFO, one_preset_note(preset)), *summary))]
    # Строка плитки несёт ключ группы: экран по нему открывает страницу сайта.
    sections += [
        ViewSection(GROUP_TITLES[key], tuple(ViewRow(COMPARE_STATES[key], name, extra=key) for name in names), tiles=True)
        for key, names in groups
        if names
    ]
    sections.append(ViewSection("Что сравнивали", tuple(facts)))
    notes = tuple(ViewRow(INFO, str(note)) for note in compare.get("notes") or ())
    if notes:
        sections.append(ViewSection("Как это читать", notes))
    return BlockView(
        "compare",
        "С Zapret и без",
        level,
        # В слове итога назван пресет: сравнение говорит о нём одном.
        " · ".join(part for part in (_COMPARE_STATUS.get(level, "Сравнение"), f"пресет «{preset}»" if preset else "") if part),
        summary,
        tuple(sections),
    )


# ---------------------------------------------------------------------------
# Реестр РКН
# ---------------------------------------------------------------------------


def registry_rows(summary: dict) -> tuple[ViewRow, ...]:
    """Сам список реестра РКН, по которому ставятся отметки «в реестре»: свежий ли и сколько в нём записей."""
    state = str(summary.get("state") or "")
    if not state:
        return ()
    if not summary.get("hosts"):
        return (ViewRow(UNKNOWN, "Список", "скачать не удалось — отметок «в реестре» в этой проверке нет"),)
    when = time.strftime("%d.%m.%Y", time.localtime(float(summary.get("updated") or 0)))
    fresh = state == "fresh"
    return (
        ViewRow(OK if fresh else WARN, "Скачан", when if fresh else f"{when} — давно не обновлялся, свежий скачать не удалось"),
        ViewRow(INFO, "Имён сайтов", f"{int(summary['hosts']):,}".replace(",", " ")),
        ViewRow(INFO, "Адресов и сетей", f"{int(summary.get('networks') or 0):,}".replace(",", " ")),
        ViewRow(INFO, "Важно", "сайт могут блокировать и без записи в реестре — так режут, например, YouTube"),
    )


def registry_block(summary: dict) -> BlockView | None:
    rows = registry_rows(summary)
    if not rows:
        return None
    level = max((row.level for row in rows), key=level_rank, default=INFO)
    return BlockView("registry", "Реестр РКН", level, "", rows, (ViewSection("Реестр РКН", rows),))


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------

# Ключ отчёта → построитель блока. Порядок — порядок карточек и разделов текста.
_BUILDERS = (
    ("network", network_block),
    ("freeze", lambda data: hostings_block(data) if data.get("servers") else None),
    ("voice", voice_block),
    ("telegram", telegram_block),
    ("dns_servers", dns_servers_block),
    ("speed", speed_block),
    ("ipv6", ipv6_block),
    ("filter", filter_block),
    ("habits", habits_block),
    ("system", system_block),
    ("crowd", crowd_block),
    ("quic_fallback", quic_fallback_block),
    ("compare", compare_block),
)
# Блоки, которые на экране не отдельные карточки (реестр живёт в карточке «Ход проверки»), а в тексте — свой раздел.
_TEXT_ONLY = (("registry", registry_block),)


def build_block(key: str, data) -> BlockView | None:
    """Блок по ключу отчёта (``freeze``, ``voice``, ``network`` …). None — показывать нечего."""
    if not data:
        return None
    for name, builder in (*_BUILDERS, *_TEXT_ONLY):
        if name == key:
            return builder(data)
    return None


def build_blocks(report: dict) -> list[BlockView]:
    """Все отдельные проверки отчёта в порядке показа; подмена DNS идёт перед DNS-серверами."""
    blocks: list[BlockView] = []
    for name, _builder in _BUILDERS:
        if name == "dns_servers":
            dns = dns_block(report)
            if dns is not None:
                blocks.append(dns)
        view = build_block(name, report.get(name))
        if view is not None:
            blocks.append(view)
    return blocks


def text_lines(key: str, data) -> list[str]:
    """Строки текстового отчёта для раздела по его словарю. Пусто — раздела нет."""
    view = build_block(key, data)
    return render_block_text(view) if view is not None else []


def render_block_text(view: BlockView) -> list[str]:
    """Блок строками текстового отчёта: заголовок и те же разделы, что на карточке."""
    # Раздел с тем же названием, что и у блока, второй раз заголовка не получает: строки идут под баннером.
    inner = []
    for section in view.sections:
        inner.extend(render_text([section])[0 if section.title != view.title else 1 :])
    return ["", f"━━━━━━━━ {view.title} ━━━━━━━━", *inner]
