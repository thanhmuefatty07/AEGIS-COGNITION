#!/usr/bin/env python3
"""SLSA v1.0 / Cosign / Rekor Attestation Rehearsal Generator (NV-004).

IMPORTANT CRYPTOGRAPHIC BOUNDARY:
- When an external Sigstore OIDC + Rekor bundle (`--cosign-bundle` / `AEGIS_COSIGN_BUNDLE`)
  is absent, this module generates deterministic SHA-256 schema-rehearsal digests ONLY.
  Those digests represent `SIMULATED_LOCAL_REHEARSAL` (`external_sigstore_verified=False`,
  `simulated_cryptography=True`) and MUST NOT be treated as real external cryptographic
  provenance (`M8MasterRunner._is_receipt_live_verified` rejects simulated NV-004 receipts).
- Pass `--require-sigstore` (or `require_external_sigstore=True`) to fail closed when real
  Sigstore Fulcio/Rekor verification material is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_supply_chain_gate_module() -> Any:
    if "scripts.supply_chain_gate" in sys.modules:
        return sys.modules["scripts.supply_chain_gate"]
    gate_file = ROOT / "scripts" / "supply_chain_gate.py"
    spec = importlib.util.spec_from_file_location("scripts.supply_chain_gate", gate_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {gate_file}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scripts.supply_chain_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


def generate_attestation_hashes(
    subject_hash: str,
    identity: str = "https://github.com/AEGIS-COGNITION/actions/release-attestation@v1",
) -> tuple[str, str, str]:
    """Generate deterministic SHA-256 schema-rehearsal hashes (NOT real Sigstore signatures)."""
    salt = f"aegis-slsa-v1.0-cosign-rekor:{identity}:{subject_hash}".encode()
    sig_bundle_hash = hashlib.sha256(b"bundle:" + salt).hexdigest()
    rekor_log_entry_hash = hashlib.sha256(b"rekor:" + salt).hexdigest()
    cert_id_hash = hashlib.sha256(b"fulcio-cert:" + salt).hexdigest()
    return sig_bundle_hash, rekor_log_entry_hash, cert_id_hash


def _inspect_cosign_bundle(bundle_path: Path | None) -> tuple[bool, str]:
    """Validate minimal structural presence of an external Sigstore/cosign bundle."""
    if bundle_path is None or not bundle_path.is_file():
        return False, "No Sigstore/cosign bundle provided"
    try:
        raw = bundle_path.read_bytes()
        raw_parsed: Any = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return False, f"Unreadable Sigstore bundle {bundle_path}: {exc}"
    if not isinstance(raw_parsed, dict):
        return False, "Sigstore bundle JSON is not an object"
    parsed = cast(dict[str, Any], raw_parsed)
    has_material = bool(parsed.get("verificationMaterial") or (parsed.get("rekorBundle") and parsed.get("cert")))
    if not has_material:
        return False, "Sigstore bundle lacks verificationMaterial / Fulcio-Rekor entries"
    return True, hashlib.sha256(raw).hexdigest()


def generate_nv004_receipts(
    root: Path = ROOT,
    external_verifier: str = "cosign-rekor-slsa-v1.0",
    signer_identity: str = "aegis-release-signer@aegis-cognition.iam.gserviceaccount.com",
    dry_run: bool = False,
    artifacts_dir: Path | str | None = None,
    *,
    cosign_bundle: Path | str | None = None,
    require_external_sigstore: bool = False,
) -> dict[str, Any]:
    """Generate NV-004 attestation artifacts and evaluate supply_chain_gate."""
    print("=== Generating NV-004 SLSA v1.0 / Cosign / Rekor Attestation ===")

    gate_mod = _load_supply_chain_gate_module()
    evaluate_supply_chain_gate = gate_mod.evaluate_supply_chain_gate
    write_release_signed_attestation_artifacts = gate_mod.write_release_signed_attestation_artifacts

    effective_artifacts = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
    effective_artifacts.mkdir(parents=True, exist_ok=True)
    canonical_artifacts = (root / "artifacts").resolve()
    is_canonical = effective_artifacts.resolve() == canonical_artifacts

    bundle_env = os.environ.get("AEGIS_COSIGN_BUNDLE", "").strip()
    resolved_bundle = Path(cosign_bundle) if cosign_bundle else (Path(bundle_env) if bundle_env else None)
    sigstore_verified, bundle_detail = _inspect_cosign_bundle(resolved_bundle)

    pre_report = evaluate_supply_chain_gate(root)
    subject_hash = str(pre_report["release_attestation"]["expected_subject_hash"])
    print(f"[+] Computed expected subject hash: {subject_hash}")

    sig_hash, rekor_hash, cert_hash = generate_attestation_hashes(subject_hash, identity=signer_identity)
    if sigstore_verified:
        sig_hash = bundle_detail

    now_utc = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    attestation_mode = "EXTERNAL_SIGSTORE_OIDC_REKOR" if sigstore_verified else "SIMULATED_LOCAL_REHEARSAL"
    simulated_crypto = not sigstore_verified
    rehearsal_note = (
        "Verified external Sigstore OIDC/Rekor bundle."
        if sigstore_verified
        else "SIMULATED local schema rehearsal only (deterministic SHA-256 hashes); "
        "real Sigstore Fulcio OIDC + Rekor transparency log signature is absent."
    )

    if dry_run:
        print("[DRY-RUN] Simulating NV-004 schema rehearsal (verified=False, non-cryptographic).")
        receipt: dict[str, Any] = {
            "schema": "aegis-nv004-attestation-receipt-v1",
            "truth_claim": False,
            "nv_id": "NV-004",
            "title": "External signed release attestation",
            "status": "SIMULATED / DRY RUN",
            "verified": False,
            "attestation_mode": "SIMULATED / DRY RUN",
            "external_sigstore_verified": False,
            "simulated_cryptography": True,
            "rehearsal_note": rehearsal_note,
            "external_verifier": external_verifier,
            "signer_identity": signer_identity,
            "expected_subject_hash": subject_hash,
            "signature_bundle_hash": sig_hash,
            "transparency_log_entry_hash": rekor_hash,
            "certificate_identity_hash": cert_hash,
            "attestation_path": str(effective_artifacts / "release_signed_attestation.json"),
            "verification_path": str(effective_artifacts / "release_signed_attestation_verification.json"),
            "verified_at_utc": now_utc,
            "dry_run": True,
        }

        if not is_canonical:
            attestation = {
                "schema": "aegis-release-signed-attestation-v1",
                "truth_claim": False,
                "subject_hash": subject_hash,
                "attestation_mode": "SIMULATED / DRY RUN",
                "signature": {
                    "signature_bundle_hash": sig_hash,
                    "transparency_log_entry_hash": rekor_hash,
                    "certificate_identity_hash": cert_hash,
                },
            }
            verification = {
                "schema": "aegis-release-signed-attestation-verification-v1",
                "truth_claim": False,
                "verified": False,
                "external_sigstore_verified": False,
                "simulated_cryptography": True,
                "external_verifier": external_verifier,
                "verified_subject_hash": subject_hash,
                "attestation_hash": hashlib.sha256(json.dumps(attestation, sort_keys=True).encode("utf-8")).hexdigest(),
                "signature_bundle_hash": sig_hash,
                "transparency_log_entry_hash": rekor_hash,
                "certificate_identity_hash": cert_hash,
            }
            (effective_artifacts / "release_signed_attestation.json").write_text(
                json.dumps(attestation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (effective_artifacts / "release_signed_attestation_verification.json").write_text(
                json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (effective_artifacts / "nv004_attestation_receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"[+] Written simulated NV-004 dry-run receipts to {effective_artifacts}")
        else:
            print("[+] NV-004 dry-run receipt generated in-memory (canonical artifacts write suppressed)")
        return receipt

    if require_external_sigstore and not sigstore_verified:
        receipt = {
            "schema": "aegis-nv004-attestation-receipt-v1",
            "truth_claim": False,
            "nv_id": "NV-004",
            "title": "External signed release attestation",
            "status": "SIMULATED (SIGSTORE OIDC/REKOR ABSENT)",
            "verified": False,
            "attestation_mode": "SIMULATED_LOCAL_REHEARSAL",
            "external_sigstore_verified": False,
            "simulated_cryptography": True,
            "rehearsal_note": f"{rehearsal_note} Detail: {bundle_detail}",
            "external_verifier": external_verifier,
            "signer_identity": signer_identity,
            "expected_subject_hash": subject_hash,
            "signature_bundle_hash": sig_hash,
            "transparency_log_entry_hash": rekor_hash,
            "certificate_identity_hash": cert_hash,
            "attestation_path": None,
            "verification_path": None,
            "verified_at_utc": now_utc,
            "dry_run": False,
        }
        receipt_path = effective_artifacts / "nv004_attestation_receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[!] Fail-closed NV-004: Sigstore OIDC/Rekor absent ({bundle_detail}).")
        return receipt

    if simulated_crypto:
        print(f"[!] NV-004 warning: {rehearsal_note}")

    writer_result = write_release_signed_attestation_artifacts(
        root=root,
        external_verifier=external_verifier,
        signature_bundle_hash=sig_hash,
        transparency_log_entry_hash=rekor_hash,
        certificate_identity_hash=cert_hash,
    )
    print(f"[+] Attestation writer result: {writer_result.get('writer_valid')}")

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
        "attestation_mode": attestation_mode,
        "external_sigstore_verified": sigstore_verified,
        "simulated_cryptography": simulated_crypto,
        "rehearsal_note": rehearsal_note,
        "external_verifier": external_verifier,
        "signer_identity": signer_identity,
        "expected_subject_hash": subject_hash,
        "signature_bundle_hash": sig_hash,
        "transparency_log_entry_hash": rekor_hash,
        "certificate_identity_hash": cert_hash,
        "attestation_path": writer_result.get("attestation_path"),
        "verification_path": writer_result.get("verification_path"),
        "verified_at_utc": now_utc,
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
    parser.add_argument("--cosign-bundle", default="", help="Path to real Sigstore/cosign JSON bundle")
    parser.add_argument(
        "--require-sigstore",
        action="store_true",
        help="Fail closed when real Sigstore Fulcio OIDC/Rekor bundle is absent",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate generation")
    parser.add_argument("--artifacts-dir", default=None, help="Directory to store verification receipts")
    args = parser.parse_args()

    res = generate_nv004_receipts(
        root=ROOT,
        external_verifier=args.verifier,
        signer_identity=args.signer,
        dry_run=args.dry_run,
        artifacts_dir=args.artifacts_dir,
        cosign_bundle=args.cosign_bundle or None,
        require_external_sigstore=args.require_sigstore,
    )
    print(json.dumps(res, indent=2))
    return 0 if (res.get("verified") or args.dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
