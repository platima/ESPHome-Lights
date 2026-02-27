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
  {"cmd": "ping"}
  {"cmd": "reload"}

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
"""

import asyncio
import json
import logging
import os
import signal
import sys

from aioesphomeapi import APIClient

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("ESPHOME_LIGHTS_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("esphome-lightsd")

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
        log.info("Connecting to %s (%s:%s)…", name, cfg["host"], cfg["port"])

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

        # Prefer LightInfo — supports brightness and RGB
        for entity in entities:
            cls = entity.__class__.__name__
            if cls == "LightInfo" and getattr(entity, "object_id", "") != "status_led":
                control_key = entity.key
                control_type = "light"
                break

        if control_key is None:
            # Fall back to SwitchInfo (smart plugs, relays, etc.)
            for entity in entities:
                if entity.__class__.__name__ == "SwitchInfo":
                    control_key = entity.key
                    control_type = "switch"
                    break

        self._entity_info[name] = {"key": control_key, "type": control_type}

    def _handle_state(self, name: str, state):
        """Cache incoming entity state updates."""
        entity = self._entity_info.get(name, {})
        if entity.get("key") is None:
            return

        cls = state.__class__.__name__
        if cls == "LightState" and state.key == entity["key"]:
            self._state_cache[name] = {
                "state": "ON" if state.state else "OFF",
                "brightness": round(state.brightness * 255) if state.brightness is not None else None,
                "rgb": (
                    f"{round(state.red * 255)},{round(state.green * 255)},{round(state.blue * 255)}"
                    if state.red is not None
                    else None
                ),
                "entity_type": "light",
            }
        elif cls == "SwitchState" and state.key == entity["key"]:
            self._state_cache[name] = {
                "state": "ON" if state.state else "OFF",
                "brightness": None,
                "rgb": None,
                "entity_type": "switch",
            }

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
        self._reconnect_tasks[name] = asyncio.get_event_loop().create_task(
            self._reconnect_loop(name)
        )

    async def _reconnect_loop(self, name: str):
        """Retry connecting with exponential backoff."""
        delay = RECONNECT_MIN
        while True:
            log.info("Reconnecting to %s in %ss…", name, delay)
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
        """Return configured devices with connection state and entity type."""
        result = {}
        for name, cfg in sorted(self._devices.items()):
            entity = self._entity_info.get(name, {})
            result[name] = {
                "host": cfg["host"],
                "port": cfg["port"],
                "connection": self._conn_state.get(name, "unknown"),
                "entity_type": entity.get("type"),
            }
        return {"ok": True, "result": result}

    def handle_status(self) -> dict:
        """Return cached state for all devices."""
        result = {}
        for name in sorted(self._devices):
            cached = self._state_cache.get(name)
            conn = self._conn_state.get(name, "unknown")
            if cached:
                result[name] = {**cached, "connection": conn}
            else:
                result[name] = {"state": "unknown", "connection": conn}
        return {"ok": True, "result": result}

    def handle_set(self, device: str, action: str, value: str | None = None) -> dict:
        """Execute a set command on a device."""
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
            return {"ok": False, "error": "Missing 'cmd' field"}

        if cmd == "list":
            return self._manager.handle_list()
        elif cmd == "status":
            return self._manager.handle_status()
        elif cmd == "ping":
            return self._manager.handle_ping()
        elif cmd == "reload":
            load_env()
            new_devices = load_devices()
            if not new_devices:
                return {"ok": False, "error": "No devices found in config after reload"}
            return await self._manager.handle_reload(new_devices)
        elif cmd == "set":
            device = request.get("device")
            action = request.get("action")
            value = request.get("value")
            if not device:
                return {"ok": False, "error": "Missing 'device' field"}
            if not action:
                return {"ok": False, "error": "Missing 'action' field"}
            return self._manager.handle_set(device, action, value)
        else:
            return {"ok": False, "error": f"Unknown command '{cmd}'"}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def main():
    load_env()
    devices = load_devices()

    if not devices:
        log.error("No devices configured (set ESPHOME_LIGHTS_* environment variables)")
        sys.exit(1)

    log.info("Found %d device(s): %s", len(devices), ", ".join(sorted(devices)))

    manager = DeviceManager(devices)
    server = SocketServer(manager)

    # Set up graceful shutdown and config-reload events
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

    # Start up
    await manager.connect_all()
    await server.start()

    log.info("Daemon ready")

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
    await server.stop()
    await manager.disconnect_all()
    log.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
