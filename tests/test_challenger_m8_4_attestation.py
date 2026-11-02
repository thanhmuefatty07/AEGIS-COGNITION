"""Empirical Challenger Test Suite for Milestone M8.4 SLSA Attestation (NV-004) & Supply Chain Gate.

Adversarially stress-tests:
1. Baseline authentic verification: evaluate_supply_chain_gate(ROOT) passes with 20/20 checks,
   external_signed_attestation_present == True, status == "verified", and overall_ok == True.
2. Cryptographic fault injections (all must FAIL CLOSED: status != "verified", overall_ok == False):
   - Corrupted subject_hash in release_signed_attestation.json
   - Corrupted verified_subject_hash in release_signed_attestation_verification.json
   - Corrupted signature_bundle_hash (attestation vs verification mismatch, invalid hex)
   - Corrupted transparency_log_entry_hash (mismatch, bit-flip, invalid hex)
   - Corrupted certificate_identity_hash (mismatch, invalid hex)
   - Corrupted/modified attestation_hash in verification
   - Empty, whitespace, None, or non-string external_verifier
   - Malformed/non-hex or non-64-char hashes
   - Schema tampering (aegis-release-signed-attestation-v1 mutated)
   - Single artifact missing (partial state must fail closed as 'invalid', not 'missing')
3. write_release_signed_attestation_artifacts API rejection validation.
4. nv004_attestation_receipt.json authenticity, non-simulation, and Q13 truth_claim=False compliance.
5. Global zero-drift assertion on canonical artifacts/.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.supply_chain_gate import (
    evaluate_supply_chain_gate,
    write_release_signed_attestation_artifacts,
)


def get_artifacts_hashes(artifacts_dir: Path = ROOT / "artifacts") -> dict[str, str]:
    """Computes SHA-256 tree of all files in artifacts_dir relative to artifacts_dir."""
    hashes: dict[str, str] = {}
    if not artifacts_dir.is_dir():
        return hashes
    for p in sorted(artifacts_dir.glob("**/*")):
        if p.is_file():
            hashes[p.relative_to(artifacts_dir).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


class TestChallengerM84Attestation(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Snapshot canonical artifacts for global zero-drift verification
        cls.initial_canonical_hashes = get_artifacts_hashes(ROOT / "artifacts")
        
        # Set up an isolated sandbox repository with full provenance & source files
        cls.sandbox_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.sandbox_path = Path(cls.sandbox_dir.name)

        # Copy manifests
        for item in ["Cargo.lock", "Cargo.toml", "pyproject.toml"]:
            shutil.copy(ROOT / item, cls.sandbox_path / item)

        # Copy source and schemas ignoring build caches
        shutil.copytree(
            ROOT / "core",
            cls.sandbox_path / "core",
            ignore=shutil.ignore_patterns("target", "__pycache__", ".pytest_cache"),
        )
        shutil.copytree(ROOT / "schemas", cls.sandbox_path / "schemas")
        shutil.copytree(
            ROOT / "fuzz",
            cls.sandbox_path / "fuzz",
            ignore=shutil.ignore_patterns("target", "__pycache__"),
        )
        (cls.sandbox_path / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "scripts" / "secret_scan.py", cls.sandbox_path / "scripts" / "secret_scan.py")

        # Copy canonical artifacts into sandbox
        shutil.copytree(ROOT / "artifacts", cls.sandbox_path / "artifacts")

        # Load baseline authentic payloads
        cls.baseline_attestation_path = cls.sandbox_path / "artifacts" / "release_signed_attestation.json"
        cls.baseline_verification_path = cls.sandbox_path / "artifacts" / "release_signed_attestation_verification.json"
        cls.baseline_attestation = json.loads(cls.baseline_attestation_path.read_text(encoding="utf-8"))
        cls.baseline_verification = json.loads(cls.baseline_verification_path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.sandbox_dir.cleanup()
        except Exception:
            pass

        # Global Zero-Drift assertion on canonical ROOT / artifacts
        final_canonical_hashes = get_artifacts_hashes(ROOT / "artifacts")
        modified = [
            k for k in final_canonical_hashes
            if k in cls.initial_canonical_hashes and final_canonical_hashes[k] != cls.initial_canonical_hashes[k]
        ]
        created = [k for k in final_canonical_hashes if k not in cls.initial_canonical_hashes]
        deleted = [k for k in cls.initial_canonical_hashes if k not in final_canonical_hashes]

        if modified or created or deleted:
            raise AssertionError(
                f"Global Zero-Drift Violation: ROOT/artifacts was modified during TestChallengerM84Attestation execution!\n"
                f"Modified: {modified}\nCreated: {created}\nDeleted: {deleted}"
            )

    def setUp(self) -> None:
        # Reset sandbox artifacts to baseline before each test
        self.sandbox_artifacts = self.sandbox_path / "artifacts"
        self.attestation_file = self.sandbox_artifacts / "release_signed_attestation.json"
        self.verification_file = self.sandbox_artifacts / "release_signed_attestation_verification.json"

        self.attestation_file.write_text(
            json.dumps(self.baseline_attestation, indent=2, sort_keys=True), encoding="utf-8"
        )
        self.verification_file.write_text(
            json.dumps(self.baseline_verification, indent=2, sort_keys=True), encoding="utf-8"
        )

    # =========================================================================
    # 1. Baseline Authentic Verification
    # =========================================================================
    def test_canonical_artifacts_baseline_acceptance(self) -> None:
        """Confirms that evaluate_supply_chain_gate(ROOT) passes 20/20 checks with authentic artifacts."""
        report = evaluate_supply_chain_gate(ROOT)
        self.assertTrue(report.get("overall_ok"), f"Gate failed on canonical ROOT: {report.get('checks')}")
        self.assertEqual(report.get("passed"), 20)
        self.assertEqual(report.get("failed"), 0)
        self.assertTrue(report.get("external_signed_attestation_present"))
        self.assertFalse(report.get("production_release_blocked_without_external_attestation"))

        rel = report.get("release_attestation", {})
        self.assertEqual(rel.get("status"), "verified")
        self.assertEqual(rel.get("error"), "")
        self.assertTrue(rel.get("attestation_state_recorded"))
        self.assertTrue(rel.get("external_signed_attestation_present"))

    def test_nv004_receipt_authenticity(self) -> None:
        """Verifies nv004_attestation_receipt.json authenticity, non-simulation, and Q13 compliance."""
        receipt_path = ROOT / "artifacts" / "nv004_attestation_receipt.json"
        self.assertTrue(receipt_path.is_file(), "nv004_attestation_receipt.json must exist")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(receipt.get("schema"), "aegis-nv004-attestation-receipt-v1")
        self.assertEqual(receipt.get("nv_id"), "NV-004")
        self.assertEqual(receipt.get("status"), "PROVEN")
        self.assertTrue(receipt.get("verified"))
        self.assertFalse(receipt.get("truth_claim"), "truth_claim must be False per Q13")
        self.assertFalse(receipt.get("dry_run"), "dry_run must be False (not simulated)")
        self.assertEqual(receipt.get("external_verifier"), "cosign-rekor-slsa-v1.0")

        # Verify cryptographic hash fields are 64-char lowercase hex
        for field in ("expected_subject_hash", "signature_bundle_hash", "transparency_log_entry_hash", "certificate_identity_hash"):
            val = receipt.get(field, "")
            self.assertEqual(len(val), 64, f"Field {field} must be 64 chars")
            self.assertTrue(all(c in "0123456789abcdef" for c in val), f"Field {field} must be lowercase hex")

    # =========================================================================
    # 2. Cryptographic Fault Injection: Subject Hash Corruptions
    # =========================================================================
    def test_fault_injection_corrupt_attestation_subject_hash(self) -> None:
        """Corrupting subject_hash in release_signed_attestation.json must fail closed."""
        mutated_attestation = copy.deepcopy(self.baseline_attestation)
        mutated_attestation["subject_hash"] = "deadbeef" * 8  # 64-char wrong hash
        self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"], "Corrupted subject_hash must fail gate")
        self.assertFalse(report["external_signed_attestation_present"])
        self.assertTrue(report["production_release_blocked_without_external_attestation"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")
        self.assertIn("not externally verified", report["release_attestation"]["error"])

        failed_check_names = {c["name"] for c in report["checks"] if not c["ok"]}
        self.assertIn("release_attestation_admission_valid", failed_check_names)
        self.assertIn("external_attestation_gap_disclosed", failed_check_names)

    def test_fault_injection_corrupt_verification_verified_subject_hash(self) -> None:
        """Corrupting verified_subject_hash in release_signed_attestation_verification.json must fail closed."""
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["verified_subject_hash"] = "badc0ffe" * 8
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertFalse(report["external_signed_attestation_present"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 3. Cryptographic Fault Injection: Signature Bundle Hash Corruptions
    # =========================================================================
    def test_fault_injection_corrupt_attestation_signature_bundle_hash(self) -> None:
        """Corrupting signature_bundle_hash in attestation signature block must fail closed."""
        mutated_attestation = copy.deepcopy(self.baseline_attestation)
        mutated_attestation["signature"]["signature_bundle_hash"] = "11" * 32
        self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertFalse(report["external_signed_attestation_present"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    def test_fault_injection_corrupt_verification_signature_bundle_hash(self) -> None:
        """Corrupting signature_bundle_hash in verification record must fail closed."""
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["signature_bundle_hash"] = "22" * 32
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertFalse(report["external_signed_attestation_present"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 4. Cryptographic Fault Injection: Transparency Log Hash Corruptions
    # =========================================================================
    def test_fault_injection_corrupt_attestation_transparency_log_hash(self) -> None:
        """Corrupting transparency_log_entry_hash in attestation must fail closed."""
        mutated_attestation = copy.deepcopy(self.baseline_attestation)
        mutated_attestation["signature"]["transparency_log_entry_hash"] = "33" * 32
        self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    def test_fault_injection_corrupt_verification_transparency_log_hash(self) -> None:
        """Corrupting transparency_log_entry_hash in verification must fail closed."""
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["transparency_log_entry_hash"] = "44" * 32
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 5. Cryptographic Fault Injection: Certificate Identity Hash Corruptions
    # =========================================================================
    def test_fault_injection_corrupt_certificate_identity_hash(self) -> None:
        """Corrupting certificate_identity_hash in attestation or verification must fail closed."""
        # Mutate attestation certificate_identity_hash
        mutated_attestation = copy.deepcopy(self.baseline_attestation)
        mutated_attestation["signature"]["certificate_identity_hash"] = "55" * 32
        self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

        # Reset attestation and mutate verification certificate_identity_hash
        self.attestation_file.write_text(json.dumps(self.baseline_attestation, indent=2), encoding="utf-8")
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["certificate_identity_hash"] = "66" * 32
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report2 = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report2["overall_ok"])
        self.assertEqual(report2["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 6. Cryptographic Fault Injection: Attestation Payload Hash Invalidation
    # =========================================================================
    def test_fault_injection_modified_attestation_hash(self) -> None:
        """Modifying attestation_hash in verification must fail closed."""
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["attestation_hash"] = "77" * 32
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertFalse(report["external_signed_attestation_present"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    def test_fault_injection_subtle_whitespace_payload_mutation(self) -> None:
        """Adding a benign-looking comment or whitespace to attestation changes payload hash -> fails closed."""
        mutated_attestation = copy.deepcopy(self.baseline_attestation)
        mutated_attestation["extra_field"] = "malicious_unattested_injection"
        self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 7. Verifier Validation & Admission Edge Cases
    # =========================================================================
    def test_fault_injection_empty_or_invalid_verifier(self) -> None:
        """Empty, whitespace, non-string, or missing external_verifier must fail closed."""
        for invalid_verifier in ["", "   ", None, 12345, []]:
            mutated_verification = copy.deepcopy(self.baseline_verification)
            mutated_verification["external_verifier"] = invalid_verifier
            self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

            report = evaluate_supply_chain_gate(self.sandbox_path)
            self.assertFalse(report["overall_ok"], f"Verifier '{invalid_verifier}' should fail")
            self.assertEqual(
                report["release_attestation"]["status"],
                "invalid",
                f"Verifier '{invalid_verifier}' must result in status='invalid'",
            )

    def test_fault_injection_verified_boolean_tampering(self) -> None:
        """Setting verified=False in verification record must fail closed."""
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["verified"] = False
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 8. Malformed / Non-Hex Hash Fault Injections
    # =========================================================================
    def test_fault_injection_malformed_hashes(self) -> None:
        """Hashes that are all-zeros, non-hex, or truncated must fail closed."""
        malformed_samples = [
            "0" * 64,             # all zeros rejected by _nonzero_hex
            "f" * 63,             # 63 characters (too short)
            "f" * 65,             # 65 characters (too long)
            "g" * 64,             # invalid hex characters
            "",                   # empty string
        ]
        for bad_hash in malformed_samples:
            mutated_verification = copy.deepcopy(self.baseline_verification)
            mutated_verification["signature_bundle_hash"] = bad_hash
            # Also sync attestation so mismatch check doesn't shadow _nonzero_hex
            mutated_attestation = copy.deepcopy(self.baseline_attestation)
            mutated_attestation["signature"]["signature_bundle_hash"] = bad_hash

            self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")
            self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

            report = evaluate_supply_chain_gate(self.sandbox_path)
            self.assertFalse(report["overall_ok"], f"Hash '{bad_hash}' should fail closed")
            self.assertEqual(report["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 9. Schema Invalidation
    # =========================================================================
    def test_fault_injection_schema_tampering(self) -> None:
        """Altering schema identifier in attestation or verification must fail closed."""
        # Mutate attestation schema
        mutated_attestation = copy.deepcopy(self.baseline_attestation)
        mutated_attestation["schema"] = "tampered-schema-v2"
        self.attestation_file.write_text(json.dumps(mutated_attestation, indent=2), encoding="utf-8")

        report = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report["overall_ok"])
        self.assertEqual(report["release_attestation"]["status"], "invalid")

        # Reset attestation and mutate verification schema
        self.attestation_file.write_text(json.dumps(self.baseline_attestation, indent=2), encoding="utf-8")
        mutated_verification = copy.deepcopy(self.baseline_verification)
        mutated_verification["schema"] = "tampered-verification-v2"
        self.verification_file.write_text(json.dumps(mutated_verification, indent=2), encoding="utf-8")

        report2 = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report2["overall_ok"])
        self.assertEqual(report2["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 10. Single Artifact Missing (Partial State Rejection)
    # =========================================================================
    def test_fault_injection_partial_missing_artifacts(self) -> None:
        """If only one artifact is present (attestation without verification or vice versa), gate fails closed."""
        # Case A: Attestation present, verification removed
        self.verification_file.unlink(missing_ok=True)
        report_a = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report_a["overall_ok"])
        self.assertFalse(report_a["external_signed_attestation_present"])
        self.assertEqual(report_a["release_attestation"]["status"], "invalid")

        # Case B: Verification present, attestation removed
        self.verification_file.write_text(json.dumps(self.baseline_verification, indent=2), encoding="utf-8")
        self.attestation_file.unlink(missing_ok=True)
        report_b = evaluate_supply_chain_gate(self.sandbox_path)
        self.assertFalse(report_b["overall_ok"])
        self.assertFalse(report_b["external_signed_attestation_present"])
        self.assertEqual(report_b["release_attestation"]["status"], "invalid")

    # =========================================================================
    # 11. Complete Missing Artifacts Behavior
    # =========================================================================
    def test_missing_artifacts_admission_behavior(self) -> None:
        """When both artifacts are absent, status is 'missing', blocking production release."""
        self.attestation_file.unlink(missing_ok=True)
        self.verification_file.unlink(missing_ok=True)

        report = evaluate_supply_chain_gate(self.sandbox_path)
        rel = report["release_attestation"]
        self.assertEqual(rel["status"], "missing")
        self.assertFalse(report["external_signed_attestation_present"])
        self.assertTrue(report["production_release_blocked_without_external_attestation"])
        # Overall ok is True only if external gap is disclosed, but external_signed_attestation_present is False
        self.assertTrue(report["overall_ok"])

    # =========================================================================
    # 12. Artifact Writer Rejection Tests
    # =========================================================================
    def test_writer_api_rejects_empty_or_invalid_inputs(self) -> None:
        """write_release_signed_attestation_artifacts must reject invalid parameters."""
        dummy_dir = self.sandbox_path / "writer_test"
        dummy_dir.mkdir(parents=True, exist_ok=True)

        valid_sig = "a" * 64
        valid_rekor = "b" * 64
        valid_cert = "c" * 64

        # 1. Empty verifier
        res1 = write_release_signed_attestation_artifacts(
            root=dummy_dir,
            external_verifier="",
            signature_bundle_hash=valid_sig,
            transparency_log_entry_hash=valid_rekor,
            certificate_identity_hash=valid_cert,
        )
        self.assertFalse(res1["writer_valid"])
        self.assertFalse(res1["artifacts_written"])

        # 2. Invalid signature hash (too short)
        res2 = write_release_signed_attestation_artifacts(
            root=dummy_dir,
            external_verifier="cosign",
            signature_bundle_hash="abc",
            transparency_log_entry_hash=valid_rekor,
            certificate_identity_hash=valid_cert,
        )
        self.assertFalse(res2["writer_valid"])

        # 3. All-zero transparency log hash
        res3 = write_release_signed_attestation_artifacts(
            root=dummy_dir,
            external_verifier="cosign",
            signature_bundle_hash=valid_sig,
            transparency_log_entry_hash="0" * 64,
            certificate_identity_hash=valid_cert,
        )
        self.assertFalse(res3["writer_valid"])


if __name__ == "__main__":
    unittest.main()
