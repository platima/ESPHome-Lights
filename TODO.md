# TODO.md — Persistent Task Tracker

This file is the persistent plan across sessions. If a session is lost, the
next session picks up from here.

---

## Future / Nice to Have

- [ ] Benchmark daemon performance on the Luckfox Pico target hardware.
- [ ] Add `--log-file` CLI override flag for ad-hoc log path without env var.
- [ ] Evaluate Python 3.14 free-threaded support for async performance gains.
- [ ] Test OpenClaw skill loading against a live OpenClaw agent.
- [ ] Add shell CLI tests (bash bats or similar) for the socat/nc fast path.

---

## Completed

### Phase 0: Foundation

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

### Phase 1: Daemon + CLI Refactor (v0.1.0)

- [x] Scaffold `esphome-lightsd.py` with shebang and docstring
- [x] Implement `.env` loading (same logic as current script)
- [x] Implement `load_devices()` from `ESPHOME_LIGHTS_*` env vars
- [x] Create Unix socket listener (`/tmp/esphome-lights.sock`, configurable
      via `ESPHOME_LIGHTS_SOCKET` env var, permissions `0o660`)
- [x] Handle stale socket file on startup (check and remove before `bind()`)
- [x] Establish persistent `aioesphomeapi` connections to all devices on startup
      (concurrent via `asyncio.gather()`)
- [x] Implement automatic reconnection with exponential backoff
      (1 s, 2 s, 4 s, 8 s, max 30 s)
- [x] Track per-device connection state (connected / connecting / disconnected)
- [x] Subscribe to entity state changes on each connection
- [x] Maintain in-memory state cache
      (`{device: {state, brightness, rgb, entity_type}}`)
- [x] Implement JSON command handler for all commands:
  - [x] `list` — return configured devices
  - [x] `status` — return cached state for all devices
  - [x] `set` with actions: `on`, `off`, `brightness`, `rgb`
  - [x] `ping` — health check (return `pong`)
- [x] Handle multiple concurrent client connections
- [x] Implement graceful shutdown (SIGTERM, SIGINT): disconnect clients,
      remove socket file
- [x] Error handling: unreachable devices on startup, disconnected device
      commands, client socket errors
- [x] Entity handling: prefer `LightInfo` over `SwitchInfo`, skip `status_led`,
      reject brightness/RGB for switch entities
- [x] Rewrite CLI as thin stdlib-only client (`socket`, `json`, `sys`, `argparse`)
- [x] Preserve existing CLI interface: `--list`, `--status`, `--set`,
      `--on/--off/--brightness/--rgb`, `--bg`, `--debug`, `--ping`
- [x] Create `esphome-lightsd.service` systemd unit file
- [x] Create unit tests (49 tests: daemon handlers, socket protocol,
      entity resolution, state caching, client-daemon integration)
- [x] Update README.md, CLAUDE.md, TODO.md
- [x] Bump VERSION to 0.1.0

### Phase 2: OpenClaw + Code Quality (v0.1.1)

- [x] Create `SKILL.md` for OpenClaw skill registration
- [x] Document OpenClaw integration in README
- [x] Code review merge: surface entity type (light/switch) in `--list`
      and `--status` output; enrich `--status` with brightness/RGB for ON lights
- [x] Add `install.sh`: one-line user-level installer (no sudo required)
  - Refuses to run as root
  - Installs to `~/.local/lib/esphome-lights/` with `~/.local/bin/` symlinks
  - Generates systemd user service with socket at `$XDG_RUNTIME_DIR`
  - Enables `loginctl linger` for boot-time start
  - Checks `~/.config/esphome-lights/env`, offers template creation
  - Detects `~/.openclaw` and offers skill registration
- [x] Fix README/CLAUDE.md/SKILL.md typography (em-dashes, double spaces,
      broken URLs)
- [x] Update SKILL.md output descriptions for entity type and light details
- [x] Bump VERSION to 0.1.1

### Phase 3: OpenClaw .env priority + live reload (v0.1.2)

- [x] Priority-ordered env file loading in daemon:
      `~/.openclaw/workspace/.env` > `~/.config/esphome-lights/env` > legacy
- [x] `DeviceManager.handle_reload()` - diffs old vs new config, reconnects
      changed/new devices, disconnects removed devices, returns summary string
- [x] `SocketServer._dispatch()` made async; added `reload` command that
      calls `load_env()` + `load_devices()` + `manager.handle_reload()`
- [x] `main()` updated with SIGHUP handler and reload event loop
- [x] `esphome-lights --reload` CLI flag sends `{"cmd": "reload"}`
- [x] `install.sh` updated: no `EnvironmentFile=` in service (Python handles
      it); OpenClaw `.env` detection and messaging; reload instructions in
      next-steps output
- [x] Tests updated: dispatch tests use `asyncio.run()`, new
      `TestLoadEnvPriority`, `TestDeviceManagerReload`, `--reload` client test
- [x] Docs updated: README/CLAUDE.md/SKILL.md env priority and reload docs
- [x] Bump VERSION to 0.1.2

### Phase 5: Reliability, installer polish, and UX improvements (v0.1.5)

- [x] Fix `set_on_disconnect` removal: replaced with `on_stop` async callback
      parameter to `client.connect()` (aioesphomeapi API change in v44)
- [x] Start socket server before connecting devices so CLI can poll status
      during daemon startup (no 10 s socket-not-found window)
- [x] Installer `--uninstall` flag: stops/disables service, removes
      scripts/symlinks/skill link, optionally removes venv and config
- [x] Installer `--fast` flag: non-interactive mode accepting all safe
      defaults (for scripting and CI)
- [x] Fix venv creation without sudo: `--without-pip` fallback so
      `python3.11-venv` Debian package is not required; pip bootstrapped
      via get-pip.py
- [x] Always force-reinstall `noiseprotocol` on every install to guard
      against the `noise`/`noiseprotocol` namespace collision regression
- [x] Fix socket path mismatch: both daemon and CLI derive socket path
      from `$XDG_RUNTIME_DIR`; removed `Environment=` from service file
- [x] Fix `python3.11-venv` separate check (redundant after --without-pip
      fix, removed)
- [x] Suppress venv and pip install stdout noise in installer output
- [x] Installer explicitly start/restart service after install (no silent
      auto-start from systemd)
- [x] Sanitise env template: replace real IPs and keys with obviously fake
      example values
- [x] Remove blank lines from installer output: after installer header,
      before "Uninstall complete.", before "Installation complete!"
- [x] `handle_set(device='all')`: broadcast on/off/brightness/rgb to every
      device; returns per-device summary; ok=True if any succeeded
- [x] CLI: rename `--set` to `--device`; keep `--set` as hidden backward-
      compatible alias
- [x] Tests: 3 new `handle_set('all', ...)` tests (all connected, partial
      disconnect, none connected)
- [x] Update SKILL.md, README.md, CLAUDE.md, TODO.md
- [x] Bump VERSION to 0.1.5

- [x] Fix test_send_reload hermeticity: patch load_env/load_devices so test
      does not read real env files or attempt real device connections (v0.1.3)
- [x] Copy SKILL.md to $INSTALL_LIB and always update skill symlink on
      reinstall instead of silently skipping (v0.1.3)
- [x] Fix daemon Noise encryption failure on ARM targets: require Python 3.11
      specifically; create venv at `~/.local/lib/esphome-lights/venv`; install
      aioesphomeapi in venv; point systemd ExecStart at venv python (v0.1.4)
- [x] Correct CLAUDE.md/README.md Python version notes (3.11 venv required,
      3.12/3.13 has ARM noise/Cython incompatibility) (v0.1.4)
### Phase 6: Production readiness + shell CLI wrapper (v0.1.6–v0.1.7)

- [x] Add `.gitattributes` to enforce LF line endings (install.sh breaks with
      CRLF on Windows git clones)
- [x] Expand `.gitignore`: `.vscode/`, `*.code-workspace`, `.env`, `venv/`
- [x] Remove dev cruft from repo: `cpython.txt`, `.code-workspace`
- [x] Fix deprecated `asyncio.get_event_loop()` → `get_running_loop()`
- [x] Fix README: test count, licence section, performance table
- [x] Fix CLAUDE.md: stale CLI examples, version, test count
- [x] Fix TODO.md: deduplicate sections
- [x] Bump to v0.1.6
- [x] Replace Python CLI with shell wrapper (`esphome-lights`) that uses
      `socat`/`nc` for direct socket I/O — ~10ms on ARM vs ~350ms Python
- [x] Python CLI (`esphome-lights.py`) retained as formatter for
      `--list`/`--status` and universal fallback
- [x] Update `install.sh`: copy + chmod shell wrapper; symlink it as the
      default `esphome-lights` command
- [x] Update README, CLAUDE.md, TODO.md with shell wrapper docs
- [x] Bump to v0.1.7
- [x] Rename repo from ESPHome-Python to ESPHome-Lights; update all URLs,
      badge, service Documentation= field, CLAUDE.md, README.md

### Phase 7: Persistent file logging (v0.2.2)

- [x] Add `_configure_logging()` — RotatingFileHandler (1 MB, 3 backups, ~4 MB
      total) attached after `load_env()` so env-file path/disable settings apply
- [x] Default log location: `~/.local/share/esphome-lights/esphome-lightsd.log`
- [x] `ESPHOME_LIGHTS_LOG_FILE` env var — override path or set to
      none/off/false/0 to disable; excluded from `load_devices()` parsing
- [x] Command audit logging in `SocketServer._dispatch()` — every command and
      its result logged at INFO with structured `cmd= device= action= → ok/error`
      format; long results (list/status) truncated to 120 chars
- [x] Daemon version logged at startup via `_DAEMON_VERSION` constant (reads
      VERSION file once at import time)
- [x] DEBUG log line in `_handle_state()` for state change updates
- [x] 12 new tests (TestConfigureLogging × 6, TestCommandAuditLogging × 6)
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION → 0.2.2
- [x] Bump to v0.2.0 (MINOR — shell wrapper milestone + rename)