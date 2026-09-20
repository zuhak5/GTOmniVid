"""Global error handling and safe user messaging middleware for GTOmniVid."""

import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from core.quota import EgressHardCapExceededError, QuotaExceededError
from core.security import SecurityError
from extractors.ytdlp_extractor import ExtractionError
from storage.workspace import DiskSpaceLowError

logger = logging.getLogger(__name__)


class GlobalErrorHandler(BaseMiddleware):
    """Intercepts known exceptions and sends polite, sanitized status messages to users."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        try:
            return await handler(event, data)
        except SecurityError as sec_err:
            logger.warning("Security violation: %s", sec_err)
            err_msg = str(sec_err)
            if "DNS resolution failed" in err_msg or "DNS resolution timed out" in err_msg:
                await self._respond_error(
                    event,
                    "⚠️ Could not resolve domain name (DNS lookup failed/timed out). "
                    "If using short links (like vt.tiktok.com), please try sending the full browser link."
                )
            else:
                await self._respond_error(event, "⛔ Prohibited or unsupported link. Supported: YouTube, TikTok, Instagram, Facebook.")
        except QuotaExceededError:
            await self._respond_error(
                event,
                "⚠️ You have reached your daily quota of 3 downloads. "
                "Please try again tomorrow or select Direct Stream Link mode (0 MB egress)."
            )
        except EgressHardCapExceededError:
            await self._respond_error(
                event,
                "🚨 The bot has reached its monthly free bandwidth limit (900 MB). "
                "Direct Stream Link mode is enabled so you can still watch videos at 0 egress cost!"
            )
        except DiskSpaceLowError:
            await self._respond_error(
                event,
                "⚠️ The server storage is temporarily full. The automated housekeeper "
                "is cleaning up; please try again in a few minutes."
            )
        except ExtractionError as ext_err:
            logger.warning("Extraction error: %s", ext_err)
            await self._respond_error(
                event,
                "❌ Could not extract media. The video may be private, age-restricted, "
                "deleted, or region-blocked."
            )
        except Exception as unhandled_err:
            logger.exception("Unhandled error in bot handler: %s", unhandled_err)
            await self._respond_error(
                event,
                "⚠️ An unexpected error occurred while processing your request. "
                "Please try again in a moment."
            )

    async def _respond_error(self, event: TelegramObject, text: str) -> None:
        """Sends error text to Message or CallbackQuery event."""
        try:
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                if event.message:
                    await event.message.answer(text)
                await event.answer(text, show_alert=True)
        except Exception as send_err:
            logger.error("Failed to send error response: %s", send_err)
