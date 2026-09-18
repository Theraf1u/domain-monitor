"""Shared keyboard building blocks: the two-tier button-color helpers,
the main menu, generic back/cancel/confirm keyboards, live-refresh
controls, and the period-label constants used by more than one section.

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

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


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
NOTIFY_DEST_LABELS = {
    "dm": "💬 Только в ЛС",
    "group": "👥 Только в группу",
    "both": "🔀 В ЛС и в группу",
}

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
