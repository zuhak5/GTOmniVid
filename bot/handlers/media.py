"""Media URL message handler for GTOmniVid.

Intercepts media links, performs 3-stage security validation, queries platform
stream metadata without downloading, and presents the dynamic quality selection menu.
"""

import logging
import uuid
from aiogram import F, Router
from aiogram.types import Message

from core.quota import EgressTier, QuotaExceededError, QuotaLedger
from core.security import validate_media_url
from extractors.base import FormatTier
from extractors.ytdlp_extractor import YtdlpExtractor
from bot.keyboards.format_menu import build_format_keyboard, cache_format_options

logger = logging.getLogger(__name__)

router = Router(name="media_router")


@router.message(F.text.regexp(r"https?://[^\s]+"))
async def handle_media_url(
    message: Message,
    extractor: YtdlpExtractor,
    quota_ledger: QuotaLedger
) -> None:
    """Processes submitted URL, inspects streams, and renders format keyboard."""
    user_id = message.from_user.id if message.from_user else 0
    raw_text = message.text or ""

    # Extract first URL from message text
    url_match = raw_text.strip().split()[0]

    # Stage 1-3 Security Validation (Protocol, Domain whitelist, DNS SSRF guard)
    clean_url = await validate_media_url(url_match)

    # Verify user daily quota
    can_download = await quota_ledger.can_user_download(user_id)
    if not can_download:
        raise QuotaExceededError("User daily download quota exceeded.")

    # Check system monthly egress status
    current_tier = await quota_ledger.evaluate_egress_tier()

    # Inform user that extraction is in progress
    status_msg = await message.answer("🔍 <i>Inspecting media streams...</i>", parse_mode="HTML")

    try:
        # Probe metadata without downloading
        metadata = await extractor.extract_info(clean_url)

        if not metadata.formats:
            await status_msg.edit_text("❌ No playable formats found for this media link.")
            return

        # Filter formats if monthly bandwidth is restricted
        available_formats = metadata.formats
        if current_tier == EgressTier.HARD_CAP:
            # Enforce 100% Direct Stream Link mode (0 MB egress)
            available_formats = [f for f in available_formats if f.tier == FormatTier.DIRECT]
            if not available_formats:
                await status_msg.edit_text(
                    "🚨 <b>Monthly Bandwidth Limit Reached (900 MB)</b>\n\n"
                    "This video does not have an external Direct Stream Link, and the monthly "
                    "free upload cap has been reached. Please check back next calendar month.",
                    parse_mode="HTML"
                )
                return
        elif current_tier == EgressTier.WARNING:
            # Prioritize lower resolutions (<= 480p), Audio, and Direct Link to preserve remaining budget
            available_formats = [
                f for f in available_formats
                if f.tier in (
                    FormatTier.AUDIO,
                    FormatTier.DIRECT,
                    FormatTier.P480,
                    FormatTier.P360,
                    FormatTier.P240,
                    FormatTier.P144,
                )
            ]

        # Generate short cache key and cache formats
        short_key = uuid.uuid4().hex[:8]
        cache_format_options(short_key, available_formats, clean_url)

        # Build inline keyboard
        keyboard = build_format_keyboard(available_formats, short_key)

        duration_min = metadata.duration_seconds // 60
        duration_sec = metadata.duration_seconds % 60
        time_str = f"{duration_min}:{duration_sec:02d}" if metadata.duration_seconds > 0 else "N/A"

        caption = (
            f"🎬 <b>{metadata.title}</b>\n\n"
            f"⏱ <b>Duration:</b> {time_str}\n"
            f"👤 <b>Creator:</b> {metadata.uploader or 'Unknown'}\n\n"
            f"👇 <b>Select quality tier to download or stream:</b>"
        )

        await status_msg.edit_text(
            text=caption,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as err:
        logger.error("Error inspecting media for %s: %s", clean_url, err)
        err_lower = str(err).lower()
        if "no video" in err_lower or "there is no video" in err_lower:
            await status_msg.edit_text(
                "📷 <b>Photo / Image Post Detected</b>\n\n"
                "This post contains photos/images, not a video. "
                "GTOmniVid is optimized to download videos, Reels, Shorts, and audio tracks.",
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(
                "❌ <b>Extraction failed.</b>\n"
                "The media could not be retrieved. Please check that the link is valid, "
                "public, and not private or deleted.",
                parse_mode="HTML"
            )
