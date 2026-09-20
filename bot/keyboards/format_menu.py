"""Dynamic inline keyboard generator for quality format selection in GTOmniVid.

Builds Telegram inline buttons displaying quality icons, resolution, estimated sizes in MB,
and Direct Stream Link buttons. Ensures callback data is safely under Telegram's 64-byte ceiling.
"""

import time
from typing import Dict, List, Optional
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from extractors.base import FormatOption, FormatTier


class FormatCallbackData(CallbackData, prefix="gtfmt"):
    """Compact callback data model guaranteeing <= 30 bytes."""
    action: str   # "dl" or "cancel"
    key: str      # Short cache key
    idx: int      # Index in format list (-1 for cancel)


# In-memory short cache mapping short_key -> (timestamp, formats, webpage_url, title)
_FORMAT_CACHE: Dict[str, tuple[float, List[FormatOption], str, str]] = {}


def cache_format_options(
    short_key: str,
    formats: List[FormatOption],
    webpage_url: str,
    title: str = "Media"
) -> None:
    """Stores extracted format options and media title for callback retrieval."""
    now = time.monotonic()
    _FORMAT_CACHE[short_key] = (now, formats, webpage_url, title)
    _prune_format_cache(now)


def get_cached_formats(short_key: str) -> Optional[tuple[List[FormatOption], str, str]]:
    """Retrieves cached formats, webpage_url, and title by short_key."""
    entry = _FORMAT_CACHE.get(short_key)
    if entry:
        return entry[1], entry[2], entry[3]
    return None


def _prune_format_cache(now: float) -> None:
    """Evicts cache entries older than 30 minutes."""
    stale = now - 1800.0
    keys_to_delete = [k for k, v in _FORMAT_CACHE.items() if v[0] < stale]
    for k in keys_to_delete:
        _FORMAT_CACHE.pop(k, None)


def build_format_keyboard(
    formats: List[FormatOption],
    short_key: str
) -> InlineKeyboardMarkup:
    """Constructs dynamic inline buttons for each quality tier in a clean compact grid."""
    buttons: List[List[InlineKeyboardButton]] = []
    video_row: List[InlineKeyboardButton] = []

    for idx, opt in enumerate(formats):
        if opt.tier == FormatTier.DIRECT and opt.direct_stream_url:
            if video_row:
                buttons.append(video_row)
                video_row = []
            buttons.append([
                InlineKeyboardButton(
                    text=opt.button_label,
                    url=opt.direct_stream_url
                )
            ])
        elif opt.tier == FormatTier.AUDIO:
            if video_row:
                buttons.append(video_row)
                video_row = []
            buttons.append([
                InlineKeyboardButton(
                    text=opt.button_label,
                    callback_data=FormatCallbackData(
                        action="dl",
                        key=short_key,
                        idx=idx
                    ).pack()
                )
            ])
        else:
            # Video resolution tier: pair into 2-column rows
            video_row.append(
                InlineKeyboardButton(
                    text=opt.button_label,
                    callback_data=FormatCallbackData(
                        action="dl",
                        key=short_key,
                        idx=idx
                    ).pack()
                )
            )
            if len(video_row) == 2:
                buttons.append(video_row)
                video_row = []

    if video_row:
        buttons.append(video_row)

    # Add Cancel button
    buttons.append([
        InlineKeyboardButton(
            text="❌ Cancel",
            callback_data=FormatCallbackData(
                action="cancel",
                key=short_key,
                idx=-1
            ).pack()
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
