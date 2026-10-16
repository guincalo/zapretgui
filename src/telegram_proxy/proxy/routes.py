"""Порядок маршрутов для одного соединения Telegram.

Главный путь — WSS, как в ZaStoGram:

1. релей Telegram kwsN / kwsN-1 для DC1–DC5 по адресу входа из
   route_catalog.RELAY_INGRESS (у DC2/DC4 их два: первым тот, что последним
   ответил); если подавлены все адреса — тот же домен через DNS;
2. свой домен Cloudflare и свой Worker из настроек, если они заданы;
3. фронты Cloudflare из CDN_FRONTS с курсором и запомненным рабочим адресом;
4. туннель через воркеры из TUNNEL_HOSTS по кругу, начиная со своего для
   каждой установки (для DC203 это единственная WSS-ступень);
5. внешний SOCKS5 (выбранный сервер страны) — запасной путь;
6. прямой TCP — только когда внешнего SOCKS нет.

Если все WSS-ступени подавлены, после внешнего SOCKS ещё раз пробуются фронты
без учёта подавления: DC не должен остаться совсем без маршрута.

Ручной внешний сервер с режимом «always» — явный выбор пользователя: тогда
весь TCP идёт только через него.

Кластер. Медиа-соединение фронт и свой домен Cloudflare (+dc) приводят на
обычный кластер DC, остальные пути — на медиа-кластер. Если известно, на каком
кластере живёт ключ медиа-сессии (key_clusters), основные пути этого кластера
(релей, свой домен, фронты) встают раньше основных путей другого. Запасные
(воркеры, SOCKS, прямой TCP) остаются в конце: суточный лимит воркеров и
сервер страны не тратятся ради кластера.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from ipaddress import IPv4Address
from urllib.parse import urlencode

from telegram_proxy.proxy.cloudflare import CloudflareFallbackConfig
from telegram_proxy.proxy.health import (
    CDN_FAILURES_BEFORE_SUPPRESS,
    FAILURES_BEFORE_SUPPRESS,
    RouteHealth,
    address_key,
    cdn_key,
)
from telegram_proxy.proxy.key_clusters import CLUSTER_MEDIA, CLUSTER_REGULAR
from telegram_proxy.proxy.obfs import DC_FIELD_PLUS, DC_FIELD_RANDOM, DC_FIELD_SIGNED
from telegram_proxy.proxy.route_catalog import (
    CDN_FRONT_DCS,
    CDN_FRONTS,
    TUNNEL_HOSTS,
    WSS_PATH,
    relay_addresses,
    relay_domain,
)
from telegram_proxy.proxy.routing import UpstreamProxyConfig, should_route_upstream


KIND_RELAY = "relay"
KIND_USER_DOMAIN = "user_domain"
KIND_USER_WORKER = "user_worker"
KIND_FRONT = "front"
KIND_TUNNEL = "tunnel"
KIND_UPSTREAM = "upstream"
KIND_DIRECT = "direct"

WS_KINDS = frozenset({KIND_RELAY, KIND_USER_DOMAIN, KIND_USER_WORKER, KIND_FRONT, KIND_TUNNEL})
# Основные пути: без суточных лимитов и чужих серверов. Остальные — запасные.
PRIMARY_KINDS = frozenset({KIND_RELAY, KIND_USER_DOMAIN, KIND_FRONT})
# Пути, которые медиа приводят на обычный кластер: заголовок с +dc.
_REGULAR_CLUSTER_KINDS = frozenset({KIND_USER_DOMAIN, KIND_FRONT})

# Сколько фронтов пробовать внутри одного соединения Telegram.
FRONTS_PER_CONNECTION = 3
# Сколько воркеров-туннелей пробовать внутри одного соединения Telegram.
TUNNELS_PER_CONNECTION = 3

# Сколько байт от сервера доказывает, что маршрут действительно возит трафик.
RELAY_PROOF_BYTES = 1
FRONT_PROOF_BYTES = 32 * 1024
TUNNEL_PROOF_BYTES = 6 * 1024

# Названия маршрутов для route_events: страница Telegram Proxy ищет в них
# подстроки «cloudflare», «внеш», «tcp».
ROUTE_LABELS = {
    KIND_RELAY: "WSS",
    KIND_USER_DOMAIN: "Cloudflare",
    KIND_USER_WORKER: "Cloudflare Worker",
    KIND_FRONT: "Cloudflare",
    KIND_TUNNEL: "Cloudflare Worker",
    KIND_UPSTREAM: "внешний SOCKS5",
    KIND_DIRECT: "TCP",
}


@dataclass(frozen=True, slots=True)
class Route:
    kind: str
    connect_host: str = ""
    sni: str = ""
    path: str = WSS_PATH
    port: int = 443
    dc_field: str = DC_FIELD_SIGNED
    health_key: str = ""
    health_threshold: int = FAILURES_BEFORE_SUPPRESS
    address_health_key: str = ""
    proof_bytes: int = RELAY_PROOF_BYTES
    pool_key: str = ""
    front_index: int = -1
    front_family: int = -1
    target_host: str = ""
    target_port: int = 443
    cluster: str = CLUSTER_REGULAR

    @property
    def is_ws(self) -> bool:
        return self.kind in WS_KINDS

    @property
    def is_primary(self) -> bool:
        return self.kind in PRIMARY_KINDS

    @property
    def label(self) -> str:
        return ROUTE_LABELS.get(self.kind, self.kind)

    def describe(self) -> str:
        if self.kind in (KIND_UPSTREAM, KIND_DIRECT):
            return f"{self.target_host}:{self.target_port}"
        if self.connect_host and self.connect_host != self.sni:
            return f"wss://{self.sni}{self.path} via {self.connect_host}"
        return f"wss://{self.sni}{self.path}"


@dataclass(frozen=True, slots=True)
class PlanInput:
    dc: int
    is_media: bool
    target_host: str
    target_port: int = 443
    upstream: UpstreamProxyConfig = field(default_factory=UpstreamProxyConfig)
    cloudflare: CloudflareFallbackConfig = field(default_factory=CloudflareFallbackConfig)
    # Кластер, на котором живёт ключ этого соединения ("" — неизвестен).
    key_cluster: str = ""


_FRONT_DOMAINS = frozenset(front.domain for front in CDN_FRONTS)


def _is_ipv4(value: str) -> bool:
    try:
        IPv4Address(str(value))
    except ValueError:
        return False
    return True


def _relay_route(dc: int, is_media: bool, health: RouteHealth) -> Route | None:
    addresses = relay_addresses(dc)
    if not addresses:
        return None
    domain = relay_domain(dc, is_media=is_media)
    if health.is_suppressed(domain):
        return None
    # Один релей в плане: за ним сразу идут фронты, и гонка фронта с
    # недоказанным релеем остаётся в силе. Отказавший адрес уступает место
    # другому адресу DC в следующих соединениях (health.relay_address_order).
    for address in health.relay_address_order(addresses):
        addr_key = address_key(address)
        if health.is_suppressed(addr_key):
            continue
        return Route(
            kind=KIND_RELAY,
            connect_host=address,
            sni=domain,
            dc_field=DC_FIELD_RANDOM,
            health_key=domain,
            address_health_key=addr_key,
            proof_bytes=RELAY_PROOF_BYTES,
            pool_key=f"relay:{domain}@{address}",
        )
    return Route(
        kind=KIND_RELAY,
        connect_host=domain,
        sni=domain,
        dc_field=DC_FIELD_RANDOM,
        health_key=domain,
        proof_bytes=RELAY_PROOF_BYTES,
        pool_key=f"relay:{domain}@dns",
    )


def _user_domain_routes(dc: int, config: CloudflareFallbackConfig, health: RouteHealth) -> list[Route]:
    if not config.enabled:
        return []
    routes: list[Route] = []
    for domain in config.domains:
        if domain in _FRONT_DOMAINS:
            continue
        host = f"kws{int(dc)}.{domain}"
        key = f"user-cf-{domain}"
        if health.is_suppressed(key):
            continue
        routes.append(
            Route(
                kind=KIND_USER_DOMAIN,
                connect_host=host,
                sni=host,
                dc_field=DC_FIELD_PLUS,
                health_key=key,
                proof_bytes=FRONT_PROOF_BYTES,
            )
        )
    return routes


def _worker_path(target_ip: str, dc: int) -> str:
    return WSS_PATH + "?" + urlencode({"dst": target_ip, "dc": str(int(dc))})


def _user_worker_routes(dc: int, target_ip: str, config: CloudflareFallbackConfig, health: RouteHealth) -> list[Route]:
    if not (config.worker_enabled and target_ip):
        return []
    routes: list[Route] = []
    for domain in config.worker_domains:
        key = f"user-worker-{domain}"
        if health.is_suppressed(key):
            continue
        routes.append(
            Route(
                kind=KIND_USER_WORKER,
                connect_host=domain,
                sni=domain,
                path=_worker_path(target_ip, dc),
                dc_field=DC_FIELD_SIGNED,
                health_key=key,
                proof_bytes=TUNNEL_PROOF_BYTES,
            )
        )
    return routes


def _front_routes(dc: int, health: RouteHealth, *, ignore_suppression: bool = False) -> list[Route]:
    if int(dc) not in CDN_FRONT_DCS or not CDN_FRONTS:
        return []
    key = cdn_key(dc)
    if not ignore_suppression and health.is_suppressed(key):
        return []
    routes: list[Route] = []
    for index, family in health.next_front_slots(FRONTS_PER_CONNECTION):
        front = CDN_FRONTS[index]
        host = front.host_for(dc)
        routes.append(
            Route(
                kind=KIND_FRONT,
                connect_host=front.addresses[family],
                sni=host,
                dc_field=DC_FIELD_PLUS,
                health_key=key,
                health_threshold=CDN_FAILURES_BEFORE_SUPPRESS,
                proof_bytes=FRONT_PROOF_BYTES,
                pool_key=f"front:kws{int(dc)}",
                front_index=index,
                front_family=family,
            )
        )
    return routes


def _tunnel_routes(dc: int, target_ip: str, health: RouteHealth) -> list[Route]:
    if not target_ip or not TUNNEL_HOSTS:
        return []
    routes: list[Route] = []
    for step in range(len(TUNNEL_HOSTS)):
        host = TUNNEL_HOSTS[(health.tunnel_start + step) % len(TUNNEL_HOSTS)]
        if health.is_suppressed(host):
            continue
        routes.append(
            Route(
                kind=KIND_TUNNEL,
                connect_host=host,
                sni=host,
                path=_worker_path(target_ip, dc),
                dc_field=DC_FIELD_SIGNED,
                health_key=host,
                proof_bytes=TUNNEL_PROOF_BYTES,
            )
        )
        if len(routes) >= TUNNELS_PER_CONNECTION:
            break
    return routes


def _tcp_route(kind: str, target_host: str, target_port: int) -> Route:
    return Route(
        kind=kind,
        dc_field=DC_FIELD_SIGNED,
        target_host=target_host,
        target_port=int(target_port or 443),
    )


def _cluster(kind: str, is_media: bool) -> str:
    if is_media and kind not in _REGULAR_CLUSTER_KINDS:
        return CLUSTER_MEDIA
    return CLUSTER_REGULAR


def _home_cluster_first(plan: list[Route], cluster: str) -> list[Route]:
    """Основные пути кластера ключа — раньше основных путей другого; запасные на месте."""
    primaries = sorted((route for route in plan if route.is_primary), key=lambda route: route.cluster != cluster)
    ordered = iter(primaries)
    return [next(ordered) if route.is_primary else route for route in plan]


def build_plan(request: PlanInput, health: RouteHealth) -> list[Route]:
    plan = [replace(route, cluster=_cluster(route.kind, request.is_media)) for route in _build_plan(request, health)]
    if request.is_media and request.key_cluster:
        plan = _home_cluster_first(plan, request.key_cluster)
    return plan


def _build_plan(request: PlanInput, health: RouteHealth) -> list[Route]:
    dc = int(request.dc)
    is_media = bool(request.is_media)
    upstream = request.upstream
    target_ip = request.target_host if _is_ipv4(request.target_host) else ""

    upstream_route = _tcp_route(KIND_UPSTREAM, request.target_host, request.target_port)
    if should_route_upstream(upstream, mode="always"):
        return [upstream_route]

    plan: list[Route] = []
    if dc in CDN_FRONT_DCS:
        relay = _relay_route(dc, is_media, health)
        if relay is not None:
            plan.append(relay)
        plan.extend(_user_domain_routes(dc, request.cloudflare, health))
        plan.extend(_user_worker_routes(dc, target_ip, request.cloudflare, health))
        plan.extend(_front_routes(dc, health))
        plan.extend(_tunnel_routes(dc, target_ip, health))
    elif dc == 203:
        plan.extend(_user_worker_routes(dc, target_ip, request.cloudflare, health))
        plan.extend(_tunnel_routes(dc, target_ip, health))

    all_wss_suppressed = not plan and dc in CDN_FRONT_DCS
    if upstream.enabled:
        plan.append(upstream_route)
    if all_wss_suppressed:
        plan.extend(_front_routes(dc, health, ignore_suppression=True))
    if not upstream.enabled:
        plan.append(_tcp_route(KIND_DIRECT, request.target_host, request.target_port))
    return plan


__all__ = [
    "FRONTS_PER_CONNECTION",
    "KIND_DIRECT",
    "KIND_FRONT",
    "KIND_RELAY",
    "KIND_TUNNEL",
    "KIND_UPSTREAM",
    "KIND_USER_DOMAIN",
    "KIND_USER_WORKER",
    "PRIMARY_KINDS",
    "PlanInput",
    "Route",
    "TUNNELS_PER_CONNECTION",
    "build_plan",
]
