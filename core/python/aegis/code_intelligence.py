"""Bounded local source mapping for the Living Workspace.

The mapper emits a read-only, hash-bound projection. Extracted calls and
lineage are candidates; they are never presented as compiler-proven facts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import time
from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Self

MAX_FILES = 10_000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_SYMBOLS_PER_FILE = 2_000
MAX_IMPORTS_PER_FILE = 1_000
MAX_WATCH_EVENTS = 2_048
IGNORED_DIRECTORIES = frozenset(
    {
        ".aegis",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
    }
)


class CodeIntelligenceError(ValueError):
    """The source projection cannot be created safely."""


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: str
    root: str
    vcs: str
    repository_root: str | None
    common_git_dir: str | None
    checkout_id: str
    branch: str | None
    head: str | None
    dirty: bool
    dirty_paths: tuple[str, ...]


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    kind: str
    line: int
    signature_hash: str


@dataclass(frozen=True)
class SourceFileRecord:
    relative_path: str
    language: str
    size_bytes: int
    modified_ns: int
    content_hash: str | None
    extraction_status: str
    symbols: tuple[SymbolRecord, ...] = ()
    imports: tuple[str, ...] = ()
    error: str | None = None
    symlink_target: str | None = None


@dataclass(frozen=True)
class LineageCandidate:
    source_path: str
    symbol: str
    candidate_paths: tuple[str, ...]
    reason: str
    certainty: str = "CANDIDATE"


@dataclass(frozen=True)
class SourceSnapshot:
    identity: ProjectIdentity
    revision: str
    created_at_ms: int
    files: tuple[SourceFileRecord, ...]
    lineage: tuple[LineageCandidate, ...]
    stale_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    overflowed: bool = False


@dataclass(frozen=True)
class SnapshotDelta:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    deleted: tuple[str, ...]
    renamed: tuple[tuple[str, str], ...]
    copied: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class WatchEvent:
    path: str
    kind: str


class BoundedSnapshotWatcher:
    """Bounded event hint queue; overflow requests a complete rescan."""

    def __init__(self, max_events: int = MAX_WATCH_EVENTS) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._events: deque[WatchEvent] = deque(maxlen=max_events)
        self._max_events = max_events
        self.overflowed = False

    def notify(self, path: str, kind: str = "changed") -> bool:
        if not isinstance(path, str) or not path.strip() or "\x00" in path:
            raise ValueError("watch path must be a non-empty string")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("watch event kind must be a non-empty string")
        if len(self._events) >= self._max_events:
            self.overflowed = True
            return False
        self._events.append(WatchEvent(path, kind))
        return True

    def drain(self) -> tuple[WatchEvent, ...]:
        events = tuple(self._events)
        self._events.clear()
        return events

    def consume_overflow(self) -> bool:
        overflowed = self.overflowed
        self.overflowed = False
        return overflowed

    def mark_overflow(self) -> None:
        """Force the next snapshot to perform a complete rescan."""

        self.overflowed = True


class NativeSnapshotWatcher:
    """Rust-backed watcher adapter that feeds the bounded Python projection."""

    def __init__(self, root: str | Path, *, max_events: int = MAX_WATCH_EVENTS) -> None:
        if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        try:
            from .native import native_module
        except ImportError:
            from aegis.native import native_module  # type: ignore[import-not-found]
        self._native = native_module()
        self._max_events = min(max_events, MAX_WATCH_EVENTS)
        self._token = self._native.aegis_start_source_watcher(str(Path(root).expanduser().resolve()))
        self._closed = False

    def poll(self) -> BoundedSnapshotWatcher:
        if self._closed:
            raise CodeIntelligenceError("native source watcher is closed")
        raw = self._native.aegis_poll_source_watcher(self._token, self._max_events)
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise CodeIntelligenceError("native source watcher returned invalid JSON") from error
        if not isinstance(payload, dict) or payload.get("schema") != "aegis-source-watch-events-v1":
            raise CodeIntelligenceError("native source watcher returned an invalid schema")
        events = payload.get("events")
        if not isinstance(events, list) or not isinstance(payload.get("overflowed"), bool):
            raise CodeIntelligenceError("native source watcher returned invalid events")
        bounded = BoundedSnapshotWatcher(self._max_events)
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("path"), str):
                raise CodeIntelligenceError("native source watcher returned an invalid path")
            bounded.notify(event["path"], str(event.get("kind", "changed")))
        if payload["overflowed"]:
            bounded.mark_overflow()
        return bounded

    def close(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        return bool(self._native.aegis_stop_source_watcher(self._token))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


class SourceMapper:
    """Create deterministic source snapshots with bounded local I/O."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_files: int = MAX_FILES,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise CodeIntelligenceError("source root must be an existing directory")
        for value, name in (
            (max_files, "max_files"),
            (max_file_bytes, "max_file_bytes"),
            (max_total_bytes, "max_total_bytes"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    def identity(self) -> ProjectIdentity:
        git = _git_identity(self.root)
        root_key = str(self.root).casefold()
        project_id = _digest("aegis-project-v1", root_key, git.repository_root or "")
        checkout_id = _digest(
            "aegis-checkout-v1",
            project_id,
            git.common_git_dir or "",
            git.checkout_path,
            git.head or "",
            git.branch or "",
        )
        return ProjectIdentity(
            project_id=project_id,
            root=str(self.root),
            vcs="git" if git.repository_root else "none",
            repository_root=git.repository_root,
            common_git_dir=git.common_git_dir,
            checkout_id=checkout_id,
            branch=git.branch,
            head=git.head,
            dirty=bool(git.dirty_paths),
            dirty_paths=git.dirty_paths,
        )

    def snapshot(
        self,
        previous: SourceSnapshot | None = None,
        *,
        watcher: BoundedSnapshotWatcher | None = None,
    ) -> SourceSnapshot:
        identity = self.identity()
        files: list[SourceFileRecord] = []
        total_bytes = 0
        overflowed = watcher.consume_overflow() if watcher is not None else False
        if watcher is not None:
            for event in watcher.drain():
                relative = _relative_event_path(self.root, event.path)
                if relative is None:
                    overflowed = True
        for path in self._walk_files():
            relative = path.relative_to(self.root).as_posix()
            if len(files) >= self.max_files:
                overflowed = True
                break
            record = self._extract(path, relative, total_bytes)
            prior = _file_by_path(previous.files, relative) if previous is not None else None
            if record.extraction_status == "SYNTAX_ERROR" and prior is not None:
                record = replace(
                    record,
                    extraction_status="SYNTAX_ERROR_STALE_FALLBACK",
                    symbols=prior.symbols,
                    imports=prior.imports,
                )
            files.append(record)
            total_bytes += record.size_bytes
            if total_bytes > self.max_total_bytes:
                overflowed = True
                break
        files.sort(key=lambda item: item.relative_path)
        lineage = _build_lineage(files)
        previous_paths = {item.relative_path for item in previous.files} if previous else set()
        current_paths = {item.relative_path for item in files}
        stale = tuple(
            sorted(
                item.relative_path
                for item in files
                if previous is not None
                and (old := _file_by_path(previous.files, item.relative_path)) is not None
                and old.content_hash != item.content_hash
            )
        )
        # A bounded/failed scan cannot distinguish deletion from an omitted file.
        deleted = tuple(sorted(previous_paths - current_paths)) if previous and not overflowed else ()
        revision = _snapshot_revision(identity, files, lineage)
        return SourceSnapshot(
            identity=identity,
            revision=revision,
            created_at_ms=int(time.time() * 1000),
            files=tuple(files),
            lineage=tuple(lineage),
            stale_paths=stale,
            deleted_paths=deleted,
            overflowed=overflowed,
        )

    def _walk_files(self) -> Iterator[Path]:
        for directory, dirnames, filenames in os.walk(self.root, topdown=True, followlinks=False):
            dirnames[:] = sorted(
                name for name in dirnames if name not in IGNORED_DIRECTORIES and not _is_link(Path(directory) / name)
            )
            for name in sorted(filenames):
                path = Path(directory) / name
                if _is_link(path):
                    yield path
                else:
                    yield path

    def _extract(self, path: Path, relative: str, total_bytes: int) -> SourceFileRecord:
        try:
            stat = path.stat(follow_symlinks=False)
        except OSError as error:
            return SourceFileRecord(relative, _language(path), 0, 0, None, "STAT_ERROR", error=str(error))
        if _is_link(path):
            target = os.readlink(path) if path.is_symlink() else None
            return SourceFileRecord(
                relative,
                _language(path),
                stat.st_size,
                stat.st_mtime_ns,
                None,
                "SKIPPED_EXTERNAL_LINK",
                symlink_target=target,
            )
        if stat.st_size > self.max_file_bytes or total_bytes + stat.st_size > self.max_total_bytes:
            return SourceFileRecord(
                relative,
                _language(path),
                stat.st_size,
                stat.st_mtime_ns,
                None,
                "SIZE_LIMIT",
            )
        try:
            raw = path.read_bytes()
        except OSError as error:
            return SourceFileRecord(
                relative, _language(path), stat.st_size, stat.st_mtime_ns, None, "READ_ERROR", error=str(error)
            )
        digest = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return SourceFileRecord(
                relative, _language(path), stat.st_size, stat.st_mtime_ns, digest, "BINARY_OR_INVALID_UTF8"
            )
        language = _language(path)
        symbols, imports, status, error = _extract_text(language, text)
        return SourceFileRecord(
            relative,
            language,
            stat.st_size,
            stat.st_mtime_ns,
            digest,
            status,
            tuple(symbols[:MAX_SYMBOLS_PER_FILE]),
            tuple(imports[:MAX_IMPORTS_PER_FILE]),
            error,
        )


@dataclass(frozen=True)
class _GitFacts:
    repository_root: str | None
    common_git_dir: str | None
    checkout_path: str
    branch: str | None
    head: str | None
    dirty_paths: tuple[str, ...]


def _git_identity(root: Path) -> _GitFacts:
    checkout_path = str(root)
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
        common = _git(root, "rev-parse", "--git-common-dir")
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD") or None
        head = _git(root, "rev-parse", "HEAD") or None
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    except OSError, subprocess.SubprocessError:
        return _GitFacts(None, None, checkout_path, None, None, ())
    dirty_paths = tuple(
        sorted(line[3:].strip() if len(line) > 3 else line.strip() for line in status.splitlines() if line.strip())
    )
    return _GitFacts(
        str(Path(top).resolve()),
        str((root / common).resolve()),
        checkout_path,
        branch,
        head,
        dirty_paths,
    )


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed.stdout.rstrip()


def _is_link(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", lambda value: False)
    return path.is_symlink() or bool(is_junction(path))


def _relative_event_path(root: Path, raw_path: str) -> str | None:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.absolute().relative_to(root).as_posix()
    except ValueError:
        return None


def _language(path: Path) -> str:
    return {
        ".py": "python",
        ".rs": "rust",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
    }.get(path.suffix.lower(), "text")


def _extract_text(language: str, text: str) -> tuple[list[SymbolRecord], list[str], str, str | None]:
    if language == "python":
        return _extract_python(text)
    if language == "rust":
        return _extract_pattern_language("rust", text)
    if language == "typescript":
        return _extract_pattern_language("typescript", text)
    if language == "javascript":
        return _extract_pattern_language("javascript", text)
    return [], [], "FALLBACK_HASH_ONLY", None


def _extract_python(text: str) -> tuple[list[SymbolRecord], list[str], str, str | None]:
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [], [], "SYNTAX_ERROR", f"line {error.lineno or 0}: {error.msg}"
    symbols: list[SymbolRecord] = []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            signature = ast.dump(node.args, annotate_fields=True) if hasattr(node, "args") else node.name
            symbols.append(SymbolRecord(node.name, kind, int(node.lineno), _digest("aegis-symbol-v1", signature)))
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return symbols, sorted(set(imports)), "EXTRACTED", None


_PATTERNS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "rust": (
        ("function", r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("struct", r"\bstruct\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("enum", r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("trait", r"\btrait\s+([A-Za-z_][A-Za-z0-9_]*)"),
        ("module", r"\bmod\s+([A-Za-z_][A-Za-z0-9_]*)"),
    ),
    "typescript": (
        ("function", r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("class", r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("interface", r"\binterface\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("type", r"\btype\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("variable", r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    ),
    "javascript": (
        ("function", r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("class", r"\bclass\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
        ("variable", r"\b(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"),
    ),
}


def _extract_pattern_language(language: str, text: str) -> tuple[list[SymbolRecord], list[str], str, str | None]:
    symbols: list[SymbolRecord] = []
    code = _mask_comments_and_strings(text)
    for kind, pattern in _PATTERNS[language]:
        for match in re.finditer(pattern, code):
            line = code.count("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.start())
            signature = text[match.start() : line_end if line_end >= 0 else len(text)]
            symbols.append(SymbolRecord(match.group(1), kind, line, _digest("aegis-symbol-v1", signature)))
    imports = re.findall(r"\b(?:use|import|from)\s+[^\s;\"']*([A-Za-z_][A-Za-z0-9_./:-]*)", code)
    return symbols, sorted(set(imports)), "LEXICAL_CANDIDATE", None


def _mask_comments_and_strings(text: str) -> str:
    """Keep line positions while removing lexical false positives."""

    chars = list(text)
    index = 0
    quote: str | None = None
    while index < len(chars):
        current = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if quote is not None:
            if current == "\\":
                if current != "\n":
                    chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                    index += 1
            elif current == quote:
                chars[index] = " "
                quote = None
            elif current != "\n":
                chars[index] = " "
            index += 1
            continue
        if current in {"'", '"', "`"}:
            chars[index] = " "
            quote = current
            index += 1
            continue
        if current == "/" and following == "/":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
            continue
        if current == "/" and following == "*":
            chars[index] = chars[index + 1] = " "
            index += 2
            while index < len(chars):
                if chars[index] == "*" and index + 1 < len(chars) and chars[index + 1] == "/":
                    chars[index] = chars[index + 1] = " "
                    index += 2
                    break
                if chars[index] != "\n":
                    chars[index] = " "
                index += 1
            continue
        index += 1
    return "".join(chars)


def _build_lineage(files: Iterable[SourceFileRecord]) -> list[LineageCandidate]:
    records = tuple(files)
    by_stem: dict[str, list[str]] = {}
    by_signature: dict[str, list[tuple[str, str]]] = {}
    for record in records:
        by_stem.setdefault(Path(record.relative_path).stem.casefold(), []).append(record.relative_path)
        for symbol in record.symbols:
            by_signature.setdefault(symbol.signature_hash, []).append((record.relative_path, symbol.name))
    candidates: list[LineageCandidate] = []
    for record in records:
        for symbol in record.symbols:
            paths = tuple(
                sorted(path for path in by_stem.get(symbol.name.casefold(), ()) if path != record.relative_path)
            )
            if paths:
                candidates.append(LineageCandidate(record.relative_path, symbol.name, paths, "duplicate-or-name-match"))
            signature_matches = tuple(
                sorted(
                    path
                    for path, name in by_signature.get(symbol.signature_hash, ())
                    if path != record.relative_path and name == symbol.name
                )
            )
            if signature_matches and not paths:
                candidates.append(
                    LineageCandidate(record.relative_path, symbol.name, signature_matches, "duplicate-signature")
                )
        for imported in record.imports:
            stem = Path(imported.replace(".", "/")).stem.casefold()
            paths = tuple(sorted(path for path in by_stem.get(stem, ()) if path != record.relative_path))
            if paths:
                candidates.append(LineageCandidate(record.relative_path, imported, paths, "import-path-match"))
    return candidates


def compare_snapshots(previous: SourceSnapshot, current: SourceSnapshot) -> SnapshotDelta:
    old = {item.relative_path: item for item in previous.files}
    new = {item.relative_path: item for item in current.files}
    added = {path for path in new if path not in old}
    deleted = {path for path in old if path not in new}
    changed = {path for path in new if path in old and new[path].content_hash != old[path].content_hash}
    old_by_hash: dict[str, list[str]] = {}
    new_by_hash: dict[str, list[str]] = {}
    for path, record in old.items():
        if record.content_hash:
            old_by_hash.setdefault(record.content_hash, []).append(path)
    for path, record in new.items():
        if record.content_hash:
            new_by_hash.setdefault(record.content_hash, []).append(path)
    renamed: set[tuple[str, str]] = set()
    copied: set[tuple[str, str]] = set()
    for digest, old_paths in old_by_hash.items():
        new_paths = new_by_hash.get(digest, [])
        for old_path in old_paths:
            for new_path in new_paths:
                if new_path in old:
                    continue
                (renamed if len(old_paths) == 1 and len(new_paths) == 1 else copied).add((old_path, new_path))
    renamed_old = {old_path for old_path, _ in renamed}
    renamed_new = {new_path for _, new_path in renamed}
    return SnapshotDelta(
        tuple(sorted(added - renamed_new)),
        tuple(sorted(changed)),
        tuple(sorted(deleted - renamed_old)),
        tuple(sorted(renamed)),
        tuple(sorted(copied)),
    )


def invalidate_dependencies(snapshot: SourceSnapshot, changed_paths: Iterable[str]) -> tuple[str, ...]:
    def normalize(path: str) -> str:
        return Path(path).as_posix().casefold()

    changed_display = {normalize(path): Path(path).as_posix() for path in changed_paths}
    affected: dict[str, str] = dict(changed_display)
    reverse: dict[str, dict[str, str]] = {}
    for candidate in snapshot.lineage:
        for path in candidate.candidate_paths:
            reverse.setdefault(normalize(path), {})[normalize(candidate.source_path)] = candidate.source_path
    pending = list(changed_display)
    while pending:
        dependency = pending.pop()
        for normalized, source_path in reverse.get(dependency, {}).items():
            if normalized not in affected:
                affected[normalized] = source_path
                pending.append(normalized)
    return tuple(sorted(affected.values()))


def _file_by_path(files: Iterable[SourceFileRecord], relative_path: str) -> SourceFileRecord | None:
    return next((item for item in files if item.relative_path == relative_path), None)


def _digest(prefix: str, *parts: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(prefix.encode())
    for part in parts:
        encoded = part.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
    return hasher.hexdigest()


def _snapshot_revision(
    identity: ProjectIdentity, files: Iterable[SourceFileRecord], lineage: Iterable[LineageCandidate]
) -> str:
    parts = [identity.project_id, identity.checkout_id]
    parts.extend(f"{record.relative_path}|{record.content_hash or ''}|{record.extraction_status}" for record in files)
    parts.extend(f"{item.source_path}|{item.symbol}|{','.join(item.candidate_paths)}|{item.reason}" for item in lineage)
    return _digest("aegis-source-snapshot-v1", *parts)


__all__ = [
    "IGNORED_DIRECTORIES",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "MAX_WATCH_EVENTS",
    "BoundedSnapshotWatcher",
    "CodeIntelligenceError",
    "LineageCandidate",
    "NativeSnapshotWatcher",
    "ProjectIdentity",
    "SnapshotDelta",
    "SourceFileRecord",
    "SourceMapper",
    "SourceSnapshot",
    "SymbolRecord",
    "WatchEvent",
    "compare_snapshots",
    "invalidate_dependencies",
]
