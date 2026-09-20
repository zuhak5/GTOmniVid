"""Aiogram 3.x bot and dispatcher setup for GTOmniVid."""

import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import callbacks_router, commands_router, media_router
from bot.middlewares.error_handler import GlobalErrorHandler
from bot.middlewares.rate_limit import AntiFloodMiddleware
from config.settings import Settings, get_settings
from core.queue import ConcurrencyController
from core.quota import QuotaLedger
from extractors.ytdlp_extractor import YtdlpExtractor

logger = logging.getLogger(__name__)


def create_bot_and_dispatcher(
    settings: Settings,
    quota_ledger: QuotaLedger,
    concurrency_controller: ConcurrencyController,
    extractor: YtdlpExtractor
) -> tuple[Bot, Dispatcher]:
    """Configures Bot instance, Dispatcher, middlewares, and routers."""
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Use MemoryStorage for FSM (minimal memory footprint on 1GB e2-micro)
    dp = Dispatcher(storage=MemoryStorage())

    # Register Middlewares
    anti_flood = AntiFloodMiddleware(min_interval_seconds=2.0)
    error_handler = GlobalErrorHandler()

    dp.message.middleware(anti_flood)
    dp.callback_query.middleware(anti_flood)
    dp.message.middleware(error_handler)
    dp.callback_query.middleware(error_handler)

    # Register Dependency Injections into Dispatcher workflow data
    dp["settings"] = settings
    dp["quota_ledger"] = quota_ledger
    dp["concurrency_controller"] = concurrency_controller
    dp["extractor"] = extractor

    # Register Routers
    dp.include_router(commands_router)
    dp.include_router(callbacks_router)
    dp.include_router(media_router)

    logger.info("Bot and Dispatcher successfully initialized with middlewares and routers.")
    return bot, dp
