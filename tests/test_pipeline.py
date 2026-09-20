"""Unit tests for MediaPipeline orchestrator (media/pipeline.py)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from extractors.base import FormatOption, FormatTier
from media.ffmpeg import MediaStreamInfo
from media.pipeline import MediaPipeline


@pytest.mark.asyncio
async def test_pipeline_direct_stream_link():
    """Verify Direct Stream Link mode bypasses disk download and yields direct URL."""
    pipeline = MediaPipeline()
    format_direct = FormatOption(
        format_id="18",
        tier=FormatTier.DIRECT,
        resolution_label="Direct Stream Link",
        direct_stream_url="https://direct.cdn.net/video.mp4"
    )

    async with pipeline.process_job(
        job_id="direct_123",
        webpage_url="https://youtube.com/watch?v=123",
        selected_format=format_direct
    ) as result:
        assert result.is_direct_link is True
        assert result.direct_url == "https://direct.cdn.net/video.mp4"
        assert result.media_path is None


@pytest.mark.asyncio
async def test_pipeline_video_processing_and_cleanup(tmp_path: Path):
    """Verify video pipeline downloads, remuxes, probes, and guarantees workspace cleanup."""
    mock_extractor = MagicMock()
    mock_ffmpeg = MagicMock()

    pipeline = MediaPipeline(extractor=mock_extractor, ffmpeg_service=mock_ffmpeg)

    # Format setup
    fmt_720 = FormatOption(
        format_id="136+140",
        tier=FormatTier.P720,
        resolution_label="720p HD",
        ext="mp4",
        estimated_size_bytes=15 * 1024 * 1024,
        requires_remux=True
    )

    captured_workspace = None

    # Define mock behaviors
    async def mock_download(url, format_selector, output_dir):
        nonlocal captured_workspace
        captured_workspace = output_dir.parent
        dummy_file = output_dir / "raw_stream.mp4"
        dummy_file.write_bytes(b"dummy raw stream")
        return dummy_file

    async def mock_remux(video_path, audio_path, output_path):
        output_path.write_bytes(b"dummy remuxed video")
        return output_path

    async def mock_thumb(video_path, thumb_path, timestamp_sec=1.0):
        thumb_path.write_bytes(b"dummy thumbnail")
        return thumb_path

    async def mock_inspect(file_path):
        return MediaStreamInfo(width=1280, height=720, duration_seconds=60, size_bytes=15000000)

    mock_extractor.download_stream = AsyncMock(side_effect=mock_download)
    mock_ffmpeg.remux_streams = AsyncMock(side_effect=mock_remux)
    mock_ffmpeg.extract_thumbnail = AsyncMock(side_effect=mock_thumb)
    mock_ffmpeg.inspect_media = AsyncMock(side_effect=mock_inspect)

    async with pipeline.process_job(
        job_id="test_video_job",
        webpage_url="https://youtube.com/watch?v=abc",
        selected_format=fmt_720
    ) as result:
        assert result.is_direct_link is False
        assert result.is_audio is False
        assert result.media_path is not None
        assert result.media_path.exists()
        assert result.thumbnail_path is not None
        assert result.thumbnail_path.exists()
        assert result.stream_info.width == 1280
        assert result.stream_info.height == 720

    # Verify RAII workspace cleanup after exiting context manager
    assert captured_workspace is not None
    assert not captured_workspace.exists()


@pytest.mark.asyncio
async def test_pipeline_audio_processing(tmp_path: Path):
    """Verify audio pipeline generates audio.m4a without video remuxing."""
    mock_extractor = MagicMock()
    pipeline = MediaPipeline(extractor=mock_extractor)

    fmt_audio = FormatOption(
        format_id="140",
        tier=FormatTier.AUDIO,
        resolution_label="Audio (M4A)",
        ext="m4a",
        estimated_size_bytes=3 * 1024 * 1024
    )

    async def mock_audio_download(url, format_selector, output_dir):
        dummy_audio = output_dir / "audio_track.m4a"
        dummy_audio.write_bytes(b"dummy audio stream")
        return dummy_audio

    mock_extractor.download_stream = AsyncMock(side_effect=mock_audio_download)

    async with pipeline.process_job(
        job_id="test_audio_job",
        webpage_url="https://youtube.com/watch?v=audio123",
        selected_format=fmt_audio
    ) as result:
        assert result.is_audio is True
        assert result.is_direct_link is False
        assert result.media_path.name == "audio.m4a"
        assert result.stream_info.audio_codec == "m4a"
