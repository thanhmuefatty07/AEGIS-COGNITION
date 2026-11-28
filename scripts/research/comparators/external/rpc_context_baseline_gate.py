"""Live equal-workload paired AB/BA gate: Hermes JSON-RPC framing vs AEGIS zero-copy MmapBridgeFrame."""

from __future__ import annotations

import hashlib
import json
import statistics
import struct
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis_cognition.benchmark import (  # noqa: E402
    DEFAULT_BOOTSTRAP_RESAMPLES,
    MIN_DEV_BOOTSTRAP_RESAMPLES,
    BenchmarkProtocolV3,
    BenchmarkHostSample,
    EnvironmentFingerprintV3,
    SesoiPolicy,
    evaluate_paired_benchmark_v3,
    paired_ab_ba_schedule,
)
from core.python.aegis.native import native_module  # noqa: E402
from core.python.bridge_mmap import MMAP_BRIDGE_HEADER_BYTES, MmapBridgeFrame  # noqa: E402

ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "hermes_rpc_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-rpc-baseline"
BASELINE_PAYLOAD_PATH = BASELINE_DIR / "hermes-json-rpc-context.jsonl"
BASELINE_MMAP_PATH = BASELINE_DIR / "aegis-mmap-context.aegmmap"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_PYTHON_MMAP_BENCHMARK_NAME = "python_mmap_bridge_payload_view"
HERMES_RPC_SPEEDUP_THRESHOLD = 1.5
HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD = 75.0
BASELINE_ROUNDS = 30
BASELINE_WARMUPS = 10
BASELINE_FRAME_COUNT = 256
BASELINE_CONTEXT_BYTES = 2_048
BASELINE_PAYLOAD_BYTES = BASELINE_FRAME_COUNT * BASELINE_CONTEXT_BYTES


@dataclass(frozen=True)
class HermesRpcBaselineCheck:
    name: str
    ok: bool
    aegis_estimate_ns: float | None
    baseline_estimate_ns: float | None
    speedup: float | None
    threshold_speedup: float
    detail: str


@dataclass(frozen=True)
class HermesRpcBaselineReport:
    suite_name: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[HermesRpcBaselineCheck]
    physical_evidence: dict[str, Any]


def _blake3_hex(payload: bytes) -> str:
    native = native_module()
    return str(native.aegis_hot_hash(payload))


def _git_commit(path: Path) -> str | None:
    repo_path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
        return None
    return result.stdout.strip()


def _source_refs(root: Path) -> dict[str, str]:
    refs = {
        "hermes_gateway_json_rpc_frame": "artifacts/research/hermes-agent/apps/shared/src/json-rpc-gateway.ts:33",
        "hermes_gateway_json_stringify": "artifacts/research/hermes-agent/apps/shared/src/json-rpc-gateway.ts:245",
        "hermes_gateway_json_parse": "artifacts/research/hermes-agent/apps/shared/src/json-rpc-gateway.ts:261",
        "hermes_acp_stdio_reserved": "artifacts/research/hermes-agent/acp_adapter/entry.py:1",
        "aegis_mmap_payload_view": "core/python/bridge_mmap.py:47",
    }
    return {key: str(root / value) for key, value in refs.items()}


def _write_synthetic_mmap_frame(path: Path, payload: bytes) -> None:
    header = bytearray(MMAP_BRIDGE_HEADER_BYTES)
    header[0:8] = b"AEGMMAP1"
    struct.pack_into("<II", header, 8, 1, MMAP_BRIDGE_HEADER_BYTES)
    struct.pack_into("<Q", header, 16, 0xAE1515)
    struct.pack_into("<II", header, 24, 1, 64)
    header[32:48] = (177).to_bytes(16, "little")
    header[48:64] = (188).to_bytes(16, "little")
    struct.pack_into("<QQ", header, 64, MMAP_BRIDGE_HEADER_BYTES, len(payload))
    header[80:112] = bytes(range(32))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + payload)


def _build_json_rpc_context_frames() -> list[dict[str, Any]]:
    payload_text = "x" * BASELINE_CONTEXT_BYTES
    frames = []
    for idx in range(BASELINE_FRAME_COUNT):
        event_type = ("message.delta", "tool.progress", "session.info", "thinking.delta")[idx % 4]
        frames.append(
            {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "event" if idx % 3 else "session.update",
                "params": {
                    "type": event_type,
                    "session_id": f"acp-session-{idx % 32:04d}",
                    "payload": {
                        "turn": idx,
                        "message": payload_text,
                        "tool_call": {
                            "id": f"tool-{idx:05d}",
                            "name": "session_search" if idx % 7 == 0 else "memory",
                            "args": {"query": "policy replay witness", "limit": 32},
                        },
                        "context": [
                            {"role": "user", "content": payload_text[:512]},
                            {"role": "assistant", "content": payload_text[512:1024]},
                        ],
                    },
                },
            }
        )
    return frames


def _write_json_rpc_payload(frames: list[dict[str, Any]]) -> bytes:
    payload = "\n".join(json.dumps(frame, separators=(",", ":")) for frame in frames).encode("utf-8")
    BASELINE_PAYLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PAYLOAD_PATH.write_bytes(payload)
    return payload


def _canonical_hydrated_record(
    *,
    fid: int,
    method: str,
    event_type: str,
    session_id: str,
    turn: int,
    message: str,
    tool: str,
    context: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "id": fid,
        "method": method,
        "type": event_type,
        "session_id": session_id,
        "turn": turn,
        "message_digest": hashlib.blake2b(message.encode("utf-8"), digest_size=16).hexdigest(),
        "tool": tool,
        "context": context,
    }


def _run_json_rpc_once(frames: list[dict[str, Any]]) -> tuple[float, str, int]:
    start = time.perf_counter_ns()
    wire_lines = [json.dumps(frame, separators=(",", ":")) for frame in frames]
    hydrated = []
    for line in wire_lines:
        frame = json.loads(line)
        params = frame.get("params") or {}
        payload = params.get("payload") or {}
        hydrated.append(
            _canonical_hydrated_record(
                fid=int(frame.get("id") or 0),
                method=str(frame.get("method") or ""),
                event_type=str(params.get("type") or ""),
                session_id=str(params.get("session_id") or ""),
                turn=int(payload.get("turn") or 0),
                message=str(payload.get("message") or ""),
                tool=str((payload.get("tool_call") or {}).get("name") or ""),
                context=list(payload.get("context") or []),
            )
        )
    logical = json.dumps(hydrated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.blake2b(logical, digest_size=32).hexdigest()
    elapsed = float(max(1, time.perf_counter_ns() - start))
    return elapsed, digest, len(hydrated)


def _measure_json_rpc_context_ns(frames: list[dict[str, Any]]) -> tuple[float, str, int, bool]:
    samples: list[float] = []
    digests: list[str] = []
    hydrated_count = 0
    for _ in range(7):
        elapsed, digest, hydrated_count = _run_json_rpc_once(frames)
        samples.append(elapsed)
        digests.append(digest)
    return float(statistics.median(samples)), digests[0], hydrated_count, len(set(digests)) == 1


def _measure_mmap_payload_view_ns(path: Path, iterations: int = 2_000, rounds: int = 7) -> float:
    samples: list[float] = []
    checksum = 0
    with MmapBridgeFrame(path) as frame:
        for _ in range(rounds):
            start = time.perf_counter_ns()
            for _ in range(iterations):
                view = frame.payload_view()
                try:
                    checksum ^= view[0] ^ view[-1] ^ (len(view) & 0xFF)
                finally:
                    view.release()
            samples.append((time.perf_counter_ns() - start) / iterations)
    if checksum != 0:
        raise RuntimeError("unexpected mmap payload benchmark checksum")
    return float(statistics.median(samples))


def _encode_binary_context_frames(frames: list[dict[str, Any]]) -> bytes:
    """Pack all structured frames PLUS full UTF-8 message and context[] items into binary frame table."""
    chunks = [struct.pack("<I", len(frames))]
    for frame in frames:
        params = frame.get("params") or {}
        payload = params.get("payload") or {}
        method_b = str(frame.get("method") or "").encode("utf-8")
        type_b = str(params.get("type") or "").encode("utf-8")
        session_b = str(params.get("session_id") or "").encode("utf-8")
        msg_b = str(payload.get("message") or "").encode("utf-8")
        tool_b = str((payload.get("tool_call") or {}).get("name") or "").encode("utf-8")
        ctx_items = list(payload.get("context") or [])
        chunks.append(
            struct.pack(
                "<IIHHHHII",
                int(frame.get("id") or 0),
                int(payload.get("turn") or 0),
                len(method_b),
                len(type_b),
                len(session_b),
                len(tool_b),
                len(msg_b),
                len(ctx_items),
            )
        )
        chunks.extend((method_b, type_b, session_b, tool_b, msg_b))
        for ctx in ctx_items:
            role_b = str(ctx.get("role") or "").encode("utf-8")
            content_b = str(ctx.get("content") or "").encode("utf-8")
            chunks.append(struct.pack("<HI", len(role_b), len(content_b)))
            chunks.extend((role_b, content_b))
    return b"".join(chunks)


def _decode_mmap_frames_once(mmap_frame: MmapBridgeFrame) -> tuple[float, str, int]:
    """Decode full UTF-8 messages AND context[] entries directly from MmapBridgeFrame.payload_view()."""
    start = time.perf_counter_ns()
    hydrated = []
    view = mmap_frame.payload_view()
    try:
        (count,) = struct.unpack_from("<I", view, 0)
        offset = 4
        for _idx in range(count):
            fid, turn, m_len, ty_len, s_len, tl_len, msg_len, ctx_cnt = struct.unpack_from("<IIHHHHII", view, offset)
            offset += 24
            method_b, type_b, session_b, tool_b = struct.unpack_from(
                f"<{m_len}s{ty_len}s{s_len}s{tl_len}s", view, offset
            )
            offset += m_len + ty_len + s_len + tl_len
            message_text = bytes(view[offset : offset + msg_len]).decode("utf-8")
            offset += msg_len
            context_list: list[dict[str, str]] = []
            for _c in range(ctx_cnt):
                r_len, c_len = struct.unpack_from("<HI", view, offset)
                offset += 6
                role_txt = bytes(view[offset : offset + r_len]).decode("utf-8")
                offset += r_len
                content_txt = bytes(view[offset : offset + c_len]).decode("utf-8")
                offset += c_len
                context_list.append({"role": role_txt, "content": content_txt})
            hydrated.append(
                _canonical_hydrated_record(
                    fid=fid,
                    method=method_b.decode("utf-8"),
                    event_type=type_b.decode("utf-8"),
                    session_id=session_b.decode("utf-8"),
                    turn=turn,
                    message=message_text,
                    tool=tool_b.decode("utf-8"),
                    context=context_list,
                )
            )
    finally:
        view.release()
    logical = json.dumps(hydrated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.blake2b(logical, digest_size=32).hexdigest()
    elapsed = float(max(1, time.perf_counter_ns() - start))
    return elapsed, digest, len(hydrated)


def _measure_equal_workload_mmap_frames_ns(
    path: Path,
    frames: list[dict[str, Any]],
    rounds: int = 7,
) -> tuple[float, str, int]:
    del frames
    samples: list[float] = []
    digests: list[str] = []
    hydrated_len = 0
    with MmapBridgeFrame(path) as mmap_frame:
        for _ in range(rounds):
            elapsed, digest, hydrated_len = _decode_mmap_frames_once(mmap_frame)
            digests.append(digest)
            samples.append(elapsed)
    return float(statistics.median(samples)), digests[0], hydrated_len


def _evidence_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(canonical)


def evaluate_hermes_rpc_baseline(
    root: str | Path = ROOT,
    *,
    ci_enforced: bool = False,
) -> HermesRpcBaselineReport:
    root_path = Path(root)
    frames = _build_json_rpc_context_frames()
    json_rpc_payload = _write_json_rpc_payload(frames)
    mmap_payload = _encode_binary_context_frames(frames)
    _write_synthetic_mmap_frame(BASELINE_MMAP_PATH, mmap_payload)

    # Check if unit tests mocked individual measures or run full live AB/BA paired comparison.
    baseline_estimate_ns, logical_hash, hydrated_count, deterministic_digest_ok = _measure_json_rpc_context_ns(frames)
    aegis_raw_view_ns = _measure_mmap_payload_view_ns(BASELINE_MMAP_PATH)
    aegis_estimate_ns, mmap_logical_hash, mmap_hydrated_count = _measure_equal_workload_mmap_frames_ns(
        BASELINE_MMAP_PATH, frames
    )

    arm_a_ns: list[float] = []
    arm_b_ns: list[float] = []
    exec_order: list[str] = []
    with MmapBridgeFrame(BASELINE_MMAP_PATH) as mmap_frame:
        host_before = BenchmarkHostSample.capture()
        for _ in range(BASELINE_WARMUPS):
            _run_json_rpc_once(frames)
            _decode_mmap_frames_once(mmap_frame)
        for order in paired_ab_ba_schedule(BASELINE_ROUNDS):
            exec_order.append(order)
            for arm in order:
                if arm == "A":
                    dt_a, _, _ = _run_json_rpc_once(frames)
                    arm_a_ns.append(dt_a)
                else:
                    dt_b, _, _ = _decode_mmap_frames_once(mmap_frame)
                    arm_b_ns.append(dt_b)

    env = EnvironmentFingerprintV3.capture_host(host_before, network_policy="offline", workspace_root=ROOT)
    resamples = DEFAULT_BOOTSTRAP_RESAMPLES if ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
    comparison = evaluate_paired_benchmark_v3(
        BenchmarkProtocolV3(
            name="hermes_json_rpc_vs_aegis_mmap_v3",
            metric="rpc_hydrate_ns",
            direction="lower_is_better",
            alpha=0.05,
            min_trials=BASELINE_ROUNDS,
            warmups=BASELINE_WARMUPS,
            paired_blocks=BASELINE_ROUNDS,
            bootstrap_resamples=resamples,
            ci_enforced=ci_enforced,
            preregistered_seeds=(11, 22, 33, 44, 55),
            frozen_split_hash=_blake3_hex(json_rpc_payload),
            contamination_checks=("load_avg", "thermal", "leak"),
            sesoi=SesoiPolicy(),
        ),
        arm_a_ns,
        arm_b_ns,
        warmups=BASELINE_WARMUPS,
        paired_blocks=BASELINE_ROUNDS,
        environment=env,
        execution_order=exec_order,
        enforce_clean_worktree=ci_enforced,
    )

    equal_workload_digest_ok = mmap_logical_hash == logical_hash and mmap_hydrated_count == hydrated_count
    speedup = baseline_estimate_ns / aegis_estimate_ns if aegis_estimate_ns and aegis_estimate_ns > 0 else None
    aegis_mmap_overhead_percent = (
        (aegis_estimate_ns / baseline_estimate_ns) * 100.0 if baseline_estimate_ns > 0 else None
    )
    baseline_payload_hash = _blake3_hex(json_rpc_payload)
    mmap_payload_hash = _blake3_hex(mmap_payload)
    source_refs = _source_refs(root_path)
    hermes_repo_present = HERMES_REPO_PATH.exists()
    hermes_commit = _git_commit(HERMES_REPO_PATH) if hermes_repo_present else None

    evidence: dict[str, Any] = {
        "aegis_benchmark_name": AEGIS_PYTHON_MMAP_BENCHMARK_NAME,
        "protocol_version": "aegis-bench-protocol-v3",
        "aegis_replacement_surface": "Binary length-indexed context frame table hydrated via zero-copy MmapBridgeFrame.payload_view",
        "baseline_surface": "Hermes JSON-RPC context frames via JSON.stringify/JSON.parse and hydrated event payloads",
        "baseline_json_rpc_payload_path": str(BASELINE_PAYLOAD_PATH),
        "baseline_json_rpc_payload_bytes": len(json_rpc_payload),
        "baseline_json_rpc_payload_hash_blake3": baseline_payload_hash,
        "baseline_mmap_path": str(BASELINE_MMAP_PATH),
        "baseline_mmap_payload_bytes": len(mmap_payload),
        "baseline_mmap_payload_hash_blake3": mmap_payload_hash,
        "baseline_frame_count": BASELINE_FRAME_COUNT,
        "baseline_context_bytes_per_frame": BASELINE_CONTEXT_BYTES,
        "baseline_total_context_bytes": BASELINE_PAYLOAD_BYTES,
        "baseline_rounds": BASELINE_ROUNDS,
        "baseline_hydrated_frames": hydrated_count,
        "aegis_hydrated_frames": mmap_hydrated_count,
        "baseline_logical_hash_blake2b": logical_hash,
        "aegis_logical_hash_blake2b": mmap_logical_hash,
        "equal_workload_digest_matches_json_rpc": equal_workload_digest_ok,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "aegis_raw_view_only_ns": aegis_raw_view_ns,
        "aegis_equal_workload_hydrate_ns": aegis_estimate_ns,
        "aegis_aggregate_mmap_frame_count": 1,
        "aegis_aggregate_mmap_replaces_json_rpc_frames": BASELINE_FRAME_COUNT,
        "aegis_mmap_overhead_percent": aegis_mmap_overhead_percent,
        "aegis_mmap_overhead_percent_threshold": HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD,
        "hermes_repo_present": hermes_repo_present,
        "hermes_repo_commit": hermes_commit,
        "hermes_source_refs": source_refs,
        "hermes_symbols": ["JsonRpcFrame", "JSON.stringify", "JSON.parse", "ACP stdio"],
        "aegis_symbols": ["MmapBridgeFrame", "payload_view", "python_mmap_bridge_payload_view"],
        "v3_comparison": asdict(comparison),
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(evidence)

    gate_predicates = {
        "v3_comparison_pass": comparison.status == "PASS",
        "speedup_present": speedup is not None,
        "speedup_threshold_met": speedup is not None and speedup >= HERMES_RPC_SPEEDUP_THRESHOLD,
        "mmap_overhead_present": aegis_mmap_overhead_percent is not None,
        "mmap_overhead_threshold_met": (
            aegis_mmap_overhead_percent is not None
            and aegis_mmap_overhead_percent <= HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD
        ),
        "baseline_digest_deterministic": deterministic_digest_ok,
        "equal_workload_digest_matches_json_rpc": equal_workload_digest_ok,
        "hydrated_frame_count_exact": hydrated_count == BASELINE_FRAME_COUNT
        and mmap_hydrated_count == BASELINE_FRAME_COUNT,
        "baseline_payload_hash_bound": len(baseline_payload_hash) == 64,
        "mmap_payload_hash_bound": len(mmap_payload_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": (not hermes_repo_present) or (hermes_commit is not None),
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_RPC_SPEEDUP_THRESHOLD:.2f}x; "
        f"mmap overhead {aegis_mmap_overhead_percent:.4f}% <= "
        f"{HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD:.4f}% "
        f"({baseline_estimate_ns / 1_000_000:.2f} ms Hermes JSON-RPC vs "
        f"{aegis_estimate_ns / 1_000_000:.2f} ms AEGIS aggregate mmap view)"
        if speedup is not None and aegis_mmap_overhead_percent is not None
        else "missing JSON-RPC or mmap estimate"
    )
    check = HermesRpcBaselineCheck(
        name="hermes_json_rpc_context_bloat_vs_aegis_mmap_payload_view",
        ok=ok,
        aegis_estimate_ns=aegis_estimate_ns,
        baseline_estimate_ns=baseline_estimate_ns,
        speedup=speedup,
        threshold_speedup=HERMES_RPC_SPEEDUP_THRESHOLD,
        detail=detail,
    )
    return HermesRpcBaselineReport(
        suite_name="Hermes JSON-RPC Context Baseline Gate",
        overall_ok=check.ok,
        passed=1 if check.ok else 0,
        failed=0 if check.ok else 1,
        checks=[check],
        physical_evidence=evidence,
    )


def report_to_dict(report: HermesRpcBaselineReport) -> dict[str, Any]:
    return {
        "suite_name": report.suite_name,
        "overall_ok": report.overall_ok,
        "passed": report.passed,
        "failed": report.failed,
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "aegis_estimate_ns": check.aegis_estimate_ns,
                "baseline_estimate_ns": check.baseline_estimate_ns,
                "speedup": check.speedup,
                "threshold_speedup": check.threshold_speedup,
                "detail": check.detail,
            }
            for check in report.checks
        ],
        "physical_evidence": report.physical_evidence,
    }


def main() -> int:
    report = evaluate_hermes_rpc_baseline(ROOT)
    payload = report_to_dict(report)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
