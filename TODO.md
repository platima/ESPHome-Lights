# TODO.md — Persistent Task Tracker

This file is the persistent plan across sessions.  If a session is lost, the
next session picks up from here.

---

## Phase 1: Daemon + CLI Refactor

Goal: Refactor `esphome-lights.py` into a persistent daemon (`esphome-lightsd.py`)
and thin CLI client to eliminate ~4.2 s cold-start latency per invocation.

### 1.1 Daemon (`esphome-lightsd.py`)

- [ ] Scaffold `esphome-lightsd.py` with shebang and docstring
- [ ] Implement `.env` loading (same logic as current script)
- [ ] Implement `load_devices()` from `ESPHOME_LIGHTS_*` env vars
- [ ] Create Unix socket listener (`/tmp/esphome-lights.sock`, configurable
      via `ESPHOME_LIGHTS_SOCKET` env var, permissions `0o660`)
- [ ] Handle stale socket file on startup (check and remove before `bind()`)
- [ ] Establish persistent `aioesphomeapi` connections to all devices on startup
      (concurrent via `asyncio.gather()`)
- [ ] Implement automatic reconnection with exponential backoff
      (1 s, 2 s, 4 s, 8 s, max 30 s)
- [ ] Track per-device connection state (connected / connecting / disconnected)
- [ ] Subscribe to entity state changes on each connection
- [ ] Maintain in-memory state cache
      (`{device: {state, brightness, rgb, entity_type}}`)
- [ ] Implement JSON command handler for all commands:
  - [ ] `list` — return configured devices
  - [ ] `status` — return cached state for all devices
  - [ ] `set` with actions: `on`, `off`, `brightness`, `rgb`
  - [ ] `ping` — health check (return `pong`)
- [ ] Handle multiple concurrent client connections
- [ ] Implement graceful shutdown (SIGTERM, SIGINT): disconnect clients,
      remove socket file
- [ ] Error handling: unreachable devices on startup, disconnected device
      commands, client socket errors
- [ ] Entity handling: prefer `LightInfo` over `SwitchInfo`, skip `status_led`,
      reject brightness/RGB for switch entities

### 1.2 CLI Client (`esphome-lights.py` rewrite)

- [ ] Rewrite as thin client using only stdlib (`socket`, `json`, `sys`,
      `argparse`)
- [ ] No `aioesphomeapi` import — this is the whole point
- [ ] Preserve existing CLI interface exactly:
  - [ ] `--list`
  - [ ] `--status`
  - [ ] `--set <device> --on / --off / --brightness N / --rgb r,g,b`
  - [ ] `--bg` / `--background` flag
  - [ ] `--debug` flag (show full JSON response)
  - [ ] `--ping` (new: health check)
- [ ] Connect to Unix socket, send JSON, read response, print, exit
- [ ] 5-second timeout on socket operations
- [ ] Clear error if daemon not running: `"Error: esphome-lightsd is not
      running (socket not found)"`
- [ ] Match current output format (preserve automation compatibility)
- [ ] Exit code 0 on success, 1 on failure

### 1.3 systemd Service

- [ ] Create `esphome-lightsd.service` unit file
- [ ] `Type=simple`, `Restart=on-failure`, `RestartSec=5`
- [ ] `EnvironmentFile` pointing to `.env`
- [ ] Document installation steps in README

### 1.4 Testing

- [ ] Daemon starts and connects to configured devices
- [ ] `--list` returns device list from daemon
- [ ] `--status` returns cached state instantly
- [ ] `--set <device> --on/--off` response < 100 ms
- [ ] `--set <device> --brightness N` works
- [ ] `--set <device> --rgb r,g,b` works
- [ ] Daemon reconnects automatically on device drop
- [ ] Daemon handles SIGTERM gracefully
- [ ] CLI gives clear error if daemon not running
- [ ] Multiple concurrent CLI clients work
- [ ] `--bg` flag returns immediately
- [ ] `--debug` flag shows full JSON
- [ ] `--ping` health check works

### 1.5 Documentation & Cleanup

- [ ] Update README.md with daemon usage, systemd setup, architecture
- [ ] Update CLAUDE.md to reflect new architecture as current (not planned)
- [ ] Bump VERSION to 0.1.0 (minor — milestone complete)

---

## Phase 2: OpenClaw Skill Integration

- [ ] Create `SKILL.md` for OpenClaw skill registration
- [ ] Test skill loading via OpenClaw agent
- [ ] Document OpenClaw integration in README

---

## Performance Targets

| Metric                  | Current | Target    |
|-------------------------|---------|-----------|
| `--set` command (cold)  | ~4.2 s  | < 100 ms  |
| `--status` (all devices)| ~5–8 s  | < 50 ms   |
| `--list`                | ~1.5 s  | < 50 ms   |
| CLI client startup      | ~1.5 s  | < 30 ms   |

---

## Future / Nice to Have

- [ ] Evaluate Python 3.14 free-threaded support for async performance gains.

---

## Completed

- [x] Initial monolithic `esphome-lights.py` (working)
- [x] Create CLAUDE.md with full project context
- [x] Review daemon implementation plan
- [x] Create TODO.md (this file)
- [x] Create README.md
- [x] Create OpenClaw SKILL.md
- [x] Research `aioesphomeapi` Python 3.13 compatibility — resolved upstream
      in v44.0.0 (Feb 2026); update docs accordingly
- [x] Document Python 3.13 incompatibility root cause (Cython noise_encryption)
- [x] Confirm Python 3.13 working on Luckfox Pico — fix: `pip install
      --force-reinstall noiseprotocol` after any `pip uninstall noise`
