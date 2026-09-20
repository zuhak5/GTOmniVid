"""Anti-flood rate limiting middleware for GTOmniVid.

Restricts incoming messages to 1 message per 2 seconds per user to prevent
spam, abuse, and event loop starvation on the 1 GB e2-micro VM.
"""

import logging
import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


class AntiFloodMiddleware(BaseMiddleware):
    """Enforces per-user rate limiting on incoming messages and callback queries."""

    def __init__(self, min_interval_seconds: float = 2.0) -> None:
        super().__init__()
        self.min_interval = min_interval_seconds
        self._user_last_action: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            return await handler(event, data)

        now = time.monotonic()
        last_action = self._user_last_action.get(user_id, 0.0)

        if now - last_action < self.min_interval:
            logger.debug("Rate limited user %d (interval: %.2fs)", user_id, now - last_action)
            if isinstance(event, Message):
                await event.answer("⚠️ You are sending requests too quickly. Please wait a moment.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⚠️ Please wait a moment before tapping again.", show_alert=False)
            return None

        self._user_last_action[user_id] = now
        self._prune_stale_cache(now)
        return await handler(event, data)

    def _prune_stale_cache(self, now: float) -> None:
        """Removes entries older than 60 seconds to prevent unbounded memory growth."""
        if len(self._user_last_action) > 500:
            stale_threshold = now - 60.0
            self._user_last_action = {
                uid: ts for uid, ts in self._user_last_action.items()
                if ts > stale_threshold
            }
