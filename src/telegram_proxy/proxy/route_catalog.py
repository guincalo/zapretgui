from __future__ import annotations

from dataclasses import dataclass


WSS_PATH = "/apiws"


# Адреса входа веб-релеев Telegram (kwsN.web.telegram.org/apiws), те же, что в
# ZaStoGram: officialRelayIpsForDc в jni/tgnet/wss/WssSocket.cpp и
# OfficialRelayIngresses в mtproto/proxy/wss/socket.cpp. Новый адрес — строка
# здесь и в обоих клиентах.
#
# Каждый адрес обслуживает только свои DC: на чужое имя адрес DC2/DC4 отвечает
# 302 с X-Redirect-Host, адрес DC1/DC3 молчит. Отсюда прежнее «релеи есть только
# у DC2 и DC4»: все проверки ходили на один 149.154.167.220. Релеи DC1–DC5
# работают (замеры 08.08.2026 и 16.10.2026).
#
# У DC2/DC4 адресов два. 149.154.167.99 отдаёт DNS (и для web.telegram.org),
# 149.154.167.220 месяцами держали зашитым все клиенты. 16.10.2026 оба приняли
# все четыре имени DC2/DC4, по 25 кругов без разницы сверх шума. Провайдер,
# закрывший один по IP, оставляет второй.
RELAY_INGRESS: dict[int, tuple[str, ...]] = {
    1: ("149.154.174.100",),
    2: ("149.154.167.220", "149.154.167.99"),
    3: ("149.154.174.100",),
    4: ("149.154.167.220", "149.154.167.99"),
    5: ("149.154.170.100",),
}


def relay_addresses(dc: int) -> tuple[str, ...]:
    """Адреса входа веб-релея DC в порядке по умолчанию; пусто, если релея нет."""

    return RELAY_INGRESS.get(int(dc), ())


def relay_domain(dc: int, *, is_media: bool) -> str:
    """Имя релея: kwsN для обычных соединений, kwsN-1 для медиа."""

    return f"kws{int(dc)}{'-1' if is_media else ''}.web.telegram.org"


@dataclass(frozen=True)
class CdnFront:
    """Фронт Cloudflare: домен зоны и два её адреса (104.21.x и 172.67.x).

    Хост для DC строится как kwsN.<domain>; SNI и Host совпадают с ним
    (это не domain fronting). Подключение идёт сразу по адресу, без DNS.
    """

    domain: str
    addresses: tuple[str, str]

    def host_for(self, dc: int) -> str:
        return f"kws{int(dc)}.{self.domain}"


# Фронты tg-ws-proxy (github.com/Flowseal/tg-ws-proxy) в том же порядке, что и
# в ZaStoGram (jni/tgnet/wss/WssSocket.cpp, kCdnFronts). На 27.09.2026 по логам
# тестеров ZaStoGram фронты приносили данные там, где релей kwsN заблокирован.
CDN_FRONTS: tuple[CdnFront, ...] = (
    CdnFront("pclead.co.uk", ("104.21.80.254", "172.67.155.165")),
    CdnFront("offshor.co.uk", ("104.21.43.90", "172.67.177.105")),
    CdnFront("cakeisalie.co.uk", ("104.21.41.25", "172.67.159.17")),
    CdnFront("noskomnadzor.co.uk", ("104.21.70.196", "172.67.138.236")),
    CdnFront("lovetrue.co.uk", ("104.21.21.168", "172.67.199.162")),
    CdnFront("sorokdva.co.uk", ("104.21.69.145", "172.67.209.89")),
    CdnFront("pyatdesyatdva.co.uk", ("104.21.73.83", "172.67.189.26")),
    CdnFront("kartoshka.co.uk", ("104.21.39.36", "172.67.142.232")),
    CdnFront("sorokodin.co.uk", ("104.21.84.223", "172.67.197.117")),
    CdnFront("pyatdesyatodin.co.uk", ("104.21.48.178", "172.67.155.85")),
    CdnFront("notelega.co.uk", ("104.21.33.146", "172.67.146.105")),
    CdnFront("ebally.co.uk", ("104.21.78.6", "172.67.214.68")),
    CdnFront("nebally.co.uk", ("104.21.7.253", "172.67.156.145")),
    CdnFront("havegreatday.co.uk", ("104.21.25.159", "172.67.134.93")),
    CdnFront("pomogite.co.uk", ("104.21.44.55", "172.67.195.218")),
    CdnFront("fixtelega.co.uk", ("104.21.64.155", "172.67.152.37")),
    CdnFront("sadnews.co.uk", ("104.21.51.133", "172.67.180.160")),
    CdnFront("onedaychamp.co.uk", ("104.21.37.105", "172.67.207.129")),
    CdnFront("stopblocking.co.uk", ("104.21.35.206", "172.67.179.145")),
    CdnFront("nothingthere.co.uk", ("104.21.78.5", "172.67.214.67")),
)

# Фронты несут DC1–DC5 и DC203: у DC203 (медиа без Premium) нет веб-релея
# Telegram, но kws203.<домен фронта> 16.10.2026 ответил настоящим resPQ на всех
# проверенных фронтах. Пока DC203 ходил только через воркеры, исчерпанный
# суточный лимит Cloudflare оставлял его без пути (ZaStoGram, тот же день).
CDN_FRONT_DCS: tuple[int, ...] = (1, 2, 3, 4, 5, 203)

# Туннель через воркер Cloudflare (zastogram-ws-worker/worker.js):
# wss://<host>/apiws?dst=<IPv4 DC>, дальше воркер идёт на DC обычным TCP.
# Воркеры лежат на разных учётных записях Cloudflare: у бесплатной записи
# 100 000 запросов в сутки, после них воркер до 00:00 UTC отвечает 429
# (error code: 1027). Каждая установка начинает перебор со случайного воркера,
# так нагрузка делится между записями. Новый воркер — новая строка здесь и в
# ZaStoGram (jni/tgnet/wss/WssSocket.cpp, mtproto/proxy/wss/socket.cpp).
TUNNEL_HOSTS: tuple[str, ...] = (
    "edge.amberwick.workers.dev",
    "fuckyourkn.copperbrook.workers.dev",
    "nodeone.brackencombe.workers.dev",
)



__all__ = [
    "CDN_FRONTS",
    "CDN_FRONT_DCS",
    "CdnFront",
    "RELAY_INGRESS",
    "TUNNEL_HOSTS",
    "WSS_PATH",
    "relay_addresses",
    "relay_domain",
]
