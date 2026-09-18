"""Keyboards for the "🧰 Фильтры" section: rule list/card, add, bulk
import, export, and the domain-check tool.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import FilterRule
from app.telegram.keyboards.common import PATTERN_TAG, _toggle_label, _toggle_style, confirm_keyboard
from app.telegram.pagination import add_pagination_row


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
