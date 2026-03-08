<img align="right" src="https://visitor-badge.laobi.icu/badge?page_id=platima.esphome-lights" height="20" />

# ESPHome Lights for Python / OpenClaw

A Python CLI tool for controlling ESPHome smart lights and switches via the
native ESPHome API, designed as an
[OpenClaw](https://github.com/openclaw/openclaw) skill for
voice/chat-driven home automation.

**Version:** 0.2.1

## Overview

ESPHome Lights lets you turn lights on/off, set brightness, and set RGB
colours from the command line. Devices are discovered from environment
variables; no separate config files to manage.

The project uses a **persistent daemon + shell CLI** architecture. The
daemon keeps ESPHome API connections alive. The CLI is a shell script that
talks to the daemon socket directly via `socat` or `nc`, achieving sub-10ms
overhead on ARM targets (vs ~350ms for a Python CLI on the same hardware).

### Architecture

```
CLI (shell)  —(Unix socket)—>  Daemon  —(persistent ESPHome API connections)—>  Devices
```

| Component              | File                      | Purpose                              |
|------------------------|---------------------------|--------------------------------------|
| Shell CLI              | `esphome-lights`          | Fast path: socat/nc socket client    |
| Python CLI             | `esphome-lights.py`       | Formatting fallback (list/status)    |
| Daemon                 | `esphome-lightsd.py`      | Persistent connections, state cache  |
| systemd unit           | `esphome-lightsd.service` | Auto-start on boot                   |
| Tests                  | `tests/`                  | Unit tests (63 tests)                |
| OpenClaw skill         | `SKILL.md`                | Chat-driven control via OpenClaw     |

## Requirements

- **Python 3.11** - required for the daemon due to `aioesphomeapi` + Noise
  encryption compatibility on ARM targets. Python 3.12/3.13 has issues with
  the compiled `noise` extension on ARM (e.g. Luckfox Pico, Orange Pi).
  The CLI shell wrapper uses only `bash` + `socat`/`nc`; Python is only
  invoked for `--list`/`--status` output formatting.
  > **Gotcha:** `noise` 1.2.2 (Perlin noise) and `noiseprotocol` both install
  > into the same `noise/` directory. The installer force-reinstalls
  > `noiseprotocol` after `aioesphomeapi` to guard against this automatically.
- **`socat`** (recommended) or **`nc` with Unix socket support** — for the
  fast CLI path. Falls back to a Python one-liner if neither is available,
  but with ~150ms overhead instead of ~10ms.
- **`aioesphomeapi`** - installed into the venv by the installer
- ESPHome devices with the native API enabled and encryption keys configured

## Installation

The installer sets up a **user-level systemd service** (no sudo required). It
will check for dependencies, create a config template, and optionally register
the OpenClaw skill.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/platima/ESPHome-Lights/main/install.sh)
# or
bash <(wget -qO- https://raw.githubusercontent.com/platima/ESPHome-Lights/main/install.sh)
```

Or, if you have already cloned the repo:

```bash
git clone https://github.com/platima/ESPHome-Lights.git
cd ESPHome-Lights
bash install.sh
```

**Options:**

| Flag          | Effect                                                  |
|---------------|---------------------------------------------------------|
| *(none)*      | Interactive install/update                              |
| `--fast`      | Non-interactive: accept all safe defaults, no prompts   |
| `--uninstall` | Remove the daemon, scripts, service, and OpenClaw link  |

The `--fast` flag is useful for scripting or CI. Safe defaults: auto-create
OpenClaw skill if detected, skip env template if OpenClaw `.env` exists,
no prompts.

`--uninstall` stops + disables the service, removes scripts and symlinks,
and offers to remove the venv (default: remove) and config dir (default: keep).

1. Refuse to run as root.
2. Install scripts to `~/.local/lib/esphome-lights/` with symlinks in `~/.local/bin/`.
3. Create a Python 3.11 venv at `~/.local/lib/esphome-lights/venv` and install `aioesphomeapi` there (with the `noiseprotocol` fix applied automatically).
4. Check for a config file and offer to create a template if none exists.
5. Install and enable a systemd user service (using the venv Python) with the socket at `$XDG_RUNTIME_DIR/esphome-lights.sock`.
6. Enable `loginctl linger` so the daemon starts at boot without requiring login.
7. Detect `~/.openclaw` and offer to register the skill if OpenClaw is installed.
8. Warn if `socat`/`nc` are not found (needed for the ~10ms fast path; falls back to Python one-liner at ~150ms).

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
export ESPHOME_LIGHTS_LIVING_ROOM="192.168.1.101:6053|A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4Y5z6A7b8C9d0="
export ESPHOME_LIGHTS_BEDROOM="192.168.1.102:6053|Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2H1g0F9e8D7c6B5a4Z3y2X1w0="
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

# Turn a light/switch on or off
esphome-lights --device living_room --on
esphome-lights --device living_room --off

# Turn ALL devices on or off at once
esphome-lights --device all --on
esphome-lights --device all --off

# Set brightness (0-255)
esphome-lights --device living_room --brightness 128

# Set RGB colour (r,g,b, each 0-255)
esphome-lights --device living_room --rgb 255,0,0

# Health check
esphome-lights --ping

# Reload config without restarting the daemon
esphome-lights --reload
```

### Flags

| Flag                   | Effect                                          |
|------------------------|-------------------------------------------------|
| `--device DEVICE`      | Device name to control, or `all` for every device |
| `--bg`, `--background` | Fire and forget - return immediately            |
| `--debug`              | Show full JSON response (overrides `--bg`)      |

> `--set` is kept as a backward-compatible alias for `--device`.

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
{"cmd": "set", "device": "all", "action": "on"}
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

### Performance

| Metric                    | Old (monolithic Python) | Current          |
|---------------------------|-------------------------|------------------|
| `--device` command        | ~4.2 s                  | **~10 ms** ¹     |
| `--status` (all devices)  | ~5–8 s                  | **< 50 ms** ²    |
| `--list`                  | ~1.5 s                  | **< 50 ms** ²    |
| CLI startup overhead      | ~1.5 s                  | **~10 ms** ¹     |

¹ Shell CLI via `socat`/`nc` on ARM (RK3576 measured). Python fallback ~150ms.  
² Delegates to Python for formatting; daemon response itself is < 1ms.

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
   ln -s /path/to/ESPHome-Lights ~/.openclaw/skills/esphome-lights
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

63 tests covering daemon command handlers, socket protocol, entity resolution,
state caching, client-daemon integration, and all-device wildcard broadcast.

> **Platform note:** Tests are developed and CI'd on Linux. 8 tests that
> exercise Unix domain socket I/O show as ERRORs on Windows/macOS (Unix
> sockets are not available). All 63 tests pass on Linux.

## File Structure

```
ESPHome-Lights/
├── install.sh                  # One-line installer (user-level, no sudo)
├── esphome-lights              # Shell CLI wrapper (fast path: socat/nc)
├── esphome-lights.py           # Python CLI (list/status formatting, fallback)
├── esphome-lightsd.py          # Daemon (persistent connections + socket)
├── esphome-lightsd.service     # systemd unit file (system-level reference)
├── tests/
│   ├── test_daemon.py          # Daemon unit tests (51 tests)
│   └── test_client.py          # Client unit + integration tests (12 tests)
├── SKILL.md                    # OpenClaw skill definition
├── CLAUDE.md                   # AI assistant context
├── TODO.md                     # Persistent task tracker
├── README.md                   # This file
├── VERSION                     # Semantic version string
└── .gitattributes              # LF line-ending enforcement
```

## Licence

MIT — see [LICENSE](LICENSE) for details.
