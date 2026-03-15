# CLAUDE.md — AI Assistant Context

This file provides project context for AI coding assistants (GitHub Copilot,
Claude, etc.). It is read at the start of each session so the assistant
understands the project without re-discovering everything.

## Project Overview

**ESPHome Lights** — A Python daemon + thin CLI client for controlling
ESPHome smart lights and switches via the native ESPHome API.

The project uses a **persistent daemon + thin CLI client** architecture to
achieve sub-100 ms command response times (previously ~4.2 s per invocation
with the monolithic script).

### Architecture

```
CLI client  —(Unix socket)—>  Daemon  —(persistent ESPHome API connections)—>  Devices
```

- **`esphome-lightsd.py`** — Long-running async daemon that keeps ESPHome
  connections alive and listens on a Unix socket for JSON commands.
- **`esphome-lights.py`** — Thin CLI client using only stdlib (`socket`,
  `json`, `sys`, `argparse`). No heavy imports; fast startup.

## Dev Environment

- **Dev machine:** Windows 11 with VS Code as the primary IDE
- **WSL2:** Debian instance available for Linux-native tooling
- **Terminals:** PowerShell in VS Code; Debian WSL2 accessible if needed
- **Deployment target:** Luckfox Pico (ARM Linux SBC), resource-constrained
- **Python interpreter:** Daemon requires **Python 3.11** in a dedicated venv
  (`~/.local/lib/esphome-lights/venv`). Python 3.12/3.13 has Noise encryption
  compatibility issues on ARM (compiled `noise` C extension behaves differently).
  The CLI client is stdlib-only and uses system `python3`.
  - **`noiseprotocol` vs `noise` conflict** — both install into the same `noise/`
    directory. The installer force-reinstalls `noiseprotocol` after `aioesphomeapi`
    to avoid silent breakage. Fix manually: `venv/bin/pip install --force-reinstall noiseprotocol`.

## Tech Stack

| Component            | Detail                                         |
|----------------------|------------------------------------------------|
| Language             | Bash (CLI wrapper), Python 3.11 (daemon venv)  |
| Async framework      | `asyncio` (stdlib)                             |
| ESPHome comms        | `aioesphomeapi` (Noise protocol, protobuf)     |
| IPC                  | Unix domain socket, newline-delimited JSON     |
| CLI socket transport | `socat` (preferred) or `nc -U` (fallback)      |
| Process management   | systemd (on the Luckfox deployment target)     |
| Config               | Environment variables + `.env` file            |

## Key Files

| File                        | Purpose                                              |
|-----------------------------|------------------------------------------------------|
| `install.sh`                | One-line user-level installer (no sudo)              |
| `esphome-lights`            | Shell CLI wrapper (fast path via socat/nc, ~10ms)    |
| `esphome-lights.py`         | Python CLI (list/status formatting + fallback)       |
| `esphome-lightsd.py`        | Daemon (persistent connections + Unix socket)        |
| `esphome-lightsd.service`   | systemd unit file (system-level reference)           |
| `tests/`                    | Unit tests (test_daemon.py, test_client.py)          |
| `SKILL.md`                  | OpenClaw skill definition for chat-driven control    |
| `.env`                      | Device config (one level up from script directory)   |
| `venv/`                     | Python 3.11 venv (at `~/.local/lib/esphome-lights/venv`, not in repo) |
| `CLAUDE.md`                 | AI assistant context (this file)                     |
| `TODO.md`                   | Persistent task tracker across sessions              |
| `README.md`                 | User-facing documentation                            |
| `VERSION`                   | Semantic version string                              |


## Device Configuration

Devices are configured via environment variables.

```
ESPHOME_LIGHTS_<LOCATION>="<host>:<port>|<encryption_key>"
```

- Location is lowercased for CLI use.
- Port is typically `6053` (native ESPHome API).

The daemon loads env files in **priority order** (highest to lowest). Later
files override earlier ones, so the highest-priority source wins:

1. `~/.openclaw/workspace/.env` -- OpenClaw workspace (if present)
2. `~/.config/esphome-lights/env` -- installer-managed config file
3. `{script_dir}/../.env` -- legacy fallback for manual installs

The systemd unit no longer uses `EnvironmentFile=`; Python handles all loading
so that SIGHUP-triggered reloads pick up changes immediately.

- Example:
  ```
  ESPHOME_LIGHTS_LIVING_ROOM="192.168.1.101:6053|A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4Y5z6A7b8C9d0="
  ESPHOME_LIGHTS_LOG_FILE="none"   # set to none/off/false/0 to disable file logging
  ```

## CLI Interface

```
esphome-lights --list                              # List configured lights
esphome-lights --status                            # Show on/off state
esphome-lights --device <id|all> --on              # Turn on  (~10ms via socat)
esphome-lights --device <id|all> --off             # Turn off (~10ms via socat)
esphome-lights --device <id|all> --brightness N    # Set brightness (0-255)
esphome-lights --device <id|all> --rgb r,g,b       # Set RGB colour
esphome-lights --ping                              # Health check (daemon mode)
esphome-lights --reload                            # Reload config without restart

Flags:
  --bg, --background   Fire and forget (return immediately)
  --debug              Wait for completion and show detailed results (delegates to Python)
```

## Entity Handling

- Prefer `LightInfo` entities for brightness/RGB control.
- Fall back to `SwitchInfo` for simple on/off devices (smart plugs, etc.).
- Always skip entities with `object_id == 'status_led'`.
- Brightness and RGB commands return errors for switch-type entities.

## Conventions

### Language

Australian English in **all** comments, log messages, and documentation.
Examples: initialise, behaviour, colour, licence, serialisation, organisation,
optimise, minimise, recognise.

### Versioning (SemVer)

Semantic Versioning tracked in the `VERSION` file at the repo root.

| Bump  | When                                               |
|-------|----------------------------------------------------|
| PATCH | Each individual commit (bug fix, small improvement) |
| MINOR | Phase or milestone complete (push + update README)  |
| MAJOR | Breaking protocol or API change                     |

### Git Workflow

1. Create a **feature or fix branch** off `master` (`feature/<name>`, `fix/<name>`).
2. Make changes, commit with a **Conventional Commits** message
   (`feat:`, `fix:`, `chore:`, `docs:`).
3. **Bump the PATCH** version in `VERSION` with each commit.
4. When the phase/milestone is complete: bump **MINOR**, update `README.md`,
   commit, and push.
5. Merge back to `master`.

### Documentation & Testing

- **Update docs with every change.** If a feature, config, or file changes,
  update `README.md`, `CLAUDE.md`, and inline comments in the same commit.
- **Create documentation if it's missing.** Never leave a new subsystem
  undocumented.
- **Update `TODO.md`** when tasks are completed or new work is identified.
  This file is the persistent plan - if a session is lost, the next session
  picks up from `TODO.md`.

### Standard Task Completion Checklist

Every piece of work (feature, fix, refactor) must complete **all** of these
steps before the task is considered done. Do not skip steps, and do not batch
them silently — each must be visible in the plan.

1. Implement the change.
2. Update or create unit tests to cover the change.
3. Run unit tests — fix and repeat until all pass.
4. Update inline code comments (Australian English).
5. Update `README.md` if the change affects usage, structure, or config.
6. Update `CLAUDE.md` if the change affects project context.
7. Update `TODO.md` — mark completed items, add new items if identified.
8. Bump version in `VERSION` (PATCH per commit, MINOR per milestone).
9. `git add -A && git commit` with a Conventional Commits message.
10. At milestone completion: bump MINOR, push, update README version.

## Build & Test

- **No build step** — pure Python, interpreted.
- **Dependencies:** `aioesphomeapi` (daemon only; CLI client is stdlib-only).
- **Running the daemon:**
  ```bash
  python3 esphome-lightsd.py
  ```
- **Running CLI commands:**
  ```bash
  python3 esphome-lights.py --list
  ```
- **Running tests:**
  ```bash
  python3 -m unittest discover -s tests -v
  ```
- **Profiling:** The old `cpython.txt` cProfile output has been removed from
  the repo (still in git history for reference).

## Daemon Protocol

Unix socket at `/tmp/esphome-lights.sock` (configurable via
`ESPHOME_LIGHTS_SOCKET` env var). Newline-delimited JSON.

**Request examples:**
```json
{"cmd": "list"}
{"cmd": "status"}
{"cmd": "set", "device": "living_room", "action": "on"}
{"cmd": "set", "device": "living_room", "action": "brightness", "value": "128"}
{"cmd": "set", "device": "living_room", "action": "rgb", "value": "255,0,0"}
{"cmd": "ping"}
{"cmd": "reload"}
```

The `reload` command re-reads all env files (priority order), rebuilds the
device list, then disconnects removed/changed devices and connects new/changed
ones. It returns a summary string (e.g. `added: 0, removed: 0, changed: 0,
unchanged: 2`).

The daemon also handles **SIGHUP** for OS-level reloads:
```bash
systemctl --user kill -s HUP esphome-lightsd
```

**Response format:**
```json
{"ok": true, "result": "Turned ON"}
{"ok": false, "error": "Device 'kitchen' not found"}
```

## OpenClaw Integration

This project is designed as an [OpenClaw](https://github.com/openclaw/openclaw)
skill. OpenClaw is a self-hosted AI gateway that bridges messaging platforms
(WhatsApp, Telegram, Discord, Slack, etc.) with AI agents.

### How It Fits

```
User (WhatsApp/Telegram/etc.)
  → OpenClaw Gateway
    → Agent (with exec tool)
      → bash <skill-dir>/esphome-lights --device living_room --on
        → ESPHome device
```

- The `SKILL.md` at the repo root registers ESPHome Lights as an OpenClaw skill.
- The OpenClaw agent reads the skill definition and uses its `exec` tool to run
  CLI commands via the **shell wrapper** in the skill directory.
- `SKILL.md` instructs agents to invoke `bash $SKILL_DIR/esphome-lights` using
  the full path — the command is NOT expected to be on `$PATH`.
- Natural-language requests like *“turn on the living room”* are translated to
  the appropriate shell wrapper invocation automatically.
- OpenClaw's cron system can schedule automated light control (e.g. lights on
  at sunset).

### Skill File

`SKILL.md` uses the AgentSkills format (YAML frontmatter + Markdown
instructions). Key metadata fields:

- `requires.bins` - binaries that must exist on `PATH`
- `requires.env` - environment variables the skill depends on
- `requires.config` - OpenClaw config keys that must be truthy

### Installation

`install.sh` handles OpenClaw skill registration automatically via an
interactive target selector. Multiple targets can be selected at once.
To link manually:

```bash
# Global (all agents)
ln -s /path/to/ESPHome-Lights ~/.openclaw/skills/esphome-lights

# Per-agent workspace
ln -s /path/to/ESPHome-Lights ~/.openclaw/workspace-layla/skills/esphome-lights
```

Ensure `ESPHOME_LIGHTS_*` env vars are available to the agent.

## Current State

- **Version:** 0.3.6
- **Status:** Shell CLI wrapper + daemon architecture. Control commands (on/off/brightness/rgb/ping/reload) achieve sub-10ms response times via socat/nc on ARM.
- `install.sh` supports `--upgrade` (git pull + update scripts/packages + restart), `--repair` (full reinstall without git pull), and `--uninstall`. Detecting an existing install runs health checks (venv, service file, symlinks, aioesphomeapi import) and defaults to Repair if issues are found.
- OpenClaw skill installer offers Global / per-agent workspace / custom path with multi-select; upgrade/repair refresh existing links silently.
- The shell wrapper (`esphome-lights`) handles all control commands natively; delegates `--list`/`--status`/`--debug` to `esphome-lights.py`.
- The Python CLI (`esphome-lights.py`) is retained for complex output formatting and as a universal fallback.
- The daemon (`esphome-lightsd.py`) maintains persistent connections and serves commands via a Unix domain socket.
- `install.sh` installs as a systemd user service (no sudo required), checks for config, and offers OpenClaw skill registration. Supports `--fast` (non-interactive) and `--uninstall` flags.
- `--device all` broadcasts commands to every device at once.
- 75 unit tests covering daemon handlers, socket protocol, entity resolution, state caching, client-daemon integration, all-device wildcard broadcast, file logging config, and command audit logging.

## Known Limitations

- **Performance not yet benchmarked** on the Luckfox Pico target hardware.
  RK3576 measured ~10ms for control commands via socat; similar expected on
  Luckfox Pico.
- **`--list` and `--status` still use Python** for output formatting (~150ms
  on ARM). These are informational commands so latency is less critical.
- **Python 3.13 confirmed working** on the Luckfox Pico with
  `aioesphomeapi` 44.0.0. See the Dev Environment section for the
  `noiseprotocol` gotcha.
