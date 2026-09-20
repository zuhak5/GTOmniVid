"""RAII Workspace Context Manager for GTOmniVid.

Ensures dynamic media processing directories (/tmp/tg_bot/<job_uuid>) are
allocated safely, verified for free disk space, and unconditionally cleaned
up via shutil.rmtree() on normal exit, unhandled exception, or task cancellation.
"""

import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from config.settings import get_settings

logger = logging.getLogger(__name__)


class DiskSpaceLowError(Exception):
    """Raised when free disk space falls below safety margin."""
    pass


class JobWorkspace:
    """Async RAII context manager for an isolated job workspace directory."""

    def __init__(
        self,
        job_id: Optional[str] = None,
        base_dir: Optional[Path] = None,
        min_free_mb: Optional[int] = None
    ) -> None:
        settings = get_settings()
        self.job_id = job_id or str(uuid.uuid4())
        self.base_dir = base_dir or settings.SCRATCH_DIR
        self.min_free_mb = min_free_mb if min_free_mb is not None else settings.MIN_FREE_DISK_MB
        self.path: Path = self.base_dir / self.job_id

    async def __aenter__(self) -> Path:
        """Verifies disk space, allocates the directory, and returns its Path."""
        # Ensure parent base directory exists to check disk usage accurately
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Verify pre-flight free disk space
        self.verify_free_disk_space()

        # Create isolated workspace directory
        self.path.mkdir(parents=True, exist_ok=True)
        logger.debug("Allocated workspace: %s", self.path)
        return self.path

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Unconditionally removes the workspace directory and all contained files."""
        self.cleanup()

    def verify_free_disk_space(self) -> None:
        """Checks available disk space against the configured threshold."""
        try:
            total, used, free = shutil.disk_usage(self.base_dir)
            free_mb = free // (1024 * 1024)
            if free_mb < self.min_free_mb:
                raise DiskSpaceLowError(
                    f"Insufficient free disk space: {free_mb} MB available, "
                    f"{self.min_free_mb} MB minimum required."
                )
        except OSError as err:
            logger.warning("Could not query disk space on %s: %s", self.base_dir, err)

    def cleanup(self) -> None:
        """Deletes workspace directory and contents if it exists."""
        if self.path.exists():
            try:
                shutil.rmtree(self.path, ignore_errors=True)
                logger.debug("Cleaned up workspace: %s", self.path)
            except Exception as err:
                logger.error("Failed to clean up workspace %s: %s", self.path, err)

    @classmethod
    def cleanup_stale_workspaces(cls, base_dir: Path, max_age_seconds: int = 1800) -> int:
        """Sweeps base_dir for job directories older than max_age_seconds.
        
        Returns the count of purged directories.
        """
        if not base_dir.exists():
            return 0

        purged_count = 0
        current_time = time.time()

        for item in base_dir.iterdir():
            if item.is_dir():
                try:
                    mtime = item.stat().st_mtime
                    if current_time - mtime > max_age_seconds:
                        shutil.rmtree(item, ignore_errors=True)
                        purged_count += 1
                        logger.info("Purged stale workspace: %s (age: %.1fs)", item, current_time - mtime)
                except Exception as err:
                    logger.warning("Error inspecting/removing stale workspace %s: %s", item, err)

        return purged_count
