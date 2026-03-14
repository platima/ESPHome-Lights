---
name: esphome-lights
description: Control ESPHome smart lights and switches on the local network - on/off, brightness, RGB colour, and status queries
homepage: https://github.com/platima/ESPHome-Lights
metadata:
  clawdbot:
    emoji: "💡"
    requires:
      bins: ["bash", "python3"]
      env: ["ESPHOME_LIGHTS_LOCATION"]
    primaryEnv: "ESPHOME_LIGHTS_LOCATION"
    files: ["esphome-lights", "esphome-lights.py", "esphome-lightsd.py"]
    user-invocable: true
---

# ESPHome Lights

Control ESPHome smart lights and switches via the native ESPHome API. The
`esphome-lights` shell CLI sends commands directly to the daemon socket via
`socat` or `nc` for sub-10ms response times on ARM targets. Python is used
only for `--list`/`--status` output formatting.

## How to Invoke

The `esphome-lights` command is a **shell wrapper bundled in this skill
directory**. It is NOT a system binary — do not rely on it being in `$PATH`.

Always invoke it using the full path to the script within this skill's
directory. For example, if this skill is loaded from
`~/.openclaw/skills/esphome-lights/`, use:

```bash
bash ~/.openclaw/skills/esphome-lights/esphome-lights <args>
```

Or if this skill is in a workspace-specific location:

```bash
bash ~/.openclaw/workspace/skills/esphome-lights/esphome-lights <args>
```

**Important:** Always use the **shell wrapper** (`esphome-lights`, no `.py`
extension), not the Python script (`esphome-lights.py`). The shell wrapper
uses `socat`/`nc` for ~10ms response times; the Python script is ~15x slower
and is only needed internally for `--list`/`--status` formatting.

## External Endpoints

| Endpoint | Protocol | Data Sent | Purpose |
|----------|----------|-----------|---------|
| `<device-ip>:6053` (local network only) | ESPHome native API (Noise/protobuf, TCP) | Device commands (on/off/brightness/rgb) | Control ESPHome smart devices |

**No internet endpoints are called.** All communication is to ESPHome devices
on the local network. The device IP addresses and encryption keys are
configured by the user in `ESPHOME_LIGHTS_*` environment variables.

## Security & Privacy

- **All traffic is local-network only** — no data leaves the machine or LAN
- **Encryption keys never leave the host** — Noise PSKs are stored in env vars and used only for the ESPHome Noise protocol handshake with the device
- **No telemetry, no logging to external services**
- **Daemon socket** (`$XDG_RUNTIME_DIR/esphome-lights.sock`) is local Unix socket, permissions `0o660` (owner + group only)
- **Commands are fire-and-forget** — the daemon sends the command; no state is returned to the agent beyond success/failure

## Model Invocation Note

This skill is designed for autonomous invocation by an AI agent in response
to natural-language requests (e.g. "turn on the living room light"). The agent
uses the `exec` tool to run `esphome-lights` commands. No user confirmation is
required for individual light control commands. If you prefer to require
confirmation before any device command, disable autonomous tool use in your
OpenClaw agent settings.

## Trust Statement

This skill communicates exclusively with ESPHome devices on your **local
network** using credentials you configure. No data is sent to any third party,
cloud service, or external API. Only install this skill if your OpenClaw agent
has access to the local network where your ESPHome devices reside.

## Available Commands

Run these via the `exec` tool. In the examples below, `SKILL_DIR` represents
the path to this skill's directory (e.g. `~/.openclaw/skills/esphome-lights`).
Replace `<device>` with the location name (lowercased, e.g. `living_room`,
`bedroom`).

### List all configured devices

```bash
bash $SKILL_DIR/esphome-lights --list
```

Output: one line per device showing `location -> host:port  [connection-state] (entity-type)`.

### Show on/off state of all devices

```bash
bash $SKILL_DIR/esphome-lights --status
```

Output: one line per device showing `location  ON/OFF  (entity-type)`. For lights
that are ON, brightness (0-255) and RGB values are also shown.

### Turn a light/switch on

```bash
bash $SKILL_DIR/esphome-lights --device <device> --on
```

Use `all` to turn every device on at once:

```bash
bash $SKILL_DIR/esphome-lights --device all --on
```

### Turn a light/switch off

```bash
bash $SKILL_DIR/esphome-lights --device <device> --off
```

Use `all` to turn every device off at once:

```bash
bash $SKILL_DIR/esphome-lights --device all --off
```

### Set brightness (0-255)

```bash
bash $SKILL_DIR/esphome-lights --device <device> --brightness <value>
```

Only works for light entities. Returns an error for switch-type devices
(smart plugs).

### Set RGB colour (r,g,b - each 0-255)

```bash
bash $SKILL_DIR/esphome-lights --device <device> --rgb <r>,<g>,<b>
```

Only works for light entities. Returns an error for switch-type devices.

### Health check (daemon mode)

```bash
bash $SKILL_DIR/esphome-lights --ping
```

Returns `pong` if the daemon is running.

### Reload config (without restarting)

```bash
bash $SKILL_DIR/esphome-lights --reload
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
- **Do not call `esphome-lights.py` directly** — always use the shell wrapper
  (`esphome-lights`, no extension) for the fastest response times.
