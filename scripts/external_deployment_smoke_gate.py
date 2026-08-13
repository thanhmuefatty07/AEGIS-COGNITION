import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "external_deployment_smoke_gate_report.json"
EXTERNAL_DEPLOYMENT_SMOKE_ADMISSION_SCHEMA = "aegis-external-deployment-smoke-admission-v1"
EXTERNAL_DEPLOYMENT_SMOKE_CAPTURE_SCHEMA = "aegis-external-deployment-smoke-capture-v1"
EXTERNAL_DEPLOYMENT_SMOKE_EXPECTED_CAPTURE_PATH = "artifacts/external_deployment_smoke_capture.json"
EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT = (
    "operator-health-url-status-200-json-schema-with-release-gate-and-container-attestation-evidence"
)
EXTERNAL_DEPLOYMENT_SMOKE_BLOCKER_ID = "external_deployment_smoke_missing"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def evaluate_external_deployment_smoke_gate(
    root: str | Path = ROOT,
    requester: Any | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    artifacts_dir = root_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    packaging_smoke = _read_json(artifacts_dir / "production_packaging_smoke_gate_report.json")
    container_attestation = _read_json(artifacts_dir / "container_image_attestation.json")
    descriptor_index = _deployment_descriptor_index(root_path)
    descriptor_contract = _deployment_descriptor_contract(root_path, descriptor_index["records"])
    runtime_probe = _container_runtime_probe()
    local_operator_health = _local_operator_health_probe(root_path)
    external_url = os.environ.get("AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL", "").strip()
    external_health = _external_health_probe(external_url, requester=requester)
    admission = _external_deployment_smoke_admission(
        root_path / EXTERNAL_DEPLOYMENT_SMOKE_EXPECTED_CAPTURE_PATH,
        external_url,
        external_health,
    )

    packaging_smoke_hash = str(packaging_smoke.get("smoke_evidence_hash", ""))
    descriptor_index_hash = _stable_hash(descriptor_index)
    descriptor_contract_hash = _stable_hash(descriptor_contract)
    runtime_probe_hash = _stable_hash(runtime_probe)
    local_operator_health_hash = _stable_hash(local_operator_health)
    external_health_hash = _stable_hash(external_health)
    container_attestation_hash = str(container_attestation.get("attestation_hash", ""))
    container_image_attestation_present = (
        container_attestation.get("schema") == "aegis-container-image-attestation-v1"
        and _nonzero_hex(container_attestation_hash)
    )
    external_deployment_smoke_present = (
        external_health["overall_ok"]
        and external_health["external_url_present"]
        and _nonzero_hex(external_health_hash)
        and admission["capture_valid"]
    )
    evidence = {
        "packaging_smoke_hash": packaging_smoke_hash,
        "descriptor_index_hash": descriptor_index_hash,
        "descriptor_contract_hash": descriptor_contract_hash,
        "runtime_probe_hash": runtime_probe_hash,
        "local_operator_health_hash": local_operator_health_hash,
        "external_health_hash": external_health_hash,
        "external_deployment_smoke_admission_hash": admission["admission_hash"],
        "container_attestation_hash": container_attestation_hash,
        "external_deployment_smoke_present": external_deployment_smoke_present,
        "container_image_attestation_present": container_image_attestation_present,
    }
    evidence_hash = _stable_hash(evidence)

    checks = (
        _check("packaging_smoke_bound", packaging_smoke.get("overall_ok") is True and _nonzero_hex(packaging_smoke_hash)),
        _check("deployment_descriptor_present", descriptor_index["descriptor_count"] > 0),
        _check("descriptor_index_hash_nonzero", _nonzero_hex(descriptor_index_hash)),
        _check("descriptor_contract_bound", descriptor_contract["overall_ok"] and _nonzero_hex(descriptor_contract_hash)),
        _check("runtime_probe_hash_nonzero", _nonzero_hex(runtime_probe_hash)),
        _check("local_operator_health_probe_passed", local_operator_health["overall_ok"]),
        _check("external_health_probe_recorded", _nonzero_hex(external_health_hash)),
        _check("external_deployment_smoke_gap_disclosed", external_deployment_smoke_present or admission["admission_status"] == "missing"),
        _check("external_deployment_smoke_admission_schema", admission["schema"] == EXTERNAL_DEPLOYMENT_SMOKE_ADMISSION_SCHEMA),
        _check("external_deployment_smoke_admission_path", admission["expected_capture_path"] == EXTERNAL_DEPLOYMENT_SMOKE_EXPECTED_CAPTURE_PATH),
        _check("external_deployment_smoke_admission_hash", _nonzero_hex(admission["admission_hash"])),
        _check("external_deployment_smoke_probe_contract_hash", _nonzero_hex(admission["probe_contract_hash"])),
        _check("external_deployment_smoke_admission_blocker", admission["production_blocker_id"] == EXTERNAL_DEPLOYMENT_SMOKE_BLOCKER_ID),
        _check("external_deployment_smoke_admission_state_bound", _admission_state_bound(admission)),
        _check("external_deployment_smoke_capture_missing_or_valid", (not admission["capture_present"]) or admission["capture_valid"]),
        _check("container_attestation_gap_disclosed", container_image_attestation_present or not container_attestation),
        _check("external_deployment_smoke_evidence_hash_nonzero", _nonzero_hex(evidence_hash)),
    )
    payload = {
        "suite_name": "AEGIS External Deployment Smoke Gate",
        "schema": "aegis-external-deployment-smoke-gate-report-v1",
        "truth_claim": False,
        "verifier": "container-runtime-url-health-and-gap-disclosure",
        "production_packaging_smoke_present": packaging_smoke.get("production_packaging_smoke_present") is True,
        "container_runtime_available": runtime_probe["runtime_available"],
        "deployment_descriptor_present": descriptor_index["descriptor_count"] > 0,
        "external_deployment_smoke_url_present": external_health["external_url_present"],
        "external_deployment_smoke_present": external_deployment_smoke_present,
        "container_image_attestation_present": container_image_attestation_present,
        "external_deployment_smoke_state_recorded": True,
        "container_attestation_state_recorded": True,
        "production_release_blocked_without_external_deployment_smoke": not external_deployment_smoke_present,
        "production_release_blocked_without_container_attestation": not container_image_attestation_present,
        "packaging_smoke_hash": packaging_smoke_hash,
        "descriptor_index": descriptor_index,
        "descriptor_index_hash": descriptor_index_hash,
        "descriptor_contract": descriptor_contract,
        "descriptor_contract_hash": descriptor_contract_hash,
        "runtime_probe": runtime_probe,
        "runtime_probe_hash": runtime_probe_hash,
        "local_operator_health": local_operator_health,
        "local_operator_health_hash": local_operator_health_hash,
        "external_health": external_health,
        "external_health_hash": external_health_hash,
        "external_deployment_smoke_admission": admission,
        "external_deployment_smoke_admission_hash": admission["admission_hash"],
        "external_deployment_smoke_admission_missing": (
            admission["capture_present"] is False
            and admission["admission_status"] == "missing"
        ),
        "external_deployment_smoke_admission_blocker_id": admission["production_blocker_id"],
        "external_deployment_smoke_capture_missing_or_valid": (
            (not admission["capture_present"]) or admission["capture_valid"]
        ),
        "container_attestation": container_attestation,
        "container_attestation_hash": container_attestation_hash,
        "external_deployment_smoke_evidence": evidence,
        "external_deployment_smoke_evidence_hash": evidence_hash,
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
    }
    payload["overall_ok"] = payload["failed"] == 0
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _container_runtime_probe() -> dict[str, Any]:
    probes = []
    for runtime in ("docker", "podman"):
        executable = shutil.which(runtime)
        if executable is None:
            probes.append(
                {
                    "runtime": runtime,
                    "available": False,
                    "executable": "",
                    "version_stdout": "",
                    "version_stderr": "",
                    "returncode": -1,
                }
            )
            continue
        result = _run_command([executable, "--version"], timeout_seconds=10)
        probes.append(
            {
                "runtime": runtime,
                "available": result["returncode"] == 0,
                "executable": executable,
                "version_stdout": result["stdout_tail"],
                "version_stderr": result["stderr_tail"],
                "returncode": result["returncode"],
            }
        )
    return {
        "schema": "aegis-container-runtime-probe-v1",
        "runtime_available": any(probe["available"] for probe in probes),
        "probes": probes,
        "host": socket.gethostname(),
    }


def _deployment_descriptor_index(root: Path) -> dict[str, Any]:
    candidates = (
        "Dockerfile",
        "Containerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "deploy/docker-compose.yml",
        "deploy/docker-compose.yaml",
        "deploy/kubernetes.yaml",
        "deploy/kubernetes.yml",
        "k8s/deployment.yaml",
        "k8s/deployment.yml",
    )
    records = []
    for relative in candidates:
        path = root / relative
        if path.exists() and path.is_file():
            records.append(
                {
                    "path": relative.replace("\\", "/"),
                    "byte_len": path.stat().st_size,
                    "file_hash": _file_hash(path),
                }
            )
    return {
        "schema": "aegis-deployment-descriptor-index-v1",
        "descriptor_count": len(records),
        "records": records,
    }


def _deployment_descriptor_contract(root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    text_by_path: dict[str, str] = {}
    for record in records:
        relative = str(record["path"])
        try:
            text_by_path[relative] = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text_by_path[relative] = ""

    checks = (
        _check(
            "dockerfile_operator_root_contract",
            _contains_all(
                text_by_path.get("Dockerfile", ""),
                (
                    "AEGIS_OPERATOR_ROOT=/var/lib/aegis",
                    "AEGIS_ARTIFACTS_DIR=/var/lib/aegis/artifacts",
                    'core.python.operator_api_healthcheck',
                    '"--root", "/var/lib/aegis"',
                ),
            ),
        ),
        _check(
            "compose_operator_root_contract",
            _contains_all(
                text_by_path.get("deploy/docker-compose.yml", ""),
                (
                    "AEGIS_OPERATOR_ROOT: /var/lib/aegis",
                    "AEGIS_ARTIFACTS_DIR: /var/lib/aegis/artifacts",
                    "evidence-artifacts:/var/lib/aegis/artifacts:ro",
                    '"--root", "/var/lib/aegis"',
                ),
            ),
        ),
        _check(
            "kubernetes_operator_root_contract",
            _contains_all(
                text_by_path.get("deploy/kubernetes.yaml", ""),
                (
                    "aegis-evidence-artifacts",
                    "AEGIS_OPERATOR_ROOT",
                    "AEGIS_ARTIFACTS_DIR",
                    "/var/lib/aegis/artifacts",
                    '"--root", "/var/lib/aegis"',
                ),
            ),
        ),
    )
    return {
        "schema": "aegis-deployment-descriptor-contract-v1",
        "operator_root": "/var/lib/aegis",
        "artifacts_dir": "/var/lib/aegis/artifacts",
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
        "overall_ok": all(check["ok"] for check in checks),
    }


def _local_operator_health_probe(root: Path) -> dict[str, Any]:
    try:
        from core.python.operator_api import OperatorEvidenceApi

        payload = OperatorEvidenceApi(
            root,
            (
                "production_packaging_smoke_gate_report.json",
            ),
        ).handle_get("/health").json()
    except Exception as exc:  # noqa: BLE001 - evidence gate records the exact failure class.
        return {
            "schema": "aegis-local-operator-health-probe-v1",
            "overall_ok": False,
            "error": type(exc).__name__,
        }
    return {
        "schema": "aegis-local-operator-health-probe-v1",
        "overall_ok": (
            payload.get("schema") == "aegis-operator-health-v1"
            and payload.get("truth_claim") is False
            and payload.get("missing_artifact_count") == 0
        ),
        "health_schema": payload.get("schema", ""),
        "truth_claim": payload.get("truth_claim"),
        "release_gate_observed_ok": payload.get("release_gate_observed_ok"),
        "missing_artifact_count": payload.get("missing_artifact_count"),
        "artifact_count": payload.get("artifact_count"),
        "snapshot_digest": payload.get("snapshot_digest", {}),
    }


def _external_health_probe(external_url: str, requester: Any | None = None) -> dict[str, Any]:
    if not external_url:
        return {
            "schema": "aegis-external-health-probe-v1",
            "overall_ok": False,
            "external_url_present": False,
            "url": "",
            "status": 0,
            "body_hash": "",
            "body_len": 0,
            "error": "AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL unset",
        }
    health_url = external_url if external_url.endswith("/health") else urljoin(external_url.rstrip("/") + "/", "health")
    try:
        request = Request(health_url, headers={"Accept": "application/json"})
        if requester is None:
            with urlopen(request, timeout=10) as response:  # noqa: S310 - operator-provided smoke URL.
                body = response.read(1024 * 1024)
                status = int(response.status)
        else:
            response = requester(request, 10)
            body = bytes(response.get("body", b""))
            status = int(response.get("status", 0))
    except (OSError, TimeoutError, URLError) as exc:
        return {
            "schema": "aegis-external-health-probe-v1",
            "overall_ok": False,
            "external_url_present": True,
            "url": health_url,
            "status": 0,
            "body_hash": "",
            "body_len": 0,
            "error": type(exc).__name__,
        }
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    return {
        "schema": "aegis-external-health-probe-v1",
        "overall_ok": (
            status == 200
            and isinstance(payload, dict)
            and payload.get("schema") == "aegis-operator-health-v1"
            and payload.get("truth_claim") is False
        ),
        "external_url_present": True,
        "url": health_url,
        "status": status,
        "body_hash": hashlib.sha256(body).hexdigest(),
        "body_len": len(body),
        "health_schema": payload.get("schema", "") if isinstance(payload, dict) else "",
        "truth_claim": payload.get("truth_claim") if isinstance(payload, dict) else None,
        "release_gate_observed_ok": payload.get("release_gate_observed_ok") if isinstance(payload, dict) else None,
        "error": "",
    }


def _external_deployment_smoke_admission(
    capture_path: Path,
    external_url: str,
    external_health: dict[str, Any],
) -> dict[str, Any]:
    capture_payload, capture_present, capture_bytes, capture_error = _read_capture(capture_path)
    external_health_hash = _stable_hash(external_health)
    capture_valid = _capture_payload_valid(
        capture_payload,
        external_url,
        external_health,
        external_health_hash,
    )
    capture_hash = hashlib.sha256(capture_bytes).hexdigest() if capture_present else "0" * 64
    if capture_valid:
        admission_status = "candidate-present-external-deployment-smoke-gate-required"
    elif capture_present:
        admission_status = "invalid-capture"
    else:
        admission_status = "missing"
    admission = {
        "schema": EXTERNAL_DEPLOYMENT_SMOKE_ADMISSION_SCHEMA,
        "expected_capture_path": EXTERNAL_DEPLOYMENT_SMOKE_EXPECTED_CAPTURE_PATH,
        "capture_present": capture_present,
        "capture_schema": capture_payload.get("schema", "") if capture_present else "",
        "capture_valid": capture_valid,
        "capture_hash": capture_hash,
        "capture_error": capture_error,
        "probe_contract": EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT,
        "probe_contract_hash": _stable_hash(EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT),
        "required_status": 200,
        "required_health_schema": "aegis-operator-health-v1",
        "required_truth_claim": False,
        "external_url_present": bool(external_url),
        "external_probe_overall_ok": external_health.get("overall_ok") is True,
        "external_probe_url": external_health.get("url", ""),
        "external_probe_status": external_health.get("status", 0),
        "external_health_hash": external_health_hash,
        "admission_status": admission_status,
        "production_blocker_id": EXTERNAL_DEPLOYMENT_SMOKE_BLOCKER_ID,
        "admission_hash": "",
    }
    admission["admission_hash"] = _admission_hash(admission)
    return admission


def write_external_deployment_smoke_capture(
    root: str | Path = ROOT,
    requester: Any | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    artifacts_dir = root_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    external_url = os.environ.get("AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL", "").strip()
    external_health = _external_health_probe(external_url, requester=requester)
    external_health_hash = _stable_hash(external_health)
    capture = _external_deployment_smoke_capture(external_url, external_health, external_health_hash)
    capture_valid = _capture_payload_valid(capture, external_url, external_health, external_health_hash)
    capture_path = root_path / EXTERNAL_DEPLOYMENT_SMOKE_EXPECTED_CAPTURE_PATH
    encoded_capture = json.dumps(capture, indent=2, sort_keys=True).encode("utf-8")
    if capture_valid:
        capture_path.write_bytes(encoded_capture)
    return {
        "suite_name": "AEGIS External Deployment Smoke Capture Producer",
        "schema": "aegis-external-deployment-smoke-capture-producer-v1",
        "truth_claim": False,
        "capture_path": str(capture_path),
        "capture_written": capture_valid,
        "capture_schema": capture["schema"],
        "capture_hash": hashlib.sha256(encoded_capture).hexdigest() if capture_valid else "0" * 64,
        "external_url_present": bool(external_url),
        "external_health_hash": external_health_hash,
        "external_health_overall_ok": external_health.get("overall_ok") is True,
        "overall_ok": capture_valid,
        "error": "" if capture_valid else "external deployment health probe did not satisfy capture contract",
    }


def _external_deployment_smoke_capture(
    external_url: str,
    external_health: dict[str, Any],
    external_health_hash: str,
) -> dict[str, Any]:
    return {
        "schema": EXTERNAL_DEPLOYMENT_SMOKE_CAPTURE_SCHEMA,
        "truth_claim": False,
        "verifier": "operator-health-url-capture-producer",
        "production_blocker_id": EXTERNAL_DEPLOYMENT_SMOKE_BLOCKER_ID,
        "probe_contract": EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT,
        "probe_contract_hash": _stable_hash(EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT),
        "required_status": 200,
        "required_health_schema": "aegis-operator-health-v1",
        "required_truth_claim": False,
        "external_url": external_url,
        "external_health": external_health,
        "external_health_hash": external_health_hash,
    }


def _read_capture(path: Path) -> tuple[dict[str, Any], bool, bytes, str]:
    try:
        capture_bytes = path.read_bytes()
    except OSError:
        return {}, False, b"", "missing"
    if not capture_bytes:
        return {}, False, b"", "empty"
    try:
        payload = json.loads(capture_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, True, capture_bytes, "invalid-json"
    if not isinstance(payload, dict):
        return {}, True, capture_bytes, "non-object"
    return payload, True, capture_bytes, ""


def _capture_payload_valid(
    capture: dict[str, Any],
    external_url: str,
    external_health: dict[str, Any],
    external_health_hash: str,
) -> bool:
    captured_health = capture.get("external_health", {})
    return (
        capture.get("schema") == EXTERNAL_DEPLOYMENT_SMOKE_CAPTURE_SCHEMA
        and capture.get("truth_claim") is False
        and capture.get("production_blocker_id") == EXTERNAL_DEPLOYMENT_SMOKE_BLOCKER_ID
        and capture.get("probe_contract") == EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT
        and capture.get("probe_contract_hash") == _stable_hash(EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT)
        and capture.get("required_status") == 200
        and capture.get("required_health_schema") == "aegis-operator-health-v1"
        and capture.get("required_truth_claim") is False
        and capture.get("external_url") == external_url
        and capture.get("external_health_hash") == external_health_hash
        and capture.get("external_health") == external_health
        and isinstance(captured_health, dict)
        and captured_health.get("overall_ok") is True
        and captured_health.get("external_url_present") is True
        and captured_health.get("status") == 200
        and captured_health.get("health_schema") == "aegis-operator-health-v1"
        and captured_health.get("truth_claim") is False
    )


def _admission_hash(admission: dict[str, Any]) -> str:
    payload = dict(admission)
    payload["admission_hash"] = ""
    return _stable_hash(payload)


def _admission_state_bound(admission: dict[str, Any]) -> bool:
    missing_capture = (
        admission.get("capture_present") is False
        and admission.get("capture_hash") == "0" * 64
        and admission.get("admission_status") == "missing"
    )
    candidate_capture = (
        admission.get("capture_present") is True
        and admission.get("capture_valid") is True
        and _nonzero_hex(str(admission.get("capture_hash", "")))
        and admission.get("admission_status")
        == "candidate-present-external-deployment-smoke-gate-required"
    )
    return (
        admission.get("schema") == EXTERNAL_DEPLOYMENT_SMOKE_ADMISSION_SCHEMA
        and admission.get("expected_capture_path") == EXTERNAL_DEPLOYMENT_SMOKE_EXPECTED_CAPTURE_PATH
        and admission.get("probe_contract") == EXTERNAL_DEPLOYMENT_SMOKE_PROBE_CONTRACT
        and _nonzero_hex(str(admission.get("probe_contract_hash", "")))
        and admission.get("production_blocker_id") == EXTERNAL_DEPLOYMENT_SMOKE_BLOCKER_ID
        and admission.get("admission_hash") == _admission_hash(admission)
        and _nonzero_hex(str(admission.get("admission_hash", "")))
        and (missing_capture or candidate_capture)
    )


def _run_command(command: list[str], timeout_seconds: int) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
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
        }
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-1000:],
        "stderr_tail": result.stderr[-1000:],
    }


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


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


def _contains_all(text: str, needles: tuple[str, ...]) -> bool:
    return all(needle in text for needle in needles)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    if "--write-capture" in sys.argv[1:]:
        result = write_external_deployment_smoke_capture(ROOT)
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["overall_ok"] else 1
    report = evaluate_external_deployment_smoke_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
