import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "supply_chain_gate_report.json"

RUST_PROVENANCE_FILES: tuple[str, ...] = (
    "Cargo.lock",
    "Cargo.toml",
    "core/rust/Cargo.toml",
    "core/rust/src/sandbox.rs",
    "core/rust/src/resource.rs",
    "core/rust/src/resource_platform.rs",
    "core/rust/src/runtime.rs",
    "core/rust/src/execution.rs",
    "core/rust/src/tool_gateway.rs",
    "schemas/lease-token-v1.json",
)
PYTHON_PROVENANCE_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "core/python/pyproject.toml",
)
FORBIDDEN_WASI_PATTERNS: tuple[str, ...] = (
    "wasmtime-wasi",
    "wasmtime_wasi",
    "wasi_common",
    "WasiCtx",
    "WasiCtxBuilder",
    "WasiView",
    "preopen_dir",
    "preopened_dir",
    "inherit_stdio",
    "inherit_env",
    "ambient_authority",
    "cap_std",
)
RUNTIME_SOURCE_GLOBS: tuple[str, ...] = (
    "core/rust/src/*.rs",
    "core/rust/src/**/*.rs",
    "core/rust/Cargo.toml",
)


@dataclass(frozen=True)
class CargoPackage:
    name: str
    version: str
    source: str
    checksum: str

    def record(self) -> dict[str, str]:
        checksum_digest = _stable_hash(self.checksum) if self.checksum else ""
        payload = {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "checksum_digest": checksum_digest,
        }
        payload["component_digest"] = _stable_hash(payload)
        return payload


def evaluate_supply_chain_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    cargo_lock = _read_toml(root_path / "Cargo.lock")
    rust_manifest = _read_toml(root_path / "core" / "rust" / "Cargo.toml")
    python_manifest = _read_toml(root_path / "pyproject.toml")
    packages = tuple(
        package
        for package in (_cargo_package(entry) for entry in cargo_lock.get("package", []))
        if package is not None
    )
    rust_direct = _dependency_names(_all_dependencies(rust_manifest))
    python_direct = tuple(str(dep) for dep in python_manifest.get("project", {}).get("dependencies", []))
    package_records = tuple(package.record() for package in packages)
    sbom_index = {
        "schema": "aegis-internal-sbom-index-v1",
        "rust_lock_package_count": len(package_records),
        "rust_direct_dependencies": rust_direct,
        "python_direct_dependencies": python_direct,
        "cargo_lock_hash": _file_hash(root_path / "Cargo.lock"),
        "rust_component_index_hash": _stable_hash(package_records),
        "python_component_index_hash": _stable_hash(python_direct),
    }
    sbom_index["sbom_index_hash"] = _stable_hash(sbom_index)
    provenance = _provenance(root_path)
    wasi = _wasi_deny_evidence(root_path)
    release_attestation = _release_attestation_evidence(root_path, sbom_index, provenance, wasi)
    external_signed_attestation_present = release_attestation["external_signed_attestation_present"]
    checks = (
        _check("cargo_lock_present", bool(sbom_index["cargo_lock_hash"])),
        _check("rust_sbom_index_nonempty", sbom_index["rust_lock_package_count"] > 0),
        _check("rust_direct_dependencies_indexed", len(rust_direct) > 0),
        _check("python_direct_dependencies_indexed", len(python_direct) > 0),
        _check("sbom_index_hash_bound", _nonzero_hex(sbom_index["sbom_index_hash"])),
        _check("provenance_hashes_present", all(item["hash"] for item in provenance["files"])),
        _check("provenance_hash_bound", _nonzero_hex(provenance["provenance_hash"])),
        _check("wasi_dependency_absent", not wasi["forbidden_dependency_patterns"]),
        _check("wasi_runtime_preopens_absent", not wasi["forbidden_runtime_patterns"]),
        _check("wasmtime_fuel_enabled", wasi["wasmtime_fuel_enabled"]),
        _check("wasmtime_epoch_enabled", wasi["wasmtime_epoch_enabled"]),
        _check("wasmtime_memory_limited", wasi["wasmtime_memory_limited"]),
        _check("sandbox_network_isolated", wasi["sandbox_network_isolated"]),
        _check("default_features_empty", wasi["default_features_empty"]),
        _check("network_features_gated", wasi["network_features_gated"]),
        _check("release_attestation_state_recorded", release_attestation["attestation_state_recorded"]),
        _check("release_attestation_subject_hash_bound", _nonzero_hex(release_attestation["expected_subject_hash"])),
        _check("release_attestation_evidence_hash_bound", _nonzero_hex(release_attestation["release_attestation_evidence_hash"])),
        _check("release_attestation_admission_valid", release_attestation["status"] in {"missing", "verified"}),
        _check("external_attestation_gap_disclosed", external_signed_attestation_present or release_attestation["status"] == "missing"),
    )
    payload = {
        "suite_name": "AEGIS Supply Chain Gate",
        "schema": "aegis-supply-chain-gate-report-v1",
        "truth_claim": False,
        "verifier": "internal-sbom-provenance-wasi-deny-plus-release-attestation-admission",
        "sbom": sbom_index,
        "provenance": provenance,
        "wasi_deny_by_default": wasi,
        "release_attestation": release_attestation,
        "external_signed_attestation_present": external_signed_attestation_present,
        "production_release_blocked_without_external_attestation": not external_signed_attestation_present,
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
    }
    payload["overall_ok"] = payload["failed"] == 0
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _cargo_package(entry: Any) -> CargoPackage | None:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    version = entry.get("version")
    source = entry.get("source", "")
    checksum = entry.get("checksum", "")
    if not isinstance(name, str) or not isinstance(version, str):
        return None
    return CargoPackage(
        name=name,
        version=version,
        source=source if isinstance(source, str) else "",
        checksum=checksum if isinstance(checksum, str) else "",
    )


def _dependency_names(dependencies: Any) -> tuple[str, ...]:
    if not isinstance(dependencies, dict):
        return ()
    return tuple(sorted(str(name) for name in dependencies.keys()))


def _all_dependencies(manifest: dict[str, Any]) -> dict[str, Any]:
    dependencies = dict(manifest.get("dependencies", {}))
    for target in manifest.get("target", {}).values():
        if isinstance(target, dict):
            for name, value in target.get("dependencies", {}).items():
                dependencies.setdefault(name, value)
    return dependencies


def _provenance(root_path: Path) -> dict[str, Any]:
    files = tuple(
        {"path": path, "hash": _file_hash(root_path / path)}
        for path in RUST_PROVENANCE_FILES + PYTHON_PROVENANCE_FILES
    )
    payload = {
        "schema": "aegis-source-provenance-index-v1",
        "files": files,
    }
    payload["provenance_hash"] = _stable_hash(files)
    return payload


def _wasi_deny_evidence(root_path: Path) -> dict[str, Any]:
    cargo_text = _read_text(root_path / "core" / "rust" / "Cargo.toml")
    sandbox_text = _read_text(root_path / "core" / "rust" / "src" / "sandbox.rs")
    runtime_sources = "\n".join(
        _read_text(path)
        for glob in RUNTIME_SOURCE_GLOBS
        for path in sorted(root_path.glob(glob))
        if path.is_file()
    )
    forbidden_dependency_patterns = tuple(
        pattern
        for pattern in FORBIDDEN_WASI_PATTERNS
        if pattern.lower() in cargo_text.lower()
    )
    forbidden_runtime_patterns = tuple(
        pattern
        for pattern in FORBIDDEN_WASI_PATTERNS
        if pattern.lower() in runtime_sources.lower()
    )
    return {
        "schema": "aegis-wasi-deny-by-default-evidence-v1",
        "forbidden_dependency_patterns": forbidden_dependency_patterns,
        "forbidden_runtime_patterns": forbidden_runtime_patterns,
        "wasmtime_fuel_enabled": "consume_fuel(true)" in sandbox_text,
        "wasmtime_epoch_enabled": "epoch_interruption(true)" in sandbox_text
        and "set_epoch_deadline" in sandbox_text,
        "wasmtime_memory_limited": "StoreLimitsBuilder::new()" in sandbox_text
        and ".memory_size(" in sandbox_text,
        "sandbox_network_isolated": "network_isolated: true" in sandbox_text
        and "self.network_isolated" in sandbox_text,
        "default_features_empty": "default = []" in cargo_text,
        "network_features_gated": "network-h3 = []" in cargo_text,
        "external_signed_attestation_present": False,
    }


def _release_attestation_evidence(
    root_path: Path,
    sbom_index: dict[str, Any],
    provenance: dict[str, Any],
    wasi: dict[str, Any],
) -> dict[str, Any]:
    artifacts_dir = root_path / "artifacts"
    attestation_path = artifacts_dir / "release_signed_attestation.json"
    verification_path = artifacts_dir / "release_signed_attestation_verification.json"
    expected_subject = {
        "schema": "aegis-release-security-attestation-subject-v1",
        "sbom_index_hash": sbom_index.get("sbom_index_hash", ""),
        "provenance_hash": provenance.get("provenance_hash", ""),
        "wasi_evidence_hash": _stable_hash(wasi),
    }
    expected_subject_hash = _stable_hash(expected_subject)

    attestation = _read_json(attestation_path)
    verification = _read_json(verification_path)
    attestation_file_hash = _file_hash(attestation_path)
    verification_file_hash = _file_hash(verification_path)
    attestation_payload_hash = _stable_hash(attestation) if attestation else ""
    signature = attestation.get("signature", {}) if isinstance(attestation.get("signature"), dict) else {}
    verification_ok = (
        verification.get("schema") == "aegis-release-signed-attestation-verification-v1"
        and verification.get("verified") is True
        and isinstance(verification.get("external_verifier"), str)
        and bool(verification.get("external_verifier", "").strip())
        and verification.get("verified_subject_hash") == expected_subject_hash
        and verification.get("attestation_hash") == attestation_payload_hash
        and verification.get("signature_bundle_hash") == signature.get("signature_bundle_hash")
        and verification.get("transparency_log_entry_hash") == signature.get("transparency_log_entry_hash")
        and verification.get("certificate_identity_hash") == signature.get("certificate_identity_hash")
        and _nonzero_hex(str(verification.get("signature_bundle_hash", "")))
        and _nonzero_hex(str(verification.get("transparency_log_entry_hash", "")))
        and _nonzero_hex(str(verification.get("certificate_identity_hash", "")))
    )
    attestation_ok = (
        attestation.get("schema") == "aegis-release-signed-attestation-v1"
        and attestation.get("subject_hash") == expected_subject_hash
        and _nonzero_hex(attestation_payload_hash)
        and _nonzero_hex(attestation_file_hash)
    )

    if not attestation and not verification:
        status = "missing"
        external_signed_attestation_present = False
        error = "release signed attestation and external verification artifacts missing"
    elif attestation_ok and verification_ok:
        status = "verified"
        external_signed_attestation_present = True
        error = ""
    else:
        status = "invalid"
        external_signed_attestation_present = False
        error = "release attestation present but not externally verified against expected subject"

    evidence = {
        "schema": "aegis-release-attestation-admission-evidence-v1",
        "status": status,
        "expected_subject_hash": expected_subject_hash,
        "attestation_file_hash": attestation_file_hash,
        "attestation_payload_hash": attestation_payload_hash,
        "verification_file_hash": verification_file_hash,
        "verification_payload_hash": _stable_hash(verification) if verification else "",
        "external_signed_attestation_present": external_signed_attestation_present,
        "attestation_state_recorded": True,
    }
    evidence["release_attestation_evidence_hash"] = _stable_hash(evidence)
    return {
        **evidence,
        "expected_subject": expected_subject,
        "attestation_path": str(attestation_path),
        "verification_path": str(verification_path),
        "external_verifier": verification.get("external_verifier", ""),
        "error": error,
    }


def write_release_signed_attestation_artifacts(
    root: str | Path = ROOT,
    *,
    external_verifier: str,
    signature_bundle_hash: str,
    transparency_log_entry_hash: str,
    certificate_identity_hash: str,
) -> dict[str, Any]:
    root_path = Path(root)
    report = evaluate_supply_chain_gate(root_path)
    subject_hash = str(report["release_attestation"]["expected_subject_hash"])
    artifacts_dir = root_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    attestation_path = artifacts_dir / "release_signed_attestation.json"
    verification_path = artifacts_dir / "release_signed_attestation_verification.json"
    signature = {
        "signature_bundle_hash": signature_bundle_hash,
        "transparency_log_entry_hash": transparency_log_entry_hash,
        "certificate_identity_hash": certificate_identity_hash,
    }
    attestation = {
        "schema": "aegis-release-signed-attestation-v1",
        "truth_claim": False,
        "subject_hash": subject_hash,
        "signature": signature,
    }
    attestation_hash = _stable_hash(attestation)
    verification = {
        "schema": "aegis-release-signed-attestation-verification-v1",
        "truth_claim": False,
        "verified": True,
        "external_verifier": external_verifier,
        "verified_subject_hash": subject_hash,
        "attestation_hash": attestation_hash,
        **signature,
    }
    writer_valid = (
        bool(external_verifier.strip())
        and _nonzero_hex(signature_bundle_hash)
        and _nonzero_hex(transparency_log_entry_hash)
        and _nonzero_hex(certificate_identity_hash)
    )
    if writer_valid:
        attestation_path.write_text(json.dumps(attestation, indent=2, sort_keys=True), encoding="utf-8")
        verification_path.write_text(json.dumps(verification, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "suite_name": "AEGIS Release Signed Attestation Artifact Writer",
        "schema": "aegis-release-signed-attestation-artifact-writer-v1",
        "truth_claim": False,
        "writer_valid": writer_valid,
        "artifacts_written": writer_valid,
        "attestation_path": str(attestation_path),
        "verification_path": str(verification_path),
        "expected_subject_hash": subject_hash,
        "attestation_hash": attestation_hash if writer_valid else "",
        "external_verifier": external_verifier if writer_valid else "",
        "signature_bundle_hash": signature_bundle_hash if writer_valid else "",
        "transparency_log_entry_hash": transparency_log_entry_hash if writer_valid else "",
        "certificate_identity_hash": certificate_identity_hash if writer_valid else "",
        "error": "" if writer_valid else "external verifier and nonzero signature hashes are required",
        "writer_evidence_hash": _stable_hash(
            {
                "schema": "aegis-release-signed-attestation-artifact-writer-v1",
                "writer_valid": writer_valid,
                "expected_subject_hash": subject_hash,
                "external_verifier": external_verifier if writer_valid else "",
                "signature_bundle_hash": signature_bundle_hash if writer_valid else "",
                "transparency_log_entry_hash": transparency_log_entry_hash if writer_valid else "",
                "certificate_identity_hash": certificate_identity_hash if writer_valid else "",
            }
        ),
    }


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _nonzero_hex(value: str) -> bool:
    return len(value) == 64 and any(char != "0" for char in value)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    report = evaluate_supply_chain_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
