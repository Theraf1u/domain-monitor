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

from app.models import FilterRule, Node
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

def nodes_list(nodes: list[Node], online_ids: set[int], fleet_monitoring_enabled: bool = True) -> InlineKeyboardMarkup:
    """Node status is shown by button COLOR, not an emoji dot: green =
    agent online and actively monitoring, red = revoked or not
    responding, blue = online but paused (either this node's own
    monitoring toggle or the fleet-wide one is off). The legend for
    this lives in the screen's text (see handlers._nodes_legend), since
    Telegram gives us only three button colors to work with."""
    b = InlineKeyboardBuilder()
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
    b.button(text="➕ Добавить", callback_data="node_add", style="primary")
    b.button(text="⬅️ Назад", callback_data="main")
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
    b.button(text="📤 Экспорт", callback_data="domains_export", style="primary")
    b.button(text="🗑 Сброс данных", callback_data="data_reset:domains", style="danger")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(2, 1, 1, 1)
    return b.as_markup()


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
    for value, label in STATS_PERIODS:
        prefix = "✅ " if value == period else ""
        b.button(text=f"{prefix}{label}", callback_data=f"stats_period:{value}")
    _add_live_controls(b, f"stats_period:{period}", f"stats:{period}", live_active)
    b.button(text="🗑 Сброс данных", callback_data="data_reset:stats", style="danger")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(2, 2, 2, 1, 1)
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

def settings_menu(retention_days: int, offline_seconds: int, watchlist_enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🗓 Хранение событий: {retention_days} дн.", callback_data="settings_retention", style="primary")
    b.button(text=f"⏱ Offline через: {offline_seconds} сек", callback_data="settings_offline", style="primary")
    wl_label = _toggle_label("Watch-уведомления включены", "Watch-уведомления выключены", watchlist_enabled)
    b.button(text=wl_label, callback_data="settings_toggle_watchlist", style=_toggle_style(watchlist_enabled))
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1)
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
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1, 1)
    return b.as_markup()


def filters_list(list_type: str, rules: list[FilterRule], page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    start = page * page_size
    page_rules = rules[start:start + page_size]
    for r in page_rules:
        tag = PATTERN_TAG.get(r.pattern_type, r.pattern_type)
        b.button(text=f"🗑 {r.pattern} ({tag})", callback_data=f"filter_remove_confirm:{r.id}:{page}", style="danger")
    rows = [1] * len(page_rules)

    if len(rules) > page_size:  # only clutter the screen with pagination if there's more than one page
        nav_buttons = add_pagination_row(b, page, page_size, len(rules), f"filters_list:{list_type}")
        rows.append(nav_buttons)

    b.button(text="⬅️ Назад", callback_data="filters")
    rows.append(1)
    b.adjust(*rows)
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
