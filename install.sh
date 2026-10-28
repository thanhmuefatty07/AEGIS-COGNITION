#!/usr/bin/env bash
# AEGIS-COGNITION Linux/macOS Installer
# All-in-one installer that builds the locked project wheel and canonical CLIs.
#
# Usage:
#   curl -fsSL https://aegis-cognition.ai/install.sh | bash
#   # or locally:
#   ./install.sh [--skip-rust] [--skip-browser] [--python 3.14.7]
#
# End-to-end execution remains unverified on Linux/macOS; no platform pass is claimed.
#
# Flags:
#   --skip-rust        skip the standalone Rust CLI build (Rust is still needed for the Python wheel)
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

# --- 4. Pinned Rust toolchain -------------------------------------------
step "4/5" "Preparing the pinned Rust toolchain"
RUST_VERSION="$(awk -F '"' '/^[[:space:]]*channel[[:space:]]*=/ { print $2; exit }' "$REPO_DIR/rust-toolchain.toml")"
if [[ -z "$RUST_VERSION" ]]; then
    fail "Could not read the pinned Rust channel from $REPO_DIR/rust-toolchain.toml"
    exit 1
fi

if ! command -v cargo >/dev/null 2>&1 && ! command -v rustup >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --default-toolchain "$RUST_VERSION" --profile minimal
    # shellcheck disable=SC1091
    source "$HOME/.cargo/env"
    ok "rustup installed"
fi
export PATH="$HOME/.cargo/bin:$PATH"

if command -v rustup >/dev/null 2>&1; then
    rustup toolchain install "$RUST_VERSION" --profile minimal --component rustfmt --component clippy
    ok "Rust toolchain $RUST_VERSION installed/verified"
fi

if ! command -v cargo >/dev/null 2>&1 || ! command -v rustc >/dev/null 2>&1; then
    fail "Rust $RUST_VERSION is required to build the native Python wheel."
    exit 1
fi
RUSTC_VERSION="$(cd "$REPO_DIR" && rustc --version)"
if [[ "$RUSTC_VERSION" != *"$RUST_VERSION"* ]]; then
    fail "Project requires Rust $RUST_VERSION; observed: $RUSTC_VERSION"
    exit 1
fi

if [[ $SKIP_RUST -eq 0 ]]; then
    (
        cd "$REPO_DIR"
        cargo build --locked --release --manifest-path "$REPO_DIR/core/rust/Cargo.toml" --bin aegis-nerve-cli
    )
    RUST_CLI="$REPO_DIR/target/release/aegis-nerve-cli"
    if [[ ! -x "$RUST_CLI" ]]; then
        fail "cargo succeeded but the standalone CLI is missing at $RUST_CLI"
        exit 1
    fi
    ok "Standalone Rust CLI built with Rust $RUST_VERSION"
else
    warn "Skipped standalone Rust CLI build (--skip-rust); Rust is still required for the native Python wheel."
fi

# --- 5. Locked Python wheel and canonical entry points ------------------
step "5/5" "Installing the locked project wheel"
sync_args=(sync --locked --no-default-groups --no-editable --reinstall-package aegis-cognition --python "$PY_VERSION" --project "$REPO_DIR")
if [[ $SKIP_BROWSER -eq 0 ]]; then
    sync_args+=(--extra browser)
fi
UV_PROJECT_ENVIRONMENT="$VENV_DIR" uv "${sync_args[@]}"

PY_BIN="$VENV_DIR/bin/python"
AEGIS_BIN="$VENV_DIR/bin/aegis"
DESKTOP_BIN="$VENV_DIR/bin/aegis-desktop"
for required_path in "$PY_BIN" "$AEGIS_BIN" "$DESKTOP_BIN"; do
    if [[ ! -x "$required_path" ]]; then
        fail "uv sync succeeded but a required installed entry point is missing: $required_path"
        exit 1
    fi
done
"$AEGIS_BIN" version
"$AEGIS_BIN" verify --help >/dev/null
"$DESKTOP_BIN" --help >/dev/null
set +e
run_output="$("$AEGIS_BIN" run 2>&1)"
run_exit_code=$?
set -e
if [[ $run_exit_code -ne 2 || "$run_output" != *"Usage: aegis run"* ]]; then
    fail "aegis run usage smoke failed; expected exit code 2 and usage output."
    exit 1
fi
ok "Installed aegis and aegis-desktop entry points passed offline smoke checks"

# Explicit native extension import check from outside repository (O02)
(
    cd "$INSTALL_DIR"
    "$PY_BIN" -c "import sysconfig, aegis_cognition; from aegis_cognition import aegis_nerve; platlib = sysconfig.get_path('platlib'); origin = getattr(aegis_nerve, '__file__', ''); assert origin and (origin.startswith(platlib) or 'site-packages' in origin), f'Native extension not loaded from site-packages: {origin}'; print('Native wheel verified')"
)
ok "Canonical native wheel imported cleanly from site-packages"

if [[ $SKIP_BROWSER -eq 0 ]]; then
    "$PY_BIN" -m playwright install chromium
    ok "Playwright Chromium installed"
else
    warn "Skipped Playwright Chromium and browser extra (--skip-browser)"
fi

# --- Wrapper: `aegis` on PATH -------------------------------------------
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/aegis" <<EOF
#!/usr/bin/env bash
# AEGIS-COGNITION CLI wrapper
exec "$AEGIS_BIN" "\$@"
EOF
chmod +x "$BIN_DIR/aegis"

cat > "$BIN_DIR/aegis-desktop" <<EOF
#!/usr/bin/env bash
# AEGIS-COGNITION desktop service wrapper
exec "$DESKTOP_BIN" "\$@"
EOF
chmod +x "$BIN_DIR/aegis-desktop"

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
