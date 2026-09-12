#!/usr/bin/env bash
# AEGIS-COGNITION Linux/macOS Installer
# All-in-one installer that uses the project's existing requirements.txt.
# Backward-compatible with the existing `aegis` CLI (init / run).
#
# Usage:
#   curl -fsSL https://aegis-cognition.ai/install.sh | bash
#   # or locally:
#   ./install.sh [--skip-rust] [--skip-browser] [--python 3.14.7]
#
# Honest scope: WRITTEN, NOT YET EXECUTED end-to-end on a Linux/macOS host
# in this session. See docs/archive/reports/install-scripts.md for the gate.
#
# Flags:
#   --skip-rust        skip rustup install + cargo build
#   --skip-browser     skip `playwright install chromium`
#   --python VERSION   python version for the venv (default 3.14.7)
#   --repo URL         git URL to clone (override for forks)
#   --install-dir DIR  install root (default $HOME/.aegis)

set -euo pipefail

PY_VERSION="${AEGIS_PY_VERSION:-3.14.7}"
SKIP_RUST=0
SKIP_BROWSER=0
REPO_URL="https://github.com/thanhmuefatty07/AEGIS-COGNITION.git"
INSTALL_DIR="$HOME/.aegis"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-rust)      SKIP_RUST=1; shift ;;
        --skip-browser)   SKIP_BROWSER=1; shift ;;
        --python)         PY_VERSION="$2"; shift 2 ;;
        --repo)           REPO_URL="$2"; shift 2 ;;
        --install-dir)    INSTALL_DIR="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "Unknown flag: $1" >&2; exit 2 ;;
    esac
done

# --- Detect platform ----------------------------------------------------
OS="$(uname -s)"
case "$OS" in
    Linux*)  PLATFORM="linux"  ;;
    Darwin*) PLATFORM="macos"  ;;
    *) echo "FAIL Unsupported OS: $OS" >&2; exit 1 ;;
esac

banner() { printf '\n%s\n %s\n%s\n' "============================================================" "$1" "============================================================"; }
step()   { printf '\n[%s] %s\n' "$1" "$2"; }
ok()     { printf '  OK   %s\n' "$1"; }
warn()   { printf '  WARN %s\n' "$1"; }
fail()   { printf '  FAIL %s\n' "$1"; }

banner "AEGIS-COGNITION $PLATFORM Installer"
ok "Detected platform: $PLATFORM"
ok "Install root: $INSTALL_DIR"
ok "Python version for venv: $PY_VERSION"

# --- 1. uv --------------------------------------------------------------
step "1/5" "Installing uv (Python package manager)"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env" 2>/dev/null || true
    export PATH="$HOME/.local/bin:$PATH"
    ok "uv installed at $HOME/.local/bin/uv"
else
    ok "uv already present: $(uv --version)"
fi

# --- 2. System deps -----------------------------------------------------
step "2/5" "Installing system dependencies"
if [[ "$PLATFORM" == "linux" ]]; then
    if command -v apt-get >/dev/null 2>&1; then
        sudo -E apt-get update
        sudo -E apt-get install -y --no-install-recommends \
            ca-certificates curl git build-essential pkg-config \
            nodejs ripgrep ffmpeg
        ok "apt dependencies installed"
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y curl git gcc gcc-c++ make pkgconf-pkg-config \
            nodejs ripgrep ffmpeg-free
        ok "dnf dependencies installed"
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -Sy --noconfirm curl git base-devel nodejs ripgrep ffmpeg
        ok "pacman dependencies installed"
    else
        warn "Unknown Linux distro. Install: curl, git, build-essential, nodejs, ripgrep, ffmpeg"
    fi
elif [[ "$PLATFORM" == "macos" ]]; then
    if ! command -v brew >/dev/null 2>&1; then
        fail "Homebrew not found. Install from https://brew.sh first."
        exit 1
    fi
    brew install node ripgrep ffmpeg git
    ok "brew dependencies installed"
fi

# --- 3. Clone / update repo --------------------------------------------
step "3/5" "Cloning AEGIS-COGNITION"
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

# --- 4. venv + install from requirements.txt ----------------------------
step "4/5" "Installing Python dependencies from requirements.txt"
REQ="$REPO_DIR/requirements.txt"
if [[ ! -f "$REQ" ]]; then
    fail "requirements.txt missing at $REQ — refusing to proceed."
    fail "Rule 3: install scripts MUST use the existing requirements.txt."
    exit 1
fi

# We do NOT add platform-specific extras here: requirements.txt is canonical.
# Voice/extras deps that misbehave on certain platforms should be removed from
# requirements.txt (not duplicated per-installer).
uv venv "$VENV_DIR" --python "$PY_VERSION"
PY_BIN="$VENV_DIR/bin/python"
uv pip install --python "$PY_BIN" -r "$REQ"
ok "Installed deps into $VENV_DIR"

# Optional: Playwright chromium download
if [[ $SKIP_BROWSER -eq 0 ]] && command -v node >/dev/null 2>&1; then
    "$PY_BIN" -m playwright install chromium || warn "playwright install failed; browser tests will not work"
    ok "Playwright chromium installed"
elif [[ $SKIP_BROWSER -eq 0 ]]; then
    warn "node not on PATH; skipping `playwright install chromium`"
fi

# --- 5. Rust toolchain + cargo build -----------------------------------
step "5/5" "Installing Rust toolchain and building core"
if [[ $SKIP_RUST -eq 0 ]]; then
    if ! command -v cargo >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
            | sh -s -- -y --default-toolchain 1.98.1 --profile minimal
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
        ok "rustup installed"
    else
        ok "cargo already present: $(cargo --version)"
    fi

    if command -v rustup >/dev/null 2>&1; then
        rustup toolchain install 1.98.1 --profile minimal --component rustfmt --component clippy
        ok "Rust toolchain 1.98.1 installed/verified"
    fi

    RUST_DIR="$REPO_DIR/core/rust"
    if [[ -d "$RUST_DIR" ]]; then
        ( cd "$REPO_DIR" && rustc --version | grep -Eq '(^| )1\.98\.1( |$)' ) \
            || { fail "Project requires Rust 1.98.1"; exit 1; }
        ( cd "$REPO_DIR" && cargo build --release )
        ok "Rust core built (release)"
    else
        warn "core/rust missing at $RUST_DIR — skipping cargo build"
    fi
else
    warn "Skipped Rust toolchain + cargo build (--skip-rust)"
fi

# --- Wrapper: `aegis` on PATH -------------------------------------------
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/aegis" <<EOF
#!/usr/bin/env bash
# AEGIS-COGNITION CLI wrapper
exec "$PY_BIN" "$REPO_DIR/core/python/aegis_cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/aegis"

# Add ~/.local/bin to PATH if missing
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        [[ -f "$rc" ]] || continue
        if ! grep -q "$BIN_DIR" "$rc"; then
            printf '\n# Added by AEGIS-COGNITION installer\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$rc"
            ok "Added $BIN_DIR to PATH in $rc"
        fi
    done
    warn "Reload shell to use 'aegis' (or: export PATH=\"$BIN_DIR:\$PATH\")"
fi

banner "Installation complete"
echo "  Next: open a NEW shell and run:  aegis init" >&2
echo "============================================================"
