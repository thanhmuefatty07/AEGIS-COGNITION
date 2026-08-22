"""Thread-safe runtime metrics with a bounded, low-cardinality surface."""

from __future__ import annotations

import re
import threading
from collections import defaultdict
from typing import Any


_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")


class RuntimeMetrics:
    """Counters and duration summaries for Python runtime paths."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._durations: dict[str, dict[str, float | int]] = {}

    def increment(self, name: str, value: int = 1) -> None:
        _validate_name(name)
        if value < 0:
            raise ValueError("metric increment must be non-negative")
        with self._lock:
            self._counters[name] += value

    def observe_ms(self, name: str, value: float) -> None:
        _validate_name(name)
        if value < 0:
            raise ValueError("metric duration must be non-negative")
        with self._lock:
            record = self._durations.setdefault(name, {"count": 0, "total_ms": 0.0, "max_ms": 0.0})
            record["count"] = int(record["count"]) + 1
            record["total_ms"] = float(record["total_ms"]) + value
            record["max_ms"] = max(float(record["max_ms"]), value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": "aegis-runtime-metrics-v1",
                "counters": dict(self._counters),
                "durations": {name: dict(record) for name, record in self._durations.items()},
            }

    def prometheus(self) -> str:
        snapshot = self.snapshot()
        lines = [
            "# HELP aegis_runtime_events_total Python runtime event count",
            "# TYPE aegis_runtime_events_total counter",
        ]
        for name, value in sorted(snapshot["counters"].items()):
            metric_name = "aegis_" + name.replace(".", "_").replace("-", "_") + "_total"
            lines.append(f"{metric_name} {value}")
        for name, record in sorted(snapshot["durations"].items()):
            metric_name = "aegis_" + name.replace(".", "_").replace("-", "_")
            lines.append(f"{metric_name}_count {record['count']}")
            lines.append(f"{metric_name}_sum_ms {record['total_ms']}")
            lines.append(f"{metric_name}_max_ms {record['max_ms']}")
        return "\n".join(lines) + "\n"


def _validate_name(name: str) -> None:
    if not _METRIC_NAME.fullmatch(name):
        raise ValueError(f"invalid metric name: {name!r}")
