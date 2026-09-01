from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SNAPSHOT_SCHEMA = "aegis-operator-evidence-snapshot-v1"
HEALTH_SCHEMA = "aegis-operator-health-v1"
PRODUCTION_CLOSURE_SCHEMA = "aegis-operator-production-closure-v1"
OPERATOR_VERIFIER = "rust-replay-and-artifact-gates"
PYTHON_OPERATOR_ROLE = "read-only-operator-summary"

DEFAULT_OPERATOR_ARTIFACTS: tuple[str, ...] = (
    "run_checks_report.json",
    "benchmark_gate_report.json",
    "constitution_audit_report.json",
    "e2e_release_gate_report.json",
    "provider_route_gate_report.json",
    "dependency_audit_gate_report.json",
    "production_readiness_report.json",
    "production_closure_workflow_report.json",
    "progress_gate_report.json",
    "browser_ops_bench_verification_report.json",
    "replay_endurance_report.json",
    "replay_chaos_scorecard.json",
    "agentic_sdk_context_report.json",
)


def resolve_operator_root(explicit_root: str | Path | None = None) -> Path:
    if explicit_root is not None and str(explicit_root).strip():
        return Path(explicit_root).resolve()
    operator_root = os.environ.get("AEGIS_OPERATOR_ROOT", "").strip()
    if operator_root:
        return Path(operator_root).resolve()
    artifacts_dir = os.environ.get("AEGIS_ARTIFACTS_DIR", "").strip()
    if artifacts_dir:
        artifact_path = Path(artifacts_dir).resolve()
        return artifact_path.parent if artifact_path.name == "artifacts" else artifact_path
    return Path.cwd().resolve()


@dataclass(frozen=True)
class EvidenceDigest:
    algorithm: str
    hex: str

    def to_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "hex": self.hex,
        }


@dataclass(frozen=True)
class OperatorArtifactSummary:
    logical_name: str
    path: str
    exists: bool
    byte_len: int
    digest: EvidenceDigest | None
    json_ok: bool
    schema: str
    observed_ok: bool | None
    passed: int | None
    failed: int | None
    check_count: int | None
    hash_field_count: int
    nonzero_hash_field_count: int
    error: str

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_name": self.logical_name,
            "path": self.path,
            "exists": self.exists,
            "byte_len": self.byte_len,
            "digest": None if self.digest is None else self.digest.to_dict(),
            "json_ok": self.json_ok,
            "schema": self.schema,
            "observed_ok": self.observed_ok,
            "passed": self.passed,
            "failed": self.failed,
            "check_count": self.check_count,
            "hash_field_count": self.hash_field_count,
            "nonzero_hash_field_count": self.nonzero_hash_field_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class OperatorEvidenceSnapshot:
    root: str
    artifacts_dir: str
    truth_claim: bool
    verifier: str
    python_role: str
    artifact_summaries: tuple[OperatorArtifactSummary, ...]
    missing_artifacts: tuple[str, ...]
    release_gate_observed_ok: bool
    snapshot_digest: EvidenceDigest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SNAPSHOT_SCHEMA,
            "root": self.root,
            "artifacts_dir": self.artifacts_dir,
            "truth_claim": self.truth_claim,
            "verifier": self.verifier,
            "python_role": self.python_role,
            "artifact_summaries": [summary.to_dict() for summary in self.artifact_summaries],
            "missing_artifacts": list(self.missing_artifacts),
            "release_gate_observed_ok": self.release_gate_observed_ok,
            "snapshot_digest": self.snapshot_digest.to_dict(),
        }


@dataclass(frozen=True)
class OperatorApiResponse:
    status: int
    content_type: str
    body: bytes

    def json(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class OperatorEvidenceApi:
    def __init__(
        self,
        root: str | Path,
        artifact_names: tuple[str, ...] = DEFAULT_OPERATOR_ARTIFACTS,
    ) -> None:
        self.root = Path(root).resolve()
        self.artifact_names = tuple(dict.fromkeys(artifact_names))

    def snapshot(self) -> OperatorEvidenceSnapshot:
        return build_operator_evidence_snapshot(self.root, self.artifact_names)

    def handle_get(self, raw_path: str) -> OperatorApiResponse:
        path = urlparse(raw_path).path
        if path in ("", "/", "/health"):
            snapshot = self.snapshot()
            payload = {
                "schema": HEALTH_SCHEMA,
                "truth_claim": False,
                "verifier": snapshot.verifier,
                "python_role": snapshot.python_role,
                "release_gate_observed_ok": snapshot.release_gate_observed_ok,
                "missing_artifact_count": len(snapshot.missing_artifacts),
                "artifact_count": len(snapshot.artifact_summaries),
                "snapshot_digest": snapshot.snapshot_digest.to_dict(),
            }
            return _json_response(200, payload)
        if path == "/snapshot":
            return _json_response(200, self.snapshot().to_dict())
        if path == "/artifacts":
            snapshot = self.snapshot()
            payload = {
                "schema": "aegis-operator-artifact-index-v1",
                "truth_claim": False,
                "verifier": snapshot.verifier,
                "artifacts": [summary.to_dict() for summary in snapshot.artifact_summaries],
                "snapshot_digest": snapshot.snapshot_digest.to_dict(),
            }
            return _json_response(200, payload)
        if path == "/production-closure":
            return _json_response(*_production_closure_response(self.root))
        return _json_response(
            404,
            {
                "schema": "aegis-operator-api-error-v1",
                "truth_claim": False,
                "error": "unknown read-only operator endpoint",
            },
        )


def _production_closure_response(root: Path) -> tuple[int, dict[str, Any]]:
    path = root / "artifacts" / "production_closure_workflow_report.json"
    if not path.exists():
        return 503, {
            "schema": PRODUCTION_CLOSURE_SCHEMA,
            "truth_claim": False,
            "overall_ok": False,
            "error": "missing production closure workflow artifact",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return 503, {
            "schema": PRODUCTION_CLOSURE_SCHEMA,
            "truth_claim": False,
            "overall_ok": False,
            "error": f"invalid production closure workflow artifact: {exc}",
        }
    if not isinstance(payload, dict) or payload.get("schema") != "aegis-production-closure-workflow-v1":
        return 503, {
            "schema": PRODUCTION_CLOSURE_SCHEMA,
            "truth_claim": False,
            "overall_ok": False,
            "error": "invalid production closure workflow schema",
        }
    steps = payload.get("steps", [])
    safe_steps = [step for step in steps if isinstance(step, dict)]
    ready_blocker_ids = [
        str(step.get("blocker_id", ""))
        for step in safe_steps
        if step.get("ready_to_execute") is True and str(step.get("blocker_id", ""))
    ]
    blocked_blocker_ids = [
        str(step.get("blocker_id", ""))
        for step in safe_steps
        if step.get("ready_to_execute") is not True and str(step.get("blocker_id", ""))
    ]
    artifact_digest = _digest_file(path)
    return 200, {
        "schema": PRODUCTION_CLOSURE_SCHEMA,
        "truth_claim": False,
        "verifier": OPERATOR_VERIFIER,
        "workflow_hash": str(payload.get("workflow_hash", "")),
        "artifact_digest": artifact_digest.to_dict(),
        "overall_ok": payload.get("overall_ok") is True,
        "execute": payload.get("execute") is True,
        "production_deployable": payload.get("production_deployable") is True,
        "active_blocker_count": _safe_int(payload.get("active_blocker_count")),
        "ready_to_execute_count": _safe_int(payload.get("ready_to_execute_count")),
        "blocked_preflight_count": _safe_int(payload.get("blocked_preflight_count")),
        "closed_blocker_count": _safe_int(payload.get("closed_blocker_count")),
        "ready_blocker_ids": ready_blocker_ids,
        "blocked_blocker_ids": blocked_blocker_ids,
    }


def build_operator_evidence_snapshot(
    root: str | Path,
    artifact_names: tuple[str, ...] = DEFAULT_OPERATOR_ARTIFACTS,
) -> OperatorEvidenceSnapshot:
    root_path = Path(root).resolve()
    artifacts_dir = root_path / "artifacts"
    summaries = tuple(
        _summarize_report_artifact(artifacts_dir / artifact_name, artifact_name)
        for artifact_name in artifact_names
    )
    missing = tuple(summary.logical_name for summary in summaries if not summary.exists)
    required_gate_names = {
        "run_checks_report.json",
        "benchmark_gate_report.json",
        "constitution_audit_report.json",
    }
    required_gate_summaries = [
        summary for summary in summaries if summary.logical_name in required_gate_names
    ]
    release_gate_observed_ok = bool(required_gate_summaries) and all(
        summary.exists and summary.json_ok and summary.observed_ok is True
        for summary in required_gate_summaries
    )
    unsigned_payload = {
        "schema": SNAPSHOT_SCHEMA,
        "root": str(root_path),
        "artifacts_dir": str(artifacts_dir),
        "truth_claim": False,
        "verifier": OPERATOR_VERIFIER,
        "python_role": PYTHON_OPERATOR_ROLE,
        "artifact_summaries": [summary.to_dict() for summary in summaries],
        "missing_artifacts": list(missing),
        "release_gate_observed_ok": release_gate_observed_ok,
    }
    snapshot_digest = _stable_digest_json(unsigned_payload)
    return OperatorEvidenceSnapshot(
        root=str(root_path),
        artifacts_dir=str(artifacts_dir),
        truth_claim=False,
        verifier=OPERATOR_VERIFIER,
        python_role=PYTHON_OPERATOR_ROLE,
        artifact_summaries=summaries,
        missing_artifacts=missing,
        release_gate_observed_ok=release_gate_observed_ok,
        snapshot_digest=snapshot_digest,
    )


def _safe_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def build_operator_http_handler(api: OperatorEvidenceApi) -> type[BaseHTTPRequestHandler]:
    class OperatorEvidenceRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response = api.handle_get(self.path)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return OperatorEvidenceRequestHandler


def serve_operator_evidence_api(
    root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    api = OperatorEvidenceApi(root)
    return ThreadingHTTPServer((host, port), build_operator_http_handler(api))


def _summarize_report_artifact(path: Path, logical_name: str) -> OperatorArtifactSummary:
    resolved = path.resolve()
    if not resolved.exists():
        return OperatorArtifactSummary(
            logical_name=logical_name,
            path=str(resolved),
            exists=False,
            byte_len=0,
            digest=None,
            json_ok=False,
            schema="",
            observed_ok=None,
            passed=None,
            failed=None,
            check_count=None,
            hash_field_count=0,
            nonzero_hash_field_count=0,
            error="missing artifact",
        )

    try:
        digest = _digest_file(resolved)
        byte_len = resolved.stat().st_size
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return OperatorArtifactSummary(
            logical_name=logical_name,
            path=str(resolved),
            exists=True,
            byte_len=resolved.stat().st_size if resolved.exists() else 0,
            digest=_digest_file(resolved) if resolved.exists() else None,
            json_ok=False,
            schema="",
            observed_ok=None,
            passed=None,
            failed=None,
            check_count=None,
            hash_field_count=0,
            nonzero_hash_field_count=0,
            error=str(exc),
        )

    hash_counts = _count_hash_fields(payload)
    return OperatorArtifactSummary(
        logical_name=logical_name,
        path=str(resolved),
        exists=True,
        byte_len=byte_len,
        digest=digest,
        json_ok=True,
        schema=_extract_schema(payload),
        observed_ok=_extract_observed_ok(payload),
        passed=_extract_int(payload, "passed"),
        failed=_extract_int(payload, "failed"),
        check_count=_extract_check_count(payload),
        hash_field_count=hash_counts[0],
        nonzero_hash_field_count=hash_counts[1],
        error="",
    )


def _extract_schema(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("schema", "schema_version", "suite_name"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    report = payload.get("report")
    if isinstance(report, dict):
        return _extract_schema(report)
    return ""


def _extract_observed_ok(payload: Any) -> bool | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("overall_ok")
    if isinstance(direct, bool):
        return direct
    nested_results = [
        value["overall_ok"]
        for value in payload.values()
        if isinstance(value, dict) and isinstance(value.get("overall_ok"), bool)
    ]
    if nested_results:
        return all(nested_results)
    report = payload.get("report")
    if isinstance(report, dict):
        return _extract_observed_ok(report)
    return None


def _extract_int(payload: Any, key: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    report = payload.get("report")
    if isinstance(report, dict):
        return _extract_int(report, key)
    return None


def _extract_check_count(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    checks = payload.get("checks")
    if isinstance(checks, list):
        return len(checks)
    report = payload.get("report")
    if isinstance(report, dict):
        return _extract_check_count(report)
    return None


def _count_hash_fields(value: Any) -> tuple[int, int]:
    total = 0
    nonzero = 0

    def walk(node: Any, key_hint: str = "") -> None:
        nonlocal total, nonzero
        if isinstance(node, dict):
            for key, child in node.items():
                child_key = str(key)
                if "hash" in child_key.lower():
                    total += 1
                    if _is_nonzero_hash_value(child):
                        nonzero += 1
                walk(child, child_key)
        elif isinstance(node, list):
            for child in node:
                walk(child, key_hint)

    walk(value)
    return total, nonzero


def _is_nonzero_hash_value(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) >= 16 and any(char != "0" for char in value)
    if isinstance(value, list):
        return len(value) > 0 and all(isinstance(item, int) for item in value) and any(item != 0 for item in value)
    return False


def _stable_digest_json(payload: Any) -> EvidenceDigest:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return _digest_bytes(encoded)


def _digest_file(path: Path) -> EvidenceDigest:
    algorithm, hasher = _new_hasher()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return EvidenceDigest(algorithm, hasher.hexdigest())


def _digest_bytes(payload: bytes) -> EvidenceDigest:
    algorithm, hasher = _new_hasher()
    hasher.update(payload)
    return EvidenceDigest(algorithm, hasher.hexdigest())


def _new_hasher() -> tuple[str, Any]:
    try:
        import blake3  # type: ignore
    except ImportError:
        return "sha256", hashlib.sha256()
    return "blake3", blake3.blake3()


def _json_response(status: int, payload: dict[str, Any]) -> OperatorApiResponse:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return OperatorApiResponse(status=status, content_type="application/json", body=body)
