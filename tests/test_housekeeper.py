"""Unit tests for HousekeeperService maintenance routines."""

import asyncio
import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from core.housekeeper import HousekeeperService


@pytest.mark.asyncio
async def test_housekeeper_run_once(tmp_path: Path):
    """Verify run_once cleans stale files and inspects disk health."""
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Create stale directory
    stale_dir = scratch_dir / "stale_run"
    stale_dir.mkdir()
    (stale_dir / "file.dat").write_text("old")
    old_time = time.time() - 3600
    os.utime(stale_dir, (old_time, old_time))

    housekeeper = HousekeeperService(
        scratch_dir=scratch_dir,
        interval_seconds=1,
        max_age_seconds=1800,
        min_free_mb=100
    )

    result = await housekeeper.run_once()
    assert result["purged_count"] == 1
    assert not stale_dir.exists()
    assert result["free_disk_mb"] >= 0


@pytest.mark.asyncio
async def test_housekeeper_disk_health_alert(tmp_path: Path):
    """Verify critical alert when disk space is below emergency threshold."""
    scratch_dir = tmp_path / "scratch"
    housekeeper = HousekeeperService(scratch_dir=scratch_dir, min_free_mb=3000)

    # Mock disk usage to return only 1500 MB free (< 2000 MB emergency threshold)
    with patch("shutil.disk_usage", return_value=(30 * 1024**3, 28 * 1024**3, 1500 * 1024**2)):
        health = housekeeper.check_disk_health()
        assert health["free_mb"] == 1500
        assert health["is_healthy"] is False


@pytest.mark.asyncio
async def test_housekeeper_lifecycle(tmp_path: Path):
    """Verify start and stop lifecycle of HousekeeperService."""
    scratch_dir = tmp_path / "scratch"
    housekeeper = HousekeeperService(scratch_dir=scratch_dir, interval_seconds=10)

    await housekeeper.start()
    assert housekeeper._running is True
    assert housekeeper._task is not None
    assert not housekeeper._task.done()

    await housekeeper.stop()
    assert housekeeper._running is False
    assert housekeeper._task is None
