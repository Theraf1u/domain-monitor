"""Helpers shared across more than one handlers/*.py module (period-range
math used by both domains and stats, the pagination no-op absorber, and
the two catch-all fallbacks). Anything used by only one module lives in
that module instead - this file is for genuine cross-cutting code only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import fleet_control
from app.database import Database
from app.telegram import keyboards as kb

router = Router()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    """The page-counter button in pagination rows (kb.add_pagination_row) -
    not meant to do anything, but every callback still has to answer() or
    the tap just spins forever on the user's end."""
    await call.answer()


def _period_range(
    period: str, now: datetime, tz_offset_minutes: int = 0,
) -> tuple[datetime | None, datetime | None]:
    """Maps a period key (shared by the export and stats menus) to a
    [since, until) window. None on either side means "no bound in that
    direction" - "all" is (None, None). "today"/"yesterday" are calendar
    days in the admin's configured timezone (spec 7.2), not UTC - shift
    into local time to find local midnight, then shift the boundary back
    to UTC for the actual DB comparison (everything is still stored and
    compared in UTC; only the boundary's *position* is timezone-aware)."""
    local_now = now + timedelta(minutes=tz_offset_minutes)
    today_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start_local - timedelta(minutes=tz_offset_minutes)
    if period == "1h":
        return now - timedelta(hours=1), None
    if period == "6h":
        return now - timedelta(hours=6), None
    if period == "today":
        return today_start, None
    if period == "yesterday":
        return today_start - timedelta(days=1), today_start
    if period == "24h":
        return now - timedelta(hours=24), None
    if period in ("7d", "week"):
        return now - timedelta(days=7), None
    if period in ("30d", "month"):
        return now - timedelta(days=30), None
    return None, None  # "all"


def _parse_date_range(text: str) -> tuple[datetime, datetime] | None:
    """"2026-09-01" -> that single day, or "2026-09-01 2026-09-15" -> that
    inclusive range. None on anything that doesn't parse - callers show
    their own format-hint error rather than this raising."""
    parts = text.split()
    try:
        if len(parts) == 1:
            day = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return day, day + timedelta(days=1)
        if len(parts) == 2:
            start = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            end = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            return start, end
    except ValueError:
        pass
    return None


# ------------------------------------------------------------------
# Fallbacks - included last (see handlers/__init__.py), so they only
# catch what nothing in any other module already matched.
# ------------------------------------------------------------------

fallback_router = Router()


@fallback_router.callback_query()
async def callback_fallback(call: CallbackQuery) -> None:
    """A button from a message sent before some callback_data format
    changed (or a screen that's been removed entirely) must never just
    throw - the tap gets an honest "this screen is stale" instead of the
    eternal Telegram spinner or an unhandled-exception trip through
    error_handler.py."""
    await call.answer("⚠️ Этот экран устарел. Откройте меню заново.", show_alert=True)


@fallback_router.message()
async def fallback(message: Message, state: FSMContext, db: Database) -> None:
    if await state.get_state() is not None:
        return  # an FSM handler above should have matched; do nothing extra
    await message.answer(
        "Используйте меню ниже:",
        reply_markup=kb.main_menu(fleet_control.is_monitoring_enabled(db), fleet_control.is_sending_enabled(db)),
    )
