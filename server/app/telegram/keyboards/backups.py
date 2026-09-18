"""Keyboards for the "💾 Бэкапы" section."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.telegram.keyboards.common import _add_live_controls, _toggle_label, _toggle_style

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


def backup_list_menu(live_active: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _add_live_controls(b, "backup_list", "backup_list", live_active)
    b.button(text="⬅️ Назад", callback_data="backups")
    b.adjust(2, 1)
    return b.as_markup()
