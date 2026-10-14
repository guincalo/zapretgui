"""Build-helper секции Premium страницы."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon, LineEdit, PrimaryPushButton, PushButton

from donater.ui.accessibility import (
    apply_premium_button_accessibility,
    apply_premium_instructions_accessibility,
    apply_premium_pair_code_accessibility,
)
from ui.accessibility import set_state_text
from ui.fluent_widgets import SettingsCard, RefreshButton, QuickActionsBar, set_tooltip


PREMIUM_ABOUT_DEFAULT = (
    "Zapret Premium открывается любой подпиской в Telegram-боте @zapretvpns_bot — "
    "подойдёт даже самый недорогой тариф «Запретик». Код ниже — не ключ и не оплата: "
    "он один раз связывает этот компьютер с вашим Telegram-аккаунтом, и дальше программа "
    "сама проверяет, активна ли подписка. VPN — отдельная часть тарифов бота и в этой "
    "программе не настраивается."
)
PREMIUM_INSTRUCTIONS_DEFAULT = (
    "1. Купите любой тариф в боте @zapretvpns_bot (цены — в боте)\n"
    "2. Нажмите «Создать код» — код действует 10 минут\n"
    "3. Отправьте код боту сообщением в Telegram\n"
    "4. Вернитесь сюда — статус обновится сам"
)


def premium_about_text(tr: Callable[[str, str], str]) -> str:
    return tr("page.premium.about", PREMIUM_ABOUT_DEFAULT)


def premium_instructions_text(tr: Callable[[str, str], str]) -> str:
    return tr("page.premium.instructions", PREMIUM_INSTRUCTIONS_DEFAULT)


@dataclass(slots=True)
class PremiumActivationWidgets:
    card: SettingsCard
    about_label: BodyLabel
    instructions_label: BodyLabel
    key_input_container: QWidget
    key_input: LineEdit
    activate_btn: PrimaryPushButton
    activation_status: CaptionLabel


@dataclass(slots=True)
class PremiumDeviceInfoWidgets:
    card: SettingsCard
    device_id_label: CaptionLabel
    saved_key_label: CaptionLabel
    last_check_label: CaptionLabel
    server_status_label: CaptionLabel
    open_guide_btn: PushButton
    open_bot_btn: PushButton


@dataclass(slots=True)
class PremiumActionsWidgets:
    actions_bar: QuickActionsBar
    refresh_btn: RefreshButton
    change_key_btn: PushButton
    test_btn: PushButton
    extend_btn: PrimaryPushButton


def build_premium_activation_section(
    *,
    tr: Callable[[str, str], str],
    on_create_pair_code: Callable[[], None],
) -> PremiumActivationWidgets:
    card = SettingsCard()

    about_label = BodyLabel(premium_about_text(tr))
    about_label.setWordWrap(True)
    card.add_widget(about_label)

    instructions_label = BodyLabel(premium_instructions_text(tr))
    instructions_label.setWordWrap(True)
    apply_premium_instructions_accessibility(tr_fn=tr, instructions_label=instructions_label)
    card.add_widget(instructions_label)

    key_input_container = QWidget()
    key_v = QVBoxLayout(key_input_container)
    key_v.setContentsMargins(0, 0, 0, 0)
    key_v.setSpacing(8)

    key_row = QHBoxLayout()
    key_row.setSpacing(8)

    key_input = LineEdit()
    key_input.setPlaceholderText(
        tr("page.premium.placeholder.pair_code", "ABCD12EF")
    )
    key_input.setReadOnly(True)
    apply_premium_pair_code_accessibility(tr_fn=tr, key_input=key_input)
    key_row.addWidget(key_input, 1)

    activate_btn = PrimaryPushButton(
        tr("page.premium.button.create_code", "Создать код"),
        icon=FluentIcon.LINK,
    )
    apply_premium_button_accessibility(tr_fn=tr, activate_btn=activate_btn)
    activate_btn.clicked.connect(on_create_pair_code)
    key_row.addWidget(activate_btn)

    key_v.addLayout(key_row)

    activation_status = CaptionLabel("")
    activation_status.setWordWrap(True)
    key_v.addWidget(activation_status)

    card.add_widget(key_input_container)

    return PremiumActivationWidgets(
        card=card,
        about_label=about_label,
        instructions_label=instructions_label,
        key_input_container=key_input_container,
        key_input=key_input,
        activate_btn=activate_btn,
        activation_status=activation_status,
    )


def build_premium_device_info_section(
    *,
    tr: Callable[[str, str], str],
    on_open_bot: Callable[[], None],
    on_open_guide: Callable[[], None],
) -> PremiumDeviceInfoWidgets:
    card = SettingsCard()

    device_id_label = CaptionLabel(
        tr("page.premium.label.device_id.loading", "ID устройства: загрузка...")
    )
    set_state_text(device_id_label, tr("page.premium.label.device_id.loading", "ID устройства: загрузка..."))
    saved_key_label = CaptionLabel(
        tr("page.premium.label.binding.none", "Привязка: —")
    )
    set_state_text(saved_key_label, tr("page.premium.binding.accessible_absent", "Привязка: нет"))
    last_check_label = CaptionLabel(
        tr("page.premium.label.last_check.none", "Последняя проверка: —")
    )
    set_state_text(last_check_label, tr("page.premium.last_check.accessible_none", "Последняя проверка Premium: —"))
    server_status_label = CaptionLabel(
        tr("page.premium.label.server.checking", "Сервер: проверка...")
    )
    set_state_text(server_status_label, tr("page.premium.server.accessible_checking", "Статус Premium-сервера: проверка..."))

    labels_layout = QVBoxLayout()
    labels_layout.setSpacing(4)
    labels_layout.setContentsMargins(0, 0, 0, 0)
    labels_layout.addWidget(device_id_label)
    labels_layout.addWidget(saved_key_label)
    labels_layout.addWidget(last_check_label)
    labels_layout.addWidget(server_status_label)

    open_guide_btn = PushButton(
        tr("page.premium.button.open_guide", "Как это работает"),
        icon=FluentIcon.HELP,
    )
    apply_premium_button_accessibility(tr_fn=tr, open_guide_btn=open_guide_btn)
    open_guide_btn.clicked.connect(on_open_guide)

    open_bot_btn = PushButton(
        tr("page.premium.button.open_bot", "Открыть бота"),
        icon=FluentIcon.SEND,
    )
    apply_premium_button_accessibility(tr_fn=tr, open_bot_btn=open_bot_btn)
    open_bot_btn.clicked.connect(on_open_bot)

    row_layout = QHBoxLayout()
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.addLayout(labels_layout)
    row_layout.addStretch(1)
    row_layout.addWidget(open_guide_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    row_layout.addWidget(open_bot_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    card.add_layout(row_layout)

    return PremiumDeviceInfoWidgets(
        card=card,
        device_id_label=device_id_label,
        saved_key_label=saved_key_label,
        last_check_label=last_check_label,
        server_status_label=server_status_label,
        open_guide_btn=open_guide_btn,
        open_bot_btn=open_bot_btn,
    )


def build_premium_actions_section(
    *,
    parent,
    tr: Callable[[str, str], str],
    on_check_status: Callable[[], None],
    on_change_key: Callable[[], None],
    on_test_connection: Callable[[], None],
    on_open_bot: Callable[[], None],
) -> PremiumActionsWidgets:
    refresh_btn = RefreshButton(tr("page.premium.button.refresh_status", "Обновить статус"))
    refresh_btn.clicked.connect(on_check_status)
    set_tooltip(
        refresh_btn,
        tr(
            "page.premium.action.refresh_status.description",
            "Повторно запросить Premium-статус и обновить данные устройства.",
        )
    )
    apply_premium_button_accessibility(tr_fn=tr, refresh_btn=refresh_btn)

    actions_bar = QuickActionsBar(parent)
    actions_bar.add_button(refresh_btn)

    change_key_btn = PushButton(
        tr("page.premium.button.reset_activation", "Сбросить активацию"),
        icon=FluentIcon.SYNC,
    )
    set_tooltip(
        change_key_btn,
        tr(
            "page.premium.action.reset_activation.description",
            "Удаляет токен устройства, офлайн-кэш и код привязки на этом компьютере.",
        )
    )
    apply_premium_button_accessibility(tr_fn=tr, change_key_btn=change_key_btn)
    change_key_btn.clicked.connect(on_change_key)
    actions_bar.add_button(change_key_btn)

    test_btn = PushButton(
        tr("page.premium.button.test_connection", "Проверить соединение"),
        icon=FluentIcon.CONNECT,
    )
    set_tooltip(
        test_btn,
        tr(
            "page.premium.action.test_connection.description",
            "Проверить доступность Premium backend и соединение с сервером.",
        )
    )
    apply_premium_button_accessibility(tr_fn=tr, test_btn=test_btn)
    test_btn.clicked.connect(on_test_connection)
    actions_bar.add_button(test_btn)

    extend_btn = PrimaryPushButton(
        tr("page.premium.button.extend", "Продлить подписку"),
        icon=FluentIcon.SEND,
    )
    set_tooltip(
        extend_btn,
        tr(
            "page.premium.action.extend.description",
            "Открыть Telegram-бота для продления подписки или покупки Premium.",
        )
    )
    apply_premium_button_accessibility(tr_fn=tr, extend_btn=extend_btn)
    extend_btn.clicked.connect(on_open_bot)
    actions_bar.add_button(extend_btn)

    return PremiumActionsWidgets(
        actions_bar=actions_bar,
        refresh_btn=refresh_btn,
        change_key_btn=change_key_btn,
        test_btn=test_btn,
        extend_btn=extend_btn,
    )
