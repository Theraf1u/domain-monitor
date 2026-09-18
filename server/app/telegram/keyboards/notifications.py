"""Keyboards for the "🔔 Уведомления" Notification Center."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.telegram.keyboards.common import NOTIFY_DEST_LABELS, _toggle_label, _toggle_style

_PRESET_MODES = [
    ("instant", "Instant"), ("5", "Batch 5s"), ("15", "Batch 15s"), ("30", "Batch 30s"),
    ("60", "Batch 60s"), ("300", "Batch 5 мин"),
]


def notify_menu(current_mode: str, global_enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle = _toggle_label("Уведомления включены", "Уведомления выключены", global_enabled)
    b.button(text=toggle, callback_data="notify_toggle_global", style=_toggle_style(global_enabled))
    b.button(text="📋 Типы событий", callback_data="notify_types", style="primary")
    b.button(text="📍 Получатели", callback_data="notify_recipients", style="primary")
    b.button(text="🌙 Тихие часы", callback_data="notify_quiet_hours", style="primary")
    b.button(text="🧪 Тест уведомления", callback_data="notify_test", style="primary")
    rows = [1, 2, 2]
    for value, label in _PRESET_MODES:
        prefix = "✅ " if value == current_mode else ""
        b.button(text=f"{prefix}{label}", callback_data=f"notify_mode:{value}")
    is_custom = current_mode not in {v for v, _ in _PRESET_MODES}
    custom_label = f"✅ Свой ({current_mode} сек)" if is_custom else "✏️ Свой интервал"
    b.button(text=custom_label, callback_data="notify_custom", style="primary")
    b.button(text="⬅️ Назад", callback_data="main")
    rows.extend([2, 2, 2, 1, 1])
    b.adjust(*rows)
    return b.as_markup()


def notify_types_menu(type_states: list[tuple[str, str, bool]]) -> InlineKeyboardMarkup:
    """`type_states` is [(event_type, label, enabled), ...] - already
    resolved by the caller (handlers.py owns NOTIFY_TYPES/Notifier), this
    only renders."""
    b = InlineKeyboardBuilder()
    for event_type, label, enabled in type_states:
        b.button(
            text=_toggle_label(label, label, enabled), style=_toggle_style(enabled),
            callback_data=f"notify_type_toggle:{event_type}",
        )
    b.button(text="⬅️ Назад", callback_data="notify_menu")
    b.adjust(1)
    return b.as_markup()


def notify_recipients_menu(global_dest: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in NOTIFY_DEST_LABELS.items():
        prefix = "✅ " if value == global_dest else ""
        b.button(text=f"{prefix}{label}", callback_data=f"notify_global_dest_set:{value}")
    b.button(text="ℹ️ У каждой ноды можно задать свой способ отдельно (карточка ноды)", callback_data="noop")
    b.button(text="⬅️ Назад", callback_data="notify_menu")
    b.adjust(1, 1, 1, 1, 1)
    return b.as_markup()


def notify_quiet_hours_menu(enabled: bool, start: str, end: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=_toggle_label(f"Тихие часы включены ({start}–{end})", "Тихие часы выключены", enabled),
        style=_toggle_style(enabled), callback_data="notify_quiet_hours_toggle",
    )
    b.button(text="✏️ Изменить время", callback_data="notify_quiet_hours_edit", style="primary")
    b.button(text="⬅️ Назад", callback_data="notify_menu")
    b.adjust(1, 1, 1)
    return b.as_markup()
