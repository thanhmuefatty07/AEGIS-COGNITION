"""Unit and dry-run test suite for scripts/cloud_m8/.

Verifies argument parsing, command formatting, schema compliance,
and dry-run orchestration without incurring cloud spend or heavy compute.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
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
        rel = p.relative_to(artifacts_dir).as_posix()
        if rel.startswith(("suites/", "suite-status/", "private/")) or p.name.startswith(
            (".suite-evidence.lock", ".pytest-evidence-")
        ):
            continue
        if p.is_file():
            with contextlib.suppress(OSError):
                hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
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
        cls.suite_initial_local_hashes = get_artifacts_hashes(ROOT / ".local")

    @classmethod
    def tearDownClass(cls) -> None:
        for label, path, initial in (
            ("ROOT/artifacts", ROOT / "artifacts", cls.suite_initial_artifacts_hashes),
            ("ROOT/.local", ROOT / ".local", cls.suite_initial_local_hashes),
        ):
            final = get_artifacts_hashes(path)
            modified = [k for k in final if k in initial and final[k] != initial[k]]
            created = [k for k in final if k not in initial]
            deleted = [k for k in initial if k not in final]
            if modified or created or deleted:
                raise AssertionError(
                    f"Global TestSuite Zero-Drift Violation: {label} was modified during TestCloudM8Packaging execution!\n"
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
        self.assertFalse((self.tmp_path / ".local").exists())

    def test_tcp_cluster_dry_run(self) -> None:
        """Verifies multi-machine TCP cluster soak orchestration in dry-run mode."""
        config = ClusterConfig(project_id="test-proj", worker_count=3, port=9000)
        self.assertEqual(config.worker_name(0), f"aegis-worker-1-{config.run_id}")

        orch = TCPClusterOrchestrator(self.tmp_path)
        result = orch.execute_soak(config, dry_run=True)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["endpoints"]), 3)
        self.assertFalse((self.tmp_path / ".local").exists())

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
        self.assertFalse((self.tmp_path / ".local").exists())

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
        """Verifies that live mode fails closed if wheel is missing, 0 bytes, truncated/corrupt, or empty ZIP."""
        orch = Track2Orchestrator(self.tmp_path)
        receipt = orch.generate_nv008m_macos_wheel(wheel_path="/nonexistent/dummy.whl", dry_run=False)
        self.assertEqual(receipt["status"], "FAIL_CLOSED (NO MACOS WHEEL FOUND)")
        self.assertFalse(receipt["verified"])
        self.assertIsNone(receipt["wheel_sha256"])

        zero_wheel = self.tmp_path / "aegis_cognition-0.1.0-cp314-cp314-macosx_14_0_arm64.whl"
        zero_wheel.write_bytes(b"")
        r_zero = orch.generate_nv008m_macos_wheel(wheel_path=zero_wheel, dry_run=False)
        self.assertEqual(r_zero["status"], "FAIL_CLOSED (NO MACOS WHEEL FOUND)")
        self.assertFalse(r_zero["verified"])

        # Truncated/corrupt ZIP archive must fail closed
        truncated_wheel = self.tmp_path / "truncated_macosx_14_0_arm64.whl"
        truncated_bytes = b"PK\x03\x04dummy-macos-arm64-wheel-payload"
        truncated_wheel.write_bytes(truncated_bytes)
        r_trunc = orch.generate_nv008m_macos_wheel(wheel_path=truncated_wheel, dry_run=False)
        self.assertEqual(r_trunc["status"], "FAIL_CLOSED (CORRUPT WHEEL ARCHIVE)")
        self.assertFalse(r_trunc["verified"])
        self.assertFalse(r_trunc["truth_claim"])
        self.assertEqual(r_trunc["wheel_sha256"], hashlib.sha256(truncated_bytes).hexdigest())

        # Empty ZIP archive (0 members) must fail closed
        empty_zip_wheel = self.tmp_path / "empty_macosx_14_0_arm64.whl"
        with zipfile.ZipFile(empty_zip_wheel, "w"):
            pass
        r_empty_zip = orch.generate_nv008m_macos_wheel(wheel_path=empty_zip_wheel, dry_run=False)
        self.assertEqual(r_empty_zip["status"], "FAIL_CLOSED (CORRUPT WHEEL ARCHIVE)")
        self.assertFalse(r_empty_zip["verified"])

        # Authentic valid ZIP wheel archive must pass with PROVEN
        valid_wheel = self.tmp_path / "aegis_cognition-0.1.0-cp314-cp314-macosx_14_0_arm64.whl"
        with zipfile.ZipFile(valid_wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("aegis_cognition-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: cp314-cp314-macosx_14_0_arm64\n")
            zf.writestr("aegis_cognition/aegis_nerve.cpython-314-darwin.so", b"\xcf\xfa\xed\xfemach-o-binary")
        wheel_bytes = valid_wheel.read_bytes()
        r_valid = orch.generate_nv008m_macos_wheel(wheel_path=valid_wheel, dry_run=False)
        self.assertEqual(r_valid["status"], "PROVEN")
        self.assertTrue(r_valid["verified"])
        self.assertFalse(r_valid["truth_claim"])
        self.assertFalse(r_valid["dry_run"])
        self.assertEqual(r_valid["wheel_sha256"], hashlib.sha256(wheel_bytes).hexdigest())
        self.assertEqual(r_valid["wheel_size_bytes"], len(wheel_bytes))

    def test_track2_nv003_macos_capability_verification_and_fail_closed(self) -> None:
        """Verifies NV-003 macOS capability receipt sets verified=True only on valid cooperative live probe."""
        isolated_artifacts = self.tmp_path / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)
        orch = Track2Orchestrator(self.tmp_path, artifacts_dir=isolated_artifacts)

        # 1. Valid live macOS probe report -> verified=True
        valid_probe = self.tmp_path / "macos_probe_valid.json"
        valid_probe.write_text(
            json.dumps({
                "schema": "aegis-macos-capability-probe-v1",
                "platform": "macOS-15.0-arm64-arm-64bit-Mach-O",
                "implementation": "core/rust/src/resource_platform.rs::MacosCooperativeController",
                "status": "COOPERATIVE LIVE VERIFIED",
                "checks": {
                    "cooperative_cancellation_observed": True,
                    "cooperative_termination_observed": True,
                },
                "kernel_enforcement_claim": False,
            }),
            encoding="utf-8",
        )
        r_ok = orch.generate_nv003_macos_capability(probe_receipt=valid_probe, dry_run=False)
        self.assertEqual(r_ok["status"], "COOPERATIVE LIVE VERIFIED")
        self.assertTrue(r_ok["verified"])
        self.assertFalse(r_ok["truth_claim"])
        self.assertFalse(r_ok["kernel_enforcement_claim"])

        # 2. Probe with failed check -> verified=False and fail-closed status
        failed_probe = self.tmp_path / "macos_probe_failed_check.json"
        failed_probe.write_text(
            json.dumps({
                "schema": "aegis-macos-capability-probe-v1",
                "status": "COOPERATIVE LIVE VERIFIED",
                "checks": {
                    "cooperative_cancellation_observed": True,
                    "cooperative_termination_observed": False,
                },
                "kernel_enforcement_claim": False,
            }),
            encoding="utf-8",
        )
        r_fail_check = orch.generate_nv003_macos_capability(probe_receipt=failed_probe, dry_run=False)
        self.assertFalse(r_fail_check["verified"])
        self.assertEqual(r_fail_check["status"], "FAIL_CLOSED (INVALID PROBE CLAIMS)")

        # 3. Tampered probe claiming kernel_enforcement_claim=True via AEGIS_MACOS_PROBE_RECEIPT -> fail-closed
        tampered_probe = self.tmp_path / "macos_probe_tampered_kernel_claim.json"
        tampered_probe.write_text(
            json.dumps({
                "schema": "aegis-macos-capability-probe-v1",
                "status": "COOPERATIVE LIVE VERIFIED",
                "checks": {
                    "cooperative_cancellation_observed": True,
                    "cooperative_termination_observed": True,
                },
                "kernel_enforcement_claim": True,
            }),
            encoding="utf-8",
        )
        prev_env = os.environ.get("AEGIS_MACOS_PROBE_RECEIPT")
        try:
            os.environ["AEGIS_MACOS_PROBE_RECEIPT"] = str(tampered_probe)
            r_tampered = orch.generate_nv003_macos_capability(dry_run=False)
        finally:
            if prev_env is None:
                os.environ.pop("AEGIS_MACOS_PROBE_RECEIPT", None)
            else:
                os.environ["AEGIS_MACOS_PROBE_RECEIPT"] = prev_env
        self.assertFalse(r_tampered["verified"])
        self.assertEqual(r_tampered["status"], "FAIL_CLOSED (INVALID PROBE CLAIMS)")
        self.assertFalse(r_tampered["kernel_enforcement_claim"])

        # 4. Nonexistent explicit probe_receipt -> NOT VERIFIED (PROBE RECEIPT NOT FOUND)
        r_missing = orch.generate_nv003_macos_capability(
            probe_receipt=self.tmp_path / "does_not_exist_probe.json",
            dry_run=False,
        )
        self.assertFalse(r_missing["verified"])
        self.assertEqual(r_missing["status"], "NOT VERIFIED (PROBE RECEIPT NOT FOUND)")

        # 5. Corrupt JSON probe -> FAIL_CLOSED (CORRUPT PROBE RECEIPT) & verified=False
        corrupt_probe = self.tmp_path / "macos_probe_corrupt.json"
        corrupt_probe.write_text("{corrupt_json", encoding="utf-8")
        r_corrupt = orch.generate_nv003_macos_capability(probe_receipt=corrupt_probe, dry_run=False)
        self.assertEqual(r_corrupt["status"], "FAIL_CLOSED (CORRUPT PROBE RECEIPT)")
        self.assertFalse(r_corrupt["verified"])

    def test_master_runner_secondary_receipt_validation(self) -> None:
        """Verifies update_not_verified_registry filters out unverified secondary receipts in live mode."""
        from scripts.cloud_m8.m8_master_runner import M8MasterRunner

        isolated_root = self.tmp_path / "repo"
        (isolated_root / "docs" / "architecture").mkdir(parents=True, exist_ok=True)
        isolated_artifacts = isolated_root / "artifacts"
        isolated_artifacts.mkdir(parents=True, exist_ok=True)

        reg_path = isolated_root / "docs" / "architecture" / "not_verified_registry.json"
        reg_path.write_text(
            json.dumps({
                "schema": "aegis-not-verified-registry-v1",
                "truth_claim": False,
                "entries": [
                    {"id": "NV-003", "title": "macOS resource enforcement", "status": "NOT VERIFIED"},
                    {"id": "NV-008", "title": "Tier-1 wheel parity", "status": "NOT VERIFIED"},
                ],
            }),
            encoding="utf-8",
        )

        # Write verified primary NV-003 and NV-008-L, but fail-closed NV-008-M
        (isolated_artifacts / "macos_capability_receipt.json").write_text(
            json.dumps({
                "schema": "aegis-macos-capability-probe-v1",
                "truth_claim": False,
                "status": "COOPERATIVE LIVE VERIFIED",
                "verified": True,
                "dry_run": False,
            }),
            encoding="utf-8",
        )
        (isolated_artifacts / "wheel_parity_linux_receipt.json").write_text(
            json.dumps({
                "schema": "aegis-wheel-parity-receipt-v1",
                "truth_claim": False,
                "status": "PROVEN",
                "verified": True,
                "dry_run": False,
            }),
            encoding="utf-8",
        )
        (isolated_artifacts / "wheel_parity_macos_receipt.json").write_text(
            json.dumps({
                "schema": "aegis-wheel-parity-receipt-v1",
                "truth_claim": False,
                "status": "FAIL_CLOSED (NO MACOS WHEEL FOUND)",
                "verified": False,
                "dry_run": False,
            }),
            encoding="utf-8",
        )

        runner = M8MasterRunner(isolated_root, artifacts_dir=isolated_artifacts)
        runner.update_not_verified_registry(dry_run=False)
        updated = json.loads(reg_path.read_text(encoding="utf-8"))
        entries_by_id = {e["id"]: e for e in updated["entries"]}
        self.assertEqual(entries_by_id["NV-003"]["status"], "LIVE VERIFIED (GCP / REMOTE)")
        self.assertEqual(entries_by_id["NV-008"]["status"], "LIVE VERIFIED (GCP / REMOTE)")
        self.assertNotIn("additional_receipts", entries_by_id["NV-008"])

        # Secondary receipt with verified=None must still be rejected
        (isolated_artifacts / "wheel_parity_macos_receipt.json").write_text(
            json.dumps({
                "schema": "aegis-wheel-parity-receipt-v1",
                "truth_claim": False,
                "status": "PROVEN",
                "verified": None,
                "dry_run": False,
            }),
            encoding="utf-8",
        )
        runner.update_not_verified_registry(dry_run=False)
        updated_none = json.loads(reg_path.read_text(encoding="utf-8"))
        entries_none = {e["id"]: e for e in updated_none["entries"]}
        self.assertNotIn("additional_receipts", entries_none["NV-008"])

        # Now mark NV-008-M as PROVEN and verified=True -> must attach to additional_receipts
        (isolated_artifacts / "wheel_parity_macos_receipt.json").write_text(
            json.dumps({
                "schema": "aegis-wheel-parity-receipt-v1",
                "truth_claim": False,
                "status": "PROVEN",
                "verified": True,
                "dry_run": False,
            }),
            encoding="utf-8",
        )
        runner.update_not_verified_registry(dry_run=False)
        updated2 = json.loads(reg_path.read_text(encoding="utf-8"))
        entries_by_id2 = {e["id"]: e for e in updated2["entries"]}
        self.assertIn("additional_receipts", entries_by_id2["NV-008"])
        self.assertEqual(
            entries_by_id2["NV-008"]["additional_receipts"][0]["receipt_path"],
            "artifacts/wheel_parity_macos_receipt.json",
        )

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
        try:
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
        finally:
            if preview_path is not None and preview_path.is_file():
                preview_path.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    preview_path.parent.rmdir()

    def test_cli_dry_run_commands(self) -> None:
        """Subprocess tests checking CLI entry points with --help and --dry-run in isolated temp directory."""
        canonical_artifacts = ROOT / "artifacts"
        canonical_local = ROOT / ".local"
        before_hashes = get_artifacts_hashes(canonical_artifacts)
        before_local_hashes = get_artifacts_hashes(canonical_local)

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
                [sys.executable, "-B", str(ROOT / script), *extra_args],
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
        after_local_hashes = get_artifacts_hashes(canonical_local)
        assert_zero_directory_drift(
            self,
            canonical_local,
            before_local_hashes,
            after_local_hashes,
            context="test_cli_dry_run_commands (.local)",
        )

    def test_artifacts_directory_immutability(self) -> None:
        """Dedicated test ensuring all orchestrators and stages preserve canonical artifacts/ with zero disk drift."""
        canonical_artifacts = ROOT / "artifacts"
        canonical_local = ROOT / ".local"
        before_hashes = get_artifacts_hashes(canonical_artifacts)
        before_local_hashes = get_artifacts_hashes(canonical_local)

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
        after_local_hashes = get_artifacts_hashes(canonical_local)
        assert_zero_directory_drift(
            self,
            canonical_local,
            before_local_hashes,
            after_local_hashes,
            context="test_artifacts_directory_immutability (.local)",
        )


if __name__ == "__main__":
    unittest.main()
