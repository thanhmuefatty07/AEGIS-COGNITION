import json
import statistics
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.python.bridge_mmap import MMAP_BRIDGE_HEADER_BYTES, MmapBridgeFrame


ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "python_hotpath_gate_report.json"
PYTHON_THRESHOLDS_NS = {
    "python_mmap_bridge_payload_view": 5_000.0,
}


@dataclass(frozen=True)
class PythonHotPathCheck:
    name: str
    ok: bool
    estimate_ns: float
    threshold_ns: float
    detail: str


@dataclass(frozen=True)
class PythonHotPathReport:
    suite_name: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[PythonHotPathCheck]


def _pattern_payload(payload_len: int) -> bytes:
    return bytes((index * 31 + 7) & 0xFF for index in range(payload_len))


def _write_synthetic_mmap_frame(path: Path, payload_len: int = 1024 * 1024) -> None:
    payload = _pattern_payload(payload_len)
    header = bytearray(MMAP_BRIDGE_HEADER_BYTES)
    header[0:8] = b"AEGMMAP1"
    struct.pack_into("<II", header, 8, 1, MMAP_BRIDGE_HEADER_BYTES)
    struct.pack_into("<Q", header, 16, 0xAE1515)
    struct.pack_into("<II", header, 24, 1, 64)
    header[32:48] = (77).to_bytes(16, "little")
    header[48:64] = (88).to_bytes(16, "little")
    struct.pack_into("<QQ", header, 64, MMAP_BRIDGE_HEADER_BYTES, len(payload))
    header[80:112] = bytes(range(32))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + payload)


def _measure_payload_view_ns(frame: MmapBridgeFrame, iterations: int, rounds: int) -> float:
    samples: list[float] = []
    checksum = 0
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


def evaluate_python_hotpaths(root: str | Path = ROOT) -> PythonHotPathReport:
    root_path = Path(root)
    frame_path = root_path / "target" / "aegis-python-hotpath" / "mmap-payload-view.aegmmap"
    _write_synthetic_mmap_frame(frame_path)
    with MmapBridgeFrame(frame_path) as frame:
        estimate_ns = _measure_payload_view_ns(frame, iterations=20_000, rounds=7)
    threshold_ns = PYTHON_THRESHOLDS_NS["python_mmap_bridge_payload_view"]
    check = PythonHotPathCheck(
        name="python_mmap_bridge_payload_view",
        ok=estimate_ns <= threshold_ns,
        estimate_ns=estimate_ns,
        threshold_ns=threshold_ns,
        detail=f"{estimate_ns:.3f} ns <= {threshold_ns:.3f} ns",
    )
    return PythonHotPathReport(
        suite_name="Python Hot-Path Benchmarks",
        overall_ok=check.ok,
        passed=1 if check.ok else 0,
        failed=0 if check.ok else 1,
        checks=[check],
    )


def report_to_dict(report: PythonHotPathReport) -> dict:
    return {
        "suite_name": report.suite_name,
        "overall_ok": report.overall_ok,
        "passed": report.passed,
        "failed": report.failed,
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "estimate_ns": check.estimate_ns,
                "threshold_ns": check.threshold_ns,
                "detail": check.detail,
            }
            for check in report.checks
        ],
    }


def main() -> int:
    report = evaluate_python_hotpaths(ROOT)
    payload = report_to_dict(report)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
