"""Middleware package for GTOmniVid."""

from bot.middlewares.error_handler import GlobalErrorHandler
from bot.middlewares.rate_limit import AntiFloodMiddleware

__all__ = ["AntiFloodMiddleware", "GlobalErrorHandler"]
