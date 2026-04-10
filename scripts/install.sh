#!/usr/bin/env bash
# OpenBaD installation script
# Usage: sudo ./install.sh [--bootstrap] [--skip-services] [--uninstall]
set -euo pipefail

OPENBAD_USER="openbad"
OPENBAD_GROUP="openbad"
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
  sudo ./install.sh [--bootstrap] [--skip-services]
  sudo ./install.sh --uninstall

Options:
  --bootstrap      Install Linux prerequisites (Ubuntu/Debian apt path)
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

install_prereqs_apt() {
    info "Bootstrapping OS dependencies with apt..."
    apt-get update
    apt-get install -y ca-certificates curl python3 python3-pip python3-venv

    # Some distros split venv by Python minor version.
    if ! python3 -m venv --help &>/dev/null; then
        py_minor="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        apt-get install -y "python${py_minor}-venv"
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
# Install Python package
# ------------------------------------------------------------------
install_package() {
    info "Installing openbad Python package..."
    if command -v python3 &>/dev/null; then
        python3 -m pip install --upgrade pip
        python3 -m pip install --upgrade "$PROJECT_ROOT"
    elif command -v pip3 &>/dev/null; then
        pip3 install --upgrade "$PROJECT_ROOT"
    elif command -v pip &>/dev/null; then
        pip install --upgrade "$PROJECT_ROOT"
    else
        error "pip not found. Install Python >= 3.11 and pip first."
        exit 1
    fi
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

    chown -R "$OPENBAD_USER:$OPENBAD_GROUP" "$CONFIG_DIR"
    chmod 750 "$CONFIG_DIR"
    chmod 640 "$CONFIG_DIR"/*
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
    if ! has_systemd; then
        warn "systemd not detected; skipping unit install."
        warn "Tip: on WSL enable systemd in /etc/wsl.conf then restart WSL."
        return
    fi

    info "Installing systemd units..."
    for unit in openbad-broker.service openbad.service; do
        src="$CONFIG_SRC/$unit"
        if [ -f "$src" ]; then
            cp "$src" "$SYSTEMD_DIR/$unit"
            info "  Installed $unit"
        else
            warn "  $unit not found in $CONFIG_SRC"
        fi
    done

    systemctl daemon-reload
    info "Enabling services..."
    systemctl enable openbad-broker.service
    systemctl enable openbad.service
}

# ------------------------------------------------------------------
# Start services
# ------------------------------------------------------------------
start_services() {
    if [[ "$SKIP_SERVICES" == "true" ]]; then
        warn "Skipping service start (--skip-services set)."
        return
    fi
    if ! has_systemd; then
        warn "systemd not detected; skipping service start."
        return
    fi

    info "Starting services..."
    systemctl start openbad-broker.service
    systemctl start openbad.service
    info "Services started. Check status with: systemctl status openbad"
}

# ------------------------------------------------------------------
# Uninstall
# ------------------------------------------------------------------
uninstall() {
    if has_systemd; then
        info "Stopping services..."
        systemctl stop openbad.service 2>/dev/null || true
        systemctl stop openbad-broker.service 2>/dev/null || true
        systemctl disable openbad.service 2>/dev/null || true
        systemctl disable openbad-broker.service 2>/dev/null || true
    else
        warn "systemd not detected; skipping service stop/disable."
    fi

    info "Removing systemd units..."
    rm -f "$SYSTEMD_DIR/openbad.service"
    rm -f "$SYSTEMD_DIR/openbad-broker.service"
    if has_systemd; then
        systemctl daemon-reload
    fi

    info "Removing Python package..."
    if command -v python3 &>/dev/null; then
        python3 -m pip uninstall -y openbad 2>/dev/null || true
    else
        pip3 uninstall -y openbad 2>/dev/null || true
    fi

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

    info "=== OpenBaD Installation ==="
    create_user
    install_package
    install_configs
    create_dirs
    install_units
    start_services
    info "=== Installation complete ==="
    info "Run 'openbad status' to verify."
}

main "$@"
