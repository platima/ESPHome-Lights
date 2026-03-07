---
name: esphome-lights
description: Control ESPHome smart lights and switches - on/off, brightness, RGB colour, and status queries
metadata:
  {
    "openclaw": {
      "requires": {
        "bins": ["bash", "python3"],
      },
      "user-invocable": true
    }
  }
---

# ESPHome Lights

Control ESPHome smart lights and switches via the native ESPHome API. The
`esphome-lights` shell CLI sends commands directly to the daemon socket via
`socat` or `nc` for sub-10ms response times on ARM targets. Python is used
only for `--list`/`--status` output formatting.

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

### Turn a light/switch on

```bash
esphome-lights --device <device> --on
```

Use `all` to turn every device on at once:

```bash
esphome-lights --device all --on
```

### Turn a light/switch off

```bash
esphome-lights --device <device> --off
```

Use `all` to turn every device off at once:

```bash
esphome-lights --device all --off
```

### Set brightness (0-255)

```bash
esphome-lights --device <device> --brightness <value>
```

Only works for light entities. Returns an error for switch-type devices
(smart plugs).

### Set RGB colour (r,g,b - each 0-255)

```bash
esphome-lights --device <device> --rgb <r>,<g>,<b>
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
- Use `all` as the device name to broadcast `--on`/`--off` to every device.
- Use `--debug` flag for detailed JSON output from the daemon.
- Use `--bg` flag to fire and forget (return immediately without waiting).
