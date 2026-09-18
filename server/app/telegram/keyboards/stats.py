"""Keyboards for the "📊 Статистика" section: unified periods (incl.
custom range) and the per-node stats screen.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Node
from app.telegram.keyboards.common import DOMAIN_PERIODS, _add_live_controls


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
