"""Файл настроек dnscrypt-proxy для выбранного режима шифрованного DNS.

Файл — единственное место, где записано, в каком режиме работает движок:
первая строка «# zapret-mode: <режим>». Программа читает её обратно
(read_mode), а не хранит режим отдельно, поэтому показанное на странице
всегда совпадает с тем, что на самом деле запущено. У режима DoH вторая
строка называет плитку, чей сервер запущен: «# zapret-server: <название>»
(read_server), а сами серверы читаются обратно из разделов [static]
(read_upstreams) — этого хватает, чтобы поднять службу заново.

Серверы перечислены в файле целиком, штампами из dns.local_proxy_catalog:
списки из сети движок не скачивает.
"""

from __future__ import annotations

from dns import local_proxy_catalog as catalog

MODE_MARKER = "# zapret-mode: "
SERVER_MARKER = "# zapret-server: "
LISTEN_IPV4 = "127.0.0.1"
LISTEN_IPV6 = "::1"


def _text(value: str) -> str:
    """Строка TOML. Путь Windows пишется как есть, в одинарных кавычках."""
    value = str(value)
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _list(values) -> str:
    return "[" + ", ".join(_text(item) for item in values) + "]"


def build_config(
    mode: str,
    *,
    log_path: str,
    listen_ipv6: bool,
    upstreams: tuple[catalog.Upstream, ...] = (),
    server_name: str = "",
) -> str:
    """Текст dnscrypt-proxy.toml: слушать 127.0.0.1:53 (и [::1]:53), серверы режима.

    upstreams и server_name нужны только режиму DoH: серверы плитки и её название.
    """
    servers = catalog.servers(mode, upstreams)
    relays = catalog.relays(mode)
    listen = [f"{LISTEN_IPV4}:53"]
    if listen_ipv6:
        listen.append(f"[{LISTEN_IPV6}]:53")
    odoh = mode == catalog.MODE_ODOH
    doh = mode == catalog.MODE_DOH
    lines = [
        f"{MODE_MARKER}{mode}",
        *([f"{SERVER_MARKER}{' '.join(str(server_name).split())}"] if doh else []),
        "# Файл пишет ZapretGUI при выборе шифрованного DNS; правки руками будут затёрты.",
        "",
        f"listen_addresses = {_list(listen)}",
        f"server_names = {_list(item.name for item in servers)}",
        "ipv4_servers = true",
        "ipv6_servers = false",
        f"dnscrypt_servers = {'false' if odoh or doh else 'true'}",
        f"doh_servers = {'true' if doh else 'false'}",
        f"odoh_servers = {'true' if odoh else 'false'}",
        # Свои запросы движок не отдаёт DNS системы: там теперь стоит он сам.
        "ignore_system_dns = true",
        f"bootstrap_resolvers = {_list(catalog.BOOTSTRAP_RESOLVERS)}",
        "netprobe_timeout = 60",
        "netprobe_address = '9.9.9.9:53'",
        "timeout = 5000",
        "keepalive = 30",
        "cache = true",
        "log_level = 2",
        f"log_file = {_text(log_path)}",
        "log_files_max_size = 2",
        "log_files_max_age = 7",
        "log_files_max_backups = 1",
    ]
    routes = catalog.routes(mode)
    if routes:
        lines += ["", "[anonymized_dns]", "routes = ["]
        lines += [f"  {{ server_name = {_text(server)}, via = {_list(via)} }}," for server, via in routes]
        # Сервер, который через посредника не отвечает, пропускается, а не идёт напрямую.
        lines += ["]", "skip_incompatible = true"]
    for item in (*servers, *relays):
        lines += ["", f"[static.{_text(item.name)}]", f"stamp = {_text(item.stamp)}"]
    return "\n".join(lines) + "\n"


def read_server(config_text: str) -> str:
    """Название плитки, чей сервер запущен в режиме DoH; у остальных режимов — пусто."""
    if read_mode(config_text) != catalog.MODE_DOH:
        return ""
    for line in str(config_text or "").split("\n")[1:4]:
        if line.startswith(SERVER_MARKER):
            return line[len(SERVER_MARKER):].strip()
    return ""


def read_upstreams(config_text: str) -> tuple[catalog.Upstream, ...]:
    """Серверы из разделов [static] файла настроек — чтобы запустить тот же режим DoH заново."""
    import re

    found = re.findall(r"^\[static\.'([^']+)'\]\s*\nstamp = '([^']+)'", str(config_text or ""), flags=re.MULTILINE)
    return tuple(catalog.Upstream(name, "", stamp) for name, stamp in found)


def read_mode(config_text: str) -> str:
    """Режим из первой строки файла настроек или пустая строка."""
    first = str(config_text or "").lstrip("\ufeff").split("\n", 1)[0].strip()
    if not first.startswith(MODE_MARKER):
        return ""
    mode = first[len(MODE_MARKER):].strip()
    return mode if mode in catalog.MODES else ""


__all__ = [
    "LISTEN_IPV4",
    "LISTEN_IPV6",
    "MODE_MARKER",
    "SERVER_MARKER",
    "build_config",
    "read_mode",
    "read_server",
    "read_upstreams",
]
