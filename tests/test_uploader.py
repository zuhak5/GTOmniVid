"""Unit tests for TelegramUploader and ThrottledProgressBar (media/uploader.py)."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from aiogram import Bot

from core.quota import QuotaLedger
from media.ffmpeg import MediaStreamInfo
from media.uploader import TelegramUploader, ThrottledProgressBar, MAX_TELEGRAM_BOT_FILE_SIZE_BYTES


@pytest.mark.asyncio
async def test_throttled_progress_bar_rate_limits():
    """Verify progress edits are debounced to respect 3-second anti-flood window."""
    mock_bot = MagicMock(spec=Bot)
    mock_bot.edit_message_text = AsyncMock()

    progress = ThrottledProgressBar(
        bot=mock_bot,
        chat_id=1234,
        message_id=5678,
        throttle_interval_seconds=1.0
    )

    # 1st update: should execute immediately
    await progress.update(current_bytes=1000, total_bytes=10000, status_title="Downloading")
    assert mock_bot.edit_message_text.call_count == 1

    # 2nd update immediately (< 1.0s): must be throttled/suppressed
    await progress.update(current_bytes=2000, total_bytes=10000, status_title="Downloading")
    assert mock_bot.edit_message_text.call_count == 1

    # 3rd update after throttle duration: should execute
    await asyncio.sleep(1.05)
    await progress.update(current_bytes=5000, total_bytes=10000, status_title="Downloading")
    assert mock_bot.edit_message_text.call_count == 2


@pytest.mark.asyncio
async def test_uploader_rejects_files_over_50mb(tmp_path: Path):
    """Verify uploader halts and warns user if file exceeds Telegram's 50 MB limit."""
    mock_bot = MagicMock(spec=Bot)
    mock_bot.edit_message_text = AsyncMock()
    mock_ledger = MagicMock(spec=QuotaLedger)

    uploader = TelegramUploader(bot=mock_bot, quota_ledger=mock_ledger)

    oversized_file = tmp_path / "oversized.mp4"
    # Create sparse or mock file of 52 MB
    oversized_file.write_bytes(b"0")
    
    # Mock stat().st_size to return 52 MB
    mock_stat = MagicMock()
    mock_stat.st_size = 52 * 1024 * 1024

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "stat", lambda self: mock_stat)
        mp.setattr(Path, "exists", lambda self: True)

        delivered = await uploader.deliver_media(
            chat_id=123,
            user_id=456,
            progress_msg_id=789,
            media_path=oversized_file,
            thumbnail_path=None,
            stream_info=None,
            title="Oversized Video",
            platform="youtube",
            is_audio=False
        )

        assert delivered is False
        mock_bot.edit_message_text.assert_called_once()
        args = mock_bot.edit_message_text.call_args[1]["text"]
        assert "File Too Large for Bot Upload" in args
        # Ledger must NOT have been called
        mock_ledger.record_egress.assert_not_called()


@pytest.mark.asyncio
async def test_uploader_successful_video_delivery(tmp_path: Path):
    """Verify video upload executes send_video, records egress, and increments quota."""
    mock_bot = MagicMock(spec=Bot)
    mock_bot.edit_message_text = AsyncMock()
    mock_bot.send_video = AsyncMock()
    mock_bot.delete_message = AsyncMock()

    mock_ledger = MagicMock(spec=QuotaLedger)
    mock_ledger.record_egress = AsyncMock()
    mock_ledger.increment_user_quota = AsyncMock()

    uploader = TelegramUploader(bot=mock_bot, quota_ledger=mock_ledger)

    video_file = tmp_path / "valid_video.mp4"
    video_file.write_bytes(b"x" * (15 * 1024 * 1024))  # 15 MB

    stream_info = MediaStreamInfo(width=1280, height=720, duration_seconds=45)

    delivered = await uploader.deliver_media(
        chat_id=100,
        user_id=200,
        progress_msg_id=300,
        media_path=video_file,
        thumbnail_path=None,
        stream_info=stream_info,
        title="Valid Video",
        platform="youtube",
        is_audio=False
    )

    assert delivered is True
    mock_bot.send_video.assert_called_once()
    mock_ledger.record_egress.assert_called_once_with(
        user_id=200,
        bytes_sent=15 * 1024 * 1024,
        file_type="video",
        platform="youtube",
        delivery_mode="upload"
    )
    mock_ledger.increment_user_quota.assert_called_once_with(user_id=200)


@pytest.mark.asyncio
async def test_uploader_fallback_to_document_on_send_video_failure(tmp_path: Path):
    """Verify uploader falls back to send_document if Telegram send_video raises an error."""
    mock_bot = MagicMock(spec=Bot)
    mock_bot.edit_message_text = AsyncMock()
    mock_bot.send_video = AsyncMock(side_effect=Exception("TelegramBadRequest: wrong format"))
    mock_bot.send_document = AsyncMock()
    mock_bot.delete_message = AsyncMock()

    mock_ledger = MagicMock(spec=QuotaLedger)
    mock_ledger.record_egress = AsyncMock()
    mock_ledger.increment_user_quota = AsyncMock()

    uploader = TelegramUploader(bot=mock_bot, quota_ledger=mock_ledger)

    video_file = tmp_path / "exotic_video.mkv"
    video_file.write_bytes(b"v" * (5 * 1024 * 1024))

    delivered = await uploader.deliver_media(
        chat_id=100,
        user_id=200,
        progress_msg_id=300,
        media_path=video_file,
        thumbnail_path=None,
        stream_info=None,
        title="Exotic MKV Video",
        platform="youtube",
        is_audio=False
    )

    assert delivered is True
    mock_bot.send_video.assert_called_once()
    mock_bot.send_document.assert_called_once()
    mock_ledger.record_egress.assert_called_once()


@pytest.mark.asyncio
async def test_uploader_audio_title_length_truncation(tmp_path: Path):
    """Verify audio track title is safely truncated to 64 characters for Telegram Bot API."""
    mock_bot = MagicMock(spec=Bot)
    mock_bot.edit_message_text = AsyncMock()
    mock_bot.send_audio = AsyncMock()
    mock_bot.delete_message = AsyncMock()

    mock_ledger = MagicMock(spec=QuotaLedger)
    mock_ledger.record_egress = AsyncMock()
    mock_ledger.increment_user_quota = AsyncMock()

    uploader = TelegramUploader(bot=mock_bot, quota_ledger=mock_ledger)

    audio_file = tmp_path / "audio.m4a"
    audio_file.write_bytes(b"a" * (2 * 1024 * 1024))

    long_title = "A" * 150  # 150 characters long
    delivered = await uploader.deliver_media(
        chat_id=100,
        user_id=200,
        progress_msg_id=300,
        media_path=audio_file,
        thumbnail_path=None,
        stream_info=None,
        title=long_title,
        platform="youtube",
        is_audio=True
    )

    assert delivered is True
    mock_bot.send_audio.assert_called_once()
    passed_title = mock_bot.send_audio.call_args[1]["title"]
    assert len(passed_title) <= 64

