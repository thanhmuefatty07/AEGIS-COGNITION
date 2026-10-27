from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from core.python import operator_api_healthcheck as healthcheck
from core.python.operator_api import serve_operator_evidence_api


_HEALTH_PAYLOAD = {
    "schema": "aegis-operator-health-v1",
    "truth_claim": False,
    "verifier": "rust-replay-and-artifact-gates",
    "python_role": "read-only-operator-summary",
    "release_gate_observed_ok": True,
    "missing_artifact_count": 0,
    "artifact_count": 13,
    "snapshot_digest": {"algorithm": "sha256", "hex": "a5" * 32},
}


class _LoopbackHttpServer(ThreadingHTTPServer):
    daemon_threads = True


@contextmanager
def _serve_health_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/json",
    hold_response: threading.Event | None = None,
    chunk_size: int | None = None,
    chunk_delay: float = 0.0,
) -> Iterator[tuple[_LoopbackHttpServer, threading.Thread, list[str], threading.Event]]:
    requests: list[str] = []
    request_seen = threading.Event()
    stop_sending = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            requests.append(self.path)
            request_seen.set()
            if hold_response is not None:
                hold_response.wait()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            size = chunk_size or max(len(body), 1)
            for offset in range(0, len(body), size):
                if stop_sending.is_set():
                    return
                try:
                    self.wfile.write(body[offset : offset + size])
                    self.wfile.flush()
                except OSError:
                    return
                if chunk_delay and stop_sending.wait(chunk_delay):
                    return

        def log_message(self, format: str, *args: object) -> None:
            return

    server = _LoopbackHttpServer(("127.0.0.1", 0), Handler)
    serving_thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    serving_thread.start()
    try:
        yield server, serving_thread, requests, request_seen
    finally:
        stop_sending.set()
        if hold_response is not None:
            hold_response.set()
        if serving_thread.is_alive():
            server.shutdown()
        server.server_close()
        serving_thread.join(timeout=1)


def _configure_probe(monkeypatch: pytest.MonkeyPatch, server: _LoopbackHttpServer, timeout: float = 1.0) -> None:
    host, port = server.server_address[:2]
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_HOST", host, raising=False)
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_PORT", port, raising=False)
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_TIMEOUT_SECONDS", timeout, raising=False)


def test_healthcheck_accepts_the_serving_operator_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    server = serve_operator_evidence_api(tmp_path, "127.0.0.1", 0)
    host, port = server.server_address[:2]
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_HOST", host, raising=False)
    monkeypatch.setattr(healthcheck, "HEALTHCHECK_PORT", port, raising=False)
    serving_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    serving_thread.start()
    try:
        assert healthcheck.main() == 0
        captured = capfd.readouterr()
        assert captured.out.strip() == "operator API healthy"
        assert "snapshot_digest" not in captured.out + captured.err
    finally:
        if serving_thread.is_alive():
            server.shutdown()
        server.server_close()
        serving_thread.join(timeout=1)


def test_healthcheck_probes_live_listener_without_printing_health_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    body = json.dumps(_HEALTH_PAYLOAD).encode("utf-8")
    with _serve_health_response(body) as (server, _, requests, _):
        _configure_probe(monkeypatch, server)

        assert healthcheck.main() == 0

        captured = capfd.readouterr()
        assert requests == ["/health"]
        assert "snapshot_digest" not in captured.out + captured.err
        assert _HEALTH_PAYLOAD["snapshot_digest"]["hex"] not in captured.out + captured.err


def test_healthcheck_fails_when_listener_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(_HEALTH_PAYLOAD).encode("utf-8")
    with _serve_health_response(body) as (server, serving_thread, requests, _):
        host, port = server.server_address[:2]
        server.shutdown()
        server.server_close()
        serving_thread.join(timeout=1)
        monkeypatch.setattr(healthcheck, "HEALTHCHECK_HOST", host, raising=False)
        monkeypatch.setattr(healthcheck, "HEALTHCHECK_PORT", port, raising=False)

        assert healthcheck.main() == 1
        assert requests == []


def test_healthcheck_times_out_when_listener_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(_HEALTH_PAYLOAD).encode("utf-8")
    hold_response = threading.Event()
    result: list[int] = []
    with _serve_health_response(body, hold_response=hold_response) as (
        server,
        _,
        requests,
        request_seen,
    ):
        _configure_probe(monkeypatch, server, timeout=0.1)
        probe_thread = threading.Thread(target=lambda: result.append(healthcheck.main()), daemon=True)
        started = time.monotonic()
        probe_thread.start()

        assert request_seen.wait(timeout=0.5), "healthcheck did not contact the listener"
        probe_thread.join(timeout=1.0)

        assert not probe_thread.is_alive(), "healthcheck did not honor its finite timeout"
        assert result == [1]
        assert requests == ["/health"]
        assert time.monotonic() - started < 1.0


def test_healthcheck_enforces_total_deadline_for_slow_valid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(_HEALTH_PAYLOAD).encode("utf-8")
    result: list[int] = []

    with _serve_health_response(body, chunk_size=16, chunk_delay=0.01) as (
        server,
        _,
        requests,
        request_seen,
    ):
        _configure_probe(monkeypatch, server, timeout=0.1)
        probe_thread = threading.Thread(target=lambda: result.append(healthcheck.main()), daemon=True)
        probe_thread.start()

        assert request_seen.wait(timeout=0.5), "healthcheck did not contact the listener"
        probe_thread.join(timeout=1.0)

        assert not probe_thread.is_alive(), "healthcheck exceeded its total deadline"
        assert result == [1], "a slow response must become unhealthy when the total deadline expires"
        assert requests == ["/health"]


def test_healthcheck_rejects_response_processed_after_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.dumps(_HEALTH_PAYLOAD).encode("utf-8")
    original_loads = healthcheck.json.loads
    deadline_seconds = 1.0
    monotonic_now = [0.0]
    parser_entered = threading.Event()

    def slow_json_loads(data: str | bytes, *args: object, **kwargs: object) -> object:
        parser_entered.set()
        monotonic_now[0] += deadline_seconds * 2
        return original_loads(data, *args, **kwargs)

    monkeypatch.setattr(healthcheck, "time", SimpleNamespace(monotonic=lambda: monotonic_now[0]))
    monkeypatch.setattr(healthcheck.json, "loads", slow_json_loads)
    with _serve_health_response(body) as (server, _, requests, _):
        _configure_probe(monkeypatch, server, timeout=deadline_seconds)

        assert healthcheck.main() == 1
        assert parser_entered.is_set(), "deadline should expire while processing the valid response"
        assert monotonic_now[0] >= deadline_seconds
        assert requests == ["/health"]


@pytest.mark.parametrize(
    ("status", "content_type", "body"),
    [
        (503, "application/json", json.dumps(_HEALTH_PAYLOAD).encode("utf-8")),
        (200, "text/plain", json.dumps(_HEALTH_PAYLOAD).encode("utf-8")),
        (200, "application/json", b"not-json"),
        (200, "application/json", b"[]"),
        (
            200,
            "application/json",
            json.dumps({**_HEALTH_PAYLOAD, "schema": "wrong-schema"}).encode("utf-8"),
        ),
        (
            200,
            "application/json",
            json.dumps({**_HEALTH_PAYLOAD, "truth_claim": True}).encode("utf-8"),
        ),
        (
            200,
            "application/json",
            json.dumps({**_HEALTH_PAYLOAD, "truth_claim": 0}).encode("utf-8"),
        ),
        (200, "application/json", json.dumps(_HEALTH_PAYLOAD).encode("utf-8") + b" " * (64 * 1024)),
    ],
    ids=(
        "http-error",
        "wrong-content-type",
        "invalid-json",
        "non-object-json",
        "wrong-schema",
        "truth-claim-true",
        "truth-claim-not-bool",
        "oversized-body",
    ),
)
def test_healthcheck_rejects_noncontract_listener_responses(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    status: int,
    content_type: str,
    body: bytes,
) -> None:
    with _serve_health_response(body, status=status, content_type=content_type) as (
        server,
        _,
        requests,
        _,
    ):
        _configure_probe(monkeypatch, server)

        assert healthcheck.main() == 1
        assert requests == ["/health"]
        captured = capfd.readouterr()
        assert "snapshot_digest" not in captured.out + captured.err
        assert _HEALTH_PAYLOAD["snapshot_digest"]["hex"] not in captured.out + captured.err
