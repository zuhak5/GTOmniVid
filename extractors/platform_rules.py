"""Platform rules, headers, cookies, and extractor configurations for GTOmniVid."""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)

# Standard mobile and desktop User-Agents to prevent bot challenge blocks
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1"
)


def get_cookie_file_path() -> Optional[str]:
    """Returns the validated path to the Netscape cookies.txt file if it exists."""
    settings = get_settings()
    if settings.COOKIES_FILE_PATH and settings.COOKIES_FILE_PATH.is_file():
        logger.debug("Using server-side cookie file: %s", settings.COOKIES_FILE_PATH)
        return str(settings.COOKIES_FILE_PATH)
    return None


def get_ytdlp_base_options(platform: str = "generic") -> Dict[str, Any]:
    """Generates optimal yt-dlp parameters tuned for e2-micro memory efficiency and security."""
    cookie_file = get_cookie_file_path()

    opts: Dict[str, Any] = {
        # General extraction behavior
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "nocheckcertificate": False,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "extract_flat": False,
        "skip_download": True,  # For metadata probing
        
        # User Agent & Network
        "user_agent": DEFAULT_USER_AGENT,
        "http_headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        
        # Output template (when downloading)
        "outtmpl": "%(id)s.%(ext)s",
        "prefer_ffmpeg": True,
        "noplaylist": True,
    }

    if cookie_file:
        opts["cookiefile"] = cookie_file

    # Platform-specific tweaks
    if platform == "youtube":
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "web"],
                "skip": ["hls", "dash"],
            }
        }
    elif platform == "tiktok":
        opts["user_agent"] = DEFAULT_MOBILE_USER_AGENT
    elif platform == "instagram":
        opts["user_agent"] = DEFAULT_MOBILE_USER_AGENT
        if cookie_file:
            opts["cookiefile"] = cookie_file

    return opts
