import json

import pytest

from core.python.aegis.desktop_protocol import (
    DesktopCommandRouter,
    DesktopProtocolError,
    decode_request,
)


def _request(command: str, payload: dict | None = None) -> bytes:
    return json.dumps(
        {
            "protocol_version": 1,
            "request_id": "req-1",
            "command": command,
            "payload": payload or {},
        }
    ).encode()


def test_desktop_protocol_accepts_allowlisted_command_and_routes_typed_payload():
    observed: list[dict] = []
    router = DesktopCommandRouter(
        {"workspace.snapshot": lambda payload: observed.append(payload) or {"revision": 3}}
    )

    response = json.loads(router.dispatch(_request("workspace.snapshot", {"workspace_id": "local"})))

    assert response["status"] == "ok"
    assert response["result"] == {"revision": 3}
    assert observed == [{"workspace_id": "local"}]


def test_desktop_protocol_rejects_arbitrary_code_and_duplicate_keys():
    with pytest.raises(DesktopProtocolError, match="not allowed"):
        decode_request(_request("execute.code"))
    with pytest.raises(DesktopProtocolError, match="duplicate"):
        decode_request(
            b'{"protocol_version":1,"request_id":"req-1","request_id":"req-2",'
            b'"command":"workspace.snapshot","payload":{}}'
        )


def test_desktop_protocol_hides_handler_details_and_bounds_frames():
    router = DesktopCommandRouter({"workspace.snapshot": lambda _payload: 7})
    response = json.loads(router.dispatch(_request("workspace.snapshot")))
    assert response["status"] == "error"
    assert response["error"] == {
        "code": "INVALID_HANDLER_RESULT",
        "message": "desktop handler returned a non-object",
    }

    with pytest.raises(DesktopProtocolError, match="exceeds"):
        decode_request(_request("workspace.snapshot"), max_frame_bytes=8)
