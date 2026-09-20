"""Unit tests for format menu keyboards and callback data (bot/keyboards/)."""

import pytest
from aiogram.types import InlineKeyboardMarkup

from bot.keyboards.format_menu import (
    FormatCallbackData,
    build_format_keyboard,
    cache_format_options,
    get_cached_formats,
)
from extractors.base import FormatOption, FormatTier


def test_format_callback_data_serialization():
    """Verify callback data serializes to well under Telegram's 64-byte limit."""
    cb = FormatCallbackData(action="dl", key="a1b2c3d4", idx=2)
    packed = cb.pack()
    assert len(packed.encode("utf-8")) < 30
    assert packed.startswith("gtfmt:dl:a1b2c3d4:2")

    unpacked = FormatCallbackData.unpack(packed)
    assert unpacked.action == "dl"
    assert unpacked.key == "a1b2c3d4"
    assert unpacked.idx == 2


def test_build_format_keyboard():
    """Verify dynamic inline keyboard creates download buttons and direct link URL button."""
    formats = [
        FormatOption(
            format_id="136+140",
            tier=FormatTier.P720,
            resolution_label="720p HD",
            estimated_size_bytes=18 * 1024 * 1024
        ),
        FormatOption(
            format_id="18",
            tier=FormatTier.DIRECT,
            resolution_label="Direct Stream Link",
            direct_stream_url="https://direct.cdn/video.mp4"
        )
    ]

    kb = build_format_keyboard(formats, short_key="testkey1")
    assert isinstance(kb, InlineKeyboardMarkup)
    assert len(kb.inline_keyboard) == 3  # 720p, Direct link, Cancel

    # Button 1: Download callback
    row1 = kb.inline_keyboard[0][0]
    assert "720p HD" in row1.text
    assert row1.callback_data is not None

    # Button 2: Direct link URL button
    row2 = kb.inline_keyboard[1][0]
    assert "Direct Link" in row2.text
    assert row2.url == "https://direct.cdn/video.mp4"

    # Button 3: Cancel button
    row3 = kb.inline_keyboard[2][0]
    assert "Cancel" in row3.text


def test_format_options_cache():
    """Verify format caching and retrieval by short key, including title."""
    formats = [
        FormatOption(format_id="140", tier=FormatTier.AUDIO, resolution_label="Audio")
    ]
    cache_format_options("key_xyz", formats, "https://youtube.com/watch?v=123", "Sample Video Title")

    entry = get_cached_formats("key_xyz")
    assert entry is not None
    cached_formats, url, title = entry
    assert len(cached_formats) == 1
    assert url == "https://youtube.com/watch?v=123"
    assert title == "Sample Video Title"

    assert get_cached_formats("non_existent") is None
