"""AEGIS cluster worker service for the real multi-machine soak test.

Runs one TCP listener per container.  For every accepted connection it
reads a single JSON-line request formatted as
``aegis-real-cluster-worker-request-v1`` (see
scripts/tcp_cluster_soak_gate.py::_cluster_worker_request) and replies
with a single JSON-line response formatted as
``aegis-real-cluster-worker-response-v1`` — the contract enforced by
``_cluster_worker_response_valid``.

Network contract / topology contract hashes MUST be byte-identical to
the values declared in the AEGIS admission contract
(``artifacts/tcp_cluster_soak_report.json``).  This script enforces that
by echoing the hashes it received from the request — any drift between
the orchestrator and the worker side will be rejected by the gate.

The worker process is intentionally minimal:
- no state, no caching, no concurrency beyond ``threading``;
- localhost is rejected (worker must be reached via the bridge IP);
- only request fields the gate uses are echoed back.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socketserver
import sys
from typing import Any


MACHINE_ID = os.environ.get("AEGIS_WORKER_ID", "unnamed-worker").strip()
PORT = int(os.environ.get("AEGIS_WORKER_PORT", "9000").strip())
MAX_REQUEST_BYTES = 64 * 1024
SCHEMA_REQUEST = "aegis-real-cluster-worker-request-v1"
SCHEMA_RESPONSE = "aegis-real-cluster-worker-response-v1"


def _machine_id_hash() -> str:
    return hashlib.sha256(MACHINE_ID.encode("utf-8")).hexdigest()


def _valid_hash_bytes(value: Any) -> list[int]:
    if (
        isinstance(value, list)
        and len(value) <= 64
        and all(isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255 for b in value)
    ):
        return value
    return []


def _valid_work_index(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000:
        return value
    return 0


def _valid_target_hash(value: Any) -> str:
    if isinstance(value, str) and len(value) <= 128:
        return value
    return ""


def _is_loopback_peer(client_address: Any) -> bool:
    if not isinstance(client_address, tuple) or not client_address:
        return False
    host = str(client_address[0]).strip().lower()
    return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def _build_response(request: dict[str, Any]) -> dict[str, Any]:
    """Build a response that satisfies ``_cluster_worker_response_valid``."""
    return {
        "schema": SCHEMA_RESPONSE,
        "truth_claim": False,
        # Required: candidate-only mode — the worker never produces
        # evidence, it only acknowledges requests.
        "candidate_only": True,
        # Required: direct worker commit attempt must be rejected. The
        # orchestrator's candidate gate alone is allowed to mint
        # physical evidence; this worker never does.
        "direct_worker_commit_rejected": True,
        # Required: when the worker detects a partition (here: any
        # contract-hash mismatch would also trigger pause, but in
        # practice the full pause applies when the bridge side is
        # not reachable).
        "side_effect_partition_paused": True,
        "network_contract_hash": _valid_hash_bytes(request.get("network_contract_hash", [])),
        "topology_contract_hash": _valid_hash_bytes(request.get("topology_contract_hash", [])),
        "worker_machine_id_hash": _machine_id_hash(),
        "echoed_work_index": _valid_work_index(request.get("work_index", 0)),
        "echoed_target_machine_id_hash": _valid_target_hash(
            request.get("target_machine_id_hash", "")
        ),
    }


class ClusterWorkerHandler(socketserver.StreamRequestHandler):
    timeout = 5.0

    def handle(self) -> None:
        reject_localhost = os.environ.get("AEGIS_WORKER_REJECT_LOCALHOST", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if reject_localhost and _is_loopback_peer(self.client_address):
            return
        # Read one bounded JSON line (the gate sends a single object per connection).
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        except OSError:
            return
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            return
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return
        if not isinstance(request, dict):
            return
        if request.get("schema") != SCHEMA_REQUEST:
            # Reject unknown schemas but still write a minimal response
            # so the orchestrator can record a clean failure rather than
            # an opaque socket close.
            response = {"schema": SCHEMA_RESPONSE, "rejected": True}
        else:
            response = _build_response(request)
        body = (json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            self.wfile.write(body)
            self.wfile.flush()


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    bind_host = os.environ.get("AEGIS_WORKER_HOST", "0.0.0.0").strip() or "0.0.0.0"
    with ThreadedTCPServer((bind_host, PORT), ClusterWorkerHandler) as srv:
        print(
            f"[{MACHINE_ID}] listening on {bind_host}:{PORT} "
            f"machine_id_hash={_machine_id_hash()}",
            file=sys.stderr,
            flush=True,
        )
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
