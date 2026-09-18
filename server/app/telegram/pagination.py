"""Shared pagination row for any list screen (filters, and future nodes/
domains/backups lists) - one place for the `[◀️] N/M [▶️]` layout instead
of every screen hand-rolling its own prev/next buttons.
"""
from __future__ import annotations

from aiogram.utils.keyboard import InlineKeyboardBuilder


def add_pagination_row(
    builder: InlineKeyboardBuilder, page: int, page_size: int, total_items: int, callback_prefix: str,
) -> int:
    """Appends `[◀️] N/M [▶️]` to `builder`, with `callback_prefix:{page±1}`
    for the arrows and a non-interactive `noop` callback for the page
    counter itself (Telegram still requires SOME callback_data on every
    button). Omits an arrow that would go out of range instead of
    disabling it - Telegram inline buttons can't be shown disabled, only
    present or absent. Returns how many buttons were added, for
    `.adjust(...)`; callers should still call `.adjust()` themselves,
    this only appends buttons.
    """
    total_pages = max(1, -(-total_items // page_size))  # ceil division
    current = min(page, total_pages - 1) + 1
    count = 0
    if page > 0:
        builder.button(text="◀️", callback_data=f"{callback_prefix}:{page - 1}")
        count += 1
    builder.button(text=f"{current} / {total_pages}", callback_data="noop")
    count += 1
    if (page + 1) * page_size < total_items:
        builder.button(text="▶️", callback_data=f"{callback_prefix}:{page + 1}")
        count += 1
    return count
