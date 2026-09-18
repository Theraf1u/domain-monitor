"""Keyboards for the "💾 Бэкапы" section (Backup Manager 2.0, spec 2.0
Part 2, section 2)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.telegram.keyboards.common import _add_live_controls, _toggle_label, _toggle_style, confirm_keyboard

BACKUP_DEST_LABELS = {
    "server": "📦 Только на сервере",
    "dm": "💬 В личку админам",
    "group": "👥 В группу",
}

BACKUP_KIND_LABELS = {"db": "🗄 База данных", "full": "📦 Полный (+ env)"}


def backups_menu(enabled: bool, interval_hours: int, keep_count: int, destination: str, kind: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    toggle = _toggle_label("Автобэкап включён", "Автобэкап выключен", enabled)
    b.button(text=toggle, callback_data="backup_toggle", style=_toggle_style(enabled))
    b.button(text=f"⏱ Периодичность: {interval_hours} ч.", callback_data="backup_set_interval", style="primary")
    b.button(text=f"🗂 Хранить копий: {keep_count}", callback_data="backup_set_keep", style="primary")
    dest_label = BACKUP_DEST_LABELS.get(destination, destination)
    b.button(text=f"📍 Доставка: {dest_label}", callback_data="backup_set_destination", style="primary")
    kind_label = BACKUP_KIND_LABELS.get(kind, kind)
    b.button(text=f"📦 Что сохранять: {kind_label}", callback_data="backup_set_kind", style="primary")
    b.button(text="▶️ Создать сейчас", callback_data="backup_now", style="success")
    b.button(text="📋 Список бэкапов", callback_data="backup_list")
    b.button(text="♻️ Восстановить", callback_data="backup_restore_menu", style="danger")
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1)
    return b.as_markup()


def backup_destination_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in BACKUP_DEST_LABELS.items():
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"backup_dest:{value}")
    b.button(text="⬅️ Назад", callback_data="backups")
    b.adjust(1, 1, 1, 1)
    return b.as_markup()


def backup_kind_menu(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in BACKUP_KIND_LABELS.items():
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"backup_kind_set:{value}")
    b.button(text="⬅️ Назад", callback_data="backups")
    b.adjust(1, 1, 1)
    return b.as_markup()


def backup_now_kind_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in BACKUP_KIND_LABELS.items():
        b.button(text=label, callback_data=f"backup_now_do:{value}")
    b.button(text="⬅️ Отмена", callback_data="backups")
    b.adjust(1, 1, 1)
    return b.as_markup()


def backup_list_menu(filenames: list[str], live_active: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for name in filenames:
        b.button(text=name, callback_data=f"bk_item:{name}")
    rows = [1] * len(filenames)
    _add_live_controls(b, "backup_list", "backup_list", live_active)
    rows.append(2)
    b.button(text="⬅️ Назад", callback_data="backups")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def backup_item_menu(filename: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬇️ Скачать", callback_data=f"bk_dl:{filename}", style="primary")
    b.button(text="🔍 Проверить целостность", callback_data=f"bk_verify:{filename}", style="primary")
    b.button(text="♻️ Восстановить из этого", callback_data=f"bk_restore_confirm:{filename}", style="danger")
    b.button(text="🗑 Удалить", callback_data=f"bk_del_confirm:{filename}", style="danger")
    b.button(text="⬅️ К списку", callback_data="backup_list")
    b.adjust(1, 1, 1, 1, 1)
    return b.as_markup()


def confirm_backup_delete(filename: str) -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, удалить", f"bk_del:{filename}", f"bk_item:{filename}")


def confirm_backup_restore(filename: str) -> InlineKeyboardMarkup:
    return confirm_keyboard("✅ Да, восстановить", f"bk_restore:{filename}", f"bk_item:{filename}")


def backup_restore_menu(filenames: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if not filenames:
        b.button(text="⬅️ Назад", callback_data="backups")
        b.adjust(1)
        return b.as_markup()
    for name in filenames:
        b.button(text=name, callback_data=f"bk_restore_confirm:{name}")
    b.button(text="⬅️ Назад", callback_data="backups")
    b.adjust(*([1] * len(filenames)), 1)
    return b.as_markup()
