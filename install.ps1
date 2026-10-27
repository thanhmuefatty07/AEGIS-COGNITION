# AEGIS-COGNITION Windows Installer
# All-in-one installer that builds the locked project wheel and canonical CLIs.
#
# Usage (run in an elevated PowerShell):
#   iwr -useb https://aegis-cognition.ai/install.ps1 | iex
#   # or locally:
#   .\install.ps1
#
# End-to-end execution remains unverified on Windows; no platform pass is claimed.

[CmdletBinding()]
param(
    [switch]$SkipRust,        # skip the standalone Rust CLI; the native Python wheel still needs Rust
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

function Invoke-NativeChecked([string]$Name, [string]$Executable, [string[]]$Arguments) {
    $output = & $Executable @Arguments
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        throw "$Name did not return a native exit code."
    }
    if ($exitCode -ne 0) {
        throw "$Name failed with exit code $exitCode."
    }
    return $output
}

function Find-VsDevCommand {
    $cl = Get-Command cl -ErrorAction SilentlyContinue
    $link = Get-Command link -ErrorAction SilentlyContinue
    if ($cl -and $link -and $env:WindowsSDKVersion) {
        return $null
    }

    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $vswhere = Join-Path $programFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) {
        throw "MSVC and a Windows SDK are required to build the native Python wheel; Visual Studio Build Tools were not found."
    }
    $installPath = Invoke-NativeChecked "Visual Studio lookup" $vswhere @(
        "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"
    ) | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($installPath)) {
        throw "MSVC Build Tools were not found; install the x64 C++ build tools and Windows SDK."
    }
    $vsDevCmd = Join-Path $installPath "Common7\Tools\VsDevCmd.bat"
    if (-not (Test-Path $vsDevCmd)) {
        throw "Visual Studio developer command file not found: $vsDevCmd"
    }
    return $vsDevCmd
}

function Invoke-VsNativeChecked(
    [string]$Name,
    [string]$Executable,
    [string]$Arguments,
    [string]$Repository,
    [string]$VsDevCmd,
    [string]$PythonVersion
) {
    $environmentNames = @(
        "AEGIS_INSTALLER_EXECUTABLE", "AEGIS_INSTALLER_REPOSITORY", "AEGIS_INSTALLER_VSDEVCMD",
        "AEGIS_INSTALLER_PYTHON"
    )
    $previousEnvironment = @{}
    foreach ($environmentName in $environmentNames) {
        $previousEnvironment[$environmentName] = [Environment]::GetEnvironmentVariable($environmentName, "Process")
    }

    try {
        $env:AEGIS_INSTALLER_EXECUTABLE = $Executable
        $env:AEGIS_INSTALLER_REPOSITORY = $Repository
        $env:AEGIS_INSTALLER_VSDEVCMD = $VsDevCmd
        $env:AEGIS_INSTALLER_PYTHON = $PythonVersion

        $command = 'cd /d "%AEGIS_INSTALLER_REPOSITORY%"'
        if ($VsDevCmd) {
            $command += ' && call "%AEGIS_INSTALLER_VSDEVCMD%" -no_logo -arch=x64 -host_arch=x64 >nul 2>&1'
        }
        $command += ' && if not defined WindowsSDKVersion (exit /b 1) else (where cl >nul && where link >nul && "%AEGIS_INSTALLER_EXECUTABLE%" ' + $Arguments + ')'
        $systemCommandInterpreter = Join-Path ([Environment]::SystemDirectory) "cmd.exe"
        if (-not (Test-Path -LiteralPath $systemCommandInterpreter -PathType Leaf)) {
            throw "Windows command interpreter was not found."
        }
        Invoke-NativeChecked $Name $systemCommandInterpreter @("/d", "/s", "/c", $command) | Out-Null
    } finally {
        foreach ($environmentName in $environmentNames) {
            $previousValue = $previousEnvironment[$environmentName]
            if ($null -eq $previousValue) {
                Remove-Item "Env:$environmentName" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($environmentName, $previousValue, "Process")
            }
        }
    }
}

$banner = "=" * 60
Write-Host $banner

if ($PythonVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "PythonVersion must be a pinned X.Y.Z release, observed: $PythonVersion"
}
Write-Host " AEGIS-COGNITION Windows Installer" -ForegroundColor White
Write-Host $banner

# --- 0. Preflight --------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
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
    try {
        Invoke-NativeChecked "git pull" (Get-Command git).Source @("pull", "--ff-only") | Out-Null
    } finally {
        Pop-Location
    }
    Ok "Repo updated at $repoDir"
} else {
    Invoke-NativeChecked "git clone" (Get-Command git).Source @("clone", $RepoUrl, $repoDir) | Out-Null
    Ok "Repo cloned to $repoDir"
}

# The root toolchain file pins the compiler used for both the Maturin wheel and CLI.
$toolchainFile = Join-Path $repoDir "rust-toolchain.toml"
if (-not (Test-Path $toolchainFile)) {
    throw "Pinned Rust toolchain file not found: $toolchainFile"
}
$toolchainText = Get-Content -Raw -LiteralPath $toolchainFile
$toolchainMatch = [regex]::Match($toolchainText, '(?m)^\s*channel\s*=\s*"([^\"]+)"\s*$')
if (-not $toolchainMatch.Success) {
    throw "Could not read the pinned Rust channel from $toolchainFile"
}
$rustToolchain = $toolchainMatch.Groups[1].Value

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
        $ru = Join-Path $env:TEMP "rustup-init.exe"
        Invoke-WebRequest -Uri "https://win.rustup.rs" -OutFile $ru
        try {
            Invoke-NativeChecked "rustup-init" $ru @("-y", "--default-toolchain", $rustToolchain, "--profile", "minimal") | Out-Null
        } finally {
            Remove-Item -LiteralPath $ru -ErrorAction SilentlyContinue
        }
        $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
        Ok "Rust installed via rustup"
    }
}

$rustup = Get-Command rustup -ErrorAction SilentlyContinue
if ($rustup) {
    Invoke-NativeChecked "rustup toolchain install" $rustup.Source @(
        "toolchain", "install", $rustToolchain, "--profile", "minimal", "--component", "rustfmt", "--component", "clippy"
    ) | Out-Null
    Ok "Rust toolchain $rustToolchain installed/verified"
}

$rustc = Get-Command rustc -ErrorAction SilentlyContinue
$cargo = Get-Command cargo -ErrorAction SilentlyContinue
if (-not $rustc -or -not $cargo) {
    throw "Rust $rustToolchain is required to build the native Python wheel; install Rust and retry."
}
Push-Location $repoDir
try {
    $rustVersion = Invoke-NativeChecked "rustc --version" $rustc.Source @("--version")
    if (($rustVersion -join " ") -notmatch "\b$([regex]::Escape($rustToolchain))\b") {
        throw "Project requires Rust $rustToolchain; observed: $rustVersion"
    }

    $vsDevCmd = Find-VsDevCommand
    if (-not $SkipRust) {
        Invoke-VsNativeChecked "cargo build" $cargo.Source `
            'build --locked --release --bin aegis-nerve-cli --manifest-path "%AEGIS_INSTALLER_REPOSITORY%\core\rust\Cargo.toml"' `
            $repoDir $vsDevCmd $PythonVersion
        $rustCli = Join-Path $repoDir "target\release\aegis-nerve-cli.exe"
        if (-not (Test-Path $rustCli)) {
            throw "cargo succeeded but the standalone CLI was not produced at $rustCli"
        }
        Ok "Standalone Rust CLI built with Rust $rustToolchain"
    } else {
        Warn "Skipped standalone Rust CLI build (-SkipRust); Rust remains required for the native Python wheel."
    }

    $uvProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
    try {
        $env:UV_PROJECT_ENVIRONMENT = $venvDir
        $syncArguments = 'sync --locked --no-default-groups --no-editable --reinstall-package aegis-cognition --python "%AEGIS_INSTALLER_PYTHON%" --project "%AEGIS_INSTALLER_REPOSITORY%"'
        if (-not $SkipBrowser) {
            $syncArguments += " --extra browser"
        }
        Invoke-VsNativeChecked "uv sync" $uv.Source $syncArguments $repoDir $vsDevCmd $PythonVersion
    } finally {
        if ($null -eq $uvProjectEnvironment) {
            Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
        } else {
            $env:UV_PROJECT_ENVIRONMENT = $uvProjectEnvironment
        }
    }
} finally {
    Pop-Location
}
Ok "Locked project wheel and dependencies installed at $venvDir"

$py = Join-Path $venvDir "Scripts\python.exe"
$aegisExe = Join-Path $venvDir "Scripts\aegis.exe"
$desktopExe = Join-Path $venvDir "Scripts\aegis-desktop.exe"
foreach ($requiredPath in @($py, $aegisExe, $desktopExe)) {
    if (-not (Test-Path $requiredPath)) {
        throw "uv sync succeeded but a required installed entry point is missing: $requiredPath"
    }
}
Invoke-NativeChecked "aegis version smoke" $aegisExe @("version") | Out-Null
Invoke-NativeChecked "aegis verify help smoke" $aegisExe @("verify", "--help") | Out-Null
Invoke-NativeChecked "aegis-desktop help smoke" $desktopExe @("--help") | Out-Null
$runOutput = & $aegisExe run
$runExitCode = $LASTEXITCODE
if ($runExitCode -ne 2 -or ($runOutput -join " ") -notmatch "Usage: aegis run") {
    throw "aegis run usage smoke failed; expected exit code 2 and usage output."
}
Ok "Installed aegis and aegis-desktop entry points passed offline smoke checks"

# Explicit native extension import check from outside repository (O02)
$verifyScript = "import sysconfig, aegis_cognition; from aegis_cognition import aegis_nerve; " + `
    "platlib = sysconfig.get_path('platlib'); " + `
    "origin = getattr(aegis_nerve, '__file__', ''); " + `
    "assert origin and (origin.startswith(platlib) or 'site-packages' in origin), f'Native extension not loaded from site-packages: {origin}'; " + `
    "print(f'Native wheel verified from {origin}')"
Push-Location $InstallRoot
try {
    Invoke-NativeChecked "native wheel import smoke" $py @("-c", $verifyScript) | Out-Null
} finally {
    Pop-Location
}
Ok "Canonical native wheel imported cleanly from site-packages"

# --- Playwright browser (optional) --------------------------------------
if (-not $SkipBrowser) {
    Invoke-NativeChecked "Playwright Chromium install" $py @("-m", "playwright", "install", "chromium") | Out-Null
    Ok "Playwright Chromium installed"
} else {
    Warn "Skipped playwright install (-SkipBrowser)"
}

# --- Wrapper: `aegis` on PATH via WindowsApps ---------------------------
# Use .cmd (not .ps1) so cmd.exe, PowerShell, and git-bash all pick it up.
$binDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }

$wrapper = Join-Path $binDir "aegis.cmd"
# .cmd wrapper that fully resolves paths so it works regardless of current dir.
$aegisResolved = (Resolve-Path $aegisExe).Path
$desktopResolved = (Resolve-Path $desktopExe).Path
@"
@echo off
"$aegisResolved" %*
"@ | Out-File -FilePath $wrapper -Encoding ASCII
Ok "Wrapper installed at $wrapper"

$desktopWrapper = Join-Path $binDir "aegis-desktop.cmd"
@"
@echo off
"$desktopResolved" %*
"@ | Out-File -FilePath $desktopWrapper -Encoding ASCII
Ok "Wrapper installed at $desktopWrapper"

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
