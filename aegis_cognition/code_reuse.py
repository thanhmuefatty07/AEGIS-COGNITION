"""Evidence-bound local code-reuse decisions.

The module never treats model weights as a source database. A reuse candidate
must point at exact local bytes, a source snapshot/file digest, an explicit
license or internal ownership label, and provenance. Materialization is an
explicit operation and refuses stale sources, path escapes, symlinks, and
implicit overwrite.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

try:
    from core.python.aegis.code_intelligence import SourceSnapshot
except ImportError:
    from aegis.code_intelligence import SourceSnapshot  # type: ignore[import-not-found]


CODE_REUSE_SCHEMA_V1 = "aegis-code-reuse-v1"
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_LICENSES = frozenset({"Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MIT"})


class CodeReuseError(ValueError):
    """A reuse candidate or materialization request is not trustworthy."""


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _digest_bytes(encoded)


def _bounded_text(value: object, name: str, limit: int = 4_096) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value or len(value) > limit:
        raise CodeReuseError(f"{name} must be bounded non-empty text")
    return value.strip()


def _inside(root: Path, candidate: Path) -> Path:
    resolved_root = root.expanduser().resolve()
    resolved_candidate = candidate.expanduser().resolve()
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise CodeReuseError("path escapes the declared reuse root") from error
    return resolved_candidate


def _relative_target(value: str | Path) -> Path:
    raw = str(value)
    if not raw or "\x00" in raw:
        raise CodeReuseError("target path is invalid")
    path = Path(raw)
    if path == Path(".") or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CodeReuseError("target path must be a relative, normalized path")
    return path


def _read_lines(source_path: Path, start_line: int, end_line: int) -> tuple[bytes, str]:
    if source_path.is_symlink():
        raise CodeReuseError("symlink source files are not reusable")
    try:
        raw = source_path.read_bytes()
    except OSError as error:
        raise CodeReuseError("reuse source cannot be read") from error
    lines = raw.splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise CodeReuseError("reuse source line range is outside the current file")
    snippet = b"".join(lines[start_line - 1 : end_line])
    return snippet, _digest_bytes(raw)


def _materialize_bytes(target: Path, data: bytes, *, overwrite: bool) -> None:
    """Write exact bytes without following a raced target symlink."""

    if not overwrite:
        flags = int(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        flags |= int(getattr(os, "O_BINARY", 0))
        try:
            descriptor = os.open(target, flags, 0o600)
        except FileExistsError as error:
            raise CodeReuseError("target exists; explicit overwrite=True is required") from error
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise CodeReuseError("reuse target cannot be written") from error
        return

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".aegis-tmp",
            delete=False,
        ) as stream:
            temporary_path = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    except OSError as error:
        raise CodeReuseError("reuse target cannot be atomically replaced") from error
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_path)


@dataclass(frozen=True, slots=True)
class CodeReusePolicy:
    """Policy for candidate selection and explicit local materialization."""

    allowed_licenses: tuple[str, ...] = tuple(sorted(_DEFAULT_LICENSES))
    allow_internal: bool = True
    max_snippet_bytes: int = 512 * 1024

    def validate(self) -> None:
        if type(self.allowed_licenses) not in (list, tuple) or any(
            type(value) is not str or not value.strip() for value in self.allowed_licenses
        ):
            raise CodeReuseError("reuse allowed_licenses must be non-empty strings")
        if len(set(self.allowed_licenses)) != len(self.allowed_licenses):
            raise CodeReuseError("reuse allowed_licenses must be unique")
        if type(self.allow_internal) is not bool:
            raise CodeReuseError("reuse allow_internal must be boolean")
        if type(self.max_snippet_bytes) is not int or self.max_snippet_bytes < 1:
            raise CodeReuseError("reuse max_snippet_bytes must be positive")

    def permits(self, license_id: str) -> bool:
        self.validate()
        return (license_id == "INTERNAL" and self.allow_internal) or license_id in self.allowed_licenses


@dataclass(frozen=True, slots=True)
class CodeReuseCandidate:
    """A source-range reference; source bytes stay on disk until materialized."""

    source_root: str
    source_path: str
    relative_path: str
    start_line: int
    end_line: int
    source_revision: str
    source_file_hash: str
    snippet_hash: str
    snippet_bytes: int
    license_id: str
    provenance_uri: str

    def validate(self) -> None:
        _bounded_text(self.source_root, "source_root", 4_096)
        _bounded_text(self.source_path, "source_path", 8_192)
        relative_path = _bounded_text(self.relative_path, "relative_path", 4_096)
        _relative_target(relative_path)
        _inside(Path(self.source_root), Path(self.source_path))
        if type(self.start_line) is not int or type(self.end_line) is not int:
            raise CodeReuseError("reuse line bounds must be integers")
        if self.start_line < 1 or self.end_line < self.start_line:
            raise CodeReuseError("reuse line bounds are invalid")
        _bounded_text(self.source_revision, "source_revision", 128)
        for value, name in ((self.source_file_hash, "source_file_hash"), (self.snippet_hash, "snippet_hash")):
            if type(value) is not str or not _DIGEST_PATTERN.fullmatch(value):
                raise CodeReuseError(f"{name} must be a lowercase SHA-256 digest")
        if type(self.snippet_bytes) is not int or self.snippet_bytes < 1:
            raise CodeReuseError("snippet_bytes must be positive")
        _bounded_text(self.license_id, "license_id", 128)
        _bounded_text(self.provenance_uri, "provenance_uri", 4_096)

    def as_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "schema": CODE_REUSE_SCHEMA_V1,
            "source_root": self.source_root,
            "source_path": self.source_path,
            "relative_path": self.relative_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source_revision": self.source_revision,
            "source_file_hash": self.source_file_hash,
            "snippet_hash": self.snippet_hash,
            "snippet_bytes": self.snippet_bytes,
            "license_id": self.license_id,
            "provenance_uri": self.provenance_uri,
        }


def build_local_reuse_candidate(
    snapshot: SourceSnapshot,
    relative_path: str,
    *,
    start_line: int,
    end_line: int,
    license_id: str = "INTERNAL",
    provenance_uri: str | None = None,
) -> CodeReuseCandidate:
    """Create a candidate only when the current bytes match a source snapshot."""

    if type(snapshot) is not SourceSnapshot:
        raise CodeReuseError("reuse snapshot must be a SourceSnapshot")
    relative = _relative_target(relative_path).as_posix()
    record = next((item for item in snapshot.files if item.relative_path == relative), None)
    if record is None or record.content_hash is None:
        raise CodeReuseError("reuse source file is absent or has no content hash")
    root = Path(snapshot.identity.root).expanduser().resolve()
    source = _inside(root, root / relative)
    snippet, file_hash = _read_lines(source, start_line, end_line)
    if file_hash != record.content_hash:
        raise CodeReuseError("reuse source is stale relative to the source snapshot")
    if not snippet:
        raise CodeReuseError("reuse source range is empty")
    candidate = CodeReuseCandidate(
        source_root=str(root),
        source_path=str(source),
        relative_path=relative,
        start_line=start_line,
        end_line=end_line,
        source_revision=snapshot.revision,
        source_file_hash=file_hash,
        snippet_hash=_digest_bytes(snippet),
        snippet_bytes=len(snippet),
        license_id=_bounded_text(license_id, "license_id", 128),
        provenance_uri=provenance_uri or f"local://{relative}",
    )
    candidate.validate()
    return candidate


@dataclass(frozen=True, slots=True)
class CodeReuseAssessment:
    """Decision record with explicit estimated model-token costs."""

    schema: str
    candidate_hash: str
    decision: str
    generation_tokens: int
    adaptation_tokens: int
    verification_tokens: int
    estimated_total_tokens: int
    reason: str

    @property
    def savings_tokens(self) -> int:
        return self.generation_tokens - self.estimated_total_tokens

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "candidate_hash": self.candidate_hash,
            "decision": self.decision,
            "generation_tokens": self.generation_tokens,
            "adaptation_tokens": self.adaptation_tokens,
            "verification_tokens": self.verification_tokens,
            "estimated_total_tokens": self.estimated_total_tokens,
            "savings_tokens": self.savings_tokens,
            "reason": self.reason,
        }


def assess_code_reuse(
    candidate: CodeReuseCandidate,
    *,
    generation_tokens: int,
    adaptation_tokens: int = 0,
    verification_tokens: int = 0,
    policy: CodeReusePolicy | None = None,
) -> CodeReuseAssessment:
    """Compare explicit reuse estimates with generation; no universal saving claim."""

    candidate.validate()
    selected_policy = policy or CodeReusePolicy()
    selected_policy.validate()
    for value, name in (
        (generation_tokens, "generation_tokens"),
        (adaptation_tokens, "adaptation_tokens"),
        (verification_tokens, "verification_tokens"),
    ):
        if type(value) is not int or value < 0:
            raise CodeReuseError(f"{name} must be a non-negative integer")
    if candidate.snippet_bytes > selected_policy.max_snippet_bytes:
        decision = "REJECT"
        reason = "snippet exceeds the reuse byte quota"
    elif not selected_policy.permits(candidate.license_id):
        decision = "REJECT"
        reason = "license or ownership is not explicitly permitted"
    else:
        total = adaptation_tokens + verification_tokens
        if total >= generation_tokens:
            decision = "GENERATE"
            reason = "measured/estimated adaptation and verification cost is not cheaper"
        elif adaptation_tokens == 0:
            decision = "REUSE_EXACT"
            reason = "exact local materialization is cheaper under the supplied estimates"
        else:
            decision = "REUSE_WITH_PATCH"
            reason = "explicit adaptation remains cheaper under the supplied estimates"
    total = adaptation_tokens + verification_tokens
    return CodeReuseAssessment(
        schema=CODE_REUSE_SCHEMA_V1,
        candidate_hash=_digest_json(candidate.as_dict()),
        decision=decision,
        generation_tokens=generation_tokens,
        adaptation_tokens=adaptation_tokens,
        verification_tokens=verification_tokens,
        estimated_total_tokens=total,
        reason=reason,
    )


@dataclass(frozen=True, slots=True)
class CodeReuseReceipt:
    """Hash-bound record returned after explicit exact materialization."""

    schema: str
    candidate_hash: str
    target_path: str
    source_file_hash: str
    snippet_hash: str
    target_hash: str
    license_id: str
    receipt_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "candidate_hash": self.candidate_hash,
            "target_path": self.target_path,
            "source_file_hash": self.source_file_hash,
            "snippet_hash": self.snippet_hash,
            "target_hash": self.target_hash,
            "license_id": self.license_id,
            "receipt_hash": self.receipt_hash,
        }


def materialize_exact(
    candidate: CodeReuseCandidate,
    *,
    target_root: str | Path,
    target_relative_path: str | Path,
    policy: CodeReusePolicy | None = None,
    overwrite: bool = False,
) -> CodeReuseReceipt:
    """Copy the hash-checked exact range once; never overwrite implicitly."""

    candidate.validate()
    selected_policy = policy or CodeReusePolicy()
    selected_policy.validate()
    if not selected_policy.permits(candidate.license_id):
        raise CodeReuseError("license or ownership is not explicitly permitted")
    source_root = Path(candidate.source_root).expanduser().resolve()
    source = _inside(source_root, Path(candidate.source_path))
    snippet, source_hash = _read_lines(source, candidate.start_line, candidate.end_line)
    if source_hash != candidate.source_file_hash or _digest_bytes(snippet) != candidate.snippet_hash:
        raise CodeReuseError("reuse source changed after candidate creation")
    if len(snippet) != candidate.snippet_bytes:
        raise CodeReuseError("reuse snippet byte count changed")
    target_root_path = Path(target_root).expanduser().resolve()
    if not target_root_path.is_dir():
        raise CodeReuseError("target root must be an existing directory")
    if candidate.license_id == "INTERNAL" and os.path.normcase(str(source_root)) != os.path.normcase(
        str(target_root_path)
    ):
        raise CodeReuseError("internal code may only be materialized inside its owning source root")
    target = target_root_path / _relative_target(target_relative_path)
    parent = _inside(target_root_path, target.parent)
    if not parent.is_dir():
        raise CodeReuseError("target parent directory must already exist")
    if target.is_symlink():
        raise CodeReuseError("symlink targets cannot be overwritten")
    if target.exists() and not overwrite:
        raise CodeReuseError("target exists; explicit overwrite=True is required")
    _materialize_bytes(target, snippet, overwrite=overwrite)
    try:
        target_hash = _digest_bytes(target.read_bytes())
    except OSError as error:
        raise CodeReuseError("materialized target cannot be verified") from error
    if target_hash != candidate.snippet_hash:
        raise CodeReuseError("materialized target hash does not match the reuse candidate")
    candidate_hash = _digest_json(candidate.as_dict())
    unsigned = {
        "schema": "aegis-code-reuse-receipt-v1",
        "candidate_hash": candidate_hash,
        "target_path": str(target),
        "source_file_hash": source_hash,
        "snippet_hash": candidate.snippet_hash,
        "target_hash": target_hash,
        "license_id": candidate.license_id,
    }
    return CodeReuseReceipt(
        schema="aegis-code-reuse-receipt-v1",
        candidate_hash=candidate_hash,
        target_path=str(target),
        source_file_hash=source_hash,
        snippet_hash=candidate.snippet_hash,
        target_hash=target_hash,
        license_id=candidate.license_id,
        receipt_hash=_digest_json(unsigned),
    )


__all__ = [
    "CODE_REUSE_SCHEMA_V1",
    "CodeReuseAssessment",
    "CodeReuseCandidate",
    "CodeReuseError",
    "CodeReusePolicy",
    "CodeReuseReceipt",
    "assess_code_reuse",
    "build_local_reuse_candidate",
    "materialize_exact",
]
