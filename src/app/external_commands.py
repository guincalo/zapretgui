from __future__ import annotations

import webbrowser
from typing import Callable

from app.external_actions import ExternalActionResult
from utils import site_links


def open_url(url: str, *, open_url_fn: Callable | None = None):
    if open_url_fn is not None:
        return open_url_fn(url)

    target = str(url or "").strip()
    if not target:
        return ExternalActionResult(ok=False, error="Пустая ссылка")
    try:
        webbrowser.open(site_links.resolve(target))
        return ExternalActionResult(ok=True)
    except Exception as exc:
        return ExternalActionResult(ok=False, error=str(exc))


__all__ = ["open_url"]
