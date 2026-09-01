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


def _is_exact_int(value: Any) -> bool:
    """Accept integer metadata without allowing bool-as-int coercion."""

    return type(value) is int


def _is_finite_real(value: Any) -> bool:
    """Accept only finite JSON-like numeric values, never bool or strings."""

    if type(value) not in (int, float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


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
        if (
            type(self.name) is not str
            or type(self.metric) is not str
            or type(self.direction) is not str
            or type(self.frozen_split_hash) is not str
            or not self.name.strip()
            or not self.metric.strip()
            or not self.frozen_split_hash.strip()
        ):
            raise ValueError("benchmark name and metric are required")
        if self.direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("benchmark direction must be explicit")
        if not _is_finite_real(self.alpha) or not 0 < self.alpha < 0.5:
            raise ValueError("alpha must be in (0, 0.5)")
        if (
            not _is_exact_int(self.min_trials)
            or not _is_exact_int(self.warmups)
            or not _is_exact_int(self.paired_blocks)
            or self.min_trials < 30
            or self.warmups < 10
            or self.paired_blocks < 30
        ):
            raise ValueError("strict benchmark requires >=30 trials, >=10 warmups and >=30 paired blocks")
        if type(self.preregistered_seeds) not in (list, tuple):
            raise ValueError("preregistered benchmark seeds must be an integer sequence")
        if len(self.preregistered_seeds) < 5:
            raise ValueError("stochastic benchmark requires at least five preregistered seeds")
        if any(not _is_exact_int(seed) for seed in self.preregistered_seeds):
            raise ValueError("preregistered benchmark seeds must be integers")
        if len(set(self.preregistered_seeds)) != len(self.preregistered_seeds):
            raise ValueError("preregistered benchmark seeds must be unique")
        if type(self.contamination_checks) not in (list, tuple):
            raise ValueError("contamination checks must be a string sequence")
        if not self.contamination_checks or any(
            type(item) is not str or not item.strip() for item in self.contamination_checks
        ):
            raise ValueError("contamination checks must be declared")
        if self.percentile is not None and (
            not _is_exact_int(self.percentile)
            or self.percentile not in {50, 90, 95, 99}
        ):
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
        if (
            any(type(field) is not str or not field.strip() for field in fields)
            or not _is_exact_int(self.logical_cpus)
            or self.logical_cpus < 1
        ):
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
            type(self.item_id) is not str
            or type(self.status) is not str
            or type(self.error_class) is not str
            or type(self.artifact_hash) is not str
            or not self.item_id.strip()
            or type(self.trial_index) is not int
            or self.trial_index < 0
            or type(self.seed) is not int
            or self.status not in _TRIAL_STATUSES
        ):
            raise ValueError("benchmark trial identity or status is invalid")
        if self.status == "SUCCESS":
            if self.value is None or not _is_finite_real(self.value):
                raise ValueError("successful benchmark trial requires a finite value")
        elif self.value is not None:
            if not _is_finite_real(self.value):
                raise ValueError("failed benchmark trial cannot carry a non-numeric value")
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

    if type(command) not in (list, tuple):
        raise ValueError("hidden validator command must be a non-empty argv sequence")
    raw_command = tuple(cast(Sequence[Any], command))
    if not raw_command or any(type(part) is not str or not part.strip() for part in raw_command):
        raise ValueError("hidden validator command must be a non-empty argv sequence")
    argv = tuple(cast(str, part) for part in raw_command)
    if not _is_finite_real(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("hidden validator timeout must be finite and positive")
    raw_output_limit: Any = max_output_bytes
    if not _is_exact_int(raw_output_limit) or not 1 <= raw_output_limit <= 1_048_576:
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
        stdout, stderr = process.communicate(payload, timeout=timeout_seconds)
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

    if type(protocol) is not BenchmarkProtocolV2:
        raise TypeError("benchmark protocol must be BenchmarkProtocolV2")
    if type(trials) not in (list, tuple):
        raise TypeError("benchmark trials must be a list or tuple")
    if type(protocol.preregistered_seeds) not in (list, tuple):
        raise ValueError("benchmark protocol seeds must be a sequence")
    records: list[BenchmarkTrialRecord] = []
    seeds = protocol.preregistered_seeds or (0,)
    for index, raw in enumerate(trials):
        if isinstance(raw, BenchmarkTrialRecord):
            record = raw
        elif type(raw) is dict:
            mapping = cast(dict[str, Any], raw)
            raw_status = mapping.get("status", "SUCCESS")
            if type(raw_status) is not str or raw_status not in _TRIAL_STATUSES:
                raise ValueError("benchmark trial status must be an uppercase known status")
            status = raw_status
            raw_value = mapping.get("value", mapping.get("metric"))
            value: float | None
            error_class = mapping.get("error_class", "")
            if type(error_class) is not str:
                raise ValueError("benchmark trial error class must be a string")
            item_id = mapping.get("item_id", f"item-{index}")
            trial_index = mapping.get("trial_index", index)
            seed = mapping.get("seed", seeds[index % len(seeds)])
            artifact_hash = mapping.get("artifact_hash", "")
            if (
                type(item_id) is not str
                or type(trial_index) is not int
                or type(seed) is not int
                or type(artifact_hash) is not str
            ):
                raise ValueError("benchmark trial identity and artifact metadata types are invalid")
            if status == "SUCCESS":
                if _is_finite_real(raw_value):
                    value = raw_value
                else:
                    value = None
                    status = "ERROR"
                    error_class = error_class or "non_numeric_value"
            else:
                value = raw_value
            record = BenchmarkTrialRecord(
                item_id=item_id,
                trial_index=trial_index,
                seed=seed,
                status=status,
                value=value,
                error_class=error_class,
                artifact_hash=artifact_hash,
            )
        else:
            if _is_finite_real(raw):
                value = raw
                status = "SUCCESS"
                error_class = ""
            else:
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
    if type(protocol) is not BenchmarkProtocolV2:
        raise TypeError("benchmark protocol must be BenchmarkProtocolV2")
    try:
        protocol.validate()
    except (TypeError, ValueError, AttributeError) as exc:
        failures.append(f"protocol_invalid:{type(exc).__name__}")
    min_trials = protocol.min_trials if _is_exact_int(protocol.min_trials) else 30
    required_warmups = protocol.warmups if _is_exact_int(protocol.warmups) else 10
    required_pairs = protocol.paired_blocks if _is_exact_int(protocol.paired_blocks) else 30
    direction = protocol.direction if type(protocol.direction) is str else "higher_is_better"

    normalized_environment_hash = environment_hash if type(environment_hash) is str else ""
    if type(environment_hash) is not str:
        failures.append("environment_hash_invalid")

    if environment is not None:
        if type(environment) is not EnvironmentFingerprint:
            failures.append("environment_invalid")
        else:
            try:
                captured_hash = environment.environment_hash
                if normalized_environment_hash and normalized_environment_hash != captured_hash:
                    failures.append("environment_hash_mismatch")
                normalized_environment_hash = captured_hash
            except (TypeError, ValueError, AttributeError) as exc:
                failures.append(f"environment_invalid:{type(exc).__name__}")

    records: tuple[BenchmarkTrialRecord, ...]
    try:
        records = _normalize_trial_records(protocol, trials)
    except (TypeError, ValueError, OverflowError, AttributeError) as exc:
        records = ()
        failures.append(f"raw_trial_records_invalid:{type(exc).__name__}")
    values = tuple(record.value for record in records if record.status == "SUCCESS" and record.value is not None)
    if not values:
        failures.append("raw_trials_empty_or_non_finite")
    if len(values) < min_trials:
        failures.append("insufficient_trials")
    if records and any(record.status != "SUCCESS" for record in records):
        failures.append("trial_failures_present")
    if warmups is not None and not _is_exact_int(warmups):
        failures.append("warmups_invalid")
    if paired_blocks is not None and not _is_exact_int(paired_blocks):
        failures.append("paired_blocks_invalid")
    actual_warmups = required_warmups if warmups is None or not _is_exact_int(warmups) else warmups
    actual_pairs = required_pairs if paired_blocks is None or not _is_exact_int(paired_blocks) else paired_blocks
    if actual_warmups < required_warmups:
        failures.append("insufficient_warmups")
    if actual_pairs < required_pairs:
        failures.append("insufficient_paired_blocks")
    if type(contamination_flags) not in (list, tuple) or any(
        type(flag) is not str or not flag.strip() for flag in contamination_flags
    ):
        failures.append("contamination_flags_invalid")
        normalized_contamination_flags: tuple[str, ...] = ()
    else:
        normalized_contamination_flags = tuple(contamination_flags)
    if normalized_contamination_flags:
        failures.append("contamination_detected")
    if baseline is not None and not _is_finite_real(baseline):
        failures.append("baseline_invalid")
        normalized_baseline: float | None = None
    else:
        normalized_baseline = baseline
    if not normalized_environment_hash.strip():
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
                verdict = validator(values)
                if type(verdict) is not bool:
                    failures.append("hidden_validator_verdict_invalid")
                elif not verdict:
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
        except (RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            failures.append(f"hidden_validator_failed:{type(exc).__name__}")

    estimate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    percentile = (
        protocol.percentile
        if _is_exact_int(protocol.percentile) and protocol.percentile in {50, 90, 95, 99}
        else None
    )
    if values:
        estimate = (
            statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]
            if percentile
            else statistics.fmean(values)
        )
        if percentile is None and len(values) > 1:
            standard_error = statistics.stdev(values) / math.sqrt(len(values))
            margin = 1.96 * standard_error
            ci_low, ci_high = estimate - margin, estimate + margin
        else:
            ci_low = ci_high = estimate
    if normalized_baseline is not None and estimate is not None:
        if direction == "higher_is_better" and ci_low is not None and ci_low <= normalized_baseline:
            failures.append("confidence_interval_does_not_clear_baseline")
        if direction == "lower_is_better" and ci_high is not None and ci_high >= normalized_baseline:
            failures.append("confidence_interval_does_not_clear_baseline")
    status = "REJECTED" if failures else "PASS"
    try:
        protocol_hash = protocol.protocol_hash
    except (TypeError, ValueError, AttributeError):
        protocol_hash = _digest(protocol)
    raw_trial_hash = _digest(records)
    artifact_hash = _digest(
        {
            "schema": "aegis-benchmark-artifact-v2",
            "protocol_hash": protocol_hash,
            "raw_records": records,
            "baseline": normalized_baseline,
            "environment_hash": normalized_environment_hash,
            "validator_hash": validator_hash,
            "validator_version": validator_version,
            "contamination_flags": normalized_contamination_flags,
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
        contamination_flags=normalized_contamination_flags,
        failure_reasons=tuple(dict.fromkeys(failures)),
        raw_trials=values,
        environment_hash=normalized_environment_hash,
        validator_hash=validator_hash,
        artifact_hash=artifact_hash,
        valid_trial_count=len(values),
        raw_records=records,
        validator_version=validator_version,
    )


__all__ = ["BenchmarkProtocolV2", "BenchmarkResultV2", "BenchmarkTrialRecord", "evaluate_benchmark"]
