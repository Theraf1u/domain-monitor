"""Keyboards for the "⚙️ Настройки" section."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.telegram.keyboards.common import _toggle_label, _toggle_style


def settings_menu(
    retention_days: int, offline_seconds: int, watchlist_enabled: bool, timezone_label: str = "UTC",
    buffer_thresholds_label: str = "70%/90%",
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🗓 Хранение событий: {retention_days} дн.", callback_data="settings_retention", style="primary")
    b.button(text=f"⏱ Offline через: {offline_seconds} сек", callback_data="settings_offline", style="primary")
    b.button(text=f"🌍 Часовой пояс: {timezone_label}", callback_data="settings_timezone", style="primary")
    b.button(text=f"📦 Пороги буфера: {buffer_thresholds_label}", callback_data="settings_buffer", style="primary")
    wl_label = _toggle_label("Watch-уведомления включены", "Watch-уведомления выключены", watchlist_enabled)
    b.button(text=wl_label, callback_data="settings_toggle_watchlist", style=_toggle_style(watchlist_enabled))
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1, 1)
    return b.as_markup()
