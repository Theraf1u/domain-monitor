"""Keyboards for the "🌐 Домены" section: quick menu, TXT period export,
and the full search/filter/list/card/history/export/data-management flow.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Domain, Node
from app.telegram.keyboards.common import (
    DOMAIN_PERIODS, EXPORT_PERIODS, _add_live_controls, _toggle_label, _toggle_style, confirm_keyboard,
)
from app.telegram.pagination import add_pagination_row


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


def domain_notification(domain_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚫 Игнорировать", callback_data=f"domain_ignore:{domain_id}", style="danger")
    b.button(text="📋 Копировать", callback_data=f"domain_copy:{domain_id}", style="primary")
    b.adjust(2)
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
