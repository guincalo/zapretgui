"""Чистые функции «Подбора стратегии», которые страница получает через фасад BlockCheck.

Здесь только то, что фасад действительно отдаёт странице; остальное
берётся прямыми импортами из модулей ``blockcheck.strategy_scan_*``.
"""
from __future__ import annotations

from blockcheck.strategy_scan_apply import apply_strategy
from blockcheck.strategy_scan_targeting import build_selection_state
from blockcheck.strategy_scan_page_plans import (
    build_apply_error_plan,
    build_apply_success_plan,
    build_idle_interaction_plan,
    build_language_plan,
    build_progress_plan,
    build_protocol_ui_plan,
    build_quick_target_menu_plan,
    build_result_presentation,
    build_running_interaction_plan,
    build_support_context,
    build_support_error_plan,
    build_support_success_plan,
    build_udp_scope_hint_plan,
    count_resumable_strategies,
    finalize_scan_report,
    plan_scan_start,
)

__all__ = [
    "apply_strategy",
    "build_apply_error_plan",
    "build_apply_success_plan",
    "build_idle_interaction_plan",
    "build_language_plan",
    "build_progress_plan",
    "build_protocol_ui_plan",
    "build_quick_target_menu_plan",
    "build_result_presentation",
    "build_running_interaction_plan",
    "build_selection_state",
    "build_support_context",
    "build_support_error_plan",
    "build_support_success_plan",
    "build_udp_scope_hint_plan",
    "count_resumable_strategies",
    "finalize_scan_report",
    "plan_scan_start",
]
