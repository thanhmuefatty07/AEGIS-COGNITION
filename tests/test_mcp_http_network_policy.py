import socket

import pytest

from aegis_cognition.extensions import ExtensionError, _resolve_mcp_host


def test_mcp_http_rejects_shared_non_global_dns_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve_shared_address(_host: str, _port: int, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve_shared_address)

    with pytest.raises(ExtensionError):
        _resolve_mcp_host("mcp.example", 443, allow_local=False)
