"""Master Media Pipeline Orchestrator for GTOmniVid.

Coordinates:
1. Ephemeral RAII workspace allocation (/tmp/tg_bot/<uuid>).
2. Non-blocking stream download via yt-dlp.
3. Zero-CPU lossless stream copy remuxing (-c copy -movflags +faststart) via FFmpeg.
4. Frame-accurate thumbnail extraction and stream metric probing via ffprobe.
5. Guaranteed post-upload cleanup.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Callable, Dict, Optional

from extractors.base import FormatOption, FormatTier
from extractors.ytdlp_extractor import YtdlpExtractor
from media.ffmpeg import FFmpegService, MediaStreamInfo
from storage.workspace import JobWorkspace

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Artifacts and metrics produced by media processing."""
    media_path: Optional[Path] = None
    thumbnail_path: Optional[Path] = None
    stream_info: Optional[MediaStreamInfo] = None
    is_audio: bool = False
    is_direct_link: bool = False
    direct_url: Optional[str] = None


class MediaPipeline:
    """End-to-end orchestrator for media download, remuxing, and preparation."""

    def __init__(
        self,
        extractor: Optional[YtdlpExtractor] = None,
        ffmpeg_service: Optional[FFmpegService] = None
    ) -> None:
        self.extractor = extractor or YtdlpExtractor()
        self.ffmpeg = ffmpeg_service or FFmpegService()

    @asynccontextmanager
    async def process_job(
        self,
        job_id: str,
        webpage_url: str,
        selected_format: FormatOption,
        cancel_event: Optional[asyncio.Event] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> AsyncGenerator[PipelineResult, None]:
        """Context manager that runs the media pipeline and guarantees cleanup upon completion."""
        # 1. Handle Direct Stream Link Mode (0 MB VM egress)
        if selected_format.tier == FormatTier.DIRECT:
            logger.info("Executing Direct Stream Link mode (0 MB egress) for job %s", job_id)
            yield PipelineResult(
                is_direct_link=True,
                direct_url=selected_format.direct_stream_url
            )
            return

        # 2. Allocate isolated RAII workspace
        async with JobWorkspace(job_id=job_id) as workspace_dir:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Job was cancelled before downloading.")

            if progress_callback:
                progress_callback("⏳ Initiating download from platform CDN...")

            # 3. Download media stream(s)
            download_dir = workspace_dir / "download"
            download_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Downloading format '%s' for job %s", selected_format.format_id, job_id)
            downloaded_file = await self.extractor.download_stream(
                url=webpage_url,
                format_selector=selected_format.format_id,
                output_dir=download_dir
            )

            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Job was cancelled after download.")

            # 4. Handle Lossless Stream Remuxing / Atom Relocation
            final_media_path: Path
            is_audio = selected_format.tier == FormatTier.AUDIO

            if is_audio:
                # Audio-only stream
                final_media_path = workspace_dir / f"audio.{selected_format.ext}"
                if downloaded_file != final_media_path:
                    downloaded_file.rename(final_media_path)
            else:
                # Video stream: enforce -c copy -movflags +faststart for native Telegram streaming
                remux_target = workspace_dir / "output.mp4"
                if progress_callback:
                    progress_callback("⚙️ Finalizing media streams (lossless stream copy)...")

                try:
                    final_media_path = await self.ffmpeg.remux_streams(
                        video_path=downloaded_file,
                        audio_path=None,  # yt-dlp combines or downloads single container
                        output_path=remux_target
                    )
                except Exception as remux_err:
                    logger.warning("Remux failed or not needed, using original: %s", remux_err)
                    final_media_path = downloaded_file

            # 5. Extract thumbnail and stream metrics for video
            thumb_path: Optional[Path] = None
            info: Optional[MediaStreamInfo] = None

            if not is_audio:
                target_thumb = workspace_dir / "thumb.jpg"
                thumb_path = await self.ffmpeg.extract_thumbnail(final_media_path, target_thumb)
                info = await self.ffmpeg.inspect_media(final_media_path)
            else:
                info = MediaStreamInfo(
                    duration_seconds=0,
                    size_bytes=final_media_path.stat().st_size if final_media_path.exists() else 0,
                    audio_codec=selected_format.ext
                )

            # 6. Yield prepared artifacts to caller (e.g. Telegram Uploader)
            yield PipelineResult(
                media_path=final_media_path,
                thumbnail_path=thumb_path if (thumb_path and thumb_path.exists()) else None,
                stream_info=info,
                is_audio=is_audio,
                is_direct_link=False
            )
            # Upon exiting this context block, JobWorkspace automatically purges workspace_dir!
