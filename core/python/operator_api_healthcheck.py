from __future__ import annotations

from contextlib import suppress
from http.client import HTTPConnection, HTTPException
import json
import socket
import sys
import threading
import time

from .operator_api import HEALTH_SCHEMA


HEALTHCHECK_HOST = "127.0.0.1"
HEALTHCHECK_PORT = 8765
HEALTHCHECK_PATH = "/health"
HEALTHCHECK_TIMEOUT_SECONDS = 2.0
MAX_HEALTH_RESPONSE_BYTES = 64 * 1024


def _listener_is_healthy() -> bool:
    deadline = time.monotonic() + HEALTHCHECK_TIMEOUT_SECONDS
    connection = HTTPConnection(
        HEALTHCHECK_HOST,
        HEALTHCHECK_PORT,
        timeout=HEALTHCHECK_TIMEOUT_SECONDS,
    )
    deadline_timer: threading.Timer | None = None
    try:
        connection.connect()
        probe_socket = connection.sock
        if probe_socket is None:
            return False

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        probe_socket.settimeout(remaining)

        def interrupt_probe() -> None:
            with suppress(OSError):
                probe_socket.shutdown(socket.SHUT_RDWR)

        deadline_timer = threading.Timer(remaining, interrupt_probe)
        deadline_timer.daemon = True
        deadline_timer.start()
        connection.request(
            "GET",
            HEALTHCHECK_PATH,
            headers={"Accept": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
        if response.status != 200:
            return False
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return False
        body = response.read(MAX_HEALTH_RESPONSE_BYTES + 1)
        if len(body) > MAX_HEALTH_RESPONSE_BYTES:
            return False
        try:
            payload = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError, ValueError, RecursionError:
            return False
        response_is_healthy = (
            isinstance(payload, dict) and payload.get("schema") == HEALTH_SCHEMA and payload.get("truth_claim") is False
        )
        return response_is_healthy and time.monotonic() < deadline
    except HTTPException, OSError:
        return False
    finally:
        if deadline_timer is not None:
            deadline_timer.cancel()
        connection.close()


def main() -> int:
    if _listener_is_healthy():
        print("operator API healthy")
        return 0
    print("operator API unhealthy", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
