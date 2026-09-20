"""Telegram Uploader with throttled progress reporting and egress accounting for GTOmniVid.

Enforces:
- Anti-flood progress editing (<= 1 edit / 3 seconds)
- Pre-upload 50 MB Telegram Bot API hard ceiling verification
- Native video delivery with dimensions, duration, thumbnail, and faststart streaming
- Automatic commit to SQLite egress ledger and daily quota incrementation
"""

import logging
import time
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile, Message

from core.quota import QuotaLedger
from media.ffmpeg import MediaStreamInfo

logger = logging.getLogger(__name__)

# Hard limit for standard Telegram Bot API uploads (50 MB)
MAX_TELEGRAM_BOT_FILE_SIZE_BYTES = 50 * 1024 * 1024


class ThrottledProgressBar:
    """Debounces Telegram message edits to adhere to anti-flood limits (<= 1 edit / 3s)."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        message_id: int,
        throttle_interval_seconds: float = 3.0
    ) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.throttle_interval = throttle_interval_seconds
        self.last_update_time = 0.0

    async def update(self, current_bytes: int, total_bytes: int, status_title: str) -> None:
        """Renders and sends throttled visual progress bar."""
        now = time.monotonic()
        if now - self.last_update_time < self.throttle_interval and current_bytes < total_bytes:
            return

        percent = min(100.0, (current_bytes / total_bytes) * 100.0) if total_bytes > 0 else 0.0
        filled = int(percent // 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        curr_mb = current_bytes / (1024 * 1024)
        total_mb = total_bytes / (1024 * 1024)

        text = (
            f"⚡ <b>{status_title}</b>\n"
            f"<code>[{bar}] {percent:.1f}%</code> ({curr_mb:.1f}MB / {total_mb:.1f}MB)"
        )

        try:
            await self.bot.edit_message_text(
                text=text,
                chat_id=self.chat_id,
                message_id=self.message_id,
                parse_mode="HTML"
            )
            self.last_update_time = now
        except Exception as err:
            logger.debug("Suppressed progress edit exception: %s", err)


class TelegramUploader:
    """Handles final media verification, native Telegram delivery, and egress accounting."""

    def __init__(self, bot: Bot, quota_ledger: Optional[QuotaLedger] = None) -> None:
        self.bot = bot
        self.quota_ledger = quota_ledger or QuotaLedger()

    async def deliver_media(
        self,
        chat_id: int,
        user_id: int,
        progress_msg_id: int,
        media_path: Path,
        thumbnail_path: Optional[Path],
        stream_info: Optional[MediaStreamInfo],
        title: str,
        platform: str,
        is_audio: bool
    ) -> bool:
        """Delivers media to user in Telegram and updates egress accounting."""
        if not media_path.exists():
            logger.error("Media file %s does not exist for upload.", media_path)
            return False

        file_size = media_path.stat().st_size

        # Pre-flight Telegram 50 MB upload limit check
        if file_size > MAX_TELEGRAM_BOT_FILE_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            logger.warning("File size (%.1f MB) exceeds Telegram 50 MB Bot API ceiling.", size_mb)
            await self.bot.edit_message_text(
                text=(
                    f"⚠️ <b>File Too Large for Bot Upload</b>\n\n"
                    f"The processed media is <b>{size_mb:.1f} MB</b>, which exceeds "
                    f"Telegram's 50 MB Bot API limit.\n\n"
                    f"💡 <i>Tip: Select 480p SD or use Direct Stream Link mode instead.</i>"
                ),
                chat_id=chat_id,
                message_id=progress_msg_id,
                parse_mode="HTML"
            )
            return False

        # Update status
        try:
            await self.bot.edit_message_text(
                text="📤 <i>Uploading to Telegram...</i>",
                chat_id=chat_id,
                message_id=progress_msg_id,
                parse_mode="HTML"
            )
        except Exception:
            pass

        # Input file handles
        media_input = FSInputFile(media_path, filename=media_path.name)
        thumb_input = FSInputFile(thumbnail_path) if (thumbnail_path and thumbnail_path.exists()) else None

        import html
        safe_title = html.escape((title or "Media")[:400])
        caption = f"🎬 <b>{safe_title}</b>\n\n⚡ <i>Delivered via GTOmniVid</i>"

        duration = stream_info.duration_seconds if stream_info else None

        try:
            if is_audio:
                await self.bot.send_audio(
                    chat_id=chat_id,
                    audio=media_input,
                    duration=duration,
                    title=title,
                    caption=caption,
                    parse_mode="HTML"
                )
            else:
                width = stream_info.width if stream_info else None
                height = stream_info.height if stream_info else None
                duration = stream_info.duration_seconds if stream_info else None

                await self.bot.send_video(
                    chat_id=chat_id,
                    video=media_input,
                    thumbnail=thumb_input,
                    width=width,
                    height=height,
                    duration=duration,
                    supports_streaming=True,
                    caption=caption,
                    parse_mode="HTML"
                )

            # Egress Accounting: Commit bytes to SQLite ledger & increment daily user count
            await self.quota_ledger.record_egress(
                user_id=user_id,
                bytes_sent=file_size,
                file_type="audio" if is_audio else "video",
                platform=platform,
                delivery_mode="upload"
            )
            await self.quota_ledger.increment_user_quota(user_id=user_id)

            # Clean up the progress message
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
            except Exception:
                pass

            logger.info("Successfully delivered %s (%d bytes) to user %d", media_path.name, file_size, user_id)
            return True

        except Exception as err:
            logger.error("Failed to upload media to Telegram: %s", err)
            await self.bot.edit_message_text(
                text="❌ Failed to deliver media file to Telegram. Please try again.",
                chat_id=chat_id,
                message_id=progress_msg_id
            )
            return False
