"""Application entrypoint for GTOmniVid Telegram Media Pipeline.

Runs permanently on Google Cloud Platform Always Free Tier e2-micro VM ($0.00/mo).
Integrates:
- Aiogram 3.x Long-Polling Gateway
- 3-Stage Security Shield (SSRF & GCP Metadata Defense)
- SQLite WAL Monthly Egress Ledger (< 900 MB Hard Cap)
- Concurrency Controller (Semaphore=1 on 1GB RAM)
- Lossless FFmpeg Stream Remuxer (-c copy)
- Automated Periodic Scratchpad Housekeeper
"""

import asyncio
import logging
import signal
import sys
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.bot import create_bot_and_dispatcher
from config.settings import get_settings
from core.housekeeper import HousekeeperService
from core.queue import ConcurrencyController, JobRequest
from core.quota import QuotaLedger
from core.security import identify_platform
from extractors.ytdlp_extractor import YtdlpExtractor
from media.ffmpeg import FFmpegService
from media.pipeline import MediaPipeline
from media.uploader import TelegramUploader

# Configure structured production logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("gtomnivid")


async def main() -> None:
    """Initializes all services and starts Telegram long polling."""
    settings = get_settings()
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    logger.info("Initializing GTOmniVid ($0.00 Always-Free Mandate)...")

    if not settings.BOT_TOKEN:
        logger.warning(
            "BOT_TOKEN is not set in environment or .env file! "
            "Please configure BOT_TOKEN from @BotFather before starting."
        )

    # 1. Initialize SQLite WAL Database
    quota_ledger = QuotaLedger(db_path=settings.DB_PATH)
    await quota_ledger.init_db()

    # 2. Initialize Core Subsystems
    housekeeper = HousekeeperService(
        scratch_dir=settings.SCRATCH_DIR,
        interval_seconds=600,
        max_age_seconds=1800,
        min_free_mb=settings.MIN_FREE_DISK_MB
    )
    concurrency_controller = ConcurrencyController(concurrency_limit=settings.CONCURRENCY_LIMIT)
    extractor = YtdlpExtractor()
    ffmpeg_service = FFmpegService()
    media_pipeline = MediaPipeline(extractor=extractor, ffmpeg_service=ffmpeg_service)

    # 3. Create Bot & Dispatcher
    bot, dp = create_bot_and_dispatcher(
        settings=settings,
        quota_ledger=quota_ledger,
        concurrency_controller=concurrency_controller,
        extractor=extractor
    )
    uploader = TelegramUploader(bot=bot, quota_ledger=quota_ledger)

    # 4. Wire Concurrency Queue Worker Handler
    async def worker_job_handler(job_request: JobRequest, cancel_event: asyncio.Event) -> bool:
        """Executes within Semaphore(1) lock to process and deliver media."""
        def progress_cb(text: str) -> None:
            # Fire-and-forget status edit
            asyncio.create_task(
                bot.edit_message_text(
                    text=f"⚙️ <i>{text}</i>",
                    chat_id=job_request.chat_id,
                    message_id=job_request.message_id,
                    parse_mode="HTML"
                )
            )

        try:
            async with media_pipeline.process_job(
                job_id=job_request.job_id,
                webpage_url=job_request.webpage_url,
                selected_format=job_request.selected_format,
                cancel_event=cancel_event,
                progress_callback=progress_cb
            ) as result:
                if result.is_direct_link and result.direct_url:
                    # Direct Stream Link mode (0 MB egress consumed)
                    btn = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="▶️ Stream Directly (0 MB Egress)", url=result.direct_url)
                    ]])
                    await bot.edit_message_text(
                        text=(
                            f"🌐 <b>Direct Stream Mode</b>\n\n"
                            f"🎬 <b>{job_request.title}</b>\n\n"
                            f"Tap below to stream directly from the platform CDN at <b>0 MB</b> cloud egress cost:"
                        ),
                        chat_id=job_request.chat_id,
                        message_id=job_request.message_id,
                        reply_markup=btn,
                        parse_mode="HTML"
                    )
                    return True

                # Native delivery to Telegram
                platform = identify_platform(job_request.webpage_url)
                assert result.media_path is not None
                return await uploader.deliver_media(
                    chat_id=job_request.chat_id,
                    user_id=job_request.user_id,
                    progress_msg_id=job_request.message_id,
                    media_path=result.media_path,
                    thumbnail_path=result.thumbnail_path,
                    stream_info=result.stream_info,
                    title=job_request.title,
                    platform=platform,
                    is_audio=result.is_audio
                )
        except asyncio.CancelledError:
            logger.info("Job %s was cancelled.", job_request.job_id)
            return False
        except Exception as job_err:
            logger.exception("Error processing job %s: %s", job_request.job_id, job_err)
            try:
                import html
                err_msg = html.escape(str(job_err))
                await bot.edit_message_text(
                    text=f"⚠️ <b>Download Error</b>\n\nCould not complete processing: <code>{err_msg}</code>",
                    chat_id=job_request.chat_id,
                    message_id=job_request.message_id,
                    parse_mode="HTML"
                )
            except Exception as notify_err:
                logger.warning("Failed to send error notification to user: %s", notify_err)
            return False

    concurrency_controller.set_handler(worker_job_handler)

    # 5. Start Background Maintenance & Queue Workers
    await housekeeper.start()
    await concurrency_controller.start()

    logger.info("GTOmniVid services operational. Starting long-polling...")

    try:
        if settings.BOT_TOKEN:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        else:
            logger.info("Dry-run mode: Services initialized successfully without active Telegram token.")
    finally:
        logger.info("Shutting down GTOmniVid services...")
        await concurrency_controller.stop()
        await housekeeper.stop()
        if settings.BOT_TOKEN:
            await bot.session.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by user/signal.")
