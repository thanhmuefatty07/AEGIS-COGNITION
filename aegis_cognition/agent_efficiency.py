"""Local, provider-neutral efficiency primitives based on cache-projection research.

These helpers are deliberately opt-in.  They reduce transport/context cost,
but they never replace the root agent's verification authority or turn a
candidate observation into an authoritative result.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


OBSERVATION_PACK_SCHEMA_V1 = "aegis-observation-pack-v1"
EVIDENCE_RECEIPT_SCHEMA_V1 = "aegis-evidence-receipt-v1"
ACTION_FUSION_SCHEMA_V1 = "aegis-action-fusion-v1"
CAPABILITY_EVALUATION_SCHEMA_V1 = "aegis-capability-evaluation-v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_text(value: object, name: str, maximum: int) -> str:
    if type(value) is not str or not value.strip() or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} must be bounded non-empty text")
    return value


@dataclass(frozen=True, slots=True)
class ObservationProjection:
    schema: str
    tool_call_id: str
    content_hash: str
    byte_length: int
    line_count: int
    inline_text: str
    archived: bool
    recall_handle: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "tool_call_id": self.tool_call_id,
            "content_hash": self.content_hash,
            "byte_length": self.byte_length,
            "line_count": self.line_count,
            "inline_text": self.inline_text,
            "archived": self.archived,
            "recall_handle": self.recall_handle,
        }


class ObservationPack:
    """Keep large successful tool observations by hash and send a projection.

    The first two observations for a tool call remain full so the model can
    establish a local schema.  Later large observations become a bounded
    excerpt plus a verifiable recall handle.  Original bytes stay available in
    memory and optionally in a caller-owned archive directory.
    """

    def __init__(
        self,
        *,
        archive_root: Path | None = None,
        threshold_bytes: int = 10 * 1024,
        full_sends: int = 2,
        excerpt_bytes: int = 1_024,
        recall_max_bytes: int = 16 * 1024,
        recall_max_lines: int = 400,
    ) -> None:
        if threshold_bytes < 1 or full_sends < 0 or excerpt_bytes < 1:
            raise ValueError("observation pack bounds are invalid")
        if recall_max_bytes < excerpt_bytes or recall_max_lines < 1:
            raise ValueError("observation recall bounds are invalid")
        self.threshold_bytes = threshold_bytes
        self.full_sends = full_sends
        self.excerpt_bytes = excerpt_bytes
        self.recall_max_bytes = recall_max_bytes
        self.recall_max_lines = recall_max_lines
        self._archive_root = archive_root.expanduser().resolve() if archive_root is not None else None
        self._objects: dict[str, bytes] = {}
        self._send_counts: dict[str, int] = {}
        self._lock = threading.RLock()
        if self._archive_root is not None:
            self._archive_root.mkdir(parents=True, exist_ok=True)
            if self._archive_root.is_symlink():
                raise ValueError("observation archive root must not be a symlink")

    def pack(self, tool_call_id: str, text: str, *, successful_text: bool = True) -> ObservationProjection:
        _require_text(tool_call_id, "tool_call_id", 256)
        _require_text(text, "observation text", 64 * 1024 * 1024)
        raw = text.encode("utf-8")
        content_hash = _sha256_bytes(raw)
        line_count = text.count("\n") + 1
        with self._lock:
            self._objects[content_hash] = raw
            self._persist(content_hash, raw)
            count = self._send_counts.get(tool_call_id, 0)
            self._send_counts[tool_call_id] = count + 1
            use_pack = successful_text and len(raw) > self.threshold_bytes and count >= self.full_sends
            inline = text if not use_pack else _bounded_utf8_excerpt(raw, self.excerpt_bytes)
            if use_pack:
                inline = f"[OBSERVATION_PACK sha256={content_hash} bytes={len(raw)} lines={line_count}]\n{inline}"
            return ObservationProjection(
                schema=OBSERVATION_PACK_SCHEMA_V1,
                tool_call_id=tool_call_id,
                content_hash=content_hash,
                byte_length=len(raw),
                line_count=line_count,
                inline_text=inline,
                archived=use_pack,
                recall_handle=f"observation:{content_hash}" if use_pack else None,
            )

    def recall(self, handle: str) -> str:
        if type(handle) is not str or not handle.startswith("observation:"):
            raise ValueError("observation recall handle is invalid")
        content_hash = handle.removeprefix("observation:")
        if len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash):
            raise ValueError("observation recall handle digest is invalid")
        with self._lock:
            raw = self._objects.get(content_hash)
            if raw is None and self._archive_root is not None:
                path = self._archive_root / content_hash
                if path.is_symlink() or path.resolve().parent != self._archive_root:
                    raise ValueError("observation recall path escapes its archive root")
                raw = path.read_bytes()
            if raw is None or _sha256_bytes(raw) != content_hash:
                raise KeyError("observation object is unavailable or tampered")
        bounded = raw[: self.recall_max_bytes].decode("utf-8", errors="replace")
        lines = bounded.splitlines()
        return "\n".join(lines[: self.recall_max_lines])

    def _persist(self, content_hash: str, raw: bytes) -> None:
        if self._archive_root is None:
            return
        path = self._archive_root / content_hash
        if path.exists() and not path.is_file():
            raise ValueError("observation archive object path is not a regular file")
        if path.is_symlink() or path.resolve().parent != self._archive_root:
            raise ValueError("observation archive object path is unsafe")
        if not path.exists():
            temporary = self._archive_root / f".{content_hash}.tmp"
            temporary.write_bytes(raw)
            temporary.replace(path)


def _bounded_utf8_excerpt(raw: bytes, maximum: int) -> str:
    return raw[:maximum].decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    schema: str
    source_hash: str
    command: str
    exit_code: int
    status: str
    quotes: tuple[str, ...]
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "source_hash": self.source_hash,
            "command": self.command,
            "exit_code": self.exit_code,
            "status": self.status,
            "quotes": list(self.quotes),
            "summary": self.summary,
        }

    def verify(self, source: str) -> bool:
        if _sha256_bytes(source.encode("utf-8")) != self.source_hash:
            return False
        if self.status != ("PASSED" if self.exit_code == 0 else "FAILED"):
            return False
        if any(not quote or quote not in source for quote in self.quotes):
            return False
        return bool(self.summary.strip())


@dataclass(frozen=True, slots=True)
class EvidenceReduction:
    original: str
    reduced: str
    receipt: EvidenceReceipt | None
    used_reduction: bool
    reason: str


class EvidencePreservingReducer:
    """Reduce diagnostic output only when its source hash and quotes verify."""

    def __init__(self, *, minimum_bytes: int = 4_096, maximum_chars: int = 600_000) -> None:
        if minimum_bytes < 1 or maximum_chars < minimum_bytes:
            raise ValueError("evidence reducer bounds are invalid")
        self.minimum_bytes = minimum_bytes
        self.maximum_chars = maximum_chars

    def reduce(self, source: str, *, command: str, exit_code: int) -> EvidenceReduction:
        _require_text(source, "diagnostic source", self.maximum_chars)
        _require_text(command, "diagnostic command", 4_096)
        if type(exit_code) is not int:
            raise ValueError("exit_code must be an integer")
        if len(source.encode("utf-8")) < self.minimum_bytes:
            return EvidenceReduction(source, source, None, False, "below_minimum_size")
        if _contains_likely_secret(source):
            return EvidenceReduction(source, source, None, False, "likely_secret_content")
        lines = source.splitlines()
        selected: list[str] = []
        selected.extend(lines[:12])
        selected.extend(line for line in lines if _diagnostic_line(line))
        selected.extend(lines[-12:])
        quotes = tuple(dict.fromkeys(line for line in selected if line.strip()))
        if not quotes:
            return EvidenceReduction(source, source, None, False, "no_evidence_quotes")
        status = "PASSED" if exit_code == 0 else "FAILED"
        summary = f"diagnostic command {status.lower()} with {len(quotes)} exact evidence quote(s)"
        receipt = EvidenceReceipt(
            schema=EVIDENCE_RECEIPT_SCHEMA_V1,
            source_hash=_sha256_bytes(source.encode("utf-8")),
            command=command,
            exit_code=exit_code,
            status=status,
            quotes=quotes,
            summary=summary,
        )
        if not receipt.verify(source):
            return EvidenceReduction(source, source, None, False, "receipt_verification_failed")
        reduced = json.dumps(
            {"receipt": receipt.as_dict(), "exact_quotes": list(receipt.quotes)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(reduced.encode("utf-8")) >= len(source.encode("utf-8")):
            return EvidenceReduction(source, source, receipt, False, "reduction_not_smaller")
        return EvidenceReduction(source, reduced, receipt, True, "verified_exact_quote_reduction")


def _contains_likely_secret(source: str) -> bool:
    lowered = source.casefold()
    return any(marker in lowered for marker in ("api_key=", "authorization: bearer", "private key", "secret="))


def _diagnostic_line(line: str) -> bool:
    lowered = line.casefold()
    return any(marker in lowered for marker in ("error", "fail", "traceback", "warning", "passed", "test"))


@dataclass(frozen=True, slots=True)
class ActionFusionResult:
    schema: str
    path: str
    mutation_output: object
    command_output: object
    mutation_hash: str


class ActionFusion:
    """Serialize a safe mutation→command pair for one file."""

    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}

    def run(
        self,
        path: Path,
        mutation: Callable[[Path], object],
        command: Callable[[Path], object],
        *,
        requires_intermediate_inspection: bool = False,
    ) -> ActionFusionResult:
        if requires_intermediate_inspection:
            raise ValueError("action fusion is unsafe when the command needs intermediate inspection")
        if not callable(mutation) or not callable(command):
            raise TypeError("mutation and command must be callable")
        resolved = path.expanduser().resolve()
        key = str(resolved)
        with self._guard:
            lock = self._locks.setdefault(key, threading.RLock())
        with lock:
            mutation_output = mutation(resolved)
            mutation_hash = _file_hash(resolved)
            if _file_hash(resolved) != mutation_hash:
                raise RuntimeError("file changed between mutation and fused command")
            command_output = command(resolved)
        return ActionFusionResult(
            ACTION_FUSION_SCHEMA_V1, str(resolved), mutation_output, command_output, mutation_hash
        )


def _file_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("action fusion path must be a regular non-symlink file")
    return _sha256_bytes(path.read_bytes())


_DISPOSABLE_COPY_IGNORED_NAMES = frozenset(
    {
        ".aws",
        ".azure",
        ".git",
        ".gnupg",
        ".local",
        ".pytest_cache",
        ".ruff_cache",
        ".ssh",
        ".venv",
        "__pycache__",
        "build",
        "credentials",
        "dist",
        "node_modules",
        "secrets",
        "target",
        "venv",
    }
)
_DISPOSABLE_COPY_IGNORED_FILES = frozenset(
    {"credentials.json", "id_ed25519", "id_rsa", "service-account.json", "token.json"}
)
_DISPOSABLE_COPY_IGNORED_SUFFIXES = frozenset(
    {".crt", ".jks", ".key", ".keystore", ".p12", ".p7b", ".p7c", ".pem", ".pfx"}
)


def _ignore_disposable_worktree_entries(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        normalized = name.casefold()
        entry = Path(directory) / name
        is_junction = getattr(entry, "is_junction", lambda: False)()
        if (
            entry.is_symlink()
            or is_junction
            or normalized.startswith(".env")
            or normalized in _DISPOSABLE_COPY_IGNORED_NAMES
            or normalized in _DISPOSABLE_COPY_IGNORED_FILES
            or entry.suffix.casefold() in _DISPOSABLE_COPY_IGNORED_SUFFIXES
        ):
            ignored.add(name)
    return ignored


class DisposableWorktree:
    """Create a disposable checkout and omit known local/private paths on copy fallback."""

    def __init__(self, repository: Path, *, ref: str | None = None, lineage: str = "root") -> None:
        self.repository = repository.expanduser().resolve()
        self.ref = ref or "HEAD"
        self.lineage = _require_text(lineage, "lineage", 512)
        self.path: Path | None = None
        self._temporary_root: Path | None = None
        self._git_worktree = False

    def __enter__(self) -> Path:
        if not self.repository.is_dir():
            raise ValueError("disposable worktree repository must be a directory")
        temporary = Path(tempfile.mkdtemp(prefix="aegis-worktree-"))
        shutil.rmtree(temporary)
        try:
            result = subprocess.run(
                ("git", "worktree", "add", "--detach", str(temporary), self.ref),
                cwd=self.repository,
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            result = None
        if result is not None and result.returncode == 0:
            self._git_worktree = True
        else:
            try:
                shutil.copytree(self.repository, temporary, ignore=_ignore_disposable_worktree_entries)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        self._temporary_root = temporary
        self.path = temporary
        return temporary

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        temporary = self._temporary_root
        if temporary is None:
            return
        if self._git_worktree:
            subprocess.run(
                ("git", "worktree", "remove", "--force", str(temporary)),
                cwd=self.repository,
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        if temporary.exists():
            shutil.rmtree(temporary)
        self.path = None
        self._temporary_root = None
        self._git_worktree = False


@dataclass(frozen=True, slots=True)
class CapabilityEvaluation:
    schema: str
    floor_passed: bool
    efficiency_improved: bool
    baseline_cost: float
    candidate_cost: float
    baseline_capability: Mapping[str, float]
    candidate_capability: Mapping[str, float]
    failed_tasks: tuple[str, ...]

    @property
    def capability_delta(self) -> float:
        if not self.baseline_capability:
            return 0.0
        return sum(
            self.candidate_capability[key] - self.baseline_capability[key] for key in self.baseline_capability
        ) / len(self.baseline_capability)


def evaluate_capability_floor(
    baseline_capability: Mapping[str, float],
    candidate_capability: Mapping[str, float],
    *,
    baseline_cost: float,
    candidate_cost: float,
    floor_ratio: float = 1.0,
) -> CapabilityEvaluation:
    if not baseline_capability:
        raise ValueError("baseline_capability must not be empty")
    if set(candidate_capability) != set(baseline_capability):
        raise ValueError("baseline and candidate capability keys must match")
    if floor_ratio < 0 or floor_ratio != floor_ratio:
        raise ValueError("floor_ratio must be finite and non-negative")
    if baseline_cost < 0 or candidate_cost < 0:
        raise ValueError("costs must be non-negative")
    failed = tuple(
        sorted(
            key for key, baseline in baseline_capability.items() if candidate_capability[key] < baseline * floor_ratio
        )
    )
    return CapabilityEvaluation(
        schema=CAPABILITY_EVALUATION_SCHEMA_V1,
        floor_passed=not failed,
        efficiency_improved=candidate_cost < baseline_cost,
        baseline_cost=baseline_cost,
        candidate_cost=candidate_cost,
        baseline_capability=dict(baseline_capability),
        candidate_capability=dict(candidate_capability),
        failed_tasks=failed,
    )


__all__ = [
    "ACTION_FUSION_SCHEMA_V1",
    "CAPABILITY_EVALUATION_SCHEMA_V1",
    "EVIDENCE_RECEIPT_SCHEMA_V1",
    "OBSERVATION_PACK_SCHEMA_V1",
    "ActionFusion",
    "ActionFusionResult",
    "CapabilityEvaluation",
    "DisposableWorktree",
    "EvidencePreservingReducer",
    "EvidenceReceipt",
    "EvidenceReduction",
    "ObservationPack",
    "ObservationProjection",
    "evaluate_capability_floor",
]
