from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from collections.abc import Mapping


REQUIRED_BROWSER_ARTIFACT_KINDS: tuple[str, ...] = (
    "url_before",
    "url_after",
    "dom_snapshot_before",
    "dom_snapshot_after",
    "screenshot_before",
    "screenshot_after",
    "accessibility_tree_after",
    "network_log",
)

_ARTIFACT_FILE_NAMES: Mapping[str, str] = {
    "url_before": "01-url-before.txt",
    "url_after": "02-url-after.txt",
    "dom_snapshot_before": "03-dom-before.html",
    "dom_snapshot_after": "04-dom-after.html",
    "screenshot_before": "05-screenshot-before.bin",
    "screenshot_after": "06-screenshot-after.bin",
    "accessibility_tree_after": "07-accessibility-after.json",
    "network_log": "08-network-log.jsonl",
}


@dataclass(frozen=True)
class BrowserLiveCollectorArtifact:
    kind: str
    bytes: bytes

    def __post_init__(self) -> None:
        if self.kind not in REQUIRED_BROWSER_ARTIFACT_KINDS:
            raise ValueError(f"unknown browser artifact kind: {self.kind}")
        if not isinstance(self.bytes, bytes):
            raise TypeError("browser artifact bytes must be bytes")


@dataclass(frozen=True)
class BrowserLiveCollectorProducerRun:
    output_root: Path
    run_directory: Path
    run_id: int
    action_id: int
    sequence_number: int
    artifact_paths: dict[str, Path]
    metadata_path: Path

    def metadata(self) -> dict[str, object]:
        return {
            "schema": "aegis-browser-live-collector-producer-v1",
            "run_id": self.run_id,
            "action_id": self.action_id,
            "sequence_number": self.sequence_number,
            "run_directory": str(self.run_directory),
            "artifact_paths": {
                kind: str(path) for kind, path in sorted(self.artifact_paths.items())
            },
            "verifier": "rust-browser-live-collector-run",
            "truth_claim": False,
        }


class BrowserLiveCollectorProducer:
    def __init__(self, output_root: str | Path, max_bytes: int = 32 * 1024 * 1024) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.output_root = Path(output_root)
        self.max_bytes = max_bytes

    def publish(
        self,
        run_id: int,
        action_id: int,
        sequence_number: int,
        artifacts: list[BrowserLiveCollectorArtifact],
    ) -> BrowserLiveCollectorProducerRun:
        _validate_positive_ids(run_id, action_id, sequence_number)
        ordered = _required_artifacts(artifacts, self.max_bytes)
        run_directory = (
            self.output_root / _run_directory_name(run_id, action_id, sequence_number)
        ).resolve()
        run_directory.mkdir(parents=True, exist_ok=True)

        artifact_paths: dict[str, Path] = {}
        for artifact in ordered:
            path = run_directory / _ARTIFACT_FILE_NAMES[artifact.kind]
            _write_staged(path, artifact.bytes)
            artifact_paths[artifact.kind] = path

        run = BrowserLiveCollectorProducerRun(
            output_root=self.output_root.resolve(),
            run_directory=run_directory,
            run_id=run_id,
            action_id=action_id,
            sequence_number=sequence_number,
            artifact_paths=artifact_paths,
            metadata_path=run_directory / "producer-metadata.json",
        )
        _write_staged(
            run.metadata_path,
            json.dumps(run.metadata(), sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        return run


def build_browser_live_collector_artifacts(
    *,
    url_before: str,
    url_after: str,
    dom_snapshot_before: str,
    dom_snapshot_after: str,
    screenshot_before: bytes,
    screenshot_after: bytes,
    accessibility_tree_after: str | bytes | Mapping[str, object],
    network_log: str | bytes | list[Mapping[str, object]],
) -> list[BrowserLiveCollectorArtifact]:
    return [
        BrowserLiveCollectorArtifact("url_before", _text_bytes(url_before)),
        BrowserLiveCollectorArtifact("url_after", _text_bytes(url_after)),
        BrowserLiveCollectorArtifact("dom_snapshot_before", _text_bytes(dom_snapshot_before)),
        BrowserLiveCollectorArtifact("dom_snapshot_after", _text_bytes(dom_snapshot_after)),
        BrowserLiveCollectorArtifact("screenshot_before", screenshot_before),
        BrowserLiveCollectorArtifact("screenshot_after", screenshot_after),
        BrowserLiveCollectorArtifact(
            "accessibility_tree_after",
            _jsonish_bytes(accessibility_tree_after),
        ),
        BrowserLiveCollectorArtifact("network_log", _network_log_bytes(network_log)),
    ]


def _required_artifacts(
    artifacts: list[BrowserLiveCollectorArtifact],
    max_bytes: int,
) -> list[BrowserLiveCollectorArtifact]:
    if len(artifacts) != len(REQUIRED_BROWSER_ARTIFACT_KINDS):
        raise ValueError("browser live collector requires exactly eight artifacts")
    ordered: list[BrowserLiveCollectorArtifact] = []
    for required_kind in REQUIRED_BROWSER_ARTIFACT_KINDS:
        matches = [artifact for artifact in artifacts if artifact.kind == required_kind]
        if not matches:
            raise ValueError(f"missing browser artifact kind: {required_kind}")
        if len(matches) > 1:
            raise ValueError(f"duplicate browser artifact kind: {required_kind}")
        artifact = matches[0]
        if not artifact.bytes:
            raise ValueError(f"empty browser artifact kind: {required_kind}")
        if len(artifact.bytes) > max_bytes:
            raise ValueError(f"oversized browser artifact kind: {required_kind}")
        ordered.append(artifact)
    return ordered


def _validate_positive_ids(run_id: int, action_id: int, sequence_number: int) -> None:
    if run_id <= 0 or action_id <= 0 or sequence_number <= 0:
        raise ValueError("run_id, action_id, and sequence_number must be positive")
    if run_id >= 1 << 128 or action_id >= 1 << 128:
        raise ValueError("run_id and action_id must fit Rust u128")
    if sequence_number >= 1 << 64:
        raise ValueError("sequence_number must fit Rust u64")


def _run_directory_name(run_id: int, action_id: int, sequence_number: int) -> str:
    return f"run-{run_id:032x}-action-{action_id:032x}-seq-{sequence_number:016x}"


def _write_staged(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temp_path.replace(path)
    _sync_parent(path.parent)


def _sync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _text_bytes(value: str) -> bytes:
    if not isinstance(value, str):
        raise TypeError("text browser artifact fields must be str")
    return value.encode("utf-8")


def _jsonish_bytes(value: str | bytes | Mapping[str, object]) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _network_log_bytes(value: str | bytes | list[Mapping[str, object]]) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if not value:
        return b"[]"
    return b"\n".join(
        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for entry in value
    )
