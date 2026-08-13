from __future__ import annotations

from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
import base64
import hashlib
import inspect
import json
import threading
from typing import Any, Mapping

from .browser_live_collector import (
    BrowserLiveCollectorArtifact,
    BrowserLiveCollectorProducer,
    BrowserLiveCollectorProducerRun,
    build_browser_live_collector_artifacts,
)


class BrowserRuntimeCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserRuntimeSnapshot:
    url: str
    dom_snapshot: str
    screenshot: bytes
    accessibility_tree: bytes


@dataclass(frozen=True)
class BrowserRuntimeCapture:
    before: BrowserRuntimeSnapshot
    after: BrowserRuntimeSnapshot
    producer_run: BrowserLiveCollectorProducerRun
    action_result: Any = field(compare=False)
    hot_evidence: "HotBrowserEvidenceBatch | None" = None


@dataclass(frozen=True)
class BrowserRuntimeColdPublishHandle:
    schema: str
    truth_claim: bool
    run_id: int
    action_id: int
    sequence_number: int
    background_publish_started: bool
    hot_returned_before_cold_publish: bool
    cold_publish_handle_hash: str
    _future: Future[BrowserLiveCollectorProducerRun] = field(repr=False, compare=False)

    def done(self) -> bool:
        return self._future.done()

    def wait(self, timeout: float | None = None) -> BrowserLiveCollectorProducerRun:
        return self._future.result(timeout=timeout)


@dataclass(frozen=True)
class BrowserRuntimeHotFirstCapture:
    before: BrowserRuntimeSnapshot
    after: BrowserRuntimeSnapshot
    action_result: Any = field(compare=False)
    hot_evidence: "HotBrowserEvidenceBatch"
    cold_publish: BrowserRuntimeColdPublishHandle


@dataclass(frozen=True)
class HotBrowserArtifactCommit:
    kind: str
    byte_len: int
    artifact_hash: str
    storage_ref_hash: str
    trust_level: str
    admission: str
    verifier: str
    handle_valid: bool


@dataclass(frozen=True)
class HotBrowserEvidenceBatch:
    schema: str
    truth_claim: bool
    verifier: str
    run_id: int
    action_id: int
    sequence_number: int
    artifact_count: int
    total_bytes: int
    no_file_roundtrip_on_hot_path: bool
    batch_digest: str
    commits: tuple[HotBrowserArtifactCommit, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "truth_claim": self.truth_claim,
            "verifier": self.verifier,
            "run_id": self.run_id,
            "action_id": self.action_id,
            "sequence_number": self.sequence_number,
            "artifact_count": self.artifact_count,
            "total_bytes": self.total_bytes,
            "no_file_roundtrip_on_hot_path": self.no_file_roundtrip_on_hot_path,
            "batch_digest": self.batch_digest,
            "commits": [commit.__dict__ for commit in self.commits],
        }


class BrowserRuntimeCollectorAdapter:
    def __init__(
        self,
        producer: BrowserLiveCollectorProducer,
        hot_evidence_publisher: Callable[[bytes], Any] | None = None,
        hot_evidence_batch_publisher: Callable[[list[BrowserLiveCollectorArtifact]], Any] | None = None,
    ) -> None:
        self.producer = producer
        self.hot_evidence_publisher = hot_evidence_publisher
        self.hot_evidence_batch_publisher = hot_evidence_batch_publisher

    async def capture_action(
        self,
        *,
        browser_session: Any,
        run_id: int,
        action_id: int,
        sequence_number: int,
        action: Callable[[], Awaitable[Any] | Any],
    ) -> BrowserRuntimeCapture:
        await _call_if_present(browser_session, "clear_network_log")
        before = await capture_browser_runtime_snapshot(browser_session)
        action_result = await _maybe_await(action())
        after = await capture_browser_runtime_snapshot(browser_session)
        network_log = await collect_browser_network_log(browser_session)
        artifacts = build_browser_live_collector_artifacts(
            url_before=before.url,
            url_after=after.url,
            dom_snapshot_before=before.dom_snapshot,
            dom_snapshot_after=after.dom_snapshot,
            screenshot_before=before.screenshot,
            screenshot_after=after.screenshot,
            accessibility_tree_after=after.accessibility_tree,
            network_log=network_log,
        )
        hot_evidence = None
        if self.hot_evidence_batch_publisher is not None:
            hot_evidence = publish_hot_browser_evidence_batch(
                run_id=run_id,
                action_id=action_id,
                sequence_number=sequence_number,
                artifacts=artifacts,
                batch_publisher=self.hot_evidence_batch_publisher,
            )
        elif self.hot_evidence_publisher is not None:
            hot_evidence = publish_hot_browser_evidence(
                run_id=run_id,
                action_id=action_id,
                sequence_number=sequence_number,
                artifacts=artifacts,
                publisher=self.hot_evidence_publisher,
            )
        producer_run = self.producer.publish(
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            artifacts=artifacts,
        )
        return BrowserRuntimeCapture(
            before=before,
            after=after,
            producer_run=producer_run,
            hot_evidence=hot_evidence,
            action_result=action_result,
        )

    async def capture_action_hot_first(
        self,
        *,
        browser_session: Any,
        run_id: int,
        action_id: int,
        sequence_number: int,
        action: Callable[[], Awaitable[Any] | Any],
    ) -> BrowserRuntimeHotFirstCapture:
        await _call_if_present(browser_session, "clear_network_log")
        before = await capture_browser_runtime_snapshot(browser_session)
        action_result = await _maybe_await(action())
        after = await capture_browser_runtime_snapshot(browser_session)
        network_log = await collect_browser_network_log(browser_session)
        artifacts = build_browser_live_collector_artifacts(
            url_before=before.url,
            url_after=after.url,
            dom_snapshot_before=before.dom_snapshot,
            dom_snapshot_after=after.dom_snapshot,
            screenshot_before=before.screenshot,
            screenshot_after=after.screenshot,
            accessibility_tree_after=after.accessibility_tree,
            network_log=network_log,
        )
        if self.hot_evidence_batch_publisher is not None:
            hot_evidence = publish_hot_browser_evidence_batch(
                run_id=run_id,
                action_id=action_id,
                sequence_number=sequence_number,
                artifacts=artifacts,
                batch_publisher=self.hot_evidence_batch_publisher,
            )
        elif self.hot_evidence_publisher is not None:
            hot_evidence = publish_hot_browser_evidence(
                run_id=run_id,
                action_id=action_id,
                sequence_number=sequence_number,
                artifacts=artifacts,
                publisher=self.hot_evidence_publisher,
            )
        else:
            raise BrowserRuntimeCaptureError("hot-first browser capture requires hot evidence")
        future: Future[BrowserLiveCollectorProducerRun] = Future()

        def publish_cold() -> None:
            try:
                producer_run = self.producer.publish(
                    run_id=run_id,
                    action_id=action_id,
                    sequence_number=sequence_number,
                    artifacts=artifacts,
                )
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(producer_run)

        thread = threading.Thread(
            target=publish_cold,
            name=f"aegis-browser-cold-publish-{sequence_number}",
            daemon=True,
        )
        thread.start()
        cold_publish = _cold_publish_handle(
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            hot_evidence=hot_evidence,
            future=future,
        )
        return BrowserRuntimeHotFirstCapture(
            before=before,
            after=after,
            action_result=action_result,
            hot_evidence=hot_evidence,
            cold_publish=cold_publish,
        )


def publish_hot_browser_evidence(
    *,
    run_id: int,
    action_id: int,
    sequence_number: int,
    artifacts: list[BrowserLiveCollectorArtifact],
    publisher: Callable[[bytes], Any],
) -> HotBrowserEvidenceBatch:
    commits = tuple(
        _hot_commit_from_record(artifact.kind, artifact.bytes, publisher(artifact.bytes))
        for artifact in artifacts
    )
    total_bytes = sum(commit.byte_len for commit in commits)
    digest_payload = {
        "schema": "aegis-hot-browser-evidence-batch-v1",
        "run_id": run_id,
        "action_id": action_id,
        "sequence_number": sequence_number,
        "commits": [
            {
                "kind": commit.kind,
                "byte_len": commit.byte_len,
                "artifact_hash": commit.artifact_hash,
                "storage_ref_hash": commit.storage_ref_hash,
            }
            for commit in commits
        ],
    }
    batch_digest = hashlib.blake2b(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        digest_size=32,
    ).hexdigest()
    return HotBrowserEvidenceBatch(
        schema="aegis-hot-browser-evidence-batch-v1",
        truth_claim=False,
        verifier="rust-hot-engine-or-python-dev-fallback",
        run_id=run_id,
        action_id=action_id,
        sequence_number=sequence_number,
        artifact_count=len(commits),
        total_bytes=total_bytes,
        no_file_roundtrip_on_hot_path=True,
        batch_digest=batch_digest,
        commits=commits,
    )


def _cold_publish_handle(
    *,
    run_id: int,
    action_id: int,
    sequence_number: int,
    hot_evidence: HotBrowserEvidenceBatch,
    future: Future[BrowserLiveCollectorProducerRun],
) -> BrowserRuntimeColdPublishHandle:
    hot_returned_before_cold_publish = not future.done()
    payload = {
        "schema": "aegis-browser-runtime-cold-publish-handle-v1",
        "run_id": run_id,
        "action_id": action_id,
        "sequence_number": sequence_number,
        "hot_evidence_batch_digest": hot_evidence.batch_digest,
        "background_publish_started": True,
        "hot_returned_before_cold_publish": hot_returned_before_cold_publish,
        "truth_claim": False,
    }
    return BrowserRuntimeColdPublishHandle(
        schema="aegis-browser-runtime-cold-publish-handle-v1",
        truth_claim=False,
        run_id=run_id,
        action_id=action_id,
        sequence_number=sequence_number,
        background_publish_started=True,
        hot_returned_before_cold_publish=hot_returned_before_cold_publish,
        cold_publish_handle_hash=hashlib.blake2b(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            digest_size=32,
        ).hexdigest(),
        _future=future,
    )


def publish_hot_browser_evidence_batch(
    *,
    run_id: int,
    action_id: int,
    sequence_number: int,
    artifacts: list[BrowserLiveCollectorArtifact],
    batch_publisher: Callable[[list[BrowserLiveCollectorArtifact]], Any],
) -> HotBrowserEvidenceBatch:
    raw = _batch_record_mapping(batch_publisher(artifacts))
    if raw.get("schema") != "aegis-hot-arena-commit-batch-v1":
        raise ValueError("hot browser batch publisher returned unexpected schema")
    commits_raw = raw.get("commits")
    if not isinstance(commits_raw, list):
        raise ValueError("hot browser batch publisher must return commit records")
    if len(commits_raw) != len(artifacts):
        raise ValueError("hot browser batch publisher commit count mismatch")
    commits = tuple(
        _hot_commit_from_record(artifact.kind, artifact.bytes, record)
        for artifact, record in zip(artifacts, commits_raw)
    )
    total_bytes = sum(commit.byte_len for commit in commits)
    declared_total = int(raw.get("total_bytes", total_bytes))
    if declared_total != total_bytes:
        raise ValueError("hot browser batch publisher total byte mismatch")
    digest_payload = {
        "schema": "aegis-hot-browser-evidence-batch-v1",
        "run_id": run_id,
        "action_id": action_id,
        "sequence_number": sequence_number,
        "hot_arena_batch_digest": str(raw["batch_digest"]),
        "arena_live_bytes": int(raw.get("arena_live_bytes", 0)),
        "arena_live_slots": int(raw.get("arena_live_slots", 0)),
        "commits": [
            {
                "kind": commit.kind,
                "byte_len": commit.byte_len,
                "artifact_hash": commit.artifact_hash,
                "storage_ref_hash": commit.storage_ref_hash,
            }
            for commit in commits
        ],
    }
    batch_digest = hashlib.blake2b(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        digest_size=32,
    ).hexdigest()
    return HotBrowserEvidenceBatch(
        schema="aegis-hot-browser-evidence-batch-v1",
        truth_claim=False,
        verifier=str(raw.get("verifier", "rust-hot-engine")),
        run_id=run_id,
        action_id=action_id,
        sequence_number=sequence_number,
        artifact_count=len(commits),
        total_bytes=total_bytes,
        no_file_roundtrip_on_hot_path=bool(raw.get("no_file_roundtrip_on_hot_path", False)),
        batch_digest=batch_digest,
        commits=commits,
    )


def _batch_record_mapping(record: Any) -> Mapping[str, Any]:
    if isinstance(record, str):
        record = json.loads(record)
    if hasattr(record, "__dict__"):
        raw = record.__dict__
    elif isinstance(record, Mapping):
        raw = record
    else:
        raise TypeError("hot evidence batch publisher must return JSON, mapping, or dataclass-like record")
    return raw


def _hot_commit_from_record(
    kind: str,
    payload: bytes,
    record: Any,
) -> HotBrowserArtifactCommit:
    if hasattr(record, "__dict__"):
        raw = record.__dict__
    elif isinstance(record, Mapping):
        raw = record
    else:
        raise TypeError("hot evidence publisher must return a mapping or dataclass-like record")
    byte_len = int(raw.get("byte_len", len(payload)))
    if byte_len != len(payload):
        raise ValueError(f"hot evidence byte length mismatch for {kind}")
    artifact_hash = str(raw["artifact_hash"])
    storage_ref_hash = str(raw["storage_ref_hash"])
    if len(artifact_hash) != 64 or len(storage_ref_hash) != 64:
        raise ValueError(f"hot evidence hash must be 64 hex characters for {kind}")
    return HotBrowserArtifactCommit(
        kind=kind,
        byte_len=byte_len,
        artifact_hash=artifact_hash,
        storage_ref_hash=storage_ref_hash,
        trust_level=str(raw.get("trust_level", "")),
        admission=str(raw.get("admission", "")),
        verifier=str(raw.get("verifier", "")),
        handle_valid=bool(raw.get("handle_valid", False)),
    )


async def capture_browser_runtime_snapshot(browser_session: Any) -> BrowserRuntimeSnapshot:
    page = await _resolve_page(browser_session)
    url = await _capture_url(browser_session, page)
    dom_snapshot = await _capture_dom(page)
    screenshot = await _capture_screenshot(browser_session, page)
    accessibility_tree = await _capture_accessibility(browser_session, page)
    return BrowserRuntimeSnapshot(
        url=url,
        dom_snapshot=dom_snapshot,
        screenshot=screenshot,
        accessibility_tree=accessibility_tree,
    )


async def collect_browser_network_log(browser_session: Any) -> list[Mapping[str, Any]]:
    for name in ("drain_network_log", "get_network_log", "network_log", "network_events"):
        if not hasattr(browser_session, name):
            continue
        value = getattr(browser_session, name)
        raw = await _maybe_await(value() if callable(value) else value)
        return _normalize_network_log(raw)
    return []


async def _resolve_page(browser_session: Any) -> Any:
    for name in ("get_current_page", "current_page", "page"):
        if not hasattr(browser_session, name):
            continue
        value = getattr(browser_session, name)
        page = await _maybe_await(value() if callable(value) else value)
        if page is not None:
            return page
    return browser_session


async def _capture_url(browser_session: Any, page: Any) -> str:
    for owner, names in (
        (page, ("url",)),
        (browser_session, ("get_current_page_url", "current_url", "url")),
    ):
        for name in names:
            if not hasattr(owner, name):
                continue
            value = getattr(owner, name)
            url = await _maybe_await(value() if callable(value) else value)
            if isinstance(url, str) and url:
                return url
    raise BrowserRuntimeCaptureError("browser runtime did not expose a non-empty URL")


async def _capture_dom(page: Any) -> str:
    if hasattr(page, "content"):
        content = getattr(page, "content")
        dom = await _maybe_await(content() if callable(content) else content)
        if isinstance(dom, str) and dom:
            return dom
    if hasattr(page, "evaluate"):
        evaluate = getattr(page, "evaluate")
        dom = await _maybe_await(evaluate("document.documentElement.outerHTML"))
        if isinstance(dom, str) and dom:
            return dom
    raise BrowserRuntimeCaptureError("browser runtime did not expose a non-empty DOM snapshot")


async def _capture_screenshot(browser_session: Any, page: Any) -> bytes:
    if hasattr(page, "screenshot"):
        screenshot = getattr(page, "screenshot")
        try:
            raw = await _maybe_await(screenshot(full_page=True))
        except TypeError:
            raw = await _maybe_await(screenshot())
        return _normalize_screenshot(raw)
    if hasattr(browser_session, "get_browser_state_summary"):
        summary = await _maybe_await(
            browser_session.get_browser_state_summary(include_screenshot=True)
        )
        if hasattr(summary, "screenshot"):
            return _normalize_screenshot(summary.screenshot)
    raise BrowserRuntimeCaptureError("browser runtime did not expose a screenshot")


async def _capture_accessibility(browser_session: Any, page: Any) -> bytes:
    for owner, names in (
        (page, ("accessibility_snapshot", "accessibility_tree")),
        (browser_session, ("get_accessibility_tree", "accessibility_tree")),
    ):
        for name in names:
            if not hasattr(owner, name):
                continue
            value = getattr(owner, name)
            raw = await _maybe_await(value() if callable(value) else value)
            if raw is not None:
                return _json_bytes(raw)
    return b'{"source":"browser-runtime-adapter","unavailable":true}'


def _normalize_screenshot(raw: Any) -> bytes:
    if isinstance(raw, bytes) and raw:
        return raw
    if isinstance(raw, str) and raw:
        payload = raw.split(",", 1)[1] if raw.startswith("data:") and "," in raw else raw
        decoded = base64.b64decode(payload)
        if decoded:
            return decoded
    raise BrowserRuntimeCaptureError("browser runtime screenshot was empty or unsupported")


def _normalize_network_log(raw: Any) -> list[Mapping[str, Any]]:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return [_mapping_entry(entry) for entry in raw]
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    elif isinstance(raw, str):
        text = raw
    else:
        return [_mapping_entry(raw)]
    stripped = text.strip()
    if not stripped:
        return []
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return [{"raw": stripped}]
    if isinstance(parsed, list):
        return [_mapping_entry(entry) for entry in parsed]
    return [_mapping_entry(parsed)]


def _mapping_entry(entry: Any) -> Mapping[str, Any]:
    if isinstance(entry, Mapping):
        return {str(key): _json_safe(value) for key, value in entry.items()}
    return {"raw": _json_safe(entry)}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _json_bytes(raw: Any) -> bytes:
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return json.dumps(_json_safe(raw), sort_keys=True, separators=(",", ":")).encode("utf-8")


async def _call_if_present(owner: Any, name: str) -> None:
    if hasattr(owner, name):
        value = getattr(owner, name)
        await _maybe_await(value() if callable(value) else value)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
