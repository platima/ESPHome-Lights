## 🏠 ESPHome Lights v0.2.0

### What's New

#### Sub-10ms shell CLI wrapper

The primary `esphome-lights` command is now a **bash script** that talks to the daemon socket directly via `socat` (preferred) or `nc`, cutting overhead from ~350ms down to **~10ms on ARM** for all control commands (`--on`, `--off`, `--brightness`, `--rgb`, `--ping`, `--reload`). The Python CLI is retained for `--list`/`--status` output formatting and as a universal fallback.

#### Repo renamed to ESPHome-Lights

All GitHub URLs, install one-liners, service `Documentation=` fields, and badge references have been updated. The old `ESPHome-Python` URL redirects automatically — no action needed for existing installs.

### Changes since v0.1.6

- **Shell wrapper** (`esphome-lights`) replaces Python as the fast path for all control commands
- **Python CLI** (`esphome-lights.py`) retained for `--list`, `--status`, and fallback
- **Installer** updated to copy + chmod the shell wrapper and symlink it as `esphome-lights`
- **Repo renamed** `platima/ESPHome-Python` → `platima/ESPHome-Lights`
- **Production polish**: `.gitattributes` (LF enforcement), expanded `.gitignore`, dev cruft removed (`cpython.txt`, `.code-workspace`)
- **Code quality**: `asyncio.get_event_loop()` → `get_running_loop()`, README test counts corrected, licence section clarified

### Performance

| Command | Before (Python CLI) | After (shell wrapper) |
|---------|--------------------|-----------------------|
| `--on` / `--off` | ~350ms (ARM) | ~10ms (ARM) |
| `--status` | instant (cache) | instant (cache) |
| `--list` | ~150ms | ~150ms (still Python) |

### Upgrading

Re-run the installer to get the shell wrapper:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/platima/ESPHome-Lights/main/install.sh)
```

No config changes required.
