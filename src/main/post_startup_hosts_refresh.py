from __future__ import annotations

from log.log import log
from main.post_startup_gate import bind_startup_gate, is_startup_host_alive
from main.post_startup_threading import enqueue_subsystem_task, schedule_after

# Адреса включённых сервисов перепроверяются, пока программа открыта: она живёт в трее сутками,
# а адрес может перестать отвечать в любой день.
HOSTS_ADDRESS_CHECK_INTERVAL_MS = 6 * 3600 * 1000


def install_hosts_applied_selection_refresh(
    startup_host,
    *,
    hosts_feature=None,
    log_startup_metric,
) -> None:
    """Сразу после запуска (и дальше раз в шесть часов) обновляет адреса в блоке ZapretGUI в hosts.

    Два шага. Сначала умный движок проверяет адреса включённых сервисов — это
    ожидание сети, поэтому оно идёт в своей очереди и остальным задачам запуска
    не мешает. Затем блок в hosts переписывается, если проверенные адреса или
    новый каталог отличаются от записанного: пользователю не нужно заново
    нажимать «применить».
    """

    def _run_hosts_applied_selection_refresh() -> None:
        if not is_startup_host_alive(startup_host) or hosts_feature is None:
            return
        try:
            result = hosts_feature.refresh_applied_selection()
        except Exception as exc:
            log(f"Обновление адресов hosts при запуске не выполнено: {exc}", "WARNING")
            return
        changed = bool(getattr(result, "changed", False))
        # Причина нужна в журнале поддержки: по «changed=False» не понять, почему старые адреса остались.
        reason = str(getattr(result, "message", "") or "причина не названа")
        log_startup_metric("StartupHostsAppliedSelectionRefreshFinished", f"changed={changed} ({reason})")

    def _enqueue_refresh() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task(
            "hosts",
            "HostsAppliedSelectionRefresh",
            _run_hosts_applied_selection_refresh,
        )

    def _run_hosts_address_check() -> None:
        try:
            if is_startup_host_alive(startup_host) and hosts_feature is not None:
                report = hosts_feature.check_addresses()
                log_startup_metric(
                    "StartupHostsAddressCheckFinished",
                    f"checked={getattr(report, 'checked', 0)} changed={bool(getattr(report, 'changed', False))}",
                )
        except Exception as exc:
            log(f"Проверка адресов hosts не выполнена: {exc}", "WARNING")
        finally:
            # Запись в hosts — на общей дорожке: она не должна идти одновременно с другими задачами диска.
            _enqueue_refresh()

    def _start_hosts_applied_selection_refresh() -> None:
        if not is_startup_host_alive(startup_host):
            return
        enqueue_subsystem_task("dns", "HostsAddressCheck", _run_hosts_address_check)

    def _on_periodic_tick() -> None:
        if not is_startup_host_alive(startup_host):
            return
        _start_hosts_applied_selection_refresh()
        schedule_after(HOSTS_ADDRESS_CHECK_INTERVAL_MS, _on_periodic_tick)

    def _start_and_schedule() -> None:
        _start_hosts_applied_selection_refresh()
        schedule_after(HOSTS_ADDRESS_CHECK_INTERVAL_MS, _on_periodic_tick)

    bind_startup_gate(
        startup_host.startup_interactive_ready,
        _start_and_schedule,
        is_ready=lambda: bool(startup_host.startup_state.interactive_logged),
    )


__all__ = ["install_hosts_applied_selection_refresh"]
