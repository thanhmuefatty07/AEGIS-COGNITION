#!/usr/bin/env python3
"""GCP Spot VM Orchestrator for AEGIS M8 Cloud Verification.

Provisions Spot VMs (e2-standard-4 for H2/heavy, e2-standard-2 for H1) with:
  --provisioning-model=SPOT
  --instance-termination-action=DELETE
  --labels=owner=aegis,task=verification,phase=m8,tier=spot

Packages the project repository, injects startup_compute.sh, monitors
execution, extracts evidence receipts, and triggers immediate teardown.
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STARTUP_SCRIPT = Path(__file__).resolve().parent / "startup_compute.sh"
DEFAULT_ZONE = "us-central1-a"
DEFAULT_REGION = "us-central1"
DEFAULT_BUCKET = "aegis-verification-2026_cloudbuild"
LABEL_ATTRIBUTION = "owner=aegis,task=verification,phase=m8,tier=spot"


@dataclass(frozen=True)
class SpotVMConfig:
    project_id: str
    zone: str = DEFAULT_ZONE
    tier: str = "h2"
    gcs_bucket: str = DEFAULT_BUCKET
    run_id: str = field(default_factory=lambda: f"m8-{int(time.time())}")
    vm_name: str = ""

    def __post_init__(self) -> None:
        if not self.gcs_bucket:
            object.__setattr__(self, "gcs_bucket", DEFAULT_BUCKET)

    @property
    def machine_type(self) -> str:
        return "e2-standard-4" if self.tier.lower() == "h2" else "e2-standard-2"

    @property
    def resolved_vm_name(self) -> str:
        if self.vm_name:
            return self.vm_name
        return f"aegis-spot-{self.tier.lower()}-{self.run_id}"


class GCPSpotOrchestrator:
    """Orchestrates ephemeral Spot VM execution for heavy verification gates."""

    def __init__(self, root: Path = ROOT, artifacts_dir: Path | str | None = None) -> None:
        self.root = root
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
        timeout: int | float | None = 300,
    ) -> subprocess.CompletedProcess[str]:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        # Apply DAK metrics environment attribution
        existing_metrics = full_env.get("CLOUDSDK_METRICS_ENVIRONMENT", "")
        full_env["CLOUDSDK_METRICS_ENVIRONMENT"] = (
            f"{existing_metrics} datacloud.antigravity".strip()
            if existing_metrics
            else "datacloud.antigravity"
        )
        resolved_cmd = list(cmd)
        if resolved_cmd:
            exe = shutil.which(resolved_cmd[0])
            if not exe and sys.platform == "win32" and not resolved_cmd[0].lower().endswith((".cmd", ".bat", ".exe")):
                exe = (
                    shutil.which(f"{resolved_cmd[0]}.cmd")
                    or shutil.which(f"{resolved_cmd[0]}.bat")
                    or shutil.which(f"{resolved_cmd[0]}.exe")
                )
            if exe:
                resolved_cmd[0] = exe
        return subprocess.run(
            resolved_cmd,
            cwd=self.root,
            check=check,
            capture_output=capture_output,
            text=True,
            env=full_env,
            timeout=timeout,
        )

    def verify_auth(self) -> tuple[bool, str]:
        """Pre-flight Step 0 verification of gcloud credentials."""
        if shutil.which("gcloud") is None:
            return False, "gcloud CLI is not installed or not in PATH."
        try:
            res = self._run_cmd(
                ["gcloud", "auth", "list", "--format=json", "--quiet"],
                check=False,
            )
            if res.returncode != 0:
                return False, f"gcloud auth list failed: {res.stderr.strip()}"
            accounts = json.loads(res.stdout) if res.stdout.strip() else []
            active = [a for a in accounts if a.get("status") == "ACTIVE"]
            if not active:
                return False, "No active credentialed account found in gcloud auth list."
            return True, active[0].get("account", "unknown")
        except Exception as exc:
            return False, f"Auth verification exception: {exc}"

    def package_repository(self, output_tar: Path) -> Path:
        """Packages tracked and essential repository files for remote compute."""
        output_tar.parent.mkdir(parents=True, exist_ok=True)
        exclude_dirs = {
            ".git",
            ".venv",
            "venv",
            "target",
            "__pycache__",
            ".pytest_cache",
            ".agents",
            "node_modules",
            ".local",
            "desktop",
        }

        def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
            parts = Path(tarinfo.name).parts
            for ex in exclude_dirs:
                if ex in parts:
                    return None
            if (
                (tarinfo.name.endswith(".pyc") or tarinfo.name.endswith(".lock"))
                and "uv.lock" not in tarinfo.name
                and "Cargo.lock" not in tarinfo.name
            ):
                return None
            return tarinfo

        with tarfile.open(output_tar, "w:gz") as tar:
            for item in self.root.iterdir():
                if item.name in exclude_dirs:
                    continue
                tar.add(item, arcname=item.name, filter=_filter)
        return output_tar

    def upload_archive(self, archive_path: Path, bucket: str, dry_run: bool = False) -> str:
        """Uploads repository archive to GCS bucket."""
        destination = f"gs://{bucket}/repo.tar.gz"
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
        """Builds strict, verified gcloud compute instances create arguments."""
        metadata = [
            f"gcs_bucket={config.gcs_bucket}",
            f"tier={config.tier}",
            f"run_id={config.run_id}",
            "task=verification",
        ]
        cmd = [
            "gcloud",
            "compute",
            "instances",
            "create",
            config.resolved_vm_name,
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            f"--machine-type={config.machine_type}",
            "--provisioning-model=SPOT",
            "--instance-termination-action=DELETE",
            f"--labels={LABEL_ATTRIBUTION}",
            f"--metadata={','.join(metadata)}",
            f"--metadata-from-file=startup-script={startup_script_path}",
            "--scopes=cloud-platform",
            "--image-family=debian-12",
            "--image-project=debian-cloud",
            "--boot-disk-size=50GB",
            "--boot-disk-type=pd-ssd",
            "--quiet",
        ]
        return cmd

    def provision_spot_vm(
        self,
        config: SpotVMConfig,
        startup_script_path: Path,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Provisions Spot VM with SPOT policy and DELETE termination action."""
        # Ensure POSIX LF line endings for startup script in guest
        if startup_script_path.is_file():
            script_bytes = startup_script_path.read_bytes()
            if b"\r\n" in script_bytes:
                startup_script_path.write_bytes(script_bytes.replace(b"\r\n", b"\n"))

        cmd = self.build_create_command(config, startup_script_path)
        if dry_run:
            print(f"[DRY-RUN] Proposing VM creation command:\n  {' '.join(cmd)}")
            return {
                "status": "DRY_RUN_PROPOSED",
                "vm_name": config.resolved_vm_name,
                "command": cmd,
                "tier": config.tier,
                "machine_type": config.machine_type,
            }

        print(f"[+] Provisioning Spot VM: {config.resolved_vm_name} ({config.machine_type})...")
        proc = self._run_cmd(cmd, check=True)
        return {
            "status": "PROVISIONED",
            "vm_name": config.resolved_vm_name,
            "stdout": proc.stdout,
            "created_at_utc": datetime.now(UTC).isoformat(),
        }

    def monitor_execution(
        self,
        config: SpotVMConfig,
        timeout_seconds: int = 2700,
        poll_interval: int = 30,
        dry_run: bool = False,
    ) -> bool:
        """Monitors GCS completion sentinel or instance lifecycle."""
        if dry_run:
            print(f"[DRY-RUN] Simulating monitoring for {config.resolved_vm_name} (sentinel gs://{config.gcs_bucket}/DONE-{config.resolved_vm_name})")
            return True

        if not config.gcs_bucket:
            print("[!] No GCS bucket provided. Relying on serial port output or timeout.")
            return True

        sentinel_uri = f"gs://{config.gcs_bucket}/DONE-{config.resolved_vm_name}"
        failure_uri = f"gs://{config.gcs_bucket}/FAILURE-{config.resolved_vm_name}"
        start_time = time.time()
        print(f"[+] Waiting for remote run to complete (sentinel: {sentinel_uri})...")
        while time.time() - start_time < timeout_seconds:
            # Check for failure sentinel first to fail-fast
            res_fail = self._run_cmd(
                ["gcloud", "storage", "ls", failure_uri, "--quiet"],
                check=False,
            )
            if res_fail.returncode == 0:
                print(f"[-] Failure sentinel detected at {failure_uri}! Downloading diagnostics...")
                log_uri = f"gs://{config.gcs_bucket}/startup-failure-{config.resolved_vm_name}.log"
                log_dest = self.artifacts_dir / f"startup-failure-{config.resolved_vm_name}.log"
                self._run_cmd(
                    ["gcloud", "storage", "cp", log_uri, str(log_dest), "--quiet"],
                    check=False,
                )
                if log_dest.is_file():
                    print(f"[-] Downloaded failure log to {log_dest}")
                return False

            res = self._run_cmd(
                ["gcloud", "storage", "ls", sentinel_uri, "--quiet"],
                check=False,
            )
            if res.returncode == 0:
                print("[+] Completion sentinel detected!")
                return True
            time.sleep(poll_interval)

        print(f"[-] Timeout reached ({timeout_seconds}s) waiting for {sentinel_uri}")
        return False

    def retrieve_evidence(
        self,
        config: SpotVMConfig,
        output_dir: Path,
        dry_run: bool = False,
    ) -> list[Path]:
        """Downloads evidence archive from GCS and unpacks receipts."""
        output_dir.mkdir(parents=True, exist_ok=True)
        retrieved: list[Path] = []
        if dry_run:
            print(f"[DRY-RUN] Simulating retrieval of receipts into {output_dir}")
            return retrieved

        if not config.gcs_bucket:
            return retrieved

        evidence_uri = f"gs://{config.gcs_bucket}/evidence-{config.resolved_vm_name}.tar.gz"
        dest_archive = output_dir / f"evidence-{config.resolved_vm_name}.tar.gz"

        res = self._run_cmd(
            ["gcloud", "storage", "cp", evidence_uri, str(dest_archive), "--quiet"],
            check=False,
        )
        if res.returncode == 0 and dest_archive.is_file():
            with tarfile.open(dest_archive, "r:gz") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(output_dir, filter="data")
                else:
                    tar.extractall(output_dir)
                for name in tar.getnames():
                    extracted_path = output_dir / name
                    if extracted_path.is_file() and name.endswith(".json"):
                        retrieved.append(extracted_path)
            print(f"[+] Extracted {len(retrieved)} receipts from remote Spot VM.")
        return retrieved

    def teardown_vm(self, config: SpotVMConfig, dry_run: bool = False) -> None:
        """Ensures immediate deletion of the Spot VM to avoid any idle spend."""
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
        print(f"[+] Tearing down Spot VM {config.resolved_vm_name}...")
        self._run_cmd(cmd, check=False)

    def execute_stage(
        self,
        config: SpotVMConfig,
        *,
        dry_run: bool = False,
        timeout: int = 2700,
    ) -> dict[str, Any]:
        """Full end-to-end execution of a Spot VM verification run."""
        print(f"=== Starting Spot VM Verification Stage (Tier: {config.tier}) ===")
        ok, auth_detail = self.verify_auth()
        if not ok and not dry_run:
            raise RuntimeError(f"GCP Authentication check failed: {auth_detail}")

        archive_path = self.root / ".local" / f"repo-{config.run_id}.tar.gz"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            print(f"[+] Packaging repository to {archive_path}...")
            self.package_repository(archive_path)
            if config.gcs_bucket:
                self.upload_archive(archive_path, config.gcs_bucket, dry_run=False)

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
                "status": "COMPLETED" if completed else "FAILED",
                "vm_name": config.resolved_vm_name,
                "provisioning": prov,
                "receipts_count": len(receipts),
            }
        finally:
            if not dry_run:
                self.teardown_vm(config, dry_run=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("CLOUDSDK_CORE_PROJECT", "aegis-verification-2026"))
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument("--tier", choices=["h1", "h2"], default="h2")
    parser.add_argument("--bucket", default=os.environ.get("AEGIS_M8_GCS_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--dry-run", action="store_true", help="Validate parameters and simulate execution without billing")
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
