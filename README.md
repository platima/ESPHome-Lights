# ESPHome Lights

A Python CLI tool for controlling ESPHome smart lights and switches via the
native ESPHome API.  Designed as an
[OpenClaw](https://github.com/nicholasgriffintn/openclaw) skill for
voice/chat-driven home automation.

**Version:** 0.1.0

## Overview

ESPHome Lights lets you turn lights on/off, set brightness, and set RGB
colours from the command line.  Devices are discovered from environment
variables — no config files to manage.

The project uses a **persistent daemon + thin CLI client** architecture.  The
daemon keeps ESPHome API connections alive, eliminating the ~4.2 s cold-start
latency of the old monolithic script.  The CLI client uses only Python stdlib
for fast startup and communicates with the daemon over a Unix domain socket.

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

- **Python 3.11 or 3.13** — both confirmed working with `aioesphomeapi` 44.0.0.
  > **Gotcha:** `noise` 1.2.2 (Perlin noise) and `noiseprotocol` both install
  > into the same `noise/` directory.  If you ever `pip uninstall noise`, run
  > `pip install --force-reinstall noiseprotocol` immediately after to restore
  > the directory that `aioesphomeapi` needs.
- **`aioesphomeapi`** — install via pip
- ESPHome devices with the native API enabled and encryption keys configured

## Installation

```bash
# Clone the repository
git clone https://github.com/platima/ESPHome-Python.git
cd ESPHome-Python

# Ensure the venv has aioesphomeapi
/home/luckfox/venv/bin/pip install aioesphomeapi
```

## Device Configuration

Devices are configured via environment variables.  Each variable follows the
format:

```
ESPHOME_LIGHTS_<LOCATION>="<host>:<port>|<encryption_key>"
```

- **Location** is uppercased in the variable name and lowercased for CLI use.
- **Port** is typically `6053` (native ESPHome API).

### Example

Set variables directly or place them in a `.env` file one directory above the
script:

```bash
# In .env (or export in your shell)
ESPHOME_LIGHTS_LIVING_ROOM="10.42.40.55:6053|J+YkHH7XC+4dQwWvPoF5kaz7tP4NY4HJNTL0QyZM1Rg="
ESPHOME_LIGHTS_BEDROOM="10.42.40.56:6053|another_key_here"
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
esphome-lights.py --list

# Show on/off state of all lights (from daemon cache — instant)
esphome-lights.py --status

# Turn a light on or off
esphome-lights.py --set living_room --on
esphome-lights.py --set living_room --off

# Set brightness (0–255)
esphome-lights.py --set living_room --brightness 128

# Set RGB colour (r,g,b — each 0–255)
esphome-lights.py --set living_room --rgb 255,0,0

# Health check
esphome-lights.py --ping
```

### Flags

| Flag                   | Effect                                          |
|------------------------|-------------------------------------------------|
| `--bg`, `--background` | Fire and forget — return immediately            |
| `--debug`              | Show full JSON response (overrides `--bg`)      |

## Entity Handling

- Prefers **`LightInfo`** entities for brightness and RGB control.
- Falls back to **`SwitchInfo`** for simple on/off devices (smart plugs, etc.).
- Always skips entities with `object_id == 'status_led'`.
- Brightness and RGB commands return errors for switch-type entities.

## Daemon Architecture

### Protocol

The daemon listens on a Unix socket (`/tmp/esphome-lights.sock` by default,
configurable via `ESPHOME_LIGHTS_SOCKET`).  Communication uses
newline-delimited JSON.

**Requests:**

```json
{"cmd": "list"}
{"cmd": "status"}
{"cmd": "set", "device": "living_room", "action": "on"}
{"cmd": "set", "device": "living_room", "action": "brightness", "value": "128"}
{"cmd": "set", "device": "living_room", "action": "rgb", "value": "255,0,0"}
{"cmd": "ping"}
```

**Responses:**

```json
{"ok": true, "result": "Turned ON"}
{"ok": false, "error": "Device 'kitchen' not found"}
{"ok": true, "result": "pong"}
```

### Daemon Features

- **Persistent connections** to all configured ESPHome devices
- **Automatic reconnection** with exponential backoff (1 s → 30 s max)
- **State caching** — `--status` returns instantly from cache
- **Multiple concurrent clients** supported
- **Graceful shutdown** on SIGTERM/SIGINT (cleans up socket file)
- **Stale socket detection** — removes orphaned socket files on startup
- **Configurable logging** via `ESPHOME_LIGHTS_LOG_LEVEL` env var

### systemd

A systemd unit file (`esphome-lightsd.service`) is provided for auto-starting
the daemon on boot:

```bash
sudo cp esphome-lightsd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now esphome-lightsd
```

To check daemon status:
```bash
sudo systemctl status esphome-lightsd
sudo journalctl -u esphome-lightsd -f
```

### Performance Targets

| Metric                  | Current | Target    |
|-------------------------|---------|-----------|
| `--set` command         | ~4.2 s  | < 100 ms  |
| `--status` (all devices)| ~5–8 s  | < 50 ms   |
| `--list`                | ~1.5 s  | < 50 ms   |
| CLI client startup      | ~1.5 s  | < 30 ms   |

## OpenClaw Integration

ESPHome Lights is designed as an [OpenClaw](https://github.com/nicholasgriffintn/openclaw)
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
flags.  Natural-language requests like *"turn on the living room light"* are
translated to CLI commands automatically.

### Skill Installation

The `SKILL.md` file at the repository root registers ESPHome Lights as an
OpenClaw skill.  To install:

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

This project is designed to run on a **Luckfox Pico** (ARM Linux SBC) — a
resource-constrained device.  The daemon architecture is deliberately kept
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
├── esphome-lights.py           # Thin CLI client (stdlib only)
├── esphome-lightsd.py          # Daemon (persistent connections + socket)
├── esphome-lightsd.service     # systemd unit file
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
