#!/usr/bin/env bash
# =============================================================================
# install.sh -- ESPHome Lights user-level installer
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
# Upgrade (from a local git clone -- run git pull first, or let the flag do it):
#   bash install.sh --upgrade
#
# Repair (reinstall scripts, venv, and service without a git pull):
#   bash install.sh --repair
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
UPGRADE=0
REPAIR=0
FAST=0
VERBOSE=0
for arg in "$@"; do
    case "$arg" in
        --install)   ;;  # explicit install (same as no args)
        --uninstall) UNINSTALL=1 ;;
        --upgrade)   UPGRADE=1 ;;
        --repair)    REPAIR=1 ;;
        --fast)      FAST=1 ;;
        --verbose)   VERBOSE=1 ;;
        -h|--help)
            echo "Usage: bash install.sh [--install | --uninstall | --upgrade | --repair] [--fast] [--verbose]"
            echo "  --install    Install ESPHome Lights (same as no args)"
            echo "  --upgrade    Pull latest changes, update scripts + packages, restart service"
            echo "  --repair     Reinstall scripts, venv, and service from current source"
            echo "  --uninstall  Remove ESPHome Lights from this system"
            echo "  --fast       Non-interactive: accept all safe defaults"
            echo "  --verbose    Show full output from git and pip commands"
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
die()   { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# Prompt y/n -- defaults to the given default if stdin is not a terminal
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

# Optional: built-in web interface (disabled when port is 0 or unset)
# ESPHOME_LIGHTS_WEB_PORT=7890
# ESPHOME_LIGHTS_WEB_BIND=localhost   # localhost (default) or any/lan for LAN access

# Optional: log file location (set to none/off/false/0 to disable file logging)
# ESPHOME_LIGHTS_LOG_FILE=~/.local/share/esphome-lights/esphome-lightsd.log
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
        systemctl --user disable "$SERVICE_NAME" > /dev/null 2>&1
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

    # 5. Remove OpenClaw skill directory or legacy symlink (if present)
    OPENCLAW_SKILL="$HOME/.openclaw/skills/esphome-lights"
    if [[ -L "$OPENCLAW_SKILL" || -d "$OPENCLAW_SKILL" ]]; then
        rm -rf "$OPENCLAW_SKILL"
        ok "OpenClaw skill removed."
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
        info "Venv retained at $VENV_DIR. Re-run the installer to restore everything."
    fi
    echo
    exit 0
}

[[ $UNINSTALL -eq 1 ]] && do_uninstall

# ---------------------------------------------------------------------------
# Shared helpers (upgrade, repair, and the standard install path all use these)
# ---------------------------------------------------------------------------

# Display which config file(s) the daemon will load on next start.
_show_config_info() {
    local _env_file="$CONFIG_DIR/env"
    local _oc_env="$HOME/.openclaw/workspace/.env"
    local _found=0
    if [[ -f "$_oc_env" ]]; then
        ok "Config (highest priority): $_oc_env"
        _found=1
    fi
    if [[ -f "$_env_file" ]]; then
        ok "Config: $_env_file"
        _found=1
    fi
    if [[ $_found -eq 0 ]]; then
        warn "No config file found. Add devices to: $_env_file"
    fi
}

# Stop the daemon service if it is currently active.
_stop_service() {
    if systemctl --user is-active "$SERVICE_NAME" > /dev/null 2>&1; then
        info "Stopping $SERVICE_NAME ..."
        systemctl --user stop "$SERVICE_NAME" \
            && ok "Service stopped." \
            || warn "Could not stop service; proceeding anyway."
    fi
}

# Copy scripts and VERSION from SOURCE_DIR to INSTALL_LIB and update symlinks.
_install_scripts() {
    mkdir -p "$INSTALL_LIB" "$INSTALL_BIN"
    # Remove stale directories or symlinks-to-directories that conflict with
    # file copies.  Previous installs may have left a directory where a regular
    # file is now expected (e.g. esphome-lights/ vs the shell wrapper file).
    local _f
    for _f in esphome-lights esphome-lights.py esphome-lightsd.py SKILL.md VERSION; do
        if [[ -e "$INSTALL_LIB/$_f" && ! -f "$INSTALL_LIB/$_f" ]]; then
            rm -rf "$INSTALL_LIB/$_f"
        fi
    done
    cp "$SOURCE_DIR/esphome-lights"     "$INSTALL_LIB/"
    cp "$SOURCE_DIR/esphome-lights.py"  "$INSTALL_LIB/"
    cp "$SOURCE_DIR/esphome-lightsd.py" "$INSTALL_LIB/"
    cp "$SOURCE_DIR/SKILL.md"           "$INSTALL_LIB/"
    cp "$SOURCE_DIR/VERSION"            "$INSTALL_LIB/"
    chmod +x "$INSTALL_LIB/esphome-lights" \
             "$INSTALL_LIB/esphome-lights.py" \
             "$INSTALL_LIB/esphome-lightsd.py"
    ln -sfn "$INSTALL_LIB/esphome-lights"     "$INSTALL_BIN/esphome-lights"
    ln -sfn "$INSTALL_LIB/esphome-lightsd.py" "$INSTALL_BIN/esphome-lightsd"
    # Post-install sanity check: verify key files are regular files.
    # Catches stale directories, broken symlinks, or failed copies.
    local _f
    for _f in esphome-lights esphome-lights.py esphome-lightsd.py; do
        if [[ ! -f "$INSTALL_LIB/$_f" ]]; then
            warn "$INSTALL_LIB/$_f is not a regular file; installation may be broken."
        elif [[ ! -x "$INSTALL_LIB/$_f" ]]; then
            warn "$INSTALL_LIB/$_f is not executable; fixing permissions."
            chmod +x "$INSTALL_LIB/$_f"
        fi
    done
    ok "Scripts installed (v$(cat "$INSTALL_LIB/VERSION" 2>/dev/null || echo "unknown"))."
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        warn "$HOME/.local/bin is not in your PATH."
        warn "Add this to your shell profile (~/.profile, ~/.bashrc, etc.):"
        warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    fi
}

# Upgrade pip packages in an existing venv.
_upgrade_deps() {
    local _venv_py="$INSTALL_LIB/venv/bin/python"
    [[ -f "$_venv_py" ]] \
        || die "venv not found at $INSTALL_LIB/venv. Use --repair to recreate it."
    info "Upgrading packages in venv ..."
    "$_venv_py" -m pip install --upgrade pip --quiet
    "$_venv_py" -m pip install --upgrade aioesphomeapi --quiet \
        || die "Failed to upgrade aioesphomeapi."
    # Force-reinstall noiseprotocol: 'noise' (Perlin) installs to the same
    # directory and silently breaks Noise encryption if left in place.
    "$_venv_py" -m pip install --force-reinstall noiseprotocol --quiet \
        && ok "Packages upgraded." \
        || warn "noiseprotocol reinstall failed; encryption may not work."
}

# Write (or rewrite) the systemd service unit file and reload daemon config.
_write_service_file() {
    local _venv_py="$INSTALL_LIB/venv/bin/python"
    mkdir -p "$SERVICE_DIR"
    cat > "$SERVICE_DIR/$SERVICE_NAME" << EOF
[Unit]
Description=ESPHome Lights Daemon
Documentation=https://github.com/platima/ESPHome-Lights
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$_venv_py $INSTALL_LIB/esphome-lightsd.py
WorkingDirectory=$INSTALL_LIB
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME" \
        && ok "Service enabled: $SERVICE_NAME" \
        || warn "Could not enable service. Enable manually: systemctl --user enable $SERVICE_NAME"
}

# Start the service, or restart it if already running.
_start_service() {
    if systemctl --user is-active "$SERVICE_NAME" > /dev/null 2>&1; then
        info "Restarting $SERVICE_NAME ..."
        systemctl --user restart "$SERVICE_NAME" \
            && ok "Service restarted." \
            || warn "Could not restart service. Try: systemctl --user restart $SERVICE_NAME"
    else
        info "Starting $SERVICE_NAME ..."
        systemctl --user start "$SERVICE_NAME" \
            && ok "Service started." \
            || warn "Could not start service. Try: systemctl --user start $SERVICE_NAME"
    fi
}

# ---------------------------------------------------------------------------
# OpenClaw skill helpers
# ---------------------------------------------------------------------------

# Copy the five skill files into a target directory, replacing any existing
# symlink or stale directory.  OpenClaw 2026.3.13+ enforces that nothing inside
# the skills root resolves outside it, so we deploy a real directory of files
# rather than a symlink to INSTALL_LIB.
_copy_skill_files() {
    local _dest="$1"
    rm -rf "$_dest"
    mkdir -p "$_dest"
    cp "$INSTALL_LIB/esphome-lights"     "$_dest/"
    cp "$INSTALL_LIB/esphome-lights.py"  "$_dest/"
    cp "$INSTALL_LIB/esphome-lightsd.py" "$_dest/"
    cp "$INSTALL_LIB/SKILL.md"           "$_dest/"
    cp "$INSTALL_LIB/VERSION"            "$_dest/"
    chmod +x "$_dest/esphome-lights" \
             "$_dest/esphome-lights.py" \
             "$_dest/esphome-lightsd.py"
}

# Install (or refresh) skill directories across whichever OpenClaw targets the
# user selects.  Called from the main install path, do_upgrade, and do_repair.
#
# Behaviour:
#   - If any skill directories already exist (global or per-workspace), they
#     are refreshed silently -- no prompt shown.  Covers upgrade/repair flows.
#     Legacy symlinks are automatically migrated to real directories.
#   - If no directories exist, the user is offered a multi-select menu:
#       [g] Global  (~/.openclaw/skills/)         -- available to all agents
#       [N] <name>  (~/.openclaw/workspace-<name>/skills/)  -- specific agent
#       [o] Other   -- manual path entry
#       [n] Skip
#     Multiple choices are accepted (space- or comma-separated, e.g. "g 1 2").
#   - In --fast mode with no existing directories, defaults to global.
_install_openclaw_skill() {
    local _oc_dir="$HOME/.openclaw"
    [[ -d "$_oc_dir" ]] || return 0

    local _skill_name="esphome-lights"
    local _global_skills="$_oc_dir/skills"

    # Build sorted list of workspace dirs (workspace, workspace-*, etc.)
    local -a _workspaces=()
    for _ws_d in "$_oc_dir"/workspace*/; do
        _ws_d="${_ws_d%/}"
        [[ -d "$_ws_d" ]] && _workspaces+=("$_ws_d")
    done

    # Collect any locations where the skill is already linked.
    local -a _existing=()
    [[ -e "$_global_skills/$_skill_name" ]] && _existing+=("$_global_skills")
    for _ws_d in ${_workspaces[@]+"${_workspaces[@]}"}; do
        [[ -e "$_ws_d/skills/$_skill_name" ]] && _existing+=("$_ws_d/skills")
    done

    if [[ ${#_existing[@]} -gt 0 ]]; then
        # Refresh existing skill directories without prompting (upgrade / repair path).
        # Any legacy symlink is replaced with a real directory of files; OpenClaw
        # 2026.3.13+ rejects symlinks that resolve outside the skills root.
        for _target_dir in "${_existing[@]}"; do
            mkdir -p "$_target_dir"
            _copy_skill_files "$_target_dir/$_skill_name"
            ok "OpenClaw skill refreshed: ${_target_dir/#$HOME/~}/$_skill_name"
        done
        return 0
    fi

    # First-time install -- offer installation targets.
    info "OpenClaw detected at ~/.openclaw"

    if [[ $FAST -eq 1 ]]; then
        mkdir -p "$_global_skills"
        _copy_skill_files "$_global_skills/$_skill_name"
        ok "OpenClaw skill linked (global): ~/.openclaw/skills/$_skill_name"
        return 0
    fi

    echo
    echo "  Where should the ESPHome Lights skill be installed?"
    echo "  (multiple choices allowed, e.g:  g   or   1 2   or   g 2)"
    echo
    echo "  [g] Global:  ~/.openclaw/skills/  (available to all agents)  [default]"
    local _i=1
    for _ws_d in ${_workspaces[@]+"${_workspaces[@]}"}; do
        printf '  [%d] Agent:  ~/%s/skills/\n' "$_i" "${_ws_d#$HOME/}"
        _i=$(( _i + 1 ))
    done
    echo "  [o] Other:   enter a custom path"
    echo "  [n] Skip"
    echo
    local _oc_choices
    read -rp "  Choice(s) [g]: " _oc_choices
    _oc_choices="${_oc_choices:-g}"

    # Split on spaces and commas; process each token.
    local _tok _idx _ws_skills _custom_dir
    IFS=', ' read -ra _oc_tokens <<< "$_oc_choices"
    for _tok in ${_oc_tokens[@]+"${_oc_tokens[@]}"}; do
        [[ -z "$_tok" ]] && continue
        case "${_tok,,}" in
            g|global)
                mkdir -p "$_global_skills"
                _copy_skill_files "$_global_skills/$_skill_name"
                ok "OpenClaw skill linked (global): ~/.openclaw/skills/$_skill_name"
                ;;
            [1-9]*)
                _idx=$(( _tok - 1 ))
                if [[ "$_idx" -ge 0 ]] && [[ "$_idx" -lt "${#_workspaces[@]}" ]]; then
                    _ws_skills="${_workspaces[$_idx]}/skills"
                    mkdir -p "$_ws_skills"
                    _copy_skill_files "$_ws_skills/$_skill_name"
                    ok "OpenClaw skill linked: ~/${_ws_skills#$HOME/}/$_skill_name"
                else
                    warn "Invalid choice: $_tok (no workspace at that index)"
                fi
                ;;
            o|other)
                read -rp "  Enter target directory (the skill link will be placed inside it): " _custom_dir
                _custom_dir="${_custom_dir/#\~/$HOME}"
                if [[ -n "$_custom_dir" ]]; then
                    mkdir -p "$_custom_dir"
                    _copy_skill_files "$_custom_dir/$_skill_name"
                    ok "OpenClaw skill linked: ${_custom_dir/#$HOME/~}/$_skill_name"
                else
                    warn "No path entered; skipped."
                fi
                ;;
            n|no|skip)
                info "Skipped OpenClaw skill installation."
                ;;
            *)
                warn "Unrecognised choice '${_tok}'; skipped."
                ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Upgrade -- pull latest commits, update scripts + packages, restart service
# ---------------------------------------------------------------------------

do_upgrade() {
    echo
    info "ESPHome Lights: upgrade"
    echo
    [[ $EUID -ne 0 ]] || die "Do not run this installer as root or with sudo."

    if [[ ! -f "$INSTALL_LIB/esphome-lightsd.py" ]]; then
        die "No existing installation found at $INSTALL_LIB. Run install.sh without --upgrade first."
    fi

    OLD_VERSION="$(cat "$INSTALL_LIB/VERSION" 2>/dev/null || echo "unknown")"
    info "Installed version: v$OLD_VERSION"

    # Pull latest commits when running from a local git clone.
    if [[ -n "${SCRIPT_DIR:-}" ]] \
            && git -C "$SCRIPT_DIR" rev-parse --git-dir > /dev/null 2>&1; then
        info "Pulling latest changes from git ..."
        if [[ $VERBOSE -eq 1 ]]; then
            git -C "$SCRIPT_DIR" pull \
                && ok "Repository updated." \
                || warn "git pull failed; upgrading from current working tree."
        else
            git -C "$SCRIPT_DIR" pull --quiet \
                && ok "Repository updated." \
                || warn "git pull failed; upgrading from current working tree."
        fi

        # Re-exec with the updated install.sh so that any fixes to the
        # installer itself take effect immediately (e.g. new cleanup logic
        # in _install_scripts).  The _ESPHOME_REEXEC env var prevents an
        # infinite loop if for some reason the on-disk script is identical.
        if [[ -z "${_ESPHOME_REEXEC:-}" ]] \
                && [[ -f "$SCRIPT_DIR/install.sh" ]]; then
            info "Re-executing updated installer ..."
            local _reexec_args=(--upgrade)
            [[ $FAST -eq 1 ]] && _reexec_args+=(--fast)
            [[ $VERBOSE -eq 1 ]] && _reexec_args+=(--verbose)
            export _ESPHOME_REEXEC=1
            exec bash "$SCRIPT_DIR/install.sh" "${_reexec_args[@]}"
        fi
    else
        warn "Source is not a git repository; upgrading from current source files."
    fi

    NEW_VERSION="$(cat "$SOURCE_DIR/VERSION" 2>/dev/null || echo "unknown")"
    _stop_service
    _install_scripts
    _upgrade_deps
    _write_service_file
    _start_service

    _install_openclaw_skill

    _show_config_info
    if [[ "$OLD_VERSION" == "$NEW_VERSION" ]]; then
        ok "Upgrade complete (v$NEW_VERSION, already up to date)."
    else
        ok "Upgrade complete: v$OLD_VERSION -> v$NEW_VERSION"
    fi
    echo
    exit 0
}

# ---------------------------------------------------------------------------
# Repair -- reinstall scripts, venv, and service from the current source tree
# ---------------------------------------------------------------------------

do_repair() {
    echo
    info "ESPHome Lights: repair"
    info "Reinstalling scripts, dependencies, and systemd service."
    echo
    [[ $EUID -ne 0 ]] || die "Do not run this installer as root or with sudo."

    OLD_VERSION="$(cat "$INSTALL_LIB/VERSION" 2>/dev/null || echo "unknown")"
    NEW_VERSION="$(cat "$SOURCE_DIR/VERSION" 2>/dev/null || echo "unknown")"
    info "Installed: v$OLD_VERSION  Source: v$NEW_VERSION"

    _stop_service
    _install_scripts

    # Re-create the venv if missing; otherwise just upgrade packages.
    local _venv_dir="$INSTALL_LIB/venv"
    local _venv_py="$_venv_dir/bin/python"
    if [[ ! -d "$_venv_dir" ]]; then
        info "venv not found; creating Python 3.11 venv ..."
        if "$PYTHON311" -m venv --upgrade-deps "$_venv_dir" > /dev/null 2>&1; then
            ok "Venv created."
        elif "$PYTHON311" -m venv --without-pip "$_venv_dir" > /dev/null 2>&1; then
            ok "Venv created (pip will be bootstrapped) ..."
        else
            die "Failed to create Python 3.11 venv. Is python3.11 installed correctly?"
        fi
        # Bootstrap pip if the venv was created without it (Debian default).
        if [[ ! -f "$_venv_dir/bin/pip" ]]; then
            if "$_venv_py" -m ensurepip --upgrade 2>/dev/null; then
                ok "pip bootstrapped."
            else
                warn "ensurepip unavailable; downloading get-pip.py ..."
                local _getpip; _getpip="$(mktemp)"
                if command -v curl > /dev/null 2>&1; then
                    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$_getpip"
                elif command -v wget > /dev/null 2>&1; then
                    wget -qO "$_getpip" https://bootstrap.pypa.io/get-pip.py
                else
                    die "curl/wget not found and ensurepip unavailable. Install pip manually."
                fi
                "$_venv_py" "$_getpip" --quiet \
                    || die "Failed to install pip. Try: sudo apt install python3-pip"
                rm -f "$_getpip"
                ok "pip bootstrapped via get-pip.py."
            fi
        fi
        # Initial package install into the fresh venv.
        info "Installing aioesphomeapi ..."
        "$_venv_py" -m pip install --upgrade pip --quiet
        "$_venv_py" -m pip install aioesphomeapi --quiet \
            || die "pip install failed. Try: $_venv_py -m pip install aioesphomeapi"
        "$_venv_py" -m pip install --force-reinstall noiseprotocol --quiet \
            && ok "aioesphomeapi installed." \
            || warn "noiseprotocol reinstall failed; encryption may not work."
    else
        _upgrade_deps
    fi

    _write_service_file
    loginctl enable-linger "$USER" 2>/dev/null \
        && ok "loginctl linger enabled." \
        || warn "Could not enable loginctl linger."
    systemctl --user enable "$SERVICE_NAME" 2>/dev/null || true
    _start_service

    _install_openclaw_skill

    _show_config_info
    ok "Repair complete (v$NEW_VERSION)."
    echo
    exit 0
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

echo
info "ESPHome Lights installer (v$(cat "$INSTALL_LIB/VERSION" 2>/dev/null || echo "unknown"))"

# 1. Refuse to run as root
[[ $EUID -ne 0 ]] || die "Do not run this installer as root or with sudo."

# 2. systemd --user must be available
systemctl --user status > /dev/null 2>&1 \
    || die "systemd user session not available. Is systemd running?\n  Hint: try 'systemctl --user status' to diagnose."

# 3. Python 3.11 -- required for the daemon (aioesphomeapi + Noise encryption
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

# 4. socat / nc -- needed for the fast CLI path (~10ms on ARM).
#    Falls back to a Python one-liner (~150ms) if neither is available.
#    Cannot be installed without sudo; just warn so the user can act.
if command -v socat >/dev/null 2>&1; then
    ok "socat found; CLI will use fast socket path (~10ms)."
elif command -v nc >/dev/null 2>&1; then
    ok "nc found; CLI will use fast socket path (~10ms)."
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
    if [[ $VERBOSE -eq 1 ]]; then
        git clone --depth 1 "$REPO_URL" "$TMP_DIR" \
            || die "Failed to clone $REPO_URL"
    else
        git clone --depth 1 --quiet "$REPO_URL" "$TMP_DIR" \
            || die "Failed to clone $REPO_URL"
    fi
    SOURCE_DIR="$TMP_DIR"
    ok "Cloned to $TMP_DIR"
fi

# ---------------------------------------------------------------------------
# Dispatch explicit --upgrade / --repair (needs SOURCE_DIR resolved first)
# ---------------------------------------------------------------------------

[[ $UPGRADE -eq 1 ]] && do_upgrade
[[ $REPAIR  -eq 1 ]] && do_repair

# ---------------------------------------------------------------------------
# Detect existing installation and offer upgrade / repair / fresh install
# ---------------------------------------------------------------------------

if [[ -f "$INSTALL_LIB/esphome-lightsd.py" ]]; then
    _OLD_VER="$(cat "$INSTALL_LIB/VERSION" 2>/dev/null || echo "unknown")"
    _NEW_VER="$(cat "$SOURCE_DIR/VERSION"   2>/dev/null || echo "unknown")"
    _SVC_FAILED=0
    systemctl --user is-failed "$SERVICE_NAME" > /dev/null 2>&1 \
        && _SVC_FAILED=1 || true

    echo
    info "Existing installation detected (v$_OLD_VER), source v$_NEW_VER"
    [[ $_SVC_FAILED -eq 1 ]] \
        && warn "Service is in a failed state. Repair is recommended."

    if [[ $FAST -eq 1 ]]; then
        info "Auto-upgrading existing installation (--fast mode) ..."
        do_upgrade
    else
        # Detect whether the install looks healthy or broken.
        _broken_reasons=()
        [[ ! -d "$INSTALL_LIB/venv" ]] \
            && _broken_reasons+=("venv missing")
        [[ ! -f "$SERVICE_DIR/$SERVICE_NAME" ]] \
            && _broken_reasons+=("service file missing")
        [[ -L "$INSTALL_BIN/esphome-lights" ]] \
            && [[ ! -e "$INSTALL_BIN/esphome-lights" ]] \
            && _broken_reasons+=("symlink broken")
        if "$INSTALL_LIB/venv/bin/python" -c "import aioesphomeapi" 2>/dev/null; then
            true
        else
            _broken_reasons+=("aioesphomeapi not importable")
        fi
        [[ $_SVC_FAILED -eq 1 ]] && _broken_reasons+=("service in failed state")

        _default_choice="1"
        if [[ ${#_broken_reasons[@]} -gt 0 ]]; then
            warn "Issues detected: ${_broken_reasons[*]}"
            warn "Repair is recommended."
            _default_choice="2"
        fi

        echo
        echo "  [1] Upgrade:  update scripts and packages, restart service"
        echo "  [2] Repair:   full reinstall of scripts, venv, and service"
        echo "  [3] Fresh    -- run the full interactive installer"
        echo "  [q] Cancel"
        echo
        read -rp "  Choice [$_default_choice]: " _ei_choice
        _ei_choice="${_ei_choice:-$_default_choice}"
        case "${_ei_choice,,}" in
            1|u|upgrade)   do_upgrade ;;
            2|r|repair)    do_repair  ;;
            3|f|fresh)     info "Continuing with full installer ..." ;;
            q|quit|cancel) info "Cancelled."; exit 0 ;;
            *)             warn "Unrecognised choice; defaulting to upgrade."; do_upgrade ;;
        esac
    fi
fi

# ---------------------------------------------------------------------------
# Install scripts
# ---------------------------------------------------------------------------

# Stop any running daemon before overwriting its script files.
_stop_service

info "Installing scripts to $INSTALL_LIB ..."
_install_scripts

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
        # get-pip.py in the next step -- no sudo required.
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
_write_service_file

# Explicitly start (or restart on update) the service so state is always
# intentional and visible. Without this, systemd silently auto-starts the
# service during 'enable' when linger is active and default.target is met.
_start_service

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

_install_openclaw_skill

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
info "Log file location:    ~/.local/share/esphome-lights/esphome-lightsd.log"
info "  (override or disable via ESPHOME_LIGHTS_LOG_FILE in your config)"
info "To upgrade:           bash install.sh --upgrade   (git pull + update)"
info "To repair:            bash install.sh --repair    (full reinstall, no git pull)"
echo
