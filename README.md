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

### Docker Deployment — 24/7 Cloud Auto-Catch Engine

> **The ultimate setup.** Docker turns KSC into an autonomous seat-sniping daemon that runs 24/7 — even when your Mac lid is closed, your terminal is dead, or your NAS is the only thing breathing in the room.

The strategy is **"extract locally, run remotely"** — SSO login happens once on your physical machine. The exported session is mounted into the container. No browser. No display. No bloat.

**Image size:** ~130 MB — `python:3.10-slim` + `requests`. PyQt6, winotify, and selenium are deliberately excluded.

#### Step 1: Export session on your local machine

Run this **once** on any machine with a display (Windows, macOS, Linux):

```bash
python export_session.py
# → session.json
```

Complete the SSO login in the browser window. `session.json` holds your encrypted cookies and token.

#### Step 2: Prepare the server directory

```bash
# On server / NAS:
git clone https://github.com/Daozhu1007/KeanSeatsCatcher.git
cd KeanSeatsCatcher

# Copy session.json from your local machine:
# scp session.json user@your-server:/opt/ksc/KeanSeatsCatcher/
```

You should end up with:

```
KeanSeatsCatcher/
├── session.json            # your exported credentials (you add this)
├── docker-compose.yml      # container orchestration
├── Dockerfile              # image definition
└── ... (other source files)
```

#### Step 3: Configure your arsenal

Edit `docker-compose.yml` on the server. Below is the full annotated reference — pick the weapons you need:

```yaml
services:
  ksc:
    build: .
    container_name: ksc-auto-catch
    restart: unless-stopped
    volumes:
      - ./session.json:/app/session.json:ro
      - ./cloud_sniper.log:/app/cloud_sniper.log

    command:
      # ═══════════════════════════════════════════════════
      # Required — always set these
      # ═══════════════════════════════════════════════════
      - "--sections"
      - "26012,26686"               # ← your target section IDs
      - "--interval"
      - "15"                        # polling interval in seconds
      - "--load-session"
      - "session.json"              # exported session from Step 1

      # ═══════════════════════════════════════════════════
      # Weapon 1: Standard Auto-Catch
      # ═══════════════════════════════════════════════════
      # The above 3 flags are all you need. KSC polls every
      # N seconds. The instant a seat opens, it fires an
      # Add request — no human in the loop.

      # ═══════════════════════════════════════════════════
      # Weapon 2: Drop-and-Add (credit-cap swap)
      # ═══════════════════════════════════════════════════
      # Uncomment when you're at the credit limit and must
      # sacrifice an enrolled course to make room:
      # - "--drop-section"
      # - "26667"                     # course to drop (the sacrifice)

      # ═══════════════════════════════════════════════════
      # Weapon 3: Waitlist Fallback
      # ═══════════════════════════════════════════════════
      # - "--waitlist"                # fallback if Add is rejected

      # ═══════════════════════════════════════════════════
      # Weapon 4: Webhook Notification
      # ═══════════════════════════════════════════════════
      # POSTs a JSON payload on every successful action.
      # Discord / Slack / ntfy — anything with a webhook URL:
      # - "--webhook"
      # - "https://discord.com/api/webhooks/xxx/yyy"
```

| Weapon | Flags | Behavior |
|---|---|---|
| **Standard Auto-Catch** | `--sections` `--load-session` | Polls target sections. Detects open seat → fires `Add` request instantly. |
| **Drop-and-Add** | `--drop-section <ID>` | Drops the old section and adds the new one in a **single atomic API call**. No gap. No risk of losing both. |
| **Waitlist Fallback** | `--waitlist` | If `Add` is rejected (e.g. section full again), falls back to a `Waitlist` action automatically. |
| **Webhook Alert** | `--webhook <URL>` | Sends `{"title":"KSC Auto-Catch","message":"..."}` on every successful registration. |

**Anti-spam guard:** A webhook fires only on a **new** "full → open" transition. If a section stays open across multiple rounds, you won't be bombarded with duplicate notifications.

#### Step 4: Manage the container

```bash
cd KeanSeatsCatcher
```

| Command | What it does |
|---|---|
| `docker compose up -d` | Builds image, starts container in background. First run takes ~30 s. Container auto-restarts on crash or host reboot (`restart: unless-stopped`). |
| `docker compose logs -f` | Tails live output. Look for `Polling (Round #N)` — this confirms KSC is alive and hunting. A successful catch prints a huge banner in the logs. |
| `docker compose up -d --build` | Force-rebuilds image then restarts. Use after editing `docker-compose.yml`, replacing `session.json`, or `git pull` on new code. |
| `docker compose down` | Stops and removes the container gracefully. Logs remain on disk at `./cloud_sniper.log`. |

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
