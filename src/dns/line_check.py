"""Что прошлая проверка «DNS-серверов» нашла на линии пользователя — для плиток «Настройки DNS».

Отметка «блокируется» в каталоге серверов общая для всей России, а блокировки у
каждого провайдера свои. Если человек недавно проверял серверы в BlockCheck,
его собственный результат точнее каталога: плитка берёт отметку из него и
называет дату проверки. Старая проверка (дольше ``MAX_AGE``) не показывается —
за это время провайдер мог поменять правила.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from dns.state import LineCheck

__all__ = ["MAX_AGE", "load_line_check"]

MAX_AGE = timedelta(days=14)


def _last_run() -> dict | None:
    from settings.store import read_settings

    runs = dict(read_settings().get("blockcheck") or {}).get("servers_history")
    if not isinstance(runs, list):
        return None
    finished = [run for run in runs if isinstance(run, dict) and run.get("log_file")]
    return finished[-1] if finished else None


def load_line_check(*, now: datetime | None = None) -> LineCheck | None:
    """Итог последней сохранённой проверки серверов или None — её нет, она стара или не читается."""
    try:
        run = _last_run()
        if run is None:
            return None
        when = datetime.fromisoformat(str(run.get("time") or ""))
        if (now or datetime.now()) - when > MAX_AGE:
            return None
        from dns.commands import load_past_server_check_report
        from dns.server_check_plans import CARD_UNKNOWN, build_cards

        report = load_past_server_check_report(str(run["log_file"]))
    except (OSError, ValueError, TypeError):
        return None
    if report is None:
        return None
    cards = tuple((card.server, card.status, card.note) for card in build_cards(report) if card.status != CARD_UNKNOWN)
    return LineCheck(time=when.isoformat(timespec="seconds"), cards=cards) if cards else None
