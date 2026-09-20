# GTOmniVid

An enterprise-grade, zero-cost Telegram media extraction bot optimized to run 100% within the **Google Cloud Platform (GCP) Always-Free Tier** on an `e2-micro` virtual machine ($0.00/month billing envelope).

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-informational.svg)](https://docs.aiogram.dev/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Lossless%20Remux-success.svg)](https://ffmpeg.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Key Features

- **Multi-Platform Extraction**: Seamlessly extracts video and audio from YouTube, TikTok, Instagram (Reels/Posts), and Facebook via an optimized `yt-dlp` async engine.
- **Strict GCP Always-Free Compliance**:
  - **1 GB RAM Protection**: Hardware-aware `ConcurrencyController` (`asyncio.Semaphore(1)`) + systemd `MemoryMax=850M` + 2 GB swap prevent Out-Of-Memory (OOM) crashes.
  - **900 MB Egress Guard**: SQLite WAL-backed `QuotaLedger` strictly enforces a 900 MB/month upload hard cap to stay within GCP's 1 GB free outbound tier.
  - **CPU Credit Preservation**: Video remuxing uses lossless `-c copy -movflags +faststart` ($< 2.5\text{s}$, $< 2.5\%$ CPU), avoiding CPU credit exhaustion on burstable `e2-micro` instances.
  - **Zero-Egress Direct Links**: Offers progressive streams as direct playback URLs (0 MB server bandwidth).
- **Comprehensive Security Shield**:
  - 3-stage validation pipeline: Scheme check $\to$ Regex domain whitelist $\to$ Pre-flight async DNS resolution.
  - Hardened against Server-Side Request Forgery (SSRF) and GCP Instance Metadata service harvesting (`169.254.169.254`).
- **Telegram Native Optimization**:
  - Streams delivered with full metadata (dimensions, duration, thumbnail) for instant in-app streaming (`supports_streaming=True`).
  - Size guards reject payloads $> 50\text{ MB}$ (Telegram Bot API limit) prior to upload.
  - Throttled progress updates ($\le 1\text{ edit} / 3\text{ seconds}$) prevent Telegram 429 flood limits.
- **Self-Healing Storage**:
  - Workspace directories allocated under RAII context managers (`JobWorkspace`) with automatic teardown.
  - Background `HousekeeperService` prunes stale data every 10 minutes and enforces disk space thresholds.

---

## Architecture & Resource Boundaries

```
User Message (URL)
       │
       ▼
[AntiFloodMiddleware] (1 msg / 2 sec)
       │
       ▼
[SecurityShield] (Protocol -> Domain Whitelist -> DNS Pre-resolve / SSRF Check)
       │
       ▼
[QuotaLedger] (Check 3 downloads/day & <900 MB monthly egress)
       │
       ▼
[YtDlpExtractor] (Async metadata & format aggregation)
       │
       ▼
[Format Menu Keyboard] (Compact callback <30 bytes & Direct Stream URLs)
       │
       ▼
[ConcurrencyController] (FIFO queue, Semaphore(1))
       │
       ▼
[JobWorkspace] (/tmp/tg_bot/<uuid>, Pre-flight disk space guard)
       │
       ▼
[FFmpegService] (Lossless -c copy, frame-accurate thumbnail)
       │
       ▼
[TelegramUploader] (Throttled progress bar, commit bytes to ledger)
```

| Constraint | Limit / Setting | Purpose |
| :--- | :--- | :--- |
| **Concurrency** | `1` active job | Protects 1 GB RAM on `e2-micro` |
| **Max Process Memory** | `850 MB` | Enforced by systemd `MemoryMax` |
| **Monthly Egress** | `900 MB` | Soft warning at 850 MB, hard stop at 900 MB |
| **User Daily Quota** | `3` downloads / user / day | Fair usage and bandwidth preservation |
| **Swap Space** | `2 GB` | Absorption layer for memory spikes |

---

## Quick Start (Local Development)

### 1. Prerequisites
- Python 3.11+
- FFmpeg and ffprobe installed and available on `PATH`

### 2. Setup Virtual Environment
```bash
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows:
.\venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configuration
Create your `.env` file from the example:
```bash
cp .env.example .env
```
Fill in your credentials in `.env`:
```env
BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
ADMIN_USER_IDS=[123456789]
```

### 5. Run the Bot
```bash
python main.py
```

### 6. Run Test Suite
```bash
pytest tests/ -v
```

---

## Google Cloud Always-Free Deployment

### 1. Create Compute Engine Instance
In the [Google Cloud Console](https://console.cloud.google.com/):
- **Machine type**: `e2-micro` (2 vCPUs, 1 GB memory)
- **Region**: `us-central1`, `us-east1`, or `us-west1`
- **Boot disk**: **Standard Persistent Disk (`pd-standard`)**, **30 GB**, Ubuntu 22.04 LTS or Debian 12 *(Do NOT choose Balanced or SSD)*
- **Network Tier**: Standard Tier

### 2. Run the VM Provisioning Script
Transfer `deployment/setup_gcp_free_vm.sh` to the server and execute:
```bash
chmod +x setup_gcp_free_vm.sh
sudo ./setup_gcp_free_vm.sh
```
This sets up the 2 GB swapfile, memory kernel tunings (`swappiness=10`), log caps (`SystemMaxUse=500M`), system packages, and the daily `yt-dlp` auto-updater cron.

### 3. Install the Service
```bash
sudo cp deployment/gtomnivid.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gtomnivid.service
```

### 4. Configure $0.01 Billing Alert
Follow [`deployment/setup_billing_alert.md`](deployment/setup_billing_alert.md) to set up a budget alert in Cloud Billing ensuring no surprise charges ever accrue.

---

## Project Structure

```
GTOmniVid/
├── bot/                       # Telegram presentation layer
│   ├── handlers/              # /start, /quota, /cancel, media handlers
│   ├── keyboards/             # Inline quality keyboards & compact callbacks
│   └── middlewares/           # Rate limiting & global error handling
├── config/                    # Pydantic settings & platform rules
├── core/                      # Core business logic
│   ├── housekeeper.py         # Periodic stale workspace reaper
│   ├── queue.py               # Semaphore(1) FIFO concurrency queue
│   ├── quota.py               # SQLite WAL monthly egress & user ledger
│   └── security.py            # SSRF protection, protocol & domain validator
├── deployment/                # GCP deployment automation
│   ├── gtomnivid.service      # Systemd unit with MemoryMax=850M
│   ├── setup_billing_alert.md # Zero-dollar billing guide
│   └── setup_gcp_free_vm.sh   # Bash provisioning & swap tuning script
├── extractors/                # yt-dlp wrapper & format aggregators
├── media/                     # FFmpeg remuxing, thumbnails & Telegram uploader
├── storage/                   # RAII isolated workspace management
├── tests/                     # 50 unit tests across all components
├── requirements.txt           # Dependency specifications
└── main.py                    # Application entrypoint
```

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
