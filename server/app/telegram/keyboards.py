"""All inline keyboards. Callback data is a short `prefix:arg` string kept
under Telegram's 64-byte limit; node/domain identity in callbacks is always
a numeric id, never a raw name/domain, so nothing user-influenced ends up
interpolated back into a query.

Buttons are colored with the `style` field added in Bot API 9.4 (only
'danger'/'success'/'primary' are valid - anything else is rejected by
Telegram, so don't invent other values). Color always follows the ACTION
the button performs, not the current state: a button offering to stop
something is danger even while monitoring is happily running, and
"confirm delete" is danger even though it's the affirmative answer -
green on a destructive confirm would say the opposite of what it means.
Purely navigational buttons (Назад/Отмена) are left unstyled."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Node


def main_menu(monitoring_enabled: bool = True, sending_enabled: bool = True) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📡 Ноды", callback_data="nodes")
    b.button(text="🌐 Домены", callback_data="domains")
    b.button(text="📊 Статистика", callback_data="stats")
    b.button(text="🔔 Уведомления", callback_data="notify_menu")
    b.button(text="🔍 Фильтры", callback_data="filters")
    b.button(text="⚙️ Настройки", callback_data="settings")
    mon_label = "⏸ Остановить мониторинг" if monitoring_enabled else "▶ Возобновить мониторинг"
    send_label = "⏸ Остановить отправку доменов" if sending_enabled else "▶ Возобновить отправку доменов"
    b.button(text=mon_label, callback_data="fleet_toggle_monitoring", style="danger" if monitoring_enabled else "success")
    b.button(text=send_label, callback_data="fleet_toggle_sending", style="danger" if sending_enabled else "success")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def back_button(target: str = "main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data=target)
    return b.as_markup()


def nodes_list(nodes: list[Node], online_ids: set[int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for node in nodes:
        dot = "🟢" if node.id in online_ids else "🔴"
        label = f"{dot} {node.name}"
        if node.status == "revoked":
            label = f"⛔ {node.name}"
        b.button(text=label, callback_data=f"node:{node.id}")
    b.button(text="➕ Добавить", callback_data="node_add", style="success")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1)
    return b.as_markup()


def node_card(node: Node) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    mon_label = "⏸ Мониторинг (вкл)" if node.monitoring_enabled else "▶ Мониторинг (выкл)"
    notif_label = "🔔 Уведомления (вкл)" if node.notifications_enabled else "🔕 Уведомления (выкл)"
    b.button(
        text=mon_label, callback_data=f"node_toggle_mon:{node.id}",
        style="danger" if node.monitoring_enabled else "success",
    )
    b.button(
        text=notif_label, callback_data=f"node_toggle_notif:{node.id}",
        style="danger" if node.notifications_enabled else "success",
    )
    if node.status == "active":
        b.button(text="🔑 Обновить токен", callback_data=f"node_regen:{node.id}", style="primary")
        b.button(text="⛔ Отозвать", callback_data=f"node_revoke:{node.id}", style="danger")
    b.button(text="🗑 Удалить", callback_data=f"node_delete:{node.id}", style="danger")
    b.button(text="⬅️ К списку нод", callback_data="nodes")
    b.adjust(1)
    return b.as_markup()


def confirm_delete_node(node_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, удалить", callback_data=f"node_delete_confirm:{node_id}", style="danger")
    b.button(text="❌ Отмена", callback_data=f"node:{node_id}", style="success")
    b.adjust(1)
    return b.as_markup()


def domains_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🕐 Последние", callback_data="domains_recent")
    b.button(text="🔝 Топ по хитам", callback_data="domains_top")
    b.button(text="📤 Экспорт .txt", callback_data="domains_export", style="primary")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(2, 1, 1)
    return b.as_markup()


def notify_menu(current_mode: str, global_enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle = "🔕 Выключить всё" if global_enabled else "🔔 Включить всё"
    b.button(text=toggle, callback_data="notify_toggle_global", style="danger" if global_enabled else "success")
    modes = [("instant", "Instant"), ("5", "Batch 5s"), ("15", "Batch 15s"), ("30", "Batch 30s"), ("60", "Batch 60s")]
    for value, label in modes:
        prefix = "✅ " if value == current_mode else ""
        b.button(text=f"{prefix}{label}", callback_data=f"notify_mode:{value}")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 2, 2, 1, 1)
    return b.as_markup()


def settings_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data="main")
    return b.as_markup()


def cancel_input() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="main")
    return b.as_markup()


def filters_menu(counts: dict[str, int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🚨 Watch ({counts.get('watch', 0)})", callback_data="filters_list:watch")
    b.button(text=f"🚫 Ignore ({counts.get('ignore', 0)})", callback_data="filters_list:ignore")
    b.button(text=f"✅ Allow ({counts.get('allow', 0)})", callback_data="filters_list:allow")
    b.button(text="➕ Добавить правило", callback_data="filter_add", style="success")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1)
    return b.as_markup()


def filters_list(list_type: str, rules: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for r in rules[:30]:
        b.button(text=f"🗑 {r.pattern}", callback_data=f"filter_remove:{r.id}", style="danger")
    b.button(text="⬅️ Назад", callback_data="filters")
    b.adjust(1)
    return b.as_markup()


def filter_add_list_type() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚨 Watch", callback_data="filter_add_type:watch")
    b.button(text="🚫 Ignore", callback_data="filter_add_type:ignore")
    b.button(text="✅ Allow", callback_data="filter_add_type:allow")
    b.button(text="❌ Отмена", callback_data="filters")
    b.adjust(3, 1)
    return b.as_markup()


def filter_add_pattern_type(list_type: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Suffix", callback_data=f"filter_add_ptype:{list_type}:suffix")
    b.button(text="Exact", callback_data=f"filter_add_ptype:{list_type}:exact")
    b.button(text="Wildcard", callback_data=f"filter_add_ptype:{list_type}:wildcard")
    b.button(text="❌ Отмена", callback_data="filters")
    b.adjust(3, 1)
    return b.as_markup()


def domain_notification(domain_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚫 Игнорировать", callback_data=f"domain_ignore:{domain_id}", style="danger")
    b.button(text="📋 Копировать", callback_data=f"domain_copy:{domain_id}", style="primary")
    b.adjust(2)
    return b.as_markup()
