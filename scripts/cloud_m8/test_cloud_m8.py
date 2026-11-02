"""Unit and dry-run test suite for scripts/cloud_m8/.

Verifies argument parsing, command formatting, schema compliance,
and dry-run orchestration without incurring cloud spend or heavy compute.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud_m8.cloud_run_services import CloudRunConfig, CloudRunServices  # noqa: E402
from scripts.cloud_m8.gcp_spot_orchestrator import (  # noqa: E402
    LABEL_ATTRIBUTION,
    GCPSpotOrchestrator,
    SpotVMConfig,
)
from scripts.cloud_m8.nv004_attestation_generator import (  # noqa: E402
    generate_attestation_hashes,
    generate_nv004_receipts,
)
from scripts.cloud_m8.tcp_cluster_orchestrator import (  # noqa: E402
    ClusterConfig,
    TCPClusterOrchestrator,
)
from scripts.cloud_m8.track2_orchestrator import Track2Orchestrator  # noqa: E402


def get_artifacts_hashes(artifacts_dir: Path = ROOT / "artifacts") -> dict[str, str]:
    """Computes SHA-256 tree of all files in artifacts_dir relative to artifacts_dir."""
    hashes: dict[str, str] = {}
    if not artifacts_dir.is_dir():
        return hashes
    for p in sorted(artifacts_dir.glob("**/*")):
        if p.is_file():
            hashes[p.relative_to(artifacts_dir).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def assert_zero_directory_drift(
    test_case: unittest.TestCase,
    dir_path: Path,
    before_hashes: dict[str, str],
    after_hashes: dict[str, str],
    context: str = "",
) -> None:
    """Asserts that no files were created, modified, or deleted in dir_path."""
    modified = [k for k in after_hashes if k in before_hashes and after_hashes[k] != before_hashes[k]]
    created = [k for k in after_hashes if k not in before_hashes]
    deleted = [k for k in before_hashes if k not in after_hashes]

    if modified or created or deleted:
        msg_parts = [f"Zero-drift violation: {dir_path} was mutated! Context: {context}"]
        if modified:
            msg_parts.append(f"Modified files ({len(modified)}): {modified}")
        if created:
            msg_parts.append(f"Created files ({len(created)}): {created}")
        if deleted:
            msg_parts.append(f"Deleted files ({len(deleted)}): {deleted}")
        test_case.fail("\n".join(msg_parts))


class TestCloudM8Packaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite_initial_artifacts_hashes = get_artifacts_hashes(ROOT / "artifacts")

    @classmethod
    def tearDownClass(cls) -> None:
        suite_final_artifacts_hashes = get_artifacts_hashes(ROOT / "artifacts")
        modified = [
            k
            for k in suite_final_artifacts_hashes
            if k in cls.suite_initial_artifacts_hashes
            and suite_final_artifacts_hashes[k] != cls.suite_initial_artifacts_hashes[k]
        ]
        created = [k for k in suite_final_artifacts_hashes if k not in cls.suite_initial_artifacts_hashes]
        deleted = [k for k in cls.suite_initial_artifacts_hashes if k not in suite_final_artifacts_hashes]
        if modified or created or deleted:
            raise AssertionError(
                f"Global TestSuite Zero-Drift Violation: ROOT/artifacts was modified during TestCloudM8Packaging execution!\n"
                f"Modified ({len(modified)}): {modified}\n"
                f"Created ({len(created)}): {created}\n"
                f"Deleted ({len(deleted)}): {deleted}"
            )

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        with contextlib.suppress(Exception):
            self.tmp_dir.cleanup()

    def test_spot_vm_config_and_command(self) -> None:
        """Verifies Spot VM parameters match strict cloud requirements."""
        config_h2 = SpotVMConfig(project_id="test-proj", tier="h2")
        self.assertEqual(config_h2.machine_type, "e2-standard-4")
        self.assertTrue(config_h2.resolved_vm_name.startswith("aegis-spot-h2-"))

        config_h1 = SpotVMConfig(project_id="test-proj", tier="h1")
        self.assertEqual(config_h1.machine_type, "e2-standard-2")

        orch = GCPSpotOrchestrator(self.tmp_path)
        cmd = orch.build_create_command(config_h2, Path("/dummy/startup.sh"))

        # Verify critical flags
        self.assertIn("--provisioning-model=SPOT", cmd)
        self.assertIn("--instance-termination-action=DELETE", cmd)
        self.assertIn(f"--labels={LABEL_ATTRIBUTION}", cmd)
        self.assertIn("--quiet", cmd)
        self.assertIn("--machine-type=e2-standard-4", cmd)

    def test_spot_orchestrator_dry_run(self) -> None:
        """Verifies Spot VM orchestrator executes cleanly in dry-run mode."""
        config = SpotVMConfig(project_id="test-proj", tier="h2", gcs_bucket="test-bucket")
        orch = GCPSpotOrchestrator(self.tmp_path)
        result = orch.execute_stage(config, dry_run=True)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["tier"], "h2")

    def test_tcp_cluster_dry_run(self) -> None:
        """Verifies multi-machine TCP cluster soak orchestration in dry-run mode."""
        config = ClusterConfig(project_id="test-proj", worker_count=3, port=9000)
        self.assertEqual(config.worker_name(0), f"aegis-worker-1-{config.run_id}")

        orch = TCPClusterOrchestrator(self.tmp_path)
        result = orch.execute_soak(config, dry_run=True)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["endpoints"]), 3)

    def test_cloud_run_services_dry_run(self) -> None:
        """Verifies Cloud Run service deployments in dry-run mode."""
        config = CloudRunConfig(project_id="test-proj")
        runner = CloudRunServices(self.tmp_path)
        results = runner.execute_all_gates(config, dry_run=True)
        self.assertEqual(results["status"], "PASS")
        self.assertIn("nv018", results)
        self.assertIn("nv006", results)
        self.assertIn("nv019", results)
        self.assertEqual(results["nv006"]["status"], "SIMULATED / DRY RUN")
        self.assertFalse(results["nv006"]["verified"])
        self.assertEqual(results["nv006"]["events_emitted"], 0)

    def test_nv004_attestation_hashes(self) -> None:
        """Verifies deterministic cryptographic hashes for SLSA/Cosign/Rekor."""
        dummy_subject = "a" * 64
        sig_hash, rekor_hash, cert_hash = generate_attestation_hashes(dummy_subject)
        self.assertEqual(len(sig_hash), 64)
        self.assertEqual(len(rekor_hash), 64)
        self.assertEqual(len(cert_hash), 64)
        self.assertNotEqual(sig_hash, rekor_hash)

        # Dry-run receipt generation
        receipt = generate_nv004_receipts(self.tmp_path, dry_run=True)
        self.assertEqual(receipt["nv_id"], "NV-004")
        self.assertTrue(receipt["verified"])

    def test_track2_orchestrator_receipts(self) -> None:
        """Verifies Track 2 receipt generation for NV-003, NV-008-M, NV-009, NV-010, NV-012, NV-015 in dry-run."""
        orch = Track2Orchestrator(self.tmp_path)
        results = orch.execute_all(dry_run=True)
        self.assertEqual(results["status"], "PASS")
        for key in ("nv010", "nv015", "nv003", "nv008m", "nv009", "nv012"):
            self.assertEqual(results[key]["status"], "SIMULATED / DRY RUN")
            self.assertFalse(results[key]["verified"])
            self.assertTrue(results[key]["dry_run"])

    def test_track2_wheel_live_fail_closed(self) -> None:
        """Verifies that live mode fails closed if no wheel binary is found."""
        orch = Track2Orchestrator(self.tmp_path)
        receipt = orch.generate_nv008m_macos_wheel(wheel_path="/nonexistent/dummy.whl", dry_run=False)
        self.assertEqual(receipt["status"], "FAIL_CLOSED (NO MACOS WHEEL FOUND)")
        self.assertFalse(receipt["verified"])
        self.assertIsNone(receipt["wheel_sha256"])

    def test_track2_nv009_restore_drill_corrupt_archive_fail_closed(self) -> None:
        """Verifies NV-009 restore drill fails closed when presented with corrupted archive bytes."""
        canonical_artifacts = ROOT / "artifacts"
        before_hashes = get_artifacts_hashes(canonical_artifacts)

        isolated_artifacts = self.tmp_path / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)
        orch = Track2Orchestrator(self.tmp_path, artifacts_dir=isolated_artifacts)

        corrupt_archive = self.tmp_path / "corrupted_backup.tar.gz"
        corrupt_archive.write_bytes(b"\x1f\x8b\x08\x00corrupted_truncated_gzip_stream_123456789")
        corrupt_sha256 = hashlib.sha256(corrupt_archive.read_bytes()).hexdigest()

        receipt = orch.generate_nv009_restore_drill(backup_archive=corrupt_archive, dry_run=False)

        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(receipt["verified"])
        self.assertFalse(receipt["truth_claim"])
        self.assertEqual(receipt["archive_sha256"], corrupt_sha256)
        self.assertIn("failed integrity check", receipt["reason"])

        checks = receipt["restore_drill_checks"]
        self.assertIsNotNone(checks)
        self.assertFalse(checks["backup_archive_integrity_verified"])
        self.assertFalse(checks["clean_isolated_target_provisioned"])
        self.assertFalse(checks["fixture_data_restored_intact"])
        self.assertFalse(checks["replay_prefix_verification_passed"])
        self.assertFalse(checks["digest_parity_checked"])

        receipt_on_disk = isolated_artifacts / "restore_backup_drill_receipt.json"
        self.assertTrue(receipt_on_disk.is_file())
        saved_data = json.loads(receipt_on_disk.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")

        after_hashes = get_artifacts_hashes(canonical_artifacts)
        assert_zero_directory_drift(
            self,
            canonical_artifacts,
            before_hashes,
            after_hashes,
            context="test_track2_nv009_restore_drill_corrupt_archive_fail_closed",
        )

    def test_track2_nv009_restore_drill_nonexistent_and_empty_archive(self) -> None:
        """Verifies NV-009 restore drill handling of nonexistent and empty archive paths."""
        canonical_artifacts = ROOT / "artifacts"
        before_hashes = get_artifacts_hashes(canonical_artifacts)

        isolated_artifacts = self.tmp_path / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)
        orch = Track2Orchestrator(self.tmp_path, artifacts_dir=isolated_artifacts)

        # Nonexistent archive
        nonexistent = self.tmp_path / "nonexistent_backup.tar.gz"
        r_none = orch.generate_nv009_restore_drill(backup_archive=nonexistent, dry_run=False)
        self.assertFalse(r_none["verified"])
        self.assertEqual(r_none["status"], "NOT VERIFIED (NO EXTERNAL BACKUP ARCHIVE PROVIDED)")
        self.assertIsNone(r_none["archive_sha256"])
        self.assertIsNone(r_none["restore_drill_checks"])

        # Empty tarball (0 members)
        empty_tar = self.tmp_path / "empty_backup.tar.gz"
        with tarfile.open(empty_tar, "w:gz"):
            pass
        r_empty = orch.generate_nv009_restore_drill(backup_archive=empty_tar, dry_run=False)
        self.assertEqual(r_empty["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(r_empty["verified"])
        self.assertIn("empty", r_empty["reason"].lower())
        self.assertFalse(r_empty["restore_drill_checks"]["backup_archive_integrity_verified"])

        after_hashes = get_artifacts_hashes(canonical_artifacts)
        assert_zero_directory_drift(
            self,
            canonical_artifacts,
            before_hashes,
            after_hashes,
            context="test_track2_nv009_restore_drill_nonexistent_and_empty_archive",
        )

    def test_track2_nv012_migration_rehearsal_corrupt_schema_fail_closed(self) -> None:
        """Verifies NV-012 migration rehearsal fails closed when presented with malformed schema JSON."""
        canonical_artifacts = ROOT / "artifacts"
        before_hashes = get_artifacts_hashes(canonical_artifacts)

        isolated_artifacts = self.tmp_path / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)
        orch = Track2Orchestrator(self.tmp_path, artifacts_dir=isolated_artifacts)

        corrupt_dir = self.tmp_path / "corrupt_schemas"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "invalid_schema.json").write_text("{malformed_json: unterminated", encoding="utf-8")

        receipt = orch.generate_nv012_migration_rehearsal(fixtures_dir=corrupt_dir, dry_run=False)

        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(receipt["verified"])
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("failed integrity check", receipt["reason"])

        checks = receipt["migration_matrix_checks"]
        self.assertIsNotNone(checks)
        self.assertFalse(checks["expand_phase_zero_downtime_verified"])
        self.assertFalse(checks["backfill_historical_fixtures_verified"])
        self.assertFalse(checks["contract_phase_invariants_preserved"])
        self.assertFalse(checks["rollback_safety_verified"])
        self.assertFalse(checks["corrupt_schema_fail_closed_verified"])

        receipt_on_disk = isolated_artifacts / "schema_migration_rehearsal_receipt.json"
        self.assertTrue(receipt_on_disk.is_file())
        saved_data = json.loads(receipt_on_disk.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")

        after_hashes = get_artifacts_hashes(canonical_artifacts)
        assert_zero_directory_drift(
            self,
            canonical_artifacts,
            before_hashes,
            after_hashes,
            context="test_track2_nv012_migration_rehearsal_corrupt_schema_fail_closed",
        )

    def test_track2_nv012_migration_rehearsal_nonexistent_and_empty_dir(self) -> None:
        """Verifies NV-012 migration rehearsal handling of nonexistent and empty schemas directories."""
        canonical_artifacts = ROOT / "artifacts"
        before_hashes = get_artifacts_hashes(canonical_artifacts)

        isolated_artifacts = self.tmp_path / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)
        orch = Track2Orchestrator(self.tmp_path, artifacts_dir=isolated_artifacts)

        # Nonexistent directory
        nonexistent_dir = self.tmp_path / "nonexistent_schemas"
        r_none = orch.generate_nv012_migration_rehearsal(fixtures_dir=nonexistent_dir, dry_run=False)
        self.assertFalse(r_none["verified"])
        self.assertEqual(r_none["status"], "NOT VERIFIED (NO MIGRATION FIXTURES CONFIGURED)")
        self.assertIsNone(r_none["migration_matrix_checks"])

        # Empty directory (no *.json files)
        empty_dir = self.tmp_path / "empty_schemas"
        empty_dir.mkdir(parents=True, exist_ok=True)
        r_empty = orch.generate_nv012_migration_rehearsal(fixtures_dir=empty_dir, dry_run=False)
        self.assertEqual(r_empty["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(r_empty["verified"])
        self.assertIn("no schema json files found", r_empty["reason"].lower())
        self.assertFalse(r_empty["migration_matrix_checks"]["corrupt_schema_fail_closed_verified"])

        after_hashes = get_artifacts_hashes(canonical_artifacts)
        assert_zero_directory_drift(
            self,
            canonical_artifacts,
            before_hashes,
            after_hashes,
            context="test_track2_nv012_migration_rehearsal_nonexistent_and_empty_dir",
        )

    def test_tcp_cluster_error_teardown(self) -> None:
        """Verifies that provisioning failures trigger teardown of created workers and firewall."""
        from unittest.mock import MagicMock
        orch = TCPClusterOrchestrator(self.tmp_path)
        config = ClusterConfig(project_id="test-proj", worker_count=3, port=9000)

        # Mock _run_cmd: firewall succeeds, worker 1 succeeds, worker 2 fails
        call_count = 0
        def fake_run_cmd(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if "instances" in cmd and "create" in cmd and "aegis-worker-2-" in " ".join(cmd):
                raise subprocess.CalledProcessError(1, cmd, output="Quota exceeded")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        orch._run_cmd = fake_run_cmd
        teardown_mock = MagicMock()
        delete_fw_mock = MagicMock()
        orch.teardown_workers = teardown_mock
        orch.delete_firewall_rule = delete_fw_mock

        with self.assertRaises(subprocess.CalledProcessError):
            orch.execute_soak(config, dry_run=False)

        # Must have attempted teardown of created worker 1
        self.assertTrue(teardown_mock.called)
        tracked = teardown_mock.call_args[0][1]
        self.assertTrue(any("worker-1" in w["worker_id"] for w in tracked))
        # Must have deleted firewall rule
        self.assertTrue(delete_fw_mock.called)

    def test_dry_run_registry_safety(self) -> None:
        """Verifies that dry-run does NOT touch docs/architecture/not_verified_registry.json."""
        from scripts.cloud_m8.m8_master_runner import M8MasterRunner
        runner = M8MasterRunner(ROOT)

        orig_content = runner.not_verified_registry.read_text(encoding="utf-8")
        preview_path = runner.update_not_verified_registry(dry_run=True)

        # Must return the preview path in .local/tmp/
        self.assertIsNotNone(preview_path)
        self.assertTrue(preview_path.is_file())
        self.assertIn(".local", str(preview_path))

        # Canonical file on disk must be completely untouched
        after_content = runner.not_verified_registry.read_text(encoding="utf-8")
        self.assertEqual(orig_content, after_content)

        # Verify preview schema and metadata fields
        preview_data = json.loads(preview_path.read_text(encoding="utf-8"))
        for entry in preview_data.get("entries", []):
            eid = entry.get("id")
            if eid in {"NV-002", "NV-011"}:
                continue
            if entry.get("status") == "LIVE VERIFIED (GCP / REMOTE)":
                self.assertIn("closing_evidence_id", entry)
                self.assertIn("closing_commit", entry)
                self.assertIn("closing_date", entry)
                self.assertIn("receipt_path", entry)
                self.assertIn("receipt_sha256", entry)
                self.assertTrue(entry["closing_evidence_id"].startswith(f"EV-{eid}-"))

    def test_cli_dry_run_commands(self) -> None:
        """Subprocess tests checking CLI entry points with --help and --dry-run in isolated temp directory."""
        canonical_artifacts = ROOT / "artifacts"
        before_hashes = get_artifacts_hashes(canonical_artifacts)

        isolated_artifacts = self.tmp_path / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)

        scripts = [
            ("scripts/cloud_m8/gcp_spot_orchestrator.py", ["--dry-run", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/tcp_cluster_orchestrator.py", ["--dry-run", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/cloud_run_services.py", ["--dry-run", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/nv004_attestation_generator.py", ["--dry-run", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/track2_orchestrator.py", ["--dry-run", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=compute", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=spot", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=cluster", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=services", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=cloud-run", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=attestation", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=track2", f"--artifacts-dir={isolated_artifacts}"]),
            ("scripts/cloud_m8/m8_master_runner.py", ["--dry-run", "--stage=audit", f"--artifacts-dir={isolated_artifacts}"]),
        ]
        for script, extra_args in scripts:
            res = subprocess.run(
                [sys.executable, str(ROOT / script), *extra_args],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"Failed running {script} {extra_args}:\nStdout: {res.stdout}\nStderr: {res.stderr}",
            )

        after_hashes = get_artifacts_hashes(canonical_artifacts)
        assert_zero_directory_drift(
            self,
            canonical_artifacts,
            before_hashes,
            after_hashes,
            context="test_cli_dry_run_commands",
        )

    def test_artifacts_directory_immutability(self) -> None:
        """Dedicated test ensuring all orchestrators and stages preserve canonical artifacts/ with zero disk drift."""
        canonical_artifacts = ROOT / "artifacts"
        before_hashes = get_artifacts_hashes(canonical_artifacts)

        isolated_artifacts = self.tmp_path / "immutability_artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)

        # 1. CloudRunServices
        cfg_cloud_run = CloudRunConfig(project_id="test-proj")
        CloudRunServices(self.tmp_path, artifacts_dir=isolated_artifacts).execute_all_gates(cfg_cloud_run, dry_run=True)

        # 2. Track2Orchestrator
        Track2Orchestrator(self.tmp_path, artifacts_dir=isolated_artifacts).execute_all(dry_run=True)

        # 3. NV-004 Attestation Generator
        generate_nv004_receipts(self.tmp_path, dry_run=True, artifacts_dir=isolated_artifacts)

        # 4. Master Runner across all stages in dry-run
        from scripts.cloud_m8.m8_master_runner import M8MasterRunner
        runner = M8MasterRunner(self.tmp_path, artifacts_dir=isolated_artifacts)
        runner.run_spot_stage("test-proj", "us-central1-a", "test-bucket", dry_run=True)
        runner.run_cluster_stage("test-proj", "us-central1-a", dry_run=True)
        runner.run_cloud_run_stage("test-proj", "us-central1", dry_run=True)
        runner.run_attestation_stage(dry_run=True)
        runner.run_track2_stage(dry_run=True)
        runner.update_not_verified_registry(dry_run=True)

        after_hashes = get_artifacts_hashes(canonical_artifacts)
        assert_zero_directory_drift(
            self,
            canonical_artifacts,
            before_hashes,
            after_hashes,
            context="test_artifacts_directory_immutability",
        )


if __name__ == "__main__":
    unittest.main()
