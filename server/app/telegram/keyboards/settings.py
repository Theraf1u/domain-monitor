"""Keyboards for the "⚙️ Настройки" section (spec 2.0 Part 2, section 1):
a hub screen linking to Сервер/Ноды по умолчанию/Часовой пояс/Хранение
данных/Администраторы/Безопасность/Диагностика/О системе/Миграция.
Docker-UFW status and system-update UI still aren't linked from the hub -
those are later stages (6) in the spec's own execution order; adding a
button that opens nothing real would be exactly the "no dead screens, no
fake functions" rule this whole project has followed.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.telegram.keyboards.common import _toggle_label, _toggle_style


def settings_menu(watchlist_enabled: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🖥 Сервер", callback_data="settings_server", style="primary")
    b.button(text="📡 Ноды по умолчанию", callback_data="settings_node_defaults", style="primary")
    b.button(text="🌍 Часовой пояс", callback_data="settings_timezone", style="primary")
    b.button(text="🗄 Хранение данных", callback_data="settings_retention_menu", style="primary")
    b.button(text="👥 Администраторы", callback_data="settings_admins", style="primary")
    b.button(text="🔐 Безопасность", callback_data="settings_security", style="primary")
    b.button(text="🩺 Диагностика", callback_data="settings_diagnostics", style="primary")
    b.button(text="🔄 Обновления", callback_data="settings_updates", style="primary")
    b.button(text="🚚 Миграция", callback_data="settings_migration", style="primary")
    b.button(text="ℹ️ О системе", callback_data="settings_about", style="primary")
    wl_label = _toggle_label("Watch-уведомления включены", "Watch-уведомления выключены", watchlist_enabled)
    b.button(text=wl_label, callback_data="settings_toggle_watchlist", style=_toggle_style(watchlist_enabled))
    b.button(text="⬅️ Назад", callback_data="main")
    b.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
    return b.as_markup()


def settings_node_defaults_menu(offline_seconds: int, buffer_thresholds_label: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"⏱ Offline через: {offline_seconds} сек", callback_data="settings_offline", style="primary")
    b.button(text=f"📦 Пороги буфера: {buffer_thresholds_label}", callback_data="settings_buffer", style="primary")
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1, 1, 1)
    return b.as_markup()


OFFLINE_PRESETS: list[tuple[int, str]] = [
    (30, "30 сек"), (60, "1 мин"), (120, "2 мин"), (300, "5 мин"), (600, "10 мин"),
]


def settings_offline_menu(current: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in OFFLINE_PRESETS:
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"settings_offline_set:{value}")
    is_custom = current not in {v for v, _ in OFFLINE_PRESETS}
    custom_label = f"✅ Свой ({current} сек)" if is_custom else "✏️ Своё значение"
    b.button(text=custom_label, callback_data="settings_offline_custom", style="primary")
    b.button(text="⬅️ Назад", callback_data="settings_node_defaults")
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


RETENTION_PRESETS: list[tuple[int, str]] = [
    (0, "Всегда"), (7, "7 дней"), (30, "30 дней"), (90, "90 дней"), (180, "180 дней"), (365, "365 дней"),
]


def settings_retention_menu(current: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value, label in RETENTION_PRESETS:
        prefix = "✅ " if value == current else ""
        b.button(text=f"{prefix}{label}", callback_data=f"settings_retention_set:{value}")
    is_custom = current not in {v for v, _ in RETENTION_PRESETS}
    custom_label = f"✅ Своё ({current} дн.)" if is_custom else "✏️ Своё значение"
    b.button(text=custom_label, callback_data="settings_retention_custom", style="primary")
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def settings_admins_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1)
    return b.as_markup()


def settings_security_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1)
    return b.as_markup()


def settings_diagnostics_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🩺 Запустить диагностику", callback_data="settings_diagnostics_run", style="primary")
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1, 1)
    return b.as_markup()


def settings_about_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1)
    return b.as_markup()


def settings_server_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1)
    return b.as_markup()


def settings_updates_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Проверить снова", callback_data="settings_updates_check", style="primary")
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1, 1)
    return b.as_markup()


def settings_migration_none_menu() -> InlineKeyboardMarkup:
    """No active job - `migrate-to` itself needs host-level SSH access
    the bot container doesn't have (spec 3: no arbitrary remote shell
    from the container), so starting one is CLI-only. Nothing to act on
    here but going back."""
    b = InlineKeyboardBuilder()
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1)
    return b.as_markup()


def settings_migration_active_menu(job_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data=f"mig_refresh:{job_id}", style="primary")
    if status == "standby":
        b.button(text="✅ Начать переключение", callback_data=f"mig_cutover_confirm:{job_id}", style="primary")
    if status == "cutover":
        b.button(text="📋 Как завершить", callback_data=f"mig_finish_help:{job_id}", style="primary")
    if status not in ("completed", "cancelled", "failed"):
        b.button(text="❌ Отменить миграцию", callback_data=f"mig_cancel_confirm:{job_id}", style="danger")
    b.button(text="⬅️ Назад", callback_data="settings")
    b.adjust(1)
    return b.as_markup()
