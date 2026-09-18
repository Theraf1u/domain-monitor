"""Keyboards for the "📡 Ноды" section: list, bulk actions, card, and the
notification-destination picker.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Node
from app.telegram.keyboards.common import NOTIFY_DEST_LABELS, _toggle_label, _toggle_style, confirm_keyboard
from app.telegram.pagination import add_pagination_row

NODES_PAGE_SIZE = 10

NODE_FILTER_LABELS = {
    "all": "Все",
    "online": "🟢 Online",
    "paused": "🔵 На паузе",
    "offline": "🔴 Offline",
    "revoked": "⚫ Отозваны",
    "full_buffer": "📦 Буфер заполнен",
}


def nodes_list(
    nodes: list[Node], online_ids: set[int], fleet_monitoring_enabled: bool,
    filter_key: str, has_search: bool, page: int, total_filtered: int,
) -> InlineKeyboardMarkup:
    """Node status is shown by button COLOR, not an emoji dot: green =
    agent online and actively monitoring, red = revoked or not
    responding, blue = online but paused (either this node's own
    monitoring toggle or the fleet-wide one is off). The legend for
    this lives in the screen's text (see handlers._nodes_legend), since
    Telegram gives us only three button colors to work with.

    `nodes` is already the current page's slice - this function only
    renders, it doesn't filter/paginate (that's handlers._build_nodes_screen,
    kept out of the presentation layer)."""
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить", callback_data="node_add", style="primary")
    b.button(text="🔎 Поиск", callback_data="nodes_search")
    filter_label = "⚙️ Фильтр: " + NODE_FILTER_LABELS.get(filter_key, filter_key)
    b.button(text=filter_label, callback_data="nodes_filter_menu")
    b.button(text="🧰 Массовые действия", callback_data="nodes_bulk")
    rows = [1, 2, 1]
    if has_search:
        b.button(text="✖️ Сбросить поиск", callback_data="nodes_search_clear")
        rows.append(1)

    for node in nodes:
        if node.status == "revoked":
            label, style = f"{node.name} (отозвана)", "danger"
        elif node.id not in online_ids:
            label, style = f"{node.name} (не отвечает)", "danger"
        elif not (node.monitoring_enabled and fleet_monitoring_enabled):
            label, style = f"{node.name} (на паузе)", "primary"
        else:
            label, style = node.name, "success"
        b.button(text=label, callback_data=f"node:{node.id}", style=style)
    rows.extend([1] * len(nodes))

    if total_filtered > NODES_PAGE_SIZE:
        nav_count = add_pagination_row(b, page, NODES_PAGE_SIZE, total_filtered, "nodes_page")
        rows.append(nav_count)

    b.button(text="⬅️ Назад", callback_data="main")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


BULK_ACTION_LABELS = {
    "mon_on": "▶️ Включить мониторинг",
    "mon_off": "⏸ Выключить мониторинг",
    "send_on": "📤 Включить отправку",
    "send_off": "📤 Выключить отправку",
    "notif_on": "🔔 Включить уведомления",
    "notif_off": "🔕 Выключить уведомления",
}


def nodes_bulk_menu(nodes: list[Node], selected_ids: set[int]) -> InlineKeyboardMarkup:
    """Telegram has no real checkboxes - a ✅/⬜ prefix on the button's
    own text is the whole affordance. Selection itself lives in FSM
    state (handlers._bulk_state), not encoded in callback_data."""
    b = InlineKeyboardBuilder()
    b.button(text="🟡 Устаревшие агенты", callback_data="nodes_bulk_select_outdated")
    b.button(text="⚠️ Проблемные ноды", callback_data="nodes_bulk_select_problem")
    b.button(text="☑️ Выбрать все", callback_data="nodes_bulk_select_all")
    b.button(text="⬜ Снять выбор", callback_data="nodes_bulk_select_none")
    rows = [2, 2]
    for node in nodes:
        prefix = "✅ " if node.id in selected_ids else "⬜ "
        b.button(text=f"{prefix}{node.name}", callback_data=f"nodes_bulk_toggle:{node.id}")
    rows.extend([1] * len(nodes))
    for action, label in BULK_ACTION_LABELS.items():
        b.button(text=label, callback_data=f"nodes_bulk_action:{action}", style="primary")
    rows.extend([2, 2, 2])
    b.button(text="⬅️ Назад", callback_data="nodes")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def confirm_bulk_action(action: str, count: int) -> InlineKeyboardMarkup:
    return confirm_keyboard(f"✅ Да, применить к {count} нод.", f"nodes_bulk_confirm:{action}", "nodes_bulk")


def nodes_filter_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in NODE_FILTER_LABELS.items():
        prefix = "✅ " if key == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"nodes_filter_set:{key}")
    b.button(text="⬅️ Назад", callback_data="nodes")
    b.adjust(1)
    return b.as_markup()


def node_card(node: Node) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    mon_label = _toggle_label("Мониторинг включён", "Мониторинг выключен", node.monitoring_enabled)
    sending_label = _toggle_label("Отправка включена", "Отправка выключена", node.sending_enabled)
    notif_label = _toggle_label("Уведомления включены", "Уведомления выключены", node.notifications_enabled)
    b.button(text=mon_label, callback_data=f"node_toggle_mon:{node.id}", style=_toggle_style(node.monitoring_enabled))
    b.button(
        text=sending_label, callback_data=f"node_toggle_sending:{node.id}", style=_toggle_style(node.sending_enabled),
    )
    b.button(
        text=notif_label, callback_data=f"node_toggle_notif:{node.id}", style=_toggle_style(node.notifications_enabled),
    )
    dest_label = (
        "⬜ Как по умолчанию" if node.notify_destination == "inherit"
        else NOTIFY_DEST_LABELS.get(node.notify_destination, node.notify_destination)
    )
    b.button(text=f"📍 Куда слать: {dest_label}", callback_data=f"node_notify_dest:{node.id}", style="primary")
    b.button(text="🧪 Проверить", callback_data=f"node_check:{node.id}", style="primary")
    b.button(text="📊 Статистика ноды", callback_data=f"stats_node:{node.id}:today", style="primary")
    b.button(text="✏️ Переименовать", callback_data=f"node_rename:{node.id}", style="primary")
    if node.status == "active":
        b.button(text="🔑 Обновить токен", callback_data=f"node_regen:{node.id}", style="primary")
        b.button(text="⛔ Отозвать", callback_data=f"node_revoke:{node.id}", style="danger")
    b.button(text="🗑 Удалить", callback_data=f"node_delete:{node.id}", style="danger")
    b.button(text="⬅️ К списку нод", callback_data="nodes")
    b.adjust(1)
    return b.as_markup()


def node_notify_dest_menu(node: Node) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    options = {"inherit": "⬜ Как по умолчанию (см. Уведомления → Получатели)", **NOTIFY_DEST_LABELS}
    for value, label in options.items():
        prefix = "✅ " if value == node.notify_destination else ""
        b.button(text=f"{prefix}{label}", callback_data=f"node_notify_dest_set:{node.id}:{value}")
    group_label = "🔗 Привязать группу/топик" if node.notify_group_chat_id is None else "🔗 Перепривязать группу/топик"
    b.button(text=group_label, callback_data=f"node_notify_bind:{node.id}", style="primary")
    b.button(text="⬅️ Назад", callback_data=f"node:{node.id}")
    b.adjust(1)
    return b.as_markup()


def confirm_delete_node(node_id: int) -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, удалить", f"node_delete_confirm:{node_id}", f"node:{node_id}")
