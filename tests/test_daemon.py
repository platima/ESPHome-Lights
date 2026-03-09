"""
Tests for esphome-lightsd.py — daemon logic.

These tests exercise the DeviceManager command handlers, SocketServer
dispatch, config loading, and the JSON protocol without requiring real
ESPHome devices.  Network-level connection behaviour is tested via mocks.
"""

import asyncio
import importlib
import json
import logging
import logging.handlers
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the project root is on sys.path so we can import the daemon module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# We need to mock aioesphomeapi before importing the daemon, since the import
# will fail in environments without the library installed.
mock_api = MagicMock()
sys.modules.setdefault("aioesphomeapi", mock_api)

# The filename contains a hyphen, so we must use importlib.
daemon = importlib.import_module("esphome-lightsd")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(devices=None):
    """Create a DeviceManager with fake device config."""
    if devices is None:
        devices = {
            "living_room": {
                "host": "10.0.0.1",
                "port": 6053,
                "encryption_key": "abc123",
            },
            "bedroom": {
                "host": "10.0.0.2",
                "port": 6053,
                "encryption_key": "def456",
            },
        }
    return daemon.DeviceManager(devices)


def _fake_light_entity(key=1, object_id="light"):
    """Return a mock LightInfo entity."""
    entity = MagicMock()
    entity.__class__ = type("LightInfo", (), {})
    entity.__class__.__name__ = "LightInfo"
    entity.key = key
    entity.object_id = object_id
    return entity


def _fake_switch_entity(key=2, object_id="relay"):
    """Return a mock SwitchInfo entity."""
    entity = MagicMock()
    entity.__class__ = type("SwitchInfo", (), {})
    entity.__class__.__name__ = "SwitchInfo"
    entity.key = key
    entity.object_id = object_id
    return entity


def _fake_light_state(key=1, state=True, brightness=0.5, r=1.0, g=0.0, b=0.0):
    """Return a mock LightState."""
    st = MagicMock()
    st.__class__ = type("LightState", (), {})
    st.__class__.__name__ = "LightState"
    st.key = key
    st.state = state
    st.brightness = brightness
    st.red = r
    st.green = g
    st.blue = b
    return st


def _fake_switch_state(key=2, state=True):
    """Return a mock SwitchState."""
    st = MagicMock()
    st.__class__ = type("SwitchState", (), {})
    st.__class__.__name__ = "SwitchState"
    st.key = key
    st.state = state
    return st


# ---------------------------------------------------------------------------
# load_devices / load_env
# ---------------------------------------------------------------------------


class TestLoadDevices(unittest.TestCase):
    """Test device discovery from environment variables."""

    def test_loads_valid_devices(self):
        env = {
            "ESPHOME_LIGHTS_KITCHEN": "10.0.0.3:6053|keyABC",
            "ESPHOME_LIGHTS_LOUNGE": "10.0.0.4:6053|keyXYZ",
        }
        with patch.dict(os.environ, env, clear=True):
            devices = daemon.load_devices()
        self.assertIn("kitchen", devices)
        self.assertIn("lounge", devices)
        self.assertEqual(devices["kitchen"]["host"], "10.0.0.3")
        self.assertEqual(devices["kitchen"]["port"], 6053)
        self.assertEqual(devices["kitchen"]["encryption_key"], "keyABC")

    def test_skips_socket_and_log_level_vars(self):
        env = {
            "ESPHOME_LIGHTS_SOCKET": "/tmp/test.sock",
            "ESPHOME_LIGHTS_LOG_LEVEL": "DEBUG",
            "ESPHOME_LIGHTS_LOG_FILE": "/tmp/test.log",
            "ESPHOME_LIGHTS_REAL": "1.2.3.4:6053|k",
        }
        with patch.dict(os.environ, env, clear=True):
            devices = daemon.load_devices()
        self.assertNotIn("socket", devices)
        self.assertNotIn("log_level", devices)
        self.assertNotIn("log_file", devices)
        self.assertIn("real", devices)

    def test_skips_invalid_format(self):
        env = {"ESPHOME_LIGHTS_BAD": "not-valid-format"}
        with patch.dict(os.environ, env, clear=True):
            devices = daemon.load_devices()
        self.assertEqual(devices, {})


# ---------------------------------------------------------------------------
# DeviceManager — command handlers
# ---------------------------------------------------------------------------


class TestDeviceManagerList(unittest.TestCase):
    """Test the list command handler."""

    def test_list_returns_all_devices(self):
        mgr = _make_manager()
        mgr._conn_state = {"living_room": "connected", "bedroom": "disconnected"}
        mgr._entity_info = {
            "living_room": {"key": 1, "type": "light"},
            "bedroom": {"key": 2, "type": "switch"},
        }
        result = mgr.handle_list()
        self.assertTrue(result["ok"])
        self.assertIn("living_room", result["result"])
        self.assertIn("bedroom", result["result"])
        self.assertEqual(result["result"]["living_room"]["connection"], "connected")
        self.assertEqual(result["result"]["living_room"]["entity_type"], "light")
        self.assertEqual(result["result"]["bedroom"]["entity_type"], "switch")

    def test_list_empty(self):
        mgr = _make_manager(devices={})
        result = mgr.handle_list()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], {})


class TestDeviceManagerStatus(unittest.TestCase):
    """Test the status command handler."""

    def test_status_with_cached_state(self):
        mgr = _make_manager()
        mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        mgr._state_cache = {
            "living_room": {
                "state": "ON",
                "brightness": 128,
                "rgb": None,
                "entity_type": "light",
            }
        }
        result = mgr.handle_status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["living_room"]["state"], "ON")
        # bedroom has no cache — should show unknown
        self.assertEqual(result["result"]["bedroom"]["state"], "unknown")

    def test_status_no_cache(self):
        mgr = _make_manager()
        mgr._conn_state = {"living_room": "disconnected", "bedroom": "disconnected"}
        result = mgr.handle_status()
        self.assertTrue(result["ok"])
        for name in result["result"]:
            self.assertEqual(result["result"][name]["state"], "unknown")


class TestDeviceManagerSet(unittest.TestCase):
    """Test the set command handler."""

    def _connected_manager(self):
        mgr = _make_manager()
        mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        client_mock = MagicMock()
        mgr._clients = {"living_room": client_mock, "bedroom": client_mock}
        mgr._entity_info = {
            "living_room": {"key": 1, "type": "light"},
            "bedroom": {"key": 2, "type": "switch"},
        }
        return mgr, client_mock

    def test_set_on_light(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("living_room", "on")
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "Turned ON")
        client.light_command.assert_called_once_with(1, state=True)

    def test_set_off_light(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("living_room", "off")
        self.assertTrue(result["ok"])
        client.light_command.assert_called_once_with(1, state=False)

    def test_set_on_switch(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("bedroom", "on")
        self.assertTrue(result["ok"])
        client.switch_command.assert_called_once_with(2, state=True)

    def test_set_brightness_light(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("living_room", "brightness", "128")
        self.assertTrue(result["ok"])
        self.assertIn("128", result["result"])
        client.light_command.assert_called_once_with(1, brightness=128 / 255.0)

    def test_set_brightness_switch_rejected(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("bedroom", "brightness", "128")
        self.assertFalse(result["ok"])
        self.assertIn("switch", result["error"].lower())

    def test_set_rgb_light(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("living_room", "rgb", "255,0,128")
        self.assertTrue(result["ok"])
        client.light_command.assert_called_once_with(
            1, rgb=(1.0, 0.0, 128 / 255.0)
        )

    def test_set_rgb_switch_rejected(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("bedroom", "rgb", "255,0,0")
        self.assertFalse(result["ok"])
        self.assertIn("switch", result["error"].lower())

    def test_set_rgb_invalid_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "rgb", "not,valid,rgb")
        self.assertFalse(result["ok"])

    def test_set_brightness_invalid_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "brightness", "abc")
        self.assertFalse(result["ok"])

    def test_set_device_not_found(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("nonexistent", "on")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_set_device_not_connected(self):
        mgr, _ = self._connected_manager()
        mgr._conn_state["living_room"] = "disconnected"
        result = mgr.handle_set("living_room", "on")
        self.assertFalse(result["ok"])
        self.assertIn("not connected", result["error"].lower())

    def test_set_unknown_action(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "flicker")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"].lower())

    def test_set_no_entity(self):
        mgr, _ = self._connected_manager()
        mgr._entity_info["living_room"] = {"key": None, "type": None}
        result = mgr.handle_set("living_room", "on")
        self.assertFalse(result["ok"])
        self.assertIn("no controllable entity", result["error"].lower())

    def test_set_brightness_missing_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "brightness")
        self.assertFalse(result["ok"])
        self.assertIn("requires a value", result["error"].lower())

    def test_set_rgb_missing_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "rgb")
        self.assertFalse(result["ok"])
        self.assertIn("requires a value", result["error"].lower())

    # -- wildcard 'all' -------------------------------------------------------

    def test_set_all_on(self):
        """'all' broadcasts to every device and returns a summary."""
        mgr, client = self._connected_manager()
        result = mgr.handle_set("all", "on")
        self.assertTrue(result["ok"])
        self.assertIn("bedroom", result["result"])
        self.assertIn("living_room", result["result"])

    def test_set_all_partial_disconnected(self):
        """'all' skips disconnected devices but still returns ok=True for the ones that worked."""
        mgr, _ = self._connected_manager()
        mgr._conn_state["bedroom"] = "disconnected"
        result = mgr.handle_set("all", "on")
        self.assertTrue(result["ok"])
        self.assertIn("living_room", result["result"])
        self.assertIn("skipped", result["result"])

    def test_set_all_none_connected(self):
        """'all' returns ok=False when every device is disconnected."""
        mgr, _ = self._connected_manager()
        mgr._conn_state = {"living_room": "disconnected", "bedroom": "disconnected"}
        result = mgr.handle_set("all", "on")
        self.assertFalse(result["ok"])


class TestDeviceManagerPing(unittest.TestCase):
    def test_ping(self):
        result = daemon.DeviceManager.handle_ping()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "pong")


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


class TestEntityResolution(unittest.TestCase):
    """Test that _resolve_entity prefers LightInfo over SwitchInfo."""

    def test_prefers_light_over_switch(self):
        mgr = _make_manager()
        entities = [_fake_switch_entity(key=2), _fake_light_entity(key=1)]
        mgr._resolve_entity("living_room", entities)
        self.assertEqual(mgr._entity_info["living_room"]["key"], 1)
        self.assertEqual(mgr._entity_info["living_room"]["type"], "light")

    def test_falls_back_to_switch(self):
        mgr = _make_manager()
        entities = [_fake_switch_entity(key=5)]
        mgr._resolve_entity("living_room", entities)
        self.assertEqual(mgr._entity_info["living_room"]["key"], 5)
        self.assertEqual(mgr._entity_info["living_room"]["type"], "switch")

    def test_skips_status_led(self):
        mgr = _make_manager()
        entities = [_fake_light_entity(key=9, object_id="status_led")]
        mgr._resolve_entity("living_room", entities)
        self.assertIsNone(mgr._entity_info["living_room"]["key"])

    def test_no_entities(self):
        mgr = _make_manager()
        mgr._resolve_entity("living_room", [])
        self.assertIsNone(mgr._entity_info["living_room"]["key"])


# ---------------------------------------------------------------------------
# State cache handling
# ---------------------------------------------------------------------------


class TestStateCache(unittest.TestCase):
    """Test that _handle_state correctly caches entity states."""

    def test_caches_light_state(self):
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        state = _fake_light_state(key=1, state=True, brightness=0.5, r=1.0, g=0.0, b=0.0)
        mgr._handle_state("living_room", state)
        cached = mgr._state_cache["living_room"]
        self.assertEqual(cached["state"], "ON")
        self.assertEqual(cached["brightness"], 128)
        self.assertEqual(cached["rgb"], "255,0,0")
        self.assertEqual(cached["entity_type"], "light")

    def test_caches_switch_state(self):
        mgr = _make_manager()
        mgr._entity_info = {"bedroom": {"key": 2, "type": "switch"}}
        state = _fake_switch_state(key=2, state=False)
        mgr._handle_state("bedroom", state)
        cached = mgr._state_cache["bedroom"]
        self.assertEqual(cached["state"], "OFF")
        self.assertIsNone(cached["brightness"])
        self.assertEqual(cached["entity_type"], "switch")

    def test_ignores_mismatched_key(self):
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        state = _fake_light_state(key=999)
        mgr._handle_state("living_room", state)
        self.assertNotIn("living_room", mgr._state_cache)

    def test_ignores_unknown_device(self):
        mgr = _make_manager()
        state = _fake_light_state(key=1)
        mgr._handle_state("nonexistent", state)
        self.assertNotIn("nonexistent", mgr._state_cache)


# ---------------------------------------------------------------------------
# SocketServer dispatch
# ---------------------------------------------------------------------------


class TestSocketServerDispatch(unittest.TestCase):
    """Test that _dispatch routes commands correctly."""

    def setUp(self):
        self.mgr = _make_manager()
        self.server = daemon.SocketServer(self.mgr, "/tmp/test.sock")

    def test_dispatch_list(self):
        result = asyncio.run(self.server._dispatch({"cmd": "list"}))
        self.assertTrue(result["ok"])

    def test_dispatch_status(self):
        self.mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        result = asyncio.run(self.server._dispatch({"cmd": "status"}))
        self.assertTrue(result["ok"])

    def test_dispatch_ping(self):
        result = asyncio.run(self.server._dispatch({"cmd": "ping"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "pong")

    def test_dispatch_set_valid(self):
        self.mgr._conn_state = {"living_room": "connected"}
        self.mgr._clients = {"living_room": MagicMock()}
        self.mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        result = asyncio.run(
            self.server._dispatch(
                {"cmd": "set", "device": "living_room", "action": "on"}
            )
        )
        self.assertTrue(result["ok"])

    def test_dispatch_set_missing_device(self):
        result = asyncio.run(self.server._dispatch({"cmd": "set", "action": "on"}))
        self.assertFalse(result["ok"])
        self.assertIn("device", result["error"].lower())

    def test_dispatch_set_missing_action(self):
        result = asyncio.run(self.server._dispatch({"cmd": "set", "device": "living_room"}))
        self.assertFalse(result["ok"])
        self.assertIn("action", result["error"].lower())

    def test_dispatch_missing_cmd(self):
        result = asyncio.run(self.server._dispatch({}))
        self.assertFalse(result["ok"])
        self.assertIn("cmd", result["error"].lower())

    def test_dispatch_unknown_cmd(self):
        result = asyncio.run(self.server._dispatch({"cmd": "explode"}))
        self.assertFalse(result["ok"])
        self.assertIn("unknown", result["error"].lower())

    def test_dispatch_reload(self):
        """reload command calls load_env/load_devices and handle_reload."""
        _fake_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"}
        }

        async def run():
            with patch.object(daemon, "load_env") as mock_env, \
                 patch.object(daemon, "load_devices", return_value=_fake_devices) as mock_dev, \
                 patch.object(self.mgr, "handle_reload", new=AsyncMock(return_value={"ok": True, "result": "no changes"})) as mock_reload:
                result = await self.server._dispatch({"cmd": "reload"})
            mock_env.assert_called_once()
            mock_dev.assert_called_once()
            mock_reload.assert_called_once_with(_fake_devices)
            self.assertTrue(result["ok"])

        asyncio.run(run())


class TestLoadEnvPriority(unittest.TestCase):
    """Test that load_env loads files in priority order."""

    def test_high_priority_overrides_low(self):
        """Variables from a higher-priority file override lower-priority ones."""
        with tempfile.TemporaryDirectory() as tmpdir:
            low = os.path.join(tmpdir, "low.env")
            high = os.path.join(tmpdir, "high.env")
            with open(low, "w") as f:
                f.write('ESPHOME_LIGHTS_TEST_VAR="low_value"\n')
            with open(high, "w") as f:
                f.write('ESPHOME_LIGHTS_TEST_VAR="high_value"\n')

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ESPHOME_LIGHTS_TEST_VAR", None)
                daemon._parse_env_file(low)
                self.assertEqual(os.environ.get("ESPHOME_LIGHTS_TEST_VAR"), "low_value")
                daemon._parse_env_file(high)
                self.assertEqual(os.environ.get("ESPHOME_LIGHTS_TEST_VAR"), "high_value")

    def test_parse_strips_quotes(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write('MY_QUOTED="hello world"\n')
            f.write("MY_SINGLE='bye world'\n")
            fname = f.name
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MY_QUOTED", None)
                os.environ.pop("MY_SINGLE", None)
                daemon._parse_env_file(fname)
                self.assertEqual(os.environ.get("MY_QUOTED"), "hello world")
                self.assertEqual(os.environ.get("MY_SINGLE"), "bye world")
        finally:
            os.unlink(fname)

    def test_parse_skips_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write("# This is a comment\n")
            f.write("\n")
            f.write('REAL_VAR="value"\n')
            fname = f.name
        try:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("REAL_VAR", None)
                daemon._parse_env_file(fname)
                self.assertEqual(os.environ.get("REAL_VAR"), "value")
        finally:
            os.unlink(fname)

    def test_parse_missing_file_is_noop(self):
        """Parsing a non-existent file should not raise."""
        daemon._parse_env_file("/nonexistent/path/to/file.env")  # should not raise


# ---------------------------------------------------------------------------
# DeviceManager reload
# ---------------------------------------------------------------------------


class TestDeviceManagerReload(unittest.TestCase):
    """Test the handle_reload method."""

    def _connected_manager(self):
        """Return a manager with two connected mock clients."""
        devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"},
            "bedroom": {"host": "10.0.0.2", "port": 6053, "encryption_key": "def"},
        }
        mgr = daemon.DeviceManager(devices)
        mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        mgr._clients = {"living_room": AsyncMock(), "bedroom": AsyncMock()}
        return mgr

    def test_reload_no_changes(self):
        """Reload with identical config should report no changes."""
        mgr = self._connected_manager()
        new_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"},
            "bedroom": {"host": "10.0.0.2", "port": 6053, "encryption_key": "def"},
        }

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()):
                result = await mgr.handle_reload(new_devices)
            self.assertTrue(result["ok"])
            self.assertIn("0 added", result["result"])
            self.assertIn("0 removed", result["result"])
            self.assertIn("2 unchanged", result["result"])

        asyncio.run(run())

    def test_reload_adds_new_device(self):
        """Reload with a new device should connect it."""
        mgr = self._connected_manager()
        new_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"},
            "bedroom": {"host": "10.0.0.2", "port": 6053, "encryption_key": "def"},
            "kitchen": {"host": "10.0.0.3", "port": 6053, "encryption_key": "ghi"},
        }

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()) as mock_connect:
                result = await mgr.handle_reload(new_devices)
            mock_connect.assert_called_once()
            self.assertIn("1 added", result["result"])

        asyncio.run(run())

    def test_reload_removes_device(self):
        """Reload without a previously present device should disconnect it."""
        mgr = self._connected_manager()
        new_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"},
        }
        mock_client = AsyncMock()
        mgr._clients["bedroom"] = mock_client

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()):
                result = await mgr.handle_reload(new_devices)
            self.assertIn("1 removed", result["result"])

        asyncio.run(run())

    def test_reload_changed_device_reconnects(self):
        """Reload with a changed encryption key should disconnect then reconnect."""
        mgr = self._connected_manager()
        new_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "NEW_KEY"},
            "bedroom": {"host": "10.0.0.2", "port": 6053, "encryption_key": "def"},
        }

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()) as mock_connect:
                result = await mgr.handle_reload(new_devices)
            mock_connect.assert_called_once()
            self.assertIn("1 changed", result["result"])

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Integration-style test: full socket round-trip
# ---------------------------------------------------------------------------


class TestSocketRoundTrip(unittest.TestCase):
    """Test actual socket communication between server and a client."""

    def test_ping_round_trip(self):
        """Start a SocketServer, connect a client, send ping, get pong."""

        async def run():
            mgr = _make_manager()
            sock_path = tempfile.mktemp(suffix=".sock")
            server = daemon.SocketServer(mgr, sock_path)

            try:
                await server.start()

                # Connect as a client
                reader, writer = await asyncio.open_unix_connection(sock_path)
                writer.write(json.dumps({"cmd": "ping"}).encode() + b"\n")
                await writer.drain()

                line = await asyncio.wait_for(reader.readline(), timeout=2)
                resp = json.loads(line.decode())
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["result"], "pong")

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

        asyncio.run(run())

    def test_multiple_commands_one_connection(self):
        """A single client can send multiple commands on one connection."""

        async def run():
            mgr = _make_manager()
            mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
            sock_path = tempfile.mktemp(suffix=".sock")
            server = daemon.SocketServer(mgr, sock_path)

            try:
                await server.start()
                reader, writer = await asyncio.open_unix_connection(sock_path)

                # First command: ping
                writer.write(json.dumps({"cmd": "ping"}).encode() + b"\n")
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=2)
                self.assertEqual(json.loads(line)["result"], "pong")

                # Second command: list
                writer.write(json.dumps({"cmd": "list"}).encode() + b"\n")
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=2)
                resp = json.loads(line)
                self.assertTrue(resp["ok"])
                self.assertIn("living_room", resp["result"])

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

        asyncio.run(run())

    def test_invalid_json(self):
        """Server returns an error for malformed JSON."""

        async def run():
            mgr = _make_manager()
            sock_path = tempfile.mktemp(suffix=".sock")
            server = daemon.SocketServer(mgr, sock_path)

            try:
                await server.start()
                reader, writer = await asyncio.open_unix_connection(sock_path)

                writer.write(b"this is not json\n")
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout=2)
                resp = json.loads(line)
                self.assertFalse(resp["ok"])
                self.assertIn("Invalid JSON", resp["error"])

                writer.close()
                await writer.wait_closed()
            finally:
                await server.stop()

        asyncio.run(run())


# ---------------------------------------------------------------------------
# _configure_logging
# ---------------------------------------------------------------------------


class TestConfigureLogging(unittest.TestCase):
    """Test _configure_logging() file handler setup."""

    def _strip_file_handlers(self):
        """Remove any RotatingFileHandlers left by previous test runs."""
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.handlers.RotatingFileHandler):
                h.close()
                root.removeHandler(h)

    def setUp(self):
        self._strip_file_handlers()

    def tearDown(self):
        self._strip_file_handlers()

    def test_file_handler_added_when_path_set(self):
        """A RotatingFileHandler is attached when a valid log path is given."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": log_path}, clear=False):
                daemon._configure_logging()
            file_handlers = [
                h for h in logging.getLogger().handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)
            self.assertEqual(file_handlers[0].baseFilename, log_path)
            # Close and remove handlers before tmpdir exit to avoid
            # Windows file-locking errors when the temp dir is deleted.
            self._strip_file_handlers()

    def test_file_logging_disabled_via_none(self):
        """Setting ESPHOME_LIGHTS_LOG_FILE=none disables file logging."""
        with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": "none"}, clear=False):
            daemon._configure_logging()
        file_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 0)

    def test_file_logging_disabled_via_off(self):
        """Setting ESPHOME_LIGHTS_LOG_FILE=off disables file logging."""
        with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": "off"}, clear=False):
            daemon._configure_logging()
        file_handlers = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        self.assertEqual(len(file_handlers), 0)

    def test_file_logging_disabled_case_insensitive(self):
        """Disabled values are matched case-insensitively."""
        for val in ("NONE", "OFF", "False", "0", "NO"):
            with self.subTest(val=val):
                self._strip_file_handlers()
                with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": val}, clear=False):
                    daemon._configure_logging()
                file_handlers = [
                    h for h in logging.getLogger().handlers
                    if isinstance(h, logging.handlers.RotatingFileHandler)
                ]
                self.assertEqual(len(file_handlers), 0, f"Expected no handler for LOG_FILE={val}")

    def test_log_dir_created_if_missing(self):
        """Missing parent directories are created automatically."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "subdir", "nested", "daemon.log")
            with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": log_path}, clear=False):
                daemon._configure_logging()
            self.assertTrue(os.path.isdir(os.path.dirname(log_path)))
            # Close and remove handlers before tmpdir exit to avoid
            # Windows file-locking errors when the temp dir is deleted.
            self._strip_file_handlers()

    def test_oserror_on_dir_creation_logs_warning(self):
        """An OSError during dir creation emits a warning but does not raise."""
        with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": "/no_such_root/a/b.log"}, clear=False):
            # This path is intentionally unwritable; just verify no exception propagates.
            try:
                daemon._configure_logging()
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_configure_logging() raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# Command audit logging
# ---------------------------------------------------------------------------


class TestCommandAuditLogging(unittest.TestCase):
    """Test that _dispatch emits a structured audit log line for each command."""

    def setUp(self):
        self.mgr = _make_manager()
        self.server = daemon.SocketServer(self.mgr, "/tmp/test.sock")

    def test_ping_is_logged(self):
        """ping produces an audit line containing 'cmd=ping'."""
        with self.assertLogs("esphome-lightsd", level="INFO") as cm:
            asyncio.run(self.server._dispatch({"cmd": "ping"}))
        self.assertTrue(any("cmd=ping" in line for line in cm.output))

    def test_set_on_logs_device_and_action(self):
        """set command logs device and action in the audit line."""
        self.mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        self.mgr._clients = {"living_room": MagicMock(), "bedroom": MagicMock()}
        self.mgr._entity_info = {
            "living_room": {"key": 1, "type": "light"},
            "bedroom": {"key": 2, "type": "switch"},
        }
        with self.assertLogs("esphome-lightsd", level="INFO") as cm:
            asyncio.run(self.server._dispatch(
                {"cmd": "set", "device": "living_room", "action": "on"}
            ))
        self.assertTrue(
            any("cmd=set" in line and "device=living_room" in line and "action=on" in line
                for line in cm.output)
        )

    def test_set_brightness_logs_value(self):
        """set brightness command includes the value in the audit line."""
        self.mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        self.mgr._clients = {"living_room": MagicMock(), "bedroom": MagicMock()}
        self.mgr._entity_info = {
            "living_room": {"key": 1, "type": "light"},
            "bedroom": {"key": 2, "type": "switch"},
        }
        with self.assertLogs("esphome-lightsd", level="INFO") as cm:
            asyncio.run(self.server._dispatch(
                {"cmd": "set", "device": "living_room", "action": "brightness", "value": "128"}
            ))
        self.assertTrue(
            any("value=128" in line for line in cm.output)
        )

    def test_error_result_is_logged(self):
        """Failed commands log an error indicator in the audit line."""
        with self.assertLogs("esphome-lightsd", level="INFO") as cm:
            asyncio.run(self.server._dispatch(
                {"cmd": "set", "device": "nonexistent", "action": "on"}
            ))
        self.assertTrue(
            any("→ error:" in line for line in cm.output)
        )

    def test_reload_is_logged(self):
        """reload command produces an audit line containing 'cmd=reload'."""
        _fake_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"}
        }

        async def run():
            with patch.object(daemon, "load_env"), \
                 patch.object(daemon, "load_devices", return_value=_fake_devices), \
                 patch.object(self.mgr, "handle_reload",
                              new=AsyncMock(return_value={"ok": True, "result": "0 added"})):
                with self.assertLogs("esphome-lightsd", level="INFO") as cm:
                    await self.server._dispatch({"cmd": "reload"})
            self.assertTrue(any("cmd=reload" in line for line in cm.output))

        asyncio.run(run())

    def test_unknown_cmd_is_logged(self):
        """Unknown commands are still logged with the error indicator."""
        with self.assertLogs("esphome-lightsd", level="INFO") as cm:
            asyncio.run(self.server._dispatch({"cmd": "explode"}))
        self.assertTrue(
            any("cmd=explode" in line and "→ error:" in line for line in cm.output)
        )


if __name__ == "__main__":
    unittest.main()
