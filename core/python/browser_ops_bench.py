from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

try:
    from .browser_live_collector import (
        REQUIRED_BROWSER_ARTIFACT_KINDS,
        BrowserLiveCollectorProducer,
    )
    from .browser_runtime_adapter import BrowserRuntimeCollectorAdapter
except ImportError:
    from browser_live_collector import (
        REQUIRED_BROWSER_ARTIFACT_KINDS,
        BrowserLiveCollectorProducer,
    )
    from browser_runtime_adapter import BrowserRuntimeCollectorAdapter


BrowserTaskAction = Callable[[Any], Awaitable[Any] | Any]


@dataclass(frozen=True)
class BrowserOpsBenchPredicate:
    name: str
    artifact_kind: str
    contains_utf8: str | None = None
    min_bytes: int = 1

    def evaluate(self, artifact_paths: dict[str, Path]) -> BrowserOpsBenchPredicateResult:
        if not self.name:
            return BrowserOpsBenchPredicateResult(self.name, self.artifact_kind, "", 0, False, "empty predicate name")
        if self.artifact_kind not in REQUIRED_BROWSER_ARTIFACT_KINDS:
            return BrowserOpsBenchPredicateResult(
                self.name,
                self.artifact_kind,
                "",
                0,
                False,
                f"unknown artifact kind: {self.artifact_kind}",
            )
        path = artifact_paths.get(self.artifact_kind)
        if path is None:
            return BrowserOpsBenchPredicateResult(
                self.name,
                self.artifact_kind,
                "",
                0,
                False,
                "missing artifact path",
            )
        payload = path.read_bytes()
        if len(payload) < self.min_bytes:
            return BrowserOpsBenchPredicateResult(
                self.name,
                self.artifact_kind,
                str(path),
                len(payload),
                False,
                f"artifact below minimum bytes: {len(payload)} < {self.min_bytes}",
            )
        if self.contains_utf8 is not None:
            text = payload.decode("utf-8", errors="replace")
            if self.contains_utf8 not in text:
                return BrowserOpsBenchPredicateResult(
                    self.name,
                    self.artifact_kind,
                    str(path),
                    len(payload),
                    False,
                    "substring missing from artifact",
                )
        return BrowserOpsBenchPredicateResult(
            self.name,
            self.artifact_kind,
            str(path),
            len(payload),
            True,
            "ok",
        )


@dataclass(frozen=True)
class BrowserOpsBenchPredicateResult:
    name: str
    artifact_kind: str
    artifact_path: str
    byte_len: int
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "artifact_kind": self.artifact_kind,
            "artifact_path": self.artifact_path,
            "byte_len": self.byte_len,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BrowserOpsBenchTask:
    task_id: str
    run_id: int
    action_id: int
    sequence_number: int
    action: BrowserTaskAction = field(compare=False)
    predicates: tuple[BrowserOpsBenchPredicate, ...] = ()

    def validate(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        if self.run_id <= 0 or self.action_id <= 0 or self.sequence_number <= 0:
            raise ValueError("run_id, action_id, and sequence_number must be positive")
        if not self.predicates:
            raise ValueError("at least one artifact predicate is required")


@dataclass(frozen=True)
class BrowserOpsBenchTaskRecord:
    task_id: str
    ok: bool
    run_directory: str
    producer_metadata_path: str
    predicate_results: tuple[BrowserOpsBenchPredicateResult, ...]
    action_result_type: str
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "ok": self.ok,
            "run_directory": self.run_directory,
            "producer_metadata_path": self.producer_metadata_path,
            "predicate_results": [result.to_dict() for result in self.predicate_results],
            "action_result_type": self.action_result_type,
            "error": self.error,
        }


@dataclass(frozen=True)
class BrowserOpsBenchScorecard:
    suite_name: str
    records: tuple[BrowserOpsBenchTaskRecord, ...]

    @property
    def passed(self) -> int:
        return sum(1 for record in self.records if record.ok)

    @property
    def failed(self) -> int:
        return len(self.records) - self.passed

    @property
    def overall_ok(self) -> bool:
        return bool(self.records) and self.failed == 0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "aegis-browser-ops-bench-scorecard-v1",
            "suite_name": self.suite_name,
            "total": len(self.records),
            "passed": self.passed,
            "failed": self.failed,
            "overall_ok": self.overall_ok,
            "truth_claim": False,
            "verifier": "rust-browser-live-collector-run",
            "records": [record.to_dict() for record in self.records],
        }


class BrowserOpsBench:
    def __init__(self, output_root: str | Path, suite_name: str = "BrowserOpsBench") -> None:
        self.output_root = Path(output_root)
        self.suite_name = suite_name
        self.adapter = BrowserRuntimeCollectorAdapter(
            BrowserLiveCollectorProducer(self.output_root / "collector-runs")
        )

    async def run_task(self, browser_session: Any, task: BrowserOpsBenchTask) -> BrowserOpsBenchTaskRecord:
        task.validate()
        try:
            capture = await self.adapter.capture_action(
                browser_session=browser_session,
                run_id=task.run_id,
                action_id=task.action_id,
                sequence_number=task.sequence_number,
                action=lambda: task.action(browser_session),
            )
        except Exception as exc:
            return BrowserOpsBenchTaskRecord(
                task_id=task.task_id,
                ok=False,
                run_directory="",
                producer_metadata_path="",
                predicate_results=(),
                action_result_type="",
                error=f"{type(exc).__name__}: {exc}",
            )
        predicate_results = tuple(
            predicate.evaluate(capture.producer_run.artifact_paths)
            for predicate in task.predicates
        )
        return BrowserOpsBenchTaskRecord(
            task_id=task.task_id,
            ok=bool(predicate_results) and all(result.ok for result in predicate_results),
            run_directory=str(capture.producer_run.run_directory),
            producer_metadata_path=str(capture.producer_run.metadata_path),
            predicate_results=predicate_results,
            action_result_type=type(capture.action_result).__name__,
        )

    async def run_tasks(
        self,
        tasks: tuple[BrowserOpsBenchTask, ...],
        browser_session_factory: Callable[[BrowserOpsBenchTask], Any],
    ) -> BrowserOpsBenchScorecard:
        records = []
        for task in tasks:
            session = browser_session_factory(task)
            records.append(await self.run_task(session, task))
        return BrowserOpsBenchScorecard(self.suite_name, tuple(records))

    def write_scorecard(
        self,
        scorecard: BrowserOpsBenchScorecard,
        path: str | Path | None = None,
    ) -> Path:
        report_path = Path(path) if path is not None else self.output_root / "browser-ops-scorecard.json"
        payload = json.dumps(scorecard.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        _write_staged(report_path, payload)
        return report_path


def _write_staged(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)
