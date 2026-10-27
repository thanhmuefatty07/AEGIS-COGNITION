import hashlib
import errno
import http.client
import ipaddress
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
    getproxies,
    proxy_bypass,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "dynamic_provider_fallback_gate_report.json"
LIVE_PROVIDER_429_SOAK_ADMISSION_SCHEMA = "aegis-live-provider-429-soak-admission-v1"
LIVE_PROVIDER_429_SOAK_CAPTURE_SCHEMA = "aegis-live-provider-429-soak-capture-v2"
LIVE_PROVIDER_429_SOAK_EXPECTED_PATH = "artifacts/live_provider_429_soak_capture.json"
LIVE_PROVIDER_429_SOAK_BLOCKER_ID = "live_provider_429_soak_missing"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinnedProviderHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned_address: str, **kwargs: Any) -> None:
        self._pinned_address = pinned_address
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        sys.audit("http.client.connect", self, self.host, self.port)
        self.sock = socket.create_connection((self._pinned_address, self.port), self.timeout, self.source_address)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                raise
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedProviderHTTPSHandler(HTTPSHandler):
    def __init__(self, pinned_address: str) -> None:
        super().__init__()
        self._pinned_address = pinned_address

    def https_open(self, req):
        def pinned_connection(host: str, **kwargs: Any) -> _PinnedProviderHTTPSConnection:
            return _PinnedProviderHTTPSConnection(host, self._pinned_address, **kwargs)

        return self.do_open(pinned_connection, req, context=self._context)


def evaluate_dynamic_provider_fallback_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifact_path = root_path / "artifacts" / "dynamic_provider_fallback_report.json"
    artifact = _read_json(artifact_path)
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    base_admission = report.get("live_provider_429_soak_admission", {})
    admission = _live_provider_429_soak_admission(root_path, report, base_admission)
    samples = report.get("sample_count", 0)
    latency_gate_ns = report.get("latency_gate_ns", 0)
    # Persisted capture fields and hashes are self-asserted; without an authenticated
    # producer receipt they remain candidates and cannot clear the live-traffic blocker.
    live_provider_traffic_present = False
    production_blocked_without_live_429 = not live_provider_traffic_present
    checks = (
        _check("artifact_present", bool(artifact)),
        _check("schema_version", artifact.get("schema_version") == 1),
        _check("report_schema", report.get("schema") == "aegis-dynamic-provider-fallback-report-v1"),
        _check("sample_count", samples == 32),
        _check("latency_gate_ns", latency_gate_ns == 1_000_000),
        _check("feedback_kind", report.get("feedback_kind") == "http_429"),
        _check(
            "rate_limited_primary",
            report.get("rate_limited_provider") == "openrouter" and report.get("rate_limited_model") == "gpt-4",
        ),
        _check(
            "selected_fallback",
            report.get("selected_provider") == "nim" and report.get("selected_model") == "llama-3-70b",
        ),
        _check("fallback_used", report.get("fallback_used") is True),
        _check("downgraded_model", report.get("downgraded_model") is True),
        _check("previous_ledger_hash_nonzero", _hash_array(report.get("previous_ledger_hash"))),
        _check("updated_ledger_hash_nonzero", _hash_array(report.get("updated_ledger_hash"))),
        _check("ledger_hash_drift", report.get("previous_ledger_hash") != report.get("updated_ledger_hash")),
        _check("feedback_hash_nonzero", _hash_array(report.get("feedback_hash"))),
        _check("admission_proof_hash_nonzero", _hash_array(report.get("admission_proof_hash"))),
        _check("fallback_proof_hash_nonzero", _hash_array(report.get("fallback_proof_hash"))),
        _check("throttled_provider_count", report.get("throttled_provider_count") == 1),
        _check("latency_window_recorded", _timing_window(report, samples)),
        _check(
            "latency_under_gate",
            report.get("latency_under_gate") is True
            and _positive_int(report.get("latency_max_ns"))
            and report.get("latency_max_ns") < latency_gate_ns,
        ),
        _check("all_samples_validated", report.get("all_samples_validated") is True),
        _check("report_hash_nonzero", _hash_array(report.get("report_hash"))),
        _check("live_429_soak_admission_schema", admission.get("schema") == LIVE_PROVIDER_429_SOAK_ADMISSION_SCHEMA),
        _check(
            "live_429_soak_admission_path",
            admission.get("expected_capture_path") == LIVE_PROVIDER_429_SOAK_EXPECTED_PATH,
        ),
        _check("live_429_soak_admission_hash", _hash_array(admission.get("admission_hash"))),
        _check("live_429_soak_provider_set_hash", _hash_array(admission.get("provider_set_hash"))),
        _check("live_429_soak_capture_contract_hash", _hash_array(admission.get("capture_contract_hash"))),
        _check("live_429_soak_admission_samples", admission.get("required_sample_count") == samples),
        _check("live_429_soak_admission_latency_gate", admission.get("latency_gate_ns") == latency_gate_ns),
        _check(
            "live_429_soak_admission_feedback", admission.get("expected_feedback_kind") == report.get("feedback_kind")
        ),
        _check(
            "live_429_soak_admission_primary",
            admission.get("primary_provider") == report.get("rate_limited_provider")
            and admission.get("primary_model") == report.get("rate_limited_model"),
        ),
        _check(
            "live_429_soak_admission_fallback",
            admission.get("fallback_provider") == report.get("selected_provider")
            and admission.get("fallback_model") == report.get("selected_model"),
        ),
        _check(
            "live_429_soak_admission_blocker",
            admission.get("production_blocker_id") == LIVE_PROVIDER_429_SOAK_BLOCKER_ID,
        ),
        _check("live_429_soak_admission_state_bound", _admission_state_bound(admission)),
        _check(
            "live_provider_429_soak_capture_missing_or_valid",
            (not admission.get("capture_present")) or admission.get("capture_valid") is True,
        ),
        _check(
            "live_provider_traffic_gap_disclosed",
            live_provider_traffic_present or report.get("live_provider_traffic_present") is False,
        ),
        _check(
            "production_blocked_without_live_429_soak",
            production_blocked_without_live_429 == (not live_provider_traffic_present),
        ),
    )
    payload = {
        "suite_name": "AEGIS Dynamic Provider Fallback Gate",
        "schema": "aegis-dynamic-provider-fallback-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-dynamic-provider-feedback-artifact-verifier",
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_hash(artifact_path),
        "dynamic_provider_fallback_evidence": {
            "sample_count": report.get("sample_count", 0),
            "latency_gate_ns": report.get("latency_gate_ns", 0),
            "feedback_kind": report.get("feedback_kind"),
            "rate_limited_provider": report.get("rate_limited_provider"),
            "rate_limited_model": report.get("rate_limited_model"),
            "selected_provider": report.get("selected_provider"),
            "selected_model": report.get("selected_model"),
            "fallback_used": report.get("fallback_used"),
            "downgraded_model": report.get("downgraded_model"),
            "throttled_provider_count": report.get("throttled_provider_count", 0),
            "latency_min_ns": report.get("latency_min_ns", 0),
            "latency_max_ns": report.get("latency_max_ns", 0),
            "latency_total_ns": report.get("latency_total_ns", 0),
            "latency_under_gate": report.get("latency_under_gate"),
            "all_samples_validated": report.get("all_samples_validated"),
        },
        "live_provider_429_soak_admission": {
            "schema": admission.get("schema", ""),
            "expected_capture_path": admission.get("expected_capture_path", ""),
            "capture_present": admission.get("capture_present"),
            "capture_hash": admission.get("capture_hash", []),
            "provider_set": admission.get("provider_set", ""),
            "provider_set_hash": admission.get("provider_set_hash", []),
            "capture_contract": admission.get("capture_contract", ""),
            "capture_contract_hash": admission.get("capture_contract_hash", []),
            "required_sample_count": admission.get("required_sample_count", 0),
            "latency_gate_ns": admission.get("latency_gate_ns", 0),
            "expected_feedback_kind": admission.get("expected_feedback_kind", ""),
            "primary_provider": admission.get("primary_provider", ""),
            "primary_model": admission.get("primary_model", ""),
            "fallback_provider": admission.get("fallback_provider", ""),
            "fallback_model": admission.get("fallback_model", ""),
            "capture_schema": admission.get("capture_schema", ""),
            "capture_valid": admission.get("capture_valid"),
            "capture_error": admission.get("capture_error", ""),
            "capture_verification_hash": admission.get("capture_verification_hash", []),
            "admission_status": admission.get("admission_status", ""),
            "production_blocker_id": admission.get("production_blocker_id", ""),
            "admission_hash": admission.get("admission_hash", []),
        },
        "live_provider_429_soak_admission_hash": admission.get("admission_hash", []),
        "live_provider_429_soak_admission_missing": (
            admission.get("capture_present") is False and admission.get("admission_status") == "missing"
        ),
        "live_provider_429_soak_capture_missing_or_valid": (
            (not admission.get("capture_present")) or admission.get("capture_valid") is True
        ),
        "live_provider_429_soak_admission_blocker_id": admission.get("production_blocker_id", ""),
        "live_provider_429_soak_state_recorded": True,
        "live_provider_traffic_present": live_provider_traffic_present,
        "production_provider_release_blocked_without_live_429_soak": production_blocked_without_live_429,
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
    }
    payload["overall_ok"] = payload["failed"] == 0
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _timing_window(report: dict[str, Any], samples: Any) -> bool:
    if not isinstance(samples, int) or isinstance(samples, bool) or samples <= 0:
        return False
    minimum = report.get("latency_min_ns")
    maximum = report.get("latency_max_ns")
    total = report.get("latency_total_ns")
    return (
        _positive_int(minimum)
        and _positive_int(maximum)
        and _positive_int(total)
        and minimum <= maximum
        and minimum * samples <= total <= maximum * samples
    )


def _live_provider_429_soak_admission(
    root: Path,
    report: dict[str, Any],
    base_admission: dict[str, Any],
) -> dict[str, Any]:
    admission = dict(base_admission)
    capture_path = root / LIVE_PROVIDER_429_SOAK_EXPECTED_PATH
    capture_payload, capture_present, capture_bytes, capture_error = _read_capture(capture_path)
    capture_valid = _live_provider_429_capture_valid(capture_payload, report, base_admission)
    capture_hash = _hash_bytes_array(capture_bytes) if capture_present else [0] * 32
    if capture_valid:
        admission_status = "candidate-present-live-soak-gate-required"
    elif capture_present:
        admission_status = "invalid-capture"
    else:
        admission_status = "missing"
    if capture_present and not capture_valid and not capture_error:
        if capture_payload.get("schema") != LIVE_PROVIDER_429_SOAK_CAPTURE_SCHEMA:
            capture_error = "unsupported or legacy capture schema"
        elif capture_payload.get("source_classification") != "external_https_endpoint_candidate":
            capture_error = "capture source is not an external HTTPS endpoint candidate"
        else:
            capture_error = "capture failed live 429 validation"
    admission.update(
        {
            "schema": admission.get("schema", LIVE_PROVIDER_429_SOAK_ADMISSION_SCHEMA),
            "expected_capture_path": admission.get("expected_capture_path", LIVE_PROVIDER_429_SOAK_EXPECTED_PATH),
            "capture_present": capture_present,
            "capture_schema": capture_payload.get("schema", "") if capture_present else "",
            "capture_source_classification": capture_payload.get("source_classification", "")
            if capture_present
            else "",
            "capture_valid": capture_valid,
            "capture_hash": capture_hash,
            "capture_error": capture_error,
            "admission_status": admission_status,
            "production_blocker_id": admission.get("production_blocker_id", LIVE_PROVIDER_429_SOAK_BLOCKER_ID),
        }
    )
    admission["capture_verification_hash"] = _hash_payload_array(
        {
            "capture_hash": capture_hash,
            "capture_valid": capture_valid,
            "capture_status": admission_status,
            "capture_contract": admission.get("capture_contract", ""),
            "provider_set": admission.get("provider_set", ""),
            "required_sample_count": admission.get("required_sample_count", 0),
            "latency_gate_ns": admission.get("latency_gate_ns", 0),
        }
    )
    return admission


def write_live_provider_429_soak_capture(
    root: str | Path = ROOT,
    requester: Any | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    artifact = _read_json(root_path / "artifacts" / "dynamic_provider_fallback_report.json")
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    admission = report.get("live_provider_429_soak_admission", {})
    samples = _live_provider_429_http_samples(report, admission, requester=requester)
    capture = _live_provider_429_capture(report, admission, samples)
    capture_valid = _live_provider_429_capture_valid(capture, report, admission)
    capture_path = root_path / LIVE_PROVIDER_429_SOAK_EXPECTED_PATH
    encoded_capture = json.dumps(capture, indent=2, sort_keys=True).encode("utf-8")
    if capture_valid:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(encoded_capture)
    return {
        "suite_name": "AEGIS Live Provider 429 Soak Capture Producer",
        "schema": "aegis-live-provider-429-soak-capture-producer-v1",
        "truth_claim": False,
        "capture_path": str(capture_path),
        "capture_written": capture_valid,
        "capture_schema": capture.get("schema", ""),
        "capture_hash": hashlib.sha256(encoded_capture).hexdigest() if capture_valid else "0" * 64,
        "soak_url_present": samples.get("soak_url_present") is True,
        "endpoint_classification": samples.get("endpoint_classification", ""),
        "source_classification": samples.get("source_classification", ""),
        "sample_count": capture.get("sample_count", 0),
        "all_status_429": capture.get("all_status_429") is True,
        "overall_ok": capture_valid,
        "error": _live_provider_429_capture_error(capture_valid, samples),
    }


def _live_provider_429_http_samples(
    report: dict[str, Any],
    admission: dict[str, Any],
    requester: Any | None = None,
) -> dict[str, Any]:
    url = os.environ.get("AEGIS_LIVE_PROVIDER_429_SOAK_URL", "").strip()
    method = os.environ.get("AEGIS_LIVE_PROVIDER_429_SOAK_METHOD", "GET").strip().upper() or "GET"
    body_text = os.environ.get("AEGIS_LIVE_PROVIDER_429_SOAK_BODY", "")
    timeout_seconds = int(os.environ.get("AEGIS_LIVE_PROVIDER_429_SOAK_TIMEOUT_SECONDS", "10"))
    body = body_text.encode("utf-8") if body_text else None
    headers, headers_error = _provider_headers_from_env()
    endpoint_classification = _live_provider_429_endpoint_classification(url) if url else "missing_endpoint"
    transport = "injected_test_double" if requester is not None else "urllib"
    source_classification = "synthetic_injected_transport" if requester is not None else endpoint_classification
    required_samples = report.get("sample_count", admission.get("required_sample_count", 0))
    if not isinstance(required_samples, int) or isinstance(required_samples, bool) or required_samples <= 0:
        required_samples = 0
    if not url:
        return {
            "schema": "aegis-live-provider-429-http-samples-v2",
            "soak_url_present": False,
            "endpoint_classification": endpoint_classification,
            "source_classification": source_classification,
            "transport": transport,
            "requested_sample_count": required_samples,
            "samples": [],
            "error": "AEGIS_LIVE_PROVIDER_429_SOAK_URL unset",
        }
    if headers_error:
        return {
            "schema": "aegis-live-provider-429-http-samples-v2",
            "soak_url_present": True,
            "endpoint_classification": endpoint_classification,
            "source_classification": source_classification,
            "transport": transport,
            "requested_sample_count": required_samples,
            "samples": [],
            "error": headers_error,
        }
    if requester is None and endpoint_classification != "external_https_endpoint_candidate":
        return {
            "schema": "aegis-live-provider-429-http-samples-v2",
            "soak_url_present": True,
            "endpoint_classification": endpoint_classification,
            "source_classification": source_classification,
            "transport": transport,
            "requested_sample_count": required_samples,
            "samples": [],
            "error": f"live 429 endpoint rejected by source policy: {endpoint_classification}",
        }
    opener = None
    if requester is None:
        opener, transport_error = _build_live_provider_429_opener(url)
        if opener is None:
            return {
                "schema": "aegis-live-provider-429-http-samples-v2",
                "soak_url_present": True,
                "endpoint_classification": endpoint_classification,
                "source_classification": source_classification,
                "transport": transport,
                "requested_sample_count": required_samples,
                "samples": [],
                "error": transport_error,
            }
    sample_rows: list[dict[str, Any]] = []
    for index in range(required_samples):
        request = Request(url, data=body, headers=headers, method=method)
        started_ns = time.perf_counter_ns()
        body_bytes = b""
        status = 0
        response_headers: dict[str, str] = {}
        error = ""
        try:
            if requester is None:
                # This URL is an explicit operator-provided live-soak target;
                # the gate records the endpoint and never treats it as trusted code.
                with opener.open(request, timeout=timeout_seconds) as response:
                    body_bytes = response.read(1024 * 1024)
                    status = int(response.status)
                    response_headers = dict(response.headers.items())
            else:
                response = requester(request, timeout_seconds, index)
                body_bytes = bytes(response.get("body", b""))
                status = int(response.get("status", 0))
                response_headers = dict(response.get("headers", {}))
        except HTTPError as exc:
            body_bytes = exc.read(1024 * 1024)
            status = int(exc.code)
            response_headers = dict(exc.headers.items()) if exc.headers else {}
        except (OSError, TimeoutError, URLError) as exc:
            error = type(exc).__name__
        elapsed_ns = time.perf_counter_ns() - started_ns
        sample_rows.append(
            {
                "index": index,
                "status": status,
                "elapsed_ns": elapsed_ns,
                "body_len": len(body_bytes),
                "body_hash": hashlib.sha256(body_bytes).hexdigest() if body_bytes else "",
                "response_header_names": sorted(str(name).lower() for name in response_headers),
                "response_headers_hash": _stable_hash(_redacted_headers(response_headers)),
                "error": error,
            }
        )
    return {
        "schema": "aegis-live-provider-429-http-samples-v2",
        "soak_url_present": True,
        "endpoint_classification": endpoint_classification,
        "source_classification": source_classification,
        "transport": transport,
        "requested_sample_count": required_samples,
        "request_method": method,
        "request_url_hash": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "request_body_len": len(body or b""),
        "request_body_hash": hashlib.sha256(body or b"").hexdigest(),
        "request_header_names": sorted(name.lower() for name in headers),
        "request_headers_hash": _stable_hash(_redacted_headers(headers)),
        "samples": sample_rows,
        "error": "",
    }


def _build_live_provider_429_opener(url: str) -> tuple[Any | None, str]:
    proxy_error = _live_provider_429_proxy_error(url)
    if proxy_error:
        return None, proxy_error
    pinned_address, address_error = _live_provider_429_resolve_address(url)
    if address_error or pinned_address is None:
        return None, address_error or "live 429 endpoint address could not be verified"
    opener = build_opener(
        ProxyHandler({}),
        _NoRedirectHandler(),
        _PinnedProviderHTTPSHandler(pinned_address),
    )
    return opener, ""


def _live_provider_429_proxy_error(url: str) -> str:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if not scheme or not host:
            return "live 429 endpoint proxy policy could not be verified"
        proxies = getproxies()
        configured_proxy = proxies.get(scheme) or proxies.get("all")
        if configured_proxy and not proxy_bypass(host):
            return "live 429 endpoint rejected because configured proxy routing is unsupported"
    except OSError, ValueError, UnicodeError:
        return "live 429 endpoint proxy policy could not be verified"
    return ""


def _live_provider_429_resolve_address(url: str) -> tuple[str | None, str]:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or 443
    except ValueError:
        return None, "live 429 endpoint address could not be verified"
    if parsed.scheme.lower() != "https" or not host:
        return None, "live 429 endpoint address requires a valid HTTPS URL"
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError, UnicodeError, ValueError:
        return None, "live 429 endpoint DNS resolution failed"
    addresses: list[str] = []
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0])
        except IndexError, TypeError, ValueError:
            return None, "live 429 endpoint DNS answer could not be verified"
        if not address.is_global:
            return None, "live 429 endpoint resolves to a non-public address"
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        return None, "live 429 endpoint DNS resolution returned no addresses"
    return addresses[0], ""


def _live_provider_429_endpoint_classification(url: str) -> str:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if parsed.port == 0:
            return "invalid_endpoint"
    except ValueError:
        return "invalid_endpoint"
    if scheme not in {"http", "https"} or not host:
        return "invalid_endpoint"
    if parsed.username is not None or parsed.password is not None:
        return "credentials_in_url"
    normalized_host = host.rstrip(".").lower()
    reserved_hosts = {"localhost", "test", "example", "invalid", "local"}
    reserved_suffixes = (".localhost", ".local", ".test", ".invalid", ".example")
    reserved_examples = (
        "example.com",
        "example.net",
        "example.org",
    )
    if (
        normalized_host in reserved_hosts
        or normalized_host.endswith(reserved_suffixes)
        or normalized_host in reserved_examples
        or normalized_host.endswith(tuple(f".{domain}" for domain in reserved_examples))
    ):
        return "local_or_reserved_endpoint"
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return "local_or_reserved_endpoint"
    if scheme != "https":
        return "insecure_transport"
    return "external_https_endpoint_candidate"


def _provider_headers_from_env() -> tuple[dict[str, str], str]:
    raw_headers = os.environ.get("AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON", "").strip()
    if not raw_headers:
        return {}, ""
    try:
        decoded = json.loads(raw_headers)
    except json.JSONDecodeError:
        return {}, "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON invalid-json"
    if not isinstance(decoded, dict):
        return {}, "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON non-object"
    headers: dict[str, str] = {}
    for key, value in decoded.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return {}, "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON requires string keys and values"
        headers[key] = value
    return headers, ""


def _live_provider_429_capture(
    report: dict[str, Any],
    admission: dict[str, Any],
    http_samples: dict[str, Any],
) -> dict[str, Any]:
    rows = http_samples.get("samples", [])
    status_counts: dict[str, int] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                status = str(row.get("status", 0))
                status_counts[status] = status_counts.get(status, 0) + 1
    sample_count = len(rows) if isinstance(rows, list) else 0
    all_status_429 = (
        sample_count == report.get("sample_count")
        and sample_count > 0
        and all(isinstance(row, dict) and row.get("status") == 429 and not row.get("error") for row in rows)
    )
    dynamic_fallback_evidence = {
        "feedback_kind": report.get("feedback_kind"),
        "rate_limited_provider": report.get("rate_limited_provider"),
        "rate_limited_model": report.get("rate_limited_model"),
        "selected_provider": report.get("selected_provider"),
        "selected_model": report.get("selected_model"),
        "fallback_used": report.get("fallback_used"),
        "downgraded_model": report.get("downgraded_model"),
        "latency_max_ns": report.get("latency_max_ns"),
        "latency_gate_ns": report.get("latency_gate_ns"),
        "latency_under_gate": report.get("latency_under_gate"),
    }
    return {
        "schema": LIVE_PROVIDER_429_SOAK_CAPTURE_SCHEMA,
        "truth_claim": False,
        "verifier": "external-http-429-redacted-capture-producer",
        "endpoint_classification": http_samples.get("endpoint_classification", "unclassified"),
        "source_classification": http_samples.get("source_classification", "unclassified"),
        "transport": http_samples.get("transport", "unclassified"),
        "production_blocker_id": LIVE_PROVIDER_429_SOAK_BLOCKER_ID,
        "capture_contract": admission.get("capture_contract", ""),
        "capture_contract_hash": admission.get("capture_contract_hash", []),
        "provider_set": admission.get("provider_set", ""),
        "provider_set_hash": admission.get("provider_set_hash", []),
        "primary_provider": admission.get("primary_provider", ""),
        "primary_model": admission.get("primary_model", ""),
        "fallback_provider": admission.get("fallback_provider", ""),
        "fallback_model": admission.get("fallback_model", ""),
        "expected_feedback_kind": admission.get("expected_feedback_kind", ""),
        "required_sample_count": report.get("sample_count", 0),
        "latency_gate_ns": report.get("latency_gate_ns", 0),
        "dynamic_fallback_evidence": dynamic_fallback_evidence,
        "dynamic_fallback_evidence_hash": _stable_hash(dynamic_fallback_evidence),
        "http_samples": http_samples,
        "sample_count": sample_count,
        "status_counts": status_counts,
        "all_status_429": all_status_429,
        "request_redacted": True,
        "response_bodies_redacted": True,
    }


def _live_provider_429_capture_valid(
    capture: dict[str, Any],
    report: dict[str, Any],
    admission: dict[str, Any],
) -> bool:
    http_samples = capture.get("http_samples", {})
    dynamic_evidence = capture.get("dynamic_fallback_evidence", {})
    if not isinstance(http_samples, dict) or not isinstance(dynamic_evidence, dict):
        return False
    rows = http_samples.get("samples", [])
    return (
        capture.get("schema") == LIVE_PROVIDER_429_SOAK_CAPTURE_SCHEMA
        and capture.get("truth_claim") is False
        and capture.get("endpoint_classification") == "external_https_endpoint_candidate"
        and capture.get("source_classification") == "external_https_endpoint_candidate"
        and capture.get("transport") == "urllib"
        and capture.get("production_blocker_id") == LIVE_PROVIDER_429_SOAK_BLOCKER_ID
        and capture.get("capture_contract") == admission.get("capture_contract")
        and capture.get("capture_contract_hash") == admission.get("capture_contract_hash")
        and capture.get("provider_set") == admission.get("provider_set")
        and capture.get("provider_set_hash") == admission.get("provider_set_hash")
        and capture.get("primary_provider") == admission.get("primary_provider") == report.get("rate_limited_provider")
        and capture.get("primary_model") == admission.get("primary_model") == report.get("rate_limited_model")
        and capture.get("fallback_provider") == admission.get("fallback_provider") == report.get("selected_provider")
        and capture.get("fallback_model") == admission.get("fallback_model") == report.get("selected_model")
        and capture.get("expected_feedback_kind")
        == admission.get("expected_feedback_kind")
        == report.get("feedback_kind")
        and capture.get("required_sample_count") == report.get("sample_count") == admission.get("required_sample_count")
        and capture.get("latency_gate_ns") == report.get("latency_gate_ns") == admission.get("latency_gate_ns")
        and capture.get("dynamic_fallback_evidence_hash") == _stable_hash(dynamic_evidence)
        and dynamic_evidence.get("fallback_used") is True
        and dynamic_evidence.get("downgraded_model") is True
        and dynamic_evidence.get("latency_under_gate") is True
        and isinstance(dynamic_evidence.get("latency_max_ns"), int)
        and dynamic_evidence.get("latency_max_ns") < dynamic_evidence.get("latency_gate_ns")
        and isinstance(http_samples, dict)
        and http_samples.get("schema") == "aegis-live-provider-429-http-samples-v2"
        and http_samples.get("soak_url_present") is True
        and http_samples.get("endpoint_classification") == "external_https_endpoint_candidate"
        and http_samples.get("source_classification") == "external_https_endpoint_candidate"
        and http_samples.get("transport") == "urllib"
        and _nonzero_hex(str(http_samples.get("request_url_hash", "")))
        and capture.get("request_redacted") is True
        and capture.get("response_bodies_redacted") is True
        and capture.get("sample_count") == report.get("sample_count")
        and capture.get("all_status_429") is True
        and isinstance(rows, list)
        and len(rows) == report.get("sample_count")
        and all(
            isinstance(row, dict)
            and row.get("status") == 429
            and _positive_int(row.get("elapsed_ns"))
            and isinstance(row.get("body_hash"), str)
            and not row.get("error")
            for row in rows
        )
    )


def _live_provider_429_capture_error(capture_valid: bool, samples: dict[str, Any]) -> str:
    if capture_valid:
        return ""
    if samples.get("source_classification") == "synthetic_injected_transport":
        return "synthetic injected responses cannot satisfy the live 429 capture"
    return str(samples.get("error") or "live provider 429 soak did not satisfy capture contract")


def _read_capture(path: Path) -> tuple[dict[str, Any], bool, bytes, str]:
    try:
        capture_bytes = path.read_bytes()
    except OSError:
        return {}, False, b"", "missing"
    if not capture_bytes:
        return {}, True, capture_bytes, "empty"
    try:
        payload = json.loads(capture_bytes.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return {}, True, capture_bytes, "invalid-json"
    if not isinstance(payload, dict):
        return {}, True, capture_bytes, "non-object"
    return payload, True, capture_bytes, ""


def _admission_state_bound(admission: dict[str, Any]) -> bool:
    missing_capture = (
        admission.get("capture_present") is False
        and admission.get("capture_hash") == [0] * 32
        and admission.get("admission_status") == "missing"
    )
    candidate_capture = (
        admission.get("capture_present") is True
        and _hash_array(admission.get("capture_hash"))
        and admission.get("admission_status") == "candidate-present-live-soak-gate-required"
    )
    return missing_capture or candidate_capture


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = {"authorization", "cookie", "x-api-key", "api-key", "proxy-authorization"}
    return {
        str(key).lower(): (
            "<redacted>" if str(key).lower() in sensitive else hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        )
        for key, value in sorted(headers.items())
    }


def _hash_array(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(item, int) and 0 <= item <= 255 for item in value)
        and any(item != 0 for item in value)
    )


def _hash_bytes_array(value: bytes) -> list[int]:
    return list(hashlib.sha256(value).digest())


def _hash_payload_array(value: Any) -> list[int]:
    return list(bytes.fromhex(_stable_hash(value)))


def _nonzero_hex(value: str) -> bool:
    return (
        len(value) == 64
        and all(char in "0123456789abcdef" for char in value.lower())
        and any(char != "0" for char in value)
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
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


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--write-capture"]:
        producer_report = write_live_provider_429_soak_capture(ROOT)
        print(json.dumps(producer_report, indent=2, sort_keys=True))
        return 0 if producer_report["overall_ok"] else 1
    if argv:
        print(json.dumps({"error": f"unknown args: {argv}"}, indent=2, sort_keys=True))
        return 2
    report = evaluate_dynamic_provider_fallback_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
