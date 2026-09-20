"""Handlers package for GTOmniVid."""

from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.commands import router as commands_router
from bot.handlers.media import router as media_router

__all__ = ["commands_router", "media_router", "callbacks_router"]
