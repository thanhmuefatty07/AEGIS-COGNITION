"""Shared cloud infrastructure constants, fail-closed archive resolution, and provenance helpers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PROJECT = "aegis-verification-2026"
DEFAULT_REGION = "us-central1"
DEFAULT_ZONE = "us-central1-a"
DEFAULT_RESEARCH_BUCKET = "aegis-research-artifacts-2026"
DEFAULT_ARTIFACT_REGISTRY = "us-central1-docker.pkg.dev/aegis-verification-2026/aegis-research-images"
DEFAULT_NAT_ROUTER = "aegis-nat-router"
DEFAULT_NAT_GW = "aegis-nat-gw"
IAP_TCP_CIDR = "35.235.240.0/20"
VPC_INTERNAL_CIDR = "10.128.0.0/9"
LABEL_ATTRIBUTION = "owner=aegis,task=verification,phase=m8,tier=spot"
DEFAULT_PYTHON_VERSION = "3.14.7"
DEFAULT_RUST_TOOLCHAIN = "1.99.1"

PROVENANCE_SCHEMA = "aegis-research-provenance-v1"


class PrivateArchiveUnavailableError(FileNotFoundError):
    """Raised when no configured, existing external private archive root is available."""


def resolve_private_archive(
    root: Path = ROOT,
    explicit_archive: Path | str | None = None,
) -> Path:
    """Resolve external private archive root strictly fail-closed per AGENTS.md.

    Resolution order:
      1. explicit_archive argument
      2. AEGIS_PRIVATE_ARCHIVE environment variable
      3. <root>/.local/private-archive.path pointer file

    Never falls back to <root>/.local/private-archive or creates directories.
    """
    if explicit_archive is not None and str(explicit_archive).strip():
        candidate = Path(str(explicit_archive).strip())
        if candidate.is_dir():
            return candidate
        raise PrivateArchiveUnavailableError(f"archival pending: explicit archive root unavailable: {candidate}")

    env_target = os.environ.get("AEGIS_PRIVATE_ARCHIVE", "").strip()
    if env_target:
        candidate = Path(env_target)
        if candidate.is_dir():
            return candidate
        raise PrivateArchiveUnavailableError(f"archival pending: AEGIS_PRIVATE_ARCHIVE unavailable: {candidate}")

    pointer = root / ".local" / "private-archive.path"
    if pointer.is_file():
        pointer_text = pointer.read_text(encoding="utf-8").strip()
        if pointer_text:
            candidate = Path(pointer_text)
            if candidate.is_dir():
                return candidate
            raise PrivateArchiveUnavailableError(
                f"archival pending: archive root from {pointer} unavailable: {candidate}"
            )

    raise PrivateArchiveUnavailableError(
        "archival pending: archive root unavailable (set AEGIS_PRIVATE_ARCHIVE, "
        ".local/private-archive.path, or pass --private-archive)"
    )


def resolve_pinned_rust_toolchain(root: Path = ROOT) -> str:
    """Read pinned Rust toolchain channel from rust-toolchain.toml."""
    toolchain_file = root / "rust-toolchain.toml"
    if toolchain_file.is_file():
        try:
            parsed = tomllib.loads(toolchain_file.read_text(encoding="utf-8"))
            channel = parsed.get("toolchain", {}).get("channel")
            if isinstance(channel, str) and channel.strip():
                return channel.strip()
        except Exception:
            pass
    return DEFAULT_RUST_TOOLCHAIN


def resolve_cli_executable(command_name: str) -> str:
    """Resolve executable path across POSIX and Windows (.cmd/.bat/.exe)."""
    exe = shutil.which(command_name)
    if not exe and sys.platform == "win32" and not command_name.lower().endswith((".cmd", ".bat", ".exe")):
        exe = (
            shutil.which(f"{command_name}.cmd")
            or shutil.which(f"{command_name}.bat")
            or shutil.which(f"{command_name}.exe")
        )
    return exe or command_name


def run_cli_cmd(
    cmd: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
    timeout: int | float | None = 300,
) -> subprocess.CompletedProcess[str]:
    """Execute CLI subprocess with consistent metrics env and Windows cmd handling."""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    existing_metrics = full_env.get("CLOUDSDK_METRICS_ENVIRONMENT", "")
    full_env["CLOUDSDK_METRICS_ENVIRONMENT"] = (
        f"{existing_metrics} datacloud.antigravity".strip() if existing_metrics else "datacloud.antigravity"
    )
    resolved_cmd = list(cmd)
    if resolved_cmd:
        resolved_cmd[0] = resolve_cli_executable(resolved_cmd[0])
        if sys.platform == "win32" and resolved_cmd[0].lower().endswith((".cmd", ".bat")):
            for idx in range(1, len(resolved_cmd)):
                if resolved_cmd[idx].startswith("--labels="):
                    val = resolved_cmd[idx][len("--labels=") :].strip("\"'")
                    val = val.replace("^,", ",").replace(",", "^,")
                    resolved_cmd[idx] = f"--labels={val}"
    return subprocess.run(
        resolved_cmd,
        cwd=cwd,
        check=check,
        capture_output=capture_output,
        text=True,
        env=full_env,
        timeout=timeout,
    )


def get_git_head_sha(root: Path = ROOT) -> str:
    """Return current Git commit HEAD SHA or UNKNOWN_HEAD."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        sha = res.stdout.strip()
        return sha or "UNKNOWN_HEAD"
    except Exception:
        return "UNKNOWN_HEAD"


def get_git_worktree_status(root: Path = ROOT) -> str:
    """Return CLEAN, DIRTY, or UNKNOWN for the Git checkout at root."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        return "CLEAN" if not res.stdout.strip() else "DIRTY"
    except Exception:
        return "UNKNOWN"


def sha256_file(path: Path) -> str:
    """Compute hex SHA-256 digest of a file in bounded chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def collect_hardware_fingerprint() -> dict[str, Any]:
    """Collect non-identifying host hardware/OS metadata for provenance."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 0,
    }


def build_provenance_manifest(
    source_dir: Path,
    *,
    root: Path = ROOT,
    tier: str = "gcp-us-central1",
    exact_command: str = "",
    toolchain_versions: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    exclude_names: frozenset[str] = frozenset({"PROVENANCE.json"}),
) -> dict[str, Any]:
    """Build PROVENANCE.json dictionary indexing all regular files under source_dir."""
    files_manifest: list[dict[str, Any]] = []
    total_bytes = 0
    if source_dir.is_dir():
        for file_path in sorted(source_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(source_dir).as_posix()
            if rel in exclude_names:
                continue
            size = file_path.stat().st_size
            total_bytes += size
            files_manifest.append(
                {
                    "relative_path": rel,
                    "size_bytes": size,
                    "sha256": sha256_file(file_path),
                }
            )

    return {
        "schema": PROVENANCE_SCHEMA,
        "truth_claim": False,
        "git_head_sha": get_git_head_sha(root),
        "worktree_status": get_git_worktree_status(root),
        "utc_timestamp": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "exact_command": exact_command,
        "tier": tier,
        "hardware_fingerprint": collect_hardware_fingerprint(),
        "toolchain_versions": toolchain_versions
        or {
            "python": platform.python_version(),
            "rust_pinned": resolve_pinned_rust_toolchain(root),
        },
        "units": units or {"size_bytes": "bytes", "latency": "ms"},
        "file_count": len(files_manifest),
        "total_bytes": total_bytes,
        "files": files_manifest,
    }


def sync_verified_archive(
    source_dir: Path,
    archive_root: Path,
    run_dirname: str,
    *,
    root: Path = ROOT,
    tier: str = "gcp-us-central1",
    exact_command: str = "",
    file_glob: str = "*",
    on_verified_cleanup: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Copy artifacts first, write PROVENANCE.json, verify SHA-256/bytes, then optionally clean."""
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source artifacts directory does not exist: {source_dir}")
    if not archive_root.is_dir():
        raise PrivateArchiveUnavailableError(f"archival pending: archive root unavailable: {archive_root}")

    run_dest = archive_root / "runs" / run_dirname
    run_dest.mkdir(parents=True, exist_ok=True)

    source_files = sorted(p for p in source_dir.rglob(file_glob) if p.is_file())
    copied_entries: list[tuple[Path, Path, str, int]] = []
    for src in source_files:
        rel = src.relative_to(source_dir)
        dst = run_dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_digest = sha256_file(src)
        src_size = src.stat().st_size
        shutil.copy2(src, dst)
        copied_entries.append((src, dst, src_digest, src_size))

    manifest = build_provenance_manifest(
        run_dest,
        root=root,
        tier=tier,
        exact_command=exact_command,
    )
    manifest_path = run_dest / "PROVENANCE.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Verify every copied file byte-size and SHA-256 against source before any cleanup
    mismatches: list[str] = []
    for _src, dst, expected_sha, expected_size in copied_entries:
        if not dst.is_file():
            mismatches.append(f"missing:{dst}")
            continue
        actual_size = dst.stat().st_size
        actual_sha = sha256_file(dst)
        if actual_size != expected_size or actual_sha != expected_sha:
            mismatches.append(f"digest_mismatch:{dst}")

    if mismatches:
        raise RuntimeError(f"Archive copy verification failed: {mismatches}")

    if on_verified_cleanup is not None:
        on_verified_cleanup()

    return {
        "verified": True,
        "destination": str(run_dest),
        "manifest_path": str(manifest_path),
        "file_count": len(copied_entries),
        "total_bytes": sum(entry[3] for entry in copied_entries),
        "mismatches": 0,
    }
