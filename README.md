<p align="center">
  <img src="assets/logo.png" width="180" alt="KeanSeatsCatcher Logo">
</p>

<h1 align="center">KeanSeatsCatcher</h1>

<p align="center">
  <em>Dual-engine course availability monitor for Ellucian Banner 9 —<br>a GUI desktop app and a headless CLI daemon.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue" alt="Platform">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python Version">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <a href="https://github.com/Daozhu1007/KeanSeatsCatcher"><img src="https://img.shields.io/github/stars/Daozhu1007/KeanSeatsCatcher?style=flat" alt="Stars"></a>
  <a href="https://space.bilibili.com/477852567"><img src="https://img.shields.io/badge/Bilibili-Limitime-00A1D6?logo=bilibili" alt="Bilibili"></a>
</p>

<p align="center">
  <img src="docs/screenshot.png" width="800" alt="KeanSeatsCatcher GUI Screenshot">
</p>

---

## Disclaimer

This software is an **external auxiliary toolkit for personal, non-commercial use**. It interacts exclusively with the standard public API of Ellucian Banner 9 using your own authenticated session — it does not exploit vulnerabilities or bypass authorization.

However:

- Automated interaction with your institution's registration system may violate its acceptable-use policy. You are responsible for understanding those rules.
- Excessive request rates can trigger rate-limiting or account suspension.
- No warranty. Use at your own risk.

---

## Dual-Engine Architecture

KSC ships with two independent engines sharing the same API core.

### 1. GUI Desktop Mode (Windows)

```
ui_main.py  →  PyQt6 + qfluentwidgets
```

| Feature | Detail |
|---|---|
| **Dashboard** | One-shot scheduled attack or continuous monitoring with countdown timer |
| **Auto-Catch** | Background polling loop — detects open seats and attacks instantly |
| **Session Auto-Recovery** | Detects 401/403 / login redirect mid-poll; silently re-authenticates via Selenium using the local Edge profile (no 2FA required); hot-swaps credentials into the live engine without restart |
| **Memory Safety** | Log widget capped at 1 000 lines; minimum 1 s polling interval to prevent CPU thrashing |
| **Recovery Counter** | Status bar shows `Polling (Round #1245) \| Recoveries: 3...` for situational awareness |
| **Waitlist Fallback** | Configurable automatic `Waitlist` action when standard `Add` is rejected |
| **Ping** | One-click server latency test |

### 2. CLI Headless Mode (Linux Server · Cloud Phantom)

```
cloud_cli.py  →  pure threading.Thread + logging  (zero GUI dependency)
```

Designed for 24/7 unattended operation on a $5 VPS.

**Session Smuggling pipeline** — solves the "headless browser can't do 2FA" problem:

```
[Windows desktop]                  [Linux server]
export_session.py ──► session.json ──► cloud_cli.py --load-session session.json
```

1. Run `export_session.py` on your local Windows machine (visible browser, one-time SSO login).
2. Copy the resulting `session.json` to your server.
3. `cloud_cli.py` injects credentials directly into the API engine — **no browser required on the server**.

**CLI features:**
- `--sections` / `--interval` / `--waitlist` — same semantics as the GUI
- `--load-session FILE` — skip browser auth, load from JSON
- `--webhook URL` — POST `{"title":"KSC Auto-Catch","message":"..."}` on success (Slack / Discord / ntfy compatible)
- `--no-headless` — show browser for initial login setup on a machine with a display
- Graceful shutdown on `SIGINT` / `SIGTERM`
- Dual logging: INFO to console, DEBUG to `cloud_sniper.log`

---

## Quick Start

### GUI (Windows)

```bash
git clone https://github.com/Daozhu1007/KeanSeatsCatcher.git
cd KeanSeatsCatcher
pip install -r requirements.txt
python ui_main.py
```

Or download the pre-built packages from [Releases](../../releases):
- `KeanSeatsCatcher_vX.X_Setup.exe` — full installer (Start Menu shortcuts, uninstaller)
- `KeanSeatsCatcher-vX.X.zip` — portable, extract and run

### CLI (Windows / Local Test)

```bash
# Export session (one-time, visible browser)
python export_session.py

# Start polling with the exported session
python cloud_cli.py --sections 12345,67890 --interval 15 --load-session session.json
```

### CLI (Linux Server / Headless Deploy)

```bash
# --- On local machine (one time) ---
python export_session.py
# → session.json

# --- Copy to server ---
scp session.json user@vps:/opt/ksc/

# --- On server ---
pip install -r requirements.txt
python cloud_cli.py --sections 12345,67890 --interval 15 --waitlist \
    --load-session session.json \
    --webhook https://discord.com/api/webhooks/xxx/yyy
```

### Docker Deployment — Universal Cloud Phantom Guide

> Docker turns KSC into a 24/7 autonomous seat-sniping daemon. Run it on a $5 VPS, a NAS in the closet, or Docker Desktop on the same Mac. Close your lid, kill your terminal, walk away — it keeps hunting.

No browser. No display. No bloat. **Image size:** ~130 MB (`python:3.10-slim` + `requests`).

#### Phase 1: Extract the Session Locally

**This step always happens on a machine with a display.** SSO demands a browser; the container has none. Run this on your daily-driver laptop or desktop — Windows, macOS, or Linux:

```bash
python export_session.py
# → session.json
```

Log in via the browser window. `session.json` captures your encrypted cookies and API token.

> **Smart Browser Fallback.** `export_session.py` ships with an automatic fallback chain — **Edge → Chrome → Safari** (macOS: **Safari → Chrome → Edge**). The first available browser wins. No need to install anything.

#### Phase 2: Configure the Arsenal

Clone the repo on your target machine and copy in `session.json`:

```bash
git clone https://github.com/Daozhu1007/KeanSeatsCatcher.git
cd KeanSeatsCatcher
# scp session.json user@your-server:/opt/ksc/KeanSeatsCatcher/
```

You should have:

```
KeanSeatsCatcher/
├── session.json            # your exported credentials
├── docker-compose.yml      # container orchestration
├── Dockerfile              # image definition
└── ... (other source files)
```

Edit `docker-compose.yml` — replace the placeholder IDs and pick your scenario:

```yaml
services:
  ksc:
    build: .
    container_name: ksc-auto-catch
    restart: unless-stopped
    volumes:
      - ./session.json:/app/session.json:ro

    command:
      # --- Required ---
      - --sections
      - 26012,26686                 # ← your target section IDs
      - --interval
      - 15                          # polling interval in seconds
      - --load-session
      - session.json                # exported session from Phase 1

      # --- Weapon 1: Standard Auto-Catch ---
      # The flags above are all you need. KSC polls every N seconds
      # and fires an Add request the instant a seat opens.

      # --- Weapon 2: Drop-and-Add (credit-cap swap) ---
      # Uncomment when you're at the credit limit:
      # - --drop-section
      # - 26667                      # course to sacrifice

      # --- Weapon 3: Waitlist Fallback ---
      # - --waitlist

      # --- Weapon 4: Webhook Notification ---
      # Discord / Slack / ntfy:
      # - --webhook
      # - https://discord.com/api/webhooks/xxx/yyy
```

**Scenario A — Standard Auto-Catch (room under credit cap)**

Keep `--sections` and `--load-session` active. KSC polls every N seconds. The moment a seat flips from full to open, it fires an `Add` request. No other flags required.

**Scenario B — Drop-and-Add (at the credit cap)**

Uncomment `--drop-section` and supply the ID of an enrolled course you're willing to sacrifice. When a target opens, KSC drops the old section and adds the new one in a **single atomic API call** — no gap, no risk of losing both.

| Optional Flag | Effect |
|---|---|
| `--waitlist` | Fallback to `Waitlist` if `Add` is rejected |
| `--webhook <URL>` | POSTs `{"title":"KSC Auto-Catch","message":"..."}` on every successful action |
| `--drop-section <ID>` | Synchronous swap: drop old, add new in one call |

**Anti-spam guard:** Webhooks fire only on a **new** "full → open" transition. No duplicate spam across rounds.

#### Phase 3: Launch & Monitor

```bash
cd KeanSeatsCatcher
```

| Command | What it does |
|---|---|
| `docker compose up -d` | Builds image, starts daemon in background (~30 s first run). Auto-restarts on crash or host reboot. |
| `docker compose logs -f` | Tails live output. `Polling (Round #N)` = alive and hunting. A successful catch prints a banner. |
| `docker compose down` | Graceful shutdown. |
| `docker compose up -d --build` | Force-rebuild image before starting. Use after editing `docker-compose.yml`, replacing `session.json`, or pulling new code. |

---

## Project Structure

```
KeanSeatsCatcher/
├── ui_main.py              # GUI entry (PyQt6 shell)
├── ui_autocatch.py         # Auto-Catch UI panel
├── auto_catch_worker.py    # Background polling worker (QThread)
├── core_api.py             # REST engine + session recovery detection
├── core_auth.py            # Selenium SSO / headless auth
├── i18n.py                 # Localization manager
├── locales/                # en_US + zh_CN translation tables
├── assets/                 # Icons, logo, screenshots
│
├── cloud_cli.py            # CLI entry point (Cloud Phantom)
├── cloud_worker.py         # Headless polling worker (threading.Thread)
├── export_session.py       # Session exporter (Windows → Linux)
│
├── Dockerfile              # Container image definition
├── docker-compose.yml      # One-command container orchestration
├── .dockerignore           # Exclude desktop cruft from build context
├── requirements.txt        # Desktop dependencies (PyQt6, selenium, etc.)
├── requirements-cli.txt    # CLI-only dependencies (requests only)
│
├── KeanSeatsCatcher.spec   # PyInstaller spec
└── KeanSeatsCatcher.iss    # Inno Setup installer script
```

---

## License

MIT. See `LICENSE`.
