import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "dependency_audit_gate_report.json"

REQUIRED_DIRECT_DEPS: dict[str, str] = {
    "arrow": "54",
    "arrow-buffer": "54",
    "blake3": "1.5.0",
    "pyo3": "0.29.2",
    "rayon": "1.12",
    "wasmtime": "47.0.4",
    "windows-sys": "0.61.2",
}
CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"


@dataclass(frozen=True)
class LockPackage:
    name: str
    version: str
    source: str
    checksum: str

    def digest(self) -> str:
        return _stable_hash(
            {
                "name": self.name,
                "version": self.version,
                "source": self.source,
                "checksum": self.checksum,
            }
        )


def evaluate_dependency_audit_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    manifest = _read_toml(root_path / "core" / "rust" / "Cargo.toml")
    lockfile = _read_toml(root_path / "Cargo.lock")
    dependencies = _all_dependencies(manifest)
    package_entries = lockfile.get("package", []) if isinstance(lockfile, dict) else []
    lock_packages = {
        package.name: package
        for package in (_lock_package(entry) for entry in package_entries)
        if package is not None
    }
    records = []
    checks = []
    for dep_name, manifest_version in REQUIRED_DIRECT_DEPS.items():
        manifest_value = dependencies.get(dep_name)
        manifest_ok = _manifest_version(manifest_value) == manifest_version
        lock_package = lock_packages.get(dep_name)
        lock_ok = lock_package is not None and _lock_version_compatible(manifest_version, lock_package.version)
        source_ok = lock_package is not None and lock_package.source == CRATES_IO_SOURCE
        checksum_ok = lock_package is not None and len(lock_package.checksum) == 64
        record = {
            "name": dep_name,
            "manifest_version": _manifest_version(manifest_value),
            "required_manifest_version": manifest_version,
            "lock_version": "" if lock_package is None else lock_package.version,
            "source": "" if lock_package is None else lock_package.source,
            "checksum_digest": "" if lock_package is None else _stable_hash(lock_package.checksum),
            "record_digest": "" if lock_package is None else lock_package.digest(),
            "ok": manifest_ok and lock_ok and source_ok and checksum_ok,
        }
        records.append(record)
        checks.append(
            {
                "name": f"{dep_name}_lock_audit",
                "ok": record["ok"],
                "detail": (
                    f"manifest={manifest_ok} lock={lock_ok} source={source_ok} "
                    f"checksum={checksum_ok}"
                ),
            }
        )
    checks.append(
        {
            "name": "sandbox_dependency_surface",
            "ok": _source_has_all(
                root_path / "core" / "rust" / "src" / "sandbox.rs",
                ("Wasmtime", "fuel", "memory", "epoch"),
            )
            or _source_has_all(
                root_path / "core" / "rust" / "src" / "eac" / "sandbox" / "runtime.rs",
                ("Wasmtime", "fuel", "memory", "epoch"),
            ),
            "detail": "Wasmtime dependency must be tied to fuel/memory/epoch sandbox policy surface",
        }
    )
    passed = sum(1 for check in checks if check["ok"])
    failed = len(checks) - passed
    payload = {
        "suite_name": "Dependency Audit Gate",
        "schema": "aegis-dependency-audit-gate-report-v1",
        "truth_claim": False,
        "verifier": "cargo-lockfile-and-rust-sandbox-surface",
        "required_dependencies": REQUIRED_DIRECT_DEPS,
        "records": records,
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "overall_ok": failed == 0,
    }
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _lock_package(entry: Any) -> LockPackage | None:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    version = entry.get("version")
    source = entry.get("source")
    checksum = entry.get("checksum")
    if not all(isinstance(value, str) for value in (name, version, source, checksum)):
        return None
    return LockPackage(name=name, version=version, source=source, checksum=checksum)


def _manifest_version(value: Any) -> str:
    if isinstance(value, str):
        return value.lstrip("=")
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return value["version"].lstrip("=")
    return ""


def _all_dependencies(manifest: dict[str, Any]) -> dict[str, Any]:
    """Flatten normal and target-specific direct dependencies for auditing."""

    dependencies = dict(manifest.get("dependencies", {}))
    for target in manifest.get("target", {}).values():
        if isinstance(target, dict):
            for name, value in target.get("dependencies", {}).items():
                dependencies.setdefault(name, value)
    return dependencies


def _lock_version_compatible(manifest_version: str, lock_version: str) -> bool:
    requested = _version_parts(manifest_version)
    locked = _version_parts(lock_version)
    if not requested or not locked:
        return False
    if len(requested) == 1:
        return locked[0] == requested[0]
    if len(requested) == 2:
        return locked[:2] == requested[:2]
    if requested[0] == 0:
        return locked[:2] == requested[:2] and locked >= requested
    return locked[0] == requested[0] and locked >= requested


def _version_parts(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def _source_has_all(path: Path, tokens: tuple[str, ...]) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lowered = text.lower()
    return all(token.lower() in lowered for token in tokens)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    report = evaluate_dependency_audit_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
