"""SQLite WAL Egress Ledger & User Daily Quota Engine for GTOmniVid.

Maintains strict compliance with the Google Cloud Always-Free 1 GB/month egress budget:
- Capped at 900 MB hard cap (100 MB safety buffer for signaling/polling)
- Soft threshold at 850 MB triggering warning tier format restrictions
- User fair-use daily download limiter (default 3 downloads / 24h)
- High-performance, zero-overhead embedded SQLite running in WAL mode
"""

import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import aiosqlite

from config.settings import get_settings

logger = logging.getLogger(__name__)


class EgressTier(str, Enum):
    """Operational egress budget tiers."""
    NORMAL = "normal"          # < 850 MB
    WARNING = "warning"        # 850 MB - 900 MB
    HARD_CAP = "hard_cap"      # >= 900 MB


class QuotaExceededError(Exception):
    """Raised when a user exceeds their daily download quota."""
    pass


class EgressHardCapExceededError(Exception):
    """Raised when the VM exceeds its 900 MB monthly free egress budget."""
    pass


class QuotaLedger:
    """Async database manager for egress accounting and user daily quotas."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        user_daily_quota: Optional[int] = None,
        egress_hard_cap_mb: Optional[int] = None,
        egress_warn_cap_mb: Optional[int] = None
    ) -> None:
        settings = get_settings()
        self.db_path = db_path or settings.DB_PATH
        self.user_daily_quota = user_daily_quota if user_daily_quota is not None else settings.USER_DAILY_QUOTA
        self.egress_hard_cap_mb = egress_hard_cap_mb if egress_hard_cap_mb is not None else settings.EGRESS_HARD_CAP_MB
        self.egress_warn_cap_mb = egress_warn_cap_mb if egress_warn_cap_mb is not None else settings.EGRESS_WARN_CAP_MB

    async def init_db(self) -> None:
        """Initializes database schema, WAL mode, and indexes."""
        # Ensure database parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for zero-latency concurrent reads and writes
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")

            # Egress Ledger Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS egress_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    user_id INTEGER NOT NULL,
                    bytes_sent INTEGER NOT NULL,
                    file_type TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL
                );
            """)

            # User Daily Quota Table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_daily_quota (
                    user_id INTEGER NOT NULL,
                    date_key TEXT NOT NULL,
                    download_count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date_key)
                );
            """)

            # Fast index for monthly sum aggregation queries
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_egress_timestamp ON egress_ledger(timestamp);
            """)

            await db.commit()
            logger.info("QuotaLedger database initialized at %s (WAL mode enabled)", self.db_path)

    async def get_monthly_egress_bytes(self) -> int:
        """Returns the total number of bytes egressed through Telegram uploads this calendar month."""
        query = """
            SELECT COALESCE(SUM(bytes_sent), 0)
            FROM egress_ledger
            WHERE timestamp >= datetime('now', 'start of month')
              AND delivery_mode = 'upload';
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0

    async def evaluate_egress_tier(self) -> EgressTier:
        """Evaluates current monthly consumption against Always-Free thresholds."""
        total_bytes = await self.get_monthly_egress_bytes()
        hard_cap_bytes = self.egress_hard_cap_mb * 1024 * 1024
        warn_cap_bytes = self.egress_warn_cap_mb * 1024 * 1024

        if total_bytes >= hard_cap_bytes:
            return EgressTier.HARD_CAP
        if total_bytes >= warn_cap_bytes:
            return EgressTier.WARNING
        return EgressTier.NORMAL

    async def record_egress(
        self,
        user_id: int,
        bytes_sent: int,
        file_type: str,
        platform: str,
        delivery_mode: str = "upload"
    ) -> None:
        """Records a media delivery transaction into the ledger."""
        query = """
            INSERT INTO egress_ledger (user_id, bytes_sent, file_type, platform, delivery_mode)
            VALUES (?, ?, ?, ?, ?);
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(query, (user_id, bytes_sent, file_type, platform, delivery_mode))
            await db.commit()

        logger.info(
            "Recorded egress: user=%d, bytes=%d (%.2f MB), type=%s, platform=%s, mode=%s",
            user_id,
            bytes_sent,
            bytes_sent / (1024 * 1024),
            file_type,
            platform,
            delivery_mode
        )

    async def get_user_daily_downloads(self, user_id: int) -> int:
        """Returns the number of successful downloads for a user today."""
        query = """
            SELECT download_count
            FROM user_daily_quota
            WHERE user_id = ? AND date_key = strftime('%Y-%m-%d', 'now');
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return int(row[0]) if row else 0

    async def can_user_download(self, user_id: int) -> bool:
        """Checks if the user has remaining downloads for today."""
        if self.user_daily_quota <= 0:
            return True
        downloads = await self.get_user_daily_downloads(user_id)
        return downloads < self.user_daily_quota

    async def increment_user_quota(self, user_id: int) -> int:
        """Increments today's download count for the user and returns the new total."""
        query = """
            INSERT INTO user_daily_quota (user_id, date_key, download_count)
            VALUES (?, strftime('%Y-%m-%d', 'now'), 1)
            ON CONFLICT(user_id, date_key) DO UPDATE SET
                download_count = download_count + 1
            RETURNING download_count;
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, (user_id,)) as cursor:
                row = await cursor.fetchone()
                await db.commit()
                return int(row[0]) if row else 1

    async def get_system_quota_summary(self, user_id: int) -> dict:
        """Returns comprehensive diagnostic status for user /quota command."""
        user_downloads = await self.get_user_daily_downloads(user_id)
        monthly_bytes = await self.get_monthly_egress_bytes()
        current_tier = await self.evaluate_egress_tier()

        monthly_mb = monthly_bytes / (1024 * 1024)
        hard_cap_mb = self.egress_hard_cap_mb
        is_unlimited = self.user_daily_quota <= 0

        return {
            "user_id": user_id,
            "user_downloads_today": user_downloads,
            "user_daily_limit": "Unlimited" if is_unlimited else self.user_daily_quota,
            "user_remaining_today": "Unlimited" if is_unlimited else max(0, self.user_daily_quota - user_downloads),
            "monthly_egress_mb": round(monthly_mb, 2),
            "monthly_hard_cap_mb": hard_cap_mb,
            "egress_percentage": round((monthly_mb / hard_cap_mb) * 100, 1) if hard_cap_mb > 0 else 0.0,
            "egress_tier": current_tier.value
        }
