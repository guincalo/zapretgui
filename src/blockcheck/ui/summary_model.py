"""Итог проверки BlockCheck для экрана — без Qt.

Здесь то, что решает, а не рисует: как проблемы собираются в группы, какой
карточке принадлежит строка итога, как называется сайт и что о нём сказать,
предупреждения про программы обхода и строки таблицы «Прошлые проверки».
Виджеты (``blockcheck.ui.check_results``) берут это готовым; проверяется без окна.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from diagnostics.block_kind import KIND_ORDER, KIND_OTHER, KINDS
from settings.schema import CHECK_HISTORY_PROBLEMS_LIMIT

def own_advice(problem: dict) -> list[str]:
    """Советы строки без фраз-свидетельств («с именем сайта соединение обрывается…»)."""
    evidence = set(problem.get("evidence") or ())
    return [str(item) for item in problem.get("advice") or () if item not in evidence]


def shared_advice(problems: list[dict]) -> list[str]:
    """Советы, одинаковые у всех строк группы: их показывают один раз, в заголовке."""
    if len(problems) < 2:
        return []
    others = [set(own_advice(problem)) for problem in problems[1:]]
    return [item for item in own_advice(problems[0]) if all(item in other for other in others)]


def group_problems(problems: list[dict]) -> list[tuple[str, list[dict]]]:
    """Проблемы по видам блокировки. Группы идут по важности худшей строки, внутри — как пришли."""
    level_order = {"fail": 0, "warn": 1, "unknown": 2, "ok": 3}
    kind_order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    groups: dict[str, list[dict]] = {}
    for problem in problems:
        kind = str(problem.get("kind") or KIND_OTHER)
        groups.setdefault(kind if kind in KINDS else KIND_OTHER, []).append(problem)

    def _rank(item: tuple[str, list[dict]]) -> tuple[int, int, int]:
        kind, rows = item
        # «Остальное» — всегда последним: без названного вида блокировки ему не место между группами.
        return kind == KIND_OTHER, min(level_order.get(str(row.get("level")), 9) for row in rows), kind_order.get(kind, 99)

    return sorted(groups.items(), key=_rank)


# Виды, которые не относятся к одному сайту: у таких строк точка важности, а не логотип.
# Всё остальное — про сайт, в том числе виды, которых здесь ещё не знают.
NOT_SITE_KINDS = frozenset({"dns", "quic", "system", "network"})


_FOR_SITES = re.compile(r"^(.*?) для: (.+)$")


def split_problem_text(problem: dict) -> tuple[str, str]:
    """Заголовок строки и пояснение к нему.

    У сайта, который не открывается целиком, заголовок — его название. Остальные
    проблемы приходят одной фразой: находки по DNS пишутся как «что случилось:
    подробности», прочие — «что случилось. Что это значит» или «что: как».
    """
    title = str(problem.get("title") or "").strip()
    text = str(problem.get("text") or "").strip()
    if title:
        return title, ""
    separators = (": ", ". ") if problem.get("kind") == "dns" else (". ", ": ")
    head, tail = text, ""
    for separator in separators:
        head, found, tail = text.partition(separator)
        tail = tail.strip()
        if found and tail:
            break
    else:
        return text, ""
    tail = f"{tail[:1].upper()}{tail[1:]}"
    return head.rstrip("."), tail if tail.endswith((".", "!", "?")) else f"{tail}."


def split_named_sites(title: str) -> tuple[str, list[str]]:
    """«QUIC блокируется по имени для: YouTube, X» → заголовок без перечня и сами сайты."""
    match = _FOR_SITES.match(str(title or ""))
    if match is None:
        return title, []
    return match.group(1), [name.strip() for name in match.group(2).split(",") if name.strip()]


def cut_providers(report: dict) -> list[tuple[str, list[str]]]:
    """Хостинги, у которых нашёлся обрыв загрузки или отправки, и их серверы с обрывом."""
    providers: dict[str, list[str]] = {}
    for server in (report.get("freeze") or {}).get("servers") or ():
        if server.get("state") == "freeze":
            providers.setdefault(str(server.get("provider") or "Без названия"), []).append(str(server.get("host") or ""))
    return list(providers.items())


# Раздел отчёта, из которого выведена проблема, → карточка с его полным отчётом.
_SECTION_CARDS = {
    "freeze": "hostings",
    "voice": "voice",
    "telegram": "telegram",
    "system": "system",
    "network": "network",
    "dns": "dns",
    "dns_servers": "dns_servers",
    "speed": "speed",
    "crowd": "crowd",
}


def problem_card_key(problem: dict, report: dict) -> str:
    """Ключ карточки с полным отчётом по этой проблеме. Пусто — такой карточки нет.

    Проблема сама говорит, о каком она сайте (``site``) и из какого раздела отчёта
    (``section``); у замеров скорости и соединений сайт находится по адресу.
    Старые отчёты дополняет этими полями ``diagnostics.report_upgrade``.
    """
    site = str(problem.get("site") or "")
    target = str(problem.get("target") or "")
    if not site and target:
        site = next(
            (
                str(service.get("key") or "")
                for service in report.get("services") or ()
                if any(str(item.get("host") or "") == target for item in service.get("targets") or ())
            ),
            "",
        )
    if site:
        return f"site:{site}"
    return _SECTION_CARDS.get(str(problem.get("section") or ""), "")


def is_site_problem(problem: dict) -> bool:
    """Проблема про один сайт: такие идут карточками, остальные (DNS, компьютер, сеть) — строками."""
    kind = str(problem.get("kind") or KIND_OTHER)
    if kind in NOT_SITE_KINDS or kind == "voice":
        return False
    return bool(problem.get("title") or problem.get("target") or problem.get("site_key") or problem.get("site"))


def site_name(problem: dict) -> str:
    """Название сайта для карточки: своё, с карточки под итогом или первое слово фразы."""
    name = str(problem.get("title") or problem.get("site_label") or "").strip()
    return name or str(problem.get("text") or "").split(" ", 1)[0].rstrip(":,")


def site_note(problem: dict) -> str:
    """Вторая строка карточки: адрес и причина в два-три слова, а у частично работающего — что не работает."""
    if not problem.get("title"):
        head = str(problem.get("text") or "").partition(": ")[0]
        rest = head.removeprefix(site_name(problem)).strip(" ,")
        if rest:
            return rest
    # Адрес — без «www.»: строка короче и реже обрезается.
    target = str(problem.get("target") or "").removeprefix("www.")
    return " · ".join(part for part in (target, str(problem.get("cause_word") or "")) if part)


def site_explanation(problem: dict) -> tuple[str, ...]:
    """Полное объяснение по сайту для подсказки: причина, факты проверки и советы."""
    evidence = set(problem.get("evidence") or ())
    lines = [str(item) if item in evidence else f"→ {item}" for item in problem.get("advice") or ()]
    if not problem.get("title"):
        # Частично работающий сайт: причина стоит во фразе после двоеточия.
        tail = str(problem.get("text") or "").partition(": ")[2].strip()
        if tail:
            lines.insert(0, f"{tail[:1].upper()}{tail[1:]}{'' if tail.endswith('.') else '.'}")
    return tuple(lines)


def bypass_warning(tools) -> str:
    """Фраза о том, что проверка шла вместе с VPN или другим обходом. Пусто — их не было."""
    names = [str(name) for name in tools or () if str(name).strip()]
    if not names:
        return ""
    return (
        # В списке бывают и программы, и строки вида «VPN-подключение «имя адаптера»» — фраза годится для обоих.
        f"Внимание: проверка шла через {', '.join(names)}. Итог показывает сеть вместе с этим, "
        "а не то, что делает провайдер. Чтобы увидеть настоящую картину, отключите это и повторите проверку."
    )


def bypass_note(tools) -> str:
    """Спокойная строка: VPN запущены, но интернет идёт мимо них. Пусто — их нет."""
    names = [str(name) for name in tools or () if str(name).strip()]
    if not names:
        return ""
    return (
        f"Запущены {', '.join(names)}, но интернет идёт напрямую, мимо них: проверка описывает сеть провайдера. "
        "Браузер и приложения при этом могут ходить через них и открывать то, что здесь закрыто."
    )


def environment_text(report: dict) -> str:
    running = report.get("zapret_running")
    parts = []
    if running is True:
        parts.append("Zapret включён — проверено с обходом")
    elif running is False:
        parts.append("Zapret выключен — проверено без обхода")
    elapsed = report.get("elapsed")
    if isinstance(elapsed, (int, float)):
        parts.append(f"проверка заняла {elapsed:.0f} с")
    if report.get("timed_out"):
        parts.append("часть проверок не успела")
    return " · ".join(parts)


# Сколько прошлых проверок видно сразу и сколько — по кнопке «Показать все».
HISTORY_SHOWN = 6


HISTORY_ALL = 50


# Столько проблем одного прогона хранит история.
HISTORY_PROBLEMS_KEPT = CHECK_HISTORY_PROBLEMS_LIMIT


_HISTORY_MARKS = {"ok": "✓", "warn": "!", "fail": "✗", "unknown": "?"}


def history_lines(runs, limit: int = HISTORY_SHOWN) -> list[tuple[str, str]]:
    """Строки карточки «Прошлые проверки», новые сверху: (уровень, текст)."""
    from diagnostics.history import format_time

    lines: list[tuple[str, str]] = []
    for run in reversed(list(runs or ())[-max(1, int(limit)) :]):
        level = str(run.get("level") or "unknown")
        level = level if level in _HISTORY_MARKS else "unknown"
        parts = [format_time(str(run.get("time") or "")), str(run.get("title") or "")]
        text = " · ".join(part for part in parts if part)
        lines.append((level, f"{_HISTORY_MARKS[level]} {text} — {run.get('headline') or 'итог не записан'}"))
    return lines


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """Одна прошлая проверка для таблицы: когда, что проверяли, чем кончилось и что изменилось."""

    level: str
    when: str
    scope: str
    # «Открывается 4 из 9 · проблем: 14».
    outcome: str
    # По сравнению с предыдущей такой же проверкой.
    changes: str
    opened: int = 0
    blocked: int = 0
    unknown: int = 0
    # Первая проблема прогона — для подсказки.
    headline: str = ""


def history_rows(runs, limit: int = HISTORY_SHOWN) -> list[HistoryRow]:
    """Строки таблицы «Прошлые проверки», новые сверху.

    Итог — сколько сайтов открывалось и сколько было проблем, а не одна первая
    проблема: по ней шесть разных прогонов выглядели одинаково. «Что
    изменилось» — сравнение с предыдущей проверкой того же набора сайтов.
    """
    from diagnostics.history import describe_changes, format_time, previous_run
    from diagnostics.levels import is_open

    runs = list(runs or ())
    rows: list[HistoryRow] = []
    for index in range(len(runs) - 1, max(-1, len(runs) - 1 - max(1, int(limit))), -1):
        run = runs[index]
        level = str(run.get("level") or "unknown")
        level = level if level in _HISTORY_MARKS else "unknown"
        states = [str(state) for state in (run.get("states") or {}).values()]
        opened = sum(1 for state in states if is_open(state))
        blocked = sum(1 for state in states if state == "fail")
        problems = len(run.get("problems") or ())
        if states:
            parts = [f"Открывается {opened} из {len(states)}"]
            if problems:
                # В истории хранится не больше стольких проблем прогона — дальше счёт неточный.
                parts.append(f"проблем: {problems}{'+' if problems >= HISTORY_PROBLEMS_KEPT else ''}")
            outcome = " · ".join(parts)
        else:
            outcome = str(run.get("headline") or "итог не записан")
        previous = previous_run(runs[:index], run)
        if previous is None:
            changes = "первая такая проверка"
        else:
            found = describe_changes(previous, run)
            changes = "; ".join(found) if found else "без изменений"
        rows.append(
            HistoryRow(
                level=level,
                when=format_time(str(run.get("time") or "")),
                scope=str(run.get("title") or ""),
                outcome=outcome,
                changes=f"{changes[:1].upper()}{changes[1:]}",
                opened=opened,
                blocked=blocked,
                unknown=len(states) - opened - blocked,
                headline=str(run.get("headline") or ""),
            )
        )
    return rows
