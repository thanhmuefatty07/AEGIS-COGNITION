"""Evidence-first goal and target contracts for long-running Lab work.

The contract is intentionally separate from the conversation and from the
execution-cell policy.  It describes what the run is trying to achieve and
what may be inspected; it does not claim that a target is safe, that an
artifact is correct, or that an OS isolation boundary exists.
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, ClassVar, cast
from collections.abc import Mapping
from urllib.parse import urlparse
from blake3 import blake3


_AMBIGUOUS_URI_ESCAPES = re.compile(r"%(?:2e|2f|5c)", re.IGNORECASE)
_TARGET_READ_EFFECTS = frozenset({"read_only", "network_read", "model_inference"})
_TARGET_WRITE_EFFECTS = frozenset(
    {
        "local_reversible",
        "external_write",
        "destructive",
        "state_write",
        "memory_write",
        "post_completion_effect",
    }
)


def _digest(value: Any) -> str:
    """Hash only canonical JSON values; never stringify malformed input."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    hasher = blake3()
    hasher.update(b"aegis-goal-contract-canonical-v1\0")
    hasher.update(payload)
    return hasher.hexdigest()


def _execution_binding_digest(value: Any) -> str:
    """Hash one execution binding without changing GoalContract hash semantics."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    hasher = blake3()
    hasher.update(b"aegis-execution-binding-canonical-v1\0")
    hasher.update(payload)
    return hasher.hexdigest()


def projection_payload_digest(value: Any) -> str:
    """Hash a value using the Rust Lab projection-payload contract."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    hasher = blake3()
    hasher.update(b"aegis-lab-projection-payload-v1\0")
    hasher.update(payload)
    return hasher.hexdigest()


def _string_sequence(value: Any, *, label: str, sort_values: bool = True) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        raise ValueError(f"{label} must be a list or tuple")
    typed = cast(list[Any] | tuple[Any, ...], value)
    if any(type(item) is not str or not item or item != item.strip() for item in typed):
        raise ValueError(f"{label} entries must be non-empty trimmed strings")
    values = tuple(typed)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(sorted(values)) if sort_values else values


def _canonical_string_sequence(value: Any, *, label: str) -> tuple[str, ...]:
    """Read a wire sequence only when it is already in canonical order.

    Constructors may still normalize ergonomic in-memory input. Wire
    deserialization is stricter so Python and the native validator agree on
    which payloads are admissible instead of silently accepting two forms.
    """

    values = _string_sequence(value, label=label, sort_values=False)
    if values != tuple(sorted(values)):
        raise GoalContractError(f"{label} must be in canonical order")
    return values


def _required_text(value: Any, *, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def external_side_effect_key(tool_name: str, effect_class: str) -> str:
    """Return the exact key used to authorize one generic external effect."""

    normalized_tool = _required_text(tool_name, label="external effect tool_name")
    normalized_effect = _required_text(effect_class, label="external effect effect_class")
    if "::" in normalized_tool or "::" in normalized_effect:
        raise ValueError("external effect components must not contain '::'")
    return f"{normalized_tool}::{normalized_effect}"


def _uri_scope(value: str) -> tuple[str, str, str] | None:
    """Parse one non-filesystem URI into comparable scope components."""

    if len(value) >= 3 and value[1] == ":" and value[2] in {"/", "\\"}:
        return None
    parsed = urlparse(value)
    if not parsed.scheme or parsed.username is not None or parsed.password is not None:
        return None
    try:
        _ = parsed.port
    except ValueError:
        return None
    if parsed.query or parsed.fragment:
        return None
    raw_path = parsed.path or "/"
    # Dot segments and encoded separators are normalized by some URI
    # consumers.  Rejecting encoded separators and backslashes here prevents
    # the target check from disagreeing with a downstream adapter; ordinary
    # literal dot segments are normalized according to RFC 3986 instead.
    if "\\" in raw_path or _AMBIGUOUS_URI_ESCAPES.search(raw_path):
        return None
    normalized_path = posixpath.normpath(raw_path)
    if raw_path.startswith("/") and not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if normalized_path == ".":
        normalized_path = "/"
    return (
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        normalized_path.rstrip("/") or "/",
    )


def _uri_has_dot_segments(value: str) -> bool:
    parsed = urlparse(value)
    return any(segment in {".", ".."} for segment in parsed.path.split("/"))


def _validate_uri_scope_roots(entries: tuple[str, ...], *, label: str) -> None:
    for entry in entries:
        if len(entry) >= 3 and entry[1] == ":" and entry[2] in {"/", "\\"}:
            continue
        parsed = urlparse(entry)
        if not parsed.scheme:
            continue
        if _uri_scope(entry) is None:
            raise ValueError(f"{label} contains an invalid or ambiguous URI scope")
        if _uri_has_dot_segments(entry):
            raise ValueError(f"{label} must not contain URI dot segments")


def _valid_network_allowlist_entry(value: str) -> bool:
    """Return whether one network entry has an unambiguous host-only shape.

    The target remains transport-neutral, but adapters can only enforce a
    host allowlist when the entry is a bare host or a credential-free HTTP(S)
    origin.  Admission rejects entries that ``network_hosts`` would silently
    discard, preventing a contract from claiming network authority that no
    adapter can actually bind.
    """

    if type(value) is not str or not value or value != value.strip():
        return False
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    if value.startswith("//"):
        return False
    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
        parsed_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed_port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    host = hostname.lower().rstrip(".")
    return bool(host) and "*" not in host


def _filesystem_scope(value: str) -> str | None:
    """Return a lexical absolute filesystem scope without resolving symlinks.

    Contract paths are data, so their comparison must not depend on the host
    running the validator. Windows drive and UNC paths may be validated from
    Linux or macOS as well as from Windows.
    """

    if _uri_scope(value) is not None:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("\\\\", "//")):
        return ntpath.normcase(ntpath.normpath(value.replace("/", "\\")))
    if not os.path.isabs(value):
        return None
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def _scope_contains(candidate: str, root: str) -> bool:
    candidate_uri = _uri_scope(candidate)
    root_uri = _uri_scope(root)
    if candidate_uri is not None or root_uri is not None:
        if candidate_uri is None or root_uri is None or candidate_uri[:2] != root_uri[:2]:
            return False
        candidate_path = candidate_uri[2]
        root_path = root_uri[2]
        return candidate_path == root_path or candidate_path.startswith(root_path.rstrip("/") + "/")
    candidate_path = _filesystem_scope(candidate)
    root_path = _filesystem_scope(root)
    if candidate_path is None or root_path is None:
        return False
    path_module = (
        ntpath
        if re.match(r"^[A-Za-z]:\\", candidate_path)
        or re.match(r"^[A-Za-z]:\\", root_path)
        or candidate_path.startswith("\\\\")
        or root_path.startswith("\\\\")
        else os.path
    )
    try:
        return path_module.commonpath((candidate_path, root_path)) == root_path
    except ValueError:
        return False


def _validate_external_effect_entries(entries: tuple[str, ...]) -> None:
    for entry in entries:
        if entry.count("::") != 1:
            raise ValueError("target external_side_effects must use tool_name::effect_class keys")
        tool_name, effect_class = entry.split("::")
        if external_side_effect_key(tool_name, effect_class) != entry:
            raise ValueError("target external_side_effects contains a non-canonical key")


def _target_values_are_subset(
    field_name: str,
    candidate_values: tuple[str, ...],
    parent_values: tuple[str, ...],
) -> bool:
    """Check whether an evolved target only narrows its parent's authority."""

    if field_name in {"read_roots", "write_roots"}:
        return all(
            any(_scope_contains(candidate, parent) for parent in parent_values) for candidate in candidate_values
        )
    return set(candidate_values).issubset(parent_values)


def _is_digest(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _json_scalar(value: Any, *, label: str) -> None:
    if value is None or type(value) in (str, int, float, bool):
        if type(value) is float:
            json.dumps(value, allow_nan=False)
        return
    raise ValueError(f"{label} must be a JSON scalar")


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    label: str,
) -> None:
    if set(value) != set(expected):
        raise GoalContractError(f"{label} contains missing or unknown fields")


def _as_string_mapping(value: object, *, label: str) -> Mapping[str, object]:
    """Narrow an untrusted JSON object to a string-keyed mapping."""

    if not isinstance(value, Mapping):
        raise GoalContractError(f"{label} must be an object with string keys")
    typed = cast(Mapping[object, object], value)
    if any(type(key) is not str for key in typed):
        raise GoalContractError(f"{label} must be an object with string keys")
    return cast(Mapping[str, object], typed)


def _optional_text(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label=label)


def _nonnegative_int(value: object, *, label: str, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise GoalContractError(f"{label} must be a non-negative integer")
    return value


def _positive_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 1:
        raise GoalContractError(f"{label} must be a positive integer")
    return value


def _required_bool(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise GoalContractError(f"{label} must be boolean")
    return value


def _json_scalar_value(value: object, *, label: str) -> str | int | float | bool | None:
    _json_scalar(value, label=label)
    return cast(str | int | float | bool | None, value)


class GoalContractError(ValueError):
    """Invalid contract or an invalid controller operation."""


@dataclass(frozen=True)
class GoalContractLimits:
    """Bound the untrusted GoalContract wire before hashing or admission.

    These are admission-profile limits, not part of the contract hash.  A
    deployment policy must bind the selected profile separately when it needs
    to distinguish profiles.  Valid payloads below the limits keep the v1
    canonical hash unchanged.
    """

    wire_bytes: int = 1 * 1024 * 1024
    text_bytes: int = 64 * 1024
    identifier_bytes: int = 128
    scope_entry_bytes: int = 4096
    max_sequence_items: int = 256
    max_total_sequence_items: int = 2048
    max_depth: int = 8
    max_nodes: int = 4096

    SCHEMA: ClassVar[str] = "aegis-goal-contract-limits-v1"

    _SCOPE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"read_roots", "write_roots", "network_allowlist", "scope", "non_goals"}
    )
    _IDENTIFIER_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema",
            "goal_id",
            "kind",
            "stable_id",
            "revision_or_digest",
            "owner",
            "policy_digest",
            "predicate_id",
            "evaluator",
            "severity",
            "inputs",
            "evidence_refs",
            "reproducibility_requirements",
            "required_record_types",
            "trusted_verifier_ids",
            "parent_goal_id",
            "parent_contract_hash",
            "author_id",
            "external_side_effects",
            "mission_id",
            "owner_id",
            "principal_id",
            "execution_cell_id",
            "execution_action_kind",
            "backend_kind",
            "verifier_id",
        }
    )

    def __post_init__(self) -> None:
        for name in (
            "wire_bytes",
            "text_bytes",
            "identifier_bytes",
            "scope_entry_bytes",
            "max_sequence_items",
            "max_total_sequence_items",
            "max_depth",
            "max_nodes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"goal contract limit {name} must be a positive integer")
        if self.max_total_sequence_items < self.max_sequence_items:
            raise ValueError("goal contract total sequence limit must cover one sequence")

    def _string_limit(self, parent_key: str | None) -> int:
        if parent_key in self._SCOPE_FIELDS:
            return self.scope_entry_bytes
        if parent_key in self._IDENTIFIER_FIELDS:
            return self.identifier_bytes
        return self.text_bytes

    def validate_wire(self, value: object) -> None:
        """Reject oversized/deep JSON-like data before contract hashing."""

        if not isinstance(value, Mapping):
            raise GoalContractError("goal contract must be an object")
        stack: list[tuple[object, int, str | None]] = [(value, 0, None)]
        total_sequence_items = 0
        total_nodes = 0
        while stack:
            current, depth, parent_key = stack.pop()
            total_nodes += 1
            if total_nodes > self.max_nodes:
                raise GoalContractError("goal contract exceeds the node limit")
            if depth > self.max_depth:
                raise GoalContractError("goal contract exceeds the nesting limit")
            if isinstance(current, Mapping):
                current_mapping = cast(Mapping[object, object], current)
                for key, child in current_mapping.items():
                    if type(key) is not str:
                        raise GoalContractError("goal contract object keys must be strings")
                    if len(key.encode("utf-8")) > self.identifier_bytes:
                        raise GoalContractError("goal contract object key exceeds the byte limit")
                    stack.append((child, depth + 1, key))
                continue
            if type(current) in (list, tuple):
                current_sequence = cast(list[object] | tuple[object, ...], current)
                if len(current_sequence) > self.max_sequence_items:
                    raise GoalContractError("goal contract sequence exceeds the item limit")
                total_sequence_items += len(current_sequence)
                if total_sequence_items > self.max_total_sequence_items:
                    raise GoalContractError("goal contract exceeds the total sequence limit")
                stack.extend((child, depth + 1, parent_key) for child in current_sequence)
                continue
            if type(current) is str:
                if len(current.encode("utf-8")) > self._string_limit(parent_key):
                    raise GoalContractError("goal contract string exceeds the byte limit")
                continue
            if current is None or type(current) in (bool, int, float):
                if type(current) is float:
                    try:
                        json.dumps(current, allow_nan=False)
                    except (TypeError, ValueError) as exc:
                        raise GoalContractError("goal contract contains a non-finite number") from exc
                continue
            raise GoalContractError("goal contract contains a non-JSON value")
        try:
            wire_bytes = len(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise GoalContractError("goal contract cannot be encoded as canonical JSON") from exc
        if wire_bytes > self.wire_bytes:
            raise GoalContractError("goal contract exceeds the wire byte limit")


DEFAULT_GOAL_CONTRACT_LIMITS = GoalContractLimits()


class StaleGoalGeneration(GoalContractError):
    """A mutation was based on an older immutable contract generation."""


class GoalLifecycle(StrEnum):
    DRAFT = "draft"
    ADMITTED = "admitted"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    USAGE_LIMITED = "usage_limited"
    BUDGET_LIMITED = "budget_limited"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SETTLED = "settled"


class GoalVerdict(StrEnum):
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    FAILED = "failed"
    INVALID = "invalid"


_TERMINAL_LIFECYCLES = frozenset(
    {
        GoalLifecycle.CANCELLED,
        GoalLifecycle.FAILED,
        GoalLifecycle.USAGE_LIMITED,
        GoalLifecycle.BUDGET_LIMITED,
    }
)

_ALLOWED_TRANSITIONS: dict[GoalLifecycle, frozenset[GoalLifecycle]] = {
    GoalLifecycle.DRAFT: frozenset({GoalLifecycle.ADMITTED, GoalLifecycle.CANCELLED}),
    GoalLifecycle.ADMITTED: frozenset({GoalLifecycle.ACTIVE, GoalLifecycle.CANCELLED, GoalLifecycle.FAILED}),
    GoalLifecycle.ACTIVE: frozenset(
        {
            GoalLifecycle.PAUSED,
            GoalLifecycle.BLOCKED,
            GoalLifecycle.USAGE_LIMITED,
            GoalLifecycle.BUDGET_LIMITED,
            GoalLifecycle.CANCELLED,
            GoalLifecycle.FAILED,
            GoalLifecycle.SETTLED,
        }
    ),
    GoalLifecycle.PAUSED: frozenset({GoalLifecycle.ACTIVE, GoalLifecycle.BLOCKED, GoalLifecycle.CANCELLED}),
    GoalLifecycle.BLOCKED: frozenset({GoalLifecycle.ACTIVE, GoalLifecycle.CANCELLED, GoalLifecycle.FAILED}),
    GoalLifecycle.SETTLED: frozenset(),
    GoalLifecycle.USAGE_LIMITED: frozenset(),
    GoalLifecycle.BUDGET_LIMITED: frozenset(),
    GoalLifecycle.CANCELLED: frozenset(),
    GoalLifecycle.FAILED: frozenset(),
}


@dataclass(frozen=True)
class TargetDescriptor:
    """A versioned target and intended effect surface.

    Generic external tool effects may be declared with exact
    ``tool_name::effect_class`` keys. This is an admission boundary, not proof
    of provider, filesystem, network, browser, or kernel isolation.
    """

    kind: str = "legacy"
    stable_id: str = "unbound"
    revision_or_digest: str = "unbound"
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    network_allowlist: tuple[str, ...] = ()
    external_side_effects: tuple[str, ...] = ()
    owner: str = "legacy-constructor"

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _required_text(self.kind, label="target kind"))
        object.__setattr__(self, "stable_id", _required_text(self.stable_id, label="target stable_id"))
        object.__setattr__(
            self,
            "revision_or_digest",
            _required_text(self.revision_or_digest, label="target revision_or_digest"),
        )
        object.__setattr__(self, "owner", _required_text(self.owner, label="target owner"))
        for name in (
            "read_roots",
            "write_roots",
            "network_allowlist",
            "external_side_effects",
        ):
            object.__setattr__(
                self,
                name,
                _string_sequence(getattr(self, name), label=f"target {name}"),
            )
        _validate_uri_scope_roots(self.read_roots, label="target read_roots")
        _validate_uri_scope_roots(self.write_roots, label="target write_roots")
        _validate_external_effect_entries(self.external_side_effects)
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(self.to_dict())

    @property
    def is_bound(self) -> bool:
        return self.stable_id != "unbound" and self.revision_or_digest != "unbound"

    def canonical_digest(self) -> str:
        """Return the digest shared by Python and native Lab projections."""

        return projection_payload_digest(self.to_dict())

    def network_hosts(self) -> tuple[str, ...]:
        """Return host tokens usable by host-based network adapters.

        Target entries may be bare host names or credential-free HTTP(S)
        origins.  Entries with a path, credentials, port, wildcard, or an
        unsupported scheme do not grant a host.  This keeps the generic target
        schema transport-neutral while giving adapters a conservative matcher.
        """

        hosts: set[str] = set()
        for entry in self.network_allowlist:
            candidate = entry if "://" in entry else f"https://{entry}"
            try:
                parsed = urlparse(candidate)
                parsed_hostname = parsed.hostname
                parsed_port = parsed.port
            except ValueError:
                continue
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed_hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed_port is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                continue
            host = parsed_hostname.lower().rstrip(".")
            if host and "*" not in host:
                hosts.add(host)
        return tuple(sorted(hosts))

    def allows_network_host(self, host: str) -> bool:
        """Return whether a host-based adapter is explicitly target-allowed."""

        if type(host) is not str or not host.strip():
            return False
        return host.strip().lower().rstrip(".") in self.network_hosts()

    def allows_path(self, path: str, effect_class: str = "read_only") -> bool:
        """Return whether a local adapter path is inside this target's roots.

        Matching is lexical and intentionally does not resolve symlinks or
        replace OS/provider enforcement. Read effects use ``read_roots``;
        write-like effects use ``write_roots``; compute may use either.
        """

        if type(path) is not str or not path.strip() or type(effect_class) is not str:
            return False
        normalized_effect = effect_class.strip().lower()
        if normalized_effect in _TARGET_WRITE_EFFECTS:
            roots = self.write_roots
        elif normalized_effect == "compute":
            roots = self.read_roots + self.write_roots
        elif normalized_effect in _TARGET_READ_EFFECTS:
            roots = self.read_roots
        else:
            # Unknown effect classes must not inherit read authority.  New
            # effects need an explicit classification before they can use a
            # target path, otherwise a typo could silently widen access.
            return False
        return bool(roots) and any(_scope_contains(path.strip(), root) for root in roots)

    def validate(self, *, require_bound: bool = False) -> None:
        # __post_init__ performs structural validation; this method makes the
        # admission requirement explicit at the caller's decision boundary.
        if require_bound and not self.is_bound:
            raise GoalContractError("target identity and revision must be explicitly bound")
        if require_bound and any(not _valid_network_allowlist_entry(entry) for entry in self.network_allowlist):
            raise GoalContractError("bound target network_allowlist contains an invalid or ambiguous host")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "stable_id": self.stable_id,
            "revision_or_digest": self.revision_or_digest,
            "read_roots": list(self.read_roots),
            "write_roots": list(self.write_roots),
            "network_allowlist": list(self.network_allowlist),
            "external_side_effects": list(self.external_side_effects),
            "owner": self.owner,
        }


@dataclass(frozen=True)
class AcceptancePredicate:
    """Machine-addressable success condition; status lives in GoalProgress."""

    predicate_id: str
    description: str
    evaluator: str
    inputs: tuple[str, ...] = ()
    expected_result: str | int | float | bool | None = True
    severity: str = "required"
    evidence_refs: tuple[str, ...] = ()
    reproducibility_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "predicate_id", _required_text(self.predicate_id, label="predicate_id"))
        object.__setattr__(self, "description", _required_text(self.description, label="predicate description"))
        object.__setattr__(self, "evaluator", _required_text(self.evaluator, label="predicate evaluator"))
        object.__setattr__(self, "inputs", _string_sequence(self.inputs, label="predicate inputs"))
        object.__setattr__(self, "evidence_refs", _string_sequence(self.evidence_refs, label="predicate evidence_refs"))
        object.__setattr__(
            self,
            "reproducibility_requirements",
            _string_sequence(
                self.reproducibility_requirements,
                label="predicate reproducibility_requirements",
            ),
        )
        if self.severity not in {"required", "advisory"}:
            raise ValueError("predicate severity must be required or advisory")
        _json_scalar(self.expected_result, label="predicate expected_result")

    @property
    def required(self) -> bool:
        return self.severity == "required"

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "description": self.description,
            "evaluator": self.evaluator,
            "inputs": list(self.inputs),
            "expected_result": self.expected_result,
            "severity": self.severity,
            "evidence_refs": list(self.evidence_refs),
            "reproducibility_requirements": list(self.reproducibility_requirements),
        }


@dataclass(frozen=True)
class GoalBudgetContract:
    """Finite budget envelope kept distinct from execution accounting."""

    token_limit: int
    attempt_limit: int
    wall_time_limit_ms: int | None = None
    cost_limit_minor_units: int | None = None
    cpu_time_limit_ms: int | None = None
    reserved_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "token_limit",
            "attempt_limit",
            "reserved_tokens",
            "wall_time_limit_ms",
            "cost_limit_minor_units",
            "cpu_time_limit_ms",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"budget {name} must be a non-negative integer or unset")
        if type(self.token_limit) is not int or self.token_limit < 1:
            raise ValueError("budget token_limit must be a positive integer")
        if type(self.attempt_limit) is not int or self.attempt_limit < 1:
            raise ValueError("budget attempt_limit must be a positive integer")
        if self.reserved_tokens >= self.token_limit:
            raise ValueError("budget reserved_tokens must leave exploration headroom")

    @classmethod
    def from_lab_budget(
        cls,
        *,
        token_limit: int,
        attempt_limit: int,
        finalization_reserve: int = 0,
        recovery_reserve: int = 0,
    ) -> GoalBudgetContract:
        return cls(
            token_limit=token_limit,
            attempt_limit=attempt_limit,
            reserved_tokens=finalization_reserve + recovery_reserve,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_limit": self.token_limit,
            "attempt_limit": self.attempt_limit,
            "wall_time_limit_ms": self.wall_time_limit_ms,
            "cost_limit_minor_units": self.cost_limit_minor_units,
            "cpu_time_limit_ms": self.cpu_time_limit_ms,
            "reserved_tokens": self.reserved_tokens,
        }


@dataclass(frozen=True)
class EvidencePolicy:
    """Evidence obligations; it never converts an artifact into proof itself."""

    required_record_types: tuple[str, ...] = ()
    minimum_sources: int = 0
    require_independent_verifier: bool = False
    trusted_verifier_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_record_types",
            _string_sequence(self.required_record_types, label="evidence required_record_types"),
        )
        object.__setattr__(
            self,
            "trusted_verifier_ids",
            _string_sequence(self.trusted_verifier_ids, label="evidence trusted_verifier_ids"),
        )
        if type(self.minimum_sources) is not int or self.minimum_sources < 0:
            raise ValueError("evidence minimum_sources must be non-negative")
        if type(self.require_independent_verifier) is not bool:
            raise ValueError("evidence require_independent_verifier must be boolean")
        if self.require_independent_verifier and not self.trusted_verifier_ids:
            raise ValueError("independent verification requires trusted verifier identities")

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_record_types": list(self.required_record_types),
            "minimum_sources": self.minimum_sources,
            "require_independent_verifier": self.require_independent_verifier,
            "trusted_verifier_ids": list(self.trusted_verifier_ids),
        }


@dataclass(frozen=True)
class GoalContract:
    """Immutable definition of one logical goal generation."""

    SCHEMA: ClassVar[str] = "aegis-goal-contract-v1"

    goal_id: str
    objective: str
    target: TargetDescriptor
    acceptance: tuple[AcceptancePredicate, ...] = ()
    scope: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()
    policy_digest: str = "unbound"
    budget: GoalBudgetContract = field(default_factory=lambda: GoalBudgetContract(token_limit=1, attempt_limit=1))
    evidence_policy: EvidencePolicy = field(default_factory=EvidencePolicy)
    generation: int = 1
    parent_goal_id: str | None = None
    parent_contract_hash: str | None = None
    author_id: str = "unknown"
    effective_epoch: int = 0
    evolution_reason: str | None = None
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    contract_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal_id", _required_text(self.goal_id, label="goal_id"))
        object.__setattr__(self, "objective", _required_text(self.objective, label="objective"))
        if type(self.target) is not TargetDescriptor:
            raise TypeError("goal target must be TargetDescriptor")
        if type(self.budget) is not GoalBudgetContract:
            raise TypeError("goal budget must be GoalBudgetContract")
        if type(self.evidence_policy) is not EvidencePolicy:
            raise TypeError("goal evidence_policy must be EvidencePolicy")
        if type(self.acceptance) not in (list, tuple):
            raise ValueError("goal acceptance must be a list or tuple")
        acceptance = tuple(self.acceptance)
        if any(type(item) is not AcceptancePredicate for item in acceptance):
            raise TypeError("goal acceptance entries must be AcceptancePredicate")
        if len({item.predicate_id for item in acceptance}) != len(acceptance):
            raise ValueError("goal acceptance predicate ids must be unique")
        object.__setattr__(
            self,
            "acceptance",
            tuple(sorted(acceptance, key=lambda item: item.predicate_id)),
        )
        object.__setattr__(self, "scope", _string_sequence(self.scope, label="goal scope"))
        object.__setattr__(self, "non_goals", _string_sequence(self.non_goals, label="goal non_goals"))
        if self.policy_digest != "unbound":
            object.__setattr__(self, "policy_digest", _required_text(self.policy_digest, label="policy_digest"))
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("goal generation must be a positive integer")
        if self.parent_goal_id is not None:
            object.__setattr__(self, "parent_goal_id", _required_text(self.parent_goal_id, label="parent_goal_id"))
        if self.parent_contract_hash is not None:
            object.__setattr__(
                self,
                "parent_contract_hash",
                _required_text(self.parent_contract_hash, label="parent_contract_hash"),
            )
        object.__setattr__(self, "author_id", _required_text(self.author_id, label="author_id"))
        if type(self.effective_epoch) is not int or self.effective_epoch < 0:
            raise ValueError("goal effective_epoch must be non-negative")
        parent_fields = (
            self.parent_goal_id,
            self.parent_contract_hash,
            self.evolution_reason,
        )
        has_parent = any(value is not None for value in parent_fields)
        if (
            self.generation == 1
            and has_parent
            and (
                self.parent_goal_id is None
                or self.parent_contract_hash is None
                or not _is_digest(self.parent_contract_hash)
                or self.evolution_reason is None
            )
        ):
            raise ValueError("child goal requires parent_goal_id, parent_contract_hash and evolution_reason")
        if self.generation > 1 and (
            self.parent_goal_id is None
            or self.parent_contract_hash is None
            or not _is_digest(self.parent_contract_hash)
            or self.evolution_reason is None
        ):
            raise ValueError("evolved goal requires parent_goal_id, parent_contract_hash and evolution_reason")
        if self.evolution_reason is not None:
            object.__setattr__(
                self,
                "evolution_reason",
                _required_text(self.evolution_reason, label="evolution_reason"),
            )
        if type(self.created_at_ms) is not int or self.created_at_ms < 0:
            raise ValueError("goal created_at_ms must be a non-negative integer")
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(self.definition_dict())
        object.__setattr__(self, "contract_hash", _digest(self.definition_dict()))

    @property
    def has_explicit_acceptance(self) -> bool:
        return bool(self.acceptance)

    def validate_for_admission(self, *, limits: GoalContractLimits | None = None) -> None:
        """Apply stricter checks at the execution boundary."""

        (DEFAULT_GOAL_CONTRACT_LIMITS if limits is None else limits).validate_wire(self.to_dict())
        self.target.validate(require_bound=True)
        if not self.acceptance:
            raise GoalContractError("admitted goal requires at least one acceptance predicate")
        if self.policy_digest == "unbound" or not _is_digest(self.policy_digest):
            raise GoalContractError("admitted goal requires a bound policy digest")
        required = tuple(predicate for predicate in self.acceptance if predicate.required)
        if not required:
            raise GoalContractError("admitted goal requires at least one required predicate")
        if self.evidence_policy.require_independent_verifier and any(
            not predicate.evidence_refs for predicate in required
        ):
            raise GoalContractError("required predicates need at least one evidence reference")

    def definition_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "goal_id": self.goal_id,
            "generation": self.generation,
            "objective": self.objective,
            "acceptance": [item.to_dict() for item in self.acceptance],
            "target": self.target.to_dict(),
            "scope": list(self.scope),
            "non_goals": list(self.non_goals),
            "policy_digest": self.policy_digest,
            "budget": self.budget.to_dict(),
            "evidence_policy": self.evidence_policy.to_dict(),
            "parent_goal_id": self.parent_goal_id,
            "parent_contract_hash": self.parent_contract_hash,
            "author_id": self.author_id,
            "effective_epoch": self.effective_epoch,
            "evolution_reason": self.evolution_reason,
            "created_at_ms": self.created_at_ms,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.definition_dict(), "contract_hash": self.contract_hash}

    def canonical_json(self) -> str:
        """Return the exact JSON form used by the native contract boundary."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        limits: GoalContractLimits | None = None,
    ) -> GoalContract:
        (DEFAULT_GOAL_CONTRACT_LIMITS if limits is None else limits).validate_wire(value)
        value = _as_string_mapping(value, label="goal contract")
        if value.get("schema") != cls.SCHEMA:
            raise GoalContractError("invalid goal contract schema")
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schema",
                    "goal_id",
                    "generation",
                    "objective",
                    "acceptance",
                    "target",
                    "scope",
                    "non_goals",
                    "policy_digest",
                    "budget",
                    "evidence_policy",
                    "parent_goal_id",
                    "parent_contract_hash",
                    "author_id",
                    "effective_epoch",
                    "evolution_reason",
                    "created_at_ms",
                    "contract_hash",
                }
            ),
            label="goal contract",
        )
        raw_acceptance = value.get("acceptance", ())
        if type(raw_acceptance) not in (list, tuple):
            raise GoalContractError("goal acceptance must be a list")
        typed_acceptance = cast(list[object] | tuple[object, ...], raw_acceptance)
        try:
            acceptance_items: list[AcceptancePredicate] = []
            for raw_item in typed_acceptance:
                item = _as_string_mapping(raw_item, label="goal acceptance entry")
                _require_exact_keys(
                    item,
                    frozenset(
                        {
                            "predicate_id",
                            "description",
                            "evaluator",
                            "inputs",
                            "expected_result",
                            "severity",
                            "evidence_refs",
                            "reproducibility_requirements",
                        }
                    ),
                    label="goal acceptance predicate",
                )
                acceptance_items.append(
                    AcceptancePredicate(
                        predicate_id=_required_text(item.get("predicate_id"), label="predicate_id"),
                        description=_required_text(item.get("description"), label="predicate description"),
                        evaluator=_required_text(item.get("evaluator"), label="predicate evaluator"),
                        inputs=_canonical_string_sequence(item.get("inputs"), label="predicate inputs"),
                        expected_result=_json_scalar_value(
                            item.get("expected_result"), label="predicate expected_result"
                        ),
                        severity=_required_text(item.get("severity"), label="predicate severity"),
                        evidence_refs=_canonical_string_sequence(
                            item.get("evidence_refs"), label="predicate evidence_refs"
                        ),
                        reproducibility_requirements=_canonical_string_sequence(
                            item.get("reproducibility_requirements"),
                            label="predicate reproducibility_requirements",
                        ),
                    )
                )
            predicate_ids = tuple(item.predicate_id for item in acceptance_items)
            if predicate_ids != tuple(sorted(predicate_ids)):
                raise GoalContractError("goal acceptance must be in canonical order")
            acceptance = tuple(acceptance_items)
        except GoalContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise GoalContractError("invalid goal acceptance predicate") from exc
        raw_target = _as_string_mapping(value.get("target"), label="goal target")
        _require_exact_keys(
            raw_target,
            frozenset(
                {
                    "kind",
                    "stable_id",
                    "revision_or_digest",
                    "read_roots",
                    "write_roots",
                    "network_allowlist",
                    "external_side_effects",
                    "owner",
                }
            ),
            label="goal target",
        )
        raw_budget = _as_string_mapping(value.get("budget"), label="goal budget")
        _require_exact_keys(
            raw_budget,
            frozenset(
                {
                    "token_limit",
                    "attempt_limit",
                    "wall_time_limit_ms",
                    "cost_limit_minor_units",
                    "cpu_time_limit_ms",
                    "reserved_tokens",
                }
            ),
            label="goal budget",
        )
        raw_evidence = _as_string_mapping(value.get("evidence_policy"), label="goal evidence policy")
        _require_exact_keys(
            raw_evidence,
            frozenset(
                {
                    "required_record_types",
                    "minimum_sources",
                    "require_independent_verifier",
                    "trusted_verifier_ids",
                }
            ),
            label="goal evidence policy",
        )
        try:
            contract = cls(
                goal_id=_required_text(value.get("goal_id"), label="goal_id"),
                objective=_required_text(value.get("objective"), label="objective"),
                target=TargetDescriptor(
                    kind=_required_text(raw_target.get("kind"), label="target kind"),
                    stable_id=_required_text(raw_target.get("stable_id"), label="target stable_id"),
                    revision_or_digest=_required_text(
                        raw_target.get("revision_or_digest"), label="target revision_or_digest"
                    ),
                    read_roots=_canonical_string_sequence(raw_target.get("read_roots"), label="target read_roots"),
                    write_roots=_canonical_string_sequence(raw_target.get("write_roots"), label="target write_roots"),
                    network_allowlist=_canonical_string_sequence(
                        raw_target.get("network_allowlist"), label="target network_allowlist"
                    ),
                    external_side_effects=_canonical_string_sequence(
                        raw_target.get("external_side_effects"), label="target external_side_effects"
                    ),
                    owner=_required_text(raw_target.get("owner"), label="target owner"),
                ),
                acceptance=acceptance,
                scope=_canonical_string_sequence(value.get("scope"), label="goal scope"),
                non_goals=_canonical_string_sequence(value.get("non_goals"), label="goal non_goals"),
                policy_digest=_required_text(value.get("policy_digest"), label="policy_digest"),
                budget=GoalBudgetContract(
                    token_limit=_positive_int(raw_budget.get("token_limit"), label="budget token_limit"),
                    attempt_limit=_positive_int(raw_budget.get("attempt_limit"), label="budget attempt_limit"),
                    wall_time_limit_ms=_nonnegative_int(
                        raw_budget.get("wall_time_limit_ms"),
                        label="budget wall_time_limit_ms",
                        optional=True,
                    ),
                    cost_limit_minor_units=_nonnegative_int(
                        raw_budget.get("cost_limit_minor_units"),
                        label="budget cost_limit_minor_units",
                        optional=True,
                    ),
                    cpu_time_limit_ms=_nonnegative_int(
                        raw_budget.get("cpu_time_limit_ms"),
                        label="budget cpu_time_limit_ms",
                        optional=True,
                    ),
                    reserved_tokens=_nonnegative_int(raw_budget.get("reserved_tokens"), label="budget reserved_tokens")
                    or 0,
                ),
                evidence_policy=EvidencePolicy(
                    required_record_types=_canonical_string_sequence(
                        raw_evidence.get("required_record_types"),
                        label="evidence required_record_types",
                    ),
                    minimum_sources=_nonnegative_int(
                        raw_evidence.get("minimum_sources"), label="evidence minimum_sources"
                    )
                    or 0,
                    require_independent_verifier=_required_bool(
                        raw_evidence.get("require_independent_verifier"),
                        label="evidence require_independent_verifier",
                    ),
                    trusted_verifier_ids=_canonical_string_sequence(
                        raw_evidence.get("trusted_verifier_ids"),
                        label="evidence trusted_verifier_ids",
                    ),
                ),
                generation=_positive_int(value.get("generation"), label="goal generation"),
                parent_goal_id=_optional_text(value.get("parent_goal_id"), label="parent_goal_id"),
                parent_contract_hash=_optional_text(value.get("parent_contract_hash"), label="parent_contract_hash"),
                author_id=_required_text(value.get("author_id"), label="author_id"),
                effective_epoch=_nonnegative_int(value.get("effective_epoch"), label="goal effective_epoch") or 0,
                evolution_reason=_optional_text(value.get("evolution_reason"), label="evolution_reason"),
                created_at_ms=_nonnegative_int(value.get("created_at_ms"), label="goal created_at_ms") or 0,
            )
        except GoalContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise GoalContractError("invalid goal contract fields") from exc
        if value.get("contract_hash") != contract.contract_hash:
            raise GoalContractError("goal contract hash mismatch")
        return contract

    def evolve(
        self,
        *,
        expected_generation: int,
        author_id: str,
        effective_epoch: int,
        reason: str,
        objective: str | None = None,
        target: TargetDescriptor | None = None,
        acceptance: tuple[AcceptancePredicate, ...] | None = None,
        scope: tuple[str, ...] | None = None,
        non_goals: tuple[str, ...] | None = None,
    ) -> GoalContract:
        if expected_generation != self.generation:
            raise StaleGoalGeneration("goal contract generation is stale")
        next_acceptance = self.acceptance if acceptance is None else acceptance
        current_by_id = {item.predicate_id: item for item in self.acceptance}
        next_by_id = {item.predicate_id: item for item in next_acceptance}
        current_ids = set(current_by_id)
        next_ids = set(next_by_id)
        if not current_ids.issubset(next_ids):
            raise GoalContractError("contract evolution cannot silently remove acceptance predicates")
        for predicate_id, previous in current_by_id.items():
            updated = next_by_id[predicate_id]
            if previous.required and not updated.required:
                raise GoalContractError("contract evolution cannot weaken a required predicate")
            if updated.evaluator != previous.evaluator:
                raise GoalContractError("contract evolution cannot replace a predicate evaluator")
            if updated.inputs != previous.inputs:
                raise GoalContractError("contract evolution cannot change predicate inputs")
            if updated.expected_result != previous.expected_result:
                raise GoalContractError("contract evolution cannot change a predicate result")
            if not set(previous.evidence_refs).issubset(updated.evidence_refs):
                raise GoalContractError("contract evolution cannot remove predicate evidence")
            if not set(previous.reproducibility_requirements).issubset(updated.reproducibility_requirements):
                raise GoalContractError("contract evolution cannot remove reproducibility requirements")
        if effective_epoch <= self.effective_epoch:
            raise GoalContractError("contract evolution epoch must increase")
        next_scope = self.scope if scope is None else _string_sequence(scope, label="goal scope")
        if not set(next_scope).issubset(self.scope):
            raise GoalContractError("contract evolution cannot expand goal scope")
        next_non_goals = self.non_goals if non_goals is None else _string_sequence(non_goals, label="goal non_goals")
        if not set(self.non_goals).issubset(next_non_goals):
            raise GoalContractError("contract evolution cannot remove non-goal constraints")
        next_target = self.target if target is None else target
        if type(next_target) is not TargetDescriptor:
            raise TypeError("evolved goal target must be TargetDescriptor")
        if self.target.is_bound:
            if (
                not next_target.is_bound
                or next_target.kind != self.target.kind
                or next_target.stable_id != self.target.stable_id
            ):
                raise GoalContractError("contract evolution cannot retarget or unbind a bound goal")
            if next_target.owner != self.target.owner:
                raise GoalContractError("contract evolution cannot transfer target ownership")
            for field_name in (
                "read_roots",
                "write_roots",
                "network_allowlist",
                "external_side_effects",
            ):
                previous_values = getattr(self.target, field_name)
                next_values = getattr(next_target, field_name)
                if not _target_values_are_subset(field_name, next_values, previous_values):
                    raise GoalContractError(f"contract evolution cannot expand target {field_name}")
        return GoalContract(
            goal_id=self.goal_id,
            objective=self.objective if objective is None else objective,
            target=next_target,
            acceptance=next_acceptance,
            scope=next_scope,
            non_goals=next_non_goals,
            policy_digest=self.policy_digest,
            budget=self.budget,
            evidence_policy=self.evidence_policy,
            generation=self.generation + 1,
            parent_goal_id=self.goal_id,
            parent_contract_hash=self.contract_hash,
            author_id=author_id,
            effective_epoch=effective_epoch,
            evolution_reason=reason,
            created_at_ms=self.created_at_ms,
        )

    def spawn_child(
        self,
        *,
        expected_generation: int,
        child_goal_id: str,
        author_id: str,
        effective_epoch: int,
        reason: str,
        objective: str | None = None,
        target: TargetDescriptor | None = None,
        acceptance: tuple[AcceptancePredicate, ...] | None = None,
        scope: tuple[str, ...] | None = None,
        non_goals: tuple[str, ...] | None = None,
        budget: GoalBudgetContract | None = None,
    ) -> GoalContract:
        """Create a child goal whose authority is a strict parent subset."""

        if expected_generation != self.generation:
            raise StaleGoalGeneration("parent goal generation is stale")
        child_goal_id = _required_text(child_goal_id, label="child_goal_id")
        if child_goal_id == self.goal_id:
            raise GoalContractError("child goal must have a distinct goal id")
        if effective_epoch <= self.effective_epoch:
            raise GoalContractError("child goal epoch must increase")
        child_acceptance = self.acceptance if acceptance is None else acceptance
        current_by_id = {item.predicate_id: item for item in self.acceptance}
        child_by_id = {item.predicate_id: item for item in child_acceptance}
        if not set(current_by_id).issubset(child_by_id):
            raise GoalContractError("child goal cannot remove parent acceptance predicates")
        for predicate_id, previous in current_by_id.items():
            updated = child_by_id[predicate_id]
            if previous.required and not updated.required:
                raise GoalContractError("child goal cannot weaken a required predicate")
            if updated.evaluator != previous.evaluator:
                raise GoalContractError("child goal cannot replace a predicate evaluator")
            if updated.inputs != previous.inputs:
                raise GoalContractError("child goal cannot change predicate inputs")
            if updated.expected_result != previous.expected_result:
                raise GoalContractError("child goal cannot change a predicate result")
            if not set(previous.evidence_refs).issubset(updated.evidence_refs):
                raise GoalContractError("child goal cannot remove predicate evidence")
            if not set(previous.reproducibility_requirements).issubset(updated.reproducibility_requirements):
                raise GoalContractError("child goal cannot remove reproducibility requirements")
        child_scope = self.scope if scope is None else _string_sequence(scope, label="child goal scope")
        if not set(child_scope).issubset(self.scope):
            raise GoalContractError("child goal cannot expand parent scope")
        child_non_goals = (
            self.non_goals if non_goals is None else _string_sequence(non_goals, label="child goal non_goals")
        )
        if not set(self.non_goals).issubset(child_non_goals):
            raise GoalContractError("child goal cannot remove parent non-goal constraints")
        child_target = self.target if target is None else target
        if type(child_target) is not TargetDescriptor:
            raise TypeError("child goal target must be TargetDescriptor")
        if self.target.is_bound:
            if (
                child_target.kind != self.target.kind
                or child_target.stable_id != self.target.stable_id
                or child_target.revision_or_digest != self.target.revision_or_digest
            ):
                raise GoalContractError("child goal cannot retarget the parent target")
            if child_target.owner != self.target.owner:
                raise GoalContractError("child goal cannot transfer target ownership")
            for field_name in (
                "read_roots",
                "write_roots",
                "network_allowlist",
                "external_side_effects",
            ):
                if not _target_values_are_subset(
                    field_name,
                    getattr(child_target, field_name),
                    getattr(self.target, field_name),
                ):
                    raise GoalContractError(f"child goal cannot expand target {field_name}")
        elif child_target != self.target:
            raise GoalContractError("child goal cannot bind or expand an unbound parent target")
        child_budget = self.budget if budget is None else budget
        if type(child_budget) is not GoalBudgetContract:
            raise TypeError("child goal budget must be GoalBudgetContract")
        for field_name in (
            "token_limit",
            "attempt_limit",
            "reserved_tokens",
            "wall_time_limit_ms",
            "cost_limit_minor_units",
            "cpu_time_limit_ms",
        ):
            parent_value = getattr(self.budget, field_name)
            child_value = getattr(child_budget, field_name)
            if parent_value is not None and (child_value is None or child_value > parent_value):
                raise GoalContractError(f"child goal cannot expand budget {field_name}")
        return GoalContract(
            goal_id=child_goal_id,
            objective=self.objective if objective is None else objective,
            target=child_target,
            acceptance=child_acceptance,
            scope=child_scope,
            non_goals=child_non_goals,
            policy_digest=self.policy_digest,
            budget=child_budget,
            evidence_policy=self.evidence_policy,
            generation=1,
            parent_goal_id=self.goal_id,
            parent_contract_hash=self.contract_hash,
            author_id=author_id,
            effective_epoch=effective_epoch,
            evolution_reason=reason,
        )


@dataclass(frozen=True)
class ExecutionBinding:
    """Bind one execution attempt to a goal, target, plan, and receipts.

    ``binding_hash`` is a tamper-evident digest of the canonical unsigned
    fields.  ``owner_id`` and ``principal_id`` are metadata used for
    continuity and audit context; they are not authentication credentials,
    authorization proofs, or cryptographic signatures.
    """

    goal_contract_hash: str
    generation: int
    target_digest: str
    plan_digest: str
    mission_id: str
    task_id: int
    attempt_id: int
    owner_id: str
    principal_id: str
    evidence_digest: str | None = None
    completion_digest: str | None = None
    execution_cell_manifest_hash: str | None = None
    execution_cell_id: str | None = None
    execution_action_kind: str | None = None
    backend_kind: str | None = None
    resource_policy_hash: str | None = None
    binding_hash: str = field(init=False, repr=False)

    SCHEMA: ClassVar[str] = "aegis-execution-binding-v1"

    def __post_init__(self) -> None:
        for value, label in (
            (self.goal_contract_hash, "execution binding goal_contract_hash"),
            (self.target_digest, "execution binding target_digest"),
            (self.plan_digest, "execution binding plan_digest"),
        ):
            if not _is_digest(value):
                raise ValueError(f"{label} must be a 64-character lowercase digest")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("execution binding generation must be positive")
        object.__setattr__(self, "mission_id", _required_text(self.mission_id, label="execution binding mission_id"))
        if type(self.task_id) is not int or self.task_id < 1:
            raise ValueError("execution binding task_id must be positive")
        if type(self.attempt_id) is not int or self.attempt_id < 1:
            raise ValueError("execution binding attempt_id must be positive")
        object.__setattr__(self, "owner_id", _required_text(self.owner_id, label="execution binding owner_id"))
        object.__setattr__(
            self,
            "principal_id",
            _required_text(self.principal_id, label="execution binding principal_id"),
        )
        for value, label in (
            (self.evidence_digest, "execution binding evidence_digest"),
            (self.completion_digest, "execution binding completion_digest"),
            (
                self.execution_cell_manifest_hash,
                "execution binding execution_cell_manifest_hash",
            ),
        ):
            if value is not None and not _is_digest(value):
                raise ValueError(f"{label} must be absent or a 64-character lowercase digest")
        if self.backend_kind is not None:
            object.__setattr__(
                self,
                "backend_kind",
                _required_text(self.backend_kind, label="execution binding backend_kind").lower(),
            )
        if self.resource_policy_hash is not None and not _is_digest(self.resource_policy_hash):
            raise ValueError("execution binding resource_policy_hash must be absent or a 64-character lowercase digest")
        if self.execution_cell_id is not None:
            object.__setattr__(
                self,
                "execution_cell_id",
                _required_text(self.execution_cell_id, label="execution binding execution_cell_id"),
            )
        if self.execution_action_kind is not None:
            object.__setattr__(
                self,
                "execution_action_kind",
                _required_text(
                    self.execution_action_kind,
                    label="execution binding execution_action_kind",
                ).lower(),
            )
        if (self.execution_cell_id is None) != (self.execution_action_kind is None):
            raise ValueError("execution binding cell identity must include execution_cell_id and execution_action_kind")
        if (self.backend_kind is None) != (self.resource_policy_hash is None):
            raise ValueError("execution binding backend identity must include backend_kind and resource_policy_hash")
        if self.execution_cell_id is not None and self.backend_kind is None:
            raise ValueError(
                "execution binding selected cell identity must include backend and resource policy identity"
            )
        if self.execution_cell_manifest_hash is None and (
            self.backend_kind is not None or self.resource_policy_hash is not None or self.execution_cell_id is not None
        ):
            raise ValueError("execution binding backend identity requires an execution-cell manifest")
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(self._unsigned_dict())
        object.__setattr__(self, "binding_hash", _execution_binding_digest(self._unsigned_dict()))

    def _unsigned_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.SCHEMA,
            "goal_contract_hash": self.goal_contract_hash,
            "generation": self.generation,
            "target_digest": self.target_digest,
            "plan_digest": self.plan_digest,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "owner_id": self.owner_id,
            "principal_id": self.principal_id,
            "evidence_digest": self.evidence_digest,
            "completion_digest": self.completion_digest,
        }
        # Keep the legacy wire shape/hash stable when no execution-cell
        # manifest is bound.  Explicit cell-bound executions opt into the
        # additional field and therefore include it in the binding digest.
        if self.execution_cell_manifest_hash is not None:
            payload["execution_cell_manifest_hash"] = self.execution_cell_manifest_hash
        if self.execution_cell_id is not None:
            payload["execution_cell_id"] = self.execution_cell_id
            payload["execution_action_kind"] = self.execution_action_kind
        if self.backend_kind is not None:
            payload["backend_kind"] = self.backend_kind
            payload["resource_policy_hash"] = self.resource_policy_hash
        return payload

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "binding_hash": self.binding_hash}

    def canonical_json(self) -> str:
        """Return the canonical JSON representation used for hashing."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def validate_against(self, contract: GoalContract) -> None:
        """Check binding continuity against one immutable goal generation.

        The target owner check preserves contract continuity only.  It does
        not authenticate either metadata field or prove that the caller is
        authorized to act as that owner or principal.
        """

        if type(contract) is not GoalContract:
            raise TypeError("execution binding contract must be GoalContract")
        if self.goal_contract_hash != contract.contract_hash or self.generation != contract.generation:
            raise StaleGoalGeneration("execution binding is bound to a different contract generation")
        if self.target_digest != contract.target.canonical_digest():
            raise GoalContractError("execution binding target digest does not match contract target")
        if self.owner_id != contract.target.owner:
            raise GoalContractError("execution binding owner does not match target owner")

    @classmethod
    def from_dict(cls, value: object) -> ExecutionBinding:
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(value)
        value = _as_string_mapping(value, label="execution binding")
        legacy_keys = frozenset(
            {
                "schema",
                "goal_contract_hash",
                "generation",
                "target_digest",
                "plan_digest",
                "mission_id",
                "task_id",
                "attempt_id",
                "owner_id",
                "principal_id",
                "evidence_digest",
                "completion_digest",
                "binding_hash",
            }
        )
        manifest_keys = legacy_keys | {"execution_cell_manifest_hash"}
        identity_keys = manifest_keys | {"backend_kind", "resource_policy_hash"}
        selected_identity_keys = identity_keys | {
            "execution_cell_id",
            "execution_action_kind",
        }
        if set(value) not in (
            set(legacy_keys),
            set(manifest_keys),
            set(identity_keys),
            set(selected_identity_keys),
        ):
            raise GoalContractError("execution binding contains missing or unknown fields")
        if value.get("schema") != cls.SCHEMA:
            raise GoalContractError("invalid execution binding schema")
        try:
            binding = cls(
                goal_contract_hash=_required_text(value.get("goal_contract_hash"), label="goal_contract_hash"),
                generation=_positive_int(value.get("generation"), label="generation"),
                target_digest=_required_text(value.get("target_digest"), label="target_digest"),
                plan_digest=_required_text(value.get("plan_digest"), label="plan_digest"),
                mission_id=_required_text(value.get("mission_id"), label="mission_id"),
                task_id=_positive_int(value.get("task_id"), label="task_id"),
                attempt_id=_positive_int(value.get("attempt_id"), label="attempt_id"),
                owner_id=_required_text(value.get("owner_id"), label="owner_id"),
                principal_id=_required_text(value.get("principal_id"), label="principal_id"),
                evidence_digest=(
                    None
                    if value.get("evidence_digest") is None
                    else _required_text(value.get("evidence_digest"), label="evidence_digest")
                ),
                completion_digest=(
                    None
                    if value.get("completion_digest") is None
                    else _required_text(value.get("completion_digest"), label="completion_digest")
                ),
                execution_cell_manifest_hash=(
                    None
                    if value.get("execution_cell_manifest_hash") is None
                    else _required_text(
                        value.get("execution_cell_manifest_hash"),
                        label="execution_cell_manifest_hash",
                    )
                ),
                execution_cell_id=(
                    None
                    if value.get("execution_cell_id") is None
                    else _required_text(value.get("execution_cell_id"), label="execution_cell_id")
                ),
                execution_action_kind=(
                    None
                    if value.get("execution_action_kind") is None
                    else _required_text(value.get("execution_action_kind"), label="execution_action_kind")
                ),
                backend_kind=(
                    None
                    if value.get("backend_kind") is None
                    else _required_text(value.get("backend_kind"), label="backend_kind")
                ),
                resource_policy_hash=(
                    None
                    if value.get("resource_policy_hash") is None
                    else _required_text(value.get("resource_policy_hash"), label="resource_policy_hash")
                ),
            )
        except (TypeError, ValueError, GoalContractError) as exc:
            raise GoalContractError("invalid execution binding fields") from exc
        if value.get("binding_hash") != binding.binding_hash:
            raise GoalContractError("execution binding hash mismatch")
        return binding


@dataclass(frozen=True)
class GoalVerification:
    """Verifier-produced result for one immutable goal generation.

    This is a receipt, not an evaluator.  The evaluator remains an explicit
    controller-owned boundary; this value only makes its output typed,
    contract-bound, reproducible and tamper-evident before settlement.
    """

    contract_hash: str
    generation: int
    verifier_id: str
    independent: bool
    predicate_results: tuple[tuple[str, bool], ...]
    evidence_refs: tuple[str, ...] = ()
    evidence_complete: bool = False
    verification_hash: str = field(init=False, repr=False)

    SCHEMA: ClassVar[str] = "aegis-goal-verification-v1"

    def __post_init__(self) -> None:
        if not _is_digest(self.contract_hash):
            raise ValueError("goal verification contract_hash must be a 64-character digest")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("goal verification generation must be positive")
        object.__setattr__(self, "verifier_id", _required_text(self.verifier_id, label="verifier_id"))
        if type(self.independent) is not bool:
            raise TypeError("goal verification independent must be boolean")
        if type(self.predicate_results) not in (list, tuple):
            raise ValueError("goal verification predicate_results must be a sequence")
        pairs = tuple(self.predicate_results)
        if any(
            type(pair) not in (list, tuple)
            or len(pair) != 2
            or type(pair[0]) is not str
            or not pair[0].strip()
            or type(pair[1]) is not bool
            for pair in pairs
        ):
            raise ValueError("goal verification predicate_results are invalid")
        if len({pair[0] for pair in pairs}) != len(pairs):
            raise ValueError("goal verification predicate ids must be unique")
        normalized_pairs = tuple((pair[0], pair[1]) for pair in pairs)
        object.__setattr__(
            self,
            "predicate_results",
            tuple(sorted(normalized_pairs, key=lambda pair: pair[0])),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _string_sequence(self.evidence_refs, label="goal verification evidence_refs"),
        )
        if type(self.evidence_complete) is not bool:
            raise TypeError("goal verification evidence_complete must be boolean")
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(self._unsigned_dict())
        object.__setattr__(self, "verification_hash", _digest(self._unsigned_dict()))

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "contract_hash": self.contract_hash,
            "generation": self.generation,
            "verifier_id": self.verifier_id,
            "independent": self.independent,
            "predicate_results": [list(pair) for pair in self.predicate_results],
            "evidence_refs": list(self.evidence_refs),
            "evidence_complete": self.evidence_complete,
        }

    def validate_against(self, contract: GoalContract) -> None:
        if type(contract) is not GoalContract:
            raise TypeError("goal verification contract must be GoalContract")
        if self.contract_hash != contract.contract_hash or self.generation != contract.generation:
            raise StaleGoalGeneration("goal verification is bound to a different contract generation")
        if contract.evidence_policy.require_independent_verifier and not self.independent:
            raise GoalContractError("goal verification requires an independent verifier")
        if (
            contract.evidence_policy.require_independent_verifier
            and self.verifier_id not in contract.evidence_policy.trusted_verifier_ids
        ):
            raise GoalContractError("goal verifier is not trusted by the contract")
        if self.verifier_id == contract.author_id:
            raise GoalContractError("goal verifier must differ from the goal author")
        known_ids = {predicate.predicate_id for predicate in contract.acceptance}
        result_ids = {predicate_id for predicate_id, _ in self.predicate_results}
        if not result_ids.issubset(known_ids):
            raise GoalContractError("goal verification contains an unknown predicate")
        required_ids = {predicate.predicate_id for predicate in contract.acceptance if predicate.required}
        required_evidence = {
            evidence_ref
            for predicate in contract.acceptance
            if predicate.required
            for evidence_ref in predicate.evidence_refs
        }
        if self.evidence_complete and not required_ids.issubset(result_ids):
            raise GoalContractError("complete goal verification is missing a required predicate result")
        if self.evidence_complete:
            evidence_refs = set(self.evidence_refs)
            for predicate in contract.acceptance:
                if predicate.predicate_id in result_ids and not set(predicate.evidence_refs).issubset(evidence_refs):
                    raise GoalContractError("complete goal verification is missing predicate evidence")
        if self.evidence_complete and not required_evidence.issubset(set(self.evidence_refs)):
            raise GoalContractError("complete goal verification is missing required evidence")

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "verification_hash": self.verification_hash}

    @classmethod
    def from_dict(cls, value: object) -> GoalVerification:
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(value)
        value = _as_string_mapping(value, label="goal verification")
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schema",
                    "contract_hash",
                    "generation",
                    "verifier_id",
                    "independent",
                    "predicate_results",
                    "evidence_refs",
                    "evidence_complete",
                    "verification_hash",
                }
            ),
            label="goal verification",
        )
        if value.get("schema") != cls.SCHEMA:
            raise GoalContractError("invalid goal verification schema")
        raw_pairs = value.get("predicate_results")
        if type(raw_pairs) not in (list, tuple):
            raise GoalContractError("goal verification predicate_results must be a list")
        pairs: list[tuple[str, bool]] = []
        for raw_pair in cast(list[object] | tuple[object, ...], raw_pairs):
            if not isinstance(raw_pair, (list, tuple)):
                raise GoalContractError("goal verification predicate result is invalid")
            pair = cast(list[object] | tuple[object, ...], raw_pair)
            if len(pair) != 2:
                raise GoalContractError("goal verification predicate result is invalid")
            pairs.append(
                (
                    _required_text(pair[0], label="goal verification predicate id"),
                    _required_bool(pair[1], label="goal verification predicate result"),
                )
            )
        try:
            verification = cls(
                contract_hash=_required_text(value.get("contract_hash"), label="contract_hash"),
                generation=_positive_int(value.get("generation"), label="generation"),
                verifier_id=_required_text(value.get("verifier_id"), label="verifier_id"),
                independent=_required_bool(value.get("independent"), label="independent"),
                predicate_results=tuple(pairs),
                evidence_refs=_string_sequence(value.get("evidence_refs"), label="goal verification evidence_refs"),
                evidence_complete=_required_bool(value.get("evidence_complete"), label="evidence_complete"),
            )
        except (TypeError, ValueError) as exc:
            raise GoalContractError("invalid goal verification fields") from exc
        if value.get("verification_hash") != verification.verification_hash:
            raise GoalContractError("goal verification hash mismatch")
        return verification


@dataclass(frozen=True)
class GoalProgress:
    """Mutable-looking progress represented as immutable CAS-style snapshots."""

    contract_hash: str
    generation: int
    lifecycle: GoalLifecycle = GoalLifecycle.DRAFT
    criterion_status: tuple[tuple[str, str], ...] = ()
    required_criteria: tuple[str, ...] = ()
    verdict: GoalVerdict | None = None
    stop_reason: str | None = None

    def __post_init__(self) -> None:
        if not _is_digest(self.contract_hash):
            raise ValueError("goal progress contract_hash must be a 64-character digest")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("goal progress generation must be positive")
        if type(self.lifecycle) is not GoalLifecycle:
            raise TypeError("goal progress lifecycle must be GoalLifecycle")
        if type(self.criterion_status) not in (list, tuple):
            raise ValueError("goal progress criterion_status must be a sequence")
        pairs = tuple(self.criterion_status)
        if any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or type(pair[1]) is not str
            or not pair[0].strip()
            or pair[1] not in {"open", "verified", "failed", "unchecked"}
            for pair in pairs
        ):
            raise ValueError("goal progress criterion_status is invalid")
        if len({pair[0] for pair in pairs}) != len(pairs):
            raise ValueError("goal progress criterion ids must be unique")
        object.__setattr__(self, "criterion_status", pairs)
        object.__setattr__(
            self,
            "required_criteria",
            _string_sequence(
                self.required_criteria,
                label="goal progress required_criteria",
            ),
        )
        if any(predicate_id not in {pair[0] for pair in pairs} for predicate_id in self.required_criteria):
            raise ValueError("goal progress required criterion is not in criterion_status")
        if self.verdict is not None and type(self.verdict) is not GoalVerdict:
            raise TypeError("goal progress verdict must be GoalVerdict or unset")
        if self.verdict is not None and self.lifecycle is not GoalLifecycle.SETTLED:
            raise ValueError("goal progress verdict requires a settled lifecycle")
        if self.stop_reason is not None:
            object.__setattr__(self, "stop_reason", _required_text(self.stop_reason, label="goal stop_reason"))
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(self.to_dict())

    @classmethod
    def from_contract(cls, contract: GoalContract) -> GoalProgress:
        return cls(
            contract_hash=contract.contract_hash,
            generation=contract.generation,
            criterion_status=tuple((predicate.predicate_id, "open") for predicate in contract.acceptance),
            required_criteria=tuple(predicate.predicate_id for predicate in contract.acceptance if predicate.required),
        )

    def transition(self, *, expected_generation: int, next_lifecycle: GoalLifecycle) -> GoalProgress:
        if expected_generation != self.generation:
            raise StaleGoalGeneration("goal progress generation is stale")
        if type(next_lifecycle) is not GoalLifecycle:
            raise TypeError("goal progress next_lifecycle must be GoalLifecycle")
        if next_lifecycle == self.lifecycle:
            return self
        if next_lifecycle not in _ALLOWED_TRANSITIONS[self.lifecycle]:
            raise GoalContractError(f"invalid goal transition: {self.lifecycle.value}->{next_lifecycle.value}")
        return replace(self, lifecycle=next_lifecycle)

    def settle(
        self,
        *,
        expected_generation: int,
        verification: GoalVerification,
        contract: GoalContract,
    ) -> GoalProgress:
        if expected_generation != self.generation:
            raise StaleGoalGeneration("goal progress generation is stale")
        if self.verdict is not None:
            raise GoalContractError("goal progress verdict is already settled")
        if self.lifecycle != GoalLifecycle.SETTLED:
            raise GoalContractError("goal must be settled before a verifier assigns a verdict")
        if type(contract) is not GoalContract:
            raise TypeError("goal progress contract must be GoalContract")
        if self.contract_hash != contract.contract_hash or self.generation != contract.generation:
            raise StaleGoalGeneration("goal progress is bound to a different contract generation")
        if type(verification) is not GoalVerification:
            raise TypeError("goal progress verification must be GoalVerification")
        verification.validate_against(contract)
        required_results = dict(verification.predicate_results)
        statuses = tuple(
            (
                predicate_id,
                "verified"
                if required_results.get(predicate_id) is True
                else "failed"
                if predicate_id in required_results
                else "unchecked",
            )
            for predicate_id, _ in self.criterion_status
        )
        required_pass = bool(self.required_criteria) and all(
            required_results.get(predicate_id) is True for predicate_id in self.required_criteria
        )
        verdict = (
            GoalVerdict.VERIFIED
            if required_pass and verification.evidence_complete
            else GoalVerdict.FAILED
            if any(required_results.get(predicate_id) is False for predicate_id in self.required_criteria)
            else GoalVerdict.INCONCLUSIVE
        )
        return replace(self, criterion_status=statuses, verdict=verdict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "aegis-goal-progress-v1",
            "contract_hash": self.contract_hash,
            "generation": self.generation,
            "lifecycle": self.lifecycle.value,
            "criterion_status": [list(pair) for pair in self.criterion_status],
            "required_criteria": list(self.required_criteria),
            "verdict": self.verdict.value if self.verdict is not None else None,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, value: object, *, contract: GoalContract) -> GoalProgress:
        DEFAULT_GOAL_CONTRACT_LIMITS.validate_wire(value)
        value = _as_string_mapping(value, label="goal progress")
        if value.get("schema") != "aegis-goal-progress-v1":
            raise GoalContractError("invalid goal progress schema")
        _require_exact_keys(
            value,
            frozenset(
                {
                    "schema",
                    "contract_hash",
                    "generation",
                    "lifecycle",
                    "criterion_status",
                    "required_criteria",
                    "verdict",
                    "stop_reason",
                }
            ),
            label="goal progress",
        )
        if type(contract) is not GoalContract:
            raise TypeError("goal progress contract must be GoalContract")
        if value.get("contract_hash") != contract.contract_hash or value.get("generation") != contract.generation:
            raise GoalContractError("goal progress contract binding mismatch")
        raw_status = value.get("criterion_status")
        if type(raw_status) not in (list, tuple):
            raise GoalContractError("goal progress criterion_status must be a list")
        typed_status = cast(list[object] | tuple[object, ...], raw_status)
        try:
            typed_pairs_list: list[list[object] | tuple[object, ...]] = []
            for raw_item in typed_status:
                if not isinstance(raw_item, (list, tuple)):
                    raise GoalContractError("goal progress criterion_status entry is invalid")
                item = cast(list[object] | tuple[object, ...], raw_item)
                if len(item) != 2:
                    raise GoalContractError("goal progress criterion_status entry is invalid")
                typed_pairs_list.append(item)
            typed_pairs = tuple(typed_pairs_list)
            criterion_status = tuple(
                (
                    _required_text(item[0], label="goal progress criterion id"),
                    _required_text(item[1], label="goal progress criterion status"),
                )
                for item in typed_pairs
            )
            lifecycle = GoalLifecycle(_required_text(value.get("lifecycle"), label="goal lifecycle"))
            raw_verdict = value.get("verdict")
            verdict = None if raw_verdict is None else GoalVerdict(_required_text(raw_verdict, label="goal verdict"))
            progress = cls(
                contract_hash=contract.contract_hash,
                generation=contract.generation,
                lifecycle=lifecycle,
                criterion_status=criterion_status,
                required_criteria=_string_sequence(
                    value.get("required_criteria"), label="goal progress required_criteria"
                ),
                verdict=verdict,
                stop_reason=_optional_text(value.get("stop_reason"), label="goal stop_reason"),
            )
        except (TypeError, ValueError) as exc:
            raise GoalContractError("invalid goal progress fields") from exc
        expected_status_ids = tuple(predicate.predicate_id for predicate in contract.acceptance)
        if tuple(predicate_id for predicate_id, _ in progress.criterion_status) != expected_status_ids:
            raise GoalContractError("goal progress criteria do not match the contract")
        expected_required = tuple(predicate.predicate_id for predicate in contract.acceptance if predicate.required)
        if progress.required_criteria != expected_required:
            raise GoalContractError("goal progress required criteria do not match the contract")
        return progress


__all__ = [
    "DEFAULT_GOAL_CONTRACT_LIMITS",
    "AcceptancePredicate",
    "EvidencePolicy",
    "GoalBudgetContract",
    "GoalContract",
    "GoalContractError",
    "GoalContractLimits",
    "GoalLifecycle",
    "GoalProgress",
    "GoalVerdict",
    "GoalVerification",
    "StaleGoalGeneration",
    "TargetDescriptor",
    "external_side_effect_key",
]
