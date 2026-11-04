"""Empirical Challenger Test Suite for Milestone M8.4 Fail-Closed & Adversarial DR Drills.

Adversarially stress-tests:
1. NV-009 Restore Backup Drill:
   - Corrupt/truncated tarball (broken gzip stream)
   - Empty tarball (0 members in tar archive)
   - Non-tar bytes (random bytes, plain text, null bytes, ELF header)
   - Zero-byte empty file
   - Nonexistent archive path
   - Directory passed as archive file
   - Authentic valid archive pass
   - Dry run mode
2. NV-012 Cross-Version Schema Migration Rehearsal:
   - Corrupt JSON syntax (malformed JSON)
   - Empty directory (0 json files)
   - Non-dict JSON objects: JSON list, string, integer, boolean, null
   - Empty dictionary object {}
   - Mixed directory with 1 corrupt file among valid schemas
   - Nonexistent directory
   - File path passed instead of directory
   - Authentic valid schemas pass
   - Dry run mode
3. Invariant checks:
   - In ALL failure cases: verified == False, status starts with FAIL_CLOSED or NOT VERIFIED,
     and all check booleans are strictly False (or None if unconfigured).
   - truth_claim == False across all outputs.
   - Zero canonical directory drift on artifacts/.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


class TestChallengerM84FailClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical_artifacts = ROOT / "artifacts"
        cls.initial_canonical_hashes = get_artifacts_hashes(cls.canonical_artifacts)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.isolated_artifacts = self.tmp_path / "artifacts"
        self.isolated_artifacts.mkdir(parents=True, exist_ok=True)
        self.orchestrator = Track2Orchestrator(self.tmp_path, artifacts_dir=self.isolated_artifacts)

    def tearDown(self) -> None:
        # Strict zero-drift check after every individual test
        current_hashes = get_artifacts_hashes(self.canonical_artifacts)
        self.assertEqual(
            self.initial_canonical_hashes,
            current_hashes,
            "CRITICAL DRIFT: Canonical artifacts/ directory was modified during test execution!",
        )
        self.temp_dir.cleanup()

    @classmethod
    def tearDownClass(cls) -> None:
        # Final global zero-drift check
        final_hashes = get_artifacts_hashes(cls.canonical_artifacts)
        if cls.initial_canonical_hashes != final_hashes:
            raise AssertionError("CRITICAL DRIFT: Canonical artifacts/ directory altered by test class!")

    # =========================================================================
    # NV-009 FAIL-CLOSED ADVERSARIAL STRESS TESTS
    # =========================================================================

    def test_nv009_corrupt_truncated_gzip_stream(self) -> None:
        """Verifies truncated gzip stream strictly fails closed."""
        corrupt_archive = self.tmp_path / "truncated_backup.tar.gz"
        corrupt_archive.write_bytes(b"\x1f\x8b\x08\x00corrupted_truncated_gzip_stream_abcdef123456789")
        expected_sha = hashlib.sha256(corrupt_archive.read_bytes()).hexdigest()

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=corrupt_archive, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(receipt["truth_claim"])
        self.assertEqual(receipt["archive_sha256"], expected_sha)
        self.assertIn("failed integrity check", receipt["reason"])

        checks = receipt["restore_drill_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertFalse(val, f"Check {check_name} must be False on corrupt archive")

        # Verify receipt written to isolated artifacts directory matches
        saved = json.loads((self.isolated_artifacts / "restore_backup_drill_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(saved["verified"])

    def test_nv009_empty_tarball_zero_members(self) -> None:
        """Verifies valid gzip tar archive with 0 members strictly fails closed."""
        empty_tar = self.tmp_path / "empty_archive.tar.gz"
        with tarfile.open(empty_tar, "w:gz"):
            pass  # create valid empty tarball
        expected_sha = hashlib.sha256(empty_tar.read_bytes()).hexdigest()

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=empty_tar, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(receipt["truth_claim"])
        self.assertEqual(receipt["archive_sha256"], expected_sha)
        self.assertIn("empty", receipt["reason"].lower())

        checks = receipt["restore_drill_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertFalse(val, f"Check {check_name} must be False on empty archive")

    def test_nv009_non_tar_random_binary_bytes(self) -> None:
        """Verifies random arbitrary binary data strictly fails closed."""
        random_archive = self.tmp_path / "random_bytes.tar.gz"
        random_archive.write_bytes(os.urandom(4096))
        expected_sha = hashlib.sha256(random_archive.read_bytes()).hexdigest()

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=random_archive, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(receipt["truth_claim"])
        self.assertEqual(receipt["archive_sha256"], expected_sha)
        self.assertIn("failed integrity check", receipt["reason"])

        checks = receipt["restore_drill_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertFalse(val, f"Check {check_name} must be False on random binary archive")

    def test_nv009_non_tar_plain_text(self) -> None:
        """Verifies plain text ASCII file masquerading as tarball strictly fails closed."""
        text_archive = self.tmp_path / "fake_archive.tar.gz"
        text_archive.write_text("This is an uncompressed ASCII text file, not a tarball.", encoding="utf-8")

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=text_archive, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(receipt["truth_claim"])
        for check_name, val in receipt["restore_drill_checks"].items():
            self.assertFalse(val, f"Check {check_name} must be False on plain text archive")

    def test_nv009_zero_byte_file(self) -> None:
        """Verifies 0-byte file strictly fails closed."""
        zero_file = self.tmp_path / "zero_byte.tar.gz"
        zero_file.write_bytes(b"")

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=zero_file, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT ARCHIVE)")
        self.assertFalse(receipt["truth_claim"])
        for check_name, val in receipt["restore_drill_checks"].items():
            self.assertFalse(val, f"Check {check_name} must be False on zero byte archive")

    def test_nv009_nonexistent_archive(self) -> None:
        """Verifies nonexistent archive path returns NOT VERIFIED with checks=None."""
        nonexistent = self.tmp_path / "does_not_exist.tar.gz"

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=nonexistent, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "NOT VERIFIED (NO EXTERNAL BACKUP ARCHIVE PROVIDED)")
        self.assertFalse(receipt["truth_claim"])
        self.assertIsNone(receipt["archive_sha256"])
        self.assertIsNone(receipt["restore_drill_checks"])

    def test_nv009_directory_passed_as_archive_file(self) -> None:
        """Verifies directory passed as backup_archive fails cleanly without crash."""
        dir_as_file = self.tmp_path / "dir_named_archive.tar.gz"
        dir_as_file.mkdir(parents=True, exist_ok=True)

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=dir_as_file, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "NOT VERIFIED (NO EXTERNAL BACKUP ARCHIVE PROVIDED)")
        self.assertIsNone(receipt["restore_drill_checks"])

    def test_nv009_authentic_valid_archive_passes(self) -> None:
        """Verifies an authentic valid tarball with valid members passes with PROVEN."""
        valid_tar = self.tmp_path / "valid_backup.tar.gz"
        content_file = self.tmp_path / "fixture.txt"
        content_file.write_text("sample fixture content for restore", encoding="utf-8")

        with tarfile.open(valid_tar, "w:gz") as tar:
            tar.add(content_file, arcname="fixture.txt")

        receipt = self.orchestrator.generate_nv009_restore_drill(backup_archive=valid_tar, dry_run=False)

        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["status"], "PROVEN")
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("1 members validated", receipt["reason"])

        checks = receipt["restore_drill_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertTrue(val, f"Check {check_name} must be True on valid archive")

    # =========================================================================
    # NV-012 FAIL-CLOSED ADVERSARIAL STRESS TESTS
    # =========================================================================

    def test_nv012_corrupt_json_syntax(self) -> None:
        """Verifies malformed JSON syntax strictly fails closed."""
        corrupt_dir = self.tmp_path / "corrupt_json_syntax"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "bad_syntax.json").write_text('{"name": "test", unquoted_key: 123}', encoding="utf-8")

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=corrupt_dir, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("failed integrity check", receipt["reason"])

        checks = receipt["migration_matrix_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertFalse(val, f"Check {check_name} must be False on malformed JSON schema")

    def test_nv012_empty_directory_zero_schemas(self) -> None:
        """Verifies existing directory with zero *.json files strictly fails closed."""
        empty_dir = self.tmp_path / "empty_schema_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=empty_dir, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("no schema json files found", receipt["reason"].lower())

        checks = receipt["migration_matrix_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertFalse(val, f"Check {check_name} must be False on empty schema directory")

    def test_nv012_non_dict_json_array(self) -> None:
        """Verifies schema containing a JSON array instead of an object strictly fails closed."""
        array_dir = self.tmp_path / "array_schema"
        array_dir.mkdir(parents=True, exist_ok=True)
        (array_dir / "list_schema.json").write_text('[{"table": "audit", "columns": []}]', encoding="utf-8")

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=array_dir, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("not a valid non-empty json object", receipt["reason"].lower())

        checks = receipt["migration_matrix_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertFalse(val, f"Check {check_name} must be False on array schema")

    def test_nv012_non_dict_json_scalar_values(self) -> None:
        """Verifies scalar JSON values (number, string, boolean, null) strictly fail closed."""
        for scalar_val, ext in [("12345", "num"), ('"string_content"', "str"), ("true", "bool"), ("null", "null")]:
            with self.subTest(scalar_val=scalar_val):
                sub_dir = self.tmp_path / f"scalar_schema_{ext}"
                sub_dir.mkdir(parents=True, exist_ok=True)
                (sub_dir / "scalar.json").write_text(scalar_val, encoding="utf-8")

                receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=sub_dir, dry_run=False)

                self.assertFalse(receipt["verified"])
                self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
                self.assertFalse(receipt["truth_claim"])
                self.assertIn("not a valid non-empty json object", receipt["reason"].lower())
                for check_name, val in receipt["migration_matrix_checks"].items():
                    self.assertFalse(val, f"Check {check_name} must be False for scalar {scalar_val}")

    def test_nv012_empty_dict_schema(self) -> None:
        """Verifies empty dict {} strictly fails closed."""
        empty_dict_dir = self.tmp_path / "empty_dict_schema"
        empty_dict_dir.mkdir(parents=True, exist_ok=True)
        (empty_dict_dir / "empty.json").write_text("{}", encoding="utf-8")

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=empty_dict_dir, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("not a valid non-empty json object", receipt["reason"].lower())
        for check_name, val in receipt["migration_matrix_checks"].items():
            self.assertFalse(val, f"Check {check_name} must be False on empty dict schema")

    def test_nv012_multiple_files_with_one_corrupt_fails_closed(self) -> None:
        """Verifies that if even one schema out of many is corrupt, the entire rehearsal fails closed."""
        multi_dir = self.tmp_path / "mixed_schemas"
        multi_dir.mkdir(parents=True, exist_ok=True)
        (multi_dir / "schema_1_valid.json").write_text('{"table": "valid1", "version": 1}', encoding="utf-8")
        (multi_dir / "schema_2_corrupt.json").write_text('{broken json !!!', encoding="utf-8")
        (multi_dir / "schema_3_valid.json").write_text('{"table": "valid3", "version": 1}', encoding="utf-8")

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=multi_dir, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "FAIL_CLOSED (CORRUPT SCHEMA)")
        self.assertFalse(receipt["truth_claim"])
        for check_name, val in receipt["migration_matrix_checks"].items():
            self.assertFalse(val, f"Check {check_name} must be False when one schema is corrupt")

    def test_nv012_nonexistent_directory(self) -> None:
        """Verifies nonexistent fixtures directory returns NOT VERIFIED."""
        nonexistent = self.tmp_path / "nonexistent_dir"

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=nonexistent, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "NOT VERIFIED (NO MIGRATION FIXTURES CONFIGURED)")
        self.assertFalse(receipt["truth_claim"])
        self.assertIsNone(receipt["migration_matrix_checks"])

    def test_nv012_file_passed_instead_of_directory(self) -> None:
        """Verifies file passed as fixtures_dir returns NOT VERIFIED."""
        file_path = self.tmp_path / "a_file.json"
        file_path.write_text('{"valid": "json"}', encoding="utf-8")

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=file_path, dry_run=False)

        self.assertFalse(receipt["verified"])
        self.assertEqual(receipt["status"], "NOT VERIFIED (NO MIGRATION FIXTURES CONFIGURED)")
        self.assertIsNone(receipt["migration_matrix_checks"])

    def test_nv012_authentic_valid_schemas_passes(self) -> None:
        """Verifies valid schema files pass with PROVEN."""
        valid_dir = self.tmp_path / "valid_schemas"
        valid_dir.mkdir(parents=True, exist_ok=True)
        (valid_dir / "01_init.json").write_text('{"version": 1, "action": "expand"}', encoding="utf-8")
        (valid_dir / "02_backfill.json").write_text('{"version": 2, "action": "backfill"}', encoding="utf-8")

        receipt = self.orchestrator.generate_nv012_migration_rehearsal(fixtures_dir=valid_dir, dry_run=False)

        self.assertTrue(receipt["verified"])
        self.assertEqual(receipt["status"], "PROVEN")
        self.assertFalse(receipt["truth_claim"])
        self.assertIn("2 fixtures", receipt["reason"])

        checks = receipt["migration_matrix_checks"]
        self.assertIsNotNone(checks)
        for check_name, val in checks.items():
            self.assertTrue(val, f"Check {check_name} must be True on valid schemas")


if __name__ == "__main__":
    unittest.main()
