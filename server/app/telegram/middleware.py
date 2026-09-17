"""Restricts every update to a single ADMIN_ID. Anyone else's message or
button press is silently dropped - callback queries still get an empty ack
so the Telegram client doesn't show a spinner, but no reply is sent, so a
stranger who finds the bot gets no confirmation it does anything at all."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject, Update


class AdminOnlyMiddleware(BaseMiddleware):
    def __init__(self, admin_id: int) -> None:
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id != self.admin_id:
            if isinstance(event, Update) and event.callback_query:
                await event.callback_query.answer()
            elif isinstance(event, CallbackQuery):
                await event.answer()
            return None
        return await handler(event, data)
