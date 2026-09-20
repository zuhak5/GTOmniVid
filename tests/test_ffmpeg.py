"""Unit tests for Lossless FFmpeg Remuxer & ffprobe Inspector (media/ffmpeg.py)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from media.ffmpeg import FFmpegService, MediaStreamInfo, RemuxError


@pytest.mark.asyncio
async def test_remux_streams_command_synthesis(tmp_path: Path):
    """Verify FFmpeg remux uses -c copy and +faststart without shell=True."""
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    output = tmp_path / "output.mp4"
    video.write_bytes(b"dummy video")
    audio.write_bytes(b"dummy audio")

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = (b"", b"")

    async def fake_exec(*args, **kwargs):
        output.write_bytes(b"dummy remuxed")
        return mock_process

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec:
        result = await FFmpegService.remux_streams(video, audio, output)
        assert result == output

        mock_exec.assert_called_once()
        args, kwargs = mock_exec.call_args
        assert args[0] == "ffmpeg"
        assert "-c" in args
        c_idx = args.index("-c")
        assert args[c_idx + 1] == "copy"
        assert "+faststart" in args
        assert "shell" not in kwargs


@pytest.mark.asyncio
async def test_remux_streams_failure_raises_remux_error(tmp_path: Path):
    """Verify RemuxError is raised when FFmpeg returns non-zero exit code."""
    video = tmp_path / "video.mp4"
    output = tmp_path / "output.mp4"
    video.write_bytes(b"corrupt")

    mock_process = AsyncMock()
    mock_process.returncode = 1
    mock_process.communicate.return_value = (b"", b"Invalid data found when processing input")

    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        with pytest.raises(RemuxError, match="Lossless remux failed"):
            await FFmpegService.remux_streams(video, None, output)


@pytest.mark.asyncio
async def test_extract_thumbnail_command(tmp_path: Path):
    """Verify thumbnail command uses accurate seeking and single frame grab."""
    video = tmp_path / "video.mp4"
    thumb = tmp_path / "thumb.jpg"
    video.write_bytes(b"video")
    thumb.write_bytes(b"thumb")

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = (b"", b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        result = await FFmpegService.extract_thumbnail(video, thumb, timestamp_sec=2.5)
        assert result == thumb

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert "-ss" in args
        ss_idx = args.index("-ss")
        assert args[ss_idx + 1] == "2.5"
        assert "-vframes" in args


def test_parse_ffprobe_data(tmp_path: Path):
    """Verify ffprobe JSON parsing extracts width, height, duration, and codecs."""
    dummy_file = tmp_path / "test.mp4"
    dummy_file.write_bytes(b"dummy")

    raw_json = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080
            },
            {
                "codec_type": "audio",
                "codec_name": "aac"
            }
        ],
        "format": {
            "duration": "125.4",
            "size": "20971520"
        }
    }

    info = FFmpegService._parse_ffprobe_data(raw_json, dummy_file)
    assert info.width == 1920
    assert info.height == 1080
    assert info.duration_seconds == 125
    assert info.size_bytes == 20971520
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"


@pytest.mark.asyncio
async def test_extract_audio_lossless(tmp_path: Path):
    """Verify extract_audio attempts lossless -c:a copy first."""
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    video.write_bytes(b"dummy video data")

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = (b"", b"")

    async def fake_exec(*args, **kwargs):
        audio.write_bytes(b"dummy audio m4a")
        return mock_process

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec:
            result = await FFmpegService.extract_audio(video, audio)
            assert result == audio
            assert mock_exec.call_count == 1
            args = mock_exec.call_args[0]
            assert "-vn" in args
            assert "-c:a" in args
            ca_idx = args.index("-c:a")
            assert args[ca_idx + 1] == "copy"


@pytest.mark.asyncio
async def test_extract_audio_transcode_fallback(tmp_path: Path):
    """Verify extract_audio falls back to AAC transcode if copy fails."""
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    video.write_bytes(b"dummy video data")

    proc_fail = AsyncMock()
    proc_fail.returncode = 1
    proc_fail.communicate.return_value = (b"", b"Could not find tag for codec opus in stream")

    proc_succ = AsyncMock()
    proc_succ.returncode = 0
    proc_succ.communicate.return_value = (b"", b"")

    call_count = 0

    async def fake_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return proc_fail
        audio.write_bytes(b"dummy transcoded audio")
        return proc_succ

    with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as mock_exec:
            result = await FFmpegService.extract_audio(video, audio)
            assert result == audio
            assert mock_exec.call_count == 2
            # Second call must be AAC transcode
            args2 = mock_exec.call_args_list[1][0]
            assert "-vn" in args2
            ca_idx = args2.index("-c:a")
            assert args2[ca_idx + 1] == "aac"
            assert "-b:a" in args2

