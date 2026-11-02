"""Regression tests for Cloudflare security-audit-skill remediations (Phase P3)."""

from __future__ import annotations

import asyncio
import json
import math
import socket
import struct
import threading
from pathlib import Path
from typing import Any

import pytest

from aegis_cognition.code_reuse import CodeReuseError, _inside
from aegis_cognition.config import render_toml, save_config
from aegis_cognition.extensions import ExtensionError, _mcp_skill_frontmatter_mapping, _skill_frontmatter
from aegis_cognition.research_adapters import (
    RedditRssQueryProvider,
    ResearchAdapterError,
    XAppOnlyQueryProvider,
)
from aegis_cognition.verification.execution import (
    LocalVerificationCommand,
    _parse_pytest_junit_report,
    _verification_environment,
)
from cluster.worker_service import (
    MAX_REQUEST_BYTES,
    SCHEMA_REQUEST,
    SCHEMA_RESPONSE,
    ClusterWorkerHandler,
    ThreadedTCPServer,
    _build_response,
)
from core.python.bridge_mmap import (
    MMAP_BRIDGE_HEADER_BYTES,
    MMAP_BRIDGE_MAGIC,
    MMAP_BRIDGE_PAYLOAD_ALIGNMENT,
    MMAP_BRIDGE_VERSION,
    MmapBridgeFrame,
    MmapBridgeHeader,
)
from core.python.operator_api import (
    MAX_OPERATOR_ARTIFACT_BYTES,
    _count_hash_fields,
    _is_allowed_operator_host,
    build_operator_evidence_snapshot,
    serve_operator_evidence_api,
)
from scripts.supply_chain_gate import evaluate_supply_chain_gate

ROOT = Path(__file__).resolve().parents[1]


def test_cluster_worker_bounded_readline_and_recursion_guard() -> None:
    """AEGIS-NET-001 & AEGIS-NET-002: bounded readline, recursion guard, and field validation."""
    assert ClusterWorkerHandler.timeout == 5.0
    sanitized = _build_response(
        {
            "schema": SCHEMA_REQUEST,
            "network_contract_hash": ["not-an-int"],
            "topology_contract_hash": [1, 2, 3],
            "work_index": "bad-index",
            "target_machine_id_hash": "x" * 300,
        }
    )
    assert sanitized["network_contract_hash"] == []
    assert sanitized["topology_contract_hash"] == [1, 2, 3]
    assert sanitized["echoed_work_index"] == 0
    assert sanitized["echoed_target_machine_id_hash"] == ""

    with ThreadedTCPServer(("127.0.0.1", 0), ClusterWorkerHandler) as srv:
        host, port = srv.server_address
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            # 1. Valid request succeeds
            with socket.create_connection((host, port), timeout=3.0) as sock:
                req = json.dumps({"schema": SCHEMA_REQUEST, "work_index": 7}) + "\n"
                sock.sendall(req.encode("utf-8"))
                reply = json.loads(sock.makefile("r", encoding="utf-8").readline())
                assert reply["schema"] == SCHEMA_RESPONSE
                assert reply["echoed_work_index"] == 7

            # 2. Deeply nested JSON does not crash handler with unhandled RecursionError
            with socket.create_connection((host, port), timeout=3.0) as sock:
                nested = "[" * 1500 + "]" * 1500 + "\n"
                sock.sendall(nested.encode("utf-8"))
                assert sock.recv(1024) == b""

            # 3. Oversized line > MAX_REQUEST_BYTES is rejected cleanly
            with socket.create_connection((host, port), timeout=3.0) as sock:
                oversized = b"a" * (MAX_REQUEST_BYTES + 128) + b"\n"
                sock.sendall(oversized)
                assert sock.recv(1024) == b""
        finally:
            srv.shutdown()
            thread.join(timeout=3.0)


def test_operator_api_host_header_and_symlink_and_recursion_guards(tmp_path: Path) -> None:
    """AEGIS-NET-003, AEGIS-NET-004, AEGIS-NET-005: Host header, symlink, and recursion guards."""
    assert _is_allowed_operator_host("127.0.0.1:8765") is True
    assert _is_allowed_operator_host("localhost:8765") is True
    assert _is_allowed_operator_host("[::1]:8765") is True
    assert _is_allowed_operator_host("aegis-operator-api.aegis-cognition.svc:8765") is True
    assert _is_allowed_operator_host("evil.example:8765") is False
    assert _is_allowed_operator_host("") is False

    # Iterative _count_hash_fields handles 2000-level nesting without RecursionError
    deep_node: Any = {"leaf_hash": "a" * 64}
    for _ in range(2000):
        deep_node = {"child": deep_node}
    total_hashes, nonzero_hashes = _count_hash_fields(deep_node)
    assert total_hashes == 1
    assert nonzero_hashes == 1

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True)
    nested_json_text = '{"a":' * 1500 + "1" + "}" * 1500
    (artifacts_dir / "run_checks_report.json").write_text(nested_json_text, encoding="utf-8")
    (artifacts_dir / "benchmark_gate_report.json").write_text("{invalid-json", encoding="utf-8")

    snapshot = build_operator_evidence_snapshot(
        tmp_path,
        artifact_names=("run_checks_report.json", "benchmark_gate_report.json"),
    )
    deep_summary = snapshot.artifact_summaries[0]
    assert deep_summary.exists is True
    assert deep_summary.json_ok is True
    assert deep_summary.digest is not None

    bad_summary = snapshot.artifact_summaries[1]
    assert bad_summary.exists is True
    assert bad_summary.json_ok is False
    assert bad_summary.digest is not None
    assert bad_summary.error != ""

    with serve_operator_evidence_api(tmp_path, host="127.0.0.1", port=0) as server:
        assert server.daemon_threads is True
        host, port = server.server_address
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                sock.sendall(b"GET /health HTTP/1.1\r\nHost: evil.example:8765\r\nConnection: close\r\n\r\n")
                raw_resp = sock.makefile("rb").read().decode("utf-8", errors="replace")
                assert "403" in raw_resp.splitlines()[0]
                assert "forbidden host header" in raw_resp
        finally:
            server.shutdown()
            thread.join(timeout=3.0)
    assert MAX_OPERATOR_ARTIFACT_BYTES == 16 * 1024 * 1024


def test_mmap_bridge_frame_closes_handles_on_error_and_rejects_zero_hash(tmp_path: Path) -> None:
    """AEGIS-MMAP-002: zero-hash rejection and handle cleanup on invalid header."""
    zero_header = MmapBridgeHeader(
        version=MMAP_BRIDGE_VERSION,
        header_bytes=MMAP_BRIDGE_HEADER_BYTES,
        schema_id=0xAE1515,
        schema_version=1,
        alignment=MMAP_BRIDGE_PAYLOAD_ALIGNMENT,
        message_id=1,
        session_id=1,
        payload_offset=MMAP_BRIDGE_HEADER_BYTES,
        payload_len=16,
        payload_blake3=b"\x00" * 32,
    )
    assert zero_header.is_valid(MMAP_BRIDGE_HEADER_BYTES + 16) is False

    corrupt_path = tmp_path / "corrupt.bridge"
    corrupt_path.write_bytes(b"NOT_AEGIS_MAGIC" + b"\x00" * 128)
    with pytest.raises(ValueError, match="mmap bridge magic mismatch"):
        MmapBridgeFrame(corrupt_path)
    # On Windows, unlinking succeeds only if both _mmap and _file were closed in __init__
    corrupt_path.unlink()
    assert not corrupt_path.exists()

    # Also test valid magic with all-zero blake3 digest
    raw_zero_digest = struct.pack(
        "<8sHHIIIQQQQ32s24s",
        MMAP_BRIDGE_MAGIC,
        MMAP_BRIDGE_VERSION,
        MMAP_BRIDGE_HEADER_BYTES,
        0xAE1515,
        1,
        MMAP_BRIDGE_PAYLOAD_ALIGNMENT,
        1,
        1,
        MMAP_BRIDGE_HEADER_BYTES,
        16,
        b"\x00" * 32,
        b"\x00" * 24,
    )
    zero_hash_file = tmp_path / "zero_hash.bridge"
    zero_hash_file.write_bytes(raw_zero_digest + b"x" * 16)
    with pytest.raises(ValueError, match="invalid mmap bridge frame"):
        MmapBridgeFrame(zero_hash_file)
    zero_hash_file.unlink()


def test_config_key_validation_and_finite_floats(tmp_path: Path) -> None:
    """AEGIS-CFG-001 & AEGIS-CFG-002: TOML key validation, finite float check, and save_config."""
    with pytest.raises(TypeError, match="invalid TOML key"):
        render_toml({"llm": {"bad\n[injected]\nk": "value"}})
    with pytest.raises(TypeError, match="non-finite TOML float"):
        render_toml({"llm": {"temperature": math.nan}})
    with pytest.raises(TypeError, match="non-finite TOML float"):
        render_toml({"llm": {"temperature": math.inf}})

    cfg_file = tmp_path / ".aegis" / "config.toml"
    save_config({"llm": {"provider": "openai", "temperature": 0.25}}, path=cfg_file)
    assert cfg_file.is_file()
    assert 'provider = "openai"' in cfg_file.read_text(encoding="utf-8")


def test_yaml_alias_expansion_cap_in_extensions() -> None:
    """AEGIS-EXT-001: _UniqueKeySafeLoader caps composed YAML nodes at 1,024."""
    alias_bomb = (
        "---\n"
        "name: bad-skill\n"
        "description: test\n"
        "a: &a [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\n"
        "b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
        "c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b, *b]\n"
        "---\n"
        "Body\n"
    )
    assert _skill_frontmatter(alias_bomb.encode("utf-8")) is None
    with pytest.raises(ExtensionError):
        _mcp_skill_frontmatter_mapping(alias_bomb.encode("utf-8"))


def test_code_reuse_rejects_symlink_before_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AEGIS-REUSE-001: _inside checks _is_link_or_junction before resolve()."""
    target = tmp_path / "real.py"
    target.write_text("x = 1\n", encoding="utf-8")
    fake_link = tmp_path / "link.py"
    fake_link.write_text("x = 1\n", encoding="utf-8")

    monkeypatch.setattr(
        "aegis_cognition.code_reuse._is_link_or_junction",
        lambda p: Path(p).name == "link.py",
    )
    with pytest.raises(CodeReuseError, match="symlink source files are not reusable"):
        _inside(tmp_path, fake_link)


def test_xml_utf16_dtd_and_x_recursion_guards(tmp_path: Path) -> None:
    """AEGIS-XML-001: UTF-16 XML payloads and DTD declarations are rejected."""
    utf16_junit = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE foo []><testsuite tests="0"/>'.encode("utf-16")
    junit_path = tmp_path / "junit.xml"
    junit_path.write_bytes(utf16_junit)
    with pytest.raises(ValueError, match="UTF-8 XML"):
        _parse_pytest_junit_report(junit_path)

    lowercase_dtd = b'<?xml version="1.0" encoding="UTF-8"?><!doctype foo []><testsuite tests="0"/>'
    junit_path.write_bytes(lowercase_dtd)
    with pytest.raises(ValueError, match="declarations are unsupported"):
        _parse_pytest_junit_report(junit_path)

    async def _fake_rss_fetcher(*_args: Any) -> bytes:
        return utf16_junit

    rss = RedditRssQueryProvider(fetcher=_fake_rss_fetcher)
    with pytest.raises(ResearchAdapterError, match="UTF-8 XML"):
        asyncio.run(rss("test query"))

    async def _fake_x_fetcher(*_args: Any) -> bytes:
        return ("[" * 5000 + "]" * 5000).encode("utf-8")

    x_provider = XAppOnlyQueryProvider(bearer_token="test_bearer_token_123", fetcher=_fake_x_fetcher)
    with pytest.raises(ResearchAdapterError, match="JSON"):
        asyncio.run(x_provider("test query"))


def test_verification_environment_scrubs_loader_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AEGIS-VERIFY-001: _verification_environment strips loader and wrapper variables."""
    exe = tmp_path / "python.exe"
    exe.write_bytes(b"MZ")
    cmd = LocalVerificationCommand(
        command_id="test-cmd",
        adapter="python-pytest",
        framework="pytest",
        executable=str(exe),
        argv=("-m", "pytest"),
        working_directory=str(tmp_path),
        timeout_seconds=10.0,
        source_revision="deadbeef",
        environment={"PYTHONSTARTUP": "/tmp/evil.py", "SAFE_VAR": "1"},
    )
    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
    monkeypatch.setenv("RUSTC_WRAPPER", "/tmp/wrapper")
    env = _verification_environment(cmd, is_pytest=True)
    assert "LD_PRELOAD" not in env
    assert "PYTHONSTARTUP" not in env
    assert "RUSTC_WRAPPER" not in env
    assert env["SAFE_VAR"] == "1"


def test_supply_chain_gate_require_external_attestation_mode() -> None:
    """AEGIS-SCG-001: evaluate_supply_chain_gate supports require_external_attestation."""
    default_report = evaluate_supply_chain_gate(ROOT, require_external_attestation=False)
    assert default_report["overall_ok"] is True
    strict_report = evaluate_supply_chain_gate(ROOT, require_external_attestation=True)
    if not strict_report["external_signed_attestation_present"]:
        assert strict_report["overall_ok"] is False
        check_names = {c["name"]: c["ok"] for c in strict_report["checks"]}
        assert check_names["external_signed_attestation_required"] is False


def test_rust_and_deploy_source_remediations_present() -> None:
    """Verify source-level invariants for Rust FFI, distributed, sandbox, execution, guardrail, replay, and deploy."""
    hot_ffi = (ROOT / "core" / "rust" / "src" / "ffi" / "hot.rs").read_text(encoding="utf-8")
    assert "arena.commit_owned(owned)" in hot_ffi

    mmap_ffi = (ROOT / "core" / "rust" / "src" / "ffi" / "mmap.rs").read_text(encoding="utf-8")
    assert "writer.write(&owned)" in mmap_ffi

    dist_rs = (ROOT / "core" / "rust" / "src" / "distributed.rs").read_text(encoding="utf-8")
    assert "proof.idempotency_key == envelope.idempotency_key" in dist_rs

    sandbox_rs = (ROOT / "core" / "rust" / "src" / "sandbox.rs").read_text(encoding="utf-8")
    assert "wasm_multi_memory(false)" in sandbox_rs
    assert "wasm_memory64(false)" in sandbox_rs
    assert ".memories(1)" in sandbox_rs
    assert "view.header().payload_blake3" in sandbox_rs

    exec_rs = (ROOT / "core" / "rust" / "src" / "execution.rs").read_text(encoding="utf-8")
    assert "scrub_untrusted_command_environment(&mut command);" in exec_rs

    guardrail_rs = (ROOT / "core" / "rust" / "src" / "guardrail.rs").read_text(encoding="utf-8")
    assert 'seg_str == "command"' in guardrail_rs
    assert "visit_macro_mut" in guardrail_rs

    replay_rs = (ROOT / "core" / "rust" / "src" / "replay.rs").read_text(encoding="utf-8")
    assert "NEXT_TEMP_SEQ" in replay_rs

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "chown -R aegis:aegis /var/lib/aegis" in dockerfile
    assert "chmod -R a+rX /opt/aegis" in dockerfile

    k8s = (ROOT / "deploy" / "kubernetes.yaml").read_text(encoding="utf-8")
    assert "kind: NetworkPolicy" in k8s
    assert "automountServiceAccountToken: false" in k8s
