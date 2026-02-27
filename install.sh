#!/usr/bin/env bash
# =============================================================================
# install.sh — ESPHome Lights user-level installer
# =============================================================================
#
# Installs the ESPHome Lights daemon and CLI as a systemd user service.
# Must NOT be run as root.
#
# One-line install:
#   bash <(curl -fsSL https://raw.githubusercontent.com/platima/ESPHome-Python/main/install.sh)
#   bash <(wget -qO- https://raw.githubusercontent.com/platima/ESPHome-Python/main/install.sh)
#
# Or, if you have already cloned the repo:
#   bash install.sh
#
# Install layout:
#   Scripts:   ~/.local/lib/esphome-lights/
#   Binaries:  ~/.local/bin/esphome-lights  (symlinks)
#              ~/.local/bin/esphome-lightsd
#   Config:    ~/.config/esphome-lights/env  (or ~/.openclaw/workspace/.env)
#   Socket:    $XDG_RUNTIME_DIR/esphome-lights.sock
#   Service:   ~/.config/systemd/user/esphome-lightsd.service
# =============================================================================

set -euo pipefail

REPO_URL="https://github.com/platima/ESPHome-Python.git"
INSTALL_LIB="$HOME/.local/lib/esphome-lights"
INSTALL_BIN="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/esphome-lights"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="esphome-lightsd.service"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '\033[1;34m[*]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

# Prompt y/n — defaults to 'n' if stdin is not a terminal (non-interactive).
ask_yn() {
    local prompt="$1" default="${2:-n}"
    if [[ ! -t 0 ]]; then
        # Non-interactive: use the default quietly
        [[ "$default" == "y" ]] && return 0 || return 1
    fi
    local yn
    [[ "$default" == "y" ]] && prompt="$prompt [Y/n] " || prompt="$prompt [y/N] "
    read -rp "$prompt" yn
    yn="${yn:-$default}"
    [[ "${yn,,}" == "y" ]]
}

# ---------------------------------------------------------------------------
# Helpers (config template)
# ---------------------------------------------------------------------------

_create_env_template() {
    cat > "$ENV_FILE" << 'EOF'
# ESPHome Lights - device configuration
# Format: ESPHOME_LIGHTS_<LOCATION>="<host>:<port>|<encryption_key>"
#
# - LOCATION is uppercased in the variable name, lowercased in CLI commands.
# - Port is usually 6053 (the native ESPHome API port).
# - The encryption key is the Noise PSK shown in your ESPHome device config.
#
# Add one line per device. Examples:
#
# ESPHOME_LIGHTS_LIVING_ROOM="10.42.40.55:6053|J+YkHH7XC+4dQwWvPoF5kaz7tP4NY4HJNTL0QyZM1Rg="
# ESPHOME_LIGHTS_BEDROOM="10.42.40.56:6053|another_key_here="
EOF
    ok "Template created at $ENV_FILE"
    warn "Edit $ENV_FILE to add your devices before starting the daemon."
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

echo
info "ESPHome Lights installer"
echo

# 1. Refuse to run as root
[[ $EUID -ne 0 ]] || die "Do not run this installer as root or with sudo."

# 2. systemd --user must be available
systemctl --user status > /dev/null 2>&1 \
    || die "systemd user session not available. Is systemd running?\n  Hint: try 'systemctl --user status' to diagnose."

# 3. Python 3.11+
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" > /dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done
[[ -n "$PYTHON" ]] || die "Python 3.11+ not found. Please install Python before running this installer."
info "Using Python: $PYTHON  ($($PYTHON --version 2>&1))"

# ---------------------------------------------------------------------------
# Locate source files (local repo or clone)
# ---------------------------------------------------------------------------

# When piped via curl/wget, BASH_SOURCE[0] is empty or /dev/stdin.
SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
fi

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/esphome-lightsd.py" ]]; then
    SOURCE_DIR="$SCRIPT_DIR"
    info "Installing from local directory: $SOURCE_DIR"
else
    command -v git > /dev/null 2>&1 \
        || die "git not found. Install git, or clone the repo manually and run install.sh from within it."
    info "Cloning repository..."
    TMP_DIR="$(mktemp -d)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    git clone --depth 1 "$REPO_URL" "$TMP_DIR" \
        || die "Failed to clone $REPO_URL"
    SOURCE_DIR="$TMP_DIR"
    ok "Cloned to $TMP_DIR"
fi

# ---------------------------------------------------------------------------
# Install scripts
# ---------------------------------------------------------------------------

info "Installing scripts to $INSTALL_LIB ..."
mkdir -p "$INSTALL_LIB" "$INSTALL_BIN"
cp "$SOURCE_DIR/esphome-lights.py"  "$INSTALL_LIB/"
cp "$SOURCE_DIR/esphome-lightsd.py" "$INSTALL_LIB/"
cp "$SOURCE_DIR/SKILL.md"           "$INSTALL_LIB/"
chmod +x "$INSTALL_LIB/esphome-lights.py" "$INSTALL_LIB/esphome-lightsd.py"

# Symlinks in ~/.local/bin so the commands are on PATH
ln -sf "$INSTALL_LIB/esphome-lights.py"  "$INSTALL_BIN/esphome-lights"
ln -sf "$INSTALL_LIB/esphome-lightsd.py" "$INSTALL_BIN/esphome-lightsd"
ok "Scripts installed."

# Warn if ~/.local/bin is not on PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    warn "$HOME/.local/bin is not in your PATH."
    warn "Add this line to your shell profile (~/.profile, ~/.bashrc, etc.):"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------

if ! "$PYTHON" -c "import aioesphomeapi" 2>/dev/null; then
    info "Installing aioesphomeapi ..."
    "$PYTHON" -m pip install --user aioesphomeapi \
        || die "pip install failed. Try manually: $PYTHON -m pip install --user aioesphomeapi"
    # Reinstall noiseprotocol in case the 'noise' Perlin package overwrote it.
    # See: https://github.com/platima/ESPHome-Python#requirements
    "$PYTHON" -m pip install --user --force-reinstall noiseprotocol
    ok "aioesphomeapi installed."
else
    ok "aioesphomeapi already installed."
fi

# ---------------------------------------------------------------------------
# Device config / env file
# ---------------------------------------------------------------------------

mkdir -p "$CONFIG_DIR"
ENV_FILE="$CONFIG_DIR/env"
OPENCLAW_ENV="$HOME/.openclaw/workspace/.env"

if [[ -f "$OPENCLAW_ENV" ]]; then
    info "OpenClaw workspace .env detected at $OPENCLAW_ENV"
    info "This file will be loaded automatically with highest priority."
    info "ESPHome device vars (ESPHOME_LIGHTS_*) can live there or in $ENV_FILE"
fi

if [[ -f "$ENV_FILE" ]]; then
    ok "Config file exists: $ENV_FILE"
else
    if [[ -f "$OPENCLAW_ENV" ]]; then
        warn "No $ENV_FILE found (OpenClaw .env will still be used)."
        if ask_yn "Create $ENV_FILE for ESPHome-specific vars anyway?" "n"; then
            _create_env_template
        fi
    else
        warn "No config file found at $ENV_FILE"
        if ask_yn "Create a template config file now?" "y"; then
            _create_env_template
        else
            warn "Skipped. Create $ENV_FILE manually before starting the daemon."
        fi
    fi
fi

# ---------------------------------------------------------------------------
# systemd user service
# ---------------------------------------------------------------------------

info "Installing systemd user service ..."
mkdir -p "$SERVICE_DIR"

# Socket lives under $XDG_RUNTIME_DIR (tmpfs, auto-created by systemd per-user).
# In the service file, %t expands to $XDG_RUNTIME_DIR at runtime.
SOCKET_PATH_DISPLAY="\$XDG_RUNTIME_DIR/esphome-lights.sock"

cat > "$SERVICE_DIR/$SERVICE_NAME" << EOF
[Unit]
Description=ESPHome Lights Daemon
Documentation=https://github.com/platima/ESPHome-Python
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PYTHON $INSTALL_LIB/esphome-lightsd.py
WorkingDirectory=$INSTALL_LIB
Environment=ESPHOME_LIGHTS_SOCKET=%t/esphome-lights.sock
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME" \
    && ok "Service enabled: $SERVICE_NAME" \
    || warn "Could not enable service automatically. Enable manually: systemctl --user enable $SERVICE_NAME"

# Enable linger: keeps the user session alive at boot so the service
# starts without requiring the user to be logged in.
if command -v loginctl > /dev/null 2>&1; then
    if loginctl enable-linger "$USER" 2>/dev/null; then
        ok "loginctl linger enabled (service will start at boot even when not logged in)."
    else
        warn "Could not enable loginctl linger. Service will only run while you are logged in."
        warn "Enable manually: loginctl enable-linger $USER"
    fi
fi

# ---------------------------------------------------------------------------
# OpenClaw skill (optional)
# ---------------------------------------------------------------------------

OPENCLAW_DIR="$HOME/.openclaw"
OPENCLAW_SKILLS_DIR="$OPENCLAW_DIR/skills"

if [[ -d "$OPENCLAW_DIR" ]]; then
    info "OpenClaw detected at ~/.openclaw"
    SKILL_LINK="$OPENCLAW_SKILLS_DIR/esphome-lights"
    if [[ -e "$SKILL_LINK" ]]; then
        # Re-create the symlink in case the target has moved, then confirm update.
        ln -sf "$INSTALL_LIB" "$SKILL_LINK"
        ok "OpenClaw skill updated at $SKILL_LINK"
    else
        if ask_yn "Install ESPHome Lights as an OpenClaw skill?" "y"; then
            mkdir -p "$OPENCLAW_SKILLS_DIR"
            ln -s "$INSTALL_LIB" "$SKILL_LINK"
            ok "OpenClaw skill linked: $SKILL_LINK -> $INSTALL_LIB"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo
ok "Installation complete!"
echo
info "Next steps:"
echo "  1. Edit your device config:    $ENV_FILE"
echo "     (or add ESPHOME_LIGHTS_* vars to ~/.openclaw/workspace/.env)"
echo "  2. Start the daemon:           systemctl --user start $SERVICE_NAME"
echo "  3. Check daemon status:        systemctl --user status $SERVICE_NAME"
echo "  4. List configured devices:    esphome-lights --list"
echo "  5. Check device states:        esphome-lights --status"
echo
info "After editing config, reload without restarting:"
echo "  esphome-lights --reload"
echo "  (or: systemctl --user kill -s HUP $SERVICE_NAME)"
echo
info "To view daemon logs:  journalctl --user -u $SERVICE_NAME -f"
info "To update:            re-run this installer"
echo
