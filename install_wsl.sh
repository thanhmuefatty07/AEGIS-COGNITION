#!/usr/bin/env bash
# AEGIS-COGNITION WSL2 Installer
# Wrapper that verifies WSL2, then delegates to install.sh.
#
# Usage (from inside WSL2):
#   curl -fsSL https://aegis-cognition.ai/install_wsl.sh | bash
#   # or:
#   ./install_wsl.sh
#
# Honest scope: WRITTEN, NOT YET EXECUTED end-to-end in a WSL2 instance
# in this session. See INSTALL_SCRIPTS_REPORT.md.

set -euo pipefail

banner() { printf '\n%s\n %s\n%s\n' "============================================================" "$1" "============================================================"; }
step()   { printf '\n[%s] %s\n' "$1" "$2"; }
ok()     { printf '  OK   %s\n' "$1"; }
warn()   { printf '  WARN %s\n' "$1"; }
fail()   { printf '  FAIL %s\n' "$1"; }

banner "AEGIS-COGNITION WSL2 Installer"

# --- Verify WSL2 environment -------------------------------------------
if ! grep -qEi "(Microsoft|WSL)" /proc/version 2>/dev/null; then
    fail "This script must run inside WSL2 (no Microsoft/WSL marker in /proc/version)."
    echo "  Hint: enable WSL2 from Windows:  wsl --install" >&2
    exit 1
fi
ok "Running inside WSL2"

# --- Locate the Linux installer ----------------------------------------
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
LINUX_INSTALL="$SELF_DIR/install.sh"
if [[ ! -f "$LINUX_INSTALL" ]]; then
    # Fall back to fetching it next to this script
    warn "install.sh missing at $LINUX_INSTALL — fetching from repo"
    curl -fsSL "https://raw.githubusercontent.com/aegis-cognition/aegis-cognition/main/install.sh" -o "$LINUX_INSTALL"
    chmod +x "$LINUX_INSTALL"
fi
ok "Found Linux installer: $LINUX_INSTALL"

# --- 1. WSL2-specific prerequisites ------------------------------------
step "1/3" "Installing WSL2 prerequisites"
if command -v apt-get >/dev/null 2>&1; then
    sudo -E apt-get update
    sudo -E apt-get install -y --no-install-recommends ca-certificates curl git build-essential pkg-config
    ok "apt prerequisites installed"
else
    warn "Non-Debian WSL2 distro detected; ensure curl/git/build-essential are present"
fi

# Windows interop is normally on by default; ensure /mnt/c is mounted
if ! mountpoint -q /mnt/c 2>/dev/null; then
    warn "/mnt/c is not mounted. Windows interop may be disabled."
    warn "Re-enable with (from PowerShell):  wsl --mount --vhd C:\\ or Set-NetAdapterBinding"
fi

# --- 2. Delegate to Linux installer ------------------------------------
step "2/3" "Running Linux installer"
chmod +x "$LINUX_INSTALL"
# Pass through all CLI flags
bash "$LINUX_INSTALL" "$@"

# --- 3. WSL2 PATH integration ------------------------------------------
step "3/3" "WSL2 Windows PATH integration"
# Append a one-time PATH add for WindowsApps (so `aegis.cmd` works from WSL2)
MARKER="# AEGIS-COGNITION WSL2 PATH integration"
if ! grep -qF "$MARKER" "$HOME/.bashrc" 2>/dev/null; then
    cat >> "$HOME/.bashrc" <<'EOF'

# AEGIS-COGNITION WSL2 PATH integration
if command -v wslpath >/dev/null 2>&1; then
    WIN_USER=$(powershell.exe -NoProfile -Command '$env:USERNAME' 2>/dev/null | tr -d '\r')
    if [ -n "$WIN_USER" ]; then
        export PATH="$PATH:/mnt/c/Users/$WIN_USER/AppData/Local/Microsoft/WindowsApps"
    fi
fi
EOF
    ok "Appended WindowsApps PATH block to ~/.bashrc"
else
    ok "PATH integration already present"
fi

banner "WSL2 install complete"
echo "  Reload shell:  source ~/.bashrc" >&2
echo "  Then:          aegis init" >&2
echo "============================================================"
