"""Application settings for GTOmniVid Telegram Media Pipeline.

Enforces zero-cost parameters and Google Cloud Platform Always-Free limits:
- 1 GB egress budget guard (capped at 900 MB)
- 30 GB standard persistent disk headroom check (>= 3,000 MB free)
- Concurrency limiter (Semaphore = 1) for 1 GB RAM e2-micro VM
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Production configuration model for GTOmniVid."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram Credentials
    BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot API Token issued by @BotFather"
    )
    ADMIN_USER_ID: int = Field(
        default=0,
        description="Telegram User ID of system administrator for emergency alerts and overrides"
    )

    # GCP Always-Free Egress Guard Parameters
    EGRESS_HARD_CAP_MB: int = Field(
        default=900,
        ge=100,
        le=1000,
        description="Strict monthly upload cutoff in megabytes (preserves 100MB GCP signaling buffer)"
    )
    EGRESS_WARN_CAP_MB: int = Field(
        default=850,
        ge=50,
        le=950,
        description="Monthly warning threshold in megabytes where format restrictions apply"
    )

    # Quota & Fair Usage Parameters
    USER_DAILY_QUOTA: int = Field(
        default=3,
        ge=1,
        le=100,
        description="Maximum successful media downloads per user per calendar day"
    )

    # Storage & System Resource Parameters
    MIN_FREE_DISK_MB: int = Field(
        default=3000,
        ge=500,
        description="Minimum free disk space required on scratch volume before accepting downloads"
    )
    CONCURRENCY_LIMIT: int = Field(
        default=1,
        ge=1,
        le=4,
        description="Maximum concurrent active media processing jobs (strictly 1 on 1GB RAM)"
    )
    SCRATCH_DIR: Path = Field(
        default=Path("/tmp/tg_bot"),
        description="Base temporary directory for isolated job workspaces"
    )
    DB_PATH: Path = Field(
        default=Path("data/gtomnivid.db"),
        description="SQLite database path for egress ledger and daily quota persistence"
    )
    COOKIES_FILE_PATH: Optional[Path] = Field(
        default=Path("config/cookies.txt"),
        description="Optional path to Netscape cookies.txt for authenticated Instagram/Meta extraction"
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
    )

    @property
    def egress_hard_cap_bytes(self) -> int:
        """Returns hard cap limit converted to bytes."""
        return self.EGRESS_HARD_CAP_MB * 1024 * 1024

    @property
    def egress_warn_cap_bytes(self) -> int:
        """Returns warning threshold converted to bytes."""
        return self.EGRESS_WARN_CAP_MB * 1024 * 1024

    @property
    def min_free_disk_bytes(self) -> int:
        """Returns minimum required free disk space in bytes."""
        return self.MIN_FREE_DISK_MB * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns a cached singleton instance of Settings."""
    return Settings()
