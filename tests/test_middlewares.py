"""Unit tests for bot middlewares (bot/middlewares/)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiogram.types import Message, User, Chat

from bot.middlewares.error_handler import GlobalErrorHandler
from bot.middlewares.rate_limit import AntiFloodMiddleware
from core.quota import QuotaExceededError
from core.security import DomainNotAllowedError, SecurityError


def _create_mock_message(user_id: int) -> Message:
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="TestUser")
    msg.chat = Chat(id=user_id, type="private")
    msg.answer = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_anti_flood_middleware_throttles_rapid_messages():
    """Verify messages sent within min_interval (2.0s) are rate-limited."""
    middleware = AntiFloodMiddleware(min_interval_seconds=1.0)
    handler = AsyncMock(return_value="handled")

    msg = _create_mock_message(user_id=123)

    # 1st message: allowed
    res1 = await middleware(handler, msg, {})
    assert res1 == "handled"
    assert handler.call_count == 1

    # 2nd message immediately after: throttled
    res2 = await middleware(handler, msg, {})
    assert res2 is None
    assert handler.call_count == 1
    msg.answer.assert_called_once_with("⚠️ You are sending requests too quickly. Please wait a moment.")

    # After interval: allowed again
    await asyncio.sleep(1.05)
    res3 = await middleware(handler, msg, {})
    assert res3 == "handled"
    assert handler.call_count == 2


@pytest.mark.asyncio
async def test_global_error_handler_catches_security_error():
    """Verify SecurityError sends polite sanitized security warning."""
    error_handler = GlobalErrorHandler()
    msg = _create_mock_message(user_id=456)

    async def throwing_handler(event, data):
        raise DomainNotAllowedError("attacker.com is bad")

    await error_handler(throwing_handler, msg, {})
    msg.answer.assert_called_once()
    args = msg.answer.call_args[0][0]
    assert "⛔ Prohibited or unsupported link" in args


@pytest.mark.asyncio
async def test_global_error_handler_catches_quota_exceeded():
    """Verify QuotaExceededError notifies user of daily quota limit."""
    error_handler = GlobalErrorHandler()
    msg = _create_mock_message(user_id=789)

    async def throwing_handler(event, data):
        raise QuotaExceededError("Limit reached")

    await error_handler(throwing_handler, msg, {})
    msg.answer.assert_called_once()
    args = msg.answer.call_args[0][0]
    assert "daily quota of 3 downloads" in args
