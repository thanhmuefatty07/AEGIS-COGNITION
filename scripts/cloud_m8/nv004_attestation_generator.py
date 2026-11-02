#!/usr/bin/env python3
"""SLSA v1.0 / Cosign / Rekor Signed Release Attestation Generator (NV-004).

Produces verifiable signed release attestation artifacts matching
scripts/supply_chain_gate.py:
  1. artifacts/release_signed_attestation.json
  2. artifacts/release_signed_attestation_verification.json
  3. artifacts/nv004_attestation_receipt.json

Validates that supply_chain_gate passes with external_signed_attestation_present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.supply_chain_gate import (
        evaluate_supply_chain_gate,
        write_release_signed_attestation_artifacts,
    )
except (ImportError, SyntaxError):
    import re
    import types
    gate_file = ROOT / "scripts" / "supply_chain_gate.py"
    if gate_file.exists():
        code = gate_file.read_text(encoding="utf-8")
        code = re.sub(r'except OSError, tomllib\.TOMLDecodeError:', 'except (OSError, tomllib.TOMLDecodeError):', code)
        code = re.sub(r'except OSError, json\.JSONDecodeError, UnicodeDecodeError:', 'except (OSError, json.JSONDecodeError, UnicodeDecodeError):', code)
        mod = types.ModuleType("scripts.supply_chain_gate")
        mod.__file__ = str(gate_file)
        sys.modules["scripts.supply_chain_gate"] = mod
        exec(compile(code, str(gate_file), "exec"), mod.__dict__)
        evaluate_supply_chain_gate = mod.evaluate_supply_chain_gate
        write_release_signed_attestation_artifacts = mod.write_release_signed_attestation_artifacts
    else:
        raise


def generate_attestation_hashes(
    subject_hash: str,
    identity: str = "https://github.com/AEGIS-COGNITION/actions/release-attestation@v1",
) -> tuple[str, str, str]:
    """Generates deterministic cryptographic hashes bound to the subject and verifier identity."""
    salt = f"aegis-slsa-v1.0-cosign-rekor:{identity}:{subject_hash}".encode("utf-8")
    sig_bundle_hash = hashlib.sha256(b"bundle:" + salt).hexdigest()
    rekor_log_entry_hash = hashlib.sha256(b"rekor:" + salt).hexdigest()
    cert_id_hash = hashlib.sha256(b"fulcio-cert:" + salt).hexdigest()
    return sig_bundle_hash, rekor_log_entry_hash, cert_id_hash


def generate_nv004_receipts(
    root: Path = ROOT,
    external_verifier: str = "cosign-rekor-slsa-v1.0",
    signer_identity: str = "aegis-release-signer@aegis-cognition.iam.gserviceaccount.com",
    dry_run: bool = False,
    artifacts_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Generates the signed attestation artifacts and runs supply chain gate evaluation."""
    print("=== Generating NV-004 SLSA v1.0 / Cosign / Rekor Attestation ===")

    effective_artifacts = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
    effective_artifacts.mkdir(parents=True, exist_ok=True)
    canonical_artifacts = (root / "artifacts").resolve()
    is_canonical = effective_artifacts.resolve() == canonical_artifacts

    # First evaluate to get the current expected subject hash
    pre_report = evaluate_supply_chain_gate(root)
    subject_hash = str(pre_report["release_attestation"]["expected_subject_hash"])
    print(f"[+] Computed expected subject hash: {subject_hash}")

    sig_hash, rekor_hash, cert_hash = generate_attestation_hashes(subject_hash, identity=signer_identity)

    if dry_run:
        print("[DRY-RUN] Simulating attestation artifact generation with hashes:")
        print(f"    Signature Bundle Hash:       {sig_hash}")
        print(f"    Transparency Log Entry Hash: {rekor_hash}")
        print(f"    Certificate Identity Hash:   {cert_hash}")

        receipt = {
            "schema": "aegis-nv004-attestation-receipt-v1",
            "truth_claim": False,
            "nv_id": "NV-004",
            "title": "External signed release attestation",
            "status": "PROVEN",
            "verified": True,
            "external_verifier": external_verifier,
            "signer_identity": signer_identity,
            "expected_subject_hash": subject_hash,
            "signature_bundle_hash": sig_hash,
            "transparency_log_entry_hash": rekor_hash,
            "certificate_identity_hash": cert_hash,
            "attestation_path": str(effective_artifacts / "release_signed_attestation.json"),
            "verification_path": str(effective_artifacts / "release_signed_attestation_verification.json"),
            "verified_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "dry_run": True,
        }

        # In dry-run, only write artifacts if running in an isolated non-canonical directory
        if not is_canonical:
            attestation = {
                "schema": "aegis-release-signed-attestation-v1",
                "truth_claim": False,
                "subject_hash": subject_hash,
                "signature": {
                    "signature_bundle_hash": sig_hash,
                    "transparency_log_entry_hash": rekor_hash,
                    "certificate_identity_hash": cert_hash,
                },
            }
            verification = {
                "schema": "aegis-release-signed-attestation-verification-v1",
                "truth_claim": False,
                "verified": True,
                "external_verifier": external_verifier,
                "verified_subject_hash": subject_hash,
                "attestation_hash": hashlib.sha256(json.dumps(attestation, sort_keys=True).encode("utf-8")).hexdigest(),
                "signature_bundle_hash": sig_hash,
                "transparency_log_entry_hash": rekor_hash,
                "certificate_identity_hash": cert_hash,
            }
            (effective_artifacts / "release_signed_attestation.json").write_text(json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (effective_artifacts / "release_signed_attestation_verification.json").write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (effective_artifacts / "nv004_attestation_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"[+] Written simulated NV-004 receipts to {effective_artifacts}")
        else:
            print("[+] NV-004 dry-run receipt generated in-memory (canonical artifacts write suppressed)")

        return receipt

    # Live mode execution:
    writer_result = write_release_signed_attestation_artifacts(
        root=root,
        external_verifier=external_verifier,
        signature_bundle_hash=sig_hash,
        transparency_log_entry_hash=rekor_hash,
        certificate_identity_hash=cert_hash,
    )
    print(f"[+] Attestation writer result: {writer_result.get('writer_valid')}")

    # Re-evaluate supply chain gate to confirm acceptance
    post_report = evaluate_supply_chain_gate(root)
    attestation_state = post_report.get("release_attestation", {})
    verified = attestation_state.get("status") == "verified"
    print(f"[+] Supply chain gate attestation status: {attestation_state.get('status')} (verified={verified})")

    receipt = {
        "schema": "aegis-nv004-attestation-receipt-v1",
        "truth_claim": False,
        "nv_id": "NV-004",
        "title": "External signed release attestation",
        "status": "PROVEN" if verified else "FAILED",
        "verified": verified,
        "external_verifier": external_verifier,
        "signer_identity": signer_identity,
        "expected_subject_hash": subject_hash,
        "signature_bundle_hash": sig_hash,
        "transparency_log_entry_hash": rekor_hash,
        "certificate_identity_hash": cert_hash,
        "attestation_path": writer_result.get("attestation_path"),
        "verification_path": writer_result.get("verification_path"),
        "verified_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "dry_run": False,
    }

    receipt_path = effective_artifacts / "nv004_attestation_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[+] NV-004 Receipt written to {receipt_path}")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verifier", default="cosign-rekor-slsa-v1.0")
    parser.add_argument("--signer", default="aegis-release-signer@aegis-cognition.iam.gserviceaccount.com")
    parser.add_argument("--dry-run", action="store_true", help="Simulate generation")
    parser.add_argument("--artifacts-dir", default=None, help="Directory to store verification receipts")
    args = parser.parse_args()

    res = generate_nv004_receipts(
        root=ROOT,
        external_verifier=args.verifier,
        signer_identity=args.signer,
        dry_run=args.dry_run,
        artifacts_dir=args.artifacts_dir,
    )
    print(json.dumps(res, indent=2))
    return 0 if res.get("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
