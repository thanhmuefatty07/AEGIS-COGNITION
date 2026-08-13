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
    core_project = toml("core/python/pyproject.toml")["project"]
    rust_manifest = toml("core/rust/Cargo.toml")
    dependencies = rust_manifest["dependencies"]
    toolchain = toml("rust-toolchain.toml")["toolchain"]
    schema = json.loads(read("schemas/resource-contract-v1.json"))

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
            dependencies.get("pyo3", {}).get("version") == "0.29.2" and dependencies.get("wasmtime") == "47.0.3",
            "PyO3 0.29.2 and Wasmtime 47.0.3 are the approved native boundary versions",
        ),
        check(
            "resource_runtime_modules",
            all(
                (ROOT / path).exists()
                for path in (
                    "core/rust/src/resource.rs",
                    "core/rust/src/runtime.rs",
                    "aegis_cognition/runtime.py",
                )
            ),
            "resource and runtime contract modules must exist",
        ),
        check(
            "resource_schema_versioned",
            schema.get("$id") == "https://aegis-cognition.ai/schemas/resource-contract-v1.json"
            and schema.get("properties", {}).get("schema", {}).get("const") == "aegis-resource-contract-v1",
            "resource schema must retain its versioned identity",
        ),
        check(
            "no_native_cpu_targeting",
            all(
                "target-cpu=native" not in path.read_text(encoding="utf-8").lower() for path in ROOT.rglob("Cargo.toml")
            ),
            "portable builds must not silently require host-specific CPU features",
        ),
        check("uv_lock_present", (ROOT / "uv.lock").is_file(), "uv.lock must be committed"),
    ]

    report = {"overall_ok": all(item["ok"] for item in checks), "checks": checks}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
