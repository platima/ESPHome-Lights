#!/usr/bin/env bash
# =============================================================================
# install.sh — ESPHome Lights user-level installer
# =============================================================================
#
# Installs the ESPHome Lights daemon and CLI as a systemd user service.
# Must NOT be run as root.
#
# One-line install:
#   bash <(curl -fsSL https://raw.githubusercontent.com/platima/ESPHome-Lights/main/install.sh)
#   bash <(wget -qO- https://raw.githubusercontent.com/platima/ESPHome-Lights/main/install.sh)
#
# Or, if you have already cloned the repo:
#   bash install.sh
#
# Uninstall:
#   bash install.sh --uninstall
#   bash <(curl -fsSL https://raw.githubusercontent.com/platima/ESPHome-Lights/main/install.sh) --uninstall
#
# Install layout:
#   Scripts:   ~/.local/lib/esphome-lights/
#   Venv:      ~/.local/lib/esphome-lights/venv  (Python 3.11, daemon only)
#   Binaries:  ~/.local/bin/esphome-lights  (symlinks)
#              ~/.local/bin/esphome-lightsd
#   Config:    ~/.config/esphome-lights/env  (or ~/.openclaw/workspace/.env)
#   Socket:    $XDG_RUNTIME_DIR/esphome-lights.sock
#   Service:   ~/.config/systemd/user/esphome-lightsd.service
# =============================================================================

set -euo pipefail

UNINSTALL=0
FAST=0
for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        --fast)      FAST=1 ;;
        -h|--help)
            echo "Usage: bash install.sh [--uninstall] [--fast]"
            echo "  (no args)    Install / update ESPHome Lights (interactive)"
            echo "  --uninstall  Remove ESPHome Lights from this system"
            echo "  --fast       Non-interactive: accept all safe defaults"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

REPO_URL="https://github.com/platima/ESPHome-Lights.git"
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

# Prompt y/n — defaults to the given default if stdin is not a terminal
# (non-interactive) or if --fast was passed.
ask_yn() {
    local prompt="$1" default="${2:-n}"
    if [[ ! -t 0 ]] || [[ $FAST -eq 1 ]]; then
        [[ "$default" == "y" ]] && return 0 || return 1
    fi
    local yn
    [[ "$default" == "y" ]] && prompt="$prompt [Y/n] " || prompt="$prompt [y/N] "
    read -rp "$prompt" yn
    yn="${yn:-$default}"
    [[  "${yn,,}" == "y" ]]
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
# Add one line per device. Examples (replace with your actual values):
#
# ESPHOME_LIGHTS_LIVING_ROOM="192.168.1.101:6053|A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4Y5z6A7b8C9d0="
# ESPHOME_LIGHTS_BEDROOM="192.168.1.102:6053|Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2H1g0F9e8D7c6B5a4Z3y2X1w0="
EOF
    ok "Template created at $ENV_FILE"
    warn "Edit $ENV_FILE to add your devices before starting the daemon."
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

do_uninstall() {
    echo
    info "ESPHome Lights uninstaller"
    echo

    # 1. Refuse to run as root
    [[ $EUID -ne 0 ]] || die "Do not run this installer as root or with sudo."

    # 2. Stop + disable the service
    if systemctl --user is-active "$SERVICE_NAME" > /dev/null 2>&1; then
        info "Stopping $SERVICE_NAME ..."
        systemctl --user stop "$SERVICE_NAME"
        ok "Service stopped."
    fi
    if systemctl --user is-enabled "$SERVICE_NAME" > /dev/null 2>&1; then
        systemctl --user disable "$SERVICE_NAME"
        ok "Service disabled."
    fi

    # 3. Remove service file
    if [[ -f "$SERVICE_DIR/$SERVICE_NAME" ]]; then
        rm -f "$SERVICE_DIR/$SERVICE_NAME"
        systemctl --user daemon-reload
        ok "Service file removed."
    fi

    # 4. Remove CLI symlinks
    rm -f "$INSTALL_BIN/esphome-lights" "$INSTALL_BIN/esphome-lightsd"
    ok "Symlinks removed from $INSTALL_BIN."

    # 5. Remove OpenClaw skill symlink (if present)
    OPENCLAW_SKILL="$HOME/.openclaw/skills/esphome-lights"
    if [[ -L "$OPENCLAW_SKILL" ]]; then
        rm -f "$OPENCLAW_SKILL"
        ok "OpenClaw skill symlink removed."
    fi

    # 6. Optionally keep or remove the Python 3.11 venv
    VENV_DIR="$INSTALL_LIB/venv"
    KEEP_VENV=1
    # In fast mode, default to removing the venv (clean uninstall)
    local venv_default="y"
    [[ $FAST -eq 1 ]] && venv_default="n"
    if [[ -d "$VENV_DIR" ]]; then
        if ask_yn "Keep the Python 3.11 venv at $VENV_DIR? (saves re-downloading packages on reinstall)" "$venv_default"; then
            ok "Venv kept at $VENV_DIR"
            KEEP_VENV=1
        else
            KEEP_VENV=0
        fi
    fi

    # 7. Remove scripts (and venv if requested)
    if [[ -d "$INSTALL_LIB" ]]; then
        rm -f "$INSTALL_LIB/esphome-lights" \
              "$INSTALL_LIB/esphome-lights.py" \
              "$INSTALL_LIB/esphome-lightsd.py" \
              "$INSTALL_LIB/SKILL.md"
        if [[ $KEEP_VENV -eq 0 ]]; then
            rm -rf "$VENV_DIR"
            ok "Venv removed."
        fi
        # Remove the lib dir itself if now empty
        if [[ -z "$(ls -A "$INSTALL_LIB" 2>/dev/null)" ]]; then
            rmdir "$INSTALL_LIB"
        fi
        ok "Scripts removed from $INSTALL_LIB."
    fi

    # 8. Optionally remove config
    if [[ -d "$CONFIG_DIR" ]]; then
        if ask_yn "Remove config directory $CONFIG_DIR?" "n"; then
            rm -rf "$CONFIG_DIR"
            ok "Config directory removed."
        else
            ok "Config kept at $CONFIG_DIR"
        fi
    fi

    ok "Uninstall complete."
    if [[ $KEEP_VENV -eq 1 && -d "$VENV_DIR" ]]; then
        info "Venv retained at $VENV_DIR — re-run the installer to restore everything."
    fi
    echo
    exit 0
}

[[ $UNINSTALL -eq 1 ]] && do_uninstall

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

echo
info "ESPHome Lights installer"

# 1. Refuse to run as root
[[ $EUID -ne 0 ]] || die "Do not run this installer as root or with sudo."

# 2. systemd --user must be available
systemctl --user status > /dev/null 2>&1 \
    || die "systemd user session not available. Is systemd running?\n  Hint: try 'systemctl --user status' to diagnose."

# 3. Python 3.11 — required for the daemon (aioesphomeapi + Noise encryption
#    has compatibility issues with Python 3.12/3.13 on ARM targets).
#    The CLI shell wrapper uses bash + socat/nc; Python is only needed for
#    the daemon and for --list/--status output formatting.
PYTHON311=""
for candidate in python3.11; do
    if command -v "$candidate" > /dev/null 2>&1; then
        PYTHON311="$candidate"
        break
    fi
done
if [[ -z "$PYTHON311" ]]; then
    die "Python 3.11 not found. The daemon requires Python 3.11 for Noise encryption compatibility.\n  Install manually (no sudo needed with pyenv): https://github.com/pyenv/pyenv\n  Or with system package manager: apt/dnf install python3.11"
fi
info "Using Python 3.11: $PYTHON311  ($($PYTHON311 --version 2>&1))"

# 4. socat / nc — needed for the fast CLI path (~10ms on ARM).
#    Falls back to a Python one-liner (~150ms) if neither is available.
#    Cannot be installed without sudo; just warn so the user can act.
if command -v socat >/dev/null 2>&1; then
    ok "socat found — CLI will use fast socket path (~10ms)."
elif command -v nc >/dev/null 2>&1; then
    ok "nc found — CLI will use fast socket path (~10ms)."
else
    warn "Neither socat nor nc found. CLI will fall back to Python one-liner (~150ms)."
    warn "For best performance, install socat: apt/dnf install socat"
fi

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
cp "$SOURCE_DIR/esphome-lights"     "$INSTALL_LIB/"
cp "$SOURCE_DIR/esphome-lights.py"  "$INSTALL_LIB/"
cp "$SOURCE_DIR/esphome-lightsd.py" "$INSTALL_LIB/"
cp "$SOURCE_DIR/SKILL.md"           "$INSTALL_LIB/"
chmod +x "$INSTALL_LIB/esphome-lights" \
         "$INSTALL_LIB/esphome-lights.py" \
         "$INSTALL_LIB/esphome-lightsd.py"

# Symlinks in ~/.local/bin so the commands are on PATH.
# esphome-lights -> shell wrapper (fast, ~10ms via socat/nc)
# esphome-lightsd -> Python daemon
ln -sf "$INSTALL_LIB/esphome-lights"    "$INSTALL_BIN/esphome-lights"
ln -sf "$INSTALL_LIB/esphome-lightsd.py" "$INSTALL_BIN/esphome-lightsd"
ok "Scripts installed."

# Warn if ~/.local/bin is not on PATH
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    warn "$HOME/.local/bin is not in your PATH."
    warn "Add this line to your shell profile (~/.profile, ~/.bashrc, etc.):"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ---------------------------------------------------------------------------
# Python venv + dependencies
# ---------------------------------------------------------------------------

VENV_DIR="$INSTALL_LIB/venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ -d "$VENV_DIR" ]]; then
    ok "Python 3.11 venv exists: $VENV_DIR"
else
    info "Creating Python 3.11 venv at $VENV_DIR ..."
    if "$PYTHON311" -m venv --upgrade-deps "$VENV_DIR" > /dev/null 2>&1; then
        ok "Venv created."
    elif "$PYTHON311" -m venv --without-pip "$VENV_DIR" > /dev/null 2>&1; then
        # ensurepip is stripped on this system (Debian without python3.11-venv).
        # --without-pip skips ensurepip entirely; pip is bootstrapped via
        # get-pip.py in the next step — no sudo required.
        ok "Venv created (pip will be bootstrapped below)."
    else
        die "Failed to create Python 3.11 venv. Check that python3.11 is installed correctly."
    fi
fi

# Bootstrap pip if the venv was created without it (Debian omits pip by default).
if [[ ! -f "$VENV_DIR/bin/pip" ]]; then
    info "Bootstrapping pip into venv ..."
    if "$VENV_PYTHON" -m ensurepip --upgrade 2>/dev/null; then
        ok "pip bootstrapped via ensurepip."
    else
        # ensurepip also stripped on this Debian build - fetch get-pip.py instead.
        warn "ensurepip not available - downloading get-pip.py ..."
        _GETPIP="$(mktemp)"
        if command -v curl > /dev/null 2>&1; then
            curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$_GETPIP"
        elif command -v wget > /dev/null 2>&1; then
            wget -qO "$_GETPIP" https://bootstrap.pypa.io/get-pip.py
        else
            die "curl/wget not found and ensurepip unavailable. Install pip manually: sudo apt install python3-pip"
        fi
        "$VENV_PYTHON" "$_GETPIP" --quiet \
            || die "Failed to install pip. Try: sudo apt install python3-pip python3.11-distutils"
        rm -f "$_GETPIP"
        ok "pip bootstrapped via get-pip.py."
    fi
fi

if ! "$VENV_PYTHON" -c "import aioesphomeapi" 2>/dev/null; then
    info "Installing aioesphomeapi into venv ..."
    "$VENV_PYTHON" -m pip install --upgrade pip --quiet
    "$VENV_PYTHON" -m pip install aioesphomeapi --quiet \
        || die "pip install failed. Try manually: $VENV_PYTHON -m pip install aioesphomeapi"
    ok "aioesphomeapi installed in venv."
else
    ok "aioesphomeapi already installed in venv."
fi

# Always force-reinstall noiseprotocol: the 'noise' (Perlin) package installs
# into the same noise/ directory and silently breaks Noise encryption if it
# ends up installed after noiseprotocol. Running this on every install is safe
# and cheap, and guards against the venv being in a broken state.
"$VENV_PYTHON" -m pip install --force-reinstall noiseprotocol --quiet \
    && ok "noiseprotocol verified." \
    || warn "noiseprotocol reinstall failed - encryption may not work."

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
Documentation=https://github.com/platima/ESPHome-Lights
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$VENV_PYTHON $INSTALL_LIB/esphome-lightsd.py
WorkingDirectory=$INSTALL_LIB
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME" \
    && ok "Service enabled: $SERVICE_NAME" \
    || warn "Could not enable service automatically. Enable manually: systemctl --user enable $SERVICE_NAME"

# Explicitly start (or restart on update) the service so state is always
# intentional and visible. Without this, systemd silently auto-starts the
# service during 'enable' when linger is active and default.target is met.
if systemctl --user is-active "$SERVICE_NAME" > /dev/null 2>&1; then
    info "Restarting $SERVICE_NAME ..."
    systemctl --user restart "$SERVICE_NAME" \
        && ok "Service restarted." \
        || warn "Could not restart service. Restart manually: systemctl --user restart $SERVICE_NAME"
else
    info "Starting $SERVICE_NAME ..."
    systemctl --user start "$SERVICE_NAME" \
        && ok "Service started." \
        || warn "Could not start service. Start manually: systemctl --user start $SERVICE_NAME"
fi

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

ok "Installation complete!"
echo
info "Next steps:"
echo "  1. Edit your device config:    $ENV_FILE"
echo "     (or add ESPHOME_LIGHTS_* vars to ~/.openclaw/workspace/.env)"
echo "  2. Check daemon status:        systemctl --user status $SERVICE_NAME"
echo "  3. List configured devices:    esphome-lights --list"
echo "  4. Check device states:        esphome-lights --status"
echo
info "After editing config, reload without restarting:"
echo "  esphome-lights --reload"
echo "  (or: systemctl --user kill -s HUP $SERVICE_NAME)"
echo
info "To view daemon logs:  journalctl --user -u $SERVICE_NAME -f"
info "To update:            re-run this installer"
echo
