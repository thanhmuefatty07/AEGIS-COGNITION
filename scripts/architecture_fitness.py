"""Small, dependency-free architecture drift gate for the repository."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def toml(path: str) -> dict:
    return tomllib.loads(read(path))


def check(name: str, condition: bool, detail: str) -> dict[str, object]:
    return {"name": name, "ok": condition, "detail": detail if not condition else "ok"}


def main() -> int:
    root_project = toml("pyproject.toml")["project"]
    root_build = toml("pyproject.toml")["build-system"]
    maturin = toml("pyproject.toml").get("tool", {}).get("maturin", {})
    core_project = toml("core/python/pyproject.toml")["project"]
    rust_manifest = toml("core/rust/Cargo.toml")
    dependencies = rust_manifest["dependencies"]
    toolchain = toml("rust-toolchain.toml")["toolchain"]
    schema = json.loads(read("schemas/resource-contract-v1.json"))
    token_schema = json.loads(read("schemas/lease-token-v1.json"))
    workspace = toml("Cargo.toml")["workspace"]
    cargo_manifests = [ROOT / member / "Cargo.toml" for member in workspace["members"]]

    checks = [
        check(
            "python_policy_aligned",
            root_project["requires-python"] == ">=3.14,<3.16" and core_project["requires-python"] == ">=3.14,<3.16",
            "root and core Python policies must remain >=3.14,<3.16",
        ),
        check(
            "rust_toolchain_pinned",
            toolchain.get("channel") == "1.97.1" and {"rustfmt", "clippy"}.issubset(toolchain.get("components", [])),
            "rust-toolchain.toml must pin 1.97.1 with rustfmt and clippy",
        ),
        check(
            "native_boundary_versions",
            dependencies.get("pyo3", {}).get("version") == "0.29.2"
            and dependencies.get("wasmtime") in {"47.0.3", "=47.0.3"},
            "PyO3 0.29.2 and Wasmtime 47.0.3 are the approved native boundary versions",
        ),
        check(
            "resource_runtime_modules",
            all(
                (ROOT / path).exists()
                for path in (
                    "core/rust/src/resource.rs",
                    "core/rust/src/runtime.rs",
                    "core/rust/src/execution.rs",
                    "core/rust/src/resource_platform.rs",
                    "aegis_cognition/runtime.py",
                )
            ),
            "resource and runtime contract modules must exist",
        ),
        check(
            "maturin_native_packaging",
            root_build.get("build-backend") == "maturin"
            and root_build.get("requires") == ["maturin==1.14.1"]
            and maturin.get("module-name") == "aegis_cognition.aegis_nerve"
            and any(entry.get("path") == "core/**/*.py" and entry.get("format") == "wheel" for entry in maturin.get("include", [])),
            "the root wheel must use pinned maturin and include the bridge package",
        ),
        check(
            "rust_2024_workspace",
            all(
                'edition = "2024"' in path.read_text(encoding="utf-8")
                for path in cargo_manifests
            ),
            "every workspace Cargo package must use edition 2024",
        ),
        check(
            "resource_schema_versioned",
            schema.get("$id") == "https://aegis-cognition.ai/schemas/resource-contract-v1.json"
            and schema.get("properties", {}).get("schema", {}).get("const") == "aegis-resource-contract-v1",
            "resource schema must retain its versioned identity",
        ),
        check(
            "lease_token_schema_versioned",
            token_schema.get("$id", "").endswith("lease-token-v1.json")
            and token_schema.get("properties", {}).get("schema", {}).get("const")
            == "aegis-resource-lease-token-v1",
            "lease completion must use a versioned opaque token schema",
        ),
        check(
            "no_native_cpu_targeting",
            all(
                "target-cpu=native" not in path.read_text(encoding="utf-8").lower() for path in cargo_manifests
            ),
            "portable builds must not silently require host-specific CPU features",
        ),
        check("uv_lock_present", (ROOT / "uv.lock").is_file(), "uv.lock must be committed"),
        check(
            "traceability_matrix",
            all(
                marker in read("docs/architecture/TRACEABILITY.md")
                for marker in ("AUTH-001", "FFI-001", "OS-001", "WASM-001", "PERF-001")
            ),
            "architecture requirements must map to implementation and evidence",
        ),
        check(
            "standards_and_evidence_docs",
            all(
                (ROOT / path).is_file()
                for path in (
                    "docs/architecture/STANDARDS_APPLICABILITY.md",
                    "docs/architecture/TESTING_AND_EVIDENCE.md",
                    "docs/architecture/SECURITY_AND_OPERATIONS.md",
                )
            ),
            "standards applicability, test process, and security/operations scope must be durable",
        ),
        check(
            "contract_inventory",
            (ROOT / "docs/architecture/CONTRACT_INVENTORY.md").is_file()
            and all(
                marker in read("docs/architecture/CONTRACT_INVENTORY.md")
                for marker in (
                    "aegis-resource-contract-v1",
                    "aegis-resource-lease-token-v1",
                    "aegis-runtime-admission-v1",
                    "aegis-runtime-telemetry-v1",
                )
            ),
            "persisted formats and the coarse FFI surface must be inventoried",
        ),
        check(
            "telemetry_contract",
            json.loads(read("schemas/runtime-telemetry-v1.json")).get("$id", "").endswith("runtime-telemetry-v1.json")
            and (ROOT / "core/rust/src/telemetry.rs").exists(),
            "runtime telemetry must have a versioned schema and Rust facade",
        ),
        check(
            "secret_scan_gate",
            (ROOT / "scripts/secret_scan.py").is_file(),
            "tracked-source secret scan must be available to CI",
        ),
        check(
            "adr_evidence_fields",
            all(
                all(field in read(path) for field in ("## Migration, security, performance, operations, rollback", "## Evidence"))
                for path in (ROOT / "docs" / "adr").glob("ADR-*.md")
            ),
            "every ADR must contain migration/security/performance/operations/rollback and evidence",
        ),
        check(
            "benchmark_claims_are_labeled",
            "69x faster" not in read("README.md") and "All benchmarks verified" not in read("README.md"),
            "README must not present stale benchmark numbers as current proof",
        ),
    ]

    report = {"overall_ok": all(item["ok"] for item in checks), "checks": checks}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
