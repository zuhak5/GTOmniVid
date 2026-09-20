"""Unit tests for JobWorkspace RAII context manager."""

import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from storage.workspace import JobWorkspace, DiskSpaceLowError


@pytest.mark.asyncio
async def test_job_workspace_lifecycle_success(tmp_path: Path):
    """Verify workspace creates directory and cleans up on exit."""
    base_dir = tmp_path / "scratch"
    workspace_path = None

    async with JobWorkspace(job_id="test-job-1", base_dir=base_dir, min_free_mb=0) as path:
        workspace_path = path
        assert path.exists()
        assert path.is_dir()
        assert path.name == "test-job-1"

        # Create dummy media files inside workspace
        dummy_file = path / "video.mp4"
        dummy_file.write_bytes(b"dummy video data")
        assert dummy_file.exists()

    # After exit, directory should be purged completely
    assert not workspace_path.exists()


@pytest.mark.asyncio
async def test_job_workspace_cleanup_on_exception(tmp_path: Path):
    """Verify workspace cleans up even when an unhandled exception occurs."""
    base_dir = tmp_path / "scratch"
    workspace_path = None

    with pytest.raises(RuntimeError, match="Processing failed"):
        async with JobWorkspace(job_id="test-job-error", base_dir=base_dir, min_free_mb=0) as path:
            workspace_path = path
            assert path.exists()
            raise RuntimeError("Processing failed")

    # Directory must still be deleted
    assert workspace_path is not None
    assert not workspace_path.exists()


@pytest.mark.asyncio
async def test_job_workspace_disk_space_guard(tmp_path: Path):
    """Verify DiskSpaceLowError is raised when free space is below threshold."""
    base_dir = tmp_path / "scratch"
    
    # Mock shutil.disk_usage to report 500 MB free when 3000 MB is required
    with patch("shutil.disk_usage", return_value=(30 * 1024**3, 29 * 1024**3, 500 * 1024**2)):
        with pytest.raises(DiskSpaceLowError, match="Insufficient free disk space"):
            async with JobWorkspace(base_dir=base_dir, min_free_mb=3000):
                pass


def test_cleanup_stale_workspaces(tmp_path: Path):
    """Verify cleanup_stale_workspaces purges old folders and keeps fresh ones."""
    base_dir = tmp_path / "scratch"
    base_dir.mkdir(parents=True, exist_ok=True)

    stale_dir = base_dir / "stale_job"
    stale_dir.mkdir()
    (stale_dir / "temp.mp4").write_text("old")

    fresh_dir = base_dir / "fresh_job"
    fresh_dir.mkdir()
    (fresh_dir / "temp.mp4").write_text("new")

    # Set stale_dir mtime to 3600 seconds (1 hour) ago
    old_time = time.time() - 3600
    os.utime(stale_dir, (old_time, old_time))

    purged = JobWorkspace.cleanup_stale_workspaces(base_dir, max_age_seconds=1800)
    assert purged == 1
    assert not stale_dir.exists()
    assert fresh_dir.exists()
