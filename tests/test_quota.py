"""Unit tests for SQLite WAL Egress Ledger & Quota Engine (core/quota.py)."""

import pytest
from pathlib import Path
from unittest.mock import patch

from config.settings import Settings
from core.quota import EgressTier, QuotaLedger


@pytest.mark.asyncio
async def test_quota_ledger_init_and_wal_mode(tmp_path: Path):
    """Verify database initialization creates tables and enables WAL journal mode."""
    db_file = tmp_path / "test_ledger.db"
    ledger = QuotaLedger(db_path=db_file)
    await ledger.init_db()

    assert db_file.exists()
    import aiosqlite
    async with aiosqlite.connect(db_file) as db:
        async with db.execute("PRAGMA journal_mode;") as cursor:
            row = await cursor.fetchone()
            assert row[0].lower() == "wal"


@pytest.mark.asyncio
async def test_record_egress_and_monthly_sum(tmp_path: Path):
    """Verify recording upload egress accumulates bytes accurately."""
    db_file = tmp_path / "test_ledger.db"
    ledger = QuotaLedger(db_path=db_file)
    await ledger.init_db()

    # Initial state: 0 bytes
    initial = await ledger.get_monthly_egress_bytes()
    assert initial == 0

    # Record two uploads (10 MB and 15 MB)
    mb10 = 10 * 1024 * 1024
    mb15 = 15 * 1024 * 1024
    await ledger.record_egress(user_id=101, bytes_sent=mb10, file_type="video", platform="youtube", delivery_mode="upload")
    await ledger.record_egress(user_id=102, bytes_sent=mb15, file_type="audio", platform="tiktok", delivery_mode="upload")

    # Record direct stream link (0 MB egress counted)
    await ledger.record_egress(user_id=103, bytes_sent=0, file_type="video", platform="youtube", delivery_mode="direct_link")

    total = await ledger.get_monthly_egress_bytes()
    assert total == mb10 + mb15


@pytest.mark.asyncio
async def test_egress_tier_transitions(tmp_path: Path):
    """Verify tier transitions across Normal (<850MB), Warning (850-900MB), and Hard Cap (>=900MB)."""
    db_file = tmp_path / "test_ledger.db"
    ledger = QuotaLedger(db_path=db_file)
    await ledger.init_db()

    # Mock settings with small thresholds for clean testing
    test_settings = Settings(
        EGRESS_HARD_CAP_MB=900,
        EGRESS_WARN_CAP_MB=850
    )

    with patch("core.quota.get_settings", return_value=test_settings):
        # Tier 1: Normal (< 850 MB)
        tier_normal = await ledger.evaluate_egress_tier()
        assert tier_normal == EgressTier.NORMAL

        # Add 860 MB -> Warning tier
        mb860 = 860 * 1024 * 1024
        await ledger.record_egress(user_id=1, bytes_sent=mb860, file_type="video", platform="youtube")
        tier_warning = await ledger.evaluate_egress_tier()
        assert tier_warning == EgressTier.WARNING

        # Add 50 MB more (total 910 MB) -> Hard Cap tier
        mb50 = 50 * 1024 * 1024
        await ledger.record_egress(user_id=1, bytes_sent=mb50, file_type="video", platform="facebook")
        tier_hard_cap = await ledger.evaluate_egress_tier()
        assert tier_hard_cap == EgressTier.HARD_CAP


@pytest.mark.asyncio
async def test_user_daily_quota_lifecycle(tmp_path: Path):
    """Verify user daily download quota incrementation and blocking on 4th download."""
    db_file = tmp_path / "test_ledger.db"
    ledger = QuotaLedger(db_path=db_file)
    await ledger.init_db()

    user_id = 42

    # Initial state: 0 downloads, permitted
    assert await ledger.can_user_download(user_id) is True
    assert await ledger.get_user_daily_downloads(user_id) == 0

    # 1st download
    count1 = await ledger.increment_user_quota(user_id)
    assert count1 == 1
    assert await ledger.can_user_download(user_id) is True

    # 2nd download
    count2 = await ledger.increment_user_quota(user_id)
    assert count2 == 2
    assert await ledger.can_user_download(user_id) is True

    # 3rd download (max limit reached)
    count3 = await ledger.increment_user_quota(user_id)
    assert count3 == 3
    assert await ledger.can_user_download(user_id) is False

    # Summary inspect
    summary = await ledger.get_system_quota_summary(user_id)
    assert summary["user_downloads_today"] == 3
    assert summary["user_remaining_today"] == 0
    assert summary["user_daily_limit"] == 3
