---
name: esphome-lights
description: Control ESPHome smart lights and switches - on/off, brightness, RGB colour, and status queries
metadata:
  {
    "openclaw": {
      "requires": {
        "bins": ["python3"],
        "config": ["tools.exec.enabled"]
      },
      "user-invocable": true
    }
  }
---

# ESPHome Lights

Control ESPHome smart lights and switches via the native ESPHome API. Uses the
`esphome-lights.py` CLI tool which communicates over the ESPHome native protocol
(port 6053, Noise encryption).

## Available Commands

Run these via the `exec` tool. Replace `<device>` with the location name
(lowercased, e.g. `living_room`, `bedroom`).

### List all configured devices

```bash
esphome-lights --list
```

Output: one line per device showing `location -> host:port  [connection-state] (entity-type)`.

### Show on/off state of all devices

```bash
esphome-lights --status
```

Output: one line per device showing `location  ON/OFF  (entity-type)`. For lights
that are ON, brightness (0-255) and RGB values are also shown.

### Turn a light on

```bash
esphome-lights --set <device> --on
```

### Turn a light off

```bash
esphome-lights --set <device> --off
```

### Set brightness (0-255)

```bash
esphome-lights --set <device> --brightness <value>
```

Only works for light entities. Returns an error for switch-type devices
(smart plugs).

### Set RGB colour (r,g,b - each 0-255)

```bash
esphome-lights --set <device> --rgb <r>,<g>,<b>
```

Only works for light entities. Returns an error for switch-type devices.

### Health check (daemon mode)

```bash
esphome-lights --ping
```

Returns `pong` if the daemon is running.

### Reload config (without restarting)

```bash
esphome-lights --reload
```

Instructs the daemon to re-read all env files and reconnect any devices that
were added, removed, or changed. Returns a summary such as
`added: 0, removed: 0, changed: 1, unchanged: 1`.

## Notes

- Device names come from environment variables: `ESPHOME_LIGHTS_<LOCATION>`.
  The location part is lowercased for use in commands (e.g.
  `ESPHOME_LIGHTS_LIVING_ROOM` → `living_room`).
- If a command fails, check `--list` output for valid device names.
- Use `--debug` flag for detailed JSON output from the daemon.
- Use `--bg` flag to fire and forget (return immediately without waiting).
