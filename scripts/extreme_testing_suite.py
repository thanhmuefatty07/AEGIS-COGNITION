#!/usr/bin/env python3
"""AEGIS-COGNITION Load, Fuzz, Chaos & Security Verification Suite.

Exercises real AEGIS components (`aegis_cognition.aegis_nerve`,
`core.python.bridge_mmap.MmapBridgeFrame`, and `aegis_cognition.benchmark`)
without fabricated XOR soak loops or hardcoded scorecard theater.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis_cognition.benchmark import (  # noqa: E402
    OpenLoopConfig,
    OpenLoopHarness,
    quantile_type7_sorted,
)
from core.python.aegis.native import native_module  # noqa: E402
from core.python.bridge_mmap import MmapBridgeFrame  # noqa: E402

ARTIFACTS_DIR = ROOT / "artifacts" / "extreme_testing"


def _rss_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024.0 * 1024.0)
    except ImportError, OSError:
        pass
    if os.name == "nt":
        import ctypes.wintypes as wintypes

        class MemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("page_fault_count", wintypes.DWORD),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
            ]

        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        proc = kernel32.OpenProcess(0x1000, False, os.getpid())
        if not proc:
            return None
        try:
            psapi.GetProcessMemoryInfo(proc, ctypes.byref(counters), ctypes.sizeof(counters))
            return float(counters.working_set_size) / (1024.0 * 1024.0)
        finally:
            kernel32.CloseHandle(proc)
    return None


class LoadTestRunner:
    """Drives real Rust HotEngine arena commits, admission queue saturation, and OpenLoop tail checks."""

    def __init__(self, tmpdir: Path) -> None:
        self.tmpdir = tmpdir
        self.native = native_module()

    def test_backpressure_flood(self, num_payloads: int = 2_000, payload_size: int = 4096) -> dict[str, Any]:
        payload = bytes(((i * 31 + 7) & 0xFF) for i in range(payload_size))
        mv = memoryview(payload)
        latencies_ns: list[float] = []
        overloads_rejected = 0
        try:
            # Warmup 10 commits.
            for _ in range(10):
                self.native.aegis_hot_commit_buffer(mv, "DEV")

            start = time.perf_counter()
            for idx in range(num_payloads):
                t0 = time.perf_counter_ns()
                raw = self.native.aegis_hot_commit_buffer(mv, "DEV")
                latencies_ns.append(float(max(1, time.perf_counter_ns() - t0)))
                if '"handle_valid":true' not in raw:
                    raise RuntimeError(f"Hot arena rejected valid commit at idx={idx}")
            elapsed = max(1e-6, time.perf_counter() - start)
        finally:
            mv.release()

        # Exercise AuthoritativeRuntime bounded queue saturation / overload rejection behavior cleanly.
        small_req = json.dumps(
            {
                "schema": "aegis-resource-contract-v1",
                "task_id": 1,
                "attempt_id": 1,
                "work_kind": "NativeTask",
                "cpu": {"min_threads": 1, "max_threads": 1},
                "host_memory": {"bytes": 1024 * 1024},
                "accelerator": None,
                "accelerator_memory": None,
                "io": {"max_in_flight": 1},
                "process_limit": None,
                "thread_limit": None,
                "fd_limit": None,
                "api_budget": None,
                "token_budget": None,
                "deadline": {"deadline_ms": None},
                "priority": "Foreground",
                "side_effect_class": "ReadOnly",
            },
            separators=(",", ":"),
        )
        base_tid = ((time.time_ns() % 1_000_000_000) + 1) * 10_000
        queued_cleanup: list[int] = []
        admitted_cleanup: list[str] = []
        try:
            for j in range(96):
                tid = base_tid + j
                huge_req = json.dumps(
                    {
                        "schema": "aegis-resource-contract-v1",
                        "task_id": tid,
                        "attempt_id": 1,
                        "work_kind": "NativeTask",
                        "cpu": {"min_threads": 1, "max_threads": 1},
                        "host_memory": {"bytes": 4 * 1024 * 1024 * 1024 * 1024},
                        "accelerator": None,
                        "accelerator_memory": None,
                        "io": {"max_in_flight": 1},
                        "process_limit": None,
                        "thread_limit": None,
                        "fd_limit": None,
                        "api_budget": None,
                        "token_budget": None,
                        "deadline": {"deadline_ms": None},
                        "priority": "Critical",
                        "side_effect_class": "ReadOnly",
                    },
                    separators=(",", ":"),
                )
                try:
                    sub = json.loads(self.native.aegis_runtime_submit(tid, "[]", huge_req, 1_700_000_000_000 + j))
                    st = sub.get("status")
                    if st == "rejected":
                        overloads_rejected += 1
                    elif st == "queued":
                        queued_cleanup.append(tid)
                    elif st == "admitted" and sub.get("lease_token"):
                        admitted_cleanup.append(json.dumps(sub["lease_token"], separators=(",", ":")))
                except RuntimeError:
                    overloads_rejected += 1
        finally:
            for tid in queued_cleanup:
                self.native.aegis_runtime_cancel(tid, 1)
            for tok in admitted_cleanup:
                self.native.aegis_runtime_finish(tok, "done")

        open_loop = OpenLoopHarness.sweep_callable(
            "extreme_backpressure_open_loop",
            lambda: self.native.aegis_resource_admission_preview(small_req, 1_700_000_000_000),
            config=OpenLoopConfig(
                utilization_levels=(0.25, 0.50, 0.75, 0.90),
                requests_per_point=100,
                epochs=10,
                queue_capacity=32,
                bootstrap_resamples=2000,
                min_resamples=2000,
                seed=0xAE61_E100,
            ),
        )

        ordered = sorted(latencies_ns)
        return {
            "test": "backpressure_flood",
            "num_payloads": num_payloads,
            "total_s": round(elapsed, 4),
            "throughput_ops_per_sec": round(num_payloads / elapsed, 1),
            "latency_p50_ns": quantile_type7_sorted(ordered, 0.50),
            "latency_p95_ns": quantile_type7_sorted(ordered, 0.95),
            "latency_p99_ns": quantile_type7_sorted(ordered, 0.99),
            "latency_max_ns": ordered[-1],
            "overloads_rejected": overloads_rejected,
            "open_loop_summary": asdict(open_loop),
            "status": "PASS" if overloads_rejected > 0 else "FAILED",
        }

    def test_memory_soak(self, duration_seconds: int = 5) -> dict[str, Any]:
        payload = bytes(((i * 19 + 3) & 0xFF) for i in range(4096))
        mv = memoryview(payload)
        rss_samples: list[dict[str, float | int]] = []
        count = 0
        start = time.perf_counter()
        try:
            while time.perf_counter() - start < duration_seconds:
                self.native.aegis_hot_commit_buffer(mv, "DEV")
                count += 1
                if count % 250 == 0:
                    rss = _rss_mb()
                    if rss is not None:
                        rss_samples.append(
                            {"elapsed_s": round(time.perf_counter() - start, 3), "rss_mb": round(rss, 3), "ops": count}
                        )
        finally:
            mv.release()
        elapsed = max(1e-6, time.perf_counter() - start)
        if len(rss_samples) >= 2:
            rss_delta_mb = float(rss_samples[-1]["rss_mb"]) - float(rss_samples[0]["rss_mb"])
        else:
            rss_delta_mb = 0.0
        return {
            "test": "memory_soak_native_arena",
            "duration_s": round(elapsed, 3),
            "total_ops": count,
            "throughput_ops_per_sec": round(count / elapsed, 1),
            "rss_start_mb": rss_samples[0]["rss_mb"] if rss_samples else None,
            "rss_end_mb": rss_samples[-1]["rss_mb"] if rss_samples else None,
            "rss_delta_mb": round(rss_delta_mb, 3),
            "rss_samples": rss_samples[:10],
            "status": "PASS" if rss_delta_mb <= 32.0 else "FAILED",
        }


class FuzzingRunner:
    """Tests Rust mmap validation, PyO3 FFI error boundaries, and Rust Wasmtime sandbox traps."""

    def __init__(self, tmpdir: Path) -> None:
        self.tmpdir = tmpdir
        self.native = native_module()
        self.crashes: list[dict[str, str]] = []

    def fuzz_mmap_boundary(self) -> dict[str, Any]:
        valid_path = self.tmpdir / "valid_bridge.aegmmap"
        self.native.aegis_write_mmap_bridge_pattern(str(valid_path), 1, 2, 4096)
        valid_bytes = valid_path.read_bytes()

        cases = [
            ("valid_frame", valid_bytes, True),
            ("empty_file", b"", False),
            ("truncated_header", valid_bytes[:40], False),
            ("bad_magic", b"BAD!" + valid_bytes[4:], False),
            ("flipped_payload_bit", valid_bytes[:-1] + bytes([valid_bytes[-1] ^ 0xFF]), False),
            ("zero_fill_4k", b"\x00" * 4096, False),
        ]
        failures: list[dict[str, Any]] = []
        passed = 0
        for name, data, expected_valid in cases:
            path = self.tmpdir / f"fuzz_{name}.aegmmap"
            path.write_bytes(data)
            try:
                actual = bool(self.native.aegis_validate_mmap_bridge_frame(str(path)))
                if actual == expected_valid:
                    passed += 1
                else:
                    failures.append({"case": name, "expected": expected_valid, "actual": actual})
            except Exception:
                if not expected_valid:
                    passed += 1
                else:
                    failures.append({"case": name, "error": "unexpected exception on valid frame"})

        if failures:
            self.crashes.extend({"case": f["case"], "error": json.dumps(f)} for f in failures)
        return {
            "test": "fuzz_mmap_boundary_native",
            "total_cases": len(cases),
            "passed": passed,
            "failures": failures,
            "status": "PASS" if not failures else "FAILED",
        }

    def fuzz_wasm_modules(self) -> dict[str, Any]:
        # Test native Rust Wasmtime mmap bridge frame rejection on corrupted payloads.
        malformed_path = self.tmpdir / "malformed_wasm_frame.aegmmap"
        self.native.aegis_write_mmap_bridge_pattern(str(malformed_path), 9, 9, 64)
        rejected = 0
        try:
            self.native.aegis_execute_mmap_wasm_bridge_frame(str(malformed_path), 10_000)
        except Exception:
            rejected += 1

        # Optional Python `wasmtime` binding check if installed; otherwise explicit NOT VERIFIED.
        py_wasmtime_status = "NOT VERIFIED (python wasmtime package not installed; covered by Rust cargo-fuzz)"
        try:
            from wasmtime import Engine, Module  # type: ignore[import-not-found]

            engine = Engine()
            Module(engine, b"\x00asm\x01\x00\x00\x00")
            try:
                Module(engine, b"\x00\x00\x00\x00")
                py_wasmtime_status = "FAILED (accepted invalid magic)"
            except Exception:
                py_wasmtime_status = "PASS"
        except ImportError:
            pass

        return {
            "test": "fuzz_wasm_modules",
            "native_wasm_bridge_invalid_rejected": rejected == 1,
            "python_wasmtime_status": py_wasmtime_status,
            "status": "PASS" if rejected == 1 else "FAILED",
        }

    def ffi_payload_injection(self) -> dict[str, Any]:
        attacks = [
            ("oversized_json_admission", "{" + '"x":' * 10 + "1" + "}" * 10, True),
            ("invalid_trust_level", ("hello", "UNTRUSTED_TIER"), True),
            ("unaligned_truncated_mmap", b"\x00" * 113, True),
        ]
        blocked = 0
        results: list[dict[str, Any]] = []
        for name, payload, must_reject in attacks:
            rejected = False
            try:
                if name == "oversized_json_admission":
                    self.native.aegis_resource_admission_preview(str(payload) * 20_000, 1)
                elif name == "invalid_trust_level":
                    self.native.aegis_hot_commit(b"hello", "UNTRUSTED_TIER")
                else:
                    p = self.tmpdir / "unaligned.aegmmap"
                    p.write_bytes(bytes(payload))  # type: ignore[arg-type]
                    if not self.native.aegis_validate_mmap_bridge_frame(str(p)):
                        rejected = True
            except Exception:
                rejected = True
            if rejected == must_reject:
                blocked += 1
            results.append({"case": name, "rejected": rejected, "expected_reject": must_reject})
        return {
            "test": "ffi_payload_injection",
            "total": len(attacks),
            "blocked": blocked,
            "findings": results,
            "status": "PASS" if blocked == len(attacks) else "FAILED",
        }


class ChaosRunner:
    """Cross-platform crash and disk-boundary verification against real lab event verification."""

    def __init__(self, tmpdir: Path) -> None:
        self.tmpdir = tmpdir

    def test_sigkill_during_write(self) -> dict[str, Any]:
        chain_file = self.tmpdir / "chaos_chain.log"
        child_script = self.tmpdir / "chaos_child.py"
        child_script.write_text(
            "\n".join(
                [
                    "import hashlib, os, sys, time",
                    f"out = {chain_file.as_posix()!r}",
                    "for i in range(600):",
                    "    h = hashlib.blake2b(os.urandom(128), digest_size=32).hexdigest()",
                    "    with open(out, 'a', encoding='utf-8') as f:",
                    "        f.write(f'{i}:{h}\\n')",
                    "        f.flush()",
                    "    if i == 250:",
                    "        sys.stdout.write('READY\\n')",
                    "        sys.stdout.flush()",
                    "        time.sleep(2.0)",
                ]
            ),
            encoding="utf-8",
        )
        proc = subprocess.Popen(
            [sys.executable, str(child_script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = proc.stdout.readline() if proc.stdout else ""
            if "READY" not in ready:
                proc.kill()
                proc.wait(timeout=5)
                return {"test": "sigkill_during_write", "status": "FAILED", "reason": "child did not reach READY"}
            sig = getattr(signal, "SIGKILL", None)
            if sig is not None:
                os.kill(proc.pid, sig)
            else:
                proc.kill()
            proc.wait(timeout=5)
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        lines = chain_file.read_text(encoding="utf-8").splitlines() if chain_file.exists() else []
        return {
            "test": "sigkill_during_write",
            "records_persisted_before_kill": len(lines),
            "status": "PASS" if len(lines) >= 250 else "FAILED",
        }

    def test_disk_full_simulation(self) -> dict[str, Any]:
        # Verify MmapBridgeFrame rejects truncated backing file rather than panicking.
        path = self.tmpdir / "truncated.aegmmap"
        path.write_bytes(b"AEGMMAP1" + b"\x00" * 16)
        rejected = False
        try:
            with MmapBridgeFrame(path):
                pass
        except ValueError, OSError:
            rejected = True
        return {
            "test": "truncated_mmap_recovery_fail_closed",
            "rejected_corrupt_header": rejected,
            "status": "PASS" if rejected else "FAILED",
        }


class SecurityRunner:
    """Tests native BLAKE3 integrity tamper detection, path traversal guards, and PROD fail-closed enforcement."""

    def __init__(self, tmpdir: Path) -> None:
        self.tmpdir = tmpdir
        self.native = native_module()
        self.vulnerabilities: list[dict[str, str]] = []

    def test_crypto_tampering(self) -> dict[str, Any]:
        frame_path = self.tmpdir / "tamper_check.aegmmap"
        self.native.aegis_write_mmap_bridge_pattern(str(frame_path), 11, 22, 4096)
        valid_before = bool(self.native.aegis_validate_mmap_bridge_frame(str(frame_path)))
        raw = bytearray(frame_path.read_bytes())
        raw[-128] ^= 0x01
        frame_path.write_bytes(bytes(raw))
        valid_after = bool(self.native.aegis_validate_mmap_bridge_frame(str(frame_path)))
        detected = valid_before and not valid_after
        if not detected:
            self.vulnerabilities.append({"id": "SEC-001", "severity": "CRITICAL", "title": "Mmap tamper undetected"})
        return {
            "test": "crypto_tampering_native_mmap",
            "tamper_detected": detected,
            "status": "PASS" if detected else "FAILED",
        }

    def test_trust_level_bypass(self) -> dict[str, Any]:
        # Verify PROD fails closed when an invalid/untrusted level string is passed to Rust PyO3 FFI.
        rejected_invalid = False
        try:
            self.native.aegis_hot_commit(b"payload", "BOGUS_LEVEL")
        except Exception:
            rejected_invalid = True

        prod_raw = json.loads(self.native.aegis_hot_commit(b"payload", "PROD"))
        fail_closed_flag = bool(prod_raw.get("fail_closed")) and bool(prod_raw.get("physical_witness_required"))
        ok = rejected_invalid and fail_closed_flag
        if not ok:
            self.vulnerabilities.append({"id": "SEC-002", "severity": "CRITICAL", "title": "Trust enforcement failed"})
        return {
            "test": "trust_level_fail_closed",
            "rejected_invalid": rejected_invalid,
            "status": "PASS" if ok else "FAILED",
        }


def generate_report(all_results: dict[str, Any], total_duration: float) -> dict[str, Any]:
    return {
        "schema": "aegis-extreme-testing-suite-v2",
        "truth_claim": False,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "duration_s": round(total_duration, 3),
        "results": all_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AEGIS Extreme Testing Suite")
    parser.add_argument("--phase", choices=["load", "fuzz", "chaos", "security", "all"], default="all")
    parser.add_argument("--quick", action="store_true", help="Fast mode (fewer iterations)")
    parser.add_argument("--output", type=Path, default=ARTIFACTS_DIR / "extreme_testing_report.json")
    args = parser.parse_args()

    start_time = time.perf_counter()
    all_results: dict[str, Any] = {}
    vulnerabilities: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="aegis-extreme-suite-") as tmp:
        tmpdir = Path(tmp)
        if args.phase in ("load", "all"):
            runner = LoadTestRunner(tmpdir)
            all_results["load"] = {
                "backpressure_flood": runner.test_backpressure_flood(num_payloads=500 if args.quick else 3_000),
                "memory_soak": runner.test_memory_soak(duration_seconds=2 if args.quick else 5),
            }
        if args.phase in ("fuzz", "all"):
            fuzz = FuzzingRunner(tmpdir)
            all_results["fuzz"] = {
                "fuzz_mmap": fuzz.fuzz_mmap_boundary(),
                "fuzz_wasm": fuzz.fuzz_wasm_modules(),
                "ffi_injection": fuzz.ffi_payload_injection(),
            }
            if fuzz.crashes:
                all_results["crashes"] = fuzz.crashes
        if args.phase in ("chaos", "all"):
            chaos = ChaosRunner(tmpdir)
            all_results["chaos"] = {
                "sigkill": chaos.test_sigkill_during_write(),
                "disk_full": chaos.test_disk_full_simulation(),
            }
        if args.phase in ("security", "all"):
            sec = SecurityRunner(tmpdir)
            all_results["security"] = {
                "crypto_tamper": sec.test_crypto_tampering(),
                "trust_bypass": sec.test_trust_level_bypass(),
            }
            vulnerabilities.extend(sec.vulnerabilities)

    report = generate_report(all_results, time.perf_counter() - start_time)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 1 if vulnerabilities else 0


if __name__ == "__main__":
    raise SystemExit(main())
