"""
Tests for esphome-lightsd.py — daemon logic.

These tests exercise the DeviceManager command handlers, SocketServer
dispatch, config loading, and the JSON protocol without requiring real
ESPHome devices.  Network-level connection behaviour is tested via mocks.
"""

import asyncio
import importlib
import json
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
            "ESPHOME_LIGHTS_REAL": "1.2.3.4:6053|k",
        }
        with patch.dict(os.environ, env, clear=True):
            devices = daemon.load_devices()
        self.assertNotIn("socket", devices)
        self.assertNotIn("log_level", devices)
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
        result = mgr.handle_list()
        self.assertTrue(result["ok"])
        self.assertIn("living_room", result["result"])
        self.assertIn("bedroom", result["result"])
        self.assertEqual(result["result"]["living_room"]["connection"], "connected")

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
        result = self.server._dispatch({"cmd": "list"})
        self.assertTrue(result["ok"])

    def test_dispatch_status(self):
        self.mgr._conn_state = {"living_room": "connected", "bedroom": "connected"}
        result = self.server._dispatch({"cmd": "status"})
        self.assertTrue(result["ok"])

    def test_dispatch_ping(self):
        result = self.server._dispatch({"cmd": "ping"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "pong")

    def test_dispatch_set_valid(self):
        self.mgr._conn_state = {"living_room": "connected"}
        self.mgr._clients = {"living_room": MagicMock()}
        self.mgr._entity_info = {"living_room": {"key": 1, "type": "light"}}
        result = self.server._dispatch(
            {"cmd": "set", "device": "living_room", "action": "on"}
        )
        self.assertTrue(result["ok"])

    def test_dispatch_set_missing_device(self):
        result = self.server._dispatch({"cmd": "set", "action": "on"})
        self.assertFalse(result["ok"])
        self.assertIn("device", result["error"].lower())

    def test_dispatch_set_missing_action(self):
        result = self.server._dispatch({"cmd": "set", "device": "living_room"})
        self.assertFalse(result["ok"])
        self.assertIn("action", result["error"].lower())

    def test_dispatch_missing_cmd(self):
        result = self.server._dispatch({})
        self.assertFalse(result["ok"])
        self.assertIn("cmd", result["error"].lower())

    def test_dispatch_unknown_cmd(self):
        result = self.server._dispatch({"cmd": "explode"})
        self.assertFalse(result["ok"])
        self.assertIn("unknown", result["error"].lower())


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


if __name__ == "__main__":
    unittest.main()
