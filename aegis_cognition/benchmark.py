"""Strict, contamination-aware benchmark primitives for AEGIS Lab."""

from __future__ import annotations

import contextlib
import math
import json
import locale
import os
import platform
import statistics
import subprocess
from dataclasses import asdict, dataclass, is_dataclass
from hashlib import blake2b
from collections.abc import Sequence
from typing import Any, cast


_TRIAL_STATUSES = frozenset({"SUCCESS", "FAIL", "TIMEOUT", "REFUSAL", "CANCELLED", "ERROR"})


def _digest(value: Any) -> str:
    payload = asdict(cast(Any, value)) if is_dataclass(value) else value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return blake2b(encoded, digest_size=32).hexdigest()


@dataclass(frozen=True)
class BenchmarkProtocolV2:
    name: str
    metric: str
    direction: str = "higher_is_better"
    alpha: float = 0.05
    min_trials: int = 30
    warmups: int = 10
    paired_blocks: int = 30
    preregistered_seeds: tuple[int, ...] = ()
    frozen_split_hash: str = ""
    contamination_checks: tuple[str, ...] = ()
    percentile: int | None = None

    def validate(self) -> None:
        if not self.name.strip() or not self.metric.strip():
            raise ValueError("benchmark name and metric are required")
        if self.direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("benchmark direction must be explicit")
        if not 0 < self.alpha < 0.5:
            raise ValueError("alpha must be in (0, 0.5)")
        if self.min_trials < 30 or self.warmups < 10 or self.paired_blocks < 30:
            raise ValueError("strict benchmark requires >=30 trials, >=10 warmups and >=30 paired blocks")
        if len(self.preregistered_seeds) < 5:
            raise ValueError("stochastic benchmark requires at least five preregistered seeds")
        if any(type(seed) is not int for seed in self.preregistered_seeds):
            raise ValueError("preregistered benchmark seeds must be integers")
        if len(set(self.preregistered_seeds)) != len(self.preregistered_seeds):
            raise ValueError("preregistered benchmark seeds must be unique")
        if not self.frozen_split_hash.strip():
            raise ValueError("frozen split hash is required")
        if not self.contamination_checks or any(not item.strip() for item in self.contamination_checks):
            raise ValueError("contamination checks must be declared")
        if self.percentile is not None and self.percentile not in {50, 90, 95, 99}:
            raise ValueError("only declared percentile metrics are supported")
        if self.percentile == 99 and self.min_trials < 1000:
            raise ValueError("p99 requires at least 1000 valid observations")

    @property
    def protocol_hash(self) -> str:
        self.validate()
        return _digest(self)


@dataclass(frozen=True)
class EnvironmentFingerprint:
    """Stable, explicit environment binding for benchmark evidence."""

    os_name: str
    os_release: str
    architecture: str
    python_version: str
    cpu_model: str
    logical_cpus: int
    container_image: str
    gpu_driver: str
    locale_name: str
    network_policy: str

    @classmethod
    def capture(
        cls,
        *,
        network_policy: str,
        container_image: str | None = None,
        gpu_driver: str | None = None,
    ) -> EnvironmentFingerprint:
        """Capture deterministic host metadata; no wall-clock value is included."""

        detected_locale = locale.getpreferredencoding(False) or "unknown"
        return cls(
            os_name=platform.system() or "unknown",
            os_release=platform.release() or "unknown",
            architecture=platform.machine() or "unknown",
            python_version=platform.python_version(),
            cpu_model=platform.processor() or "unknown",
            logical_cpus=os.cpu_count() or 1,
            container_image=container_image or os.environ.get("AEGIS_CONTAINER_IMAGE", "unknown"),
            gpu_driver=gpu_driver or os.environ.get("AEGIS_GPU_DRIVER", "unknown"),
            locale_name=detected_locale,
            network_policy=network_policy,
        )

    def validate(self) -> None:
        fields = (
            self.os_name,
            self.os_release,
            self.architecture,
            self.python_version,
            self.cpu_model,
            self.container_image,
            self.gpu_driver,
            self.locale_name,
            self.network_policy,
        )
        if any(not field.strip() for field in fields) or self.logical_cpus < 1:
            raise ValueError("benchmark environment fingerprint is incomplete")

    @property
    def environment_hash(self) -> str:
        self.validate()
        return _digest({"schema": "aegis-environment-fingerprint-v1", **asdict(self)})


@dataclass(frozen=True)
class BenchmarkTrialRecord:
    """Immutable raw record for one item/trial/seed attempt."""

    item_id: str
    trial_index: int
    seed: int
    status: str = "SUCCESS"
    value: float | None = None
    error_class: str = ""
    artifact_hash: str = ""

    def validate(self) -> None:
        if (
            not self.item_id.strip()
            or type(self.trial_index) is not int
            or self.trial_index < 0
            or type(self.seed) is not int
            or self.status not in _TRIAL_STATUSES
        ):
            raise ValueError("benchmark trial identity or status is invalid")
        if self.status == "SUCCESS":
            if self.value is None or not math.isfinite(float(self.value)):
                raise ValueError("successful benchmark trial requires a finite value")
        elif self.value is not None:
            raise ValueError("failed benchmark trial cannot carry a metric value")
        if self.status != "SUCCESS" and not self.error_class.strip():
            raise ValueError("failed benchmark trial requires an error class")

    @property
    def key(self) -> tuple[str, int, int]:
        return self.item_id, self.trial_index, self.seed


@dataclass(frozen=True)
class BenchmarkResultV2:
    protocol_hash: str
    status: str
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    trial_count: int
    warmup_count: int
    paired_block_count: int
    raw_trial_hash: str
    contamination_flags: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    truth_claim: bool = False
    raw_trials: tuple[float, ...] = ()
    environment_hash: str = ""
    validator_hash: str = ""
    artifact_hash: str = ""
    valid_trial_count: int = 0
    raw_records: tuple[BenchmarkTrialRecord, ...] = ()
    validator_version: str = ""


def _run_isolated_validator(
    command: Sequence[str] | str | bytes,
    *,
    values: tuple[float, ...],
    protocol_hash: str,
    raw_trial_hash: str,
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[bool, str, str]:
    """Run a harness-owned validator outside the candidate process.

    The command is deliberately an argv sequence and is never passed through a
    shell.  The validator receives a versioned JSON envelope and must return a
    single versioned JSON object binding its verdict to the exact input hash.
    This is process isolation for the local harness, not a claim of hosted
    secrecy or OS sandboxing; the command must be injected by the benchmark
    operator, never by model output.
    """

    if isinstance(command, (str, bytes)):
        raise ValueError("hidden validator command must be a non-empty argv sequence")
    raw_command = tuple(cast(Sequence[Any], command))
    if not raw_command or any(type(part) is not str or not part.strip() for part in raw_command):
        raise ValueError("hidden validator command must be a non-empty argv sequence")
    argv = tuple(cast(str, part) for part in raw_command)
    if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
        raise ValueError("hidden validator timeout must be finite and positive")
    raw_output_limit: Any = max_output_bytes
    if type(raw_output_limit) is not int or not 1 <= raw_output_limit <= 1_048_576:
        raise ValueError("hidden validator output limit is invalid")

    input_hash = _digest(values)
    payload = json.dumps(
        {
            "schema": "aegis-hidden-validator-input-v1",
            "protocol_hash": protocol_hash,
            "raw_trial_hash": raw_trial_hash,
            "values": values,
            "input_hash": input_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            shell=False,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        stdout, stderr = process.communicate(payload, timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired as exc:
        assert process is not None
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=1.0)
        raise TimeoutError("hidden validator timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"hidden validator execution failed: {type(exc).__name__}") from exc
    assert process is not None
    if len(stdout.encode("utf-8")) > max_output_bytes or len(stderr.encode("utf-8")) > max_output_bytes:
        raise RuntimeError("hidden validator output exceeded limit")
    if process.returncode != 0:
        raise RuntimeError(f"hidden validator exited with status {process.returncode}")
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("hidden validator returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ValueError("hidden validator result must be an object")
    result_mapping = cast(dict[str, Any], result)
    if result_mapping.get("schema") != "aegis-hidden-validator-result-v1":
        raise ValueError("hidden validator result schema is invalid")
    if not isinstance(result_mapping.get("valid"), bool):
        raise ValueError("hidden validator result verdict must be boolean")
    if result_mapping.get("input_hash") != input_hash:
        raise ValueError("hidden validator input hash mismatch")
    validator_version = result_mapping.get("validator_version")
    if not isinstance(validator_version, str) or not validator_version.strip():
        raise ValueError("hidden validator version is required")
    return (
        cast(bool, result_mapping["valid"]),
        validator_version,
        _digest(
            {
                "kind": "isolated-process",
                "command": list(argv),
                "validator_version": validator_version,
                "input_hash": input_hash,
            }
        ),
    )


def _normalize_trial_records(
    protocol: BenchmarkProtocolV2,
    trials: list[Any] | tuple[Any, ...],
) -> tuple[BenchmarkTrialRecord, ...]:
    """Normalize scalar compatibility inputs without dropping failed attempts."""

    records: list[BenchmarkTrialRecord] = []
    seeds = protocol.preregistered_seeds or (0,)
    for index, raw in enumerate(trials):
        if isinstance(raw, BenchmarkTrialRecord):
            record = raw
        elif isinstance(raw, dict):
            mapping = cast(dict[str, Any], raw)
            status = str(mapping.get("status", "SUCCESS")).upper()
            raw_value = mapping.get("value", mapping.get("metric"))
            value: float | None
            error_class = str(mapping.get("error_class", ""))
            if status == "SUCCESS":
                try:
                    if raw_value is None:
                        raise TypeError("missing metric value")
                    value = float(raw_value)
                except (TypeError, ValueError, OverflowError):
                    value = None
                    status = "ERROR"
                    error_class = error_class or "non_numeric_value"
            else:
                value = None
            record = BenchmarkTrialRecord(
                item_id=str(mapping.get("item_id", f"item-{index}")),
                trial_index=int(mapping.get("trial_index", index)),
                seed=int(mapping.get("seed", seeds[index % len(seeds)])),
                status=status,
                value=value,
                error_class=error_class,
                artifact_hash=str(mapping.get("artifact_hash", "")),
            )
        else:
            try:
                value = float(raw)
                status = "SUCCESS"
                error_class = ""
            except (TypeError, ValueError, OverflowError):
                value = None
                status = "ERROR"
                error_class = "non_numeric_value"
            record = BenchmarkTrialRecord(
                item_id=f"item-{index}",
                trial_index=index,
                seed=seeds[index % len(seeds)],
                status=status,
                value=value,
                error_class=error_class,
            )
        record.validate()
        if record.seed not in protocol.preregistered_seeds:
            raise ValueError("benchmark trial seed is not preregistered")
        records.append(record)
    keys = [record.key for record in records]
    if len(keys) != len(set(keys)):
        raise ValueError("benchmark trial identity is duplicated")
    return tuple(records)


def evaluate_benchmark(
    protocol: BenchmarkProtocolV2,
    trials: list[Any] | tuple[Any, ...],
    *,
    baseline: float | None = None,
    contamination_flags: tuple[str, ...] = (),
    paired_blocks: int | None = None,
    warmups: int | None = None,
    environment_hash: str = "",
    environment: EnvironmentFingerprint | None = None,
    validator: Any | None = None,
    validator_command: Sequence[str] | None = None,
    validator_timeout_seconds: float = 5.0,
    validator_max_output_bytes: int = 65_536,
) -> BenchmarkResultV2:
    """Evaluate raw trials conservatively using a normal CI.

    The result is a measurement record, never a truth claim. Any protocol or
    data deficiency produces ``REJECTED`` and an explicit failure reason.
    """

    failures: list[str] = []
    if environment is not None:
        try:
            captured_hash = environment.environment_hash
            if environment_hash and environment_hash != captured_hash:
                failures.append("environment_hash_mismatch")
            environment_hash = captured_hash
        except ValueError as exc:
            failures.append(str(exc))
    try:
        protocol.validate()
    except ValueError as exc:
        failures.append(str(exc))
    records: tuple[BenchmarkTrialRecord, ...]
    try:
        records = _normalize_trial_records(protocol, trials)
    except (TypeError, ValueError, OverflowError) as exc:
        records = ()
        failures.append(f"raw_trial_records_invalid:{type(exc).__name__}")
    values = tuple(record.value for record in records if record.status == "SUCCESS" and record.value is not None)
    if not values:
        failures.append("raw_trials_empty_or_non_finite")
    if len(values) < protocol.min_trials:
        failures.append("insufficient_trials")
    if records and any(record.status != "SUCCESS" for record in records):
        failures.append("trial_failures_present")
    actual_warmups = protocol.warmups if warmups is None else warmups
    actual_pairs = protocol.paired_blocks if paired_blocks is None else paired_blocks
    if actual_warmups < protocol.warmups:
        failures.append("insufficient_warmups")
    if actual_pairs < protocol.paired_blocks:
        failures.append("insufficient_paired_blocks")
    if contamination_flags:
        failures.append("contamination_detected")
    if baseline is not None and not math.isfinite(float(baseline)):
        failures.append("baseline_non_finite")
    if not environment_hash.strip():
        failures.append("environment_hash_missing")

    validator_hash = ""
    validator_version = ""
    if validator is not None and validator_command is not None:
        failures.append("multiple_validator_modes")
    if validator is not None:
        if not callable(validator):
            failures.append("hidden_validator_not_callable")
        else:
            validator_hash = _digest({"validator": getattr(validator, "__qualname__", repr(validator))})
            try:
                if not bool(validator(values)):
                    failures.append("hidden_validator_rejected")
            except Exception as exc:  # validator failures are explicit benchmark failures
                failures.append(f"hidden_validator_failed:{type(exc).__name__}")
    elif validator_command is not None:
        try:
            validated, validator_version, validator_hash = _run_isolated_validator(
                validator_command,
                values=values,
                protocol_hash=protocol.protocol_hash,
                raw_trial_hash=_digest(records),
                timeout_seconds=validator_timeout_seconds,
                max_output_bytes=validator_max_output_bytes,
            )
            if not validated:
                failures.append("hidden_validator_rejected")
        except (RuntimeError, TimeoutError, ValueError) as exc:
            failures.append(f"hidden_validator_failed:{type(exc).__name__}")

    estimate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    if values:
        estimate = statistics.quantiles(values, n=100, method="inclusive")[protocol.percentile - 1] if protocol.percentile else statistics.fmean(values)
        if protocol.percentile is None and len(values) > 1:
            standard_error = statistics.stdev(values) / math.sqrt(len(values))
            margin = 1.96 * standard_error
            ci_low, ci_high = estimate - margin, estimate + margin
        else:
            ci_low = ci_high = estimate
    if baseline is not None and estimate is not None:
        if protocol.direction == "higher_is_better" and ci_low is not None and ci_low <= baseline:
            failures.append("confidence_interval_does_not_clear_baseline")
        if protocol.direction == "lower_is_better" and ci_high is not None and ci_high >= baseline:
            failures.append("confidence_interval_does_not_clear_baseline")
    status = "REJECTED" if failures else "PASS"
    try:
        protocol_hash = protocol.protocol_hash
    except ValueError:
        protocol_hash = _digest(protocol)
    raw_trial_hash = _digest(records)
    artifact_hash = _digest(
        {
            "schema": "aegis-benchmark-artifact-v2",
            "protocol_hash": protocol_hash,
            "raw_records": records,
            "baseline": baseline,
            "environment_hash": environment_hash,
            "validator_hash": validator_hash,
            "validator_version": validator_version,
            "contamination_flags": contamination_flags,
            "status": status,
        }
    )
    return BenchmarkResultV2(
        protocol_hash=protocol_hash,
        status=status,
        estimate=estimate,
        ci_low=ci_low,
        ci_high=ci_high,
        trial_count=len(records),
        warmup_count=actual_warmups,
        paired_block_count=actual_pairs,
        raw_trial_hash=raw_trial_hash,
        contamination_flags=tuple(contamination_flags),
        failure_reasons=tuple(dict.fromkeys(failures)),
        raw_trials=values,
        environment_hash=environment_hash,
        validator_hash=validator_hash,
        artifact_hash=artifact_hash,
        valid_trial_count=len(values),
        raw_records=records,
        validator_version=validator_version,
    )


__all__ = ["BenchmarkProtocolV2", "BenchmarkResultV2", "BenchmarkTrialRecord", "evaluate_benchmark"]
