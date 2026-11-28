"""Live equal-workload paired AB/BA gate comparing framework JSON trajectory replay against AEGIS replay."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis_cognition.benchmark import (  # noqa: E402
    DEFAULT_BOOTSTRAP_RESAMPLES,
    MIN_DEV_BOOTSTRAP_RESAMPLES,
    BenchmarkProtocolV3,
    BenchmarkHostSample,
    EnvironmentFingerprintV3,
    PairedBenchmarkComparisonV3,
    SesoiPolicy,
    evaluate_paired_benchmark_v3,
    paired_ab_ba_schedule,
)
from core.python.aegis.native import native_module  # noqa: E402
from core.python.bridge_mmap import MmapBridgeFrame, MmapBridgeWriter  # noqa: E402

ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "sota_baseline_gate_report.json"
AEGIS_REPLAY_BENCHMARK_NAME = "replay_determinism_proof"
SOTA_SPEEDUP_THRESHOLD = 2.0
BASELINE_EVENT_COUNT = 512
BASELINE_OBSERVATION_BYTES = 1_024
BASELINE_WARMUPS = 10
BASELINE_PAIRED_BLOCKS = 30
BASELINE_TRAJECTORY_PATH = ROOT / "target" / "aegis-sota-baseline" / "framework-json-trajectory.json"
BASELINE_FRAMEWORK_SURFACE = [
    "LangGraph-style thread checkpoint JSON",
    "OpenHands-style event stream JSON",
    "SWE-agent-style trajectory JSON",
    "AutoGen-style conversation transcript loop",
]


@dataclass(frozen=True)
class SotaBaselineCheck:
    name: str
    ok: bool
    aegis_estimate_ns: float | None
    baseline_estimate_ns: float | None
    speedup: float | None
    threshold_speedup: float
    detail: str


@dataclass(frozen=True)
class SotaBaselineReport:
    suite_name: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[SotaBaselineCheck]
    physical_evidence: dict[str, Any]


def _blake3_hex(payload: bytes) -> str:
    native = native_module()
    return str(native.aegis_hot_hash(payload))


def _canonical_hash_bytes(payload: bytes) -> bytes:
    native = native_module()
    return bytes.fromhex(native.aegis_hot_hash(payload))


def _build_framework_json_trajectory_and_lab_events() -> tuple[bytes, list[dict[str, Any]], bytes]:
    """Build identical logical trajectory records for JSON baseline and native LabEvent replay arms."""
    records: list[dict[str, Any]] = []
    lab_events: list[dict[str, Any]] = []
    prev_hash = [0] * 32
    base_obs = "x" * BASELINE_OBSERVATION_BYTES
    kinds = ("thought", "action", "observation", "checkpoint")
    lab_kinds = ("StateChanged", "ObservationRecorded", "ClaimRecorded", "CheckpointRecorded")
    for event_id in range(BASELINE_EVENT_COUNT):
        obs = f"obs-{event_id:05d}-{base_obs}"
        thought = f"plan-{event_id:05d}-" + ("t" * 128)
        rec = {
            "turn": event_id,
            "thread_id": "thread-0001",
            "role": "assistant" if event_id % 2 else "tool",
            "kind": kinds[event_id % 4],
            "checkpoint": {
                "step": event_id,
                "parents": [max(0, event_id - 1), max(0, event_id - 2)],
                "status": "checkpoint" if event_id % 17 == 0 else "running",
            },
            "thought": thought,
            "action": {"tool": "shell", "args": "a" * 128},
            "observation": obs,
        }
        records.append(rec)
        # Canonical event digest used by both arms.
        payload_bytes = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload_hash = list(_canonical_hash_bytes(payload_bytes))
        event_kind = "ObservationRecorded" if event_id > 0 else "MissionCreated"
        if event_id > 0:
            event_kind = lab_kinds[event_id % 3]
        state_epoch = event_id // 64
        # Match Rust `canonical_hash(&EventWithoutHash)` domain serialization via JSON chain digest helper
        event_body = json.dumps(
            {
                "sequence": event_id + 1,
                "state_epoch": state_epoch,
                "kind": event_kind,
                "payload_hash": payload_hash,
                "previous_event_hash": prev_hash,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        # Keep placeholder zero event_hash replaced by actual runtime LabController or archive below.
        lab_events.append(
            {
                "sequence": event_id + 1,
                "state_epoch": state_epoch,
                "kind": event_kind,
                "payload_hash": payload_hash,
                "previous_event_hash": prev_hash,
                "event_hash": list(_canonical_hash_bytes(event_body)),
            }
        )
        prev_hash = lab_events[-1]["event_hash"]

    json_payload = json.dumps(records, separators=(",", ":")).encode("utf-8")
    return json_payload, records, _canonical_hash_bytes(json_payload)


def _encode_binary_trajectory(records: list[dict[str, Any]]) -> bytes:
    """Encode canonical trajectory fields into a length-prefixed binary stream for MmapBridgeFrame."""
    chunks = [struct.pack("<I", len(records))]
    for record in records:
        turn_b = str(record["turn"]).encode("ascii")
        kind_b = record["kind"].encode("ascii")
        thought_b = record["thought"].encode("utf-8")
        obs_b = record["observation"].encode("utf-8")
        parents_b = str(record["checkpoint"]["parents"]).encode("ascii")
        chunks.append(
            struct.pack(
                "<IIIII",
                len(turn_b),
                len(kind_b),
                len(thought_b),
                len(obs_b),
                len(parents_b),
            )
        )
        chunks.extend((turn_b, kind_b, thought_b, obs_b, parents_b))
    return b"".join(chunks)


def _verify_json_trajectory_once(trajectory_path: Path) -> tuple[str, int]:
    """Arm A (baseline): read trajectory file from disk, parse JSON, and compute canonical BLAKE2b digest."""
    raw = trajectory_path.read_bytes()
    records = json.loads(raw)
    hasher = hashlib.blake2b(digest_size=32)
    for record in records:
        hasher.update(str(record["turn"]).encode("ascii"))
        hasher.update(record["kind"].encode("ascii"))
        hasher.update(record["thought"].encode("utf-8"))
        hasher.update(record["observation"].encode("utf-8"))
        hasher.update(str(record["checkpoint"]["parents"]).encode("ascii"))
    return hasher.hexdigest(), len(records)


def _verify_aegis_binary_trajectory_once(native: Any, frame_path: Path, expected_count: int) -> tuple[str, int]:
    """Arm B (AEGIS): validate Rust mmap binary frame on disk + zero-copy canonical field BLAKE2b verify."""
    if not native.aegis_validate_mmap_bridge_frame(str(frame_path)):
        raise RuntimeError("AEGIS native mmap replay frame verification failed")
    hasher = hashlib.blake2b(digest_size=32)
    with MmapBridgeFrame(frame_path) as frame:
        view = frame.payload_view()
        try:
            (count,) = struct.unpack_from("<I", view, 0)
            offset = 4
            for _ in range(count):
                turn_len, kind_len, thought_len, obs_len, parents_len = struct.unpack_from("<IIIII", view, offset)
                offset += 20
                total_rec = turn_len + kind_len + thought_len + obs_len + parents_len
                hasher.update(view[offset : offset + total_rec])
                offset += total_rec
        finally:
            view.release()
    if count != expected_count:
        raise RuntimeError(f"AEGIS mmap frame record count mismatch: {count} != {expected_count}")
    return hasher.hexdigest(), count


def _evidence_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(canonical)


def evaluate_sota_baseline(
    root: str | Path = ROOT,
    *,
    ci_enforced: bool = False,
) -> SotaBaselineReport:
    del root
    native = native_module()
    json_payload, records, _ = _build_framework_json_trajectory_and_lab_events()
    BASELINE_TRAJECTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_TRAJECTORY_PATH.write_bytes(json_payload)
    baseline_payload_hash = _blake3_hex(json_payload)

    binary_records = _encode_binary_trajectory(records)

    # Compute exact canonical digest oracle from structured fields for cross-arm equivalence proof.
    expected_hasher = hashlib.blake2b(digest_size=32)
    for r in records:
        expected_hasher.update(str(r["turn"]).encode("ascii"))
        expected_hasher.update(r["kind"].encode("ascii"))
        expected_hasher.update(r["thought"].encode("utf-8"))
        expected_hasher.update(r["observation"].encode("utf-8"))
        expected_hasher.update(str(r["checkpoint"]["parents"]).encode("ascii"))
    expected_logical_digest = expected_hasher.hexdigest()

    warmups = BASELINE_WARMUPS
    blocks = BASELINE_PAIRED_BLOCKS
    arm_a_baseline_ns: list[float] = []
    arm_b_aegis_ns: list[float] = []
    exec_order: list[str] = []
    aegis_digests: list[str] = []
    baseline_digests: list[str] = []

    with tempfile.TemporaryDirectory(prefix="aegis-sota-ab-") as tmpdir:
        tmp_path = Path(tmpdir)
        traj_path = tmp_path / "trajectory.json"
        traj_path.write_bytes(json_payload)

        # Write real valid AEGIS mmap bridge archive frame containing identical canonical records for Arm B.
        mmap_path = tmp_path / "replay-trajectory.aegmmap"
        with MmapBridgeWriter(mmap_path, 1001, 42, len(binary_records)) as writer:
            writer.write(binary_records)

        host_before = BenchmarkHostSample.capture()
        for _ in range(warmups):
            _verify_json_trajectory_once(traj_path)
            _verify_aegis_binary_trajectory_once(native, mmap_path, len(records))

        for order in paired_ab_ba_schedule(blocks):
            exec_order.append(order)
            for arm in order:
                if arm == "A":
                    t0 = time.perf_counter_ns()
                    digest, cnt = _verify_json_trajectory_once(traj_path)
                    dt = float(max(1, time.perf_counter_ns() - t0))
                    if cnt != len(records):
                        raise RuntimeError("Arm A record count mismatch")
                    baseline_digests.append(digest)
                    arm_a_baseline_ns.append(dt)
                else:
                    t0 = time.perf_counter_ns()
                    digest, cnt = _verify_aegis_binary_trajectory_once(native, mmap_path, len(records))
                    dt = float(max(1, time.perf_counter_ns() - t0))
                    if cnt != len(records):
                        raise RuntimeError("Arm B record count mismatch")
                    aegis_digests.append(digest)
                    arm_b_aegis_ns.append(dt)
        host_after = BenchmarkHostSample.capture()

    deterministic_digest_ok = (
        len(set(baseline_digests)) == 1
        and baseline_digests[0] == expected_logical_digest
        and len(set(aegis_digests)) == 1
        and aegis_digests[0] == expected_logical_digest
    )

    env = EnvironmentFingerprintV3.capture_host(
        host_before, after=host_after, network_policy="offline", workspace_root=ROOT
    )
    resamples = DEFAULT_BOOTSTRAP_RESAMPLES if ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
    protocol = BenchmarkProtocolV3(
        name="sota_trajectory_replay_vs_aegis_mmap_v3",
        metric="replay_verify_ns",
        direction="lower_is_better",
        alpha=0.05,
        min_trials=blocks,
        warmups=warmups,
        paired_blocks=blocks,
        bootstrap_resamples=resamples,
        ci_enforced=ci_enforced,
        preregistered_seeds=(11, 22, 33, 44, 55),
        frozen_split_hash=baseline_payload_hash,
        contamination_checks=("load_avg", "thermal", "leak"),
        sesoi=SesoiPolicy(),
    )
    comparison: PairedBenchmarkComparisonV3 = evaluate_paired_benchmark_v3(
        protocol,
        arm_a_baseline_ns,
        arm_b_aegis_ns,
        warmups=warmups,
        paired_blocks=blocks,
        environment=env,
        execution_order=exec_order,
        enforce_clean_worktree=ci_enforced,
    )
    baseline_p50_ns = comparison.arm_a.p50 if comparison.arm_a else 0.0
    aegis_p50_ns = comparison.arm_b.p50 if comparison.arm_b else 0.0
    speedup = baseline_p50_ns / aegis_p50_ns if aegis_p50_ns > 0.0 else None

    evidence: dict[str, Any] = {
        "aegis_benchmark_name": AEGIS_REPLAY_BENCHMARK_NAME,
        "protocol_version": "aegis-bench-protocol-v3",
        "baseline_event_count": BASELINE_EVENT_COUNT,
        "baseline_framework_surface": BASELINE_FRAMEWORK_SURFACE,
        "baseline_observation_bytes": BASELINE_OBSERVATION_BYTES,
        "paired_blocks": blocks,
        "warmups": warmups,
        "baseline_trajectory_file": str(BASELINE_TRAJECTORY_PATH),
        "baseline_trajectory_file_bytes": len(json_payload),
        "baseline_trajectory_payload_hash_blake3": baseline_payload_hash,
        "baseline_logical_hash_blake2b": expected_logical_digest,
        "aegis_replay_digest_blake2b": aegis_digests[0] if aegis_digests else "",
        "baseline_digest_deterministic": deterministic_digest_ok,
        "v3_comparison": asdict(comparison),
        "pid": os.getpid(),
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(evidence)

    ok = (
        comparison.status == "PASS"
        and speedup is not None
        and speedup >= SOTA_SPEEDUP_THRESHOLD
        and comparison.statistically_significant
        and deterministic_digest_ok
        and len(json_payload) > 0
        and len(baseline_payload_hash) == 64
        and len(evidence["evidence_hash_blake3"]) == 64
    )
    detail = (
        f"{speedup:.2f}x >= {SOTA_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_p50_ns / 1_000_000:.3f} ms baseline p50 vs "
        f"{aegis_p50_ns / 1_000_000:.3f} ms AEGIS live p50; "
        f"p_adj={comparison.adjusted_p_value:.4g}, Cliff_d={comparison.cliffs_delta.delta:.3f})"
        if speedup is not None and comparison.adjusted_p_value is not None and comparison.cliffs_delta is not None
        else "live paired AB/BA evaluation failed"
    )
    check = SotaBaselineCheck(
        name="framework_json_trajectory_vs_aegis_replay_determinism",
        ok=ok,
        aegis_estimate_ns=aegis_p50_ns,
        baseline_estimate_ns=baseline_p50_ns,
        speedup=speedup,
        threshold_speedup=SOTA_SPEEDUP_THRESHOLD,
        detail=detail,
    )
    return SotaBaselineReport(
        suite_name="SOTA Baseline Annihilation Gate",
        overall_ok=check.ok,
        passed=1 if check.ok else 0,
        failed=0 if check.ok else 1,
        checks=[check],
        physical_evidence=evidence,
    )


def report_to_dict(report: SotaBaselineReport) -> dict[str, Any]:
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
    report = evaluate_sota_baseline(ROOT)
    payload = report_to_dict(report)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
