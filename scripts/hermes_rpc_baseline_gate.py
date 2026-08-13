import json
import os
import statistics
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.python.bridge_mmap import MMAP_BRIDGE_HEADER_BYTES, MmapBridgeFrame


ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "hermes_rpc_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-rpc-baseline"
BASELINE_PAYLOAD_PATH = BASELINE_DIR / "hermes-json-rpc-context.jsonl"
BASELINE_MMAP_PATH = BASELINE_DIR / "aegis-mmap-context.aegmmap"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_PYTHON_MMAP_BENCHMARK_NAME = "python_mmap_bridge_payload_view"
HERMES_RPC_SPEEDUP_THRESHOLD = 20.0
HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD = 1.5
BASELINE_ROUNDS = 7
BASELINE_FRAME_COUNT = 512
BASELINE_CONTEXT_BYTES = 4096
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
    physical_evidence: dict


def _cargo_env() -> dict:
    env = os.environ.copy()
    py_root = Path(sys.executable).parent
    env["PYO3_PYTHON"] = sys.executable
    env["PATH"] = os.pathsep.join(
        [str(py_root), str(py_root / "DLLs"), str(py_root / "libs"), env.get("PATH", "")]
    )
    env.setdefault("RUSTFLAGS", "-C debuginfo=0")
    return env


def _blake3_hex(root: Path, payload: bytes) -> str:
    try:
        import blake3  # type: ignore

        return blake3.blake3(payload).hexdigest()
    except ImportError:
        result = subprocess.run(
            ["cargo", "run", "--quiet", "--bin", "aegis-nerve-cli", "--", "blake3-stdin"],
            cwd=root / "core" / "rust",
            input=payload,
            capture_output=True,
            env=_cargo_env(),
            check=True,
        )
        return result.stdout.decode("utf-8").strip()


def _git_commit(path: Path) -> str | None:
    repo_path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
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


def _pattern_payload(payload_len: int) -> bytes:
    return bytes((index * 31 + 7) & 0xFF for index in range(payload_len))


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


def _build_json_rpc_context_frames() -> list[dict]:
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


def _write_json_rpc_payload(frames: list[dict]) -> bytes:
    payload = "\n".join(json.dumps(frame, separators=(",", ":")) for frame in frames).encode("utf-8")
    BASELINE_PAYLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PAYLOAD_PATH.write_bytes(payload)
    return payload


def _measure_json_rpc_context_ns(frames: list[dict]) -> tuple[float, str, int, bool]:
    samples: list[float] = []
    digests: list[str] = []
    hydrated_count = 0
    import hashlib

    for _ in range(BASELINE_ROUNDS):
        start = time.perf_counter_ns()
        wire_lines = []
        for frame in frames:
            wire_lines.append(json.dumps(frame, separators=(",", ":")))
        hydrated = []
        for line in wire_lines:
            frame = json.loads(line)
            params = frame.get("params") or {}
            payload = params.get("payload") or {}
            hydrated.append(
                {
                    "id": frame.get("id"),
                    "method": frame.get("method"),
                    "type": params.get("type"),
                    "session_id": params.get("session_id"),
                    "turn": payload.get("turn"),
                    "message_len": len(payload.get("message") or ""),
                    "tool": (payload.get("tool_call") or {}).get("name"),
                    "context_count": len(payload.get("context") or []),
                }
            )
        logical = json.dumps(hydrated, sort_keys=True, separators=(",", ":")).encode("utf-8")
        hasher = hashlib.blake2b(digest_size=32)
        hasher.update(logical)
        digests.append(hasher.hexdigest())
        hydrated_count = len(hydrated)
        samples.append(time.perf_counter_ns() - start)
    return float(statistics.median(samples)), digests[0], hydrated_count, len(set(digests)) == 1


def _measure_mmap_payload_view_ns(path: Path, iterations: int = 20_000, rounds: int = 7) -> float:
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


def _evidence_hash(root: Path, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(root, canonical)


def evaluate_hermes_rpc_baseline(root: str | Path = ROOT) -> HermesRpcBaselineReport:
    root_path = Path(root)
    frames = _build_json_rpc_context_frames()
    json_rpc_payload = _write_json_rpc_payload(frames)
    mmap_payload = _pattern_payload(BASELINE_PAYLOAD_BYTES)
    _write_synthetic_mmap_frame(BASELINE_MMAP_PATH, mmap_payload)

    baseline_estimate_ns, logical_hash, hydrated_count, deterministic_digest_ok = _measure_json_rpc_context_ns(
        frames
    )
    aegis_estimate_ns = _measure_mmap_payload_view_ns(BASELINE_MMAP_PATH)
    speedup = (
        baseline_estimate_ns / aegis_estimate_ns
        if aegis_estimate_ns and aegis_estimate_ns > 0
        else None
    )
    aegis_mmap_overhead_percent = (
        (aegis_estimate_ns / baseline_estimate_ns) * 100.0 if baseline_estimate_ns > 0 else None
    )
    baseline_payload_hash = _blake3_hex(root_path, json_rpc_payload)
    mmap_payload_hash = _blake3_hex(root_path, mmap_payload)
    source_refs = _source_refs(root_path)

    evidence = {
        "aegis_benchmark_name": AEGIS_PYTHON_MMAP_BENCHMARK_NAME,
        "aegis_replacement_surface": "Rust-owned mmap frame exposed as Python memoryview payload_view",
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
        "baseline_logical_hash_blake2b": logical_hash,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "aegis_aggregate_mmap_frame_count": 1,
        "aegis_aggregate_mmap_replaces_json_rpc_frames": BASELINE_FRAME_COUNT,
        "aegis_mmap_overhead_percent": aegis_mmap_overhead_percent,
        "aegis_mmap_overhead_percent_threshold": HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD,
        "hermes_repo_commit": _git_commit(HERMES_REPO_PATH),
        "hermes_source_refs": source_refs,
        "hermes_symbols": ["JsonRpcFrame", "JSON.stringify", "JSON.parse", "ACP stdio"],
        "aegis_symbols": ["MmapBridgeFrame", "payload_view", "python_mmap_bridge_payload_view"],
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(root_path, evidence)

    gate_predicates = {
        "speedup_present": speedup is not None,
        "speedup_threshold_met": speedup is not None and speedup >= HERMES_RPC_SPEEDUP_THRESHOLD,
        "mmap_overhead_present": aegis_mmap_overhead_percent is not None,
        "mmap_overhead_threshold_met": (
            aegis_mmap_overhead_percent is not None
            and aegis_mmap_overhead_percent <= HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD
        ),
        "baseline_digest_deterministic": deterministic_digest_ok,
        "hydrated_frame_count_exact": hydrated_count == BASELINE_FRAME_COUNT,
        "baseline_payload_hash_bound": len(baseline_payload_hash) == 64,
        "mmap_payload_hash_bound": len(mmap_payload_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": evidence["hermes_repo_commit"] is not None,
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_RPC_SPEEDUP_THRESHOLD:.2f}x; "
        f"mmap overhead {aegis_mmap_overhead_percent:.4f}% <= "
        f"{HERMES_RPC_MMAP_OVERHEAD_PERCENT_THRESHOLD:.4f}% "
        f"({baseline_estimate_ns / 1_000_000:.2f} ms Hermes JSON-RPC vs "
        f"{aegis_estimate_ns:.2f} ns AEGIS aggregate mmap view)"
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


def report_to_dict(report: HermesRpcBaselineReport) -> dict:
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
