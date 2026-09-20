"""Unit tests for deployment scripts and systemd units (deployment/)."""

import configparser
from pathlib import Path


def test_systemd_unit_file_structure():
    """Verify gtomnivid.service conforms to systemd unit specifications."""
    service_path = Path("deployment/gtomnivid.service")
    assert service_path.exists()

    config = configparser.ConfigParser(interpolation=None)
    # Read service file preserving case
    config.optionxform = str
    config.read(service_path, encoding="utf-8")

    assert "Unit" in config
    assert "Service" in config
    assert "Install" in config

    service = config["Service"]
    assert service.get("Type") == "simple"
    assert service.get("WorkingDirectory") == "/opt/gtomnivid"
    assert service.get("ExecStart") == "/opt/gtomnivid/venv/bin/python main.py"
    assert service.get("Restart") == "always"
    assert service.get("EnvironmentFile") == "/opt/gtomnivid/.env"

    # Verify resource boundaries protecting 1 GB RAM e2-micro
    assert service.get("MemoryMax") == "850M"
    assert service.get("CPUQuota") == "180%"


def test_setup_script_contents():
    """Verify setup_gcp_free_vm.sh includes critical hardening commands."""
    script_path = Path("deployment/setup_gcp_free_vm.sh")
    assert script_path.exists()

    content = script_path.read_text(encoding="utf-8")
    # Swap configuration
    assert "fallocate -l 2G /swapfile" in content
    # Memory kernel tuning
    assert "vm.swappiness=10" in content
    assert "vm.vfs_cache_pressure=50" in content
    # Journald capping
    assert "SystemMaxUse=500M" in content
    # Daily yt-dlp cron
    assert "pip install --upgrade yt-dlp" in content
    # Target directory
    assert "/opt/gtomnivid" in content


def test_billing_alert_documentation():
    """Verify setup_billing_alert.md contains zero-cost parameters."""
    doc_path = Path("deployment/setup_billing_alert.md")
    assert doc_path.exists()

    content = doc_path.read_text(encoding="utf-8")
    assert "pd-standard" in content
    assert "30 GB" in content
    assert "e2-micro" in content
    assert "900 MB" in content
