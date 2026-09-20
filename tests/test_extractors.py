"""Unit tests for Metadata Extraction & Format Tier Normalizer (extractors/)."""

import pytest
from extractors.base import FormatOption, FormatTier, MediaMetadata
from extractors.platform_rules import get_ytdlp_base_options
from extractors.ytdlp_extractor import YtdlpExtractor


def test_format_option_model_and_labels():
    """Verify FormatOption button labels and size conversions."""
    opt_video = FormatOption(
        format_id="136+140",
        tier=FormatTier.P720,
        resolution_label="720p HD",
        ext="mp4",
        estimated_size_bytes=18 * 1024 * 1024,
        requires_remux=True
    )
    assert opt_video.size_mb == 18.0
    assert opt_video.button_label == "📹 720p HD (~18.0MB)"

    opt_audio = FormatOption(
        format_id="140",
        tier=FormatTier.AUDIO,
        resolution_label="Audio (M4A)",
        ext="m4a",
        estimated_size_bytes=int(3.4 * 1024 * 1024),
        requires_remux=False
    )
    assert opt_audio.size_mb == 3.4
    assert opt_audio.button_label == "🎵 Audio (M4A) (~3.4MB)"

    opt_direct = FormatOption(
        format_id="18",
        tier=FormatTier.DIRECT,
        resolution_label="Direct Stream Link",
        ext="mp4",
        estimated_size_bytes=0,
        direct_stream_url="https://direct.cdn/video.mp4"
    )
    assert opt_direct.button_label == "🌐 Direct Link (0MB Egress)"


def test_youtube_dash_aggregation():
    """Verify adaptive DASH streams are paired and sizes aggregated."""
    extractor = YtdlpExtractor()
    duration = 60

    # Mock raw yt-dlp formats
    raw_formats = [
        # Audio track (128kbps AAC)
        {
            "format_id": "140",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "abr": 128,
            "ext": "m4a",
            "filesize": 2 * 1024 * 1024,
            "url": "https://googlevideo.com/audio"
        },
        # 1080p DASH Video (no audio)
        {
            "format_id": "137",
            "vcodec": "avc1.640028",
            "acodec": "none",
            "height": 1080,
            "ext": "mp4",
            "filesize": 25 * 1024 * 1024,
            "url": "https://googlevideo.com/1080p"
        },
        # 720p DASH Video (no audio)
        {
            "format_id": "136",
            "vcodec": "avc1.4d401f",
            "acodec": "none",
            "height": 720,
            "ext": "mp4",
            "filesize": 15 * 1024 * 1024,
            "url": "https://googlevideo.com/720p"
        },
        # 480p DASH Video (no audio)
        {
            "format_id": "135",
            "vcodec": "avc1.4d401e",
            "acodec": "none",
            "height": 480,
            "ext": "mp4",
            "filesize": 7 * 1024 * 1024,
            "url": "https://googlevideo.com/480p"
        },
        # Progressive 360p stream (audio + video included)
        {
            "format_id": "18",
            "vcodec": "avc1.42001e",
            "acodec": "mp4a.40.2",
            "height": 360,
            "ext": "mp4",
            "filesize": 10 * 1024 * 1024,
            "url": "https://googlevideo.com/360p"
        }
    ]

    options = extractor.aggregate_formats(raw_formats, duration, platform="youtube")
    tiers = {opt.tier: opt for opt in options}

    # 1. 1080p Full HD: 25MB video + 2MB audio = 27MB
    assert FormatTier.P1080 in tiers
    p1080 = tiers[FormatTier.P1080]
    assert p1080.format_id == "137+140"
    assert p1080.estimated_size_bytes == 27 * 1024 * 1024
    assert p1080.requires_remux is True

    # 2. 720p HD: 15MB video + 2MB audio = 17MB
    assert FormatTier.P720 in tiers
    p720 = tiers[FormatTier.P720]
    assert p720.format_id == "136+140"
    assert p720.estimated_size_bytes == 17 * 1024 * 1024
    assert p720.requires_remux is True

    # 3. 480p SD: 7MB video + 2MB audio = 9MB
    assert FormatTier.P480 in tiers
    p480 = tiers[FormatTier.P480]
    assert p480.format_id == "135+140"
    assert p480.estimated_size_bytes == 9 * 1024 * 1024
    assert p480.requires_remux is True

    # 4. Audio M4A: 2MB
    assert FormatTier.AUDIO in tiers
    audio = tiers[FormatTier.AUDIO]
    assert audio.format_id == "140"
    assert audio.estimated_size_bytes == 2 * 1024 * 1024
    assert audio.requires_remux is False

    # 5. Direct Progressive Link
    assert FormatTier.DIRECT in tiers
    direct = tiers[FormatTier.DIRECT]
    assert direct.format_id == "18"
    assert direct.estimated_size_bytes == 0
    assert direct.direct_stream_url == "https://googlevideo.com/360p"


def test_tiktok_progressive_aggregation():
    """Verify progressive single-file streams (TikTok/Meta) need no remuxing."""
    extractor = YtdlpExtractor()
    duration = 15

    raw_formats = [
        {
            "format_id": "download_addr-0",
            "vcodec": "h264",
            "acodec": "aac",
            "height": 1080,
            "ext": "mp4",
            "filesize": 12 * 1024 * 1024,
            "url": "https://tiktok-cdn.net/video.mp4"
        }
    ]

    options = extractor.aggregate_formats(raw_formats, duration, platform="tiktok")
    tiers = {opt.tier: opt for opt in options}

    assert FormatTier.P1080 in tiers
    p1080 = tiers[FormatTier.P1080]
    assert p1080.requires_remux is False
    assert p1080.estimated_size_bytes == 12 * 1024 * 1024

    assert FormatTier.DIRECT in tiers
    direct = tiers[FormatTier.DIRECT]
    assert direct.direct_stream_url == "https://tiktok-cdn.net/video.mp4"

    # Audio tier must also be available from progressive stream
    assert FormatTier.AUDIO in tiers
    audio = tiers[FormatTier.AUDIO]
    assert audio.tier == FormatTier.AUDIO
    assert audio.ext == "m4a"
    assert audio.format_id == "bestaudio/best"
    assert audio.estimated_size_bytes > 0


def test_1080p_exceeding_45mb_omitted():
    """Verify 1080p option is omitted from upload list if size > 45MB (safeguards 50MB limit)."""
    extractor = YtdlpExtractor()
    duration = 300

    raw_formats = [
        {
            "format_id": "140",
            "vcodec": "none",
            "acodec": "aac",
            "ext": "m4a",
            "filesize": 5 * 1024 * 1024,
            "abr": 128
        },
        # 1080p with 42MB video + 5MB audio = 47MB (> 45MB)
        {
            "format_id": "137",
            "vcodec": "h264",
            "acodec": "none",
            "height": 1080,
            "ext": "mp4",
            "filesize": 42 * 1024 * 1024
        },
        # 720p with 20MB video + 5MB audio = 25MB (<= 45MB)
        {
            "format_id": "136",
            "vcodec": "h264",
            "acodec": "none",
            "height": 720,
            "ext": "mp4",
            "filesize": 20 * 1024 * 1024
        }
    ]

    options = extractor.aggregate_formats(raw_formats, duration, platform="youtube")
    tiers = {opt.tier: opt for opt in options}

    # 1080p must be excluded due to > 45MB size guard
    assert FormatTier.P1080 not in tiers
    # 720p must still be present
    assert FormatTier.P720 in tiers


def test_stream_size_calculation_fallback():
    """Verify size calculation falls back to bitrate and duration when filesize is missing."""
    extractor = YtdlpExtractor()
    
    # 1000 kbps for 80 seconds = 1000 * 1000 / 8 * 80 = 10,000,000 bytes
    stream_info = {"tbr": 1000}
    size = extractor._calculate_stream_size(stream_info, duration=80)
    assert size == 10_000_000


def test_ytdlp_base_options_structure():
    """Verify extractor options structure and platform tuning."""
    opts_yt = get_ytdlp_base_options("youtube")
    assert opts_yt["quiet"] is True
    assert opts_yt["noplaylist"] is True
    assert "youtube" in opts_yt.get("extractor_args", {})

    opts_tiktok = get_ytdlp_base_options("tiktok")
    assert "iPhone" in opts_tiktok["user_agent"]


def test_facebook_progressive_audio_aggregation():
    """Verify Facebook progressive streams (SD/HD) provide an Audio tier."""
    extractor = YtdlpExtractor()
    duration = 30
    fb_formats = [
        {
            "format_id": "sd",
            "vcodec": "h264",
            "acodec": "aac",
            "height": 480,
            "ext": "mp4",
            "filesize": 5 * 1024 * 1024,
            "url": "https://fb.com/sd.mp4"
        },
        {
            "format_id": "hd",
            "vcodec": "h264",
            "acodec": "aac",
            "height": 720,
            "ext": "mp4",
            "filesize": 12 * 1024 * 1024,
            "url": "https://fb.com/hd.mp4"
        }
    ]
    options = extractor.aggregate_formats(fb_formats, duration, platform="facebook")
    tiers = {opt.tier: opt for opt in options}

    assert FormatTier.AUDIO in tiers
    audio = tiers[FormatTier.AUDIO]
    assert audio.tier == FormatTier.AUDIO
    assert audio.ext == "m4a"
    assert audio.format_id == "bestaudio/best"


def test_silent_video_omits_audio_tier():
    """Verify explicitly silent videos (no audio track) do not show an Audio option."""
    extractor = YtdlpExtractor()
    duration = 10
    silent_formats = [
        {
            "format_id": "video-only",
            "vcodec": "h264",
            "acodec": "none",
            "height": 720,
            "ext": "mp4",
            "filesize": 3 * 1024 * 1024,
            "url": "https://cdn.net/silent.mp4"
        }
    ]
    options = extractor.aggregate_formats(silent_formats, duration, platform="youtube")
    tiers = {opt.tier: opt for opt in options}

    assert FormatTier.AUDIO not in tiers


def test_all_standard_resolutions_supported():
    """Verify all resolution tiers from 4K (2160p) down to 144p are supported."""
    extractor = YtdlpExtractor()
    duration = 20  # short clip so high resolutions fit within 45MB limit

    raw_formats = [
        # Audio
        {"format_id": "140", "vcodec": "none", "acodec": "aac", "filesize": 1 * 1024 * 1024, "ext": "m4a"},
        # 4K (2160p)
        {"format_id": "313", "vcodec": "vp9", "acodec": "none", "height": 2160, "width": 3840, "filesize": 35 * 1024 * 1024, "ext": "webm"},
        # 2K (1440p)
        {"format_id": "271", "vcodec": "vp9", "acodec": "none", "height": 1440, "width": 2560, "filesize": 20 * 1024 * 1024, "ext": "webm"},
        # 1080p Full HD
        {"format_id": "137", "vcodec": "h264", "acodec": "none", "height": 1080, "width": 1920, "filesize": 12 * 1024 * 1024, "ext": "mp4"},
        # 720p HD
        {"format_id": "136", "vcodec": "h264", "acodec": "none", "height": 720, "width": 1280, "filesize": 7 * 1024 * 1024, "ext": "mp4"},
        # 480p SD
        {"format_id": "135", "vcodec": "h264", "acodec": "none", "height": 480, "width": 854, "filesize": 4 * 1024 * 1024, "ext": "mp4"},
        # 360p
        {"format_id": "134", "vcodec": "h264", "acodec": "none", "height": 360, "width": 640, "filesize": 2 * 1024 * 1024, "ext": "mp4"},
        # 240p
        {"format_id": "133", "vcodec": "h264", "acodec": "none", "height": 240, "width": 426, "filesize": 1 * 1024 * 1024, "ext": "mp4"},
        # 144p
        {"format_id": "160", "vcodec": "h264", "acodec": "none", "height": 144, "width": 256, "filesize": 500 * 1024, "ext": "mp4"},
    ]

    options = extractor.aggregate_formats(raw_formats, duration, platform="youtube")
    tiers = {opt.tier: opt for opt in options}

    # Verify all 8 resolution tiers are present
    expected_tiers = [
        (FormatTier.P2160, "4K 2160p"),
        (FormatTier.P1440, "2K 1440p"),
        (FormatTier.P1080, "1080p Full HD"),
        (FormatTier.P720, "720p HD"),
        (FormatTier.P480, "480p SD"),
        (FormatTier.P360, "360p"),
        (FormatTier.P240, "240p"),
        (FormatTier.P144, "144p"),
    ]

    for tier, label in expected_tiers:
        assert tier in tiers, f"Expected {tier} to be present in options"
        assert tiers[tier].resolution_label == label

    # Audio tier must also be present
    assert FormatTier.AUDIO in tiers


def test_portrait_reels_effective_resolution():
    """Verify portrait/vertical videos (Reels/Shorts/TikTok) resolve effective resolution accurately."""
    extractor = YtdlpExtractor()
    duration = 15

    raw_formats = [
        # 1080x1920 (Vertical 1080p)
        {"format_id": "reel-1080", "vcodec": "h264", "acodec": "aac", "width": 1080, "height": 1920, "filesize": 10 * 1024 * 1024, "ext": "mp4", "url": "https://cdn.net/1080.mp4"},
        # 720x1280 (Vertical 720p)
        {"format_id": "reel-720", "vcodec": "h264", "acodec": "aac", "width": 720, "height": 1280, "filesize": 5 * 1024 * 1024, "ext": "mp4", "url": "https://cdn.net/720.mp4"},
    ]

    options = extractor.aggregate_formats(raw_formats, duration, platform="instagram")
    tiers = {opt.tier: opt for opt in options}

    assert FormatTier.P1080 in tiers
    assert tiers[FormatTier.P1080].resolution_label == "1080p Full HD"

    assert FormatTier.P720 in tiers
    assert tiers[FormatTier.P720].resolution_label == "720p HD"


