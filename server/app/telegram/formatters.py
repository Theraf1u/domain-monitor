"""Shared formatting helpers for Telegram screen text - one place for
byte sizes, durations, and relative/absolute timestamps so every screen
renders them the same way instead of each handler rolling its own.
"""
from __future__ import annotations

from datetime import datetime, timezone


def format_bytes(num_bytes: int | float) -> str:
    """1536 -> "1.5 КБ". Caps at ГБ - nothing this project stores gets
    anywhere near ТБ, and adding that unit would just be dead code."""
    value = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ГБ"


def format_duration(total_seconds: int | float) -> str:
    """3900 -> "1ч 5м", 90000 -> "1д 1ч", 45 -> "45с". Drops minutes once
    the duration is measured in days - "3д 2ч 14м" is noise nobody reads
    that precisely at that scale."""
    total_seconds = max(0, int(total_seconds))
    if total_seconds < 60:
        return f"{total_seconds}с"
    minutes, _ = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes and not days:
        parts.append(f"{minutes}м")
    return " ".join(parts) if parts else "0м"


def format_relative_time(when: datetime | None, now: datetime | None = None) -> str:
    """"8 сек назад" / "5 мин назад" / "3 ч назад" / "2 дн назад".
    None -> "никогда" (a heartbeat/event that has genuinely never
    happened, not a formatting edge case to hide)."""
    if when is None:
        return "никогда"
    now = now or datetime.now(timezone.utc)
    delta = max(0, int((now - when).total_seconds()))
    if delta < 60:
        return f"{delta} сек назад"
    if delta < 3600:
        return f"{delta // 60} мин назад"
    if delta < 86400:
        return f"{delta // 3600} ч назад"
    return f"{delta // 86400} дн назад"


def format_datetime(when: datetime | None) -> str:
    """Absolute UTC timestamp for display: "2026-09-18 03:55 UTC".
    None -> "—" (matches the "—" placeholder already used elsewhere in
    node cards for an unknown field, not a fabricated timestamp)."""
    if when is None:
        return "—"
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
