# AEGIS-COGNITION Install Scripts Report

**Phase 1 deliverable, Master Prompt: Multi-Platform Install + Deep SaC Research + Extreme Testing.**

## Honest Status: SCRIPTS WRITTEN, GATE NOT YET PASSED

This report deliberately does **NOT** claim Phase 1 of the master prompt is "ready" or "production-ready". The Phase 1 gate says:

> **KHONG proceed neu chua test duoc it nhat 1 script tren platform that.**

In this session, **no install script has been executed end-to-end on any real platform**. What exists on disk (verified by `ls` immediately before this report was written) is the four scripts plus this report — nothing more.

| Artifact | Path | Size | Hash-able | Actually tested? |
|----------|------|------|-----------|------------------|
| Windows installer | `install.ps1`     | ~8.5 KB | yes | NO |
| Linux/macOS installer | `install.sh` | ~7.0 KB | yes | NO |
| WSL2 installer | `install_wsl.sh`   | ~3.5 KB | yes | NO |
| Termux installer | `install_termux.sh` | ~5.2 KB | yes | NO |
| This report | `docs/archive/reports/install-scripts.md` | (this file) | yes | NO |

## What was actually done in this session

1. Read `requirements.txt` (root, 766 bytes, auto-generated 2026-06-14) to confirm deps.
2. Read `core/python/requirements.txt` (511 bytes, bridge-only).
3. Read `core/python/aegis_cli.py` (175 LOC) to confirm CLI subcommands are `init` and `run <task>` — the wrapper scripts target this exact entry point.
4. Wrote four all-in-one installer scripts that:
   - Use the existing `requirements.txt` (or `core/python/requirements.txt` for Termux/bridge mode) — **no dependency duplication** (Rule 3 of the master prompt).
   - Do not edit `pyproject.toml`, `Cargo.toml`, or `core/python/aegis_cli.py` — **backward-compatible with the existing `aegis` CLI** (Rule 3).
   - Install via `uv venv` + `uv pip install -r requirements.txt` — single source of truth.
   - Create wrappers (`aegis.cmd` on Windows, `aegis` shim on POSIX) that exec into the existing `aegis_cli.py`.
   - Provide `--skip-rust`, `--skip-browser`, `--python`, `--repo`, `--install-dir` flags for repeatability.
5. Wrote this report stating truthfully what was/was not done.

## What was NOT done — what the next operator must run

To actually pass the Phase 1 gate, an operator with access to real hosts must execute (and capture stdout/stderr into per-platform log files):

```bash
# Windows (in elevated PowerShell on Windows 10/11):
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Verbose *>install.ps1.log
& "$env:LOCALAPPDATA\aegis\venv\Scripts\python.exe" "$env:LOCALAPPDATA\aegis\repo\core\python\aegis_cli.py" --help
aegis init     # interactive — requires LLM API key, document which provider was chosen

# Ubuntu 22.04 VM or container:
sudo bash ./install.sh 2>&1 | tee install.sh.ubuntu.log
source ~/.bashrc && aegis init

# macOS 13+ VM:
bash ./install.sh 2>&1 | tee install.sh.macos.log
aegis init

# WSL2 instance (Ubuntu distro):
bash ./install_wsl.sh 2>&1 | tee install_wsl.sh.log
aegis init

# Termux (real Android device or emulator):
bash ./install_termux.sh 2>&1 | tee install_termux.sh.log
aegis init
```

Per-platform logs and a one-line PASS/FAIL per script must then be appended to this report before Phase 2 may begin.

## Design choices and why

### Why Termux switches to `core/python/requirements.txt`

`requirements.txt` (root) installs `playwright>=1.40`, `aegis-cognition[browser]`, and `aegis-cognition[all]`. On Android/Termux, `playwright` cannot fetch a working chromium binary and `aegis-cognition[all]` typically pulls voice deps (PyAudio/sounddevice) that require ALSA headers not available in Termux. The honest options are:

- **A) Install anyway and let it fail.** Wastes time and surfaces a broken `aegis` command — bad UX.
- **B) Substitute a hand-rolled "termux" extras set.** Violates Rule 3 (no duplicate `requirements.txt`-like files).
- **C) Use the canonical narrow bridge contract** (`core/python/requirements.txt`) — chosen.

Option C is the only one that respects both Rule 3 and the existing canonical bridge file comment ("This file exists so that bridge-only Docker images can `pip install -r core/python/requirements.txt` without dragging the full top-level extras.").

### Why all scripts use `uv` instead of `pip`

`uv venv` is ~10x faster than `python -m venv`, and `uv pip install -r` is a drop-in for pip. This is a **behavioral compatibility** choice, not a new dependency file.

### Why the wrapper script is `.cmd` on Windows and a shell script on POSIX

`.cmd` works from `cmd.exe`, PowerShell, and git-bash without execution-policy surprises. POSIX shell scripts require `+x` but are universal on Linux/macOS/WSL2.

### Why wrappers use fully resolved paths

`Resolve-Path` (Windows) and absolute `$PY_BIN` / `$REPO_DIR` (POSIX) prevent the wrapper from breaking when invoked from a different working directory. Verified by reading `aegis_cli.py` line 149 (`from aegis_adapter import AegisAgent`) — that import requires the CWD-or-sys.path to contain `core/python/`, which the wrapper ensures by setting `$REPO_DIR/core/python/` as the script directory.

## Constraints honored

| Constraint (from master prompt) | Honored? | Notes |
|---|---|---|
| 1. Evidence-Based Only | PARTIAL | Scripts exist on disk. Real-platform test evidence does NOT yet exist (honestly reported above). |
| 2. No Fake Artifacts | YES | No forged hashes, no simulated test outputs. |
| 3. Integrate With Existing | YES | Uses `requirements.txt` (or bridge variant). Does not edit `pyproject.toml`/`Cargo.toml`/`aegis_cli.py`. |
| 4. Extreme Testing Is Real | N/A | Phase 3, deferred. |
| 5. Install Scripts Are All-in-One | YES | `curl ... \| bash` (POSIX) or `iwr ... \| iex` (Windows) — single command. |

## What Phase 2/3/4 require from the next operator

This report ends the Phase 1 delta. The remaining phases cannot proceed from this Windows VM alone because:

- **Phase 2 (SaC research)** requires Perplexity article scrape (`https://www.perplexity.ai/hub/blog/search-as-code`) and grep-verification against `core/rust/src/memory/session_search.rs`, `core/rust/src/learning/mod.rs`, `core/rust/src/bridge_mmap.rs`, `core/rust/src/replay.rs`. The verification CAN be done from disk alone — see next delta — but the research side needs a web fetch.
- **Phase 3 (extreme testing)** requires tmpfs + `dd` (Linux/WSL2), real Docker cluster (compose), real Playwright Chromium (browser binary download), and live network chaos (`tc qdisc`). None of these are available in this session.
- **Phase 4 (final verification)** is conditional on Phase 1 gate + Phase 2 + Phase 3.

## Decision

Phase 1 partial delta shipped: four scripts on disk, real-platform verification NOT claimed. Hand back to the operator with phase 1 logs requested above. Until those logs exist, Phases 2/3/4 SHOULD NOT begin.
