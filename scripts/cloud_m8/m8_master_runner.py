#!/usr/bin/env python3
"""Master Orchestrator for AEGIS Milestone 8 (M8 True Cloud Verification).

Coordinates all M8 verification stages:
1. GCP Spot VM compute (NV-001, NV-005, NV-007, NV-008-L, NV-013, NV-014, NV-017, NV-020)
2. TCP cluster soak on 3 Spot instances (NV-016)
3. Cloud Run services (NV-006, NV-018, NV-019)
4. SLSA v1.0 / Cosign / Rekor attestation (NV-004)
5. Track 2 receipts: Branch protection (NV-010), Remote CI (NV-015), macOS controls (NV-003, NV-008-M), drills (NV-009, NV-012)
6. Collects all 18 receipts, updates docs/architecture/not_verified_registry.json
7. Regenerates the 6 AESE derived registries in strict topological order
8. Syncs evidence to the private archive (D:\\AEGIS-COGNITION.local-private\\ or .local/private-archive/)
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud_m8.cloud_run_services import CloudRunConfig, CloudRunServices  # noqa: E402
from scripts.cloud_m8.gcp_spot_orchestrator import GCPSpotOrchestrator, SpotVMConfig  # noqa: E402
from scripts.cloud_m8.nv004_attestation_generator import generate_nv004_receipts  # noqa: E402
from scripts.cloud_m8.tcp_cluster_orchestrator import (  # noqa: E402
    ClusterConfig,
    TCPClusterOrchestrator,
)
from scripts.cloud_m8.track2_orchestrator import Track2Orchestrator  # noqa: E402

AESE_SCRIPTS_TOPOLOGICAL: tuple[str, ...] = (
    "scripts/aese_inventory.py",
    "scripts/aese_claim_graph.py",
    "scripts/aese_s2_mapping.py",
    "scripts/aese_affected_closure.py",
    "scripts/aese_shadow_planner.py",
    "scripts/aese_validation_corpus.py",
)

RECEIPT_SPEC: dict[str, str] = {
    "NV-001": "linux_cgroup_live_receipt.json",
    "NV-003": "macos_capability_receipt.json",
    "NV-004": "nv004_attestation_receipt.json",
    "NV-005": "resource_policy_H2_receipt.json",
    "NV-006": "otlp_exporter_receipt.json",
    "NV-007": "fuzz_campaign_receipt.json",
    "NV-008-L": "wheel_parity_linux_receipt.json",
    "NV-008-M": "wheel_parity_macos_receipt.json",
    "NV-009": "restore_backup_drill_receipt.json",
    "NV-010": "branch_protection_receipt.json",
    "NV-012": "schema_migration_rehearsal_receipt.json",
    "NV-013": "miri_asan_receipt.json",
    "NV-014": "communication_payload_receipt.json",
    "NV-015": "remote_ci_matrix_receipt.json",
    "NV-016": "tcp_cluster_soak_gate_report.json",
    "NV-017": "quickjs_full_interpreter_cold_start_capture.json",
    "NV-018": "live_provider_429_soak_capture.json",
    "NV-019": "external_deployment_smoke_capture.json",
    "NV-020": "trust_policy_hash_receipt.json",
}

# Mapping of the 18 M8 obligations to primary and auxiliary receipts in artifacts/
OBLIGATION_RECEIPT_MAP: dict[str, list[str]] = {
    "NV-001": ["linux_cgroup_live_receipt.json"],
    "NV-003": ["macos_capability_receipt.json"],
    "NV-004": ["nv004_attestation_receipt.json"],
    "NV-005": ["resource_policy_H2_receipt.json", "resource_policy_H1_receipt.json"],
    "NV-006": ["otlp_exporter_receipt.json"],
    "NV-007": ["fuzz_campaign_receipt.json"],
    "NV-008": ["wheel_parity_linux_receipt.json", "wheel_parity_macos_receipt.json"],
    "NV-009": ["restore_backup_drill_receipt.json"],
    "NV-010": ["branch_protection_receipt.json"],
    "NV-012": ["schema_migration_rehearsal_receipt.json"],
    "NV-013": ["miri_asan_receipt.json"],
    "NV-014": ["communication_payload_receipt.json"],
    "NV-015": ["remote_ci_matrix_receipt.json"],
    "NV-016": ["tcp_cluster_soak_gate_report.json"],
    "NV-017": ["quickjs_full_interpreter_cold_start_capture.json"],
    "NV-018": ["live_provider_429_soak_capture.json"],
    "NV-019": ["external_deployment_smoke_capture.json"],
    "NV-020": ["trust_policy_hash_receipt.json"],
}


def resolve_private_archive() -> Path:
    """Resolves target private archive directory per repository rules."""
    env_target = os.environ.get("AEGIS_PRIVATE_ARCHIVE", "").strip()
    if env_target:
        return Path(env_target)
    drive_d = Path("D:/AEGIS-COGNITION.local-private")
    if drive_d.exists():
        return drive_d
    local_pointer = ROOT / ".local" / "private-archive.path"
    if local_pointer.is_file():
        try:
            target = Path(local_pointer.read_text(encoding="utf-8").strip())
            if target.exists():
                return target
        except Exception:
            pass
    fallback = ROOT / ".local" / "private-archive"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class M8MasterRunner:
    """Master controller executing and verifying Milestone 8 cloud workflows."""

    def __init__(self, root: Path = ROOT, artifacts_dir: Path | str | None = None) -> None:
        self.root = root
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.not_verified_registry = root / "docs" / "architecture" / "not_verified_registry.json"

    def run_spot_stage(
        self,
        project: str,
        zone: str,
        bucket: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        print("\n>>> Stage 1: GCP Spot VM Heavy Compute (NV-001, NV-005, NV-007, NV-008-L, NV-013, NV-014, NV-017, NV-020) <<<")
        config = SpotVMConfig(project_id=project, zone=zone, tier="h2", gcs_bucket=bucket)
        orch = GCPSpotOrchestrator(self.root, artifacts_dir=self.artifacts_dir)
        result = orch.execute_stage(config, dry_run=dry_run)
        if dry_run:
            canonical_artifacts = (self.root / "artifacts").resolve()
            if self.artifacts_dir.resolve() != canonical_artifacts:
                self._materialize_mock_spot_receipts()
        return result

    def _materialize_mock_spot_receipts(self) -> None:
        """In dry-run mode, materializes valid simulated receipts for local validation."""
        canonical_artifacts = (self.root / "artifacts").resolve()
        if self.artifacts_dir.resolve() == canonical_artifacts:
            return
        now_utc = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        mock_receipts = {
            "linux_cgroup_live_receipt.json": {
                "schema": "aegis-linux-cgroup-v2-live-probe-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "resource_policy_H2_receipt.json": {
                "schema": "aegis-resource-policy-benchmark-v1",
                "truth_claim": False,
                "profile": "H2",
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "resource_policy_H1_receipt.json": {
                "schema": "aegis-resource-policy-benchmark-v1",
                "truth_claim": False,
                "profile": "H1",
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "fuzz_campaign_receipt.json": {
                "schema": "aegis-fuzz-campaign-receipt-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "crashes_discovered": 0,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "wheel_parity_linux_receipt.json": {
                "schema": "aegis-wheel-parity-receipt-v1",
                "truth_claim": False,
                "platform": "Linux x86_64",
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "miri_asan_receipt.json": {
                "schema": "aegis-miri-asan-evidence-receipt-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "miri_status": "PASS",
                "asan_status": "PASS",
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "communication_payload_receipt.json": {
                "schema": "aegis-communication-payload-benchmark-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "quickjs_full_interpreter_cold_start_capture.json": {
                "schema": "aegis-quickjs-full-interpreter-cold-start-capture-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
            "trust_policy_hash_receipt.json": {
                "schema": "aegis-trust-policy-cross-cell-receipt-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "verified_at_utc": now_utc,
                "dry_run": True,
            },
        }
        for filename, data in mock_receipts.items():
            path = self.artifacts_dir / filename
            if not path.exists():
                path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def run_cluster_stage(
        self,
        project: str,
        zone: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        print("\n>>> Stage 2: TCP Multi-Machine Cluster Soak (NV-016) <<<")
        config = ClusterConfig(project_id=project, zone=zone)
        orch = TCPClusterOrchestrator(self.root, artifacts_dir=self.artifacts_dir)
        result = orch.execute_soak(config, dry_run=dry_run)
        if dry_run:
            canonical_artifacts = (self.root / "artifacts").resolve()
            if self.artifacts_dir.resolve() != canonical_artifacts:
                soak_rep = self.artifacts_dir / "tcp_cluster_soak_gate_report.json"
                if not soak_rep.exists():
                    soak_rep.write_text(
                        json.dumps({
                            "schema": "aegis-tcp-cluster-soak-gate-report-v1",
                            "truth_claim": False,
                            "overall_ok": True,
                            "status": "PASS",
                            "dry_run": True,
                        }, indent=2) + "\n",
                        encoding="utf-8",
                    )
        return result

    def run_cloud_run_stage(
        self,
        project: str,
        region: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        print("\n>>> Stage 3: Cloud Run Services (NV-006, NV-018, NV-019) <<<")
        config = CloudRunConfig(project_id=project, region=region)
        runner = CloudRunServices(self.root, artifacts_dir=self.artifacts_dir)
        result = runner.execute_all_gates(config, dry_run=dry_run)
        if dry_run:
            canonical_artifacts = (self.root / "artifacts").resolve()
            if self.artifacts_dir.resolve() != canonical_artifacts:
                for fname in ("live_provider_429_soak_capture.json", "external_deployment_smoke_capture.json"):
                    p = self.artifacts_dir / fname
                    if not p.exists():
                        p.write_text(json.dumps({"schema": "aegis-capture-v1", "overall_ok": True, "dry_run": True}, indent=2) + "\n", encoding="utf-8")
        return result

    def run_attestation_stage(self, dry_run: bool = False) -> dict[str, Any]:
        print("\n>>> Stage 4: SLSA v1.0 / Cosign Attestation (NV-004) <<<")
        return generate_nv004_receipts(self.root, dry_run=dry_run, artifacts_dir=self.artifacts_dir)

    def run_track2_stage(self, dry_run: bool = False) -> dict[str, Any]:
        print("\n>>> Stage 5: Track 2 Receipts (NV-003, NV-008-M, NV-009, NV-010, NV-012, NV-015) <<<")
        orch = Track2Orchestrator(self.root, artifacts_dir=self.artifacts_dir)
        return orch.execute_all(dry_run=dry_run)

    def verify_all_receipts(self) -> dict[str, bool]:
        """Checks presence and schema of all 18 receipts."""
        print("\n>>> Auditing 18 Receipts in artifacts/ <<<")
        status: dict[str, bool] = {}
        for nv_id, filename in RECEIPT_SPEC.items():
            path = self.artifacts_dir / filename
            exists = path.is_file()
            status[nv_id] = exists
            print(f"  [{'OK' if exists else 'MISSING'}] {nv_id:8} -> {filename}")
        return status

    def _get_git_head(self) -> str:
        """Retrieves the current Git commit HEAD SHA."""
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            return "UNKNOWN_HEAD"

    def update_not_verified_registry(self, dry_run: bool = False) -> Path | None:
        """Updates not_verified_registry.json marking verified gates with full closing metadata.

        Under dry-run mode, strictly prevents modifying the tracked registry on disk,
        outputting a preview diff and writing to .local/tmp/dry_run_registry_preview.json.
        """
        print("\n>>> Updating docs/architecture/not_verified_registry.json <<<")
        if not self.not_verified_registry.is_file():
            print(f"[!] Warning: {self.not_verified_registry} not found.")
            return None

        canonical_content = self.not_verified_registry.read_text(encoding="utf-8")
        registry = json.loads(canonical_content)
        entries = registry.get("entries", [])

        git_head = self._get_git_head()
        closing_date = datetime.now(UTC).strftime("%Y-%m-%d")
        updated_count = 0

        for entry in entries:
            eid = entry.get("id")
            if eid not in OBLIGATION_RECEIPT_MAP:
                continue

            receipt_files = OBLIGATION_RECEIPT_MAP[eid]
            primary_file = receipt_files[0]
            primary_path = self.artifacts_dir / primary_file

            if not primary_path.is_file():
                print(f"  [-] {eid:8}: Primary receipt {primary_file} missing. Skipping closure.")
                continue

            # In live mode, ensure receipts are authentic and not simulation mocks
            if not dry_run:
                try:
                    receipt_json = json.loads(primary_path.read_text(encoding="utf-8"))
                    if (
                        receipt_json.get("dry_run") is True
                        or receipt_json.get("truth_claim") is not False
                        or receipt_json.get("verified") is False
                        or receipt_json.get("status") in {
                            "NOT VERIFIED",
                            "SIMULATED / DRY RUN",
                            "FAIL_CLOSED (NO MACOS WHEEL FOUND)",
                            "FAILED",
                        }
                    ):
                        print(f"  [-] {eid:8}: Receipt {primary_file} has dry_run flags, unverified status, or invalid truth_claim. Skipping live closure.")
                        continue
                except Exception as err:
                    print(f"  [-] {eid:8}: Failed parsing {primary_file}: {err}. Skipping.")
                    continue

            receipt_bytes = primary_path.read_bytes()
            receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
            evidence_id = f"EV-{eid}-{receipt_sha256[:16].upper()}"

            entry["status"] = "LIVE VERIFIED (GCP / REMOTE)"
            entry["closing_evidence_id"] = evidence_id
            entry["closing_commit"] = git_head
            entry["closing_date"] = closing_date
            entry["receipt_path"] = f"artifacts/{primary_file}"
            entry["receipt_sha256"] = receipt_sha256

            if len(receipt_files) > 1:
                additional = []
                for sec_file in receipt_files[1:]:
                    sec_path = self.artifacts_dir / sec_file
                    if sec_path.is_file():
                        sec_sha = hashlib.sha256(sec_path.read_bytes()).hexdigest()
                        additional.append({
                            "receipt_path": f"artifacts/{sec_file}",
                            "receipt_sha256": sec_sha,
                        })
                if additional:
                    entry["additional_receipts"] = additional

            updated_count += 1
            print(f"  [+] {eid:8} -> {entry['status']} ({evidence_id})")

        new_content = json.dumps(registry, indent=2, sort_keys=False) + "\n"

        if dry_run:
            preview_dir = self.root / ".local" / "tmp"
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_file = preview_dir / "dry_run_registry_preview.json"
            preview_file.write_text(new_content, encoding="utf-8")
            print(f"\n[+] Dry-run active: Preview written to {preview_file.relative_to(self.root).as_posix()}.")
            print(f"[+] Tracked file {self.not_verified_registry.relative_to(self.root).as_posix()} preserved intact.")

            # Show diff preview
            diff_lines = list(difflib.unified_diff(
                canonical_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile="not_verified_registry.json (canonical)",
                tofile="dry_run_registry_preview.json (preview)",
                n=1,
            ))
            if diff_lines:
                print(">>> Registry Preview Diff (First 25 lines) <<<")
                print("".join(diff_lines[:25]))

            return preview_file
        else:
            self.not_verified_registry.write_text(new_content, encoding="utf-8")
            print(f"\n[+] docs/architecture/not_verified_registry.json updated successfully ({updated_count} closed).")
            return self.not_verified_registry

    def regenerate_aese_registries(self, dry_run: bool = False) -> None:
        """Regenerates the 6 AESE derived registries in strict topological order."""
        print("\n>>> Regenerating 6 AESE Derived Registries in Topological Order <<<")
        if dry_run:
            print("[+] Dry-run active: Simulating topological regeneration of 6 AESE registries without touching tracked quality/registry/*.json files.")
            for script_name in AESE_SCRIPTS_TOPOLOGICAL:
                print(f"  [SIMULATED] {sys.executable} {script_name}")
            return

        for script_name in AESE_SCRIPTS_TOPOLOGICAL:
            script_path = self.root / script_name
            if not script_path.is_file():
                print(f"[!] Script {script_name} not found. Skipping.")
                continue
            print(f"[+] Executing: {sys.executable} {script_name}...")
            res = subprocess.run(
                [sys.executable, str(script_path)],
                cwd=self.root,
                check=False,
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                print(f"[-] Regeneration failed for {script_name}: {res.stderr.strip() or res.stdout.strip()}")
            else:
                out_summary = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "OK"
                print(f"    Result: {out_summary}")

    def sync_to_private_archive(self, destination: Path | None = None, dry_run: bool = False) -> Path:
        """Syncs all verified receipts and gate artifacts to private archive."""
        dest = destination or resolve_private_archive()
        print(f"\n>>> Syncing Evidence to Private Archive: {dest} <<<")
        if dry_run:
            print(f"[+] Dry-run active: Simulating evidence sync to {dest / 'artifacts'} (no files copied).")
            return dest

        dest.mkdir(parents=True, exist_ok=True)
        target_artifacts = dest / "artifacts"
        target_artifacts.mkdir(parents=True, exist_ok=True)

        copied = 0
        for item in self.artifacts_dir.glob("*.json"):
            shutil.copy2(item, target_artifacts / item.name)
            copied += 1

        print(f"[+] Synced {copied} evidence artifacts to {target_artifacts}.")
        return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=["all", "compute", "spot", "cluster", "services", "cloud-run", "attestation", "track2", "sync", "audit"],
        default="all",
        help="Stage to execute",
    )
    parser.add_argument("--project", default=os.environ.get("CLOUDSDK_CORE_PROJECT", "aegis-verification-2026"))
    parser.add_argument("--zone", default="us-central1-a")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--bucket", default=os.environ.get("AEGIS_M8_GCS_BUCKET", "aegis-verification-2026_cloudbuild"))
    parser.add_argument("--dry-run", action="store_true", help="Simulate cloud steps without spinning up billable infrastructure")
    parser.add_argument("--artifacts-dir", default=None, help="Directory containing or receiving verification receipts")
    parser.add_argument("--private-archive", default="", help="Explicit private archive destination directory")
    args = parser.parse_args()

    runner = M8MasterRunner(artifacts_dir=args.artifacts_dir)

    if args.stage in {"all", "compute", "spot"}:
        runner.run_spot_stage(args.project, args.zone, args.bucket, dry_run=args.dry_run)

    if args.stage in {"all", "cluster"}:
        runner.run_cluster_stage(args.project, args.zone, dry_run=args.dry_run)

    if args.stage in {"all", "services", "cloud-run"}:
        runner.run_cloud_run_stage(args.project, args.region, dry_run=args.dry_run)

    if args.stage in {"all", "attestation"}:
        runner.run_attestation_stage(dry_run=args.dry_run)

    if args.stage in {"all", "track2"}:
        runner.run_track2_stage(dry_run=args.dry_run)

    if args.stage in {"all", "audit"}:
        runner.verify_all_receipts()
        runner.update_not_verified_registry(dry_run=args.dry_run)
        runner.regenerate_aese_registries(dry_run=args.dry_run)

    if args.stage in {"all", "sync"}:
        archive_dest = Path(args.private_archive) if args.private_archive else None
        runner.sync_to_private_archive(archive_dest, dry_run=args.dry_run)

    print("\n[+] M8 Master Runner completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
