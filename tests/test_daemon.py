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


def _fake_light_state(key=1, state=True, brightness=0.5, r=1.0, g=0.0, b=0.0,
                      color_temperature=None, cold_white=None, warm_white=None):
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
    st.color_temperature = color_temperature
    st.cold_white = cold_white
    st.warm_white = warm_white
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

    def test_set_color_temp_light(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("living_room", "color_temp", "2700")
        self.assertTrue(result["ok"])
        self.assertIn("2700", result["result"])
        client.light_command.assert_called_once_with(
            1, color_temperature=1_000_000.0 / 2700
        )

    def test_set_color_temp_switch_rejected(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("bedroom", "color_temp", "2700")
        self.assertFalse(result["ok"])
        self.assertIn("switch", result["error"].lower())

    def test_set_color_temp_invalid_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "color_temp", "abc")
        self.assertFalse(result["ok"])

    def test_set_color_temp_missing_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "color_temp")
        self.assertFalse(result["ok"])
        self.assertIn("requires a value", result["error"].lower())

    def test_set_cwww_light(self):
        mgr, client = self._connected_manager()
        result = mgr.handle_set("living_room", "cwww", "200,50")
        self.assertTrue(result["ok"])
        client.light_command.assert_called_once_with(
            1, cold_white=200 / 255.0, warm_white=50 / 255.0
        )

    def test_set_cwww_switch_rejected(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("bedroom", "cwww", "200,50")
        self.assertFalse(result["ok"])
        self.assertIn("switch", result["error"].lower())

    def test_set_cwww_invalid_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "cwww", "abc,def")
        self.assertFalse(result["ok"])

    def test_set_cwww_missing_value(self):
        mgr, _ = self._connected_manager()
        result = mgr.handle_set("living_room", "cwww")
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
        self.assertIsNone(cached["color_temp"])
        self.assertIsNone(cached["cold_white"])
        self.assertIsNone(cached["warm_white"])
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

    def test_caches_light_state_with_color_temp(self):
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        # color_temperature is in mireds; 370 mireds = 2702.7...K -> round to 2703
        state = _fake_light_state(key=1, state=True, color_temperature=370.0)
        mgr._handle_state("living_room", state)
        cached = mgr._state_cache["living_room"]
        self.assertEqual(cached["color_temp"], 2703)
        self.assertIsNone(cached["cold_white"])
        self.assertIsNone(cached["warm_white"])

    def test_caches_light_state_with_cwww(self):
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        state = _fake_light_state(key=1, state=True, cold_white=0.8, warm_white=0.2)
        mgr._handle_state("living_room", state)
        cached = mgr._state_cache["living_room"]
        self.assertIsNone(cached["color_temp"])
        self.assertEqual(cached["cold_white"], 204)
        self.assertEqual(cached["warm_white"], 51)

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

    def test_dispatch_reconnect(self):
        """reconnect command is routed to handle_reconnect."""

        async def run():
            with patch.object(
                self.mgr,
                "handle_reconnect",
                new=AsyncMock(return_value={"ok": True, "result": "Reconnected to living_room"}),
            ) as mock_reconnect:
                result = await self.server._dispatch(
                    {"cmd": "reconnect", "device": "living_room"}
                )
            mock_reconnect.assert_called_once_with("living_room")
            self.assertTrue(result["ok"])

        asyncio.run(run())

    def test_dispatch_reconnect_default_all(self):
        """reconnect without a device field defaults to 'all'."""

        async def run():
            with patch.object(
                self.mgr,
                "handle_reconnect",
                new=AsyncMock(return_value={"ok": True, "result": "..."}),
            ) as mock_reconnect:
                await self.server._dispatch({"cmd": "reconnect"})
            mock_reconnect.assert_called_once_with("all")

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
        bad_path = "/no_such_root/a/b.log"
        with patch.dict(os.environ, {"ESPHOME_LIGHTS_LOG_FILE": bad_path}, clear=False), \
             patch("os.makedirs", side_effect=OSError("mocked permission error")):
            # os.makedirs is mocked to always raise, so this test is
            # platform-independent (on Windows the original path was
            # writable, accidentally creating d:\no_such_root\).
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
            any("-> error:" in line for line in cm.output)
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
            any("cmd=explode" in line and "-> error:" in line for line in cm.output)
        )


# ---------------------------------------------------------------------------
# Reconnect handler
# ---------------------------------------------------------------------------


class TestReconnectHandler(unittest.TestCase):
    """Test handle_reconnect and _reconnect_device."""

    def _disconnected_manager(self):
        """Manager with two devices, both disconnected."""
        mgr = _make_manager()
        mgr._conn_state = {"living_room": "disconnected", "bedroom": "disconnected"}
        return mgr

    def _connected_manager(self):
        """Manager with living_room connected and a mock client."""
        mgr = _make_manager()
        client = MagicMock()
        client.disconnect = AsyncMock()
        mgr._clients = {"living_room": client}
        mgr._conn_state = {"living_room": "connected", "bedroom": "disconnected"}
        mgr._state_cache = {"living_room": {"state": "ON"}}
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        return mgr, client

    def test_reconnect_nonexistent_device(self):
        """handle_reconnect returns an error for an unknown device name."""
        mgr = self._disconnected_manager()

        async def run():
            return await mgr.handle_reconnect("kitchen")

        result = asyncio.run(run())
        self.assertFalse(result["ok"])
        self.assertIn("kitchen", result["error"])

    def test_reconnect_cancels_backoff_and_connects(self):
        """Pending backoff task is cancelled and _connect is called immediately."""
        mgr = self._disconnected_manager()
        fake_task = MagicMock()
        fake_task.done.return_value = False
        fake_task.cancel = MagicMock()
        mgr._reconnect_tasks["living_room"] = fake_task

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()) as mock_connect:
                # Simulate a successful connection after _connect is called
                async def _set_connected(name):
                    mgr._conn_state[name] = "connected"

                mock_connect.side_effect = _set_connected
                result = await mgr.handle_reconnect("living_room")
            return result, mock_connect

        result, mock_connect = asyncio.run(run())
        fake_task.cancel.assert_called_once()
        mock_connect.assert_called_once_with("living_room")
        self.assertTrue(result["ok"])
        self.assertIn("living_room", result["result"])
        self.assertNotIn("living_room", mgr._reconnect_tasks)

    def test_reconnect_disconnects_existing_client(self):
        """An existing connected client is disconnected before reconnecting."""
        mgr, old_client = self._connected_manager()

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()) as mock_connect:
                async def _set_connected(name):
                    mgr._conn_state[name] = "connected"

                mock_connect.side_effect = _set_connected
                result = await mgr.handle_reconnect("living_room")
            return result

        result = asyncio.run(run())
        old_client.disconnect.assert_called_once()
        # State cache should have been cleared before reconnect
        self.assertNotIn("living_room", mgr._state_cache)
        self.assertTrue(result["ok"])

    def test_reconnect_returns_error_on_failed_connect(self):
        """Returns ok=False when the connection attempt fails."""
        mgr = self._disconnected_manager()

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()):
                # _connect leaves state as 'disconnected' (simulates failure)
                result = await mgr.handle_reconnect("living_room")
            return result

        result = asyncio.run(run())
        self.assertFalse(result["ok"])
        self.assertIn("living_room", result["error"])

    def test_reconnect_all(self):
        """device='all' attempts reconnect on every configured device."""
        mgr = self._disconnected_manager()

        async def run():
            with patch.object(mgr, "_connect", new=AsyncMock()) as mock_connect:
                async def _set_connected(name):
                    mgr._conn_state[name] = "connected"

                mock_connect.side_effect = _set_connected
                result = await mgr.handle_reconnect("all")
            return result, mock_connect

        result, mock_connect = asyncio.run(run())
        self.assertTrue(result["ok"])
        self.assertEqual(mock_connect.call_count, 2)
        self.assertIn("living_room", result["result"])
        self.assertIn("bedroom", result["result"])


# ---------------------------------------------------------------------------
# WebServer HTTP routes
# ---------------------------------------------------------------------------


class _FakeWriter:
    """Minimal async writer mock that captures bytes written to it.

    Used by TestWebServerRoutes to inspect HTTP responses without
    requiring a real TCP connection.
    """

    def __init__(self):
        self._buf = b""
        self.closed = False

    def write(self, data: bytes):
        self._buf += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass

    def status_code(self) -> int:
        """Parse the HTTP response status code from the buffer."""
        first = self._buf[: self._buf.index(b"\r\n")]
        return int(first.split()[1])

    def response_body(self) -> dict:
        """Parse the JSON body from the HTTP response buffer."""
        sep = self._buf.index(b"\r\n\r\n")
        return json.loads(self._buf[sep + 4 :].decode("utf-8"))

    def raw_body(self) -> bytes:
        """Return the raw (non-decoded) response body bytes."""
        sep = self._buf.index(b"\r\n\r\n")
        return self._buf[sep + 4 :]

    def content_type(self) -> str:
        """Return the Content-Type header value."""
        header_block = self._buf[: self._buf.index(b"\r\n\r\n")].decode()
        for line in header_block.split("\r\n"):
            if line.lower().startswith("content-type:"):
                return line.split(":", 1)[1].strip()
        return ""


def _make_web_server():
    """Build a WebServer with a pre-populated DeviceManager for route tests."""
    mgr = _make_manager()
    mgr._conn_state = {"living_room": "connected", "bedroom": "disconnected"}
    mgr._entity_info = {
        "living_room": {"key": 1, "type": "light"},
        "bedroom": {"key": 2, "type": "switch"},
    }
    return daemon.WebServer(mgr, "127.0.0.1", 7890), mgr


class TestWebServerRoutes(unittest.TestCase):
    """Test WebServer HTTP route dispatch via _route() with a fake writer."""

    def test_get_root_returns_html(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/", b""))
        self.assertEqual(w.status_code(), 200)
        self.assertIn("text/html", w.content_type())
        self.assertIn(b"ESPHome Lights", w.raw_body())

    def test_get_favicon_returns_204(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/favicon.ico", b""))
        self.assertEqual(w.status_code(), 204)

    def test_get_api_list(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/api/list", b""))
        self.assertEqual(w.status_code(), 200)
        body = w.response_body()
        self.assertTrue(body["ok"])
        self.assertIn("living_room", body["result"])

    def test_get_api_status(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/api/status", b""))
        self.assertEqual(w.status_code(), 200)
        self.assertTrue(w.response_body()["ok"])

    def test_get_api_ping(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/api/ping", b""))
        self.assertEqual(w.status_code(), 200)
        self.assertEqual(w.response_body()["result"], "pong")

    def test_post_api_set_on(self):
        ws, mgr = _make_web_server()
        mgr._clients = {"living_room": MagicMock(), "bedroom": MagicMock()}
        w = _FakeWriter()
        payload = json.dumps({"device": "living_room", "action": "on"}).encode()
        asyncio.run(ws._route(w, "POST", "/api/set", payload))
        self.assertEqual(w.status_code(), 200)
        self.assertTrue(w.response_body()["ok"])

    def test_post_api_set_missing_device(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "POST", "/api/set", json.dumps({"action": "on"}).encode()))
        self.assertEqual(w.status_code(), 400)
        body = w.response_body()
        self.assertFalse(body["ok"])
        self.assertIn("device", body["error"].lower())

    def test_post_api_set_missing_action(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "POST", "/api/set", json.dumps({"device": "living_room"}).encode()))
        self.assertEqual(w.status_code(), 400)
        body = w.response_body()
        self.assertFalse(body["ok"])
        self.assertIn("action", body["error"].lower())

    def test_post_api_set_invalid_json(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "POST", "/api/set", b"not valid json"))
        self.assertEqual(w.status_code(), 400)
        self.assertFalse(w.response_body()["ok"])

    def test_post_api_set_device_error_returns_400(self):
        """A set command that fails (e.g. device not connected) returns HTTP 400."""
        ws, _ = _make_web_server()
        w = _FakeWriter()
        payload = json.dumps({"device": "bedroom", "action": "brightness", "value": "128"}).encode()
        asyncio.run(ws._route(w, "POST", "/api/set", payload))
        # bedroom is disconnected so the command fails -> HTTP 400
        self.assertEqual(w.status_code(), 400)
        self.assertFalse(w.response_body()["ok"])

    def test_post_api_reconnect_named_device(self):
        ws, mgr = _make_web_server()
        w = _FakeWriter()
        payload = json.dumps({"device": "living_room"}).encode()
        with patch.object(
            mgr, "handle_reconnect",
            new=AsyncMock(return_value={"ok": True, "result": "Reconnected to living_room"}),
        ) as mock_reconnect:
            asyncio.run(ws._route(w, "POST", "/api/reconnect", payload))
        mock_reconnect.assert_called_once_with("living_room")
        self.assertEqual(w.status_code(), 200)

    def test_post_api_reconnect_defaults_to_all(self):
        """reconnect with empty body defaults device to 'all'."""
        ws, mgr = _make_web_server()
        w = _FakeWriter()
        with patch.object(
            mgr, "handle_reconnect",
            new=AsyncMock(return_value={"ok": True, "result": "..."}),
        ) as mock_reconnect:
            asyncio.run(ws._route(w, "POST", "/api/reconnect", b"{}"))
        mock_reconnect.assert_called_once_with("all")

    def test_post_api_reload(self):
        ws, mgr = _make_web_server()
        w = _FakeWriter()
        _fake_devices = {
            "living_room": {"host": "10.0.0.1", "port": 6053, "encryption_key": "abc"}
        }
        with patch.object(daemon, "load_env"), \
             patch.object(daemon, "load_devices", return_value=_fake_devices), \
             patch.object(mgr, "handle_reload",
                          new=AsyncMock(return_value={"ok": True, "result": "0 added"})):
            asyncio.run(ws._route(w, "POST", "/api/reload", b""))
        self.assertEqual(w.status_code(), 200)
        self.assertTrue(w.response_body()["ok"])

    def test_unknown_path_returns_404(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/does/not/exist", b""))
        self.assertEqual(w.status_code(), 404)
        self.assertFalse(w.response_body()["ok"])

    def test_unknown_method_returns_405(self):
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "DELETE", "/api/list", b""))
        self.assertEqual(w.status_code(), 405)
        self.assertFalse(w.response_body()["ok"])

    def test_query_string_stripped(self):
        """Query strings are stripped so the path still matches cleanly."""
        ws, _ = _make_web_server()
        w = _FakeWriter()
        asyncio.run(ws._route(w, "GET", "/api/ping?foo=bar", b""))
        self.assertEqual(w.status_code(), 200)
        self.assertEqual(w.response_body()["result"], "pong")

    def test_max_body_constant_is_reasonable(self):
        """_MAX_BODY should cap at 64 KB to guard against oversized bodies."""
        self.assertGreater(daemon.WebServer._MAX_BODY, 0)
        self.assertLessEqual(daemon.WebServer._MAX_BODY, 65_536)

    def test_web_ui_contains_version(self):
        """The inline HTML should embed the daemon version string."""
        self.assertIn(daemon._DAEMON_VERSION.encode(), daemon._WEB_UI_BYTES)

    def test_web_server_env_vars_excluded_from_devices(self):
        """ESPHOME_LIGHTS_WEB_PORT and WEB_BIND are not parsed as device entries."""
        env = {
            "ESPHOME_LIGHTS_WEB_PORT": "7890",
            "ESPHOME_LIGHTS_WEB_BIND": "0.0.0.0",
            "ESPHOME_LIGHTS_REAL": "1.2.3.4:6053|k",
        }
        with patch.dict(os.environ, env, clear=True):
            devices = daemon.load_devices()
        self.assertNotIn("web_port", devices)
        self.assertNotIn("web_bind", devices)
        self.assertIn("real", devices)


# ---------------------------------------------------------------------------
# SSE subscriber push mechanism
# ---------------------------------------------------------------------------


class TestSSESubscribers(unittest.TestCase):
    """Test that state changes are pushed to registered SSE subscriber queues."""

    def test_state_change_notifies_subscriber(self):
        """A state update should place a status snapshot on the subscriber queue."""
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        mgr._conn_state = {"living_room": "connected", "bedroom": "disconnected"}

        q = asyncio.Queue()
        mgr._sse_subscribers.append(q)

        state = _fake_light_state(key=1, state=True, brightness=0.5)
        mgr._handle_state("living_room", state)

        self.assertFalse(q.empty())
        item = q.get_nowait()
        self.assertTrue(item["ok"])
        self.assertIn("living_room", item["result"])

    def test_no_subscribers_no_error(self):
        """State change with no SSE subscribers should not raise."""
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        state = _fake_light_state(key=1, state=True)
        # Should not raise; _sse_subscribers is empty
        mgr._handle_state("living_room", state)
        self.assertEqual(mgr._sse_subscribers, [])

    def test_multiple_subscribers_all_notified(self):
        """Every registered subscriber queue receives the update."""
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        mgr._conn_state = {"living_room": "connected"}

        q1, q2, q3 = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
        mgr._sse_subscribers.extend([q1, q2, q3])

        state = _fake_light_state(key=1, state=False)
        mgr._handle_state("living_room", state)

        self.assertFalse(q1.empty())
        self.assertFalse(q2.empty())
        self.assertFalse(q3.empty())

    def test_subscriber_receives_full_status_snapshot(self):
        """The pushed item is a full handle_status() snapshot (ok+result)."""
        mgr = _make_manager()
        mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        mgr._conn_state = {"living_room": "connected", "bedroom": "disconnected"}

        q = asyncio.Queue()
        mgr._sse_subscribers.append(q)

        state = _fake_light_state(key=1, state=True, brightness=1.0)
        mgr._handle_state("living_room", state)

        item = q.get_nowait()
        # Must be in the same shape as handle_status() output
        self.assertIn("ok", item)
        self.assertIn("result", item)
        self.assertIn("living_room", item["result"])
        self.assertIn("bedroom", item["result"])

    def test_switch_state_change_notifies_subscriber(self):
        """Switch state changes should also push to SSE subscribers."""
        mgr = _make_manager()
        mgr._entity_info = {"bedroom": {"key": 2, "type": "switch"}}
        mgr._conn_state = {"bedroom": "connected", "living_room": "disconnected"}

        q = asyncio.Queue()
        mgr._sse_subscribers.append(q)

        state = _fake_switch_state(key=2, state=True)
        mgr._handle_state("bedroom", state)

        self.assertFalse(q.empty())


if __name__ == "__main__":
    unittest.main()
