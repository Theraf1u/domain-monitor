"""All inline keyboards. Callback data is a short `prefix:arg` string kept
under Telegram's 64-byte limit; node/domain identity in callbacks is always
a numeric id, never a raw name/domain, so nothing user-influenced ends up
interpolated back into a query.

Buttons are colored with the `style` field added in Bot API 9.4 (only
'danger'/'success'/'primary' are valid - anything else is rejected by
Telegram, so don't invent other values). Two different rules apply
depending on what the button IS:

- A plain on/off toggle (monitoring, sending, notifications, watchlist
  alerts, autobackup) shows its CURRENT STATE, not the action tapping it
  performs: the label reads "Мониторинг включён"/"выключён" and the
  color is green while it's on, red while it's off - like a light
  switch, not a verb. See _toggle_style()/_toggle_label().
- A one-shot destructive action (delete, revoke, "confirm delete") is
  colored by what it DOES regardless of any state: "✅ Да, удалить" is
  danger even though it's the affirmative answer, because green on a
  destructive confirm would say the opposite of what it means.

Purely navigational buttons (Назад/Отмена) are left unstyled."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Domain, FilterRule, Node
from app.telegram.pagination import add_pagination_row


def _toggle_style(enabled: bool) -> str:
    return "success" if enabled else "danger"


def _toggle_label(on_text: str, off_text: str, enabled: bool) -> str:
    return f"🟢 {on_text}" if enabled else f"🔴 {off_text}"

PATTERN_TAG = {"exact": "точно", "suffix": "поддомены", "wildcard": "шаблон"}

EXPORT_PERIODS: list[tuple[str, str]] = [
    ("today", "Сегодня"),
    ("yesterday", "Вчера"),
    ("24h", "24 часа"),
    ("7d", "7 дней"),
    ("30d", "30 дней"),
    ("all", "Весь период"),
]

STATS_PERIODS: list[tuple[str, str]] = [
    ("today", "Сегодня"),
    ("week", "Неделя"),
    ("month", "Месяц"),
    ("all", "Всё время"),
]

# Unified period set (spec section 6.2/7): includes 1h/6h that the older
# EXPORT_PERIODS/STATS_PERIODS constants above don't cover. Used by the new
# domains list filter; existing screens keep their own constants unchanged
# rather than being silently reflowed onto a different button layout.
DOMAIN_PERIODS: list[tuple[str, str]] = [
    ("1h", "1 час"),
    ("6h", "6 часов"),
    ("today", "Сегодня"),
    ("yesterday", "Вчера"),
    ("24h", "24 часа"),
    ("7d", "7 дней"),
    ("30d", "30 дней"),
    ("all", "Всё время"),
]


def main_menu(monitoring_enabled: bool = True, sending_enabled: bool = True) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📡 Ноды", callback_data="nodes")
    b.button(text="🌐 Домены", callback_data="domains")
    b.button(text="📊 Статистика", callback_data="stats")
    b.button(text="🔔 Уведомления", callback_data="notify_menu")
    b.button(text="🔍 Фильтры", callback_data="filters")
    b.button(text="⚙️ Настройки", callback_data="settings")
    b.button(text="💾 Бэкапы", callback_data="backups")
    mon_label = _toggle_label("Мониторинг включён", "Мониторинг выключен", monitoring_enabled)
    send_label = _toggle_label("Отправка доменов включена", "Отправка доменов выключена", sending_enabled)
    b.button(text=mon_label, callback_data="fleet_toggle_monitoring", style=_toggle_style(monitoring_enabled))
    b.button(text=send_label, callback_data="fleet_toggle_sending", style=_toggle_style(sending_enabled))
    b.adjust(2, 2, 2, 1, 1, 1)
    return b.as_markup()


def _add_live_controls(b: InlineKeyboardBuilder, refresh_callback: str, live_key: str, live_active: bool) -> None:
    """Appends a manual "🔄 Обновить" (just re-runs the screen's own
    entry callback) and a "🔁 Автообновление" toggle (background
    periodic re-render via app.live_view) to a screen that shows live
    data - top/recent domains, stats, the backup list."""
    b.button(text="🔄 Обновить", callback_data=refresh_callback)
    live_label = "🔁 Автообновление: ВКЛ" if live_active else "🔁 Автообновление: выкл"
    b.button(text=live_label, callback_data=f"live_toggle:{live_key}", style="danger" if live_active else "success")


def back_button(target: str = "main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data=target)
    return b.as_markup()


def cancel_input(target: str = "main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data=target)
    return b.as_markup()


def confirm_keyboard(
    confirm_text: str, confirm_callback: str, cancel_callback: str, cancel_text: str = "❌ Отмена",
) -> InlineKeyboardMarkup:
    """Shared shape for every destructive-action confirmation screen: one
    danger-styled confirm button, one success-styled cancel button,
    single column. Per the module docstring's two-tier color rule, this
    is a one-shot destructive action, not a toggle - the confirm button
    stays `danger` regardless of what it confirms, and cancel is
    `success` because backing out is the safe choice."""
    b = InlineKeyboardBuilder()
    b.button(text=confirm_text, callback_data=confirm_callback, style="danger")
    b.button(text=cancel_text, callback_data=cancel_callback, style="success")
    b.adjust(1)
    return b.as_markup()


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

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


NOTIFY_DEST_LABELS = {
    "dm": "💬 Только в ЛС",
    "group": "👥 Только в группу",
    "both": "🔀 В ЛС и в группу",
}


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
    dest_label = NOTIFY_DEST_LABELS.get(node.notify_destination, node.notify_destination)
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
    for value, label in NOTIFY_DEST_LABELS.items():
        prefix = "✅ " if value == node.notify_destination else ""
        b.button(text=f"{prefix}{label}", callback_data=f"node_notify_dest_set:{node.id}:{value}")
    group_label = "🔗 Привязать группу/топик" if node.notify_group_chat_id is None else "🔗 Перепривязать группу/топик"
    b.button(text=group_label, callback_data=f"node_notify_bind:{node.id}", style="primary")
    b.button(text="⬅️ Назад", callback_data=f"node:{node.id}")
    b.adjust(1, 1, 1, 1, 1)
    return b.as_markup()


def confirm_delete_node(node_id: int) -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, удалить", f"node_delete_confirm:{node_id}", f"node:{node_id}")


# ------------------------------------------------------------------
# Domains
# ------------------------------------------------------------------

def domains_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🕐 Последние", callback_data="domains_recent")
    b.button(text="🔝 Топ по хитам", callback_data="domains_top")
    b.button(text="📋 Список / поиск / фильтры", callback_data="domains_list", style="primary")
    b.button(text="📤 Экспорт", callback_data="domains_export", style="primary")
    b.button(text="🗑 Управление данными", callback_data="domains_data", style="danger")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(2, 1, 1, 1, 1)
    return b.as_markup()


# ------------------------------------------------------------------
# Domains — search / filter / list / card / history
# ------------------------------------------------------------------

DOMAINS_PAGE_SIZE = 15

DOMAIN_SOURCE_LABELS = {"all": "Все источники", "tls_sni": "TLS SNI", "dns": "DNS"}
DOMAIN_STATUS_LABELS = {
    "all": "Все", "ignore": "🚫 Ignore", "allow": "✅ Allow", "watch": "👁 Watch", "none": "⚪ Без статуса",
}
DOMAIN_MINHITS_LABELS = {"any": "Любое кол-во хитов", "10": "10+", "100": "100+", "1000": "1000+"}


def domains_list_menu(
    domains: list[Domain], page: int, total_filtered: int, has_search: bool, has_filters: bool,
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔎 Поиск", callback_data="domains_search")
    filter_label = "🎛 Фильтры ✓" if has_filters else "🎛 Фильтры"
    b.button(text=filter_label, callback_data="domains_filter_menu")
    b.button(text="📤 Экспорт списка", callback_data="domains_list_export")
    rows = [2, 1]
    if has_search or has_filters:
        b.button(text="✖️ Сбросить всё", callback_data="domains_filter_reset")
        rows.append(1)

    for d in domains:
        b.button(text=f"{d.domain} · {d.hits}", callback_data=f"domain_card:{d.id}:{page}")
    rows.extend([1] * len(domains))

    if total_filtered > DOMAINS_PAGE_SIZE:
        nav_count = add_pagination_row(b, page, DOMAINS_PAGE_SIZE, total_filtered, "domains_list_page")
        rows.append(nav_count)

    b.button(text="⬅️ Назад", callback_data="domains")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def domains_filter_menu(filters: dict) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    node_label = filters.get("node_label") or "Все ноды"
    b.button(text=f"📡 Нода: {node_label}", callback_data="domains_filter_node")
    period_label = dict(DOMAIN_PERIODS).get(filters.get("period", "all"), "Всё время")
    b.button(text=f"🕐 Период: {period_label}", callback_data="domains_filter_period")
    source_label = DOMAIN_SOURCE_LABELS.get(filters.get("source", "all"), "Все источники")
    b.button(text=f"📶 Источник: {source_label}", callback_data="domains_filter_source")
    status_label = DOMAIN_STATUS_LABELS.get(filters.get("status", "all"), "Все")
    b.button(text=f"🏷 Статус: {status_label}", callback_data="domains_filter_status")
    minhits_label = DOMAIN_MINHITS_LABELS.get(filters.get("min_hits", "any"), "Любое кол-во хитов")
    b.button(text=f"🔢 Хиты: {minhits_label}", callback_data="domains_filter_minhits")
    new_only = filters.get("new_only", False)
    b.button(
        text=_toggle_label("Только новые за период", "Только новые за период", new_only),
        callback_data="domains_filter_new_only", style=_toggle_style(new_only) if new_only else "primary",
    )
    b.button(text="✖️ Сбросить фильтры", callback_data="domains_filter_reset", style="danger")
    b.button(text="⬅️ К списку", callback_data="domains_list")
    b.adjust(1, 1, 1, 1, 1, 1, 1, 1)
    return b.as_markup()


def domains_filter_node_menu(nodes: list[Node], current: int | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    prefix = "✅ " if current is None else ""
    b.button(text=f"{prefix}Все ноды", callback_data="domains_filter_node_set:all")
    for n in nodes:
        prefix = "✅ " if n.id == current else ""
        b.button(text=f"{prefix}{n.name}", callback_data=f"domains_filter_node_set:{n.id}")
    b.button(text="⬅️ Назад", callback_data="domains_filter_menu")
    b.adjust(1)
    return b.as_markup()


def domains_filter_period_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in DOMAIN_PERIODS:
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"domains_filter_period_set:{value}")
    b.button(text="⬅️ Назад", callback_data="domains_filter_menu")
    b.adjust(2, 2, 2, 2, 1)
    return b.as_markup()


def domains_filter_source_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in DOMAIN_SOURCE_LABELS.items():
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"domains_filter_source_set:{value}")
    b.button(text="⬅️ Назад", callback_data="domains_filter_menu")
    b.adjust(1)
    return b.as_markup()


def domains_filter_status_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in DOMAIN_STATUS_LABELS.items():
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"domains_filter_status_set:{value}")
    b.button(text="⬅️ Назад", callback_data="domains_filter_menu")
    b.adjust(1)
    return b.as_markup()


def domains_filter_minhits_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in DOMAIN_MINHITS_LABELS.items():
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"domains_filter_minhits_set:{value}")
    b.button(text="⬅️ Назад", callback_data="domains_filter_menu")
    b.adjust(1)
    return b.as_markup()


def domains_list_export_format() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="CSV", callback_data="domains_list_export_do:csv")
    b.button(text="JSON", callback_data="domains_list_export_do:json")
    b.button(text="⬅️ Отмена", callback_data="domains_list")
    b.adjust(2, 1)
    return b.as_markup()


def domain_card_kb(domain_id: int, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👁 Watch", callback_data=f"domain_filter:{domain_id}:watch:{page}")
    b.button(text="✅ Allow", callback_data=f"domain_filter:{domain_id}:allow:{page}")
    b.button(text="🚫 Ignore", callback_data=f"domain_filter:{domain_id}:ignore:{page}")
    b.button(text="📊 История", callback_data=f"domain_history:{domain_id}:0:{page}")
    b.button(text="📋 Копировать", callback_data=f"domain_copy:{domain_id}")
    b.button(text="⬅️ К списку", callback_data=f"domains_list_page:{page}")
    b.adjust(3, 1, 1, 1)
    return b.as_markup()


def domain_filter_pattern_type(domain_id: int, list_type: str, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Suffix", callback_data=f"domain_filter_add:{domain_id}:{list_type}:suffix:{page}")
    b.button(text="Exact", callback_data=f"domain_filter_add:{domain_id}:{list_type}:exact:{page}")
    b.button(text="⬅️ Отмена", callback_data=f"domain_card:{domain_id}:{page}")
    b.adjust(2, 1)
    return b.as_markup()


def domain_history_kb(domain_id: int, page: int, card_page: int, total: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    rows: list[int] = []
    if total > 20:
        nav_count = add_pagination_row(b, page, 20, total, f"domain_history:{domain_id}", suffix=f":{card_page}")
        rows.append(nav_count)
    b.button(text="⬅️ К домену", callback_data=f"domain_card:{domain_id}:{card_page}")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def domains_data_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Удалить события старше...", callback_data="domains_purge_age", style="danger")
    b.button(text="🧹 Очистить историю событий", callback_data="domains_purge_events", style="danger")
    b.button(text="💣 Очистить домены и события", callback_data="data_reset:domains", style="danger")
    b.button(text="⬅️ Назад", callback_data="domains")
    b.adjust(1)
    return b.as_markup()


PURGE_AGE_OPTIONS: list[tuple[int, str]] = [(7, "7 дней"), (30, "30 дней"), (90, "90 дней"), (180, "180 дней")]


def domains_purge_age_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for days, label in PURGE_AGE_OPTIONS:
        b.button(text=f"Старше {label}", callback_data=f"domains_purge_age_confirm:{days}")
    b.button(text="⬅️ Назад", callback_data="domains_data")
    b.adjust(2, 2, 1)
    return b.as_markup()


def confirm_purge_age(days: int) -> InlineKeyboardMarkup:
    return confirm_keyboard(f"✅ Да, удалить события старше {days} дн.", f"domains_purge_age_do:{days}", "domains_data")


def confirm_purge_events() -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, очистить историю событий", "domains_purge_events_do", "domains_data")


def export_period_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in EXPORT_PERIODS:
        b.button(text=label, callback_data=f"domains_export_period:{value}")
    b.button(text="✏️ Свой диапазон", callback_data="domains_export_custom", style="primary")
    b.button(text="⬅️ Назад", callback_data="domains")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def confirm_reset_data(back_target: str = "domains") -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, сбросить всё", f"data_reset_confirm:{back_target}", back_target)


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

def stats_menu(period: str, live_active: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in DOMAIN_PERIODS:
        prefix = "✅ " if value == period else ""
        b.button(text=f"{prefix}{label}", callback_data=f"stats_period:{value}")
    prefix = "✅ " if period == "custom" else ""
    b.button(text=f"{prefix}✏️ Свой диапазон", callback_data="stats_custom", style="primary")
    rows = [2, 2, 2, 2, 1]
    if period == "custom":
        # Live auto-refresh re-renders from a bare period key with no
        # access to the FSM-stored custom range (see cb_live_toggle /
        # _render_live_screen) - it would silently fall back to "all
        # time" instead of the range the admin actually asked for, so
        # it's simplest to not offer it here at all rather than show
        # data quietly different from what was requested.
        b.button(text="🔄 Обновить", callback_data="stats_period:custom")
        rows.append(1)
    else:
        _add_live_controls(b, f"stats_period:{period}", f"stats:{period}", live_active)
        rows.append(2)
    b.button(text="📡 Статистика по ноде", callback_data="stats_node_menu", style="primary")
    b.button(text="🗑 Сброс данных", callback_data="data_reset:stats", style="danger")
    b.button(text="⬅️ Назад", callback_data="main")
    rows.extend([1, 1, 1])
    b.adjust(*rows)
    return b.as_markup()


def stats_node_select_menu(nodes: list[Node]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for n in nodes:
        b.button(text=n.name, callback_data=f"stats_node:{n.id}:today")
    b.button(text="⬅️ Назад", callback_data="stats_period:today")
    b.adjust(1)
    return b.as_markup()


def stats_node_menu(node_id: int, period: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in DOMAIN_PERIODS:
        prefix = "✅ " if value == period else ""
        b.button(text=f"{prefix}{label}", callback_data=f"stats_node:{node_id}:{value}")
    b.button(text="⬅️ К выбору ноды", callback_data="stats_node_menu")
    b.button(text="⬅️ К статистике", callback_data="stats_period:today")
    b.adjust(2, 2, 2, 2, 1, 1)
    return b.as_markup()


def domains_recent_menu(live_active: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _add_live_controls(b, "domains_recent", "domains_recent", live_active)
    b.button(text="⬅️ Назад", callback_data="domains")
    b.adjust(2, 1)
    return b.as_markup()


def domains_top_menu(live_active: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _add_live_controls(b, "domains_top", "domains_top", live_active)
    b.button(text="⬅️ Назад", callback_data="domains")
    b.adjust(2, 1)
    return b.as_markup()


def backup_list_menu(live_active: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _add_live_controls(b, "backup_list", "backup_list", live_active)
    b.button(text="⬅️ Назад", callback_data="backups")
    b.adjust(2, 1)
    return b.as_markup()


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------

_PRESET_MODES = [("instant", "Instant"), ("5", "Batch 5s"), ("15", "Batch 15s"), ("30", "Batch 30s"), ("60", "Batch 60s")]


def notify_menu(current_mode: str, global_enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle = _toggle_label("Уведомления включены", "Уведомления выключены", global_enabled)
    b.button(text=toggle, callback_data="notify_toggle_global", style=_toggle_style(global_enabled))
    for value, label in _PRESET_MODES:
        prefix = "✅ " if value == current_mode else ""
        b.button(text=f"{prefix}{label}", callback_data=f"notify_mode:{value}")
    is_custom = current_mode not in {v for v, _ in _PRESET_MODES}
    custom_label = f"✅ Свой ({current_mode} сек)" if is_custom else "✏️ Свой интервал"
    b.button(text=custom_label, callback_data="notify_custom", style="primary")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 2, 2, 1, 1, 1)
    return b.as_markup()


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def settings_menu(
    retention_days: int, offline_seconds: int, watchlist_enabled: bool, timezone_label: str = "UTC",
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🗓 Хранение событий: {retention_days} дн.", callback_data="settings_retention", style="primary")
    b.button(text=f"⏱ Offline через: {offline_seconds} сек", callback_data="settings_offline", style="primary")
    b.button(text=f"🌍 Часовой пояс: {timezone_label}", callback_data="settings_timezone", style="primary")
    wl_label = _toggle_label("Watch-уведомления включены", "Watch-уведомления выключены", watchlist_enabled)
    b.button(text=wl_label, callback_data="settings_toggle_watchlist", style=_toggle_style(watchlist_enabled))
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1)
    return b.as_markup()


# ------------------------------------------------------------------
# Filters (ignore / allow / watch)
# ------------------------------------------------------------------

def filters_menu(counts: dict[str, int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🚨 Watch ({counts.get('watch', 0)})", callback_data="filters_list:watch:0")
    b.button(text=f"🚫 Ignore ({counts.get('ignore', 0)})", callback_data="filters_list:ignore:0")
    b.button(text=f"✅ Allow ({counts.get('allow', 0)})", callback_data="filters_list:allow:0")
    b.button(text="🔍 Проверить домен", callback_data="filter_check", style="primary")
    b.button(text="➕ Добавить правило", callback_data="filter_add", style="success")
    b.button(text="📥 Массовый импорт", callback_data="filter_import", style="primary")
    b.button(text="📤 Экспорт", callback_data="filter_export", style="primary")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1, 1, 1, 1)
    return b.as_markup()


def filters_list(list_type: str, rules: list[FilterRule], page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    start = page * page_size
    page_rules = rules[start:start + page_size]
    for r in page_rules:
        tag = PATTERN_TAG.get(r.pattern_type, r.pattern_type)
        prefix = "" if r.enabled else "🚫 "
        b.button(
            text=f"{prefix}{r.pattern} ({tag}) · {r.hits_count}",
            callback_data=f"filter_rule:{r.id}:{page}", style=_toggle_style(r.enabled),
        )
    rows = [1] * len(page_rules)

    if len(rules) > page_size:  # only clutter the screen with pagination if there's more than one page
        nav_buttons = add_pagination_row(b, page, page_size, len(rules), f"filters_list:{list_type}")
        rows.append(nav_buttons)

    b.button(text="⬅️ Назад", callback_data="filters")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def filter_rule_card(rule: FilterRule, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle_label = _toggle_label("Включено", "Выключено", rule.enabled)
    b.button(
        text=toggle_label, callback_data=f"filter_toggle:{rule.id}:{page}", style=_toggle_style(rule.enabled),
    )
    comment_label = "✏️ Изменить комментарий" if rule.comment else "✏️ Добавить комментарий"
    b.button(text=comment_label, callback_data=f"filter_comment:{rule.id}:{page}", style="primary")
    b.button(text="🗑 Удалить", callback_data=f"filter_remove_confirm:{rule.id}:{page}", style="danger")
    b.button(text="⬅️ К списку", callback_data=f"filters_list:{rule.list_type}:{page}")
    b.adjust(1)
    return b.as_markup()


def confirm_remove_filter(rule: FilterRule, page: int) -> InlineKeyboardMarkup:
    return confirm_keyboard(
        "✅ Да, удалить",
        f"filter_remove:{rule.id}:{rule.list_type}:{page}",
        f"filters_list:{rule.list_type}:{page}",
    )


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


def filter_import_list_type() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚨 Watch", callback_data="filter_import_type:watch")
    b.button(text="🚫 Ignore", callback_data="filter_import_type:ignore")
    b.button(text="✅ Allow", callback_data="filter_import_type:allow")
    b.button(text="❌ Отмена", callback_data="filters")
    b.adjust(3, 1)
    return b.as_markup()


def filter_import_pattern_type(list_type: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Suffix", callback_data=f"filter_import_ptype:{list_type}:suffix")
    b.button(text="Exact", callback_data=f"filter_import_ptype:{list_type}:exact")
    b.button(text="Wildcard", callback_data=f"filter_import_ptype:{list_type}:wildcard")
    b.button(text="❌ Отмена", callback_data="filters")
    b.adjust(3, 1)
    return b.as_markup()


def confirm_filter_import() -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, импортировать", "filter_import_confirm", "filters")


def filter_export_scope() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚨 Watch", callback_data="filter_export_scope:watch")
    b.button(text="🚫 Ignore", callback_data="filter_export_scope:ignore")
    b.button(text="✅ Allow", callback_data="filter_export_scope:allow")
    b.button(text="📦 Всё сразу", callback_data="filter_export_scope:all")
    b.button(text="❌ Отмена", callback_data="filters")
    b.adjust(3, 1, 1)
    return b.as_markup()


def filter_export_format(scope: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="TXT", callback_data=f"filter_export_fmt:{scope}:txt")
    b.button(text="CSV", callback_data=f"filter_export_fmt:{scope}:csv")
    b.button(text="JSON", callback_data=f"filter_export_fmt:{scope}:json")
    b.button(text="❌ Отмена", callback_data="filters")
    b.adjust(3, 1)
    return b.as_markup()


def domain_notification(domain_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚫 Игнорировать", callback_data=f"domain_ignore:{domain_id}", style="danger")
    b.button(text="📋 Копировать", callback_data=f"domain_copy:{domain_id}", style="primary")
    b.adjust(2)
    return b.as_markup()


# ------------------------------------------------------------------
# Backups
# ------------------------------------------------------------------

BACKUP_DEST_LABELS = {
    "server": "📦 Только на сервере",
    "dm": "💬 В личку админам",
    "group": "👥 В группу",
}


def backups_menu(enabled: bool, interval_hours: int, keep_count: int, destination: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle = _toggle_label("Автобэкап включён", "Автобэкап выключен", enabled)
    b.button(text=toggle, callback_data="backup_toggle", style=_toggle_style(enabled))
    b.button(text=f"⏱ Периодичность: {interval_hours} ч.", callback_data="backup_set_interval", style="primary")
    b.button(text=f"🗂 Хранить копий: {keep_count}", callback_data="backup_set_keep", style="primary")
    dest_label = BACKUP_DEST_LABELS.get(destination, destination)
    b.button(text=f"Способ доставки: {dest_label}", callback_data="backup_set_destination", style="primary")
    b.button(text="▶️ Сделать бэкап сейчас", callback_data="backup_now", style="success")
    b.button(text="📋 Список бэкапов", callback_data="backup_list")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1, 1, 1)
    return b.as_markup()


def backup_destination_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in BACKUP_DEST_LABELS.items():
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"backup_dest:{value}")
    b.button(text="⬅️ Назад", callback_data="backups")
    b.adjust(1, 1, 1, 1)
    return b.as_markup()
