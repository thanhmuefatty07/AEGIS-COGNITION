<#
Install the reviewed Codex Web GPT desktop launcher without starting it.

The launcher is the Windows browser host required by the upstream bridge. The
installer is pinned to v5.0.2 and its published SHA-256. Use -Launch only when
you are ready to sign in interactively in the new private browser profile.
#>
[CmdletBinding()]
param(
    [switch]$Launch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$version = "5.0.2"
$asset = "codex-web-gpt-$version-win-x64.exe"
$expectedSha256 = "2b444c6490822193774b107ae5ea95f99994aa4ddbc5861a514805e3f4586b8d"
$url = "https://github.com/miuuyy/codex-chatgpt-web/releases/download/v$version/$asset"
$registryPath = "HKCU:\Software\d1a6026a-6210-588e-9a2b-da3936f94e02"
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("aegis-chatgpt-web-launcher-" + [guid]::NewGuid().ToString("N"))
$installer = Join-Path $tempRoot $asset

New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
try {
    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expectedSha256) {
        throw "SHA-256 mismatch for $asset. Expected $expectedSha256, got $actual."
    }
    $process = Start-Process -FilePath $installer -ArgumentList "/S", "/currentuser" -WindowStyle Hidden -Wait -PassThru
    $installReady = Test-Path -LiteralPath $registryPath
    if ($process.ExitCode -ne 0 -and -not $installReady) {
        throw "Launcher installer exited with code $($process.ExitCode) and published no install entry"
    }
    if ($process.ExitCode -ne 0) {
        Write-Warning "Launcher installer returned code $($process.ExitCode), but its verified user install entry exists; continuing."
    }
    if (-not $installReady) { throw "Launcher did not publish its user install registry entry" }
    $installLocation = [string](Get-ItemPropertyValue -LiteralPath $registryPath -Name "InstallLocation")
    if (-not [IO.Path]::IsPathFullyQualified($installLocation)) { throw "Launcher install path is not absolute" }
    $executable = Join-Path $installLocation "Codex Web GPT.exe"
    if (-not (Test-Path -LiteralPath $executable)) { throw "Installed launcher executable was not found: $executable" }
    Write-Output "Installed Codex Web GPT v$version at $executable"
    if ($Launch) {
        Start-Process -FilePath $executable
        Write-Output "Launcher started. Complete sign-in and browser setup in its window."
    } else {
        Write-Output "Launcher was not started. Re-run with -Launch when you are ready for interactive sign-in."
    }
} finally {
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
}
