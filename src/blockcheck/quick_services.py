"""Самые известные заблокированные сервисы: что проверять по нажатию на карточку.

Раньше сайт для подбора стратегии выбирали из текстового меню или вводили руками. Карточки
с логотипами нужны, чтобы YouTube, Discord и другие открывались одним нажатием. Список короткий
и состоит из сервисов, которые чаще всего ищут. Здесь только данные: какой адрес подставить и как
сервис называется; логотипы рисует ``blockcheck.ui.brand_icons`` по ключу сервиса.
"""

from __future__ import annotations

from dataclasses import dataclass

from blockcheck.hosts import host_of

__all__ = ["QUICK_SERVICES", "QuickService", "service_for_target"]


@dataclass(frozen=True, slots=True)
class QuickService:
    # Ключ сервиса: он же ключ логотипа в ``brand_icons``.
    key: str
    title: str
    # Адрес, который подставляется в поле «Какой сайт проверить».
    target: str
    # Подсказка под курсором: что именно проверяется.
    hint: str = ""
    # Адрес для «Проверки домена»: там нужна страница сайта, остальные адреса сервиса (у YouTube — видеосервер)
    # добавляются к ней сами. Пусто — тот же адрес, что и для подбора.
    site: str = ""

    @property
    def site_host(self) -> str:
        return self.site or self.target


QUICK_SERVICES: tuple[QuickService, ...] = (
    QuickService(
        "youtube",
        "YouTube",
        "googlevideo.com",
        "Видео YouTube: проверяется тот видеосервер, который YouTube выдаёт именно вашей сети",
        "www.youtube.com",
    ),
    QuickService("discord", "Discord", "discord.com", "Сайт и вход в Discord"),
    QuickService("telegram", "Telegram", "telegram.org", "Сайт Telegram"),
    QuickService("instagram", "Instagram", "www.instagram.com", "Сайт Instagram"),
    QuickService("facebook", "Facebook", "www.facebook.com", "Сайт Facebook"),
    QuickService("x", "X (Twitter)", "x.com", "Сайт X"),
    QuickService("whatsapp", "WhatsApp", "web.whatsapp.com", "Веб-версия WhatsApp"),
    QuickService("spotify", "Spotify", "www.spotify.com", "Сайт Spotify"),
    QuickService("chatgpt", "ChatGPT", "chatgpt.com", "Сайт ChatGPT"),
    QuickService("rutracker", "RuTracker", "rutracker.org", "Сайт RuTracker"),
    QuickService("twitch", "Twitch", "www.twitch.tv", "Сайт Twitch"),
    QuickService("linkedin", "LinkedIn", "www.linkedin.com", "Сайт LinkedIn"),
)


def service_for_target(value: str) -> QuickService | None:
    """Карточка, чей адрес сейчас стоит в поле: так на ней видно, какой сервис выбран. ``None`` — другой адрес."""
    host = host_of(str(value or "")).lower()
    if not host:
        return None
    return next((item for item in QUICK_SERVICES if host in (item.target, item.site_host)), None)
