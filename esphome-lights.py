#!/usr/bin/env python3
"""
esphome-lights.py — Thin CLI client for the esphome-lightsd daemon

Communicates with the running daemon over a Unix domain socket using
newline-delimited JSON.  Uses only stdlib modules for fast startup
(no aioesphomeapi, no protobuf, no cryptography imports).

Usage:
  esphome-lights.py --list                               # List all devices
  esphome-lights.py --status                             # Show on/off state
  esphome-lights.py --device <id|all> --on               # Turn on
  esphome-lights.py --device <id|all> --off              # Turn off
  esphome-lights.py --device <id|all> --brightness N     # Set brightness (0-255)
  esphome-lights.py --device <id|all> --rgb r,g,b        # Set RGB colour
  esphome-lights.py --device <id|all> --color-temp N     # Set colour temperature (Kelvin)
  esphome-lights.py --device <id|all> --cwww C,W         # Set cold/warm white (0-255 each)
  esphome-lights.py --ping                               # Daemon health check
  esphome-lights.py --reload                             # Reload daemon config

  Use 'all' as the device name to send a command to every device at once.

Flags:
  --json               Output raw JSON (for --list and --status)
  --bg, --background   Fire and forget (return immediately)
  --debug              Show full JSON response from daemon
"""

import argparse
import json
import os
import socket
import sys

_xdg = os.environ.get("XDG_RUNTIME_DIR", "")
SOCKET_PATH = os.environ.get(
    "ESPHOME_LIGHTS_SOCKET",
    os.path.join(_xdg, "esphome-lights.sock") if _xdg else "/tmp/esphome-lights.sock",
)
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
        print(f"Error: cannot connect to daemon -- {exc}", file=sys.stderr)
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
        print(f"Error: invalid response from daemon -- {exc}", file=sys.stderr)
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

        # Show all available detail fields for light entities that are ON
        if etype == "light" and state == "ON":
            brightness = info.get("brightness")
            if brightness is not None:
                parts.append(f"  brightness:{brightness}")
            rgb = info.get("rgb")
            if rgb:
                parts.append(f"  rgb:{rgb}")
            color_temp = info.get("color_temp")
            if color_temp:
                parts.append(f"  color_temp:{color_temp}K")
            cold_white = info.get("cold_white")
            warm_white = info.get("warm_white")
            if cold_white is not None and warm_white is not None and (cold_white > 0 or warm_white > 0):
                parts.append(f"  cwww:{cold_white},{warm_white}")

        print("".join(parts))


def main():
    parser = argparse.ArgumentParser(
        description="Control ESPHome smart lights via the esphome-lightsd daemon"
    )
    parser.add_argument("--list", action="store_true", help="List all configured devices")
    parser.add_argument(
        "--status", action="store_true", help="Show on/off state of all devices"
    )
    parser.add_argument(
        "--device", metavar="DEVICE",
        help="Device name to control, or 'all' to target every device",
    )
    # --set is kept as a hidden backward-compatible alias for --device
    parser.add_argument("--set", metavar="DEVICE", dest="device", help=argparse.SUPPRESS)
    parser.add_argument("--on", action="store_true", help="Turn on")
    parser.add_argument("--off", action="store_true", help="Turn off")
    parser.add_argument("--brightness", type=str, help="Set brightness (0-255)")
    parser.add_argument("--rgb", type=str, help="Set RGB colour (r,g,b)")
    parser.add_argument("--color-temp", type=str, dest="color_temp", help="Set colour temperature in Kelvin (e.g. 2700)")
    parser.add_argument("--cwww", type=str, help="Set cold/warm white (cold,warm - each 0-255)")
    parser.add_argument("--ping", action="store_true", help="Daemon health check")
    parser.add_argument("--reload", action="store_true", help="Reload daemon configuration")
    parser.add_argument(
        "--reconnect",
        action="store_true",
        help="Cancel backoff and immediately reconnect a device (use with --device)",
    )
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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON (for --list and --status)",
    )

    args = parser.parse_args()

    # --debug overrides --bg
    background = args.bg and not args.debug

    if args.list:
        resp = send_command({"cmd": "list"})
        if resp and resp.get("ok"):
            if args.json:
                print(json.dumps(resp["result"], indent=2))
            else:
                format_list(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    elif args.status:
        resp = send_command({"cmd": "status"})
        if resp and resp.get("ok"):
            if args.json:
                print(json.dumps(resp["result"], indent=2))
            else:
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

    elif args.reconnect:
        device = (args.device or "all").lower()
        resp = send_command({"cmd": "reconnect", "device": device})
        if resp and resp.get("ok"):
            print(resp["result"])
        elif resp:
            print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
            sys.exit(1)

    elif args.device:
        device = args.device.lower()

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
        elif args.color_temp:
            request = {
                "cmd": "set",
                "device": device,
                "action": "color_temp",
                "value": args.color_temp,
            }
        elif args.cwww:
            request = {
                "cmd": "set",
                "device": device,
                "action": "cwww",
                "value": args.cwww,
            }
        else:
            print(
                "Error: --device requires --on, --off, --brightness, --rgb, --color-temp, or --cwww",
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
