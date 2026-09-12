# AEGIS-COGNITION Windows Installer
# All-in-one installer that uses the project's existing requirements.txt.
# Backward-compatible with the existing `aegis` CLI (init / run).
#
# Usage (run in an elevated PowerShell):
#   iwr -useb https://aegis-cognition.ai/install.ps1 | iex
#   # or locally:
#   .\install.ps1
#
# Honest scope: this script is WRITTEN but NOT YET EXECUTED end-to-end on a
# Windows host in this session. See docs/archive/reports/install-scripts.md for the gate.

[CmdletBinding()]
param(
    [switch]$SkipRust,        # skip Rust toolchain + cargo build
    [switch]$SkipBrowser,     # skip playwright install (saves ~150MB)
    [string]$PythonVersion = "3.14.7",
    [string]$RepoUrl = "https://github.com/thanhmuefatty07/AEGIS-COGNITION.git",
    [string]$InstallRoot = "$env:LOCALAPPDATA\aegis"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

function Step([string]$n, [string]$msg) {
    Write-Host ""
    Write-Host "[$n] $msg" -ForegroundColor Cyan
}

function Ok([string]$msg)   { Write-Host "  OK   $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "  WARN $msg" -ForegroundColor Yellow }
function Fail([string]$msg) { Write-Host "  FAIL $msg" -ForegroundColor Red }

$banner = "=" * 60
Write-Host $banner
Write-Host " AEGIS-COGNITION Windows Installer" -ForegroundColor White
Write-Host $banner

# --- 0. Preflight --------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())
    .IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Warn "Not running as Administrator. winget installs will likely fail."
    Warn "Re-run from an elevated PowerShell for full automation."
}

# --- 1. uv (Python package manager) --------------------------------------
Step "1/6" "Installing uv (Python package manager)"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    try {
        Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -UseBasicParsing | Invoke-Expression
        # uv installer shims into $env:USERPROFILE\.local\bin for the current session; load it
        $uvBin = Join-Path $env:USERPROFILE ".local\bin"
        if (Test-Path $uvBin) { $env:PATH = "$uvBin;$env:PATH" }
        Ok "uv installed"
    } catch {
        Fail "uv install failed: $_"
        throw
    }
} else {
    Ok "uv already present at $($uv.Source)"
}

# Refresh uv reference
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) { throw "uv not on PATH after install. Restart shell and retry." }

# --- 2. Node.js LTS ------------------------------------------------------
Step "2/6" "Installing Node.js LTS"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements | Out-Null
        Ok "Node.js installed via winget"
    } else {
        Warn "winget unavailable. Skipping Node.js (only needed for browser automation)."
    }
} else {
    Ok "Node.js already present: $(node --version)"
}

# --- 3. ripgrep + ffmpeg (best-effort, non-fatal) -----------------------
Step "3/6" "Installing ripgrep"
if (-not (Get-Command rg -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id BurntSushi.ripgrep.MSVC --silent --accept-source-agreements --accept-package-agreements | Out-Null
        Ok "ripgrep installed via winget"
    } else {
        Warn "winget unavailable; ripgrep not installed."
    }
} else {
    Ok "ripgrep already present"
}

Step "4/6" "Installing ffmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements | Out-Null
        Ok "ffmpeg installed via winget"
    } else {
        Warn "winget unavailable; ffmpeg not installed."
    }
} else {
    Ok "ffmpeg already present"
}

# --- 5. Portable Git (MinGit) for restricted environments ---------------
Step "5/6" "Ensuring Git is available"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    $mingit = Join-Path $env:LOCALAPPDATA "hermes\git"
    if (-not (Test-Path $mingit)) {
        New-Item -ItemType Directory -Path $mingit -Force | Out-Null
        $zip = Join-Path $env:TEMP "MinGit.zip"
        $url = "https://github.com/git-for-windows/git/releases/download/v2.42.0.windows.2/MinGit-2.42.0.2-64-bit.zip"
        Invoke-WebRequest -Uri $url -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $mingit -Force
        Remove-Item $zip
        $env:PATH = "$mingit\cmd;$env:PATH"
        Ok "MinGit unpacked to $mingit"
    }
} else {
    Ok "Git already present: $(git --version)"
}

# --- 6. AEGIS-COGNITION install (Python venv + Rust build) --------------
Step "6/6" "Installing AEGIS-COGNITION into $InstallRoot"

$repoDir = Join-Path $InstallRoot "repo"
$venvDir = Join-Path $InstallRoot "venv"
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null

# Clone or update
if (Test-Path (Join-Path $repoDir ".git")) {
    Push-Location $repoDir
    git pull --ff-only | Out-Null
    Pop-Location
    Ok "Repo updated at $repoDir"
} else {
    git clone $RepoUrl $repoDir
    Ok "Repo cloned to $repoDir"
}

# Verify requirements.txt exists
$req = Join-Path $repoDir "requirements.txt"
if (-not (Test-Path $req)) {
    Fail "requirements.txt missing in $repoDir — refusing to proceed."
    Fail "Rule 3: install scripts MUST use the existing requirements.txt."
    throw "requirements.txt not found"
}

# Create venv at the requested Python version
uv venv $venvDir --python $PythonVersion | Out-Null
$py = Join-Path $venvDir "Scripts\python.exe"
Ok "venv created at $venvDir (Python $PythonVersion)"

# Install from requirements.txt — this is the SINGLE source of truth.
# We deliberately do NOT add per-platform extras; requirements.txt is canonical.
uv pip install --python $py -r $req | Out-Null
Ok "Installed dependencies from requirements.txt"

# Optional: Rust toolchain + cargo build
if (-not $SkipRust) {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        $ru = Join-Path $env:TEMP "rustup-init.exe"
        Invoke-WebRequest -Uri "https://win.rustup.rs" -OutFile $ru
        & $ru -y --default-toolchain 1.98.1 --profile minimal
        Remove-Item $ru
        $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
        Ok "Rust installed via rustup"
    } else {
        Ok "cargo already present: $(cargo --version)"
    }

    if (Get-Command rustup -ErrorAction SilentlyContinue) {
        rustup toolchain install 1.98.1 --profile minimal --component rustfmt --component clippy | Out-Null
        Ok "Rust toolchain 1.98.1 installed/verified"
    }

    $rustDir = Join-Path $repoDir "core\rust"
    if (Test-Path $rustDir) {
        Push-Location $repoDir
        $rustVersion = (& rustc --version)
        if ($rustVersion -notmatch "\b1\.98\.1\b") {
            throw "Project requires Rust 1.98.1; observed: $rustVersion"
        }
        cargo build --release | Out-Null
        Pop-Location
        Ok "Rust core built (release)"
    } else {
        Warn "core\rust not present at $rustDir — skipping cargo build"
    }
} else {
    Warn "Skipped Rust toolchain + cargo build (-SkipRust)"
}

# --- Playwright browser (optional) --------------------------------------
if (-not $SkipBrowser) {
    if (Get-Command node -ErrorAction SilentlyContinue) {
        & $py -m playwright install chromium | Out-Null
        Ok "Playwright chromium installed"
    } else {
        Warn "Node.js not available; skipping playwright install"
    }
} else {
    Warn "Skipped playwright install (-SkipBrowser)"
}

# --- Wrapper: `aegis` on PATH via WindowsApps ---------------------------
# Use .cmd (not .ps1) so cmd.exe, PowerShell, and git-bash all pick it up.
$binDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }

$wrapper = Join-Path $binDir "aegis.cmd"
# .cmd wrapper that fully resolves paths so it works regardless of current dir.
$pyResolved = (Resolve-Path $py).Path
$scriptResolved = (Resolve-Path (Join-Path $repoDir "core\python\aegis_cli.py")).Path
@"
@echo off
"$pyResolved" "$scriptResolved" %*
"@ | Out-File -FilePath $wrapper -Encoding ASCII
Ok "Wrapper installed at $wrapper"

$env:PATH = "$binDir;$env:PATH"
$persistPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($persistPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$binDir;$persistPath", "User")
    Ok "Added $binDir to user PATH (restart shell to persist)"
}

Write-Host ""
Write-Host $banner
Write-Host " Installation complete." -ForegroundColor Green
Write-Host " Open a NEW PowerShell and run:  aegis init" -ForegroundColor Cyan
Write-Host $banner
