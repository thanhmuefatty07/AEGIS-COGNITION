<#
Install the pinned, Windows x64 codex-chatgpt-web bridge for local AEGIS use.

This script downloads only the published release asset, verifies its SHA-256,
extracts into a versioned per-user directory, and does not start the browser or
change Codex settings.  Sign-in remains an explicit user action.
#>
[CmdletBinding()]
param(
    [string]$Version = "5.0.2",
    [string]$InstallRoot = "$(Join-Path $env:LOCALAPPDATA 'AEGIS-COGNITION\third-party\codex-chatgpt-web')"
)

$ErrorActionPreference = "Stop"
$asset = "codex-chatgpt-web-windows-amd64.zip"
$expectedSha256 = "0137ed9a80a2412ea5ff00ca0569826846bc307003e638bf1543f21ef99c8e1f"
$downloadUrl = "https://github.com/miuuyy/codex-chatgpt-web/releases/download/v$Version/$asset"
$target = Join-Path ([IO.Path]::GetFullPath($InstallRoot)) $Version
$launcher = Join-Path $target "bin\codex-chatgpt-web.cmd"

if ($Version -ne "5.0.2") {
    throw "This installer is pinned to the reviewed release v5.0.2; update the checksum before selecting another version."
}
if (Test-Path -LiteralPath $launcher) {
    & $launcher --version
    if ($LASTEXITCODE -ne 0) { throw "Existing ChatGPT Web bridge failed its version check: $launcher" }
    Write-Output "ChatGPT Web bridge already installed at $target"
    exit 0
}

$root = [IO.Path]::GetFullPath($InstallRoot)
New-Item -ItemType Directory -Force -Path $root | Out-Null
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("aegis-chatgpt-web-" + [guid]::NewGuid().ToString("N"))
$zipPath = Join-Path $tempRoot $asset
$stage = Join-Path $tempRoot "stage"
New-Item -ItemType Directory -Force -Path $tempRoot,$stage | Out-Null
try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath -UseBasicParsing
    $actualSha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "SHA-256 mismatch for $asset. Expected $expectedSha256, got $actualSha256."
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($entry in $archive.Entries) {
            $relative = $entry.FullName.Replace('/', '\')
            if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains("..\")) {
                throw "Refusing unsafe archive path: $($entry.FullName)"
            }
            $destination = [IO.Path]::GetFullPath((Join-Path $stage $relative))
            $stagePrefix = $stage.TrimEnd('\') + '\'
            if (-not $destination.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing archive path outside staging directory: $($entry.FullName)"
            }
            if ($entry.FullName.EndsWith('/')) {
                New-Item -ItemType Directory -Force -Path $destination | Out-Null
            } else {
                New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($destination)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $false)
            }
        }
    } finally {
        $archive.Dispose()
    }

    $stagedLauncher = Join-Path $stage "bin\codex-chatgpt-web.cmd"
    $manifest = Join-Path $stage "manifest.json"
    if (-not (Test-Path -LiteralPath $stagedLauncher) -or -not (Test-Path -LiteralPath $manifest)) {
        throw "Release archive is missing the launcher or manifest."
    }
    $manifestObject = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    if ($manifestObject.appVersion -ne $Version -or $manifestObject.platform -ne "win32" -or $manifestObject.arch -ne "x64") {
        throw "Release manifest does not match the pinned Windows x64 package."
    }
    if (Test-Path -LiteralPath $target) {
        throw "Refusing to overwrite an existing install directory: $target"
    }
    Move-Item -LiteralPath $stage -Destination $target
    & $launcher --version
    if ($LASTEXITCODE -ne 0) { throw "Installed ChatGPT Web bridge failed its version check: $launcher" }
    Write-Output "Installed ChatGPT Web bridge v$Version at $target"
    Write-Output "Next: launch the bridge UI, sign in there, run its browser check, then set llm.provider = chatgpt-web in ~/.aegis/config.toml."
} finally {
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
}
