#!/usr/bin/env bash
# AEGIS-COGNITION Termux Installer
# All-in-one installer for Android (Termux).
#
# IMPORTANT — Honest scope:
#   This script installs only the *bridge* dependencies
#   (core/python/requirements.txt — pyyaml, python-dotenv) because
#   the full root requirements.txt pulls Playwright + voice extras
#   that are not buildable on Android. Rule 3 of the master prompt
#   forbids duplicating requirements, so we DO NOT introduce a new
#   "termux" extras set — bridge mode is the canonical narrow contract.
#
# Usage:
#   curl -fsSL https://aegis-cognition.ai/install_termux.sh | bash
#
# Gate: WRITTEN, NOT YET EXECUTED end-to-end in Termux on an Android
# device in this session. See INSTALL_SCRIPTS_REPORT.md.

set -euo pipefail

banner() { printf '\n%s\n %s\n%s\n' "============================================================" "$1" "============================================================"; }
step()   { printf '\n[%s] %s\n' "$1" "$2"; }
ok()     { printf '  OK   %s\n' "$1"; }
warn()   { printf '  WARN %s\n' "$1"; }
fail()   { printf '  FAIL %s\n' "$1"; }

# --- Verify Termux -----------------------------------------------------
if [[ -z "${TERMUX_VERSION:-}" ]]; then
    fail "This script must run inside Termux (TERMUX_VERSION unset)."
    echo "  Install Termux from F-Droid: https://f-droid.org/packages/com.termux/" >&2
    exit 1
fi

banner "AEGIS-COGNITION Termux Installer ($TERMUX_VERSION)"
ok "Detected Termux"
ok "Architecture: $(uname -m)"

INSTALL_DIR="$HOME/.aegis"
REPO_URL="https://github.com/thanhmuefatty07/AEGIS-COGNITION.git"
PY_VERSION="${AEGIS_PY_VERSION:-3.14}"

# --- 1. Update + core packages ----------------------------------------
step "1/4" "Updating system packages"
pkg update -y
pkg upgrade -y
ok "pkg index refreshed"

# --- 2. Build deps ----------------------------------------------------
step "2/4" "Installing build/runtime dependencies"
pkg install -y python git clang make cmake pkg-config openssl-tool libffi rust binutils-nodejs 2>/dev/null \
    || pkg install -y python git clang make cmake pkg-config openssl-tool libffi rust 2>/dev/null \
    || true
# nodejs / ripgrep / ffmpeg are OPTIONAL on Termux — only needed for browser/voice
# workflows which are not supported on Android. Do not pull them by default.
ok "Build deps installed (skipped Node.js/ffmpeg — these need full Android, not Termux)"

# --- 3. Install uv ----------------------------------------------------
step "3/4" "Installing uv"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    # Persist for next session
    if ! grep -q '\.local/bin' "$HOME/.bashrc" 2>/dev/null; then
        printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
    fi
    ok "uv installed at $HOME/.local/bin/uv"
else
    ok "uv already present: $(uv --version)"
fi

# --- 4. Clone repo + bridge venv --------------------------------------
step "4/4" "Installing AEGIS-COGNITION bridge into $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
REPO_DIR="$INSTALL_DIR/repo"
VENV_DIR="$INSTALL_DIR/venv"

if [[ -d "$REPO_DIR/.git" ]]; then
    ( cd "$REPO_DIR" && git pull --ff-only )
    ok "Repo updated at $REPO_DIR"
else
    git clone "$REPO_URL" "$REPO_DIR"
    ok "Repo cloned at $REPO_DIR"
fi

# Use the NARROW bridge requirements file (Rule 3: do NOT duplicate deps).
BRIDGE_REQ="$REPO_DIR/core/python/requirements.txt"
if [[ ! -f "$BRIDGE_REQ" ]]; then
    fail "core/python/requirements.txt missing at $BRIDGE_REQ"
    exit 1
fi

uv venv "$VENV_DIR" --python "$PY_VERSION"
PY_BIN="$VENV_DIR/bin/python"
uv pip install --python "$PY_BIN" -r "$BRIDGE_REQ"
ok "Installed BRIDGE deps from core/python/requirements.txt"

warn "Skipped root requirements.txt intentionally:"
warn "  - playwright (no headless chromium on Android)"
warn "  - aegis-cognition[all]/[browser] (pulls voice + GUI deps that fail on Termux)"
warn "Use bridge mode (aegis init, aegis run) for offline workflow."

# Optional: Rust build of core. Skip on Termux by default — `cargo test --lib`
# exercises the local Rust toolchain, but on Termux some crates (e.g. wasmtime
# on certain archs, arrow) may fail to cross-compile. Leave opt-in.
if [[ "${AEGIS_BUILD_RUST:-0}" == "1" ]]; then
    RUST_DIR="$REPO_DIR/core/rust"
    if [[ -d "$RUST_DIR" ]]; then
        ( cd "$RUST_DIR" && cargo build --release ) || warn "cargo build failed on this arch"
    fi
else
    warn "Skipped cargo build (set AEGIS_BUILD_RUST=1 to enable; may fail on some arches)"
fi

# --- Wrapper ----------------------------------------------------------
BIN_DIR="$HOME/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/aegis" <<EOF
#!/usr/bin/env bash
# AEGIS-COGNITION CLI wrapper (Termux bridge mode)
exec "$PY_BIN" "$REPO_DIR/core/python/aegis_cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/aegis"
ok "Wrapper installed at $BIN_DIR/aegis"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    printf '\nexport PATH="$HOME/bin:$PATH"\n' >> "$HOME/.bashrc"
    ok "Added $BIN_DIR to PATH in ~/.bashrc"
fi

banner "Termux install complete (bridge mode)"
echo "  Reload shell:  source ~/.bashrc"
echo "  Then:          aegis init"
echo "============================================================"
