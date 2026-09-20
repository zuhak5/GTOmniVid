"""Standard bot command handlers (/start, /help, /quota, /cancel) for GTOmniVid."""

import logging
from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from core.queue import ConcurrencyController
from core.quota import QuotaLedger

logger = logging.getLogger(__name__)

router = Router(name="commands_router")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Sends welcome message explaining GTOmniVid capabilities and free usage."""
    text = (
        "👋 <b>Welcome to GTOmniVid!</b>\n\n"
        "I am an enterprise-grade, zero-cost media downloader engineered to run permanently "
        "on <b>Google Cloud Always-Free Tier</b> infrastructure ($0.00/mo guaranteed).\n\n"
        "<b>Supported Platforms:</b>\n"
        "• 📹 YouTube (Shorts & Videos)\n"
        "• 🎵 TikTok (Clean unwatermarked HD)\n"
        "• 📸 Instagram (Reels & Posts)\n"
        "• 📘 Facebook (Watch & Reels)\n\n"
        "<b>Key Features:</b>\n"
        "• ⚡ Stream remuxing (-c copy, 0% CPU re-encoding)\n"
        "• 🎵 Native lossless M4A audio extraction\n"
        "• 🌐 Direct Stream Link mode (0 MB egress bandwidth)\n\n"
        "Simply paste any media link to get started, or check /quota to view your usage!"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Sends detailed user instructions."""
    text = (
        "📖 <b>How to Use GTOmniVid:</b>\n\n"
        "1. <b>Send a Link:</b> Paste any public YouTube, TikTok, Instagram, or Facebook link.\n"
        "2. <b>Select Quality:</b> Choose your preferred resolution (4K, 2K, 1080p, 720p, 480p, 360p, 240p, 144p) or Audio (M4A).\n"
        "3. <b>Direct Link:</b> If you want to stream directly without consuming bot upload bandwidth, "
        "choose <b>Direct Link</b>.\n\n"
        "<b>Commands:</b>\n"
        "/start — Restart bot and show intro\n"
        "/quota — View daily downloads & monthly GCP egress stats\n"
        "/cancel — Cancel your active or queued download\n"
        "/help — View this guidance"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("quota"))
async def cmd_quota(message: Message, quota_ledger: QuotaLedger) -> None:
    """Displays user's remaining daily downloads and global monthly bandwidth consumption."""
    user_id = message.from_user.id if message.from_user else 0
    stats = await quota_ledger.get_system_quota_summary(user_id)

    tier_emoji = {
        "normal": "🟢 Normal",
        "warning": "🟡 Warning (Prioritizing <= 480p & Audio)",
        "hard_cap": "🔴 Hard Cap (Direct Stream Link Only)"
    }.get(stats["egress_tier"], "🟢 Normal")

    rem_display = stats["user_remaining_today"]
    remaining_text = "Unlimited" if rem_display == "Unlimited" else f"{rem_display} downloads"

    text = (
        "📊 <b>GTOmniVid Free Tier Resource Monitor</b>\n\n"
        "<b>Your Daily Fair Quota:</b>\n"
        f"• Downloads today: <b>{stats['user_downloads_today']} / {stats['user_daily_limit']}</b>\n"
        f"• Remaining today: <b>{remaining_text}</b>\n\n"
        "<b>Global Monthly Bandwidth (GCP Free Tier):</b>\n"
        f"• Consumed egress: <b>{stats['monthly_egress_mb']:.1f} MB / {stats['monthly_hard_cap_mb']} MB</b> ({stats['egress_percentage']}%)\n"
        f"• System status: <b>{tier_emoji}</b>\n\n"
        "<i>Note: Quotas ensure the service remains 100% free with $0.00 cloud billing.</i>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, concurrency_controller: ConcurrencyController) -> None:
    """Cancels any pending or active job for the calling user."""
    user_id = message.from_user.id if message.from_user else 0
    cancelled_job_id = concurrency_controller.cancel_user_active_job(user_id)

    if cancelled_job_id:
        await message.answer("🛑 <b>Your processing job has been cancelled.</b>", parse_mode="HTML")
    else:
        await message.answer("ℹ️ You have no active or queued jobs to cancel.")
