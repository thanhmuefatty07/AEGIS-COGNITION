"""Small, dependency-free architecture drift gate for the repository."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from rust_toolchain import derived_minimum, read_channel

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
    rust_channel = read_channel(ROOT)
    rust_minimum = derived_minimum(rust_channel)
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
            toolchain.get("channel") == rust_channel
            and {"rustfmt", "clippy"}.issubset(toolchain.get("components", []))
            and workspace.get("package", {}).get("rust-version") == rust_minimum,
            "rust-toolchain.toml is the exact source; Cargo must use its derived major.minor minimum",
        ),
        check(
            "rust_2024_workspace_resolver",
            workspace.get("resolver") == "3"
            and set(workspace.get("default-members", []))
            == {
                "core/rust",
                "aegis-plugins/aegis-search-sdk",
                "aegis-plugins/aegis-browser",
                "aegis-plugins/aegis-sandbox",
                "aegis-plugins/aegis-skills",
                "aegis-plugins/aegis-evidence",
                "aegis-plugins/aegis-bench",
            },
            "Rust 2024 must use resolver 3 and production default-members must exclude POCs",
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
            "resource_runtime_acceptance_surfaces",
            all(
                marker in read(path)
                for path, marker in (
                    ("core/rust/src/resource.rs", "pub fn capacity_feedback"),
                    ("core/rust/src/runtime.rs", "pub fn drain_queued"),
                    ("core/rust/src/runtime.rs", "pub fn reap_expired"),
                    ("core/rust/src/execution.rs", "pub fn run_untrusted_process"),
                    ("core/rust/src/execution.rs", "pub trait AcceleratorExecutor"),
                    ("core/rust/src/ffi.rs", "pub fn aegis_resource_usage_sample"),
                )
            ),
            "resource feedback, queued admission, deadlines, process isolation, accelerator seams, and sampling must remain present",
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
            "release_evidence_gate",
            (ROOT / "scripts/release_evidence.py").is_file()
            and (ROOT / ".github/workflows/release.yml").is_file()
            and "attest-build-provenance" in read(".github/workflows/release.yml"),
            "release artifacts must have reproducible hash/SBOM evidence and a provenance-attestation path",
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
        check(
            "canonical_python_boundaries",
            "sys.path.insert" not in read("aegis_cognition/agent.py")
            and "sys.path.insert" not in read("aegis_cognition/rag.py")
            and (ROOT / "aegis_cognition/application.py").is_file()
            and (ROOT / "aegis_cognition/config.py").is_file()
            and (ROOT / "aegis_cognition/infrastructure.py").is_file(),
            "canonical Agent must use explicit application/config/infrastructure boundaries",
        ),
        check(
            "gateway_responsibilities_split",
            all(
                (ROOT / path).is_file()
                for path in (
                    "core/python/aegis/contracts.py",
                    "core/python/aegis/provider.py",
                    "core/python/aegis/evidence.py",
                    "core/python/aegis/learning.py",
                )
            )
            and len(read("core/python/aegis_adapter.py").splitlines()) <= 700
            and "class LearningManager" not in read("core/python/aegis_adapter.py")
            and "async def _invoke_with_provider_route" not in read("core/python/aegis_adapter.py")
            and "def commit_hot_evidence(" not in read("core/python/aegis_adapter.py"),
            "gateway DTOs, provider policy, evidence, and learning must stay outside the facade",
        ),
        check(
            "legacy_subtree_is_reference_only",
            all(
                path.relative_to(ROOT / "AEGIS-COGNITION").as_posix()
                in {
                    "DX_TRANSFORMATION_REPORT.md",
                    "LEGACY_OWNERSHIP.md",
                    "artifacts/extreme_testing/EXTREME_TESTING_CHAOS_REPORT.md",
                }
                for path in (ROOT / "AEGIS-COGNITION").rglob("*")
                if path.is_file()
            ),
            "nested AEGIS-COGNITION must not contain executable implementation copies",
        ),
    ]

    report = {"overall_ok": all(item["ok"] for item in checks), "checks": checks}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
