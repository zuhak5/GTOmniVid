"""Unit tests for GTOmniVid settings and configuration."""

import pytest
from pathlib import Path
from config.settings import Settings, get_settings


def test_settings_defaults():
    """Verify production Always-Free default parameters."""
    settings = Settings(_env_file=None)
    assert settings.EGRESS_HARD_CAP_MB == 900
    assert settings.EGRESS_WARN_CAP_MB == 850
    assert settings.USER_DAILY_QUOTA == 3
    assert settings.MIN_FREE_DISK_MB == 3000
    assert settings.CONCURRENCY_LIMIT == 1
    assert settings.SCRATCH_DIR == Path("/tmp/tg_bot")
    assert settings.DB_PATH == Path("data/gtomnivid.db")
    assert settings.LOG_LEVEL == "INFO"


def test_settings_computed_properties():
    """Verify megabyte to byte conversions for Always-Free limits."""
    settings = Settings(_env_file=None)
    assert settings.egress_hard_cap_bytes == 900 * 1024 * 1024
    assert settings.egress_warn_cap_bytes == 850 * 1024 * 1024
    assert settings.min_free_disk_bytes == 3000 * 1024 * 1024


def test_settings_custom_overrides():
    """Verify settings can be overridden with valid inputs."""
    custom = Settings(
        BOT_TOKEN="test_token_123",
        ADMIN_USER_ID=99999,
        EGRESS_HARD_CAP_MB=800,
        USER_DAILY_QUOTA=5,
        MIN_FREE_DISK_MB=2000
    )
    assert custom.BOT_TOKEN == "test_token_123"
    assert custom.ADMIN_USER_ID == 99999
    assert custom.EGRESS_HARD_CAP_MB == 800
    assert custom.USER_DAILY_QUOTA == 5
    assert custom.MIN_FREE_DISK_MB == 2000


def test_get_settings_cached_singleton():
    """Verify get_settings returns consistent cached instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
