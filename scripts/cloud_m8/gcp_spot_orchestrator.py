#!/usr/bin/env python3
"""GCP Spot & On-Demand VM Orchestrator for AEGIS Research & Cloud Verification.

Provisions ephemeral research/verification VMs across Day-1 and quota-scaled profiles
(`c3-standard-22-lssd`, `n2-standard-8`, `e2-standard-2/4`, `t2a-standard-16`) with:
  --no-address (0 public IPs; outbound via Cloud NAT `aegis-nat-gw`, inbound via IAP)
  --boot-disk-type=pd-balanced (preserves regional SSD_TOTAL_GB quota)
  --provisioning-model=SPOT --instance-termination-action=DELETE (or STANDARD)
  --labels=owner=aegis,task=verification,phase=m8,tier=spot

Packages the full repository (preserving `.git` and `desktop/`), uploads to unique
run-scoped GCS paths (`gs://{bucket}/runs/{prefix}/repo.tar.gz`), injects
`startup_compute.sh` without mutating tracked files, monitors sentinels, extracts
receipts, and guarantees immediate VM teardown.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud_m8.provenance import (  # noqa: E402
    DEFAULT_ARTIFACT_REGISTRY,
    DEFAULT_PROJECT,
    DEFAULT_RESEARCH_BUCKET,
    DEFAULT_ZONE,
    LABEL_ATTRIBUTION,
    get_git_head_sha,
    resolve_cli_executable,
    run_cli_cmd,
)

STARTUP_SCRIPT = Path(__file__).resolve().parent / "startup_compute.sh"
DEFAULT_BUCKET = DEFAULT_RESEARCH_BUCKET

TIER_MACHINE_MAP: dict[str, str] = {
    "h1": "e2-standard-2",
    "h2": "e2-standard-4",
    "ctrl-e2": "e2-standard-2",
    "eval-n2": "n2-standard-8",
    "bench-c3": "c3-standard-22-lssd",
    "bench-c3-8": "c3-standard-8-lssd",
    "arm64-t2a": "t2a-standard-16",
}


@dataclass(frozen=True)
class SpotVMConfig:
    project_id: str = DEFAULT_PROJECT
    zone: str = DEFAULT_ZONE
    tier: str = "h2"
    gcs_bucket: str = DEFAULT_BUCKET
    run_id: str = field(default_factory=lambda: f"m8-{int(time.time())}")
    vm_name: str = ""
    machine_type: str = ""
    provisioning_model: str = "SPOT"
    no_address: bool = True
    boot_disk_type: str = "pd-balanced"
    boot_disk_size_gb: int = 100
    head_sha: str = ""
    artifact_registry: str = DEFAULT_ARTIFACT_REGISTRY
    fuzz_seconds: int = 300
    fuzz_runs: int = 0
    secret_names: tuple[str, ...] = ()
    sccache_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.gcs_bucket:
            object.__setattr__(self, "gcs_bucket", DEFAULT_BUCKET)
        if not self.machine_type:
            resolved = TIER_MACHINE_MAP.get(self.tier.lower(), "e2-standard-4")
            object.__setattr__(self, "machine_type", resolved)
        normalized_model = self.provisioning_model.upper()
        if normalized_model not in {"SPOT", "STANDARD"}:
            raise ValueError(f"Unsupported provisioning_model: {self.provisioning_model}")
        object.__setattr__(self, "provisioning_model", normalized_model)

    @property
    def resolved_vm_name(self) -> str:
        if self.vm_name:
            return self.vm_name
        safe_tier = self.tier.lower().replace("_", "-")
        return f"aegis-spot-{safe_tier}-{self.run_id}"

    @property
    def resolved_run_prefix(self) -> str:
        if self.head_sha and self.head_sha != "UNKNOWN_HEAD":
            return f"{self.head_sha[:12]}-{self.run_id}"
        return self.run_id


class GCPSpotOrchestrator:
    """Orchestrates ephemeral Spot/On-Demand VM execution for verification and Phase 0 gates."""

    def __init__(self, root: Path = ROOT, artifacts_dir: Path | str | None = None) -> None:
        self.root = root
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _local_dir(self) -> Path:
        if self.artifacts_dir.resolve() != (self.root / "artifacts").resolve():
            return self.artifacts_dir / ".local"
        return self.root / ".local"

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
        timeout: int | float | None = 300,
    ) -> subprocess.CompletedProcess[str]:
        return run_cli_cmd(
            cmd,
            cwd=self.root,
            env=env,
            check=check,
            capture_output=capture_output,
            timeout=timeout,
        )

    def verify_auth(self) -> tuple[bool, str]:
        """Pre-flight Step 0 verification of gcloud credentials."""
        exe = resolve_cli_executable("gcloud")
        if shutil.which(exe) is None and shutil.which("gcloud") is None:
            return False, "gcloud CLI is not installed or not in PATH."
        try:
            res = self._run_cmd(
                ["gcloud", "auth", "list", "--format=json", "--quiet"],
                check=False,
            )
            if res.returncode != 0:
                return False, f"gcloud auth list failed: {res.stderr.strip()}"
            raw_accounts: Any = json.loads(res.stdout) if res.stdout.strip() else []
            accounts = (
                [cast(dict[str, Any], item) for item in cast(list[Any], raw_accounts) if isinstance(item, dict)]
                if isinstance(raw_accounts, list)
                else []
            )
            active = [a for a in accounts if a.get("status") == "ACTIVE"]
            if not active:
                return False, "No active credentialed account found in gcloud auth list."
            return True, str(active[0].get("account", "unknown"))
        except Exception as exc:
            return False, f"Auth verification exception: {exc}"

    @staticmethod
    def repo_gcs_uri(config: SpotVMConfig) -> str:
        return f"gs://{config.gcs_bucket}/runs/{config.resolved_run_prefix}/repo.tar.gz"

    @staticmethod
    def sentinel_gcs_uri(config: SpotVMConfig) -> str:
        return f"gs://{config.gcs_bucket}/runs/{config.resolved_run_prefix}/DONE-{config.resolved_vm_name}"

    @staticmethod
    def failure_gcs_uri(config: SpotVMConfig) -> str:
        return f"gs://{config.gcs_bucket}/runs/{config.resolved_run_prefix}/FAILURE-{config.resolved_vm_name}"

    @staticmethod
    def failure_log_gcs_uri(config: SpotVMConfig) -> str:
        return (
            f"gs://{config.gcs_bucket}/runs/{config.resolved_run_prefix}/startup-failure-{config.resolved_vm_name}.log"
        )

    @staticmethod
    def evidence_gcs_uri(config: SpotVMConfig) -> str:
        return f"gs://{config.gcs_bucket}/runs/{config.resolved_run_prefix}/evidence-{config.resolved_vm_name}.tar.gz"

    def package_repository(self, output_tar: Path) -> Path:
        """Package repository files including `.git` and `desktop/`, excluding build caches."""
        output_tar.parent.mkdir(parents=True, exist_ok=True)
        exclude_top_dirs = {
            ".venv",
            "venv",
            "target",
            "__pycache__",
            ".pytest_cache",
            ".agents",
            "node_modules",
            ".local",
            "dist",
        }
        exclude_any_parts = {
            ".venv",
            "venv",
            "target",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "node_modules",
        }
        exclude_artifact_subdirs = {"suites", "suite-status", "evidence", "private"}

        def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
            parts = Path(tarinfo.name).parts
            for part in parts:
                if part in exclude_any_parts:
                    return None
            if len(parts) >= 2 and parts[0] == "artifacts" and parts[1] in exclude_artifact_subdirs:
                return None
            if (
                (tarinfo.name.endswith(".pyc") or tarinfo.name.endswith(".lock"))
                and "uv.lock" not in tarinfo.name
                and "Cargo.lock" not in tarinfo.name
            ):
                return None
            return tarinfo

        with tarfile.open(output_tar, "w:gz") as tar:
            for item in sorted(self.root.iterdir(), key=lambda p: p.name):
                if item.name in exclude_top_dirs:
                    continue
                tar.add(item, arcname=item.name, filter=_filter)
        return output_tar

    def upload_archive(
        self,
        archive_path: Path,
        bucket: str,
        dry_run: bool = False,
        *,
        run_prefix: str = "",
        config: SpotVMConfig | None = None,
    ) -> str:
        """Upload repository archive to a unique run-scoped GCS URI."""
        if config is not None:
            destination = self.repo_gcs_uri(config)
        elif run_prefix:
            clean_prefix = run_prefix.strip("/")
            destination = f"gs://{bucket}/runs/{clean_prefix}/repo.tar.gz"
        else:
            fallback_id = archive_path.stem.replace("repo-", "") or f"run-{int(time.time())}"
            destination = f"gs://{bucket}/runs/{fallback_id}/repo.tar.gz"
        if dry_run:
            print(f"[DRY-RUN] Would upload {archive_path} to {destination}")
            return destination
        self._run_cmd(["gcloud", "storage", "cp", str(archive_path), destination, "--quiet"])
        return destination

    def build_create_command(
        self,
        config: SpotVMConfig,
        startup_script_path: Path,
    ) -> list[str]:
        """Build strict gcloud compute instances create arguments (no-address + IAP + NAT ready)."""
        metadata = [
            f"gcs_bucket={config.gcs_bucket}",
            f"gcs_run_prefix=runs/{config.resolved_run_prefix}",
            f"tier={config.tier}",
            f"run_id={config.run_id}",
            f"head_sha={config.head_sha or get_git_head_sha(self.root)}",
            "task=verification",
            f"artifact_registry={config.artifact_registry}",
            f"fuzz_max_total_time={max(1, int(config.fuzz_seconds))}",
            f"fuzz_runs={max(0, int(config.fuzz_runs))}",
        ]
        if config.sccache_enabled and config.gcs_bucket:
            metadata.append(f"sccache_prefix=gs://{config.gcs_bucket}/sccache")
        if config.secret_names:
            # Secret NAMES only; payloads are retrieved inside VM via GCP Secret Manager.
            metadata.append(f"secret_names={'|'.join(config.secret_names)}")

        cmd = [
            "gcloud",
            "compute",
            "instances",
            "create",
            config.resolved_vm_name,
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            f"--machine-type={config.machine_type}",
            f"--provisioning-model={config.provisioning_model}",
        ]
        if config.provisioning_model == "SPOT":
            cmd.append("--instance-termination-action=DELETE")
        if config.no_address:
            cmd.append("--no-address")
        cmd.extend(
            [
                f"--labels={LABEL_ATTRIBUTION}",
                f"--metadata={','.join(metadata)}",
                f"--metadata-from-file=startup-script={startup_script_path}",
                "--scopes=cloud-platform",
                "--image-family=debian-12",
                "--image-project=debian-cloud",
                f"--boot-disk-size={config.boot_disk_size_gb}GB",
                f"--boot-disk-type={config.boot_disk_type}",
                "--quiet",
            ]
        )
        return cmd

    @staticmethod
    def iap_ssh_command(config: SpotVMConfig, remote_cmd: str = "") -> list[str]:
        """Build an IAP-tunneled SSH command for `--no-address` private VMs."""
        cmd = [
            "gcloud",
            "compute",
            "ssh",
            config.resolved_vm_name,
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            "--tunnel-through-iap",
        ]
        if remote_cmd:
            cmd.extend(["--command", remote_cmd])
        return cmd

    def _stage_lf_startup_script(self, config: SpotVMConfig, startup_script_path: Path) -> Path:
        """Return an LF-normalized startup script path without ever mutating tracked sources."""
        if not startup_script_path.is_file():
            return startup_script_path
        script_bytes = startup_script_path.read_bytes()
        if b"\r\n" not in script_bytes:
            return startup_script_path
        staged_dir = self._local_dir / "tmp"
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staged_dir / f"startup-{config.resolved_vm_name}.sh"
        staged_path.write_bytes(script_bytes.replace(b"\r\n", b"\n"))
        return staged_path

    def provision_spot_vm(
        self,
        config: SpotVMConfig,
        startup_script_path: Path,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Provision VM without modifying tracked startup_compute.sh on disk."""
        effective_script = (
            startup_script_path if dry_run else self._stage_lf_startup_script(config, startup_script_path)
        )
        cmd = self.build_create_command(config, effective_script)
        if dry_run:
            print(f"[DRY-RUN] Proposing VM creation command:\n  {' '.join(cmd)}")
            return {
                "status": "DRY_RUN_PROPOSED",
                "vm_name": config.resolved_vm_name,
                "command": cmd,
                "tier": config.tier,
                "machine_type": config.machine_type,
                "no_address": config.no_address,
                "repo_gcs_uri": self.repo_gcs_uri(config),
            }

        print(f"[+] Provisioning VM: {config.resolved_vm_name} ({config.machine_type}, {config.provisioning_model})...")
        proc = self._run_cmd(cmd, check=True)
        return {
            "status": "PROVISIONED",
            "vm_name": config.resolved_vm_name,
            "stdout": proc.stdout,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "repo_gcs_uri": self.repo_gcs_uri(config),
        }

    def monitor_execution(
        self,
        config: SpotVMConfig,
        timeout_seconds: int = 2700,
        poll_interval: int = 30,
        dry_run: bool = False,
        *,
        wait_fn: Callable[[float], None] | None = None,
    ) -> bool:
        """Monitor unique GCS completion or failure sentinel for the VM run."""
        sentinel_uri = self.sentinel_gcs_uri(config)
        failure_uri = self.failure_gcs_uri(config)
        if dry_run:
            print(f"[DRY-RUN] Simulating monitoring for {config.resolved_vm_name} (sentinel {sentinel_uri})")
            return True

        if not config.gcs_bucket:
            print("[!] No GCS bucket provided. Relying on serial port output or timeout.")
            return True

        sleeper = wait_fn or time.sleep
        start_time = time.monotonic()
        print(f"[+] Waiting for remote run to complete (sentinel: {sentinel_uri})...")
        while (time.monotonic() - start_time) < timeout_seconds:
            res_fail = self._run_cmd(["gcloud", "storage", "ls", failure_uri, "--quiet"], check=False)
            if res_fail.returncode == 0:
                print(f"[-] Failure sentinel detected at {failure_uri}! Downloading diagnostics...")
                log_uri = self.failure_log_gcs_uri(config)
                log_dest = self.artifacts_dir / f"startup-failure-{config.resolved_vm_name}.log"
                self._run_cmd(["gcloud", "storage", "cp", log_uri, str(log_dest), "--quiet"], check=False)
                if log_dest.is_file():
                    print(f"[-] Downloaded failure log to {log_dest}")
                return False

            res_done = self._run_cmd(["gcloud", "storage", "ls", sentinel_uri, "--quiet"], check=False)
            if res_done.returncode == 0:
                print("[+] Completion sentinel detected!")
                return True
            sleeper(poll_interval)

        print(f"[-] Timeout reached ({timeout_seconds}s) waiting for {sentinel_uri}")
        return False

    def retrieve_evidence(
        self,
        config: SpotVMConfig,
        output_dir: Path,
        dry_run: bool = False,
    ) -> list[Path]:
        """Download run-scoped evidence archive from GCS and unpack JSON receipts safely."""
        output_dir.mkdir(parents=True, exist_ok=True)
        retrieved: list[Path] = []
        if dry_run or not config.gcs_bucket:
            if dry_run:
                print(f"[DRY-RUN] Simulating retrieval of receipts into {output_dir}")
            return retrieved

        evidence_uri = self.evidence_gcs_uri(config)
        dest_archive = output_dir / f"evidence-{config.resolved_vm_name}.tar.gz"
        res = self._run_cmd(["gcloud", "storage", "cp", evidence_uri, str(dest_archive), "--quiet"], check=False)
        if res.returncode == 0 and dest_archive.is_file():
            with tarfile.open(dest_archive, "r:gz") as tar:
                tar.extractall(output_dir, filter="data")
                for name in tar.getnames():
                    extracted_path = output_dir / name
                    if extracted_path.is_file() and name.endswith(".json"):
                        retrieved.append(extracted_path)
            print(f"[+] Extracted {len(retrieved)} receipts from remote VM.")
        return retrieved

    def teardown_vm(self, config: SpotVMConfig, dry_run: bool = False) -> None:
        """Delete VM immediately to prevent idle spend."""
        cmd = [
            "gcloud",
            "compute",
            "instances",
            "delete",
            config.resolved_vm_name,
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            "--quiet",
        ]
        if dry_run:
            print(f"[DRY-RUN] Proposing teardown command:\n  {' '.join(cmd)}")
            return
        print(f"[+] Tearing down VM {config.resolved_vm_name}...")
        self._run_cmd(cmd, check=False)

    def execute_stage(
        self,
        config: SpotVMConfig,
        *,
        dry_run: bool = False,
        timeout: int = 2700,
    ) -> dict[str, Any]:
        """Execute an end-to-end ephemeral VM verification/benchmark stage."""
        print(f"=== Starting Cloud VM Stage (Tier: {config.tier}, Machine: {config.machine_type}) ===")
        ok, auth_detail = self.verify_auth()
        if not ok and not dry_run:
            raise RuntimeError(f"GCP Authentication check failed: {auth_detail}")

        archive_path = self._local_dir / f"repo-{config.run_id}.tar.gz"
        if not dry_run:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"[+] Packaging repository to {archive_path}...")
            self.package_repository(archive_path)
            if config.gcs_bucket:
                self.upload_archive(archive_path, config.gcs_bucket, dry_run=False, config=config)

        try:
            prov = self.provision_spot_vm(config, STARTUP_SCRIPT, dry_run=dry_run)
            if not dry_run:
                completed = self.monitor_execution(config, timeout_seconds=timeout)
                receipts = self.retrieve_evidence(config, self.artifacts_dir)
            else:
                completed = True
                receipts = []
            return {
                "tier": config.tier,
                "machine_type": config.machine_type,
                "provisioning_model": config.provisioning_model,
                "no_address": config.no_address,
                "status": "COMPLETED" if completed else "FAILED",
                "vm_name": config.resolved_vm_name,
                "repo_gcs_uri": self.repo_gcs_uri(config),
                "provisioning": prov,
                "receipts_count": len(receipts),
            }
        finally:
            if not dry_run:
                self.teardown_vm(config, dry_run=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("CLOUDSDK_CORE_PROJECT", DEFAULT_PROJECT))
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument(
        "--tier",
        choices=sorted(TIER_MACHINE_MAP),
        default="h2",
    )
    parser.add_argument("--machine-type", default="", help="Explicit GCE machine type override")
    parser.add_argument("--provisioning-model", choices=["SPOT", "STANDARD"], default="SPOT")
    parser.add_argument("--allow-external-ip", action="store_true", help="Disable --no-address default")
    parser.add_argument("--boot-disk-type", default="pd-balanced")
    parser.add_argument("--boot-disk-size-gb", type=int, default=100)
    parser.add_argument("--fuzz-seconds", type=int, default=300)
    parser.add_argument("--fuzz-runs", type=int, default=0)
    parser.add_argument("--secret-name", action="append", default=[], help="GCP Secret Manager secret name to inject")
    parser.add_argument("--bucket", default=os.environ.get("AEGIS_M8_GCS_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--dry-run", action="store_true", help="Validate parameters and simulate execution")
    parser.add_argument("--teardown-only", action="store_true", help="Teardown an existing instance by name")
    parser.add_argument("--vm-name", default="")
    parser.add_argument("--timeout", type=int, default=2700)
    parser.add_argument("--artifacts-dir", default=None, help="Directory to store verification receipts")
    args = parser.parse_args()

    config = SpotVMConfig(
        project_id=args.project,
        zone=args.zone,
        tier=args.tier,
        gcs_bucket=args.bucket,
        vm_name=args.vm_name,
        machine_type=args.machine_type,
        provisioning_model=args.provisioning_model,
        no_address=not args.allow_external_ip,
        boot_disk_type=args.boot_disk_type,
        boot_disk_size_gb=args.boot_disk_size_gb,
        fuzz_seconds=args.fuzz_seconds,
        fuzz_runs=args.fuzz_runs,
        secret_names=tuple(args.secret_name),
    )
    orch = GCPSpotOrchestrator(artifacts_dir=args.artifacts_dir)

    if args.teardown_only:
        orch.teardown_vm(config, dry_run=args.dry_run)
        return 0

    result = orch.execute_stage(config, dry_run=args.dry_run, timeout=args.timeout)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in {"COMPLETED", "DRY_RUN_PROPOSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
