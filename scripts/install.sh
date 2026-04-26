#!/usr/bin/env bash
# OpenBaD installation script
# Usage: sudo ./scripts/install.sh [--bootstrap] [--configure-wsl-systemd] [--skip-services] [--uninstall]
set -euo pipefail

OPENBAD_USER="openbad"
OPENBAD_GROUP="openbad"
APP_HOME="/opt/openbad"
VENV_DIR="/opt/openbad/venv"
OPENBAD_BIN="/usr/local/bin/openbad"
CONFIG_DIR="/etc/openbad"
DATA_DIR="/var/lib/openbad"
LOG_DIR="/var/log/openbad"
SYSTEMD_DIR="/etc/systemd/system"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
CONFIG_SRC="$PROJECT_ROOT/config"

DO_BOOTSTRAP=false
SKIP_SERVICES=false
DO_UNINSTALL=false
CONFIGURE_WSL_SYSTEMD=false
BROKER_IMPL=""
BROKER_SERVICE_MODE="managed"
EXTERNAL_BROKER_UNIT=""

# Colours (if terminal supports them)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Colour

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

usage() {
    cat <<'EOF'
OpenBaD installer

Usage:
        sudo ./scripts/install.sh [--bootstrap] [--configure-wsl-systemd] [--skip-services]
    sudo ./scripts/install.sh --uninstall

Options:
  --bootstrap      Install Linux prerequisites (Ubuntu/Debian apt path)
    --configure-wsl-systemd  Configure /etc/wsl.conf with systemd=true (requires WSL restart)
  --skip-services  Do not install/enable/start systemd units
  --uninstall      Remove OpenBaD package + units
  --help           Show this help
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --bootstrap)
                DO_BOOTSTRAP=true
                ;;
            --skip-services)
                SKIP_SERVICES=true
                ;;
            --configure-wsl-systemd)
                CONFIGURE_WSL_SYSTEMD=true
                ;;
            --uninstall)
                DO_UNINSTALL=true
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                error "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done
}

require_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (sudo)."
        exit 1
    fi
}

is_linux() {
    [[ "$(uname -s)" == "Linux" ]]
}

is_wsl() {
    if [[ -f /proc/version ]] && grep -qi "microsoft" /proc/version; then
        return 0
    fi
    return 1
}

has_systemd() {
    [[ -d /run/systemd/system ]] && command -v systemctl &>/dev/null
}

has_systemd_unit() {
    local unit="$1"
    has_systemd && systemctl list-unit-files --type=service --no-legend "$unit" 2>/dev/null | grep -q "^${unit}"
}

configure_wsl_systemd() {
    if ! is_wsl; then
        warn "--configure-wsl-systemd ignored (not running inside WSL)."
        return
    fi

    local wsl_conf="/etc/wsl.conf"
    info "Configuring WSL systemd support in $wsl_conf ..."

    if [[ -f "$wsl_conf" ]] && grep -Eq '^\s*systemd\s*=\s*true\s*$' "$wsl_conf"; then
        info "systemd=true already present in $wsl_conf"
        return
    fi

    if [[ -f "$wsl_conf" ]] && grep -Eq '^\s*\[boot\]\s*$' "$wsl_conf"; then
        {
            echo
            echo "# Added by OpenBaD installer"
            echo "systemd=true"
        } >> "$wsl_conf"
    else
        {
            echo "[boot]"
            echo "systemd=true"
        } >> "$wsl_conf"
    fi

    warn "WSL restart required: run 'wsl.exe --shutdown' from Windows, then reopen WSL and rerun installer."
}

ensure_systemd_ready() {
    if [[ "$SKIP_SERVICES" == "true" ]]; then
        return
    fi

    if has_systemd; then
        return
    fi

    if is_wsl; then
        if [[ "$CONFIGURE_WSL_SYSTEMD" == "true" ]]; then
            configure_wsl_systemd
        fi
        error "systemd is required for full install. Enable systemd in WSL and retry, or pass --skip-services for dev-only mode."
        exit 1
    fi

    error "systemd is required for full install on Linux."
    error "Use --skip-services only for development mode."
    exit 1
}

install_prereqs_apt() {
    info "Bootstrapping OS dependencies with apt..."
    apt-get update
    apt-get install -y ca-certificates curl python3 python3-pip python3-venv

    # Some distros split venv by Python minor version.
    if ! python3 -m venv --help &>/dev/null; then
        py_minor="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        apt-get install -y "python${py_minor}-venv"
    fi

    # MQTT broker: prefer NanoMQ, fallback to Mosquitto.
    if ! command -v nanomq &>/dev/null && ! command -v mosquitto &>/dev/null; then
        apt-get install -y nanomq || true
        if ! command -v nanomq &>/dev/null; then
            apt-get install -y mosquitto
        fi
    fi
}

bootstrap_os() {
    if ! is_linux; then
        error "Bootstrap is supported on Linux only."
        exit 1
    fi

    if command -v apt-get &>/dev/null; then
        install_prereqs_apt
    else
        warn "No supported package manager detected for bootstrap."
        warn "Install manually: python3, python3-pip, python3-venv"
    fi
}

select_broker_impl() {
    if command -v nanomq &>/dev/null; then
        BROKER_IMPL="nanomq"
        BROKER_SERVICE_MODE="managed"
        info "Broker selected: NanoMQ"
        return
    fi

    if command -v mosquitto &>/dev/null; then
        BROKER_IMPL="mosquitto"
        if has_systemd_unit "mosquitto.service"; then
            BROKER_SERVICE_MODE="external"
            EXTERNAL_BROKER_UNIT="mosquitto.service"
            info "Broker selected: existing Mosquitto system service"
            return
        fi
        BROKER_SERVICE_MODE="managed"
        warn "NanoMQ not found. Falling back to Mosquitto broker service."
        return
    fi

    error "No MQTT broker found (nanomq/mosquitto)."
    error "Run with --bootstrap or install a broker manually."
    exit 1
}

install_broker_unit() {
    local dst="$SYSTEMD_DIR/openbad-broker.service"

    if [[ "$BROKER_SERVICE_MODE" == "external" ]]; then
        if [[ -f "$dst" ]]; then
            systemctl disable --now openbad-broker.service 2>/dev/null || true
            rm -f "$dst"
        fi
        info "  Reusing ${EXTERNAL_BROKER_UNIT} for MQTT broker"
        return
    fi

    if [[ "$BROKER_IMPL" == "nanomq" ]]; then
        cp "$CONFIG_SRC/openbad-broker.service" "$dst"
        info "  Installed openbad-broker.service (NanoMQ)"
        return
    fi

    # Mosquitto fallback unit.
    local mosquitto_bin
    mosquitto_bin="$(command -v mosquitto)"
    cat > "$dst" <<EOF
[Unit]
Description=OpenBaD MQTT Broker (Mosquitto fallback)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${mosquitto_bin} -p 1883
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
    info "  Installed openbad-broker.service (Mosquitto fallback)"
}

restart_or_start_service() {
    local unit="$1"

    if systemctl is-active --quiet "$unit"; then
        systemctl restart "$unit"
        return
    fi

    systemctl start "$unit"
}

# ------------------------------------------------------------------
# Create system user
# ------------------------------------------------------------------
create_user() {
    if id "$OPENBAD_USER" &>/dev/null; then
        info "User '$OPENBAD_USER' already exists."
    else
        info "Creating system user '$OPENBAD_USER'..."
        useradd --system --no-create-home --home-dir "$DATA_DIR" \
                --shell /usr/sbin/nologin "$OPENBAD_USER"
    fi
}

# ------------------------------------------------------------------
# Ensure openbad can traverse the invoking user's home directory
# ------------------------------------------------------------------
ensure_home_traversable() {
    local owner_home=""
    # Determine the real user who invoked sudo
    if [[ -n "${SUDO_USER:-}" ]] && [[ "$SUDO_USER" != "root" ]]; then
        owner_home="$(eval echo "~$SUDO_USER")"
    fi
    if [[ -z "$owner_home" ]] || [[ ! -d "$owner_home" ]]; then
        return
    fi

    # Grant the openbad user read+execute via POSIX ACL so that:
    #   - x (execute): allows traversing to known paths inside the home dir
    #   - r (read):    allows listing directory contents (needed by find_files)
    # Using an ACL targets only the openbad user — not all users on the system.
    if command -v setfacl &>/dev/null; then
        if ! getfacl -p "$owner_home" 2>/dev/null | grep -q "^user:${OPENBAD_USER}:r.x"; then
            info "Granting $OPENBAD_USER read+traverse ACL on $owner_home..."
            setfacl -m "u:${OPENBAD_USER}:rx" "$owner_home"
        fi
    else
        # Fallback: plain chmod o+x (traverse only, find_files from / won't
        # discover files but stat-based access still works).
        local perms
        perms="$(stat -c '%a' "$owner_home")"
        if (( (perms & 1) == 0 )); then
            info "Adding traverse (o+x) permission on $owner_home for $OPENBAD_USER..."
            chmod o+x "$owner_home"
        fi
    fi
}

# ------------------------------------------------------------------
# Install Python package
# ------------------------------------------------------------------
install_package() {
    info "Installing openbad Python package into dedicated virtualenv..."
    if ! command -v python3 &>/dev/null; then
        error "pip not found. Install Python >= 3.11 and pip first."
        exit 1
    fi

    mkdir -p "$APP_HOME"

    # Use a private virtualenv to avoid PEP 668 'externally managed' failures
    # on Debian/Ubuntu and WSL images.
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        python3 -m venv "$VENV_DIR"
    fi

    "$VENV_DIR/bin/python" -m pip install --upgrade pip
    # Reinstall the package (picks up code changes regardless of version),
    # then ensure all dependency constraints are satisfied without trying
    # to upgrade already-compatible packages (avoids resolver backtracking).
    "$VENV_DIR/bin/python" -m pip install --force-reinstall --no-deps "$PROJECT_ROOT"
    "$VENV_DIR/bin/python" -m pip install "$PROJECT_ROOT"

    # Keep CLI path stable for systemd and operators.
    ln -sf "$VENV_DIR/bin/openbad" "$OPENBAD_BIN"
    chmod 755 "$OPENBAD_BIN"

    # Install the privileged heartbeat-interval helper script.
    local helper_src="$SCRIPT_DIR/openbad-apply-heartbeat-interval"
    local helper_dst="/usr/local/bin/openbad-apply-heartbeat-interval"
    if [ -f "$helper_src" ]; then
        cp "$helper_src" "$helper_dst"
        chown root:root "$helper_dst"
        chmod 755 "$helper_dst"
        info "  Installed openbad-apply-heartbeat-interval"
    else
        warn "  openbad-apply-heartbeat-interval not found in $SCRIPT_DIR"
    fi

    # Install the privileged telemetry-interval helper script.
    helper_src="$SCRIPT_DIR/openbad-apply-telemetry-interval"
    helper_dst="/usr/local/bin/openbad-apply-telemetry-interval"
    if [ -f "$helper_src" ]; then
        cp "$helper_src" "$helper_dst"
        chown root:root "$helper_dst"
        chmod 755 "$helper_dst"
        info "  Installed openbad-apply-telemetry-interval"
    else
        warn "  openbad-apply-telemetry-interval not found in $SCRIPT_DIR"
    fi

    # Remove the old sudoers rule if it exists (no longer needed).
    rm -f "/etc/sudoers.d/openbad-heartbeat"

    # The drop-in directory is managed by openbad-heartbeat-apply.service (root).
    mkdir -p /etc/systemd/system/openbad-heartbeat.timer.d
}

# ------------------------------------------------------------------
# Copy configuration files
# ------------------------------------------------------------------
install_configs() {
    info "Installing configuration to $CONFIG_DIR..."
    mkdir -p "$CONFIG_DIR"

    for f in "$CONFIG_SRC"/*.yaml "$CONFIG_SRC"/*.conf; do
        [ -f "$f" ] || continue
        name="$(basename "$f")"
        dst="$CONFIG_DIR/$name"
        if [ -f "$dst" ]; then
            warn "  $name already exists, skipping (remove to reinstall)."
        else
            cp "$f" "$dst"
            info "  Copied $name"
        fi
    done

    ensure_identity_secret

    chown -R "$OPENBAD_USER:$OPENBAD_GROUP" "$CONFIG_DIR"
    chmod 750 "$CONFIG_DIR"
    chmod 640 "$CONFIG_DIR"/*
}

ensure_identity_secret() {
    local status

    status="$(OPENBAD_INSTALL_CONFIG_DIR="$CONFIG_DIR" "$VENV_DIR/bin/python" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

import yaml

from openbad.setup import generate_secret_key, patch_identity_config

config_dir = Path(os.environ["OPENBAD_INSTALL_CONFIG_DIR"])
identity_path = config_dir / "identity.yaml"
if not identity_path.exists():
    print("missing")
    raise SystemExit(0)

data = yaml.safe_load(identity_path.read_text()) or {}
secret_hex = ((data.get("identity") or {}).get("secret_hex") or "").strip()
if secret_hex:
    print("present")
else:
    patch_identity_config(config_dir, generate_secret_key())
    print("generated")
PY
)"

    case "$status" in
        generated)
            info "  Generated persistent identity secret"
            ;;
        present)
            info "  Preserved existing identity secret"
            ;;
        missing)
            warn "  identity.yaml missing; skipping identity secret generation"
            ;;
        *)
            warn "  Unexpected identity secret status: $status"
            ;;
    esac
}

# ------------------------------------------------------------------
# Create data and log directories
# ------------------------------------------------------------------
create_dirs() {
    info "Creating directories..."
    mkdir -p "$DATA_DIR" "$LOG_DIR"
    chown "$OPENBAD_USER:$OPENBAD_GROUP" "$DATA_DIR" "$LOG_DIR"
    chmod 750 "$DATA_DIR" "$LOG_DIR"
}

# ------------------------------------------------------------------
# Install systemd units
# ------------------------------------------------------------------
install_units() {
    if [[ "$SKIP_SERVICES" == "true" ]]; then
        warn "Skipping service installation (--skip-services set)."
        return
    fi
    ensure_systemd_ready

    info "Installing systemd units..."
    install_broker_unit

    if [ -f "$CONFIG_SRC/openbad.service" ]; then
        cp "$CONFIG_SRC/openbad.service" "$SYSTEMD_DIR/openbad.service"
        info "  Installed openbad.service"
    else
        warn "  openbad.service not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-wui.service" ]; then
        cp "$CONFIG_SRC/openbad-wui.service" "$SYSTEMD_DIR/openbad-wui.service"
        info "  Installed openbad-wui.service"
    else
        warn "  openbad-wui.service not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-heartbeat.service" ]; then
        cp "$CONFIG_SRC/openbad-heartbeat.service" "$SYSTEMD_DIR/openbad-heartbeat.service"
        info "  Installed openbad-heartbeat.service"
    else
        warn "  openbad-heartbeat.service not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-heartbeat.timer" ]; then
        cp "$CONFIG_SRC/openbad-heartbeat.timer" "$SYSTEMD_DIR/openbad-heartbeat.timer"
        info "  Installed openbad-heartbeat.timer"
    else
        warn "  openbad-heartbeat.timer not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-heartbeat-apply.service" ]; then
        cp "$CONFIG_SRC/openbad-heartbeat-apply.service" "$SYSTEMD_DIR/openbad-heartbeat-apply.service"
        info "  Installed openbad-heartbeat-apply.service"
    else
        warn "  openbad-heartbeat-apply.service not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-heartbeat-watch.path" ]; then
        cp "$CONFIG_SRC/openbad-heartbeat-watch.path" "$SYSTEMD_DIR/openbad-heartbeat-watch.path"
        info "  Installed openbad-heartbeat-watch.path"
    else
        warn "  openbad-heartbeat-watch.path not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-telemetry-apply.service" ]; then
        cp "$CONFIG_SRC/openbad-telemetry-apply.service" "$SYSTEMD_DIR/openbad-telemetry-apply.service"
        info "  Installed openbad-telemetry-apply.service"
    else
        warn "  openbad-telemetry-apply.service not found in $CONFIG_SRC"
    fi

    if [ -f "$CONFIG_SRC/openbad-telemetry-watch.path" ]; then
        cp "$CONFIG_SRC/openbad-telemetry-watch.path" "$SYSTEMD_DIR/openbad-telemetry-watch.path"
        info "  Installed openbad-telemetry-watch.path"
    else
        warn "  openbad-telemetry-watch.path not found in $CONFIG_SRC"
    fi

    systemctl daemon-reload
    info "Enabling services..."
    if [[ "$BROKER_SERVICE_MODE" == "external" ]]; then
        systemctl enable "$EXTERNAL_BROKER_UNIT"
    else
        systemctl enable openbad-broker.service
    fi
    systemctl enable openbad.service
    systemctl enable openbad-wui.service
    systemctl enable openbad-heartbeat.timer
    systemctl enable openbad-heartbeat-watch.path
    systemctl enable openbad-telemetry-watch.path
}

# ------------------------------------------------------------------
# Start services
# ------------------------------------------------------------------
start_services() {
    if [[ "$SKIP_SERVICES" == "true" ]]; then
        warn "Skipping service start (--skip-services set)."
        return
    fi
    ensure_systemd_ready

    info "Starting services..."
    if [[ "$BROKER_SERVICE_MODE" == "external" ]]; then
        restart_or_start_service "$EXTERNAL_BROKER_UNIT"
    else
        restart_or_start_service openbad-broker.service
    fi
    restart_or_start_service openbad.service
    restart_or_start_service openbad-wui.service
    # Ensure the timer is running at the configured interval.
    # openbad-heartbeat-apply.service reads heartbeat.yaml and writes the drop-in.
    systemctl start openbad-heartbeat-apply.service
    systemctl start openbad-heartbeat.timer
    systemctl start openbad-heartbeat-watch.path
    # Ensure telemetry interval from telemetry.yaml is applied and watched.
    systemctl start openbad-telemetry-apply.service
    systemctl start openbad-telemetry-watch.path
    info "Services started. Check status with: systemctl status openbad"
}

validate_installation() {
    local failures=0

    if [[ "$SKIP_SERVICES" == "true" ]]; then
        return
    fi

    info "Validating installation..."

    for unit in openbad.service openbad-wui.service; do
        if ! systemctl is-active --quiet "$unit"; then
            error "Service failed to start: $unit"
            failures=1
        fi
    done

    if [[ "$BROKER_SERVICE_MODE" == "external" ]]; then
        if ! systemctl is-active --quiet "$EXTERNAL_BROKER_UNIT"; then
            error "Broker service failed to start: $EXTERNAL_BROKER_UNIT"
            failures=1
        fi
    elif ! systemctl is-active --quiet openbad-broker.service; then
        error "Service failed to start: openbad-broker.service"
        failures=1
    fi

    # Services may need a moment to become ready after systemctl start.
    local health_ok=false
    for attempt in 1 2 3 4 5; do
        if "$OPENBAD_BIN" health >/dev/null 2>&1; then
            health_ok=true
            break
        fi
        info "  Waiting for services to stabilize (attempt $attempt/5)..."
        sleep 2
    done
    if [[ "$health_ok" != "true" ]]; then
        error "openbad health reported an unhealthy stack"
        "$OPENBAD_BIN" health || true
        failures=1
    fi

    if [[ "$failures" -ne 0 ]]; then
        error "Installation validation failed. Review systemctl status and openbad health output."
        exit 1
    fi

    info "Installation validation passed."
}

# ------------------------------------------------------------------
# Uninstall
# ------------------------------------------------------------------
uninstall() {
    if has_systemd; then
        info "Stopping services..."
        systemctl stop openbad-telemetry-watch.path 2>/dev/null || true
        systemctl stop openbad-heartbeat-watch.path 2>/dev/null || true
        systemctl stop openbad-heartbeat.timer 2>/dev/null || true
        systemctl stop openbad-wui.service 2>/dev/null || true
        systemctl stop openbad.service 2>/dev/null || true
        systemctl stop openbad-broker.service 2>/dev/null || true
        systemctl disable openbad-telemetry-watch.path 2>/dev/null || true
        systemctl disable openbad-heartbeat-watch.path 2>/dev/null || true
        systemctl disable openbad-heartbeat.timer 2>/dev/null || true
        systemctl disable openbad-wui.service 2>/dev/null || true
        systemctl disable openbad.service 2>/dev/null || true
        systemctl disable openbad-broker.service 2>/dev/null || true
    else
        warn "systemd not detected; skipping service stop/disable."
    fi

    info "Removing systemd units..."
    rm -f "$SYSTEMD_DIR/openbad-telemetry-watch.path"
    rm -f "$SYSTEMD_DIR/openbad-telemetry-apply.service"
    rm -f "$SYSTEMD_DIR/openbad-heartbeat-watch.path"
    rm -f "$SYSTEMD_DIR/openbad-heartbeat-apply.service"
    rm -f "$SYSTEMD_DIR/openbad-heartbeat.timer"
    rm -f "$SYSTEMD_DIR/openbad-heartbeat.service"
    rm -f "$SYSTEMD_DIR/openbad-wui.service"
    rm -f "$SYSTEMD_DIR/openbad.service"
    rm -f "$SYSTEMD_DIR/openbad-broker.service"
    if has_systemd; then
        systemctl daemon-reload
    fi

    info "Removing Python package..."
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        "$VENV_DIR/bin/python" -m pip uninstall -y openbad 2>/dev/null || true
    fi
    rm -f "$OPENBAD_BIN"
    rm -f "/usr/local/bin/openbad-apply-telemetry-interval"
    rm -f "/usr/local/bin/openbad-apply-heartbeat-interval"
    rm -rf "/etc/systemd/system/openbad-heartbeat.timer.d"
    rm -rf "$VENV_DIR"

    warn "Config ($CONFIG_DIR) and data ($DATA_DIR) directories preserved."
    warn "To fully remove: rm -rf $CONFIG_DIR $DATA_DIR $LOG_DIR"
    warn "To remove user: userdel $OPENBAD_USER"
    info "Uninstall complete."
}

# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
main() {
    parse_args "$@"
    require_root

    if [[ "$DO_UNINSTALL" == "true" ]]; then
        uninstall
        exit 0
    fi

    if ! is_linux; then
        error "OpenBaD installer currently supports Linux/WSL hosts only."
        exit 1
    fi

    if is_wsl; then
        info "WSL environment detected."
    fi

    if [[ "$DO_BOOTSTRAP" == "true" ]]; then
        bootstrap_os
    fi

    if [[ "$SKIP_SERVICES" != "true" ]]; then
        select_broker_impl
        ensure_systemd_ready
    fi

    info "=== OpenBaD Installation ==="
    create_user
    ensure_home_traversable
    install_package
    install_configs
    create_dirs
    install_units
    start_services
    validate_installation
    info "=== Installation complete ==="
    info "Run 'openbad status' to verify."
}

main "$@"
