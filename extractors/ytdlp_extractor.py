"""Async yt-dlp Wrapper & Format Normalization Engine for GTOmniVid.

Extracts rich stream metadata without downloading, aggregates complex platform format
codes into clean standardized tiers (1080p, 720p, 480p, Audio M4A, Direct Link),
and calculates accurate file sizes and bitrates.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yt_dlp

from core.security import identify_platform
from extractors.base import FormatOption, FormatTier, MediaMetadata
from extractors.platform_rules import get_ytdlp_base_options

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when media metadata extraction fails."""
    pass


class YtdlpExtractor:
    """Non-blocking async wrapper around yt-dlp."""

    async def extract_info(self, url: str) -> MediaMetadata:
        """Asynchronously probes media metadata without downloading binary streams."""
        platform = identify_platform(url)
        opts = get_ytdlp_base_options(platform)

        try:
            # Run blocking extraction in a separate thread to avoid freezing event loop
            raw_info = await asyncio.to_thread(self._run_ytdlp_extract, url, opts)
        except Exception as err:
            logger.error("yt-dlp extraction failed for %s: %s", url, err)
            raise ExtractionError(f"Failed to extract media information: {err}") from err

        if not raw_info:
            raise ExtractionError("Extractor returned empty metadata.")

        return self.normalize_metadata(url, platform, raw_info)

    def _run_ytdlp_extract(self, url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous call to yt_dlp.YoutubeDL."""
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return ydl.sanitize_info(info) if info else {}

    def normalize_metadata(self, url: str, platform: str, raw_info: Dict[str, Any]) -> MediaMetadata:
        """Parses raw yt-dlp dictionary into clean MediaMetadata and normalized FormatOptions."""
        # Handle playlist or multi-entry containers (e.g. YouTube mix, Instagram carousel)
        if "entries" in raw_info and isinstance(raw_info["entries"], list):
            valid_entries = [e for e in raw_info["entries"] if e and isinstance(e, dict)]
            if valid_entries:
                raw_info = valid_entries[0]

        title = raw_info.get("title") or "Media"
        duration = int(raw_info.get("duration") or 0)
        thumbnail = raw_info.get("thumbnail")
        uploader = raw_info.get("uploader") or raw_info.get("channel")

        raw_formats: List[Dict[str, Any]] = raw_info.get("formats") or []
        normalized_formats = self.aggregate_formats(raw_formats, duration, platform)

        return MediaMetadata(
            extractor=platform,
            webpage_url=url,
            title=title,
            duration_seconds=duration,
            thumbnail_url=thumbnail,
            uploader=uploader,
            formats=normalized_formats,
        )

    def aggregate_formats(
        self,
        raw_formats: List[Dict[str, Any]],
        duration: int,
        platform: str
    ) -> List[FormatOption]:
        """Normalizes diverse platform stream matrices into standard GTOmniVid tiers."""
        if not raw_formats:
            return []

        # 1. Locate best audio stream (favoring AAC / M4A for zero-transcoding remuxing)
        best_audio = self._find_best_audio(raw_formats, duration)
        audio_size = best_audio.get("filesize_calc", 0) if best_audio else 0

        # Standard resolution brackets ordered from highest to lowest:
        # (threshold, FormatTier, default_label)
        BRACKETS = [
            (2160, FormatTier.P2160, "4K 2160p"),
            (1440, FormatTier.P1440, "2K 1440p"),
            (1080, FormatTier.P1080, "1080p Full HD"),
            (720, FormatTier.P720, "720p HD"),
            (480, FormatTier.P480, "480p SD"),
            (360, FormatTier.P360, "360p"),
            (240, FormatTier.P240, "240p"),
            (144, FormatTier.P144, "144p"),
        ]

        def _is_better_stream(new_f: Dict[str, Any], current_f: Optional[Dict[str, Any]]) -> bool:
            if current_f is None:
                return True
            new_is_mp4 = new_f.get("ext") == "mp4"
            cur_is_mp4 = current_f.get("ext") == "mp4"
            if new_is_mp4 and not cur_is_mp4:
                return True
            if cur_is_mp4 and not new_is_mp4:
                return False
            return new_f.get("filesize_calc", 0) > current_f.get("filesize_calc", 0)

        # Mapping: tier -> (best_format_dict, label_string)
        tier_formats: Dict[FormatTier, tuple[Dict[str, Any], str]] = {}
        direct_progressive: Optional[Dict[str, Any]] = None

        for f in raw_formats:
            vcodec = f.get("vcodec") or "none"
            acodec = f.get("acodec") or "none"
            ext = f.get("ext") or "mp4"

            if vcodec == "none":
                continue  # Audio-only stream

            # Enrich format with calculated size
            f["filesize_calc"] = self._calculate_stream_size(f, duration)

            # Check for progressive stream (contains both video and audio)
            is_progressive = acodec != "none"
            if is_progressive and f.get("url"):
                if direct_progressive is None or (f.get("filesize_calc", 0) > direct_progressive.get("filesize_calc", 0)):
                    direct_progressive = f

            # Calculate effective resolution (supports landscape and portrait/reels)
            h = f.get("height") or 0
            w = f.get("width") or 0
            eff_res = min(h, w) if (h > 0 and w > 0) else (h or w or 0)

            if eff_res <= 0:
                continue

            # Classify into standard tier
            for threshold, tier, default_label in BRACKETS:
                if eff_res >= threshold:
                    cur_stream = tier_formats.get(tier, (None, ""))[0]
                    if _is_better_stream(f, cur_stream):
                        label = f"{eff_res}p" if eff_res not in (2160, 1440, 1080, 720, 480) else default_label
                        tier_formats[tier] = (f, label)
                    break
            else:
                # Below 144p but > 0: classify as 144p
                cur_stream = tier_formats.get(FormatTier.P144, (None, ""))[0]
                if _is_better_stream(f, cur_stream):
                    tier_formats[FormatTier.P144] = (f, f"{eff_res}p" if eff_res != 144 else "144p")

        options: List[FormatOption] = []

        # Build options for each resolution tier (highest to lowest)
        for threshold, tier, default_label in BRACKETS:
            if tier not in tier_formats:
                continue

            v_fmt, label = tier_formats[tier]
            requires_remux = v_fmt.get("acodec") == "none"
            comb_size = v_fmt["filesize_calc"] + (audio_size if requires_remux else 0)

            # Safeguard Telegram 50 MB limit: only offer upload options <= 45 MB
            if comb_size > 45 * 1024 * 1024:
                continue

            fmt_id = (
                f"{v_fmt['format_id']}+{best_audio['format_id']}"
                if (requires_remux and best_audio)
                else str(v_fmt["format_id"])
            )
            options.append(FormatOption(
                format_id=fmt_id,
                tier=tier,
                resolution_label=label,
                ext="mp4",
                estimated_size_bytes=comb_size,
                video_url=v_fmt.get("url"),
                audio_url=best_audio.get("url") if best_audio else None,
                requires_remux=requires_remux,
            ))

        # Fallback if no specific tiers matched: use best available progressive or DASH stream
        if not options and raw_formats:
            best_fmt = raw_formats[-1]
            fmt_size = self._calculate_stream_size(best_fmt, duration)
            options.append(FormatOption(
                format_id=str(best_fmt.get("format_id", "best")),
                tier=FormatTier.P720,
                resolution_label="Standard Quality",
                ext=best_fmt.get("ext", "mp4"),
                estimated_size_bytes=fmt_size,
                video_url=best_fmt.get("url"),
                requires_remux=best_fmt.get("acodec") == "none",
            ))

        # Tier: Audio (M4A) — Lossless native audio stream or extracted from progressive stream
        if best_audio:
            options.append(FormatOption(
                format_id=str(best_audio["format_id"]),
                tier=FormatTier.AUDIO,
                resolution_label="Audio (M4A)",
                ext=best_audio.get("ext", "m4a"),
                estimated_size_bytes=audio_size,
                audio_url=best_audio.get("url"),
                requires_remux=False,
            ))
        elif raw_formats:
            # Check if all streams are explicitly marked silent (vcodec present, acodec == "none")
            is_silent = all((f.get("acodec") == "none" and f.get("vcodec") != "none") for f in raw_formats)
            if not is_silent:
                est_audio_size = int((128 * 1000 / 8) * duration) if duration > 0 else 2 * 1024 * 1024
                options.append(FormatOption(
                    format_id="bestaudio/best",
                    tier=FormatTier.AUDIO,
                    resolution_label="Audio (M4A)",
                    ext="m4a",
                    estimated_size_bytes=est_audio_size,
                    requires_remux=True,
                ))
        elif not raw_formats:
            est_audio_size = int((128 * 1000 / 8) * duration) if duration > 0 else 2 * 1024 * 1024
            options.append(FormatOption(
                format_id="bestaudio/best",
                tier=FormatTier.AUDIO,
                resolution_label="Audio (M4A)",
                ext="m4a",
                estimated_size_bytes=est_audio_size,
                requires_remux=True,
            ))

        # Tier: Direct Stream Link (0 MB Egress)
        # Compatible with TikTok, Facebook, and verified progressive URLs
        if direct_progressive and direct_progressive.get("url"):
            options.append(FormatOption(
                format_id=str(direct_progressive["format_id"]),
                tier=FormatTier.DIRECT,
                resolution_label="Direct Stream Link",
                ext="mp4",
                estimated_size_bytes=0,  # Zero VM egress consumed!
                direct_stream_url=direct_progressive.get("url"),
                requires_remux=False,
            ))

        return options

    def _find_best_audio(
        self,
        raw_formats: List[Dict[str, Any]],
        duration: int
    ) -> Optional[Dict[str, Any]]:
        """Identifies highest quality audio track, prioritizing AAC/M4A containers."""
        best_m4a: Optional[Dict[str, Any]] = None
        best_other: Optional[Dict[str, Any]] = None

        for f in raw_formats:
            vcodec = f.get("vcodec") or "none"
            acodec = f.get("acodec") or "none"
            ext = f.get("ext") or ""

            if vcodec != "none" or acodec == "none":
                continue  # Skip video or silent streams

            f["filesize_calc"] = self._calculate_stream_size(f, duration)
            abr = f.get("abr") or 0

            if ext in ("m4a", "mp4", "aac"):
                if best_m4a is None or abr > (best_m4a.get("abr") or 0):
                    best_m4a = f
            else:
                if best_other is None or abr > (best_other.get("abr") or 0):
                    best_other = f

        chosen = best_m4a or best_other
        return chosen

    def _calculate_stream_size(self, stream_info: Dict[str, Any], duration: int) -> int:
        """Calculates accurate stream byte size from filesize, approx filesize, or bitrate."""
        if stream_info.get("filesize"):
            return int(stream_info["filesize"])
        if stream_info.get("filesize_approx"):
            return int(stream_info["filesize_approx"])

        # Estimate from total bitrate (tbr in kbps) or vbr/abr and duration
        bitrate = stream_info.get("tbr") or ((stream_info.get("vbr") or 0) + (stream_info.get("abr") or 0))
        if bitrate and duration > 0:
            # (bitrate kbps * 1000 / 8) * duration
            return int((bitrate * 1000 / 8) * duration)

        return 0

    async def download_stream(
        self,
        url: str,
        format_selector: str,
        output_dir: Path,
        progress_hook: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Path:
        """Asynchronously downloads the chosen media stream into the target workspace."""
        platform = identify_platform(url)
        opts = get_ytdlp_base_options(platform)
        # Ensure robust format selection across all platforms and environments:
        # If ffmpeg is absent or format IDs are ephemeral (e.g. Facebook/Instagram DASH),
        # gracefully fallback to progressive single-container streams (hd/sd/best).
        import shutil
        has_ffmpeg = shutil.which("ffmpeg") is not None

        if not has_ffmpeg and "+" in format_selector:
            if platform == "facebook":
                effective_format = "hd/sd/best[ext=mp4]/best"
            else:
                effective_format = "best[ext=mp4]/best"
        else:
            is_audio_selector = (
                "audio" in format_selector
                or format_selector in ("ba", "ba/b")
                or format_selector.endswith("a")
            )
            if platform == "facebook":
                if is_audio_selector:
                    effective_format = f"{format_selector}/bestaudio/hd/sd/best[ext=mp4]/best"
                else:
                    effective_format = f"{format_selector}/hd/sd/best[ext=mp4]/best"
            elif is_audio_selector:
                effective_format = f"{format_selector}/bestaudio/best"
            else:
                effective_format = f"{format_selector}/best[ext=mp4]/best"

        opts.update({
            "skip_download": False,
            "format": effective_format,
            "paths": {"home": str(output_dir)},
            "outtmpl": {"default": "%(id)s_%(format_id)s.%(ext)s"},
            "noprogress": True,
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        logger.info("Starting yt-dlp download: requested=%s, effective=%s, output_dir=%s", format_selector, effective_format, output_dir)
        try:
            await asyncio.to_thread(self._run_ytdlp_download, url, opts)
        except Exception as err:
            logger.error("Download failed for %s: %s", url, err)
            raise ExtractionError(f"Stream download failed: {err}") from err

        # Identify downloaded file in output_dir (filter out .part, .temp, .ytdl, metadata files)
        ignored_exts = {".part", ".ytdl", ".temp", ".aria2", ".json", ".txt", ".vtt", ".srt"}
        valid_files = [
            f for f in output_dir.glob("*.*")
            if f.is_file() and f.suffix.lower() not in ignored_exts and f.stat().st_size > 0
        ]
        if not valid_files:
            # Fallback to any non-empty file
            valid_files = [f for f in output_dir.glob("*.*") if f.is_file() and f.stat().st_size > 0]
        if not valid_files:
            raise ExtractionError(f"No file produced in {output_dir} after download.")

        # Pick the largest completed file (prevents picking thumbnail or metadata leftover)
        valid_files.sort(key=lambda x: x.stat().st_size, reverse=True)
        return valid_files[0]

    def _run_ytdlp_download(self, url: str, opts: Dict[str, Any]) -> None:
        """Synchronous call to yt_dlp.YoutubeDL for binary downloading."""
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
