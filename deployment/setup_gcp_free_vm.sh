#!/usr/bin/env bash
# ==============================================================================
# GTOmniVid - Automated GCP Always-Free Tier e2-micro Hardening & Setup Script
# ==============================================================================
# Operating System: Ubuntu 22.04 LTS (x86_64)
# Target Budget: Exactly $0.00 / month on Google Cloud Platform
# ==============================================================================

set -euo pipefail

echo "===================================================================="
echo "⚡ Starting GTOmniVid VM Hardening & Deployment Setup"
echo "===================================================================="

# Ensure running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run this script with sudo: sudo bash $0"
    exit 1
fi

TARGET_USER="${SUDO_USER:-ubuntu}"
INSTALL_DIR="/opt/gtomnivid"

echo "👤 Configuring for system user: ${TARGET_USER}"

# ------------------------------------------------------------------------------
# 1. System Updates & Essential Packages
# ------------------------------------------------------------------------------
echo "📦 1. Updating apt repositories and installing core packages..."
apt-get update -y
apt-get install -y --no-install-recommends \
    ffmpeg \
    python3-pip \
    python3-venv \
    git \
    curl \
    ca-certificates

# ------------------------------------------------------------------------------
# 2. Allocate 2 GB Swap Space (Essential for 1 GB RAM e2-micro)
# ------------------------------------------------------------------------------
if [ ! -f /swapfile ]; then
    echo "💾 2. Creating 2 GB Swapfile at /swapfile..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    echo "✅ Swapfile created and activated."
else
    echo "ℹ️  /swapfile already exists. Skipping swap allocation."
fi

# ------------------------------------------------------------------------------
# 3. Kernel Memory Tuning (swappiness=10, vfs_cache_pressure=50)
# ------------------------------------------------------------------------------
echo "⚙️  3. Applying Linux kernel memory tuning..."
sysctl -w vm.swappiness=10
sysctl -w vm.vfs_cache_pressure=50

cat << 'EOF' > /etc/sysctl.d/99-gtomnivid.conf
vm.swappiness=10
vm.vfs_cache_pressure=50
EOF

# ------------------------------------------------------------------------------
# 4. Cap Systemd Logs to 500 MB (Preserves 30 GB Standard PD)
# ------------------------------------------------------------------------------
echo "📝 4. Capping journald logs to 500 MB..."
mkdir -p /etc/systemd/journald.conf.d
cat << 'EOF' > /etc/systemd/journald.conf.d/99-gtomnivid-logcap.conf
[Journal]
SystemMaxUse=500M
SystemKeepFree=2G
EOF
systemctl restart systemd-journald

# ------------------------------------------------------------------------------
# 5. Directory Structure & Permissions
# ------------------------------------------------------------------------------
echo "📁 5. Setting up directory layout at ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/data"
mkdir -p "${INSTALL_DIR}/config"
mkdir -p /tmp/tg_bot

chown -R "${TARGET_USER}:${TARGET_USER}" "${INSTALL_DIR}"
chmod 700 "${INSTALL_DIR}/data"
chmod 777 /tmp/tg_bot

# ------------------------------------------------------------------------------
# 6. Python Virtual Environment & Dependencies
# ------------------------------------------------------------------------------
echo "🐍 6. Initializing Python virtual environment..."
if [ ! -d "${INSTALL_DIR}/venv" ]; then
    sudo -u "${TARGET_USER}" python3 -m venv "${INSTALL_DIR}/venv"
fi

sudo -u "${TARGET_USER}" "${INSTALL_DIR}/venv/bin/pip install --upgrade pip"

if [ -f "${INSTALL_DIR}/requirements.txt" ]; then
    echo "📦 Installing production dependencies..."
    sudo -u "${TARGET_USER}" "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"
fi

# ------------------------------------------------------------------------------
# 7. Automated Daily yt-dlp Upgrade (04:00 AM Daily)
# ------------------------------------------------------------------------------
echo "⏰ 7. Configuring automated daily yt-dlp cron job..."
CRON_CMD="0 4 * * * ${INSTALL_DIR}/venv/bin/pip install --upgrade yt-dlp >> /var/log/ytdlp_update.log 2>&1"
(crontab -u "${TARGET_USER}" -l 2>/dev/null | grep -v "yt-dlp" || true; echo "${CRON_CMD}") | crontab -u "${TARGET_USER}" -

# ------------------------------------------------------------------------------
# 8. Install Systemd Service Unit
# ------------------------------------------------------------------------------
if [ -f "${INSTALL_DIR}/deployment/gtomnivid.service" ]; then
    echo "🚀 8. Installing systemd unit file..."
    cp "${INSTALL_DIR}/deployment/gtomnivid.service" /etc/systemd/system/gtomnivid.service
    systemctl daemon-reload
    systemctl enable gtomnivid
    echo "✅ gtomnivid.service enabled."
fi

echo "===================================================================="
echo "🎉 GTOmniVid VM Hardening & Setup Complete!"
echo "===================================================================="
echo "Next steps:"
echo "1. Configure ${INSTALL_DIR}/.env with your BOT_TOKEN from @BotFather."
echo "2. Start the service: sudo systemctl start gtomnivid"
echo "3. Monitor live logs: sudo journalctl -u gtomnivid -f"
echo "===================================================================="
