"""Periodic Housekeeper & Disk Threshold Monitor for GTOmniVid.

Runs in the background every 10 minutes to:
1. Sweep and purge abandoned temporary workspaces older than 30 minutes in SCRATCH_DIR.
2. Monitor free disk space against safety thresholds (MIN_FREE_DISK_MB).
3. Prevent Linux root filesystem exhaustion on the 30 GB standard persistent disk.
"""

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from config.settings import get_settings
from storage.workspace import JobWorkspace

logger = logging.getLogger(__name__)


class HousekeeperService:
    """Background maintenance service for ephemeral workspace reaping and disk health."""

    def __init__(
        self,
        scratch_dir: Optional[Path] = None,
        interval_seconds: int = 600,
        max_age_seconds: int = 1800,
        min_free_mb: Optional[int] = None
    ) -> None:
        settings = get_settings()
        self.scratch_dir = scratch_dir or settings.SCRATCH_DIR
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds
        self.min_free_mb = min_free_mb if min_free_mb is not None else settings.MIN_FREE_DISK_MB
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Starts the periodic housekeeper background loop."""
        if self._running:
            logger.warning("HousekeeperService is already running.")
            return

        self._running = True
        self._task = asyncio.create_task(self._loop(), name="gtomnivid-housekeeper")
        logger.info(
            "HousekeeperService started (interval=%ds, max_age=%ds, scratch_dir=%s)",
            self.interval_seconds,
            self.max_age_seconds,
            self.scratch_dir
        )

    async def stop(self) -> None:
        """Stops the periodic housekeeper background task gracefully."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("HousekeeperService stopped.")

    async def run_once(self) -> dict:
        """Executes a single maintenance sweep and returns diagnostic status."""
        purged = self.reap_stale_files()
        disk_status = self.check_disk_health()
        return {
            "purged_count": purged,
            "free_disk_mb": disk_status["free_mb"],
            "is_healthy": disk_status["is_healthy"]
        }

    def reap_stale_files(self) -> int:
        """Purges workspaces older than max_age_seconds."""
        return JobWorkspace.cleanup_stale_workspaces(
            base_dir=self.scratch_dir,
            max_age_seconds=self.max_age_seconds
        )

    def check_disk_health(self) -> dict:
        """Inspects disk usage on the scratch volume and logs threshold alerts."""
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        try:
            total, used, free = shutil.disk_usage(self.scratch_dir)
            free_mb = free // (1024 * 1024)
            is_healthy = free_mb >= self.min_free_mb

            if free_mb < 2000:
                logger.critical(
                    "EMERGENCY: Extremely low disk space on %s! Free: %d MB (Critical limit: 2000 MB)",
                    self.scratch_dir,
                    free_mb
                )
            elif not is_healthy:
                logger.warning(
                    "Low disk space on %s: Free: %d MB (Required: %d MB)",
                    self.scratch_dir,
                    free_mb,
                    self.min_free_mb
                )
            else:
                logger.debug("Disk space healthy on %s: Free: %d MB", self.scratch_dir, free_mb)

            return {
                "free_mb": free_mb,
                "total_mb": total // (1024 * 1024),
                "is_healthy": is_healthy
            }
        except OSError as err:
            logger.error("Failed to query disk health on %s: %s", self.scratch_dir, err)
            return {
                "free_mb": -1,
                "total_mb": -1,
                "is_healthy": False
            }

    async def _loop(self) -> None:
        """Main periodic loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.exception("Unexpected error in HousekeeperService loop: %s", err)
