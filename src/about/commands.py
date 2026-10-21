from __future__ import annotations

import webbrowser
from dataclasses import dataclass

from log.log import log
from utils.site_links import resolve


@dataclass(slots=True)
class AboutActionResult:
    ok: bool
    message: str


def open_support_discussions() -> AboutActionResult:
    from config.urls import SUPPORT_ISSUES_URL

    try:
        target = resolve(SUPPORT_ISSUES_URL)
        webbrowser.open(target)
        log(f"Открыт Forgejo Issues: {target}", "INFO")
        return AboutActionResult(True, target)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_docs_home() -> AboutActionResult:
    from config.urls import DOCS_URL

    try:
        target = resolve(DOCS_URL)
        webbrowser.open(target)
        log(f"Открыт вики-сайт: {target}", "INFO")
        return AboutActionResult(True, target)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_telegram(
    domain: str, *, post: int | None = None, start: str | None = None
) -> AboutActionResult:
    try:
        from config.telegram_links import open_telegram_link

        open_telegram_link(domain, post=post, start=start)
        log(f"Открыт Telegram: {domain}", "INFO")
        return AboutActionResult(True, domain)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_telegram_folder(slug: str) -> AboutActionResult:
    """Папка Telegram (t.me/addlist/...): добавляет сразу все чаты из неё."""
    try:
        from config.telegram_links import open_telegram_link

        open_telegram_link("", slug=slug)
        log(f"Открыта папка Telegram: {slug}", "INFO")
        return AboutActionResult(True, slug)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_link(url: str) -> AboutActionResult:
    try:
        target = resolve(url)
        webbrowser.open(target)
        log(f"Открыта ссылка: {target}", "INFO")
        return AboutActionResult(True, target)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_discord(url: str) -> AboutActionResult:
    try:
        target = resolve(url)
        webbrowser.open(target)
        log(f"Открыт Discord: {target}", "INFO")
        return AboutActionResult(True, target)
    except Exception as e:
        return AboutActionResult(False, str(e))


def open_github(url: str) -> AboutActionResult:
    try:
        target = resolve(url)
        webbrowser.open(target)
        log(f"Открыт Forgejo: {target}", "INFO")
        return AboutActionResult(True, target)
    except Exception as e:
        return AboutActionResult(False, str(e))


__all__ = [
    "AboutActionResult",
    "open_discord",
    "open_docs_home",
    "open_github",
    "open_link",
    "open_support_discussions",
    "open_telegram",
    "open_telegram_folder",
]
