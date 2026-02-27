#!/usr/bin/env python3
"""
esphome-lights.py — Thin CLI client for the esphome-lightsd daemon

Communicates with the running daemon over a Unix domain socket using
newline-delimited JSON.  Uses only stdlib modules for fast startup
(no aioesphomeapi, no protobuf, no cryptography imports).

Usage:
  esphome-lights.py --list                          # List all lights
  esphome-lights.py --status                        # Show on/off state
  esphome-lights.py --set <light-id> --on           # Turn on
  esphome-lights.py --set <light-id> --off          # Turn off
  esphome-lights.py --set <light-id> --brightness N # Set brightness (0-255)
  esphome-lights.py --set <light-id> --rgb r,g,b    # Set RGB colour
  esphome-lights.py --ping                          # Daemon health check
  esphome-lights.py --reload                        # Reload daemon config

Flags:
  --bg, --background   Fire and forget (return immediately)
  --debug              Show full JSON response from daemon
"""

import argparse
import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("ESPHOME_LIGHTS_SOCKET", "/tmp/esphome-lights.sock")
SOCKET_TIMEOUT = 5  # seconds


def send_command(request: dict, background: bool = False) -> dict | None:
    """Send a JSON command to the daemon and return the parsed response."""
    if not os.path.exists(SOCKET_PATH):
        print(
            "Error: esphome-lightsd is not running (socket not found)",
            file=sys.stderr,
        )
        sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(SOCKET_TIMEOUT)

    try:
        sock.connect(SOCKET_PATH)
    except (ConnectionRefusedError, OSError) as exc:
        print(f"Error: cannot connect to daemon — {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        payload = json.dumps(request) + "\n"
        sock.sendall(payload.encode("utf-8"))

        if background:
            return None

        # Read the response (newline-delimited)
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk

        if not buf:
            print("Error: empty response from daemon", file=sys.stderr)
            sys.exit(1)

        return json.loads(buf.decode("utf-8").strip())

    except socket.timeout:
        print("Error: daemon did not respond within 5 seconds", file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Error: invalid response from daemon — {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        sock.close()


def format_list(result: dict):
    """Print device list with connection state and entity type."""
    print("Configured ESPHome devices:")
    for name, info in sorted(result.items()):
        host = info.get("host", "?")
        port = info.get("port", "?")
        conn = info.get("connection", "unknown")
        etype = info.get("entity_type") or "unknown"
        print(f"  {name:20} -> {host}:{port}  [{conn}] ({etype})")


def format_status(result: dict):
    """Print device status with entity type and light details."""
    for name, info in sorted(result.items()):
        state = info.get("state", "unknown")
        etype = info.get("entity_type") or "unknown"
        parts = [f"  {name:20} {state:4}  ({etype})"]

        # Show brightness and RGB details for light entities
        if etype == "light" and state == "ON":
            brightness = info.get("brightness")
            if brightness is not None:
                parts.append(f"  brightness:{brightness}")
            rgb = info.get("rgb")
            if rgb:
                parts.append(f"  rgb:{rgb}")

        print("".join(parts))


def main():
    parser = argparse.ArgumentParser(
        description="Control ESPHome smart lights via the esphome-lightsd daemon"
    )
    parser.add_argument("--list", action="store_true", help="List all configured lights")
    parser.add_argument(
        "--status", action="store_true", help="Show on/off state of all lights"
    )
    parser.add_argument("--set", metavar="DEVICE", help="Light ID to control")
    parser.add_argument("--on", action="store_true", help="Turn on")
    parser.add_argument("--off", action="store_true", help="Turn off")
    parser.add_argument("--brightness", type=str, help="Set brightness (0-255)")
    parser.add_argument("--rgb", type=str, help="Set RGB colour (r,g,b)")
    parser.add_argument("--ping", action="store_true", help="Daemon health check")
    parser.add_argument("--reload", action="store_true", help="Reload daemon configuration")
    parser.add_argument(
        "--bg",
        "--background",
        action="store_true",
        help="Fire and forget (return immediately)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Show full JSON response (overrides --bg)",
    )

    args = parser.parse_args()

    # --debug overrides --bg
    background = args.bg and not args.debug

    if args.list:
        resp = send_command({"cmd": "list"})
        if resp and resp.get("ok"):
            format_list(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    elif args.status:
        resp = send_command({"cmd": "status"})
        if resp and resp.get("ok"):
            format_status(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    elif args.ping:
        resp = send_command({"cmd": "ping"})
        if resp and resp.get("ok"):
            print(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    elif args.reload:
        resp = send_command({"cmd": "reload"})
        if resp and resp.get("ok"):
            print(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    elif args.set:
        device = args.set.lower()

        if args.on:
            request = {"cmd": "set", "device": device, "action": "on"}
        elif args.off:
            request = {"cmd": "set", "device": device, "action": "off"}
        elif args.brightness:
            request = {
                "cmd": "set",
                "device": device,
                "action": "brightness",
                "value": args.brightness,
            }
        elif args.rgb:
            request = {
                "cmd": "set",
                "device": device,
                "action": "rgb",
                "value": args.rgb,
            }
        else:
            print(
                "Error: --set requires --on, --off, --brightness, or --rgb",
                file=sys.stderr,
            )
            sys.exit(1)

        resp = send_command(request, background=background)

        if background:
            print("Command sent")
        elif args.debug:
            print(json.dumps(resp, indent=2))
        elif resp and resp.get("ok"):
            print(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
