import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "sota_baseline_gate_report.json"
AEGIS_REPLAY_BENCHMARK_NAME = "replay_determinism_proof"
SOTA_SPEEDUP_THRESHOLD = 10.0
BASELINE_EVENT_COUNT = 8_002
BASELINE_OBSERVATION_BYTES = 4_096
BASELINE_ROUNDS = 3
BASELINE_TRAJECTORY_PATH = (
    ROOT / "target" / "aegis-sota-baseline" / "framework-json-trajectory.json"
)
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
    physical_evidence: dict


def _criterion_estimate_ns(root: Path, name: str) -> float | None:
    path = root / "target" / "criterion" / name / "new" / "estimates.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    estimate = data.get("slope") or data.get("mean") or data.get("median")
    if not estimate or "point_estimate" not in estimate:
        return None
    return float(estimate["point_estimate"])


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


def _build_framework_json_trajectory() -> bytes:
    records = []
    for event_id in range(BASELINE_EVENT_COUNT):
        records.append(
            {
                "turn": event_id,
                "thread_id": "thread-0001",
                "role": "assistant" if event_id % 2 else "tool",
                "kind": ("thought", "action", "observation", "checkpoint")[event_id % 4],
                "checkpoint": {
                    "step": event_id,
                    "parents": [max(0, event_id - 1), max(0, event_id - 2)],
                    "status": "checkpoint" if event_id % 17 == 0 else "running",
                },
                "thought": f"plan-{event_id:05d}-" + ("t" * 256),
                "action": {
                    "tool": "shell",
                    "args": "a" * 256,
                },
                "observation": f"obs-{event_id:05d}-" + ("x" * BASELINE_OBSERVATION_BYTES),
            }
        )
    return json.dumps(records, separators=(",", ":")).encode("utf-8")


def _measure_framework_json_replay_ns(payload: bytes) -> tuple[float, str, bool]:
    samples: list[float] = []
    digests: list[str] = []
    for _ in range(BASELINE_ROUNDS):
        start = time.perf_counter_ns()
        records = json.loads(payload)
        import hashlib

        hasher = hashlib.blake2b(digest_size=32)
        for record in records:
            hasher.update(str(record["turn"]).encode("ascii"))
            hasher.update(record["kind"].encode("ascii"))
            hasher.update(record["thought"].encode("utf-8"))
            hasher.update(record["observation"].encode("utf-8"))
            hasher.update(str(record["checkpoint"]["parents"]).encode("ascii"))
        digests.append(hasher.hexdigest())
        samples.append(time.perf_counter_ns() - start)
    return float(statistics.median(samples)), digests[0], len(set(digests)) == 1


def _evidence_hash(root: Path, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(root, canonical)


def evaluate_sota_baseline(root: str | Path = ROOT) -> SotaBaselineReport:
    root_path = Path(root)
    aegis_estimate_ns = _criterion_estimate_ns(root_path, AEGIS_REPLAY_BENCHMARK_NAME)
    payload = _build_framework_json_trajectory()
    BASELINE_TRAJECTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_TRAJECTORY_PATH.write_bytes(payload)
    baseline_payload_hash = _blake3_hex(root_path, payload)
    baseline_estimate_ns, logical_hash, deterministic_digest_ok = _measure_framework_json_replay_ns(
        payload
    )
    speedup = (
        baseline_estimate_ns / aegis_estimate_ns
        if aegis_estimate_ns and aegis_estimate_ns > 0
        else None
    )
    evidence = {
        "aegis_benchmark_name": AEGIS_REPLAY_BENCHMARK_NAME,
        "baseline_event_count": BASELINE_EVENT_COUNT,
        "baseline_framework_surface": BASELINE_FRAMEWORK_SURFACE,
        "baseline_observation_bytes": BASELINE_OBSERVATION_BYTES,
        "baseline_rounds": BASELINE_ROUNDS,
        "baseline_trajectory_file": str(BASELINE_TRAJECTORY_PATH),
        "baseline_trajectory_file_bytes": len(payload),
        "baseline_trajectory_payload_hash_blake3": baseline_payload_hash,
        "baseline_logical_hash_blake2b": logical_hash,
        "baseline_digest_deterministic": deterministic_digest_ok,
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(root_path, evidence)

    ok = (
        aegis_estimate_ns is not None
        and speedup is not None
        and speedup >= SOTA_SPEEDUP_THRESHOLD
        and deterministic_digest_ok
        and len(payload) > 0
        and len(baseline_payload_hash) == 64
        and len(evidence["evidence_hash_blake3"]) == 64
    )
    detail = (
        f"{speedup:.2f}x >= {SOTA_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_estimate_ns / 1_000_000:.2f} ms baseline vs "
        f"{aegis_estimate_ns / 1_000_000:.2f} ms AEGIS)"
        if speedup is not None and aegis_estimate_ns is not None
        else f"missing Criterion estimate for {AEGIS_REPLAY_BENCHMARK_NAME}"
    )
    check = SotaBaselineCheck(
        name="framework_json_trajectory_vs_aegis_replay_determinism",
        ok=ok,
        aegis_estimate_ns=aegis_estimate_ns,
        baseline_estimate_ns=baseline_estimate_ns,
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


def report_to_dict(report: SotaBaselineReport) -> dict:
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
