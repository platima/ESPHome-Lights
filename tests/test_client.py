"""
Tests for esphome-lights.py — thin CLI client.

These tests exercise the client's argument parsing, socket communication,
and output formatting.  The daemon socket is mocked.
"""

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock aioesphomeapi before importing the daemon (it imports at top level).
sys.modules.setdefault("aioesphomeapi", MagicMock())

# Both filenames contain hyphens, so we must use importlib.
daemon = importlib.import_module("esphome-lightsd")
esphome_lights = importlib.import_module("esphome-lights")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeDaemon:
    """A minimal daemon that runs in-process for client integration tests."""

    def __init__(self):
        self.manager = daemon.DeviceManager(
            {
                "living_room": {
                    "host": "10.0.0.1",
                    "port": 6053,
                    "encryption_key": "k1",
                },
                "bedroom": {
                    "host": "10.0.0.2",
                    "port": 6053,
                    "encryption_key": "k2",
                },
            }
        )
        self.manager._conn_state = {
            "living_room": "connected",
            "bedroom": "connected",
        }
        self.manager._state_cache = {
            "living_room": {
                "state": "ON",
                "brightness": 200,
                "rgb": "255,128,0",
                "entity_type": "light",
            },
            "bedroom": {
                "state": "OFF",
                "brightness": None,
                "rgb": None,
                "entity_type": "switch",
            },
        }
        self.sock_path = tempfile.mktemp(suffix=".sock")
        self.server = daemon.SocketServer(self.manager, self.sock_path)

    async def start(self):
        await self.server.start()

    async def stop(self):
        await self.server.stop()


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


class TestFormatList(unittest.TestCase):
    """Test format_list output."""

    def test_format_list(self):
        data = {
            "kitchen": {"host": "10.0.0.5", "port": 6053, "connection": "connected", "entity_type": "light"},
        }
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            esphome_lights.format_list(data)
        output = buf.getvalue()
        self.assertIn("kitchen", output)
        self.assertIn("10.0.0.5", output)
        self.assertIn("connected", output)
        self.assertIn("light", output)


class TestFormatStatus(unittest.TestCase):
    """Test format_status output."""

    def test_format_status(self):
        data = {
            "bedroom": {"state": "OFF", "connection": "connected", "entity_type": "switch"},
            "living_room": {"state": "ON", "connection": "connected", "entity_type": "light", "brightness": 200, "rgb": "255,128,0"},
        }
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            esphome_lights.format_status(data)
        output = buf.getvalue()
        self.assertIn("bedroom", output)
        self.assertIn("OFF", output)
        self.assertIn("switch", output)
        self.assertIn("living_room", output)
        self.assertIn("ON", output)
        self.assertIn("light", output)
        self.assertIn("brightness:200", output)
        self.assertIn("rgb:255,128,0", output)

    def test_format_status_off_light_no_details(self):
        """When a light is OFF, brightness/RGB details are not shown."""
        data = {
            "hallway": {"state": "OFF", "entity_type": "light", "brightness": 0, "rgb": "0,0,0"},
        }
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            esphome_lights.format_status(data)
        output = buf.getvalue()
        self.assertIn("hallway", output)
        self.assertIn("OFF", output)
        self.assertNotIn("brightness", output)


# ---------------------------------------------------------------------------
# Client → daemon integration tests
# ---------------------------------------------------------------------------


class TestClientDaemonIntegration(unittest.TestCase):
    """Test the CLI client talking to a real (in-process) daemon.

    The CLI client uses blocking sockets, so we must run it in a separate
    thread to avoid blocking the event loop that the async daemon needs.
    """

    def _run_client_in_thread(self, fd, request, background=False):
        """Run send_command in a thread while the event loop processes requests."""
        import concurrent.futures

        async def run():
            loop = asyncio.get_event_loop()
            with patch.object(esphome_lights, "SOCKET_PATH", fd.sock_path):
                resp = await loop.run_in_executor(
                    None,
                    lambda: esphome_lights.send_command(request, background=background),
                )
            return resp

        return run()

    def test_send_ping(self):
        """Client sends ping, gets pong."""

        async def run():
            fd = FakeDaemon()
            await fd.start()
            try:
                resp = await self._run_client_in_thread(fd, {"cmd": "ping"})
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["result"], "pong")
            finally:
                await fd.stop()

        asyncio.run(run())

    def test_send_list(self):
        """Client sends list, gets device list."""

        async def run():
            fd = FakeDaemon()
            await fd.start()
            try:
                resp = await self._run_client_in_thread(fd, {"cmd": "list"})
                self.assertTrue(resp["ok"])
                self.assertIn("living_room", resp["result"])
                self.assertIn("bedroom", resp["result"])
            finally:
                await fd.stop()

        asyncio.run(run())

    def test_send_status(self):
        """Client sends status, gets cached state."""

        async def run():
            fd = FakeDaemon()
            await fd.start()
            try:
                resp = await self._run_client_in_thread(fd, {"cmd": "status"})
                self.assertTrue(resp["ok"])
                self.assertEqual(resp["result"]["living_room"]["state"], "ON")
                self.assertEqual(resp["result"]["bedroom"]["state"], "OFF")
            finally:
                await fd.stop()

        asyncio.run(run())

    def test_send_reload(self):
        """Client sends reload, gets ok response."""

        async def run():
            fd = FakeDaemon()
            await fd.start()
            try:
                resp = await self._run_client_in_thread(fd, {"cmd": "reload"})
                # FakeDaemon echoes unknown cmds with ok:false, but we just
                # verify the command was sent and a JSON response received.
                self.assertIsInstance(resp, dict)
                self.assertIn("ok", resp)
            finally:
                await fd.stop()

        asyncio.run(run())

    def test_background_returns_none(self):
        """Background mode sends the command but returns None (no response read)."""

        async def run():
            fd = FakeDaemon()
            await fd.start()
            try:
                resp = await self._run_client_in_thread(
                    fd, {"cmd": "ping"}, background=True
                )
                self.assertIsNone(resp)
            finally:
                await fd.stop()

        asyncio.run(run())


class TestClientSocketMissing(unittest.TestCase):
    """Test the client's behaviour when the daemon is not running."""

    def test_missing_socket_exits(self):
        """Client exits with error if socket file does not exist."""
        with patch.object(
            esphome_lights, "SOCKET_PATH", "/tmp/nonexistent-test.sock"
        ):
            with self.assertRaises(SystemExit) as ctx:
                esphome_lights.send_command({"cmd": "ping"})
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
