#!/usr/bin/env python3
"""
esphome-lightsd.py — Persistent daemon for ESPHome smart light control

Maintains persistent connections to ESPHome devices and serves commands
via a Unix domain socket.  Eliminates the ~4.2 s cold-start latency of
per-invocation imports and Noise protocol handshakes.

Socket protocol: newline-delimited JSON on a Unix domain socket.

Request examples:
  {"cmd": "list"}
  {"cmd": "status"}
  {"cmd": "set", "device": "living_room", "action": "on"}
  {"cmd": "set", "device": "living_room", "action": "brightness", "value": "128"}
  {"cmd": "set", "device": "living_room", "action": "rgb", "value": "255,0,0"}
  {"cmd": "set", "device": "living_room", "action": "color_temp", "value": "2700"}
  {"cmd": "set", "device": "living_room", "action": "cwww", "value": "180,60"}
  {"cmd": "ping"}
  {"cmd": "reload"}
  {"cmd": "reconnect", "device": "living_room"}
  {"cmd": "reconnect", "device": "all"}

Response format:
  {"ok": true, "result": ...}
  {"ok": false, "error": "..."}

Configuration (loaded in priority order, highest wins):
  ~/.openclaw/workspace/.env          — shared OpenClaw workspace config
  ~/.config/esphome-lights/env        — per-service config (installer default)
  {script_dir}/../.env                — legacy fallback

  ESPHOME_LIGHTS_<LOCATION>="<host>:<port>|<encryption_key>"
  ESPHOME_LIGHTS_SOCKET="/tmp/esphome-lights.sock"  (optional, default shown)

Reload:
  Send SIGHUP or {"cmd": "reload"} to re-read config files and reconnect
  added/changed/removed devices without restarting the daemon.

Web interface (disabled by default):
  Set ESPHOME_LIGHTS_WEB_PORT to a non-zero port to enable a browser-based
  control UI with real-time updates via Server-Sent Events.

  ESPHOME_LIGHTS_WEB_PORT=7890     (optional; 0 = disabled, the default)
  ESPHOME_LIGHTS_WEB_BIND=localhost (optional; default: localhost — local access only)

  WEB_BIND accepts: localhost/local/127.0.0.1 (local only) or any/all/lan/0.0.0.0
  (LAN access).  No authentication is provided; rely on network-level access
  controls when exposing on the LAN.
  Suggested port: 7890 (not used by WHMCS/Immich/Frigate/Home Assistant/ESPHome).
"""

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys

from aioesphomeapi import APIClient

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Values that disable file logging when set in ESPHOME_LIGHTS_LOG_FILE.
_LOG_FILE_DISABLED_VALUES = frozenset({"none", "off", "false", "0", "no", ""})

# Daemon version — read once at import time from the VERSION file.
_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
try:
    with open(_VERSION_FILE) as _vf:
        _DAEMON_VERSION = _vf.read().strip()
except OSError:
    _DAEMON_VERSION = "unknown"

# Basic console handler — active immediately so startup messages are visible
# even before _configure_logging() adds the file handler.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("esphome-lightsd")


def _configure_logging():
    """Attach a rotating file handler and apply the configured log level.

    Called from main() after load_env() so that ESPHOME_LIGHTS_LOG_FILE
    and ESPHOME_LIGHTS_LOG_LEVEL can be sourced from the env file.

    File logging is enabled by default.  Set ESPHOME_LIGHTS_LOG_FILE to
    'none', 'off', 'false', or '0' to disable.  Set it to a custom path
    to override the default location:
        ~/.local/share/esphome-lights/esphome-lightsd.log

    Rotation: 1 MB per file, 3 backups (~4 MB total on disk).
    """
    # Re-apply log level now that the env file has been loaded.
    log_level_str = os.environ.get("ESPHOME_LIGHTS_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.getLogger().setLevel(log_level)

    log_file_env = os.environ.get("ESPHOME_LIGHTS_LOG_FILE", "").strip()
    if log_file_env.lower() in _LOG_FILE_DISABLED_VALUES:
        if log_file_env:  # Only log if explicitly set, not just absent
            log.debug("File logging disabled via ESPHOME_LIGHTS_LOG_FILE=%s", log_file_env)
        return

    log_file = log_file_env or os.path.join(
        os.path.expanduser("~"), ".local", "share", "esphome-lights", "esphome-lightsd.log"
    )
    log_dir = os.path.dirname(log_file)
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_048_576, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        file_handler.setLevel(log_level)
        logging.getLogger().addHandler(file_handler)
        log.debug("File logging active: %s", log_file)
    except OSError as exc:
        log.warning("Could not set up file logging to %s: %s", log_file, exc)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_xdg = os.environ.get("XDG_RUNTIME_DIR", "")
SOCKET_PATH = os.environ.get(
    "ESPHOME_LIGHTS_SOCKET",
    os.path.join(_xdg, "esphome-lights.sock") if _xdg else "/tmp/esphome-lights.sock",
)

# Reconnection backoff parameters (seconds)
RECONNECT_MIN = 1
RECONNECT_MAX = 30
RECONNECT_FACTOR = 2


def _parse_env_file(path: str):
    """Parse a key=value env file and apply variables to os.environ.

    Uses direct assignment so later calls override earlier ones,
    enabling priority-ordered loading. Surrounding quotes are stripped.
    Silently returns if the file does not exist.
    """
    try:
        fh = open(path)
    except FileNotFoundError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                value = value.strip()
                # Strip optional surrounding quotes (single or double)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ[key.strip()] = value


def load_env():
    """Load device config from env files in priority order.

    Priority (highest wins, loaded last):
      1. ~/.openclaw/workspace/.env  - shared OpenClaw workspace config
      2. ~/.config/esphome-lights/env - per-service config (installer default)
      3. {script_dir}/../.env         - legacy fallback

    Files are loaded with direct os.environ assignment so higher-priority
    files override lower-priority ones. Safe to call again on reload.
    """
    candidates = [
        # Lowest priority first
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
        os.path.join(os.path.expanduser("~"), ".config", "esphome-lights", "env"),
        os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env"),
    ]
    for path in candidates:
        if os.path.exists(path):
            _parse_env_file(path)
            log.debug("Loaded env from %s", path)


def load_devices():
    """Discover ESPHome devices from ESPHOME_LIGHTS_* environment variables."""
    devices = {}
    for key, value in os.environ.items():
        if key.startswith("ESPHOME_LIGHTS_") and key not in (
            "ESPHOME_LIGHTS_SOCKET",
            "ESPHOME_LIGHTS_LOG_LEVEL",
            "ESPHOME_LIGHTS_LOG_FILE",
            "ESPHOME_LIGHTS_WEB_PORT",
            "ESPHOME_LIGHTS_WEB_BIND",
        ):
            location = key[15:].lower()
            try:
                host_port, api_key = value.split("|")
                host, port = host_port.rsplit(":", 1)
                devices[location] = {
                    "host": host,
                    "port": int(port),
                    "encryption_key": api_key,
                }
            except (ValueError, IndexError):
                log.warning(
                    "Invalid format for %s - expected 'host:port|encryption_key'", key
                )
    return devices


# ---------------------------------------------------------------------------
# Light capability detection
# ---------------------------------------------------------------------------


def _detect_light_caps(entity) -> dict:
    """Detect light capabilities from a LightInfo entity.

    Supports both modern aioesphomeapi (``supported_color_modes`` bitmask set,
    where each element is a union of ``LightColorCapability`` bits) and legacy
    individual boolean fields (``supports_brightness``, etc.).

    Returns a dict with keys: has_brightness, has_rgb, has_color_temp,
    has_cwww, min_ct (Kelvin), max_ct (Kelvin).
    """
    caps: dict = {
        "has_brightness": False,
        "has_rgb": False,
        "has_color_temp": False,
        "has_cwww": False,
        "min_ct": 2700,
        "max_ct": 6500,
    }

    # Modern API: supported_color_modes is a set of LightColorCapability bitmasks.
    # Union all mode bits — any capability bit present in any mode counts.
    # LightColorCapability bit values: ON_OFF=1, BRIGHTNESS=2, WHITE=4,
    #   COLOR_TEMPERATURE=8, COLD_WARM_WHITE=16, RGB=32.
    modes = (
        getattr(entity, "supported_color_modes", None)
        or getattr(entity, "color_modes", None)
    )
    if modes:
        combined = 0
        for m in modes:
            combined |= int(m)
        caps["has_brightness"] = bool(combined & 2)
        caps["has_color_temp"] = bool(combined & 8)
        caps["has_cwww"]       = bool(combined & 16)
        caps["has_rgb"]        = bool(combined & 32)
    else:
        # Legacy individual boolean fields (aioesphomeapi < v12)
        caps["has_brightness"] = bool(getattr(entity, "supports_brightness", False))
        caps["has_color_temp"] = bool(
            getattr(entity, "supports_color_temperature", False)
        )
        caps["has_rgb"] = bool(
            getattr(entity, "supports_rgb_color", False)
            or getattr(entity, "supports_rgb", False)
        )
        caps["has_cwww"] = bool(getattr(entity, "supports_white_value", False))

    # Colour temperature range: convert mireds to Kelvin (inverted relationship).
    # min_mireds → coolest (highest Kelvin); max_mireds → warmest (lowest Kelvin).
    min_m = getattr(entity, "min_mireds", 0) or 0
    max_m = getattr(entity, "max_mireds", 0) or 0
    if min_m > 0 and max_m > 0 and min_m < max_m:
        caps["min_ct"] = round(1_000_000 / max_m)
        caps["max_ct"] = round(1_000_000 / min_m)

    return caps


# ---------------------------------------------------------------------------
# Device manager — persistent connections and state cache
# ---------------------------------------------------------------------------


class DeviceManager:
    """Manages persistent ESPHome API connections and cached entity state."""

    def __init__(self, devices: dict):
        self._devices = devices          # Raw config keyed by location name
        self._clients: dict[str, APIClient] = {}
        self._conn_state: dict[str, str] = {}   # connected / connecting / disconnected
        self._state_cache: dict[str, dict] = {}  # Cached entity state per device
        self._entity_info: dict[str, dict] = {}  # Control key/type per device
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        self._sse_subscribers: list[asyncio.Queue] = []  # Web interface SSE queues

    # -- lifecycle -----------------------------------------------------------

    async def connect_all(self):
        """Connect to every configured device concurrently."""
        await asyncio.gather(
            *(self._connect(name) for name in self._devices),
            return_exceptions=True,
        )

    async def disconnect_all(self):
        """Gracefully disconnect all devices and cancel reconnection tasks."""
        for task in self._reconnect_tasks.values():
            task.cancel()
        self._reconnect_tasks.clear()

        for name, client in list(self._clients.items()):
            try:
                await client.disconnect()
                log.info("Disconnected from %s", name)
            except Exception:
                pass
            self._conn_state[name] = "disconnected"
        self._clients.clear()

    # -- connection handling -------------------------------------------------

    async def _connect(self, name: str):
        """Establish a connection to a single device."""
        cfg = self._devices[name]
        self._conn_state[name] = "connecting"
        log.info("Connecting to %s (%s:%s)...", name, cfg["host"], cfg["port"])

        # on_stop is the modern aioesphomeapi callback (replaces set_on_disconnect)
        async def _on_stop(expected_disconnect: bool):
            await self._on_disconnect(name, expected_disconnect)

        try:
            client = APIClient(
                cfg["host"],
                cfg["port"],
                noise_psk=cfg["encryption_key"],
                password="",
            )
            await asyncio.wait_for(
                client.connect(on_stop=_on_stop, login=True), timeout=10
            )
            self._clients[name] = client
            self._conn_state[name] = "connected"
            log.info("Connected to %s", name)

            # Discover entities
            entities, _ = await client.list_entities_services()
            self._resolve_entity(name, entities)

            # Subscribe to state changes and populate cache
            def on_state(state):
                self._handle_state(name, state)

            client.subscribe_states(on_state)

        except Exception as exc:
            self._conn_state[name] = "disconnected"
            log.error("Failed to connect to %s: %s", name, exc)
            self._schedule_reconnect(name)

    def _resolve_entity(self, name: str, entities):
        """Pick the best controllable entity (LightInfo > SwitchInfo).

        Prefer LightInfo for brightness/RGB support, fall back to
        SwitchInfo for simple on/off devices (smart plugs, relays, etc.).
        The special ``status_led`` entity is always skipped.
        """
        control_key = None
        control_type = None
        found_entity = None

        # Prefer LightInfo — supports brightness and RGB
        for entity in entities:
            cls = entity.__class__.__name__
            if cls == "LightInfo" and getattr(entity, "object_id", "") != "status_led":
                control_key = entity.key
                control_type = "light"
                found_entity = entity
                break

        if control_key is None:
            # Fall back to SwitchInfo (smart plugs, relays, etc.)
            for entity in entities:
                if entity.__class__.__name__ == "SwitchInfo":
                    control_key = entity.key
                    control_type = "switch"
                    break

        info: dict = {"key": control_key, "type": control_type}
        if control_type == "light" and found_entity is not None:
            info.update(_detect_light_caps(found_entity))
        self._entity_info[name] = info

    def _handle_state(self, name: str, state):
        """Cache incoming entity state updates."""
        entity = self._entity_info.get(name, {})
        if entity.get("key") is None:
            return

        cls = state.__class__.__name__
        if cls == "LightState" and state.key == entity["key"]:
            _ct = getattr(state, "color_temperature", None)
            _cw = getattr(state, "cold_white", None)
            _ww = getattr(state, "warm_white", None)
            self._state_cache[name] = {
                "state": "ON" if state.state else "OFF",
                "brightness": round(state.brightness * 255) if state.brightness is not None else None,
                "rgb": (
                    f"{round(state.red * 255)},{round(state.green * 255)},{round(state.blue * 255)}"
                    if state.red is not None
                    else None
                ),
                # colour temperature: converted from mireds to Kelvin for display
                "color_temp": round(1_000_000 / _ct) if _ct else None,
                # cold/warm white channels: stored as 0-255 integers
                "cold_white": round(_cw * 255) if _cw is not None else None,
                "warm_white": round(_ww * 255) if _ww is not None else None,
                "entity_type": "light",
            }
        elif cls == "SwitchState" and state.key == entity["key"]:
            self._state_cache[name] = {
                "state": "ON" if state.state else "OFF",
                "brightness": None,
                "rgb": None,
                "entity_type": "switch",
            }
        else:
            return
        log.debug("State update for %s: %s", name, self._state_cache.get(name))

        # Notify web interface SSE subscribers with a full status snapshot
        if self._sse_subscribers:
            snapshot = self.handle_status()
            for q in list(self._sse_subscribers):
                q.put_nowait(snapshot)

    # -- reconnection --------------------------------------------------------

    async def _on_disconnect(self, name: str, expected_disconnect: bool = False):
        """Called by aioesphomeapi when a device connection stops."""
        if expected_disconnect:
            log.info("Disconnected from %s (expected)", name)
        else:
            log.warning("Lost connection to %s", name)
        self._conn_state[name] = "disconnected"
        self._clients.pop(name, None)
        self._schedule_reconnect(name)

    def _schedule_reconnect(self, name: str):
        """Schedule a reconnection attempt with exponential backoff."""
        if name in self._reconnect_tasks and not self._reconnect_tasks[name].done():
            return  # Already scheduled
        self._reconnect_tasks[name] = asyncio.get_running_loop().create_task(
            self._reconnect_loop(name)
        )

    async def _reconnect_loop(self, name: str):
        """Retry connecting with exponential backoff."""
        delay = RECONNECT_MIN
        while True:
            log.info("Reconnecting to %s in %ss...", name, delay)
            await asyncio.sleep(delay)
            try:
                await self._connect(name)
                if self._conn_state.get(name) == "connected":
                    log.info("Reconnected to %s", name)
                    return
            except Exception as exc:
                log.error("Reconnect to %s failed: %s", name, exc)
            delay = min(delay * RECONNECT_FACTOR, RECONNECT_MAX)

    # -- command handling ----------------------------------------------------

    def handle_list(self) -> dict:
        """Return configured devices with connection state, entity type, and capability flags."""
        result = {}
        for name, cfg in sorted(self._devices.items()):
            entity = self._entity_info.get(name, {})
            result[name] = {
                "host": cfg["host"],
                "port": cfg["port"],
                "connection": self._conn_state.get(name, "unknown"),
                "entity_type": entity.get("type"),
                "has_brightness": entity.get("has_brightness", False),
                "has_rgb": entity.get("has_rgb", False),
                "has_color_temp": entity.get("has_color_temp", False),
                "has_cwww": entity.get("has_cwww", False),
                "min_ct": entity.get("min_ct", 2700),
                "max_ct": entity.get("max_ct", 6500),
            }
        return {"ok": True, "result": result}

    def handle_status(self) -> dict:
        """Return cached state for all devices, including capability flags."""
        result = {}
        for name in sorted(self._devices):
            cached = self._state_cache.get(name)
            conn = self._conn_state.get(name, "unknown")
            entity = self._entity_info.get(name, {})
            caps = {
                "has_brightness": entity.get("has_brightness", False),
                "has_rgb": entity.get("has_rgb", False),
                "has_color_temp": entity.get("has_color_temp", False),
                "has_cwww": entity.get("has_cwww", False),
                "min_ct": entity.get("min_ct", 2700),
                "max_ct": entity.get("max_ct", 6500),
            }
            if cached:
                result[name] = {**cached, "connection": conn, **caps}
            else:
                result[name] = {"state": "unknown", "connection": conn, **caps}
        return {"ok": True, "result": result}

    def handle_set(self, device: str, action: str, value: str | None = None) -> dict:
        """Execute a set command on one device, or all devices when device='all'."""
        if device == "all":
            results = {}
            any_ok = False
            for name in sorted(self._devices.keys()):
                r = self.handle_set(name, action, value)
                if r.get("ok"):
                    any_ok = True
                    results[name] = r["result"]
                else:
                    results[name] = f"skipped ({r.get('error', 'error')})"
            summary = ", ".join(f"{k}: {v}" for k, v in results.items())
            return {"ok": any_ok, "result": summary}

        if device not in self._devices:
            available = ", ".join(sorted(self._devices.keys()))
            return {"ok": False, "error": f"Device '{device}' not found. Available: {available}"}

        if self._conn_state.get(device) != "connected":
            return {"ok": False, "error": f"Device '{device}' is not connected"}

        client = self._clients.get(device)
        if client is None:
            return {"ok": False, "error": f"Device '{device}' has no active client"}

        entity = self._entity_info.get(device, {})
        control_key = entity.get("key")
        control_type = entity.get("type")

        if control_key is None:
            return {"ok": False, "error": f"No controllable entity found on '{device}'"}

        if action == "on":
            if control_type == "switch":
                client.switch_command(control_key, state=True)
            else:
                client.light_command(control_key, state=True)
            return {"ok": True, "result": "Turned ON"}

        elif action == "off":
            if control_type == "switch":
                client.switch_command(control_key, state=False)
            else:
                client.light_command(control_key, state=False)
            return {"ok": True, "result": "Turned OFF"}

        elif action == "brightness":
            if control_type == "switch":
                return {"ok": False, "error": "Brightness not supported for switch entities"}
            if value is None:
                return {"ok": False, "error": "Brightness requires a value (0-255)"}
            try:
                brightness = int(value) / 255.0
            except ValueError:
                return {"ok": False, "error": f"Brightness must be 0-255, got {value}"}
            client.light_command(control_key, brightness=brightness)
            return {"ok": True, "result": f"Brightness set to {value}"}

        elif action == "rgb":
            if control_type == "switch":
                return {"ok": False, "error": "RGB not supported for switch entities"}
            if value is None:
                return {"ok": False, "error": "RGB requires a value (r,g,b)"}
            try:
                r, g, b = map(int, value.split(","))
                if not all(0 <= c <= 255 for c in [r, g, b]):
                    raise ValueError()
            except ValueError:
                return {"ok": False, "error": f"RGB must be r,g,b (0-255 each), got {value}"}
            client.light_command(
                control_key, rgb=(r / 255.0, g / 255.0, b / 255.0)
            )
            return {"ok": True, "result": f"RGB set to ({r},{g},{b})"}

        elif action == "color_temp":
            if control_type == "switch":
                return {"ok": False, "error": "Colour temperature not supported for switch entities"}
            if value is None:
                return {"ok": False, "error": "Colour temperature requires a value in Kelvin (e.g. 2700)"}
            try:
                kelvin = int(value)
                if kelvin <= 0:
                    raise ValueError()
            except ValueError:
                return {"ok": False, "error": f"Colour temperature must be a positive integer (Kelvin), got {value}"}
            # ESPHome native API uses mireds; convert from Kelvin
            client.light_command(control_key, color_temperature=1_000_000.0 / kelvin)
            return {"ok": True, "result": f"Colour temperature set to {kelvin}K"}

        elif action == "cwww":
            if control_type == "switch":
                return {"ok": False, "error": "CW/WW not supported for switch entities"}
            if value is None:
                return {"ok": False, "error": "CW/WW requires a value (cold,warm - each 0-255)"}
            try:
                cw, ww = map(int, value.split(","))
                if not all(0 <= c <= 255 for c in [cw, ww]):
                    raise ValueError()
            except ValueError:
                return {"ok": False, "error": f"CW/WW must be cold,warm (0-255 each), got {value}"}
            client.light_command(
                control_key,
                cold_white=cw / 255.0,
                warm_white=ww / 255.0,
            )
            return {"ok": True, "result": f"CW/WW set to ({cw},{ww})"}

        else:
            return {"ok": False, "error": f"Unknown action '{action}'"}

    @staticmethod
    def handle_ping() -> dict:
        return {"ok": True, "result": "pong"}

    async def handle_reload(self, new_devices: dict) -> dict:
        """Reload device configuration and reconnect as needed.

        Compares new_devices against the current config:
          - New devices are connected.
          - Removed devices are disconnected.
          - Changed devices are disconnected then reconnected.
          - Unchanged devices are left alone.
        """
        old_keys = set(self._devices.keys())
        new_keys = set(new_devices.keys())

        removed = old_keys - new_keys
        added = new_keys - old_keys
        changed = {
            k for k in old_keys & new_keys
            if new_devices[k] != self._devices[k]
        }

        # Update stored config before reconnecting
        self._devices = new_devices

        # Disconnect removed and changed devices cleanly
        for name in removed | changed:
            task = self._reconnect_tasks.pop(name, None)
            if task and not task.done():
                task.cancel()
            client = self._clients.pop(name, None)
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self._conn_state.pop(name, None)
            self._state_cache.pop(name, None)
            self._entity_info.pop(name, None)

        # Connect new and changed devices
        if added | changed:
            await asyncio.gather(
                *(self._connect(name) for name in added | changed),
                return_exceptions=True,
            )

        summary = (
            f"Reloaded: {len(added)} added, {len(removed)} removed, "
            f"{len(changed)} changed, {len(old_keys & new_keys) - len(changed)} unchanged"
        )
        log.info(summary)
        return {"ok": True, "result": summary}

    async def handle_reconnect(self, device: str) -> dict:
        """Cancel any pending backoff and immediately reconnect one or all devices.

        Useful when a device has just rebooted and you don't want to wait for
        the exponential backoff timer to expire before the next retry attempt.
        Supports device='all' to reconnect every configured device at once.
        """
        if device == "all":
            results = {}
            for name in sorted(self._devices.keys()):
                r = await self._reconnect_device(name)
                results[name] = r["result"] if r["ok"] else r["error"]
            summary = ", ".join(f"{k}: {v}" for k, v in results.items())
            return {"ok": True, "result": summary}

        if device not in self._devices:
            available = ", ".join(sorted(self._devices.keys()))
            return {"ok": False, "error": f"Device '{device}' not found. Available: {available}"}

        return await self._reconnect_device(device)

    async def _reconnect_device(self, name: str) -> dict:
        """Cancel pending backoff and immediately reconnect a single device."""
        # Cancel any in-flight backoff task so it doesn't interfere
        task = self._reconnect_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

        # Disconnect the existing client cleanly if one is present
        client = self._clients.pop(name, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

        self._conn_state[name] = "disconnected"
        # Clear stale state so status shows 'unknown' while reconnecting
        self._state_cache.pop(name, None)
        log.info("Manual reconnect requested for %s", name)

        await self._connect(name)

        if self._conn_state.get(name) == "connected":
            return {"ok": True, "result": f"Reconnected to {name}"}
        return {"ok": False, "error": f"Failed to reconnect to {name}"}


# ---------------------------------------------------------------------------
# Command audit helper
# ---------------------------------------------------------------------------


def _audit_cmd(cmd: str, request: dict, response: dict):
    """Log a single-line audit entry for every dispatched command.

    Format:  cmd=<cmd> [device=<d>] [action=<a>] [value=<v>] -> ok|error: <msg>
    Long results (e.g. list/status payloads) are truncated to 120 characters.
    """
    parts = [f"cmd={cmd}"]
    if "device" in request:
        parts.append(f"device={request['device']}")
    if "action" in request:
        parts.append(f"action={request['action']}")
    if "value" in request:
        parts.append(f"value={request['value']}")
    prefix = " ".join(parts)
    if response.get("ok"):
        result_str = str(response.get("result", ""))
        if len(result_str) > 120:
            result_str = result_str[:117] + "..."
        log.info("%s -> ok: %s", prefix, result_str)
    else:
        log.info("%s -> error: %s", prefix, response.get("error", ""))


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------


class SocketServer:
    """Unix domain socket server that dispatches JSON commands to DeviceManager."""

    def __init__(self, manager: DeviceManager, path: str = SOCKET_PATH):
        self._manager = manager
        self._path = path
        self._server: asyncio.AbstractServer | None = None

    async def start(self):
        """Bind and start listening on the Unix socket."""
        # Remove stale socket file
        if os.path.exists(self._path):
            try:
                # Try connecting to check if another daemon is running
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(self._path), timeout=1
                )
                writer.close()
                await writer.wait_closed()
                log.error("Another daemon is already listening on %s", self._path)
                sys.exit(1)
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                # Stale socket — safe to remove
                os.unlink(self._path)
                log.info("Removed stale socket file %s", self._path)

        self._server = await asyncio.start_unix_server(
            self._handle_client, path=self._path
        )
        os.chmod(self._path, 0o660)
        log.info("Listening on %s", self._path)

    async def stop(self):
        """Stop the server and clean up the socket file."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if os.path.exists(self._path):
            os.unlink(self._path)
            log.info("Removed socket file %s", self._path)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a single client connection (may send multiple commands)."""
        peer = "client"
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break  # Client disconnected

                try:
                    request = json.loads(line.decode("utf-8").strip())
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    response = {"ok": False, "error": f"Invalid JSON: {exc}"}
                    writer.write((json.dumps(response) + "\n").encode("utf-8"))
                    await writer.drain()
                    continue

                response = await self._dispatch(request)
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                await writer.drain()

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass
        except Exception as exc:
            log.error("Error handling %s: %s", peer, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, request: dict) -> dict:
        """Route a parsed JSON request to the appropriate handler."""
        cmd = request.get("cmd")
        if cmd is None:
            response = {"ok": False, "error": "Missing 'cmd' field"}
            _audit_cmd("<missing>", request, response)
            return response

        if cmd == "list":
            response = self._manager.handle_list()
        elif cmd == "status":
            response = self._manager.handle_status()
        elif cmd == "ping":
            response = self._manager.handle_ping()
        elif cmd == "reload":
            load_env()
            new_devices = load_devices()
            if not new_devices:
                response = {"ok": False, "error": "No devices found in config after reload"}
            else:
                response = await self._manager.handle_reload(new_devices)
        elif cmd == "reconnect":
            device = request.get("device", "all")
            response = await self._manager.handle_reconnect(device)
        elif cmd == "set":
            device = request.get("device")
            action = request.get("action")
            value = request.get("value")
            if not device:
                response = {"ok": False, "error": "Missing 'device' field"}
            elif not action:
                response = {"ok": False, "error": "Missing 'action' field"}
            else:
                response = self._manager.handle_set(device, action, value)
        else:
            response = {"ok": False, "error": f"Unknown command '{cmd}'"}

        _audit_cmd(cmd, request, response)
        return response


# ---------------------------------------------------------------------------
# Web interface — inline single-page HTML/CSS/JS
# ---------------------------------------------------------------------------

# Raw HTML template.  __VERSION__ is replaced with _DAEMON_VERSION at import
# time so all requests see the correct version without runtime overhead.
_WEB_UI_HTML_RAW = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ESPHome Lights</title>
<style>
/* === Solarized colour palette === */
:root{
  --s-base03:#002b36;--s-base02:#073642;--s-base01:#586e75;--s-base00:#657b83;
  --s-base0:#839496;--s-base1:#93a1a1;--s-base2:#eee8d5;--s-base3:#fdf6e3;
  --s-yellow:#b58900;--s-orange:#cb4b16;--s-red:#dc322f;--s-magenta:#d33682;
  --s-violet:#6c71c4;--s-blue:#268bd2;--s-cyan:#2aa198;--s-green:#859900;
}
/* Light theme (system default) */
@media(prefers-color-scheme:light){:root{
  --bg:var(--s-base3);--bg-alt:var(--s-base2);
  --text:var(--s-base00);--emph:var(--s-base01);
  --subtle:var(--s-base1);--border:var(--s-base2);
}}
/* Dark theme */
@media(prefers-color-scheme:dark){:root{
  --bg:var(--s-base03);--bg-alt:var(--s-base02);
  --text:var(--s-base0);--emph:var(--s-base1);
  --subtle:var(--s-base01);--border:var(--s-base02);
}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;font-size:16px;line-height:1.5}
/* Header */
header{background:var(--bg-alt);border-bottom:2px solid var(--s-blue);padding:12px 16px;display:flex;align-items:center;gap:12px}
header h1{font-size:1.2rem;color:var(--emph);flex:1}
#sseStatus{font-size:.8rem;color:var(--subtle)}
#sseStatus.ok::before{content:'\\25CF  ';color:var(--s-green)}
#sseStatus.err::before{content:'\\25CF  ';color:var(--s-red)}
/* Device grid — responsive, minimum 300 px per card */
main{padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;align-items:start}
/* Device cards */
.card{background:var(--bg-alt);border:1px solid var(--border);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:10px;border-left:4px solid var(--subtle)}
.card.state-on{border-left-color:var(--s-green)}
.card.state-off{border-left-color:var(--subtle)}
/* Card header row */
.card-hdr{display:flex;align-items:center;gap:8px}
.card-name{font-weight:600;color:var(--emph);flex:1;text-transform:capitalize;font-size:1rem}
.badge{font-size:.7rem;padding:2px 8px;border-radius:10px;font-weight:700}
.badge.connected{background:var(--s-green);color:var(--s-base3)}
.badge.disconnected{background:var(--s-red);color:var(--s-base3)}
.badge.connecting{background:var(--s-yellow);color:var(--s-base03)}
.badge.unknown{background:var(--subtle);color:var(--s-base3)}
/* Toggle button — 44 px minimum for touch targets */
.toggle{width:100%;min-height:44px;border:none;border-radius:6px;font-size:1rem;font-weight:600;cursor:pointer;transition:opacity .15s}
.toggle.on{background:var(--s-green);color:var(--s-base3)}
.toggle.off{background:var(--subtle);color:var(--s-base3)}
.toggle:hover{opacity:.85}
.toggle:disabled{opacity:.4;cursor:not-allowed}
/* Slider controls */
.ctrl{display:flex;flex-direction:column;gap:4px}
.ctrl label{font-size:.82rem;color:var(--subtle)}
input[type=range]{width:100%;height:28px;cursor:pointer;accent-color:var(--s-blue)}
input[type=range]:disabled{opacity:.4;cursor:not-allowed}
/* Colour picker */
input[type=color]{width:44px;height:44px;border:none;background:none;cursor:pointer;padding:0;border-radius:4px}
input[type=color]:disabled{opacity:.4;cursor:not-allowed}
/* Reconnect button */
.reconnect{min-height:40px;width:100%;background:transparent;border:1px solid var(--s-blue);color:var(--s-blue);border-radius:6px;font-size:.85rem;cursor:pointer;transition:background .15s,color .15s}
.reconnect:hover{background:var(--s-blue);color:var(--s-base3)}
/* Footer */
footer{text-align:center;padding:10px;font-size:.8rem;color:var(--subtle)}
</style>
</head>
<body>
<header>
  <h1>&#9889; ESPHome Lights</h1>
  <span id="sseStatus" class="err">Connecting&#8230;</span>
</header>
<main id="grid">
  <p style="grid-column:1/-1;text-align:center;padding:2rem;color:var(--subtle)">Loading&#8230;</p>
</main>
<footer id="footer">ESPHome Lights v__VERSION__</footer>
<script>
"use strict";
const grid=document.getElementById("grid");
const sseStatus=document.getElementById("sseStatus");
const footer=document.getElementById("footer");
let devList={},devStatus={},debounceTimers={};

function db(key,fn,ms){
  clearTimeout(debounceTimers[key]);
  debounceTimers[key]=setTimeout(fn,ms||120);
}

async function req(method,path,body){
  const o={method,headers:{}};
  if(body){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body);}
  try{const r=await fetch(path,o);return r.json();}
  catch(e){return{ok:false,error:String(e)};}
}

function el(tag,cls,txt){
  const e=document.createElement(tag);
  if(cls)e.className=cls;
  if(txt!=null)e.textContent=txt;
  return e;
}

async function sendAction(dev,action,value){
  const b={device:dev,action:action};
  if(value!=null)b.value=String(value);
  await req("POST","/api/set",b);
}

function buildCard(name,st,info){
  const state=st.state||"unknown";
  const conn=st.connection||(info&&info.connection)||"unknown";
  const entityType=st.entity_type||(info&&info.entity_type);
  const isOn=state==="ON";
  const isLight=entityType==="light";
  const disabled=conn!=="connected";
  /* Capability flags — prefer status fields (populated after entity discovery);
     fall back to list fields (populated from LightInfo on first connection). */
  const hasBrightness = !!(st.has_brightness || (info && info.has_brightness));
  const hasRgb        = !!(st.has_rgb        || (info && info.has_rgb));
  const hasColorTemp  = !!(st.has_color_temp  || (info && info.has_color_temp));
  const hasCwww       = !!(st.has_cwww        || (info && info.has_cwww));
  const minCt = st.min_ct || (info && info.min_ct) || 2700;
  const maxCt = st.max_ct || (info && info.max_ct) || 6500;

  const card=el("div","card state-"+state.toLowerCase());
  card.id="card-"+name;

  /* Header row */
  const hdr=el("div","card-hdr");
  hdr.appendChild(el("span","card-name",name.replace(/_/g," ")));
  hdr.appendChild(el("span","badge "+conn,conn));
  card.appendChild(hdr);

  /* Toggle button */
  const tog=el("button","toggle "+(isOn?"on":"off"),isOn?"Turn Off":"Turn On");
  tog.disabled=disabled;
  tog.onclick=function(){sendAction(name,isOn?"off":"on");};
  card.appendChild(tog);

  if(isLight){
    /* Brightness slider — shown whenever the device supports brightness,
       regardless of current on/off state. Defaults to 0 when state is null. */
    if(hasBrightness){
      var briVal=st.brightness!=null?st.brightness:0;
      const c=el("div","ctrl");
      const lbl=el("label","","Brightness: "+briVal);
      const s=document.createElement("input");
      s.type="range";s.min=0;s.max=255;s.value=briVal;s.disabled=disabled;
      s.oninput=function(){
        lbl.textContent="Brightness: "+s.value;
        db(name+"-br",function(){sendAction(name,"brightness",s.value);});
      };
      c.appendChild(lbl);c.appendChild(s);card.appendChild(c);
    }

    /* RGB colour picker — shown when device supports RGB.
       Defaults to white when no colour is cached. */
    if(hasRgb){
      var hex="#ffffff";
      if(st.rgb!=null){
        var parts=st.rgb.split(",").map(Number);
        hex="#"+parts.map(function(v){return v.toString(16).padStart(2,"0");}).join("");
      }
      const c=el("div","ctrl");
      c.appendChild(el("label","","Colour"));
      const p=document.createElement("input");
      p.type="color";p.value=hex;p.disabled=disabled;
      p.oninput=function(){
        db(name+"-rgb",function(){
          var h=p.value;
          sendAction(name,"rgb",
            parseInt(h.slice(1,3),16)+","+parseInt(h.slice(3,5),16)+","+parseInt(h.slice(5,7),16));
        });
      };
      c.appendChild(p);card.appendChild(c);
    }

    /* Colour temperature slider — shown when device supports colour temp.
       Uses device-reported min/max range; defaults to midpoint when null. */
    if(hasColorTemp){
      var ctVal=st.color_temp!=null?st.color_temp:Math.round((minCt+maxCt)/2);
      const c=el("div","ctrl");
      const lbl=el("label","","Colour Temp: "+ctVal+"K");
      const s=document.createElement("input");
      s.type="range";s.min=minCt;s.max=maxCt;s.value=ctVal;s.disabled=disabled;
      s.oninput=function(){
        lbl.textContent="Colour Temp: "+s.value+"K";
        db(name+"-ct",function(){sendAction(name,"color_temp",s.value);});
      };
      c.appendChild(lbl);c.appendChild(s);card.appendChild(c);
    }

    /* Cold/warm white sliders — shown when device supports CW/WW channels.
       Both default to 0 when no state is cached. */
    if(hasCwww){
      var cw=st.cold_white!=null?st.cold_white:0;
      var ww=st.warm_white!=null?st.warm_white:0;
      const cc=el("div","ctrl"),wc=el("div","ctrl");
      const cwL=el("label","","Cold White: "+cw);
      const wwL=el("label","","Warm White: "+ww);
      const cwS=document.createElement("input");
      const wwS=document.createElement("input");
      [cwS,wwS].forEach(function(s){s.type="range";s.min=0;s.max=255;s.disabled=disabled;});
      cwS.value=cw;wwS.value=ww;
      function sendCWWW(){sendAction(name,"cwww",cwS.value+","+wwS.value);}
      cwS.oninput=function(){cwL.textContent="Cold White: "+cwS.value;db(name+"-cwww",sendCWWW);};
      wwS.oninput=function(){wwL.textContent="Warm White: "+wwS.value;db(name+"-cwww",sendCWWW);};
      cc.appendChild(cwL);cc.appendChild(cwS);
      wc.appendChild(wwL);wc.appendChild(wwS);
      card.appendChild(cc);card.appendChild(wc);
    }
  }

  /* Reconnect button */
  const rb=el("button","reconnect","Reconnect");
  rb.onclick=function(){req("POST","/api/reconnect",{device:name});};
  card.appendChild(rb);

  return card;
}

function render(){
  grid.innerHTML="";
  const names=Object.keys(devStatus).sort();
  if(!names.length){
    grid.innerHTML="<p style=\\"grid-column:1/-1;text-align:center;padding:2rem;color:var(--subtle)\\">No devices found.</p>";
    return;
  }
  names.forEach(function(n){grid.appendChild(buildCard(n,devStatus[n],devList[n]));});
}

function connectSSE(){
  var es=new EventSource("/api/events");
  es.onopen=function(){sseStatus.className="ok";sseStatus.textContent="Live";};
  es.onmessage=function(e){
    try{
      var d=JSON.parse(e.data);
      if(d.ok&&d.result){devStatus=d.result;render();}
    }catch(err){}
  };
  es.onerror=function(){sseStatus.className="err";sseStatus.textContent="Reconnecting\u2026";};
}

async function init(){
  var results=await Promise.all([req("GET","/api/list"),req("GET","/api/status")]);
  if(results[0].ok)devList=results[0].result;
  if(results[1].ok){devStatus=results[1].result;render();}
  connectSSE();
}

init();
</script>
</body>
</html>
"""

# Pre-encode to bytes with version substituted — done once at import time.
_WEB_UI_BYTES: bytes = _WEB_UI_HTML_RAW.replace(
    "__VERSION__", _DAEMON_VERSION
).encode("utf-8")


# ---------------------------------------------------------------------------
# Web server — embedded HTTP server for browser-based device control
# ---------------------------------------------------------------------------


class WebServer:
    """Embedded async HTTP server providing a browser UI and REST API.

    Disabled by default.  Enable by setting ESPHOME_LIGHTS_WEB_PORT to a
    non-zero integer (suggested: 7890).  Binds to 127.0.0.1 by default;
    set ESPHOME_LIGHTS_WEB_BIND=0.0.0.0 to expose on the LAN.

    REST endpoints:
      GET  /              — Single-page web UI (HTML)
      GET  /api/list      — Configured devices and connection state (JSON)
      GET  /api/status    — Cached device state (JSON)
      GET  /api/ping      — Health check (JSON)
      GET  /api/events    — Server-Sent Events stream for real-time updates
      POST /api/set       — Control a device {device, action, value?}
      POST /api/reload    — Reload configuration without restarting
      POST /api/reconnect — Force immediate reconnect {device?}
    """

    # Hard cap on request body size — protects against oversized POST bodies.
    _MAX_BODY = 65_536  # 64 KB

    _STATUS_PHRASES: dict[int, str] = {
        200: "OK",
        204: "No Content",
        400: "Bad Request",
        404: "Not Found",
        405: "Method Not Allowed",
        413: "Payload Too Large",
    }

    def __init__(self, manager: "DeviceManager", host: str, port: int):
        self._manager = manager
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None

    async def start(self):
        """Bind and start listening on the configured TCP port."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        log.info(
            "Web interface listening on http://%s:%d/", self._host, self._port
        )

    async def stop(self):
        """Stop the HTTP server."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            log.info("Web interface stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle a single HTTP request/response cycle."""
        try:
            # Parse the request line: METHOD PATH HTTP/x.x
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line:
                return
            parts = request_line.decode("utf-8", errors="replace").strip().split()
            if len(parts) < 2:
                return
            method, path = parts[0].upper(), parts[1]

            # Read headers until the blank separator line
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                stripped = line.strip()
                if not stripped:
                    break
                if b":" in stripped:
                    k, _, v = stripped.decode("utf-8", errors="replace").partition(":")
                    headers[k.lower().strip()] = v.strip()

            # Read request body for POST requests
            body = b""
            if method == "POST":
                content_length = int(headers.get("content-length", "0"))
                if content_length > self._MAX_BODY:
                    await self._write_response(
                        writer, 413, "application/json",
                        json.dumps({"ok": False, "error": "Request body too large"}),
                    )
                    return
                if content_length > 0:
                    body = await asyncio.wait_for(
                        reader.read(content_length), timeout=10
                    )

            await self._route(writer, method, path, body)

        except (ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.TimeoutError:
            pass
        except UnicodeDecodeError:
            pass
        except Exception as exc:
            log.debug("Web client error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: str | bytes,
    ):
        """Write a complete HTTP/1.1 response."""
        if isinstance(body, str):
            body = body.encode("utf-8")
        phrase = self._STATUS_PHRASES.get(status, "OK")
        header_block = (
            f"HTTP/1.1 {status} {phrase}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(header_block.encode("utf-8") + body)
        await writer.drain()

    async def _route(
        self,
        writer: asyncio.StreamWriter,
        method: str,
        path: str,
        body: bytes,
    ):
        """Dispatch the request to the appropriate handler."""
        # Ignore query strings
        path = path.split("?")[0]

        if path == "/" and method == "GET":
            # Serve the single-page web UI
            header_block = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(_WEB_UI_BYTES)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            writer.write(header_block.encode("utf-8") + _WEB_UI_BYTES)
            await writer.drain()

        elif path == "/favicon.ico":
            await self._write_response(writer, 204, "text/plain", "")

        elif path == "/api/list" and method == "GET":
            result = self._manager.handle_list()
            await self._write_response(writer, 200, "application/json", json.dumps(result))

        elif path == "/api/status" and method == "GET":
            result = self._manager.handle_status()
            await self._write_response(writer, 200, "application/json", json.dumps(result))

        elif path == "/api/ping" and method == "GET":
            result = self._manager.handle_ping()
            await self._write_response(writer, 200, "application/json", json.dumps(result))

        elif path == "/api/events" and method == "GET":
            await self._handle_sse(writer)

        elif path == "/api/set" and method == "POST":
            try:
                data = json.loads(body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                await self._write_response(
                    writer, 400, "application/json",
                    json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"}),
                )
                return
            device = data.get("device")
            action = data.get("action")
            value = data.get("value")
            if not device:
                await self._write_response(
                    writer, 400, "application/json",
                    json.dumps({"ok": False, "error": "Missing 'device' field"}),
                )
                return
            if not action:
                await self._write_response(
                    writer, 400, "application/json",
                    json.dumps({"ok": False, "error": "Missing 'action' field"}),
                )
                return
            result = self._manager.handle_set(device, action, value)
            http_status = 200 if result.get("ok") else 400
            await self._write_response(writer, http_status, "application/json", json.dumps(result))

        elif path == "/api/reload" and method == "POST":
            load_env()
            new_devices = load_devices()
            if not new_devices:
                result = {"ok": False, "error": "No devices found in config after reload"}
            else:
                result = await self._manager.handle_reload(new_devices)
            await self._write_response(writer, 200, "application/json", json.dumps(result))

        elif path == "/api/reconnect" and method == "POST":
            try:
                data = json.loads(body.decode("utf-8")) if body.strip() else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                data = {}
            device = data.get("device", "all")
            result = await self._manager.handle_reconnect(device)
            await self._write_response(writer, 200, "application/json", json.dumps(result))

        elif method not in ("GET", "POST", "HEAD"):
            await self._write_response(
                writer, 405, "application/json",
                json.dumps({"ok": False, "error": f"Method not allowed: {method}"}),
            )

        else:
            await self._write_response(
                writer, 404, "application/json",
                json.dumps({"ok": False, "error": f"Not found: {path}"}),
            )

    async def _handle_sse(self, writer: asyncio.StreamWriter):
        """Stream Server-Sent Events to a connected browser client.

        Sends an initial full status snapshot immediately, then pushes
        incremental updates whenever any device state changes.  A keepalive
        comment is emitted every 20 s to prevent proxy/browser timeouts.
        """
        sse_headers = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        )
        writer.write(sse_headers.encode("utf-8"))
        await writer.drain()

        # Send initial full state so the browser has data immediately
        initial = self._manager.handle_status()
        writer.write(f"data: {json.dumps(initial)}\n\n".encode("utf-8"))
        await writer.drain()

        # Register as a subscriber and stream updates until disconnect
        queue: asyncio.Queue = asyncio.Queue()
        self._manager._sse_subscribers.append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    writer.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                except asyncio.TimeoutError:
                    # Keepalive comment — prevents proxies and browsers timing out
                    writer.write(b": keepalive\n\n")
                await writer.drain()
        finally:
            # Clean up subscription whether we exit cleanly or via disconnect
            try:
                self._manager._sse_subscribers.remove(queue)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def main():
    load_env()
    _configure_logging()
    devices = load_devices()

    if not devices:
        log.error("No devices configured (set ESPHOME_LIGHTS_* environment variables)")
        sys.exit(1)

    log.info(
        "Daemon starting v%s, %d device(s): %s",
        _DAEMON_VERSION,
        len(devices),
        ", ".join(sorted(devices)),
    )

    manager = DeviceManager(devices)
    server = SocketServer(manager)

    # Optional web interface (disabled when ESPHOME_LIGHTS_WEB_PORT is 0 or unset)
    _web_port_str = os.environ.get("ESPHOME_LIGHTS_WEB_PORT", "0")
    try:
        _web_port = int(_web_port_str)
    except ValueError:
        log.warning("Invalid ESPHOME_LIGHTS_WEB_PORT=%r — disabling web interface", _web_port_str)
        _web_port = 0
    _web_bind_raw = os.environ.get("ESPHOME_LIGHTS_WEB_BIND", "localhost").strip().lower()
    if _web_bind_raw in ("localhost", "local", "127.0.0.1"):
        _web_bind = "127.0.0.1"
    elif _web_bind_raw in ("any", "all", "lan", "0.0.0.0"):
        _web_bind = "0.0.0.0"
    else:
        _web_bind = _web_bind_raw  # Treat as a literal IP address.
    web_server: WebServer | None = WebServer(manager, _web_bind, _web_port) if _web_port > 0 else None
    shutdown_event = asyncio.Event()
    reload_event = asyncio.Event()

    def request_shutdown():
        log.info("Shutdown requested")
        shutdown_event.set()

    def request_reload():
        log.info("SIGHUP received - reloading configuration")
        reload_event.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, request_shutdown)
    loop.add_signal_handler(signal.SIGINT, request_shutdown)
    loop.add_signal_handler(signal.SIGHUP, request_reload)

    # Start the socket server first so the CLI can connect and poll status
    # while device connections are still in progress.
    await server.start()
    if web_server:
        await web_server.start()
    log.info("Daemon ready")
    await manager.connect_all()

    # Main loop - handle shutdown and reload signals
    while not shutdown_event.is_set():
        reload_event.clear()

        wait_shutdown = asyncio.ensure_future(shutdown_event.wait())
        wait_reload = asyncio.ensure_future(reload_event.wait())
        done, pending = await asyncio.wait(
            [wait_shutdown, wait_reload],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

        if shutdown_event.is_set():
            break

        if reload_event.is_set():
            load_env()
            new_devices = load_devices()
            if new_devices:
                await manager.handle_reload(new_devices)
            else:
                log.warning("Reload: no devices found in config, keeping existing devices")

    # Graceful shutdown
    log.info("Shutting down...")
    if web_server:
        await web_server.stop()
    await server.stop()
    await manager.disconnect_all()
    log.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
