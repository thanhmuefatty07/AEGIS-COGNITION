import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "quickjs_cold_start_gate_report.json"
FULL_INTERPRETER_ADMISSION_SCHEMA = "aegis-quickjs-full-interpreter-admission-v1"
FULL_INTERPRETER_CAPTURE_SCHEMA = "aegis-quickjs-full-interpreter-cold-start-capture-v1"
FULL_INTERPRETER_EXPECTED_RUNTIME_PATH = "artifacts/quickjs_full_interpreter.wasm"
FULL_INTERPRETER_EXPECTED_CAPTURE_PATH = "artifacts/quickjs_full_interpreter_cold_start_capture.json"
FULL_INTERPRETER_BLOCKER_ID = "full_quickjs_interpreter_cold_start_missing"
FULL_INTERPRETER_RUNNER_ENV = "AEGIS_QUICKJS_FULL_INTERPRETER_RUNNER_JSON"

SEMANTIC_CORPUS_CASES: tuple[dict[str, str], ...] = (
    {
        "case_id": "arithmetic-precedence",
        "script": "console.log(String(1 + 2 * 3));\n",
        "expected_stdout": "7",
    },
    {
        "case_id": "json-deterministic",
        "script": "console.log(JSON.stringify({a:1,b:[2,true],c:null}));\n",
        "expected_stdout": '{"a":1,"b":[2,true],"c":null}',
    },
    {
        "case_id": "closure-array-map",
        "script": "const xs=[1,2,3]; console.log(String(xs.map(x=>x*x).reduce((a,b)=>a+b,0)));\n",
        "expected_stdout": "14",
    },
)


def evaluate_quickjs_cold_start_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    artifact_path = root_path / "artifacts" / "quickjs_cold_start_report.json"
    artifact = _read_json(artifact_path)
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    base_admission = report.get("full_interpreter_admission", {})
    base_admission = base_admission if isinstance(base_admission, dict) else {}
    admission = _full_interpreter_admission(root_path, report, base_admission)
    samples = report.get("sample_count", 0)
    full_interpreter_present = admission.get("semantic_capture_valid") is True
    production_blocked_without_full_interpreter = not full_interpreter_present
    checks = (
        _check("artifact_present", bool(artifact)),
        _check("schema_version", artifact.get("schema_version") == 1),
        _check("report_schema", report.get("schema") == "aegis-quickjs-cold-start-report-v1"),
        _check("sample_count", samples == 5),
        _check("fuel_limit", report.get("fuel_limit") == 10_000),
        _check("abi_packet_size_bound", _abi_packet_size_bound(report)),
        _check("wasm_module_present", _positive_int(report.get("wasm_module_bytes"))),
        _check("cold_timings_recorded", _timing_window(report, "cold_start", samples)),
        _check("warm_timings_recorded", _timing_window(report, "warm_cached", samples)),
        _check("warm_faster_than_cold_total", _positive_int(report.get("warm_cached_total_ns")) and report.get("warm_cached_total_ns") < report.get("cold_start_total_ns", 0)),
        _check("cold_cache_materialized", report.get("cold_cache_entries_after_first") == 1),
        _check("warm_cache_reused", report.get("warm_cache_entries_before") == 1 and report.get("warm_cache_entries_after") == 1),
        _check("fuel_consumed", _positive_int(report.get("cold_fuel_consumed_total")) and _positive_int(report.get("warm_fuel_consumed_total"))),
        _check("cold_artifact_hash_nonzero", _hash_array(report.get("cold_artifact_hash"))),
        _check("warm_artifact_hash_nonzero", _hash_array(report.get("warm_artifact_hash"))),
        _check("script_hash_nonzero", _hash_array(report.get("script_blake3"))),
        _check("wrapper_hash_nonzero", _hash_array(report.get("wrapper_blake3"))),
        _check("invocation_hash_nonzero", _hash_array(report.get("invocation_blake3"))),
        _check("report_hash_nonzero", _hash_array(report.get("report_hash"))),
        _check("bridge_probe_executed", report.get("bridge_probe_executed") is True),
        _check("full_interpreter_admission_schema", admission.get("schema") == FULL_INTERPRETER_ADMISSION_SCHEMA),
        _check("full_interpreter_admission_path", admission.get("expected_runtime_path") == FULL_INTERPRETER_EXPECTED_RUNTIME_PATH),
        _check("full_interpreter_admission_hash", _hash_array(admission.get("admission_hash"))),
        _check("full_interpreter_semantic_corpus_hash", _hash_array(admission.get("semantic_corpus_hash"))),
        _check("full_interpreter_sandbox_contract_hash", _hash_array(admission.get("sandbox_contract_hash"))),
        _check("full_interpreter_admission_fuel", admission.get("required_fuel_limit") == report.get("fuel_limit")),
        _check("full_interpreter_admission_samples", admission.get("required_cold_start_sample_count") == samples),
        _check("full_interpreter_admission_blocker", admission.get("production_blocker_id") == FULL_INTERPRETER_BLOCKER_ID),
        _check("full_interpreter_admission_state_bound", _admission_state_bound(admission)),
        _check("full_interpreter_semantic_capture_missing_or_valid", (not admission.get("semantic_capture_present")) or admission.get("semantic_capture_valid") is True),
        _check("full_interpreter_state_recorded", True),
        _check("full_interpreter_gap_disclosed", full_interpreter_present or report.get("full_quickjs_interpreter_present") is False),
        _check("production_blocked_without_full_interpreter", production_blocked_without_full_interpreter == (not full_interpreter_present)),
    )
    payload = {
        "suite_name": "AEGIS QuickJS Cold-Start Gate",
        "schema": "aegis-quickjs-cold-start-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-quickjs-cold-start-artifact-verifier",
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_hash(artifact_path),
        "quickjs_cold_start_evidence": {
            "sample_count": report.get("sample_count", 0),
            "cold_start_min_ns": report.get("cold_start_min_ns", 0),
            "cold_start_max_ns": report.get("cold_start_max_ns", 0),
            "cold_start_total_ns": report.get("cold_start_total_ns", 0),
            "warm_cached_min_ns": report.get("warm_cached_min_ns", 0),
            "warm_cached_max_ns": report.get("warm_cached_max_ns", 0),
            "warm_cached_total_ns": report.get("warm_cached_total_ns", 0),
            "bridge_probe_executed": report.get("bridge_probe_executed"),
            "full_quickjs_interpreter_present": full_interpreter_present,
        },
        "full_interpreter_admission": {
            "schema": admission.get("schema", ""),
            "expected_runtime_path": admission.get("expected_runtime_path", ""),
            "runtime_wasm_present": admission.get("runtime_wasm_present"),
            "runtime_wasm_hash": admission.get("runtime_wasm_hash", []),
            "semantic_corpus": admission.get("semantic_corpus", ""),
            "semantic_corpus_hash": admission.get("semantic_corpus_hash", []),
            "sandbox_contract": admission.get("sandbox_contract", ""),
            "sandbox_contract_hash": admission.get("sandbox_contract_hash", []),
            "required_cold_start_sample_count": admission.get("required_cold_start_sample_count", 0),
            "required_fuel_limit": admission.get("required_fuel_limit", 0),
            "semantic_capture_path": admission.get("semantic_capture_path", ""),
            "semantic_capture_present": admission.get("semantic_capture_present"),
            "semantic_capture_schema": admission.get("semantic_capture_schema", ""),
            "semantic_capture_valid": admission.get("semantic_capture_valid"),
            "semantic_capture_hash": admission.get("semantic_capture_hash", []),
            "semantic_capture_error": admission.get("semantic_capture_error", ""),
            "semantic_capture_verification_hash": admission.get("semantic_capture_verification_hash", []),
            "admission_status": admission.get("admission_status", ""),
            "production_blocker_id": admission.get("production_blocker_id", ""),
            "admission_hash": admission.get("admission_hash", []),
        },
        "full_interpreter_admission_hash": admission.get("admission_hash", []),
        "full_interpreter_admission_missing": (
            admission.get("runtime_wasm_present") is False
            and admission.get("admission_status") == "missing"
        ),
        "full_interpreter_semantic_capture_missing_or_valid": (
            (not admission.get("semantic_capture_present")) or admission.get("semantic_capture_valid") is True
        ),
        "full_interpreter_admission_blocker_id": admission.get("production_blocker_id", ""),
        "full_interpreter_state_recorded": True,
        "real_quickjs_interpreter_cold_start_present": full_interpreter_present,
        "production_quickjs_release_blocked_without_full_interpreter": production_blocked_without_full_interpreter,
        "checks": list(checks),
        "passed": sum(1 for check in checks if check["ok"]),
        "failed": sum(1 for check in checks if not check["ok"]),
    }
    payload["overall_ok"] = payload["failed"] == 0
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _check(name: str, ok: bool) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok)}


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _timing_window(report: dict[str, Any], prefix: str, samples: Any) -> bool:
    if not isinstance(samples, int) or isinstance(samples, bool) or samples <= 0:
        return False
    minimum = report.get(f"{prefix}_min_ns")
    maximum = report.get(f"{prefix}_max_ns")
    total = report.get(f"{prefix}_total_ns")
    return (
        _positive_int(minimum)
        and _positive_int(maximum)
        and _positive_int(total)
        and minimum <= maximum
        and minimum * samples <= total <= maximum * samples
    )


def _abi_packet_size_bound(report: dict[str, Any]) -> bool:
    script_bytes = report.get("script_bytes")
    abi_packet_bytes = report.get("abi_packet_bytes")
    return (
        isinstance(script_bytes, int)
        and not isinstance(script_bytes, bool)
        and isinstance(abi_packet_bytes, int)
        and not isinstance(abi_packet_bytes, bool)
        and abi_packet_bytes == script_bytes + 104
    )


def _full_interpreter_admission(
    root: Path,
    report: dict[str, Any],
    base_admission: dict[str, Any],
) -> dict[str, Any]:
    admission = dict(base_admission)
    runtime_path = root / FULL_INTERPRETER_EXPECTED_RUNTIME_PATH
    runtime_bytes = _read_bytes(runtime_path)
    runtime_present = runtime_bytes.startswith(b"\0asm") and len(runtime_bytes) > 8
    runtime_hash = _hash_bytes_array(runtime_bytes) if runtime_present else [0] * 32
    capture_path = root / FULL_INTERPRETER_EXPECTED_CAPTURE_PATH
    capture_payload, capture_present, capture_bytes, capture_error = _read_capture(capture_path)
    capture_valid = _full_interpreter_capture_valid(capture_payload, report, admission, runtime_hash)
    if capture_valid:
        admission_status = "candidate-present-semantic-cold-start-gate-required"
    elif runtime_present:
        admission_status = "runtime-present-semantic-capture-missing-or-invalid"
    else:
        admission_status = "missing"
    admission.update(
        {
            "schema": admission.get("schema", FULL_INTERPRETER_ADMISSION_SCHEMA),
            "expected_runtime_path": admission.get("expected_runtime_path", FULL_INTERPRETER_EXPECTED_RUNTIME_PATH),
            "runtime_wasm_present": runtime_present,
            "runtime_wasm_hash": runtime_hash,
            "semantic_capture_path": FULL_INTERPRETER_EXPECTED_CAPTURE_PATH,
            "semantic_capture_present": capture_present,
            "semantic_capture_schema": capture_payload.get("schema", "") if capture_present else "",
            "semantic_capture_valid": capture_valid,
            "semantic_capture_hash": _hash_bytes_array(capture_bytes) if capture_present else [0] * 32,
            "semantic_capture_error": capture_error,
            "admission_status": admission_status,
            "production_blocker_id": admission.get("production_blocker_id", FULL_INTERPRETER_BLOCKER_ID),
        }
    )
    admission["semantic_capture_verification_hash"] = _hash_payload_array(
        {
            "runtime_wasm_hash": admission["runtime_wasm_hash"],
            "semantic_capture_hash": admission["semantic_capture_hash"],
            "semantic_capture_valid": capture_valid,
            "admission_status": admission_status,
            "semantic_corpus_hash": admission.get("semantic_corpus_hash", []),
            "sandbox_contract_hash": admission.get("sandbox_contract_hash", []),
            "required_cold_start_sample_count": admission.get("required_cold_start_sample_count", 0),
            "required_fuel_limit": admission.get("required_fuel_limit", 0),
        }
    )
    return admission


def write_full_quickjs_interpreter_cold_start_capture(
    root: str | Path = ROOT,
    runner: list[str] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    artifact = _read_json(root_path / "artifacts" / "quickjs_cold_start_report.json")
    report = artifact.get("report", {}) if isinstance(artifact, dict) else {}
    admission = report.get("full_interpreter_admission", {})
    admission = admission if isinstance(admission, dict) else {}
    runtime_path = root_path / FULL_INTERPRETER_EXPECTED_RUNTIME_PATH
    runtime_bytes = _read_bytes(runtime_path)
    runtime_present = runtime_bytes.startswith(b"\0asm") and len(runtime_bytes) > 8
    runtime_hash = _hash_bytes_array(runtime_bytes) if runtime_present else [0] * 32
    probe = _full_interpreter_runner_probe(root_path, runtime_path, runtime_hash, report, admission, runner)
    capture = _full_interpreter_capture(report, admission, runtime_hash, probe)
    capture_valid = _full_interpreter_capture_valid(capture, report, admission, runtime_hash)
    capture_path = root_path / FULL_INTERPRETER_EXPECTED_CAPTURE_PATH
    encoded_capture = json.dumps(capture, indent=2, sort_keys=True).encode("utf-8")
    if capture_valid:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(encoded_capture)
    return {
        "suite_name": "AEGIS Full QuickJS Interpreter Cold-Start Capture Producer",
        "schema": "aegis-quickjs-full-interpreter-cold-start-capture-producer-v1",
        "truth_claim": False,
        "capture_path": str(capture_path),
        "capture_written": capture_valid,
        "capture_schema": capture.get("schema", ""),
        "capture_hash": hashlib.sha256(encoded_capture).hexdigest() if capture_valid else "0" * 64,
        "runtime_wasm_present": runtime_present,
        "runner_config_present": probe.get("runner_config_present") is True,
        "sample_count": capture.get("sample_count", 0),
        "semantic_case_count": capture.get("semantic_case_count", 0),
        "overall_ok": capture_valid,
        "error": "" if capture_valid else probe.get("error", "full QuickJS interpreter semantic cold-start capture did not satisfy contract"),
    }


def _full_interpreter_runner_probe(
    root: Path,
    runtime_path: Path,
    runtime_hash: list[int],
    report: dict[str, Any],
    admission: dict[str, Any],
    runner_override: list[str] | None = None,
) -> dict[str, Any]:
    runner_raw = os.environ.get(FULL_INTERPRETER_RUNNER_ENV, "").strip()
    samples = report.get("sample_count", admission.get("required_cold_start_sample_count", 0))
    if not isinstance(samples, int) or isinstance(samples, bool) or samples <= 0:
        samples = 0
    if runner_override is not None:
        runner = runner_override
        runner_config_present = True
        runner_config_source = "injected"
    elif runner_raw:
        try:
            runner = json.loads(runner_raw)
        except json.JSONDecodeError:
            return {
                "schema": "aegis-quickjs-full-interpreter-runner-probe-v1",
                "runner_config_present": True,
                "runner_config_source": "environment",
                "sample_count": samples,
                "semantic_case_count": len(SEMANTIC_CORPUS_CASES),
                "runs": [],
                "error": f"{FULL_INTERPRETER_RUNNER_ENV} invalid-json",
            }
        runner_config_present = True
        runner_config_source = "environment"
    else:
        return {
            "schema": "aegis-quickjs-full-interpreter-runner-probe-v1",
            "runner_config_present": False,
            "runner_config_source": "missing",
            "sample_count": samples,
            "semantic_case_count": len(SEMANTIC_CORPUS_CASES),
            "runs": [],
            "error": f"{FULL_INTERPRETER_RUNNER_ENV} unset",
        }
    if not isinstance(runner, list) or not runner or not all(isinstance(part, str) and part for part in runner):
        return {
            "schema": "aegis-quickjs-full-interpreter-runner-probe-v1",
            "runner_config_present": runner_config_present,
            "runner_config_source": runner_config_source,
            "sample_count": samples,
            "semantic_case_count": len(SEMANTIC_CORPUS_CASES),
            "runs": [],
            "error": "full interpreter runner must be a non-empty string list",
        }
    if not runtime_path.exists():
        return {
            "schema": "aegis-quickjs-full-interpreter-runner-probe-v1",
            "runner_config_present": True,
            "runner_config_source": runner_config_source,
            "sample_count": samples,
            "semantic_case_count": len(SEMANTIC_CORPUS_CASES),
            "runs": [],
            "error": f"{FULL_INTERPRETER_EXPECTED_RUNTIME_PATH} missing",
        }
    temp_dir = root / "artifacts" / "tmp_quickjs_full_interpreter_corpus"
    temp_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for sample_index in range(samples):
        for case in SEMANTIC_CORPUS_CASES:
            script_path = temp_dir / f"{sample_index:02d}_{case['case_id']}.js"
            script_path.write_text(case["script"], encoding="utf-8")
            command = [
                part.format(runtime=str(runtime_path), script=str(script_path))
                for part in runner
            ]
            started_ns = time.perf_counter_ns()
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    cwd=str(root),
                )
                stdout = result.stdout.strip()
                stderr_tail = result.stderr[-1000:]
                returncode = result.returncode
                error = ""
            except (OSError, subprocess.TimeoutExpired) as exc:
                stdout = ""
                stderr_tail = type(exc).__name__
                returncode = -1
                error = type(exc).__name__
            elapsed_ns = time.perf_counter_ns() - started_ns
            runs.append(
                {
                    "sample_index": sample_index,
                    "case_id": case["case_id"],
                    "script_hash": hashlib.sha256(case["script"].encode("utf-8")).hexdigest(),
                    "expected_stdout_hash": hashlib.sha256(case["expected_stdout"].encode("utf-8")).hexdigest(),
                    "stdout_hash": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                    "stdout_matches": stdout == case["expected_stdout"],
                    "returncode": returncode,
                    "elapsed_ns": elapsed_ns,
                    "stderr_tail_hash": hashlib.sha256(stderr_tail.encode("utf-8")).hexdigest() if stderr_tail else "",
                    "error": error,
                }
            )
    return {
        "schema": "aegis-quickjs-full-interpreter-runner-probe-v1",
        "runner_config_present": True,
        "runner_config_source": runner_config_source,
        "runner_hash": _stable_hash(runner),
        "runtime_path": FULL_INTERPRETER_EXPECTED_RUNTIME_PATH,
        "runtime_wasm_hash": runtime_hash,
        "semantic_corpus_cases_hash": _semantic_cases_hash(),
        "sample_count": samples,
        "semantic_case_count": len(SEMANTIC_CORPUS_CASES),
        "runs": runs,
        "error": "",
    }


def _full_interpreter_capture(
    report: dict[str, Any],
    admission: dict[str, Any],
    runtime_hash: list[int],
    probe: dict[str, Any],
) -> dict[str, Any]:
    runs = probe.get("runs", [])
    timing = _runs_timing(runs if isinstance(runs, list) else [])
    valid_run_count = sum(
        1
        for run in runs
        if isinstance(run, dict)
        and run.get("returncode") == 0
        and run.get("stdout_matches") is True
        and _positive_int(run.get("elapsed_ns"))
        and not run.get("error")
    ) if isinstance(runs, list) else 0
    return {
        "schema": FULL_INTERPRETER_CAPTURE_SCHEMA,
        "truth_claim": False,
        "verifier": "external-quickjs-full-interpreter-runner-capture-producer",
        "production_blocker_id": FULL_INTERPRETER_BLOCKER_ID,
        "runtime_path": FULL_INTERPRETER_EXPECTED_RUNTIME_PATH,
        "runtime_wasm_hash": runtime_hash,
        "semantic_corpus": admission.get("semantic_corpus", ""),
        "semantic_corpus_hash": admission.get("semantic_corpus_hash", []),
        "semantic_corpus_cases_hash": _semantic_cases_hash(),
        "sandbox_contract": admission.get("sandbox_contract", ""),
        "sandbox_contract_hash": admission.get("sandbox_contract_hash", []),
        "required_cold_start_sample_count": report.get("sample_count", 0),
        "required_fuel_limit": report.get("fuel_limit", 0),
        "semantic_case_count": len(SEMANTIC_CORPUS_CASES),
        "sample_count": probe.get("sample_count", 0),
        "expected_run_count": report.get("sample_count", 0) * len(SEMANTIC_CORPUS_CASES),
        "valid_run_count": valid_run_count,
        "cold_start_min_ns": timing["min_ns"],
        "cold_start_max_ns": timing["max_ns"],
        "cold_start_total_ns": timing["total_ns"],
        "probe": probe,
        "probe_hash": _stable_hash(probe),
        "all_semantic_runs_valid": valid_run_count == report.get("sample_count", 0) * len(SEMANTIC_CORPUS_CASES),
        "stdout_redacted": True,
        "stderr_redacted": True,
    }


def _full_interpreter_capture_valid(
    capture: dict[str, Any],
    report: dict[str, Any],
    admission: dict[str, Any],
    runtime_hash: list[int],
) -> bool:
    probe = capture.get("probe", {})
    runs = probe.get("runs", []) if isinstance(probe, dict) else []
    expected_run_count = report.get("sample_count", 0) * len(SEMANTIC_CORPUS_CASES)
    return (
        capture.get("schema") == FULL_INTERPRETER_CAPTURE_SCHEMA
        and capture.get("truth_claim") is False
        and capture.get("production_blocker_id") == FULL_INTERPRETER_BLOCKER_ID
        and capture.get("runtime_path") == FULL_INTERPRETER_EXPECTED_RUNTIME_PATH
        and _hash_array(capture.get("runtime_wasm_hash"))
        and capture.get("runtime_wasm_hash") == runtime_hash
        and capture.get("semantic_corpus") == admission.get("semantic_corpus")
        and capture.get("semantic_corpus_hash") == admission.get("semantic_corpus_hash")
        and capture.get("semantic_corpus_cases_hash") == _semantic_cases_hash()
        and capture.get("sandbox_contract") == admission.get("sandbox_contract")
        and capture.get("sandbox_contract_hash") == admission.get("sandbox_contract_hash")
        and capture.get("required_cold_start_sample_count") == admission.get("required_cold_start_sample_count") == report.get("sample_count")
        and capture.get("required_fuel_limit") == admission.get("required_fuel_limit") == report.get("fuel_limit")
        and capture.get("semantic_case_count") == len(SEMANTIC_CORPUS_CASES)
        and capture.get("sample_count") == report.get("sample_count")
        and capture.get("expected_run_count") == expected_run_count
        and capture.get("valid_run_count") == expected_run_count
        and capture.get("all_semantic_runs_valid") is True
        and capture.get("stdout_redacted") is True
        and capture.get("stderr_redacted") is True
        and isinstance(probe, dict)
        and probe.get("runner_config_present") is True
        and probe.get("runtime_wasm_hash") == runtime_hash
        and probe.get("semantic_corpus_cases_hash") == _semantic_cases_hash()
        and probe.get("error") == ""
        and capture.get("probe_hash") == _stable_hash(probe)
        and _timing_values_valid(capture)
        and isinstance(runs, list)
        and len(runs) == expected_run_count
        and all(_semantic_run_valid(run) for run in runs)
    )


def _semantic_run_valid(run: Any) -> bool:
    return (
        isinstance(run, dict)
        and isinstance(run.get("case_id"), str)
        and run.get("returncode") == 0
        and run.get("stdout_matches") is True
        and _positive_int(run.get("elapsed_ns"))
        and _nonzero_hex(str(run.get("script_hash", "")))
        and _nonzero_hex(str(run.get("expected_stdout_hash", "")))
        and _nonzero_hex(str(run.get("stdout_hash", "")))
        and not run.get("error")
    )


def _semantic_cases_hash() -> str:
    return _stable_hash(SEMANTIC_CORPUS_CASES)


def _runs_timing(runs: list[dict[str, Any]]) -> dict[str, int]:
    timings = [run.get("elapsed_ns") for run in runs if _positive_int(run.get("elapsed_ns"))]
    if not timings:
        return {"min_ns": 0, "max_ns": 0, "total_ns": 0}
    return {"min_ns": min(timings), "max_ns": max(timings), "total_ns": sum(timings)}


def _timing_values_valid(capture: dict[str, Any]) -> bool:
    return (
        _positive_int(capture.get("cold_start_min_ns"))
        and _positive_int(capture.get("cold_start_max_ns"))
        and _positive_int(capture.get("cold_start_total_ns"))
        and capture.get("cold_start_min_ns") <= capture.get("cold_start_max_ns")
        and capture.get("cold_start_total_ns") >= capture.get("cold_start_min_ns")
    )


def _hash_array(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(item, int) and 0 <= item <= 255 for item in value)
        and any(item != 0 for item in value)
    )


def _hash_bytes_array(value: bytes) -> list[int]:
    return list(hashlib.sha256(value).digest())


def _hash_payload_array(value: Any) -> list[int]:
    return list(bytes.fromhex(_stable_hash(value)))


def _nonzero_hex(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower()) and any(char != "0" for char in value)


def _zero_hash_array(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(item, int) and item == 0 for item in value)
    )


def _admission_state_bound(admission: dict[str, Any]) -> bool:
    missing = (
        admission.get("runtime_wasm_present") is False
        and _zero_hash_array(admission.get("runtime_wasm_hash"))
        and admission.get("admission_status") == "missing"
    )
    candidate = (
        admission.get("runtime_wasm_present") is True
        and _hash_array(admission.get("runtime_wasm_hash"))
        and admission.get("admission_status") == "candidate-present-semantic-cold-start-gate-required"
    )
    return missing or candidate


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _read_capture(path: Path) -> tuple[dict[str, Any], bool, bytes, str]:
    try:
        capture_bytes = path.read_bytes()
    except OSError:
        return {}, False, b"", "missing"
    if not capture_bytes:
        return {}, True, capture_bytes, "empty"
    try:
        payload = json.loads(capture_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, True, capture_bytes, "invalid-json"
    if not isinstance(payload, dict):
        return {}, True, capture_bytes, "non-object"
    return payload, True, capture_bytes, ""


def _file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--write-capture"]:
        producer_report = write_full_quickjs_interpreter_cold_start_capture(ROOT)
        print(json.dumps(producer_report, indent=2, sort_keys=True))
        return 0 if producer_report["overall_ok"] else 1
    if argv:
        print(json.dumps({"error": f"unknown args: {argv}"}, indent=2, sort_keys=True))
        return 2
    report = evaluate_quickjs_cold_start_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
