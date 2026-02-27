<img align="right" src="https://visitor-badge.laobi.icu/badge?page_id=platima.esphome-python" height="20" />

# ESPHome Lights for Python / OpenClaw

A Python CLI tool for controlling ESPHome smart lights and switches via the
native ESPHome API, designed as an
[OpenClaw](https://github.com/openclaw/openclaw) skill for
voice/chat-driven home automation.

**Version:** 0.1.4

## Overview

ESPHome Lights lets you turn lights on/off, set brightness, and set RGB
colours from the command line. Devices are discovered from environment
variables; no separate config files to manage.

The project uses a **persistent daemon + thin CLI client** architecture. The
daemon keeps ESPHome API connections alive, eliminating the ~4.2 s cold-start
latency of a monolithic script. The CLI client uses only Python stdlib for
fast startup and communicates with the daemon over a Unix domain socket.

### Architecture

```
CLI client  —(Unix socket)—>  Daemon  —(persistent ESPHome API connections)—>  Devices
```

| Component              | File                      | Purpose                              |
|------------------------|---------------------------|--------------------------------------|
| Daemon                 | `esphome-lightsd.py`      | Persistent connections, state cache  |
| CLI client             | `esphome-lights.py`       | Thin stdlib-only client, fast start  |
| systemd unit           | `esphome-lightsd.service` | Auto-start on boot                   |
| Tests                  | `tests/`                  | Unit tests (49 tests)                |
| OpenClaw skill         | `SKILL.md`                | Chat-driven control via OpenClaw     |

## Requirements

- **Python 3.11** - required for the daemon due to `aioesphomeapi` + Noise
  encryption compatibility on ARM targets. Python 3.12/3.13 has issues with
  the compiled `noise` extension on ARM (e.g. Luckfox Pico, Orange Pi).
  The CLI client is stdlib-only and works with any `python3`.
  > **Gotcha:** `noise` 1.2.2 (Perlin noise) and `noiseprotocol` both install
  > into the same `noise/` directory. The installer force-reinstalls
  > `noiseprotocol` after `aioesphomeapi` to guard against this automatically.
- **`aioesphomeapi`** - installed into the venv by the installer
- ESPHome devices with the native API enabled and encryption keys configured

## Installation

The installer sets up a **user-level systemd service** (no sudo required). It
will check for dependencies, create a config template, and optionally register
the OpenClaw skill.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/platima/ESPHome-Python/main/install.sh)
# or
bash <(wget -qO- https://raw.githubusercontent.com/platima/ESPHome-Python/main/install.sh)
```

Or, if you have already cloned the repo:

```bash
git clone https://github.com/platima/ESPHome-Python.git
cd ESPHome-Python
bash install.sh
```

The installer will:

1. Refuse to run as root.
2. Install scripts to `~/.local/lib/esphome-lights/` with symlinks in `~/.local/bin/`.
3. Create a Python 3.11 venv at `~/.local/lib/esphome-lights/venv` and install `aioesphomeapi` there (with the `noiseprotocol` fix applied automatically).
4. Check for a config file and offer to create a template if none exists.
5. Install and enable a systemd user service (using the venv Python) with the socket at `$XDG_RUNTIME_DIR/esphome-lights.sock`.
6. Enable `loginctl linger` so the daemon starts at boot without requiring login.
7. Detect `~/.openclaw` and offer to register the skill if OpenClaw is installed.

## Device Configuration

Devices are configured via environment variables. Each variable follows the
format:

```
ESPHOME_LIGHTS_<LOCATION>="<host>:<port>|<encryption_key>"
```

- **Location** is uppercased in the variable name and lowercased for CLI use.
- **Port** is typically `6053` (native ESPHome API).

### Config file (installer)

When installed via `install.sh`, the config file lives at:

```
~/.config/esphome-lights/env
```

The installer creates a commented template here if no file is found.

### Config loading priority

The daemon loads env files in priority order on startup and on every reload.
Higher-priority sources override lower ones:

| Priority | File |
|----------|------|
| 1 (highest) | `~/.openclaw/workspace/.env` (if present) |
| 2 | `~/.config/esphome-lights/env` |
| 3 (fallback) | `{script_dir}/../.env` |

The systemd unit does **not** use `EnvironmentFile=`; Python handles all
env loading, so reloads via SIGHUP or `--reload` immediately pick up changes.

### Manual / environment variable

You can also export variables in your shell or place a `.env` file one
directory above the script:

```bash
export ESPHOME_LIGHTS_LIVING_ROOM="10.42.40.55:6053|J+YkHH7XC+4dQwWvPoF5kaz7tP4NY4HJNTL0QyZM1Rg="
export ESPHOME_LIGHTS_BEDROOM="10.42.40.56:6053|another_key_here="
```

## Usage

### Starting the Daemon

The daemon must be running before CLI commands will work:

```bash
# Start the daemon (foreground)
python3 esphome-lightsd.py

# Or install as a systemd service (see systemd section below)
```

### CLI Commands

```bash
# List configured lights (shows connection state)
esphome-lights --list

# Show on/off state of all lights (from daemon cache - instant)
esphome-lights --status

# Turn a light on or off
esphome-lights --set living_room --on
esphome-lights --set living_room --off

# Set brightness (0-255)
esphome-lights --set living_room --brightness 128

# Set RGB colour (r,g,b, each 0-255)
esphome-lights --set living_room --rgb 255,0,0

# Health check
esphome-lights --ping

# Reload config without restarting the daemon
esphome-lights --reload
```

### Flags

| Flag                   | Effect                                          |
|------------------------|-------------------------------------------------|
| `--bg`, `--background` | Fire and forget - return immediately            |
| `--debug`              | Show full JSON response (overrides `--bg`)      |

## Entity Handling

- Prefers **`LightInfo`** entities for brightness and RGB control.
- Falls back to **`SwitchInfo`** for simple on/off devices (smart plugs, etc.).
- Always skips entities with `object_id == 'status_led'`.
- Brightness and RGB commands return errors for switch-type entities.

## Daemon Architecture

### Protocol

The daemon listens on a Unix socket (`/tmp/esphome-lights.sock` by default,
configurable via `ESPHOME_LIGHTS_SOCKET`). Communication uses
newline-delimited JSON.

**Requests:**

```json
{"cmd": "list"}
{"cmd": "status"}
{"cmd": "set", "device": "living_room", "action": "on"}
{"cmd": "set", "device": "living_room", "action": "brightness", "value": "128"}
{"cmd": "set", "device": "living_room", "action": "rgb", "value": "255,0,0"}
{"cmd": "ping"}
{"cmd": "reload"}
```

The `reload` command re-reads all env files, rebuilds the device list, then
disconnects removed/changed devices and connects new/changed ones. It returns
a summary string such as `added: 0, removed: 0, changed: 1, unchanged: 1`.

**Responses:**

```json
{"ok": true, "result": "Turned ON"}
{"ok": false, "error": "Device 'kitchen' not found"}
{"ok": true, "result": "pong"}
```

### Daemon Features

- **Persistent connections** to all configured ESPHome devices
- **Automatic reconnection** with exponential backoff (1 s to 30 s max)
- **State caching** - `--status` returns instantly from cache
- **Multiple concurrent clients** supported
- **Graceful shutdown** on SIGTERM/SIGINT (cleans up socket file)
- **Stale socket detection** - removes orphaned socket files on startup
- **Configurable logging** via `ESPHOME_LIGHTS_LOG_LEVEL` env var
- **Live reload** via `--reload` or SIGHUP without restarting the daemon

### systemd

The installer configures a **user-level** systemd service automatically. To
manage it:

```bash
# Start / stop / restart
systemctl --user start esphome-lightsd
systemctl --user stop esphome-lightsd
systemctl --user restart esphome-lightsd

# Check status
systemctl --user status esphome-lightsd

# View live logs
journalctl --user -u esphome-lightsd -f
```

A system-level unit file (`esphome-lightsd.service`) is also included in the
repo for manual system-wide installs (requires root).

### Performance Targets

| Metric                  | Current | Target    |
|-------------------------|---------|-----------|
| `--set` command         | ~4.2 s  | < 100 ms  |
| `--status` (all devices)| ~5–8 s  | < 50 ms   |
| `--list`                | ~1.5 s  | < 50 ms   |
| CLI client startup      | ~1.5 s  | < 30 ms   |

## OpenClaw Integration

ESPHome Lights is designed as an [OpenClaw](https://github.com/openclaw/openclaw)
skill, enabling chat-driven smart home control from any messaging platform
(WhatsApp, Telegram, Discord, Slack, etc.).

### How It Works

```
User (WhatsApp/Telegram/etc.)
  → OpenClaw Gateway
    → Agent (with exec tool)
      → esphome-lights.py --set living_room --on
        → ESPHome device
```

The OpenClaw agent reads the `SKILL.md` file to understand available commands,
then uses its `exec` tool to run `esphome-lights.py` with the appropriate
flags. Natural-language requests like *"turn on the living room light"* are
translated to CLI commands automatically.

### Skill Installation

The `SKILL.md` file at the repository root registers ESPHome Lights as an
OpenClaw skill. To install:

1. Clone this repository into your OpenClaw workspace's `skills/` directory,
   or symlink it:
   ```bash
   ln -s /path/to/ESPHome-Python ~/.openclaw/skills/esphome-lights
   ```
2. Ensure `ESPHOME_LIGHTS_*` environment variables are available to the
   OpenClaw agent (via the skill's env config or the agent's environment).
3. The skill will appear in the agent's skill list automatically.

### Automation Examples

With OpenClaw's cron system, you can schedule light control:

```bash
# Turn on living room at sunset (example: 6 PM daily)
openclaw cron add \
  --name "Lights on at sunset" \
  --cron "0 18 * * *" \
  --tz "Australia/Melbourne" \
  --session isolated \
  --message "Turn on the living room light" \
  --announce
```

## Deployment Target

This project is designed to run on a **Luckfox Pico** (ARM Linux SBC), a
resource-constrained device. The daemon architecture is deliberately kept
simple with no heavy frameworks.

## Running Tests

```bash
python3 -m unittest discover -s tests -v
```

49 tests covering daemon command handlers, socket protocol, entity resolution,
state caching, and client-daemon integration.

## File Structure

```
ESPHome-Python/
├── install.sh                  # One-line installer (user-level, no sudo)
├── esphome-lights.py           # Thin CLI client (stdlib only)
├── esphome-lightsd.py          # Daemon (persistent connections + socket)
├── esphome-lightsd.service     # systemd unit file (system-level reference)
├── tests/
│   ├── test_daemon.py          # Daemon unit tests (37 tests)
│   └── test_client.py          # Client unit + integration tests (12 tests)
├── SKILL.md                    # OpenClaw skill definition
├── CLAUDE.md                   # AI assistant context
├── TODO.md                     # Persistent task tracker
├── README.md                   # This file
├── VERSION                     # Semantic version string
└── cpython.txt                 # cProfile output (old monolithic script)
```

## Licence

See repository for licence details.
