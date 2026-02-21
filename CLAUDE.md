# CLAUDE.md — AI Assistant Context

This file provides project context for AI coding assistants (GitHub Copilot,
Claude, etc.).  It is read at the start of each session so the assistant
understands the project without re-discovering everything.

## Project Overview

**ESPHome Lights** — A Python CLI tool (and soon daemon) for controlling
ESPHome smart lights and switches via the native ESPHome API.

The current single-script design (`esphome-lights.py`) suffers from ~4.2 s
cold-start latency per invocation (importing ~295 modules including
`aioesphomeapi`, protobuf, cryptography, plus the Noise protocol handshake).
The project is being refactored into a **persistent daemon + thin CLI client**
architecture to achieve sub-100 ms command response times.

### Target Architecture

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
- **Python interpreter:** `/home/luckfox/venv/bin/python` (Python 3.11)
  - System Python is 3.13 and is **NOT** compatible with `aioesphomeapi`
    due to the noise protocol import. Never use system Python for anything
    importing `aioesphomeapi`.

## Tech Stack

| Component            | Detail                                         |
|----------------------|------------------------------------------------|
| Language             | Python 3.11                                    |
| Async framework      | `asyncio` (stdlib)                             |
| ESPHome comms        | `aioesphomeapi` (Noise protocol, protobuf)     |
| IPC                  | Unix domain socket, newline-delimited JSON     |
| Process management   | systemd (on the Luckfox deployment target)     |
| Config               | Environment variables + `.env` file            |

## Key Files

| File                        | Purpose                                              |
|-----------------------------|------------------------------------------------------|
| `esphome-lights.py`         | CLI client (currently monolithic; being refactored)  |
| `esphome-lightsd.py`        | Daemon (planned — persistent connections + socket)   |
| `esphome-lightsd.service`   | systemd unit file (planned)                          |
| `.env`                      | Device config (one level up from script directory)   |
| `CLAUDE.md`                 | AI assistant context (this file)                     |
| `TODO.md`                   | Persistent task tracker across sessions              |
| `README.md`                 | User-facing documentation                            |
| `VERSION`                   | Semantic version string                              |
| `cpython.txt`               | cProfile output showing the 4.2 s cold-start cost   |

## Device Configuration

Devices are configured via environment variables (or a `.env` file loaded from
one directory above the script):

```
ESPHOME_LIGHTS_<LOCATION>="<host>:<port>|<encryption_key>"
```

- Location is lowercased for CLI use.
- Port is typically `6053` (native ESPHome API).
- Example:
  ```
  ESPHOME_LIGHTS_LIVING_ROOM="10.42.40.55:6053|J+YkHH7XC+4dQwWvPoF5kaz7tP4NY4HJNTL0QyZM1Rg="
  ```

## CLI Interface

```
esphome-lights.py --list                          # List configured lights
esphome-lights.py --status                        # Show on/off state
esphome-lights.py --set <light-id> --on           # Turn on
esphome-lights.py --set <light-id> --off          # Turn off
esphome-lights.py --set <light-id> --brightness N # Set brightness (0-255)
esphome-lights.py --set <light-id> --rgb r,g,b    # Set RGB colour
esphome-lights.py --ping                          # Health check (daemon mode)

Flags:
  --bg, --background   Fire and forget (return immediately)
  --debug              Wait for completion and show detailed results
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
  This file is the persistent plan — if a session is lost, the next session
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
- **Virtual environment:** `/home/luckfox/venv/` (Python 3.11) on the target.
- **Dependencies:** `aioesphomeapi` (installed in the venv).
- **Running the current script:**
  ```bash
  /home/luckfox/venv/bin/python esphome-lights.py --list
  ```
- **Profiling:** `cpython.txt` contains cProfile output for cold-start analysis.

## Daemon Protocol (Planned)

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
```

**Response format:**
```json
{"ok": true, "result": "Turned ON"}
{"ok": false, "error": "Device 'kitchen' not found"}
```

## Current State

- **Version:** 0.0.1
- **Status:** Working monolithic CLI script; daemon refactor planned.
- The monolithic script works correctly but has a ~4.2 s per-invocation cost
  due to heavy imports and per-call connection setup.

## Known Limitations

- **Cold-start latency:** ~4.2 s per command invocation (the entire reason
  for the daemon refactor).
- **No persistent connections:** Each invocation connects, authenticates
  (Noise protocol handshake), sends one command, and disconnects.
- **`--status` is slow:** Queries every device sequentially-ish per call;
  no state caching.
- **Python 3.13 incompatible:** `aioesphomeapi` noise protocol fails on
  system Python 3.13; must use the 3.11 venv.
