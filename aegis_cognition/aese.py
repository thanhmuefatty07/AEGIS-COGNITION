"""Deterministic AESE measurement and model primitives.

The objects in this module are deliberately evidence-conservative.  Adaptive
measurement never drops malformed observations, hardware/workload vectors keep
unknown fields explicit, simulation is never labelled as observation, and
cross-hardware prediction refuses missing or out-of-domain inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Final, cast
from collections.abc import Mapping, Sequence


MEASUREMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {"PASS", "FAIL", "CONTINUE", "UNSTABLE", "CONTAMINATED", "INSUFFICIENT_EVIDENCE"}
)
REGIMES: Final[frozenset[str]] = frozenset(
    {
        "COMPUTE_BOUND",
        "MEMORY_BANDWIDTH_BOUND",
        "MEMORY_CAPACITY_BOUND",
        "CACHE_BOUND",
        "STORAGE_BOUND",
        "DURABILITY_BOUND",
        "SYNC_BOUND",
        "NETWORK_BOUND",
        "MIXED",
        "UNKNOWN",
    }
)
SIMULATION_CLASSES: Final[frozenset[str]] = frozenset(
    {
        "ANALYTIC_MODEL",
        "ABSTRACT_STATE_SIMULATOR",
        "FAULT_SIMULATOR",
        "SYSTEMATIC_CONCURRENCY_EXPLORATION",
        "FORMAL_PROOF",
        "EMULATION",
        "DETAILED_ARCHITECTURE_SIMULATION",
    }
)
ANCHOR_OOD_STATUSES: Final[frozenset[str]] = frozenset({"IN_DOMAIN", "OUT_OF_DOMAIN", "UNKNOWN"})
COVERAGE_VALUES: Final[frozenset[str]] = frozenset({"UNKNOWN", "NOT_VERIFIED", "PARTIAL", "COMPLETE"})
_HARDWARE_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "architecture",
    "storage_kind",
    "os_name",
    "kernel",
    "virtualization",
    "pressure",
)
_HARDWARE_INT_FIELDS: Final[tuple[str, ...]] = (
    "physical_cores",
    "logical_cores",
    "cache_bytes",
    "memory_capacity_bytes",
)
_HARDWARE_FLOAT_FIELDS: Final[tuple[str, ...]] = (
    "frequency_hz",
    "memory_bandwidth_bytes_s",
    "memory_latency_ns",
    "fsync_latency_ns",
    "process_startup_ns",
    "ffi_latency_ns",
    "serialization_bytes_s",
)
_WORKLOAD_INT_FIELDS: Final[tuple[str, ...]] = ("working_set_bytes",)
_WORKLOAD_INTENSITY_FIELDS: Final[tuple[str, ...]] = (
    "compute_intensity",
    "memory_intensity",
    "cache_sensitivity",
    "io_intensity",
    "durability_intensity",
    "process_startup_intensity",
    "serialization_intensity",
    "ffi_intensity",
    "parallelism",
    "contention",
    "network_intensity",
    "external_service_dependence",
)
_SCHEMA_VERSION: Final[str] = "aegis-aese-primitives-v1"
_INTERVAL_METHOD: Final[str] = "student_t_cornish_fisher_bonferroni_peek_v1"


def _is_finite(value: object) -> bool:
    if type(value) not in (int, float) or isinstance(value, bool):
        return False
    return math.isfinite(float(cast(int | float, value)))


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _invalid_string(value: object) -> bool:
    return type(value) is not str or not value.strip()


def _string_or_empty(value: object) -> str:
    """Keep only an exact runtime string for failure payload metadata."""

    return value if type(value) is str else ""


def _normal_critical(alpha: float, degrees_of_freedom: int) -> float:
    """Return a conservative Student-t critical approximation.

    The Cornish-Fisher expansion is deterministic and avoids adding a numeric
    dependency to the runtime.  The result is intentionally recorded as an
    approximate interval method; it is not a substitute for external
    calibration or independent replication.
    """

    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    if degrees_of_freedom <= 0:
        return math.inf
    inverse = 1.0 / degrees_of_freedom
    z2 = z * z
    z3 = z2 * z
    z5 = z3 * z2
    z7 = z5 * z2
    return (
        z
        + (z3 + z) * inverse / 4.0
        + (5.0 * z5 + 16.0 * z3 + 3.0 * z) * inverse**2 / 96.0
        + (3.0 * z7 + 19.0 * z5 + 17.0 * z3 - 15.0 * z) * inverse**3 / 384.0
    )


def _lag_one(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    first = values[:-1]
    second = values[1:]
    first_mean = statistics.fmean(first)
    second_mean = statistics.fmean(second)
    first_dev = [value - first_mean for value in first]
    second_dev = [value - second_mean for value in second]
    denominator = math.sqrt(sum(value * value for value in first_dev) * sum(value * value for value in second_dev))
    if denominator == 0.0:
        return 0.0
    return sum(left * right for left, right in zip(first_dev, second_dev, strict=True)) / denominator


def _drift_ratio(values: Sequence[float]) -> float | None:
    if len(values) < 4:
        return None
    midpoint = len(values) // 2
    left = statistics.fmean(values[:midpoint])
    right = statistics.fmean(values[midpoint:])
    scale = max(abs(statistics.fmean(values)), 1e-12)
    return abs(right - left) / scale


@dataclass(frozen=True)
class AdaptiveMeasurementSpec:
    """Pre-registered stopping rule for one scalar benchmark estimand."""

    metric: str
    estimand: str = "mean"
    direction: str = "lower_is_better"
    alpha: float = 0.05
    warmup_count: int = 10
    min_observations: int = 30
    block_size: int = 1
    max_observations: int = 300
    relative_precision: float = 0.05
    absolute_precision: float | None = None
    max_lag1_autocorrelation: float = 0.2
    max_drift_ratio: float = 0.1

    def validate(self) -> None:
        if _invalid_string(self.metric) or self.estimand != "mean":
            raise ValueError("AESE currently requires a named mean estimand")
        if self.direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError("measurement direction must be explicit")
        if not _is_finite(self.alpha) or not 0.0 < float(self.alpha) < 0.5:
            raise ValueError("measurement alpha must be in (0, 0.5)")
        if (
            type(self.warmup_count) is not int
            or type(self.min_observations) is not int
            or type(self.block_size) is not int
            or type(self.max_observations) is not int
            or self.warmup_count < 0
            or self.min_observations < 30
            or self.block_size < 1
            or self.block_size > self.min_observations // 3
            or self.max_observations < self.min_observations
        ):
            raise ValueError("measurement sample floors or block size are invalid")
        for name, value in (
            ("relative_precision", self.relative_precision),
            ("max_lag1_autocorrelation", self.max_lag1_autocorrelation),
            ("max_drift_ratio", self.max_drift_ratio),
        ):
            if not _is_finite(value) or float(value) <= 0.0 or float(value) >= 1.0:
                raise ValueError(f"{name} must be finite and in (0, 1)")
        if self.absolute_precision is not None and (
            not _is_finite(self.absolute_precision) or float(self.absolute_precision) <= 0.0
        ):
            raise ValueError("absolute precision must be finite and positive")

    @property
    def protocol_hash(self) -> str:
        self.validate()
        return _hash({"schema": f"{_SCHEMA_VERSION}-measurement-spec", **asdict(self)})


@dataclass(frozen=True)
class AdaptiveMeasurementResult:
    protocol_hash: str
    status: str
    metric: str
    estimand: str
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    precision_ratio: float | None
    lag1_autocorrelation: float | None
    drift_ratio: float | None
    raw_observation_count: int
    observation_count: int
    block_count: int
    warmup_count: int
    raw_observation_hash: str
    contamination_flags: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    interval_method: str = _INTERVAL_METHOD
    artifact_hash: str = ""


@dataclass(frozen=True)
class AdaptiveMeasurementSession:
    """Append-only checkpoint state for the AESE sequential measurement loop."""

    spec: AdaptiveMeasurementSpec
    warmups: tuple[object, ...] = ()
    observations: tuple[object, ...] = ()

    def checkpoint(self, *, baseline: float | None = None) -> AdaptiveMeasurementResult:
        return evaluate_adaptive_measurement(
            self.spec,
            self.observations,
            warmups=self.warmups,
            baseline=baseline,
        )

    def append_warmups(self, values: Sequence[object]) -> AdaptiveMeasurementSession:
        if self.checkpoint().status != "CONTINUE" and self.observations:
            raise RuntimeError("measurement session is terminal")
        return AdaptiveMeasurementSession(self.spec, self.warmups + tuple(values), self.observations)

    def append_observations(self, values: Sequence[object]) -> AdaptiveMeasurementSession:
        if self.checkpoint().status != "CONTINUE":
            raise RuntimeError("measurement session is terminal")
        return AdaptiveMeasurementSession(self.spec, self.warmups, self.observations + tuple(values))


def evaluate_adaptive_measurement(
    spec: AdaptiveMeasurementSpec,
    observations: Sequence[object],
    *,
    warmups: Sequence[object] = (),
    baseline: float | None = None,
    contamination_flags: Sequence[object] = (),
) -> AdaptiveMeasurementResult:
    """Evaluate one sequential checkpoint with conservative peek control.

    A session may checkpoint at any observation count.  The interval therefore
    spends the pre-registered alpha budget across the maximum number of
    possible checkpoints (Bonferroni), rather than treating repeated ordinary
    confidence intervals as independent evidence.  Non-complete blocks and
    observations beyond the declared budget remain visible and cannot produce
    a passing result.
    """

    typed_spec = type(spec) is AdaptiveMeasurementSpec
    if typed_spec:
        protocol_payload: dict[str, object] = {
            "schema": f"{_SCHEMA_VERSION}-measurement-spec",
            **asdict(spec),
        }
    else:
        protocol_payload = {
            "schema": f"{_SCHEMA_VERSION}-measurement-spec",
            "invalid_spec_type": type(spec).__name__,
        }
    protocol_hash = _hash(protocol_payload)
    observations_valid = type(observations) in (list, tuple)
    warmups_valid = type(warmups) in (list, tuple)
    flags_valid = type(contamination_flags) in (list, tuple)
    raw_observations = tuple(observations) if observations_valid else ()
    raw_warmups = tuple(warmups) if warmups_valid else ()
    reasons: list[str] = []
    raw_flags = tuple(contamination_flags) if flags_valid else ()
    container_invalid = not observations_valid or not warmups_valid
    if not observations_valid:
        reasons.append("observations_invalid")
    if not warmups_valid:
        reasons.append("warmups_invalid")
    if not flags_valid:
        reasons.append("contamination_flags_invalid")
    flags = tuple(flag for flag in raw_flags if type(flag) is str)
    protocol_valid = True
    try:
        if not typed_spec:
            raise TypeError("measurement spec must use the canonical type")
        spec.validate()
    except (TypeError, ValueError, AttributeError) as exc:
        protocol_valid = False
        reasons.append(f"protocol_invalid:{type(exc).__name__}")
    if (
        not flags_valid
        or len(flags) != len(raw_flags)
        or any(_invalid_string(flag) for flag in flags)
    ):
        reasons.append("contamination_flags_invalid")
        flags = ()
    if flags:
        reasons.append("contamination_detected")
    finite_warmups = [float(cast(int | float, value)) for value in raw_warmups if _is_finite(value)]
    finite_observations = [float(cast(int | float, value)) for value in raw_observations if _is_finite(value)]
    if len(finite_warmups) != len(raw_warmups) or len(finite_observations) != len(raw_observations):
        reasons.append("non_finite_observation")
    metric = _string_or_empty(spec.metric) if typed_spec else ""
    estimand = _string_or_empty(spec.estimand) if typed_spec else ""
    direction = (
        spec.direction
        if typed_spec and spec.direction in {"higher_is_better", "lower_is_better"}
        else "lower_is_better"
    )
    alpha = (
        float(spec.alpha)
        if typed_spec and _is_finite(spec.alpha) and 0.0 < float(spec.alpha) < 0.5
        else 0.05
    )
    warmup_count = (
        spec.warmup_count
        if typed_spec and type(spec.warmup_count) is int and spec.warmup_count >= 0
        else 0
    )
    min_observations = (
        spec.min_observations
        if typed_spec and type(spec.min_observations) is int
        and spec.min_observations >= 0
        else 0
    )
    maximum = (
        spec.max_observations
        if typed_spec and type(spec.max_observations) is int
        and spec.max_observations > 0
        else 0
    )
    block_size = (
        spec.block_size
        if typed_spec and type(spec.block_size) is int and spec.block_size > 0
        else 1
    )
    relative_precision = (
        float(spec.relative_precision)
        if typed_spec and _is_finite(spec.relative_precision)
        and 0.0 < float(spec.relative_precision) < 1.0
        else math.inf
    )
    absolute_precision = (
        float(spec.absolute_precision)
        if typed_spec and spec.absolute_precision is not None
        and _is_finite(spec.absolute_precision)
        and float(spec.absolute_precision) > 0.0
        else None
    )
    max_lag1_autocorrelation = (
        float(spec.max_lag1_autocorrelation)
        if typed_spec and _is_finite(spec.max_lag1_autocorrelation)
        and 0.0 < float(spec.max_lag1_autocorrelation) < 1.0
        else math.inf
    )
    max_drift_ratio = (
        float(spec.max_drift_ratio)
        if typed_spec and _is_finite(spec.max_drift_ratio)
        and 0.0 < float(spec.max_drift_ratio) < 1.0
        else math.inf
    )
    if len(finite_warmups) < warmup_count:
        reasons.append("insufficient_warmups")
    analyzed = finite_observations[:maximum] if maximum else []
    if len(finite_observations) > len(analyzed):
        reasons.append("observation_budget_exceeded")
    if len(analyzed) < min_observations:
        reasons.append("insufficient_observations")
    complete_count = len(analyzed) - (len(analyzed) % block_size)
    if complete_count != len(analyzed):
        reasons.append("incomplete_final_block")
    values = analyzed[:complete_count]
    block_means = [statistics.fmean(values[index : index + block_size]) for index in range(0, len(values), block_size)]
    block_count = len(block_means)
    lag = _lag_one(block_means)
    drift = _drift_ratio(block_means)
    if (
        lag is not None
        and abs(lag) > max_lag1_autocorrelation
    ):
        reasons.append("autocorrelation_exceeds_bound")
    if drift is not None and drift > max_drift_ratio:
        reasons.append("drift_exceeds_bound")
    estimate: float | None = statistics.fmean(block_means) if block_means else None
    ci_low: float | None = None
    ci_high: float | None = None
    precision_ratio: float | None = None
    if estimate is not None and block_count > 1:
        standard_error = statistics.stdev(block_means) / math.sqrt(block_count)
        # A caller may inspect a session after every appended observation.  A
        # deterministic Bonferroni allocation keeps the family-wise error
        # bound conservative under that allowed stopping rule.
        checkpoint_alpha = alpha / max(maximum, 1)
        margin = _normal_critical(checkpoint_alpha, block_count - 1) * standard_error
        ci_low, ci_high = estimate - margin, estimate + margin
        precision_ratio = margin / max(abs(estimate), 1e-12)
    elif estimate is not None:
        ci_low = ci_high = estimate
        precision_ratio = 0.0
    if baseline is not None and not _is_finite(baseline):
        reasons.append("baseline_invalid")
        baseline = None
    stable = not any(reason in reasons for reason in ("autocorrelation_exceeds_bound", "drift_exceeds_bound"))
    floor_met = len(finite_warmups) >= warmup_count and len(values) >= min_observations
    precision_met = precision_ratio is not None and precision_ratio <= relative_precision
    if absolute_precision is not None and ci_low is not None and ci_high is not None:
        precision_met = precision_met and (ci_high - ci_low) / 2.0 <= absolute_precision
    if floor_met and not precision_met:
        reasons.append("precision_not_met")
    if floor_met and baseline is not None and ci_low is not None and ci_high is not None:
        if direction == "higher_is_better" and ci_low <= float(baseline):
            reasons.append("confidence_interval_does_not_clear_baseline")
        if direction == "lower_is_better" and ci_high >= float(baseline):
            reasons.append("confidence_interval_does_not_clear_baseline")
    if "contamination_detected" in reasons or "non_finite_observation" in reasons:
        status = "CONTAMINATED"
    elif "autocorrelation_exceeds_bound" in reasons or "drift_exceeds_bound" in reasons:
        status = "UNSTABLE"
    elif (
        not protocol_valid
        or container_invalid
        or "contamination_flags_invalid" in reasons
        or "observation_budget_exceeded" in reasons
    ):
        status = "INSUFFICIENT_EVIDENCE"
    elif "incomplete_final_block" in reasons:
        status = "CONTINUE" if len(finite_observations) < maximum else "INSUFFICIENT_EVIDENCE"
    elif floor_met and stable and precision_met and "confidence_interval_does_not_clear_baseline" in reasons:
        status = "FAIL"
    elif floor_met and stable and precision_met:
        status = "PASS"
    elif len(finite_observations) >= maximum > 0:
        status = "INSUFFICIENT_EVIDENCE"
    else:
        status = "CONTINUE"
    raw_hash = _hash({"warmups": raw_warmups, "observations": raw_observations})
    result_without_hash = {
        "schema": f"{_SCHEMA_VERSION}-measurement-result",
        "protocol_hash": protocol_hash,
        "status": status,
        "metric": metric,
        "estimate": estimate,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "precision_ratio": precision_ratio,
        "lag1_autocorrelation": lag,
        "drift_ratio": drift,
        "raw_observation_count": len(raw_observations),
        "observation_count": len(values),
        "block_count": block_count,
        "warmup_count": len(raw_warmups),
        "raw_observation_hash": raw_hash,
        "contamination_flags": flags,
        "failure_reasons": tuple(dict.fromkeys(reasons)),
    }
    artifact_hash = _hash(result_without_hash)
    return AdaptiveMeasurementResult(
        protocol_hash=protocol_hash,
        status=status,
        metric=metric,
        estimand=estimand,
        estimate=estimate,
        ci_low=ci_low,
        ci_high=ci_high,
        precision_ratio=precision_ratio,
        lag1_autocorrelation=lag,
        drift_ratio=drift,
        raw_observation_count=len(raw_observations),
        observation_count=len(values),
        block_count=block_count,
        warmup_count=len(raw_warmups),
        raw_observation_hash=raw_hash,
        contamination_flags=flags,
        failure_reasons=tuple(dict.fromkeys(reasons)),
        artifact_hash=artifact_hash,
    )


@dataclass(frozen=True)
class HardwareCapabilityVector:
    """Versioned host capability vector; ``None`` serializes as ``UNKNOWN``."""

    architecture: str | None = None
    physical_cores: int | None = None
    logical_cores: int | None = None
    cache_bytes: int | None = None
    frequency_hz: float | None = None
    memory_capacity_bytes: int | None = None
    memory_bandwidth_bytes_s: float | None = None
    memory_latency_ns: float | None = None
    storage_kind: str | None = None
    fsync_latency_ns: float | None = None
    process_startup_ns: float | None = None
    ffi_latency_ns: float | None = None
    serialization_bytes_s: float | None = None
    os_name: str | None = None
    kernel: str | None = None
    virtualization: str | None = None
    pressure: str | None = None

    def validate(self) -> None:
        for name in _HARDWARE_TEXT_FIELDS:
            value = getattr(self, name)
            if value is not None and _invalid_string(value):
                raise ValueError(f"hardware field {name} must be a non-empty string or UNKNOWN")
        for name in _HARDWARE_INT_FIELDS:
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"hardware field {name} must be a non-negative integer or UNKNOWN")
        for name in _HARDWARE_FLOAT_FIELDS:
            value = getattr(self, name)
            if value is not None and (not _is_finite(value) or float(value) < 0.0):
                raise ValueError(f"hardware field {name} must be a non-negative finite number or UNKNOWN")
        if (
            self.logical_cores is not None
            and self.physical_cores is not None
            and self.logical_cores < self.physical_cores
        ):
            raise ValueError("logical cores cannot be below physical cores")

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema": f"{_SCHEMA_VERSION}-hardware",
            "values": {name: "UNKNOWN" if value is None else value for name, value in asdict(self).items()},
            "unknown_fields": [name for name, value in asdict(self).items() if value is None],
        }

    @property
    def vector_hash(self) -> str:
        return _hash(self.as_dict())

    @classmethod
    def from_runtime_profile(cls, profile: object) -> HardwareCapabilityVector:
        """Project only explicitly reported runtime fields; never infer missing data."""

        if not isinstance(profile, Mapping):
            raise ValueError("runtime profile must be a mapping")
        typed_profile = cast(Mapping[str, object], profile)

        raw_cpu = typed_profile.get("cpu")
        raw_os = typed_profile.get("os")
        if raw_cpu is not None and not isinstance(raw_cpu, Mapping):
            raise ValueError("runtime profile cpu must be a mapping or UNKNOWN")
        if raw_os is not None and not isinstance(raw_os, Mapping):
            raise ValueError("runtime profile os must be a mapping or UNKNOWN")
        cpu: Mapping[str, object] = cast(Mapping[str, object], raw_cpu) if isinstance(raw_cpu, Mapping) else {}
        os_info: Mapping[str, object] = cast(Mapping[str, object], raw_os) if isinstance(raw_os, Mapping) else {}
        raw_architecture = cpu.get("architecture")
        architecture = raw_architecture if type(raw_architecture) is str else None
        usable = cpu.get("usable_parallelism")
        logical_cores = usable if type(usable) is int and usable >= 1 else None
        backend = os_info.get("backend")
        enforcement = os_info.get("enforcement")
        os_name = backend if type(backend) is str and backend.strip() else None
        virtualization = enforcement if type(enforcement) is str and enforcement.strip() else None
        vector = cls(
            architecture=architecture,
            logical_cores=logical_cores,
            os_name=os_name,
            virtualization=virtualization,
        )
        vector.validate()
        return vector

    def numeric_features(self) -> dict[str, float]:
        self.validate()
        return {
            f"hardware.{name}": float(value)
            for name, value in asdict(self).items()
            if name not in _HARDWARE_TEXT_FIELDS and value is not None
        }

    @classmethod
    def from_mapping(cls, values: object) -> HardwareCapabilityVector:
        if not isinstance(values, Mapping):
            raise ValueError("hardware mapping must be a mapping")
        typed_values = cast(Mapping[str, object], values)
        allowed = set(_HARDWARE_TEXT_FIELDS) | set(_HARDWARE_INT_FIELDS) | set(_HARDWARE_FLOAT_FIELDS)
        unknown = set(typed_values) - allowed
        if unknown:
            raise ValueError(f"unknown hardware fields: {sorted(unknown, key=str)}")
        vector = cls(
            architecture=cast(str | None, typed_values.get("architecture")),
            physical_cores=cast(int | None, typed_values.get("physical_cores")),
            logical_cores=cast(int | None, typed_values.get("logical_cores")),
            cache_bytes=cast(int | None, typed_values.get("cache_bytes")),
            frequency_hz=cast(float | None, typed_values.get("frequency_hz")),
            memory_capacity_bytes=cast(int | None, typed_values.get("memory_capacity_bytes")),
            memory_bandwidth_bytes_s=cast(float | None, typed_values.get("memory_bandwidth_bytes_s")),
            memory_latency_ns=cast(float | None, typed_values.get("memory_latency_ns")),
            storage_kind=cast(str | None, typed_values.get("storage_kind")),
            fsync_latency_ns=cast(float | None, typed_values.get("fsync_latency_ns")),
            process_startup_ns=cast(float | None, typed_values.get("process_startup_ns")),
            ffi_latency_ns=cast(float | None, typed_values.get("ffi_latency_ns")),
            serialization_bytes_s=cast(float | None, typed_values.get("serialization_bytes_s")),
            os_name=cast(str | None, typed_values.get("os_name")),
            kernel=cast(str | None, typed_values.get("kernel")),
            virtualization=cast(str | None, typed_values.get("virtualization")),
            pressure=cast(str | None, typed_values.get("pressure")),
        )
        vector.validate()
        return vector


@dataclass(frozen=True)
class WorkloadSignature:
    """Normalized workload shape used to select a scaling regime."""

    compute_intensity: float | None = None
    memory_intensity: float | None = None
    working_set_bytes: int | None = None
    cache_sensitivity: float | None = None
    io_intensity: float | None = None
    durability_intensity: float | None = None
    process_startup_intensity: float | None = None
    serialization_intensity: float | None = None
    ffi_intensity: float | None = None
    parallelism: float | None = None
    contention: float | None = None
    network_intensity: float | None = None
    external_service_dependence: float | None = None

    def validate(self) -> None:
        if self.working_set_bytes is not None and (
            type(self.working_set_bytes) is not int or self.working_set_bytes < 0
        ):
            raise ValueError("working_set_bytes must be a non-negative integer or UNKNOWN")
        for name in _WORKLOAD_INTENSITY_FIELDS:
            value = getattr(self, name)
            if value is not None and (not _is_finite(value) or not 0.0 <= float(value) <= 1.0):
                raise ValueError(f"workload intensity {name} must be in [0, 1] or UNKNOWN")

    def as_dict(self) -> dict[str, object]:
        self.validate()
        values = asdict(self)
        return {
            "schema": f"{_SCHEMA_VERSION}-workload",
            "values": {name: "UNKNOWN" if value is None else value for name, value in values.items()},
            "regime": self.classify_regime(),
        }

    @property
    def signature_hash(self) -> str:
        return _hash(self.as_dict())

    def numeric_features(self) -> dict[str, float]:
        self.validate()
        return {f"workload.{name}": float(value) for name, value in asdict(self).items() if value is not None}

    def classify_regime(self, hardware: HardwareCapabilityVector | None = None) -> str:
        self.validate()
        values = asdict(self)
        known = [value for value in values.values() if value is not None]
        if len(known) < 2:
            return "UNKNOWN"
        if (self.external_service_dependence or 0.0) >= 0.7 or (self.network_intensity or 0.0) >= 0.7:
            return "NETWORK_BOUND"
        if (self.durability_intensity or 0.0) >= 0.7 and (self.io_intensity or 0.0) >= 0.4:
            return "DURABILITY_BOUND"
        if (self.contention or 0.0) >= 0.7:
            return "SYNC_BOUND"
        if (
            self.working_set_bytes is not None
            and hardware is not None
            and hardware.memory_capacity_bytes is not None
            and self.working_set_bytes > hardware.memory_capacity_bytes
        ):
            return "MEMORY_CAPACITY_BOUND"
        if (self.io_intensity or 0.0) >= 0.7:
            return "STORAGE_BOUND"
        if (self.cache_sensitivity or 0.0) >= 0.7:
            return "CACHE_BOUND"
        if (self.memory_intensity or 0.0) >= 0.7:
            return "MEMORY_BANDWIDTH_BOUND"
        if (self.compute_intensity or 0.0) >= 0.7:
            return "COMPUTE_BOUND"
        return "MIXED"


@dataclass(frozen=True)
class SimulationEvidence:
    """Simulation output whose epistemic class cannot become an observation."""

    simulation_id: str
    simulation_class: str
    question: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    status: str = "SIMULATION_ONLY"
    evidence_class: str = "SIMULATED"

    def validate(self) -> None:
        if _invalid_string(self.simulation_id) or self.simulation_class not in SIMULATION_CLASSES:
            raise ValueError("simulation identity or class is invalid")
        if (
            _invalid_string(self.question)
            or type(self.inputs) not in (tuple, list)
            or type(self.outputs) not in (tuple, list)
        ):
            raise ValueError("simulation question and I/O declarations are required")
        if any(_invalid_string(value) for value in (*self.inputs, *self.outputs)):
            raise ValueError("simulation I/O declarations must be non-empty strings")
        if self.status != "SIMULATION_ONLY" or self.evidence_class != "SIMULATED":
            raise ValueError("simulation evidence cannot be promoted to observation")

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return {"schema": f"{_SCHEMA_VERSION}-simulation", **asdict(self), "claimable_as_observed": False}


@dataclass(frozen=True)
class AnchorObservation:
    anchor_id: str
    hardware: HardwareCapabilityVector
    workload: WorkloadSignature
    observed_value: float

    def validate(self) -> None:
        if _invalid_string(self.anchor_id) or not _is_finite(self.observed_value):
            raise ValueError("anchor identity and observed value are required")
        if type(self.hardware) is not HardwareCapabilityVector or type(self.workload) is not WorkloadSignature:
            raise TypeError("anchor hardware and workload must use canonical vector types")
        self.hardware.validate()
        self.workload.validate()

    def features(self) -> dict[str, float]:
        self.validate()
        return {**self.hardware.numeric_features(), **self.workload.numeric_features()}

    @property
    def evidence_hash(self) -> str:
        """Hash the complete validated anchor, not only its feature projection."""

        self.validate()
        return _hash(
            {
                "schema": f"{_SCHEMA_VERSION}-anchor-observation",
                "anchor_id": self.anchor_id,
                "hardware": self.hardware.as_dict(),
                "workload": self.workload.as_dict(),
                "observed_value": self.observed_value,
            }
        )


@dataclass(frozen=True)
class AnalyticPredictionModel:
    """Preregistered analytic model with measured residual uncertainty."""

    model_id: str
    model_version: str
    metric: str
    intercept: float
    coefficients: tuple[tuple[str, float], ...]
    validated_domain: tuple[tuple[str, float, float], ...]
    residual_half_width: float | None
    residual_sample_count: int
    distance_penalty_per_unit: float = 0.0
    residual_evidence_class: str = "NOT_VERIFIED"

    def validate(self) -> None:
        if any(_invalid_string(value) for value in (self.model_id, self.model_version, self.metric)):
            raise ValueError("prediction model identity is required")
        if type(self.coefficients) not in (list, tuple) or type(self.validated_domain) not in (list, tuple):
            raise TypeError("prediction model coefficients and domains must be canonical sequences")
        if any(type(item) not in (list, tuple) or len(item) != 2 for item in self.coefficients):
            raise ValueError("prediction model coefficients must be pairs")
        if any(type(item) not in (list, tuple) or len(item) != 3 for item in self.validated_domain):
            raise ValueError("prediction model domains must be triples")
        if (
            not _is_finite(self.intercept)
            or type(self.residual_sample_count) is not int
            or self.residual_sample_count < 0
        ):
            raise ValueError("prediction model numeric metadata is invalid")
        if self.residual_half_width is not None and (
            not _is_finite(self.residual_half_width) or float(self.residual_half_width) <= 0.0
        ):
            raise ValueError("residual half-width must be finite and positive")
        if self.residual_evidence_class not in {"MEASURED", "NOT_VERIFIED"}:
            raise ValueError("residual evidence class is invalid")
        if not _is_finite(self.distance_penalty_per_unit) or self.distance_penalty_per_unit < 0.0:
            raise ValueError("distance penalty must be finite and non-negative")
        coefficient_names = [
            name for name, value in self.coefficients if not _invalid_string(name) and _is_finite(value)
        ]
        if len(coefficient_names) != len(self.coefficients) or len(set(coefficient_names)) != len(coefficient_names):
            raise ValueError("prediction coefficients must have unique finite names")
        domains = {name: (lower, upper) for name, lower, upper in self.validated_domain}
        if (
            len(domains) != len(self.validated_domain)
            or set(domains) != set(coefficient_names)
            or any(not _is_finite(lower) or not _is_finite(upper) or lower > upper for lower, upper in domains.values())
        ):
            raise ValueError("validated domain must cover every model feature")


@dataclass(frozen=True)
class PredictionResult:
    model_id: str
    model_version: str
    metric: str
    status: str
    estimate: float | None
    prediction_interval: tuple[float, float] | None
    validated_domain: tuple[tuple[str, float, float], ...]
    nearest_anchor_distance: float | None
    ood_status: str
    missing_features: tuple[str, ...]
    failure_reasons: tuple[str, ...]
    artifact_hash: str
    anchor_ids: tuple[str, ...] = ()
    anchor_evidence_hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AnchorCandidate:
    """A possible high-fidelity external anchor; this object never executes it."""

    anchor_id: str
    platform: str
    estimated_cost_seconds: float
    mandatory: bool = False
    ood_status: str = "UNKNOWN"
    model_uncertainty: float | None = None
    changed_platform_boundary: bool = False
    periodic_sentinel_due: bool = False
    available: bool = True

    def validate(self) -> None:
        if _invalid_string(self.anchor_id) or _invalid_string(self.platform):
            raise ValueError("anchor identity and platform are required")
        if not _is_finite(self.estimated_cost_seconds) or self.estimated_cost_seconds <= 0.0:
            raise ValueError("anchor cost must be finite and positive")
        if self.ood_status not in ANCHOR_OOD_STATUSES:
            raise ValueError("anchor OOD status is invalid")
        if self.model_uncertainty is not None and (
            not _is_finite(self.model_uncertainty) or not 0.0 <= float(self.model_uncertainty) <= 1.0
        ):
            raise ValueError("anchor model uncertainty must be in [0, 1] or UNKNOWN")
        if (
            type(self.mandatory) is not bool
            or type(self.changed_platform_boundary) is not bool
            or type(self.periodic_sentinel_due) is not bool
            or type(self.available) is not bool
        ):
            raise ValueError("anchor priority flags must be boolean")

    def priority_key(self) -> tuple[int, int, int, int, float, str]:
        self.validate()
        return (
            int(self.mandatory),
            int(self.changed_platform_boundary),
            int(self.ood_status == "OUT_OF_DOMAIN"),
            int(self.periodic_sentinel_due),
            float(self.model_uncertainty or 0.0),
            self.anchor_id,
        )


@dataclass(frozen=True)
class AnchorSelectionPlan:
    status: str
    budget_seconds: float
    selected_anchor_ids: tuple[str, ...]
    unavailable_anchor_ids: tuple[str, ...]
    skipped_anchor_ids: tuple[str, ...]
    planned_cost_seconds: float
    remaining_budget_seconds: float
    execution: str = "PLANNED_NOT_EXECUTED"
    failure_reasons: tuple[str, ...] = ()
    artifact_hash: str = ""


def _coerce_anchor_sequence(value: object) -> Sequence[AnchorObservation] | None:
    """Return a runtime-checked anchor sequence without coercion."""

    if type(value) not in (list, tuple):
        return None
    return cast(Sequence[AnchorObservation], value)


def select_anchor_plan(
    candidates: Sequence[AnchorCandidate],
    *,
    budget_seconds: float,
    ) -> AnchorSelectionPlan:
    """Select external anchors without invoking a hosted runner."""

    reasons: list[str] = []
    input_invalid = False
    if not _is_finite(budget_seconds) or float(budget_seconds) <= 0.0:
        reasons.append("budget_invalid")
        input_invalid = True
        budget = 0.0
    else:
        budget = float(budget_seconds)
    if type(candidates) not in (list, tuple):
        candidate_values: Sequence[AnchorCandidate] = ()
        reasons.append("candidates_invalid:TypeError")
        input_invalid = True
    else:
        candidate_values = candidates
    by_id: dict[str, AnchorCandidate] = {}
    for candidate in candidate_values:
        if type(candidate) is not AnchorCandidate:
            reasons.append("candidate_invalid:TypeError")
            input_invalid = True
            continue
        try:
            candidate.validate()
        except (TypeError, ValueError, AttributeError) as exc:
            reasons.append(f"candidate_invalid:{type(exc).__name__}")
            input_invalid = True
            continue
        if candidate.anchor_id in by_id:
            reasons.append("duplicate_anchor_id")
            continue
        by_id[candidate.anchor_id] = candidate
    ordered = sorted(by_id.values(), key=lambda item: item.priority_key(), reverse=True)
    selected: list[str] = []
    unavailable: list[str] = []
    skipped: list[str] = []
    spent = 0.0
    mandatory_unavailable = False
    mandatory_over_budget = False
    for candidate in ordered:
        if not candidate.available:
            unavailable.append(candidate.anchor_id)
            if candidate.mandatory:
                mandatory_unavailable = True
            continue
        cost = float(candidate.estimated_cost_seconds)
        next_spent = spent + cost
        if not math.isfinite(next_spent):
            reasons.append("planned_cost_overflow")
            input_invalid = True
            continue
        if candidate.mandatory:
            selected.append(candidate.anchor_id)
            spent = next_spent
            if spent > budget:
                mandatory_over_budget = True
        elif spent + cost <= budget:
            selected.append(candidate.anchor_id)
            spent = next_spent
        else:
            skipped.append(candidate.anchor_id)
    if mandatory_unavailable:
        reasons.append("mandatory_anchor_unavailable")
        input_invalid = True
    if mandatory_over_budget:
        reasons.append("mandatory_anchor_budget_exceeded")
    if input_invalid:
        # A blocked plan must not expose a seemingly executable subset. Keep
        # invalidation visible in the reasons while clearing all planned spend.
        selected = []
        spent = 0.0
    if input_invalid:
        status = "EXTERNAL_VERIFICATION_BLOCKED"
    elif mandatory_over_budget:
        status = "INSUFFICIENT_BUDGET"
    elif not selected:
        status = "INSUFFICIENT_BUDGET"
        reasons.append("no_anchor_fits_budget")
    elif skipped:
        status = "PARTIAL_PLAN"
    else:
        status = "PLAN_READY"
    payload = {
        "schema": f"{_SCHEMA_VERSION}-anchor-plan",
        "status": status,
        "budget_seconds": budget,
        "selected_anchor_ids": tuple(selected),
        "unavailable_anchor_ids": tuple(unavailable),
        "skipped_anchor_ids": tuple(skipped),
        "planned_cost_seconds": spent,
        "remaining_budget_seconds": budget - spent,
        "execution": "PLANNED_NOT_EXECUTED",
        "failure_reasons": tuple(dict.fromkeys(reasons)),
    }
    return AnchorSelectionPlan(
        status=status,
        budget_seconds=budget,
        selected_anchor_ids=tuple(selected),
        unavailable_anchor_ids=tuple(unavailable),
        skipped_anchor_ids=tuple(skipped),
        planned_cost_seconds=spent,
        remaining_budget_seconds=budget - spent,
        failure_reasons=tuple(dict.fromkeys(reasons)),
        artifact_hash=_hash(payload),
    )


@dataclass(frozen=True)
class CoverageVector:
    """Independent coverage dimensions; no aggregate percentage is inferred."""

    contract_coverage: str = "UNKNOWN"
    state_coverage: str = "UNKNOWN"
    failure_mode_coverage: str = "UNKNOWN"
    schedule_coverage: str = "UNKNOWN"
    hardware_domain_coverage: str = "UNKNOWN"
    platform_semantic_coverage: str = "UNKNOWN"
    statistical_precision: str = "UNKNOWN"

    def validate(self) -> None:
        values = (
            self.contract_coverage,
            self.state_coverage,
            self.failure_mode_coverage,
            self.schedule_coverage,
            self.hardware_domain_coverage,
            self.platform_semantic_coverage,
            self.statistical_precision,
        )
        if any(value not in COVERAGE_VALUES for value in values):
            raise ValueError("coverage vector contains an invalid status")

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return {"schema": f"{_SCHEMA_VERSION}-coverage-vector", **asdict(self), "aggregate": None}


def predict_cross_hardware(
    model: AnalyticPredictionModel,
    hardware: HardwareCapabilityVector,
    workload: WorkloadSignature,
    anchors: Sequence[AnchorObservation],
) -> PredictionResult:
    """Predict only inside a measured model domain with reconstructible metadata."""

    reasons: list[str] = []
    typed_model = type(model) is AnalyticPredictionModel
    typed_hardware = type(hardware) is HardwareCapabilityVector
    typed_workload = type(workload) is WorkloadSignature
    model_id = _string_or_empty(model.model_id) if typed_model else ""
    model_version = _string_or_empty(model.model_version) if typed_model else ""
    metric = _string_or_empty(model.metric) if typed_model else ""
    raw_domain: object = model.validated_domain if typed_model else ()
    validated_domain = (
        tuple(cast(Sequence[tuple[str, float, float]], raw_domain))
        if typed_model and type(raw_domain) in (list, tuple)
        else ()
    )
    if not typed_model:
        reasons.append("model_invalid:TypeError")
    if not typed_hardware:
        reasons.append("hardware_invalid:TypeError")
    if not typed_workload:
        reasons.append("workload_invalid:TypeError")
    try:
        if typed_model:
            model.validate()
        if typed_hardware:
            hardware.validate()
        if typed_workload:
            workload.validate()
    except (TypeError, ValueError, AttributeError, KeyError, IndexError) as exc:
        reasons.append(f"input_invalid:{type(exc).__name__}")
    if reasons:
        payload = {
            "schema": f"{_SCHEMA_VERSION}-prediction",
            "model_id": model_id,
            "model_version": model_version,
            "metric": metric,
            "status": "INSUFFICIENT_EVIDENCE",
            "validated_domain": validated_domain,
            "nearest_anchor_distance": None,
            "ood_status": "UNKNOWN",
            "missing_features": (),
            "failure_reasons": tuple(dict.fromkeys(reasons)),
            "anchor_ids": (),
            "anchor_evidence_hashes": (),
        }
        return PredictionResult(
            model_id=model_id,
            model_version=model_version,
            metric=metric,
            status="INSUFFICIENT_EVIDENCE",
            estimate=None,
            prediction_interval=None,
            validated_domain=validated_domain,
            nearest_anchor_distance=None,
            ood_status="UNKNOWN",
            missing_features=(),
            failure_reasons=tuple(dict.fromkeys(reasons)),
            artifact_hash=_hash(payload),
        )
    features = {**hardware.numeric_features(), **workload.numeric_features()}
    coefficient_names = tuple(name for name, _value in model.coefficients)
    missing = tuple(sorted(name for name in coefficient_names if name not in features))
    domains = {name: (lower, upper) for name, lower, upper in model.validated_domain}
    outside = tuple(
        sorted(
            name
            for name in coefficient_names
            if name in features and (features[name] < domains[name][0] or features[name] > domains[name][1])
        )
    )
    valid_anchors: list[tuple[AnchorObservation, dict[str, float]]] = []
    out_of_domain_anchor_count = 0
    duplicate_anchor_count = 0
    seen_anchor_ids: set[str] = set()
    raw_anchor_values = _coerce_anchor_sequence(anchors)
    if raw_anchor_values is None:
        reasons.append("anchors_invalid:TypeError")
        anchor_values: Sequence[AnchorObservation] = ()
    else:
        anchor_values = raw_anchor_values
    invalid_anchor_count = 0
    for anchor in anchor_values:
        if type(anchor) is not AnchorObservation:
            invalid_anchor_count += 1
            continue
        try:
            anchor_features = anchor.features()
        except (TypeError, ValueError, AttributeError, KeyError, IndexError):
            invalid_anchor_count += 1
            continue
        if anchor.anchor_id in seen_anchor_ids:
            duplicate_anchor_count += 1
            continue
        seen_anchor_ids.add(anchor.anchor_id)
        if all(name in anchor_features for name in coefficient_names):
            if any(
                anchor_features[name] < domains[name][0] or anchor_features[name] > domains[name][1]
                for name in coefficient_names
            ):
                out_of_domain_anchor_count += 1
                continue
            valid_anchors.append((anchor, anchor_features))
    if invalid_anchor_count:
        reasons.append("invalid_anchor_observation")
    anchor_evidence_hashes = tuple(
        sorted((anchor.anchor_id, anchor.evidence_hash) for anchor, _features in valid_anchors)
    )
    anchor_ids = tuple(anchor_id for anchor_id, _evidence_hash in anchor_evidence_hashes)
    nearest: float | None = None
    if not missing and valid_anchors:
        distances: list[float] = []
        for _anchor, anchor_features in valid_anchors:
            distance = math.sqrt(
                sum(
                    ((features[name] - anchor_features[name]) / max(domains[name][1] - domains[name][0], 1e-12)) ** 2
                    for name in coefficient_names
                )
            )
            distances.append(distance)
        nearest = min(distances)
    if reasons:
        status = "INSUFFICIENT_EVIDENCE"
        ood = "UNKNOWN"
    elif missing:
        reasons.append("missing_feature")
        status = "INSUFFICIENT_EVIDENCE"
        ood = "UNKNOWN"
    elif outside:
        reasons.append("target_outside_validated_domain")
        status = "REJECTED_OOD"
        ood = "OUT_OF_DOMAIN"
    elif len(valid_anchors) < 2:
        reasons.append("no_reconstructible_anchor")
        if out_of_domain_anchor_count:
            reasons.append("anchor_outside_validated_domain")
        if duplicate_anchor_count:
            reasons.append("duplicate_anchor_id")
        status = "INSUFFICIENT_EVIDENCE"
        ood = "UNKNOWN"
    elif (
        model.residual_half_width is None
        or model.residual_sample_count < 30
        or model.residual_evidence_class != "MEASURED"
    ):
        reasons.append("residual_uncertainty_not_validated")
        status = "INSUFFICIENT_EVIDENCE"
        ood = "IN_DOMAIN"
    else:
        estimate = model.intercept + sum(coefficient * features[name] for name, coefficient in model.coefficients)
        assert nearest is not None
        half_width = float(model.residual_half_width) + float(model.distance_penalty_per_unit) * nearest
        interval = (estimate - half_width, estimate + half_width)
        payload = {
            "schema": f"{_SCHEMA_VERSION}-prediction",
            "model_id": model.model_id,
            "model_version": model.model_version,
            "metric": model.metric,
            "status": "PREDICTED_IN_DOMAIN",
            "estimate": estimate,
            "prediction_interval": interval,
            "validated_domain": model.validated_domain,
            "nearest_anchor_distance": nearest,
            "ood_status": "IN_DOMAIN",
            "anchor_ids": anchor_ids,
            "anchor_evidence_hashes": anchor_evidence_hashes,
        }
        return PredictionResult(
            model_id=model_id,
            model_version=model_version,
            metric=metric,
            status="PREDICTED_IN_DOMAIN",
            estimate=estimate,
            prediction_interval=interval,
            validated_domain=validated_domain,
            nearest_anchor_distance=nearest,
            ood_status="IN_DOMAIN",
            missing_features=(),
            failure_reasons=(),
            artifact_hash=_hash(payload),
            anchor_ids=anchor_ids,
            anchor_evidence_hashes=anchor_evidence_hashes,
        )
    payload = {
        "schema": f"{_SCHEMA_VERSION}-prediction",
        "model_id": model_id,
        "model_version": model_version,
        "metric": metric,
        "status": status,
        "validated_domain": validated_domain,
        "nearest_anchor_distance": nearest,
        "ood_status": ood,
        "missing_features": missing,
        "failure_reasons": tuple(dict.fromkeys(reasons)),
        "anchor_ids": anchor_ids,
        "anchor_evidence_hashes": anchor_evidence_hashes,
    }
    return PredictionResult(
        model_id=model_id,
        model_version=model_version,
        metric=metric,
        status=status,
        estimate=None,
        prediction_interval=None,
        validated_domain=validated_domain,
        nearest_anchor_distance=nearest,
        ood_status=ood,
        missing_features=missing,
        failure_reasons=tuple(dict.fromkeys(reasons)),
        artifact_hash=_hash(payload),
        anchor_ids=anchor_ids,
        anchor_evidence_hashes=anchor_evidence_hashes,
    )


__all__ = [
    "ANCHOR_OOD_STATUSES",
    "COVERAGE_VALUES",
    "MEASUREMENT_STATUSES",
    "REGIMES",
    "SIMULATION_CLASSES",
    "AdaptiveMeasurementResult",
    "AdaptiveMeasurementSession",
    "AdaptiveMeasurementSpec",
    "AnalyticPredictionModel",
    "AnchorCandidate",
    "AnchorObservation",
    "AnchorSelectionPlan",
    "CoverageVector",
    "HardwareCapabilityVector",
    "PredictionResult",
    "SimulationEvidence",
    "WorkloadSignature",
    "evaluate_adaptive_measurement",
    "predict_cross_hardware",
    "select_anchor_plan",
]
