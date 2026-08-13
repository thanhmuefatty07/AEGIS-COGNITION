import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "production_packaging_smoke_gate_report.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def evaluate_production_packaging_smoke_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifacts_dir = root_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    root_pyproject = _read_toml(root_path / "pyproject.toml")
    core_pyproject = _read_toml(root_path / "core" / "python" / "pyproject.toml")
    workspace_cargo = _read_toml(root_path / "Cargo.toml")
    core_cargo = _read_toml(root_path / "core" / "rust" / "Cargo.toml")

    python_smoke = _run_command(
        [sys.executable, str(root_path / "scripts" / "smoke_check.py")],
        root_path,
        {"PYTHONPATH": str(root_path)},
    )
    rust_cli_smoke = _run_command(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(root_path / "core" / "rust" / "Cargo.toml"),
            "--bin",
            "aegis-nerve-cli",
            "--",
        ],
        root_path,
        _cargo_env(),
        timeout_seconds=240,
    )
    friendly_gateway = _friendly_gateway_dev_smoke()
    service_manifest = _service_manifest_smoke()
    artifact_write = _artifact_write_smoke(artifacts_dir)

    package_surface = {
        "root_pyproject_hash": _file_hash(root_path / "pyproject.toml"),
        "core_python_pyproject_hash": _file_hash(root_path / "core" / "python" / "pyproject.toml"),
        "workspace_cargo_hash": _file_hash(root_path / "Cargo.toml"),
        "core_rust_cargo_hash": _file_hash(root_path / "core" / "rust" / "Cargo.toml"),
        "env_example_hash": _file_hash(root_path / ".env.example"),
        "root_python_project": _project_summary(root_pyproject),
        "core_python_project": _project_summary(core_pyproject),
        "workspace_members": workspace_cargo.get("workspace", {}).get("members", []),
        "rust_package": _project_summary(core_cargo),
        "rust_bins": [item.get("name") for item in core_cargo.get("bin", []) if isinstance(item, dict)],
        "rust_lib_crate_types": core_cargo.get("lib", {}).get("crate-type", []),
    }
    package_surface_hash = _stable_hash(package_surface)
    smoke_evidence = {
        "python_smoke_returncode": python_smoke["returncode"],
        "rust_cli_returncode": rust_cli_smoke["returncode"],
        "friendly_gateway_hash": friendly_gateway["friendly_gateway_hash"],
        "service_manifest_hash": service_manifest["service_manifest_hash"],
        "artifact_write_hash": artifact_write["artifact_write_hash"],
        "package_surface_hash": package_surface_hash,
    }
    smoke_evidence_hash = _stable_hash(smoke_evidence)

    checks = (
        _check("root_pyproject_present", bool(root_pyproject)),
        _check("root_project_name", root_pyproject.get("project", {}).get("name") == "aegis-cognition"),
        _check("root_requires_python_313", root_pyproject.get("project", {}).get("requires-python") == ">=3.13"),
        _check("core_python_pyproject_present", bool(core_pyproject)),
        _check("core_python_project_name", core_pyproject.get("project", {}).get("name") == "aegis-cognition-core-python"),
        _check("workspace_contains_core_rust", "core/rust" in workspace_cargo.get("workspace", {}).get("members", [])),
        _check("rust_package_present", core_cargo.get("package", {}).get("name") == "aegis-nerve"),
        _check("rust_cli_bin_declared", "aegis-nerve-cli" in package_surface["rust_bins"]),
        _check("rust_cdylib_declared", "cdylib" in package_surface["rust_lib_crate_types"]),
        _check("python_smoke_passed", python_smoke["returncode"] == 0 and _json_stdout_ok(python_smoke, "overall_ok")),
        _check("friendly_gateway_dev_smoke_passed", friendly_gateway["overall_ok"]),
        _check("service_manifest_smoke_passed", service_manifest["overall_ok"]),
        _check("rust_cli_smoke_passed", rust_cli_smoke["returncode"] == 0 and "aegis-nerve-cli ready" in rust_cli_smoke["stdout_tail"]),
        _check("artifact_write_smoke_passed", artifact_write["overall_ok"]),
        _check("package_surface_hash_nonzero", _nonzero_hex(package_surface_hash)),
        _check("smoke_evidence_hash_nonzero", _nonzero_hex(smoke_evidence_hash)),
    )
    payload = {
        "suite_name": "AEGIS Production Packaging Smoke Gate",
        "schema": "aegis-production-packaging-smoke-gate-report-v1",
        "truth_claim": False,
        "verifier": "local-package-manifest-python-rust-cli-smoke",
        "production_packaging_smoke_present": True,
        "container_image_attestation_present": False,
        "external_deployment_smoke_present": False,
        "package_surface": package_surface,
        "package_surface_hash": package_surface_hash,
        "smoke_evidence": smoke_evidence,
        "smoke_evidence_hash": smoke_evidence_hash,
        "python_smoke": python_smoke,
        "rust_cli_smoke": rust_cli_smoke,
        "friendly_gateway_dev_smoke": friendly_gateway,
        "service_manifest_smoke": service_manifest,
        "artifact_write_smoke": artifact_write,
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
    }
    payload["overall_ok"] = payload["failed"] == 0
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _friendly_gateway_dev_smoke() -> dict[str, Any]:
    try:
        from core.python import Agent

        result = Agent(task="production packaging smoke", trust_level="DEV").run_sync()
        hot_commit = result.hot_commit
        evidence = {
            "schema": result.schema,
            "truth_claim": result.truth_claim,
            "trust_level": result.trust_level,
            "hot_commit_verifier": hot_commit.verifier,
            "hot_commit_admission": hot_commit.admission,
            "hot_commit_byte_len": hot_commit.byte_len,
            "hot_commit_handle_valid": hot_commit.handle_valid,
            "physical_witness_required": hot_commit.physical_witness_required,
            "fail_closed": hot_commit.fail_closed,
            "artifact_hash": hot_commit.artifact_hash,
            "storage_ref_hash": hot_commit.storage_ref_hash,
        }
        return {
            "overall_ok": (
                result.truth_claim is False
                and result.trust_level == "DEV"
                and hot_commit.handle_valid
                and hot_commit.byte_len > 0
                and _nonzero_hex(hot_commit.artifact_hash)
                and _nonzero_hex(hot_commit.storage_ref_hash)
            ),
            "evidence": evidence,
            "friendly_gateway_hash": _stable_hash(evidence),
        }
    except Exception as exc:
        return {
            "overall_ok": False,
            "error": type(exc).__name__,
            "friendly_gateway_hash": "",
        }


def _service_manifest_smoke() -> dict[str, Any]:
    try:
        from core.python import build_message, build_service_manifest

        manifest = build_service_manifest(build_message(41, 42, b"production-packaging-smoke"))
        evidence = {
            "service_name": manifest.service_name,
            "version": manifest.version,
            "bridge_ready": manifest.bridge_ready,
            "contract_ok": manifest.contract_ok,
            "runtime_surface_ok": manifest.runtime_surface_ok,
            "packaging_ready": manifest.packaging_ready,
        }
        return {
            "overall_ok": all(
                (
                    manifest.service_name == "aegis-cognition",
                    manifest.version == "0.1.0",
                    manifest.bridge_ready,
                    manifest.contract_ok,
                    manifest.runtime_surface_ok,
                    manifest.packaging_ready,
                )
            ),
            "evidence": evidence,
            "service_manifest_hash": _stable_hash(evidence),
        }
    except Exception as exc:
        return {
            "overall_ok": False,
            "error": type(exc).__name__,
            "service_manifest_hash": "",
        }


def _artifact_write_smoke(artifacts_dir: Path) -> dict[str, Any]:
    path = artifacts_dir / "production_packaging_smoke_probe.json"
    payload = {
        "schema": "aegis-production-packaging-smoke-probe-v1",
        "truth_claim": False,
        "probe": "artifact-write",
    }
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        path.write_bytes(encoded)
        file_hash = _file_hash(path)
        return {
            "overall_ok": path.is_file() and path.stat().st_size == len(encoded) and _nonzero_hex(file_hash),
            "path": str(path),
            "byte_len": len(encoded),
            "file_hash": file_hash,
            "artifact_write_hash": _stable_hash({"path": str(path), "byte_len": len(encoded), "file_hash": file_hash}),
        }
    except OSError as exc:
        return {
            "overall_ok": False,
            "error": type(exc).__name__,
            "artifact_write_hash": "",
        }


def _run_command(
    command: list[str],
    cwd: Path,
    extra_env: dict[str, str],
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(extra_env)
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": command,
            "returncode": -1,
            "stdout_tail": "",
            "stderr_tail": type(exc).__name__,
            "command_hash": _stable_hash({"command": command, "error": type(exc).__name__}),
        }
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
        "command_hash": _stable_hash(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-2000:],
                "stderr_tail": result.stderr[-2000:],
            }
        ),
    }


def _cargo_env() -> dict[str, str]:
    py_root = Path(sys.executable).parent
    return {
        "PYO3_PYTHON": sys.executable,
        "PATH": os.pathsep.join(
            (
                str(py_root),
                str(py_root / "DLLs"),
                str(py_root / "libs"),
                os.environ.get("PATH", ""),
            )
        ),
        "RUSTFLAGS": os.environ.get("RUSTFLAGS", "-C debuginfo=0"),
    }


def _json_stdout_ok(command_result: dict[str, Any], field: str) -> bool:
    try:
        payload = json.loads(command_result.get("stdout_tail", "{}"))
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get(field) is True


def _project_summary(payload: dict[str, Any]) -> dict[str, Any]:
    project = payload.get("project", {}) if isinstance(payload, dict) else {}
    package = payload.get("package", {}) if isinstance(payload, dict) else {}
    return {
        "name": project.get("name") or package.get("name") or "",
        "version": project.get("version") or package.get("version") or "",
        "requires_python": project.get("requires-python", ""),
        "dependencies": project.get("dependencies", []),
    }


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


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
    report = evaluate_production_packaging_smoke_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
