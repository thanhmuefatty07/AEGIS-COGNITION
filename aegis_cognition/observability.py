"""Bounded, low-cardinality runtime telemetry for the Python boundary."""

from __future__ import annotations

import contextvars
import json
import secrets
import time
from collections import deque
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from .metrics import RuntimeMetrics


def _positive_id() -> int:
    return secrets.randbits(63) or 1


@dataclass(frozen=True)
class CorrelationContext:
    """Shared identifiers carried through Agent, provider, evidence, and Rust."""

    mission_id: str
    task_id: int
    run_id: int
    attempt_id: int
    lease_id: int | None = None

    @classmethod
    def new(cls, mission_id: str | None = None) -> CorrelationContext:
        return cls(
            mission_id=mission_id or f"mission-{secrets.token_hex(12)}",
            task_id=_positive_id(),
            run_id=_positive_id(),
            attempt_id=_positive_id(),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
        }


_CURRENT_CORRELATION: contextvars.ContextVar[CorrelationContext | None] = contextvars.ContextVar(
    "aegis_current_correlation", default=None
)


class RuntimeTelemetry:
    """Observation-only sink with a bounded local buffer and Rust forwarding."""

    def __init__(self, capacity: int = 1024) -> None:
        if capacity < 1:
            raise ValueError("telemetry capacity must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._dropped = 0
        self.metrics = RuntimeMetrics()

    @property
    def current_correlation(self) -> CorrelationContext | None:
        return _CURRENT_CORRELATION.get()

    @contextmanager
    def bind(self, correlation: CorrelationContext) -> Generator[CorrelationContext]:
        token = _CURRENT_CORRELATION.set(correlation)
        try:
            yield correlation
        finally:
            _CURRENT_CORRELATION.reset(token)

    def emit(
        self,
        kind: str,
        outcome: str,
        *,
        correlation: CorrelationContext | Mapping[str, Any] | None = None,
        queue_depth: int | None = None,
        active: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        context = correlation or self.current_correlation
        if context is None:
            raise ValueError("telemetry events require a correlation context")
        correlation_mapping = (
            context.as_mapping() if isinstance(context, CorrelationContext) else dict(context)
        )
        _validate_correlation(correlation_mapping)
        if not kind or not outcome or len(outcome) > 128:
            raise ValueError("telemetry kind/outcome must be non-empty and bounded")
        event: dict[str, Any] = {
            "schema": "aegis-runtime-telemetry-v1",
            "emitted_at_ms": int(time.time() * 1000),
            "correlation": correlation_mapping,
            "kind": kind,
            "outcome": outcome,
            "queue_depth": queue_depth,
            "active": active,
            "limit": limit,
        }
        if len(self._events) == self._events.maxlen:
            self._dropped += 1
        self._events.append(event)
        self.metrics.increment(f"runtime.events.{kind}.{outcome}")
        self._forward_to_rust(event)
        return event

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "aegis-runtime-telemetry-v1",
            "events": list(self._events),
            "dropped": self._dropped,
            "metrics": self.metrics.snapshot(),
            "authoritative": False,
        }

    def _forward_to_rust(self, event: Mapping[str, Any]) -> None:
        try:
            import aegis_nerve  # type: ignore[import-not-found]
        except ImportError:
            try:
                from aegis_cognition import aegis_nerve  # type: ignore[import-not-found]
            except ImportError:
                self.metrics.increment("runtime.telemetry.forward.unavailable")
                return
        emit = getattr(cast(Any, aegis_nerve), "aegis_runtime_telemetry_emit", None)
        if not callable(emit):
            self.metrics.increment("runtime.telemetry.forward.unsupported")
            return
        try:
            emit(json.dumps(event, sort_keys=True, separators=(",", ":")))
        except Exception:
            self.metrics.increment("runtime.telemetry.forward.rejected")


def _validate_correlation(correlation: Mapping[str, Any]) -> None:
    if not isinstance(correlation.get("mission_id"), str) or not correlation["mission_id"].strip():
        raise ValueError("telemetry mission_id must be non-empty")
    for field in ("task_id", "run_id", "attempt_id"):
        value = correlation.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"telemetry {field} must be a positive integer")
    lease_id = correlation.get("lease_id")
    if lease_id is not None and (not isinstance(lease_id, int) or isinstance(lease_id, bool) or lease_id < 1):
        raise ValueError("telemetry lease_id must be null or a positive integer")
