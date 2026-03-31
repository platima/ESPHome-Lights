# TODO.md — Persistent Task Tracker

This file is the persistent plan across sessions. If a session is lost, the
next session picks up from here.

---

## Future / Nice to Have

- [ ] Benchmark daemon performance on the Luckfox Pico target hardware.
- [ ] Add `--log-file` CLI override flag for ad-hoc log path without env var.
- [ ] Add shell CLI tests (bash bats or similar) for the socat/nc fast path.
- [ ] Evaluate Python 3.14 free-threaded support for async performance gains.

---

## Completed

### v0.5.0 — Built-in web interface

- [x] Add `WebServer` class to `esphome-lightsd.py` — embedded async HTTP
      server using stdlib `asyncio.start_server`; no new dependencies.
- [x] Add REST API endpoints: `GET /api/list`, `/api/status`, `/api/ping`,
      `POST /api/set`, `/api/reload`, `/api/reconnect`.
- [x] Add SSE endpoint `GET /api/events` — streams real-time state updates to
      all connected browser clients whenever any device state changes.
- [x] Add `_sse_subscribers` queue list to `DeviceManager`; `_handle_state()`
      pushes a full status snapshot to all queues on every state change.
- [x] Add `_WEB_UI_HTML_RAW` / `_WEB_UI_BYTES` — complete inline
      single-page HTML/CSS/JS app; Solarized colours, auto light/dark via
      `prefers-color-scheme`, CSS Grid responsive layout, 44 px+ tap targets.
- [x] Device cards: toggle, brightness slider, RGB colour picker,
      colour-temp slider, CW/WW sliders, reconnect button — controls only
      shown for entity types that support them.
- [x] Disabled by default (`ESPHOME_LIGHTS_WEB_PORT=0`); enable with a
      non-zero port (suggested: 7890). Bind to `127.0.0.1` by default;
      `ESPHOME_LIGHTS_WEB_BIND=0.0.0.0` for LAN access.
- [x] Add `ESPHOME_LIGHTS_WEB_PORT` and `ESPHOME_LIGHTS_WEB_BIND` to
      `load_devices()` exclusion list so they aren't parsed as devices.
- [x] Wire `WebServer` into `main()` — starts after socket server, stops
      before socket server during graceful shutdown.
- [x] Add 24 new unit tests: `TestWebServerRoutes` (18 tests covering all
      REST routes, 404/405, body-size constant, version embed, env var
      exclusion) and `TestSSESubscribers` (5 tests for subscriber
      registration, multi-subscriber, full snapshot format, switch state).
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION (0.5.0).

### v0.4.1 — On-demand reconnect

- [x] Add `handle_reconnect` / `_reconnect_device` to `DeviceManager` — cancels
      backoff task and immediately retries the connection.
- [x] Add `{"cmd": "reconnect", "device": "<name>|all"}` to the daemon socket protocol.
- [x] Add `--device <name|all> --reconnect` to the bash wrapper and Python CLI.
- [x] Add 8 new unit tests (dispatch routing + reconnect handler + client integration).
- [x] Update README, CLAUDE.md, and VERSION (0.4.1).

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

### Phase 8: Installer upgrade/repair modes (v0.2.3)

- [x] Add `--upgrade` flag: git pull (if local clone), stop service, update
      scripts (including VERSION), upgrade pip packages, rewrite service file,
      start service; show v_old → v_new diff
- [x] Add `--repair` flag: stop service, reinstall scripts, recreate venv if
      missing (including pip bootstrap), reinstall/upgrade packages, rewrite
      service file, enable linger, start service
- [x] Extract shared helpers: `_stop_service`, `_install_scripts`,
      `_upgrade_deps`, `_write_service_file`, `_start_service`
- [x] Main install path detects existing installation and prompts:
      Upgrade / Repair / Fresh install / Cancel; auto-upgrades in --fast mode
- [x] Copy VERSION file to INSTALL_LIB on every install/upgrade
- [x] `bash -n` syntax check passes
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION → 0.2.3

### Phase 9: Installer health checks + OpenClaw workspace skill targeting (v0.2.4)

- [x] Replace OpenClaw skill install block with `_install_openclaw_skill()` helper
      called from main install path, do_upgrade, and do_repair
- [x] Skill target selector: [g] Global / [N] per-agent workspace / [o] Other;
      multiple choices accepted (e.g. `g 1 2`); upgrade/repair refresh existing
      links silently without re-prompting
- [x] Broken-install health checks in the existing-install detection block:
      venv missing, service file missing, broken symlink, aioesphomeapi not
      importable, service in failed state — defaults menu to Repair if any found
- [x] Stop running service before overwriting scripts on fresh-over-existing path
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION → 0.2.4
- [x] Bump to v0.3.0 (MINOR — installer lifecycle + OpenClaw multi-target)

### Fix: SKILL.md PATH issue (v0.3.1)

- [x] Tested OpenClaw skill against a live agent — discovered PATH issue
      (agent couldn't find `esphome-lights` on PATH, fell back to calling
      `python3 esphome-lights.py` directly, bypassing the fast shell wrapper)
- [x] Removed `esphome-lights` from `requires.bins` (it's bundled, not a
      system binary)
- [x] Added "How to Invoke" section instructing the agent to use the full
      path to the shell wrapper within the skill directory
- [x] Updated all command examples to use `bash $SKILL_DIR/esphome-lights`
      pattern instead of bare `esphome-lights`
- [x] Added explicit note: never call `esphome-lights.py` directly
- [x] Update CLAUDE.md, TODO.md, VERSION → 0.3.1

### Fix: installer cp fails when target is a directory (v0.3.2)

- [x] `_install_scripts()` now removes stale directories/symlinks-to-dirs
      before copying files (defensive cleanup for leftover state)
- [x] Main install path refactored to call `_install_scripts()` instead of
      duplicating cp commands (DRY)
- [x] Update CLAUDE.md, TODO.md, VERSION → 0.3.2

### Fix: upgrade re-execs installer after git pull (v0.3.3)

- [x] `do_upgrade()` re-execs itself after `git pull` so fixes to
      `install.sh` take effect immediately (env var guard prevents loops;
      `--fast` flag preserved across re-exec)
- [x] `_install_scripts()` post-copy validation: warns if installed files
      are not regular files or missing execute permission
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION → 0.3.3

### Fix: ln -sf follows symlink-to-directory (v0.3.4)

- [x] All `ln -sf` calls changed to `ln -sfn` throughout install.sh
      (`-n` prevents following existing symlink-to-directory targets)
- [x] Root cause: `_install_openclaw_skill()` ran `ln -sf $INSTALL_LIB
      $target/esphome-lights` — when the target already pointed to a
      directory, `ln` created the link INSIDE the directory, overwriting
      the shell wrapper file with a symlink back to the parent dir
- [x] Update CLAUDE.md, TODO.md, VERSION → 0.3.4

### Chore: ASCII-only installer output (v0.3.5)

- [x] Replaced all Unicode characters in user-visible output with ASCII
      equivalents (em dash → `--`, arrow → `->`, cross → `x`)
- [x] Terminal on Luckfox Pico rendered `→` as `_` due to locale/font
- [x] Comments left unchanged (not rendered to terminal)
- [x] Update CLAUDE.md, TODO.md, VERSION → 0.3.5

### Chore: quiet git output + --verbose flag (v0.3.6)

- [x] `git pull` and `git clone` use `--quiet` by default (suppresses
      object count / delta resolution noise)
- [x] Added `--verbose` installer flag to restore full git/pip output
- [x] `--verbose` preserved across re-exec (same as `--fast`)
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION → 0.3.6
### Fix: uninstall polish + --install alias (v0.3.7)

- [x] Silenced `systemctl --user disable` output ("Removed ..." line
      was noisy in the uninstall flow)
- [x] Added `--install` flag as explicit alias for the default (no args)
      install behaviour
- [x] Updated help text and README flags table
- [x] Replaced remaining Unicode in daemon log messages (ellipsis, arrows,
      em dashes) and Python CLI error messages with ASCII equivalents
- [x] Updated test assertions to match new ASCII audit log format
- [x] Fixed test_oserror_on_dir_creation creating d:\no_such_root on
      Windows (now mocks os.makedirs instead of relying on unwritable path)
- [x] Added log file location and config hint to installer post-install output
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION -> 0.3.7

### Fix: missing newline in shell wrapper output (v0.3.8)

- [x] `_result()` in the shell wrapper now uses `printf '%s\n'` instead of
      `printf '%s'` -- fixes missing trailing newline on --ping, --reload,
      --on, --off, --brightness, --rgb output
- [x] Added `esphome-lights` (no extension) to `.gitattributes` for LF
      enforcement
- [x] Update README.md, CLAUDE.md, TODO.md, VERSION -> 0.3.8

### Fix: OpenClaw 2026.3.13 symlink security rejection (v0.3.9)

- [x] Added `_copy_skill_files()` helper in install.sh that deploys a real
      directory of the five skill files instead of a symlink to INSTALL_LIB
- [x] All six `ln -sfn "$INSTALL_LIB"` call-sites in `_install_openclaw_skill()`
      replaced with `_copy_skill_files` (refresh, fast, g, N, o paths)
- [x] Upgrade/repair path detects and migrates any existing legacy symlink
      to a real directory automatically
- [x] `do_uninstall()` updated: guard changed from `-L` to `-L || -d`,
      `rm -f` changed to `rm -rf` to handle real directories
- [x] Updated CLAUDE.md installation section (manual copy instructions
      instead of symlink)
- [x] Update CLAUDE.md, TODO.md, VERSION -> 0.3.9

### Feat: CW/WW and colour temperature support (v0.4.0)

- [x] Daemon `_handle_state()`: cache `color_temp` (Kelvin, converted from
      mireds), `cold_white`, and `warm_white` (0-255) for LightState updates
- [x] Daemon `handle_set()`: add `color_temp` action (Kelvin → mireds)
      and `cwww` action (cold,warm 0-255 → 0.0-1.0 float pair)
- [x] Both actions rejected with clear errors for switch-type entities
- [x] Python CLI `esphome-lights.py`: add `--color-temp` and `--cwww` flags,
      `--json` flag for raw JSON output on `--list`/`--status`
- [x] `format_status()` extended to display color_temp (Kelvin with K suffix)
      and cwww (cold,warm integers) for ON light entities
- [x] Shell wrapper `esphome-lights`: add `--color-temp`, `--cwww`, `--json`
      flag parsing; fix `--list`/`--status` delegation to pass `"$@"` so
      `--json` is forwarded correctly
- [x] 10 new unit tests: 8 handle_set tests (color_temp × 4, cwww × 4),
      2 state cache tests; existing test updated with new field assertions
- [x] README updated: symlink → copy for OpenClaw manual install
- [x] Update README.md, CLAUDE.md, SKILL.md, TODO.md, VERSION → 0.4.0