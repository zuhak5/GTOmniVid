"""Data models and contracts for media format extraction and tier normalization."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class FormatTier(str, Enum):
    """Normalized quality and stream tiers for GTOmniVid."""
    P2160 = "2160p"
    P1440 = "1440p"
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"
    P360 = "360p"
    P240 = "240p"
    P144 = "144p"
    AUDIO = "audio"
    DIRECT = "direct_stream"


class FormatOption(BaseModel):
    """Normalized stream option presented to the user in Telegram."""
    format_id: str = Field(description="yt-dlp format selector, e.g. '137+140' or 'best'")
    tier: FormatTier = Field(description="Normalized quality tier")
    resolution_label: str = Field(description="Human-readable label, e.g. '720p HD'")
    ext: str = Field(default="mp4", description="Output container extension (mp4, m4a)")
    estimated_size_bytes: int = Field(default=0, description="Estimated total bytes for download")
    video_url: Optional[str] = Field(default=None, description="Direct video stream CDN URL")
    audio_url: Optional[str] = Field(default=None, description="Direct audio stream CDN URL")
    direct_stream_url: Optional[str] = Field(default=None, description="Direct playable progressive stream URL")
    requires_remux: bool = Field(default=False, description="True if video and audio must be merged with -c copy")

    @property
    def size_mb(self) -> float:
        """Returns estimated file size in megabytes."""
        return round(self.estimated_size_bytes / (1024 * 1024), 1)

    @property
    def button_label(self) -> str:
        """Constructs Telegram inline keyboard button text."""
        if self.tier == FormatTier.DIRECT:
            return "🌐 Direct Link (0MB Egress)"
        if self.tier == FormatTier.AUDIO:
            size_str = f"~{self.size_mb}MB" if self.estimated_size_bytes > 0 else "N/A"
            return f"🎵 {self.resolution_label} ({size_str})"
        
        size_str = f"~{self.size_mb}MB" if self.estimated_size_bytes > 0 else "N/A"
        return f"📹 {self.resolution_label} ({size_str})"


class MediaMetadata(BaseModel):
    """Canonical media metadata representation extracted from source platforms."""
    extractor: str = Field(description="Platform name: youtube, tiktok, instagram, facebook")
    webpage_url: str = Field(description="Original user submitted URL")
    title: str = Field(default="Media", description="Title or caption of the media")
    duration_seconds: int = Field(default=0, description="Media duration in seconds")
    thumbnail_url: Optional[str] = Field(default=None, description="Platform preview thumbnail URL")
    uploader: Optional[str] = Field(default=None, description="Channel, user, or creator name")
    formats: List[FormatOption] = Field(default_factory=list, description="List of normalized format tiers available")
