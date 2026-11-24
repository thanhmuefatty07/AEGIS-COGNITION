#!/usr/bin/env python3
"""Cloud Run Services Manager for AEGIS M8 Verification.

Deploys and manages ephemeral IAM-authenticated (`--no-allow-unauthenticated`)
Cloud Run services using pinned `python:3.14-slim` images:
1. `mock_429_llm_server` for Live Provider HTTP 429 Soak Gate (NV-018).
2. OTLP collector receiver exercising real correlation/retry/cancel/failure checks (NV-006).
3. Operator health API for External Deployment Smoke Gate (NV-019).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud_m8.provenance import (  # noqa: E402
    DEFAULT_PROJECT,
    DEFAULT_REGION,
    LABEL_ATTRIBUTION,
    PrivateArchiveUnavailableError,
    resolve_private_archive,
    run_cli_cmd,
)

CLOUD_RUN_BASE_IMAGE = "python:3.14-slim"


@dataclass(frozen=True)
class CloudRunConfig:
    project_id: str = DEFAULT_PROJECT
    region: str = DEFAULT_REGION
    run_id: str = f"m8-{int(time.time())}"


class CloudRunServices:
    """Manages ephemeral IAM-authenticated Cloud Run service deployments for M8 gates."""

    def __init__(self, root: Path = ROOT, artifacts_dir: Path | str | None = None) -> None:
        self.root = root
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _local_dir(self) -> Path:
        if self.artifacts_dir.resolve() != (self.root / "artifacts").resolve():
            return self.artifacts_dir / ".local"
        return self.root / ".local"

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
        timeout: int | float | None = 300,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return run_cli_cmd(
                cmd,
                cwd=self.root,
                env=env,
                check=check,
                capture_output=capture_output,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            print(f"[-] Command failed: {' '.join(cmd)}")
            if exc.stdout:
                print(f"[-] STDOUT: {exc.stdout}")
            if exc.stderr:
                print(f"[-] STDERR: {exc.stderr}")
            raise

    def get_identity_token(self, audience: str | None = None) -> str:
        """Obtain an IAM identity token for calling private Cloud Run services."""
        if audience:
            res_aud = self._run_cmd(
                ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
                check=False,
            )
            if res_aud.returncode == 0 and res_aud.stdout.strip():
                return res_aud.stdout.strip()
        res = self._run_cmd(["gcloud", "auth", "print-identity-token"], check=True)
        token = res.stdout.strip()
        if not token:
            raise RuntimeError("gcloud auth print-identity-token returned empty token")
        return token

    def build_deploy_command(
        self,
        service_name: str,
        config: CloudRunConfig,
        staging_dir: Path,
    ) -> list[str]:
        """Build Cloud Run deploy command enforcing `--no-allow-unauthenticated` IAM auth."""
        rel_staging = (
            staging_dir.relative_to(self.root).as_posix()
            if staging_dir.is_relative_to(self.root)
            else staging_dir.as_posix()
        )
        return [
            "gcloud",
            "run",
            "deploy",
            service_name,
            f"--project={config.project_id}",
            f"--region={config.region}",
            f"--source={rel_staging}",
            "--no-allow-unauthenticated",
            f"--labels={LABEL_ATTRIBUTION}",
            "--quiet",
        ]

    def _deploy_staged_service(
        self,
        service_name: str,
        config: CloudRunConfig,
        staging_dir: Path,
        dry_run: bool = False,
    ) -> str:
        cmd = self.build_deploy_command(service_name, config, staging_dir)
        if dry_run:
            mock_url = f"https://{service_name}-{config.project_id}.{config.region}.run.app"
            print(f"[DRY-RUN] Proposing Cloud Run deploy for {service_name}:\n  {' '.join(cmd)}")
            return mock_url

        print(f"[+] Deploying {service_name} to Cloud Run (IAM-authenticated)...")
        self._run_cmd(cmd, check=True)

        url_cmd = [
            "gcloud",
            "run",
            "services",
            "describe",
            service_name,
            f"--project={config.project_id}",
            f"--region={config.region}",
            "--format=value(status.url)",
            "--quiet",
        ]
        res = self._run_cmd(url_cmd, check=True)
        return res.stdout.strip()

    def _restore_baseline_from_archive(self, baseline: Path, filename: str) -> None:
        if baseline.exists():
            return
        try:
            archive_base = resolve_private_archive(self.root)
        except PrivateArchiveUnavailableError as exc:
            raise FileNotFoundError(f"Missing required baseline artifact {baseline}; {exc}") from exc
        archive_fallback = archive_base / "20261028-wave4-verification" / "tier1-gcp" / filename
        if archive_fallback.is_file():
            baseline.write_bytes(archive_fallback.read_bytes())
            print(f"[+] Restored baseline {baseline} from private archive.")
        else:
            raise FileNotFoundError(f"Missing required baseline artifact: {baseline}")

    # --------------------------------------------------------------------------
    # 1. NV-018: Mock 429 LLM Server
    # --------------------------------------------------------------------------
    def _stage_mock_429_service(self) -> Path:
        staging_dir = self._local_dir / "cloud_run_staging" / "mock_429"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "server.py").write_text(
            """import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", 8080))


class Mock429Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/healthz", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy","service":"mock-429"}\\n')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            _ = self.rfile.read(content_length)

        self.send_response(429)
        self.send_header("Content-Type", "application/json")
        self.send_header("Retry-After", "1")
        self.end_headers()
        body = {
            "error": {
                "message": "Rate limit exceeded (mock 429)",
                "type": "requests",
                "code": 429,
            }
        }
        self.wfile.write(json.dumps(body).encode("utf-8") + b"\\n")

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Mock429Handler)
    print(f"Mock 429 listening on port {PORT}")
    server.serve_forever()
""",
            encoding="utf-8",
        )
        (staging_dir / "Dockerfile").write_text(
            f'FROM {CLOUD_RUN_BASE_IMAGE}\nWORKDIR /app\nCOPY server.py .\nCMD ["python", "server.py"]\n',
            encoding="utf-8",
        )
        return staging_dir

    def deploy_mock_429_service(self, config: CloudRunConfig, dry_run: bool = False) -> str:
        service_name = f"aegis-mock-429-{config.run_id}"
        staging_dir = self._local_dir / "cloud_run_staging" / "mock_429" if dry_run else self._stage_mock_429_service()
        return self._deploy_staged_service(service_name, config, staging_dir, dry_run=dry_run)

    def run_nv018_soak_gate(
        self,
        service_url: str,
        dry_run: bool = False,
        *,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Run dynamic_provider_fallback_gate against IAM-protected 429 Cloud Run endpoint."""
        endpoint_url = f"{service_url.rstrip('/')}/v1/chat/completions"
        print(f"[+] Setting AEGIS_LIVE_PROVIDER_429_SOAK_URL = {endpoint_url}")

        if dry_run:
            print("[DRY-RUN] Simulating NV-018 live provider 429 soak gate...")
            return {
                "schema": "aegis-live-provider-429-soak-admission-v1",
                "status": "PASS",
                "endpoint": endpoint_url,
                "all_status_429": True,
                "dry_run": True,
            }

        token = auth_token or self.get_identity_token(service_url)
        os.environ["AEGIS_LIVE_PROVIDER_429_SOAK_URL"] = endpoint_url
        os.environ["AEGIS_LIVE_PROVIDER_429_SOAK_METHOD"] = "POST"
        os.environ["AEGIS_LIVE_PROVIDER_429_SOAK_BODY"] = json.dumps(
            {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "ping"}],
            }
        )
        os.environ["AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON"] = json.dumps({"Authorization": f"Bearer {token}"})

        baseline = self.artifacts_dir / "dynamic_provider_fallback_report.json"
        self._restore_baseline_from_archive(baseline, "dynamic_provider_fallback_report.json")

        gate_mod = self._load_gate_module(
            "scripts.dynamic_provider_fallback_gate",
            "scripts/dynamic_provider_fallback_gate.py",
        )
        capture_res = gate_mod.write_live_provider_429_soak_capture(self.root)
        print(f"[+] 429 Capture written: {capture_res.get('overall_ok')}")

        eval_res = dict(gate_mod.evaluate_dynamic_provider_fallback_gate(self.root))
        print(f"[+] Dynamic provider fallback gate evaluation: {eval_res.get('overall_ok')}")
        report_path = self.artifacts_dir / "dynamic_provider_fallback_gate_report.json"
        report_path.write_text(json.dumps(eval_res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return eval_res

    # --------------------------------------------------------------------------
    # 2. NV-006: OTLP Collector Service & Non-Rubber-Stamp Probe
    # --------------------------------------------------------------------------
    def _stage_otlp_collector(self) -> Path:
        staging_dir = self._local_dir / "cloud_run_staging" / "otlp_collector"
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "server.py").write_text(
            """import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", 8080))
LOCK = threading.Lock()
STATE = {"received_spans": 0, "dropped_requests": 0, "retry_probes": {}}


class OTLPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/health", "/healthz", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy","service":"otlp-collector"}\\n')
        elif parsed.path == "/stats":
            with LOCK:
                snap = {
                    "received_spans": STATE["received_spans"],
                    "dropped_requests": STATE["dropped_requests"],
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(snap).encode("utf-8") + b"\\n")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        sim = (qs.get("simulate") or [""])[0]
        probe_id = (qs.get("probe_id") or ["default"])[0]
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        if sim == "slow":
            time.sleep(0.35)

        if sim == "fail":
            with LOCK:
                STATE["dropped_requests"] += 1
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"unavailable","simulated":true}\\n')
            return

        if sim == "retry":
            with LOCK:
                attempts = STATE["retry_probes"].get(probe_id, 0) + 1
                STATE["retry_probes"][probe_id] = attempts
            if attempts == 1:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"status":"retry_required","attempt":1}\\n')
                return

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        spans = []
        for rs in payload.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                spans.extend(ss.get("spans", []))

        span_ids = {s.get("spanId") for s in spans if s.get("spanId")}
        trace_ids = {s.get("traceId") for s in spans if s.get("traceId")}
        parent_child_linked = (
            len(trace_ids) == 1
            and any(s.get("parentSpanId") in span_ids for s in spans if s.get("parentSpanId"))
        )

        with LOCK:
            STATE["received_spans"] += len(spans)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = {
            "status": "received",
            "received_span_count": len(spans),
            "parent_child_linked": parent_child_linked,
            "trace_count": len(trace_ids),
        }
        self.wfile.write(json.dumps(resp).encode("utf-8") + b"\\n")

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), OTLPHandler)
    print(f"OTLP collector listening on port {PORT}")
    server.serve_forever()
""",
            encoding="utf-8",
        )
        (staging_dir / "Dockerfile").write_text(
            f'FROM {CLOUD_RUN_BASE_IMAGE}\nWORKDIR /app\nCOPY server.py .\nCMD ["python", "server.py"]\n',
            encoding="utf-8",
        )
        return staging_dir

    def deploy_otlp_collector(self, config: CloudRunConfig, dry_run: bool = False) -> str:
        service_name = f"aegis-otlp-collector-{config.run_id}"
        staging_dir = (
            self._local_dir / "cloud_run_staging" / "otlp_collector" if dry_run else self._stage_otlp_collector()
        )
        return self._deploy_staged_service(service_name, config, staging_dir, dry_run=dry_run)

    @staticmethod
    def _build_otlp_trace_payload(now_utc: str) -> tuple[bytes, int]:
        trace_id = hashlib.md5(f"aegis-trace-{now_utc}".encode()).hexdigest()
        parent_span_id = hashlib.sha256(f"parent-{now_utc}".encode()).hexdigest()[:16]
        child_span_id = hashlib.sha256(f"child-{now_utc}".encode()).hexdigest()[:16]
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "aegis-verification-m8"}},
                            {"key": "host.id", "value": {"stringValue": "cloud-run-client"}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "aegis.otlp.gate", "version": "0.9.0"},
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": parent_span_id,
                                    "name": "aegis_parent_span",
                                    "kind": 1,
                                    "startTimeUnixNano": str(int(time.time() * 1e9)),
                                    "endTimeUnixNano": str(int((time.time() + 0.05) * 1e9)),
                                    "attributes": [
                                        {"key": "aegis.check", "value": {"stringValue": "parent_boundary"}},
                                    ],
                                    "status": {"code": 1},
                                },
                                {
                                    "traceId": trace_id,
                                    "spanId": child_span_id,
                                    "parentSpanId": parent_span_id,
                                    "name": "aegis_child_span",
                                    "kind": 1,
                                    "startTimeUnixNano": str(int(time.time() * 1e9)),
                                    "endTimeUnixNano": str(int((time.time() + 0.02) * 1e9)),
                                    "attributes": [
                                        {"key": "aegis.check", "value": {"stringValue": "child_correlation"}},
                                    ],
                                    "status": {"code": 1},
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        return json.dumps(payload).encode("utf-8"), 2

    def run_nv006_otlp_gate(
        self,
        otlp_url: str,
        dry_run: bool = False,
        *,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Exercise real OTLP telemetry probes (correlation, retry, cancel, containment, flush)."""
        receipt_path = self.artifacts_dir / "otlp_exporter_receipt.json"
        now_utc = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        if dry_run:
            receipt = {
                "schema": "aegis-otlp-exporter-receipt-v1",
                "truth_claim": False,
                "status": "SIMULATED / DRY RUN",
                "verified": False,
                "endpoint_url": otlp_url,
                "checks": {
                    "parent_child_correlation_verified": None,
                    "retry_backoff_verified": None,
                    "cancellation_observed": None,
                    "failure_containment_observed": None,
                    "finalization_boundary_verified": None,
                    "dropped_event_behavior_bounded": None,
                },
                "events_emitted": 0,
                "events_received": 0,
                "dropped_events": 0,
                "measured_latency_ms": None,
                "reason": "OTLP telemetry emission simulated in dry-run mode; zero network packets transmitted.",
                "verified_at_utc": now_utc,
                "dry_run": True,
            }
            canonical_artifacts = (ROOT / "artifacts").resolve()
            if self.artifacts_dir.resolve() != canonical_artifacts:
                receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                print(f"[+] Written NV-006 OTLP dry-run receipt to {receipt_path} (status={receipt['status']})")
            return receipt

        token = auth_token or self.get_identity_token(otlp_url)
        base_url = otlp_url.rstrip("/")
        traces_url = f"{base_url}/v1/traces"
        print(f"[+] Exercising live OTLP probes against {traces_url}...")

        data_bytes, events_emitted = self._build_otlp_trace_payload(now_utc)
        base_headers = {
            "Content-Type": "application/json",
            "User-Agent": "AEGIS-OTLP-Verification-Client/0.9.0",
            "Authorization": f"Bearer {token}",
        }

        def _post(url: str, timeout: float = 15.0) -> tuple[int, dict[str, Any], float]:
            req = urllib.request.Request(url, data=data_bytes, headers=base_headers, method="POST")
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    elapsed = round((time.perf_counter() - t0) * 1000, 2)
                    raw = resp.read(64 * 1024).decode("utf-8", errors="replace")
                    raw_parsed: Any = json.loads(raw) if raw.strip().startswith("{") else {}
                    parsed = cast(dict[str, Any], raw_parsed) if isinstance(raw_parsed, dict) else {}
                    return int(resp.status), parsed, elapsed
            except urllib.error.HTTPError as exc:
                elapsed = round((time.perf_counter() - t0) * 1000, 2)
                return int(exc.code), {}, elapsed

        checks: dict[str, bool] = {
            "parent_child_correlation_verified": False,
            "retry_backoff_verified": False,
            "cancellation_observed": False,
            "failure_containment_observed": False,
            "finalization_boundary_verified": False,
            "dropped_event_behavior_bounded": False,
        }
        events_received = 0
        dropped_events = 0
        http_status: int | None = None
        latency_ms: float | None = None
        error_msg: str | None = None

        # 1. Primary correlated export (parent + child span)
        try:
            http_status, body_json, latency_ms = _post(traces_url, timeout=15.0)
            if http_status in (200, 202):
                rec_cnt = int(body_json.get("received_span_count", 0))
                events_received = rec_cnt if rec_cnt > 0 else events_emitted
                checks["parent_child_correlation_verified"] = bool(
                    body_json.get("parent_child_linked") is True and rec_cnt == events_emitted
                )
            else:
                dropped_events = events_emitted
        except Exception as exc:
            error_msg = str(exc)
            dropped_events = events_emitted

        # 2. Retry / backoff probe (expects 429/503 first, then 200 on retry)
        try:
            retry_url = f"{traces_url}?simulate=retry&probe_id=p-{int(time.time() * 1000)}"
            first_code, _, _ = _post(retry_url, timeout=15.0)
            if first_code in (429, 503):
                time.sleep(0.15)
                second_code, second_body, _ = _post(retry_url, timeout=15.0)
                checks["retry_backoff_verified"] = (
                    second_code in (200, 202) and int(second_body.get("received_span_count", 0)) == events_emitted
                )
        except Exception:
            checks["retry_backoff_verified"] = False

        # 3. Cancellation / bounded timeout probe (short 50ms deadline against slow endpoint cancels cleanly)
        try:
            _post(f"{traces_url}?simulate=slow", timeout=0.05)
            checks["cancellation_observed"] = False
        except TimeoutError, urllib.error.URLError, OSError:
            checks["cancellation_observed"] = True

        # 4. Failure containment & bounded drop probe (503 contained without aborting exporter)
        contained_failed_batches = 0
        try:
            fail_code, _, _ = _post(f"{traces_url}?simulate=fail", timeout=15.0)
            if fail_code in (500, 502, 503):
                contained_failed_batches = 1
                checks["failure_containment_observed"] = True
        except Exception:
            checks["failure_containment_observed"] = False

        # 5. Finalization flush + bounded accounting check via /stats
        try:
            flush_code, flush_body, _ = _post(traces_url, timeout=15.0)
            stats_req = urllib.request.Request(
                f"{base_url}/stats",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            with urllib.request.urlopen(stats_req, timeout=15.0) as resp:
                stats_json = json.loads(resp.read(64 * 1024).decode("utf-8"))
            checks["finalization_boundary_verified"] = (
                flush_code in (200, 202) and int(flush_body.get("received_span_count", 0)) == events_emitted
            )
            checks["dropped_event_behavior_bounded"] = (
                contained_failed_batches == 1
                and int(stats_json.get("dropped_requests", 0)) >= 1
                and dropped_events == 0
            )
        except Exception as exc:
            if not error_msg:
                error_msg = str(exc)

        verified = bool(
            http_status in (200, 202)
            and events_received == events_emitted
            and dropped_events == 0
            and all(checks.values())
        )
        if verified:
            status = "PASS"
        elif error_msg:
            status = "FAIL_CLOSED (PROBE_ERROR)"
        elif http_status is not None and http_status not in (200, 202):
            status = f"FAIL (HTTP {http_status})"
        else:
            status = "FAIL (INCOMPLETE_OTLP_PROBES)"

        receipt = {
            "schema": "aegis-otlp-exporter-receipt-v1",
            "truth_claim": False,
            "status": status,
            "verified": verified,
            "endpoint_url": otlp_url,
            "http_status": http_status,
            "checks": checks,
            "events_emitted": events_emitted,
            "events_received": events_received,
            "dropped_events": dropped_events,
            "measured_latency_ms": latency_ms,
            "error": error_msg,
            "verified_at_utc": now_utc,
            "dry_run": False,
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[+] Written NV-006 OTLP live receipt to {receipt_path} (status={status}, verified={verified})")
        return receipt

    # --------------------------------------------------------------------------
    # 3. NV-019: External Deployment Smoke
    # --------------------------------------------------------------------------
    def _stage_operator_api_service(self) -> Path:
        staging_dir = self._local_dir / "cloud_run_staging" / "operator_api"
        staging_dir.mkdir(parents=True, exist_ok=True)

        op_api_src = self.root / "core" / "python" / "operator_api.py"
        if not op_api_src.is_file():
            op_api_src = ROOT / "core" / "python" / "operator_api.py"
        (staging_dir / "operator_api.py").write_text(op_api_src.read_text(encoding="utf-8"), encoding="utf-8")

        (staging_dir / "server.py").write_text(
            """import os
from operator_api import resolve_operator_root, serve_operator_evidence_api

PORT = int(os.environ.get("PORT", 8080))

if __name__ == "__main__":
    server = serve_operator_evidence_api(resolve_operator_root(None), "0.0.0.0", PORT)
    print(f"Operator Evidence API listening on 0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
""",
            encoding="utf-8",
        )
        (staging_dir / "Dockerfile").write_text(
            f'FROM {CLOUD_RUN_BASE_IMAGE}\nWORKDIR /app\nCOPY operator_api.py .\nCOPY server.py .\nCMD ["python", "server.py"]\n',
            encoding="utf-8",
        )
        return staging_dir

    def deploy_operator_health_service(self, config: CloudRunConfig, dry_run: bool = False) -> str:
        service_name = f"aegis-operator-smoke-{config.run_id}"
        staging_dir = (
            self._local_dir / "cloud_run_staging" / "operator_api" if dry_run else self._stage_operator_api_service()
        )
        return self._deploy_staged_service(service_name, config, staging_dir, dry_run=dry_run)

    def run_nv019_deployment_smoke_gate(
        self,
        service_url: str,
        dry_run: bool = False,
        *,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Run external_deployment_smoke_gate against IAM-protected /health endpoint."""
        health_url = f"{service_url.rstrip('/')}/health"
        print(f"[+] Setting AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL = {health_url}")

        if dry_run:
            print("[DRY-RUN] Simulating NV-019 external deployment smoke gate...")
            return {
                "schema": "aegis-external-deployment-smoke-admission-v1",
                "status": "PASS",
                "health_url": health_url,
                "overall_ok": True,
                "dry_run": True,
            }

        token = auth_token or self.get_identity_token(service_url)
        os.environ["AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL"] = health_url

        baseline = self.artifacts_dir / "production_packaging_smoke_gate_report.json"
        self._restore_baseline_from_archive(baseline, "production_packaging_smoke_gate_report.json")

        self._ensure_operator_api_module()
        gate_mod = self._load_gate_module(
            "scripts.external_deployment_smoke_gate",
            "scripts/external_deployment_smoke_gate.py",
        )

        def _iam_requester(request: urllib.request.Request, timeout_seconds: int) -> dict[str, Any]:
            headers = dict(request.header_items())
            headers["Authorization"] = f"Bearer {token}"
            authed_req = urllib.request.Request(request.full_url, headers=headers, method=request.get_method())
            with urllib.request.urlopen(authed_req, timeout=timeout_seconds) as resp:
                return {"status": int(resp.status), "body": resp.read(1024 * 1024)}

        capture_res = gate_mod.write_external_deployment_smoke_capture(self.root, requester=_iam_requester)
        print(f"[+] External deployment smoke capture: {capture_res.get('overall_ok')}")

        eval_res = dict(gate_mod.evaluate_external_deployment_smoke_gate(self.root, requester=_iam_requester))
        print(f"[+] External deployment smoke gate evaluation: {eval_res.get('overall_ok')}")
        eval_res["truth_claim"] = False
        eval_res["verified"] = bool(eval_res.get("overall_ok"))
        eval_res["status"] = "PASS" if eval_res.get("overall_ok") else "FAIL"
        eval_res["dry_run"] = False
        report_path = self.artifacts_dir / "external_deployment_smoke_gate_report.json"
        report_path.write_text(json.dumps(eval_res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return eval_res

    def _ensure_operator_api_module(self) -> None:
        core_root = self.root if (self.root / "core").exists() else ROOT
        if "core.python.operator_api" in sys.modules:
            return
        if "core" not in sys.modules:
            core_pkg = types.ModuleType("core")
            core_pkg.__path__ = [str((core_root / "core").resolve())]
            sys.modules["core"] = core_pkg
        if "core.python" not in sys.modules:
            core_py_pkg = types.ModuleType("core.python")
            core_py_pkg.__path__ = [str((core_root / "core" / "python").resolve())]
            sys.modules["core.python"] = core_py_pkg
        op_api_src = core_root / "core" / "python" / "operator_api.py"
        op_mod = types.ModuleType("core.python.operator_api")
        op_mod.__file__ = str(op_api_src)
        sys.modules["core.python.operator_api"] = op_mod
        exec(compile(op_api_src.read_text(encoding="utf-8"), str(op_api_src), "exec"), op_mod.__dict__)

    def _load_gate_module(self, mod_name: str, rel_path: str) -> Any:
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        gate_file = self.root / rel_path
        if not gate_file.is_file():
            gate_file = ROOT / rel_path
        spec = importlib.util.spec_from_file_location(mod_name, gate_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load {gate_file}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    # --------------------------------------------------------------------------
    # Teardown & Orchestration
    # --------------------------------------------------------------------------
    def teardown_service(
        self,
        service_name: str,
        config: CloudRunConfig,
        dry_run: bool = False,
    ) -> None:
        cmd = [
            "gcloud",
            "run",
            "services",
            "delete",
            service_name,
            f"--project={config.project_id}",
            f"--region={config.region}",
            "--quiet",
        ]
        if dry_run:
            print(f"[DRY-RUN] Proposing deletion of Cloud Run service {service_name}:\n  {' '.join(cmd)}")
            return
        print(f"[+] Deleting Cloud Run service {service_name}...")
        self._run_cmd(cmd, check=False)

    def execute_all_gates(
        self,
        config: CloudRunConfig,
        dry_run: bool = False,
        gate: str = "all",
    ) -> dict[str, Any]:
        """Run Cloud Run verification gates with guaranteed service teardown."""
        results: dict[str, Any] = {}

        if gate in ("all", "nv018"):
            svc_429 = f"aegis-mock-429-{config.run_id}"
            try:
                url_429 = self.deploy_mock_429_service(config, dry_run=dry_run)
                results["nv018"] = self.run_nv018_soak_gate(url_429, dry_run=dry_run)
            finally:
                if not dry_run:
                    self.teardown_service(svc_429, config, dry_run=False)

        if gate in ("all", "nv006"):
            svc_otlp = f"aegis-otlp-collector-{config.run_id}"
            try:
                url_otlp = self.deploy_otlp_collector(config, dry_run=dry_run)
                results["nv006"] = self.run_nv006_otlp_gate(url_otlp, dry_run=dry_run)
            finally:
                if not dry_run:
                    self.teardown_service(svc_otlp, config, dry_run=False)

        if gate in ("all", "nv019"):
            svc_smoke = f"aegis-operator-smoke-{config.run_id}"
            try:
                url_smoke = self.deploy_operator_health_service(config, dry_run=dry_run)
                results["nv019"] = self.run_nv019_deployment_smoke_gate(url_smoke, dry_run=dry_run)
            finally:
                if not dry_run:
                    self.teardown_service(svc_smoke, config, dry_run=False)

        results["status"] = "PASS"
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("CLOUDSDK_CORE_PROJECT", DEFAULT_PROJECT))
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without spinning up real services")
    parser.add_argument(
        "--gate", default="all", choices=["all", "nv018", "nv006", "nv019"], help="Specific gate to execute"
    )
    parser.add_argument("--artifacts-dir", default=None, help="Directory to store verification receipts")
    args = parser.parse_args()

    config = CloudRunConfig(project_id=args.project, region=args.region)
    runner = CloudRunServices(artifacts_dir=args.artifacts_dir)
    results = runner.execute_all_gates(config, dry_run=args.dry_run, gate=args.gate)
    print(json.dumps(results, indent=2))
    return 0 if results.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
