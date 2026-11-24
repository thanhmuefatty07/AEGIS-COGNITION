"""Token-optimal multi-agent team protocol (AIP-v2), long-horizon anti-drift, and blinded adversarial audit.

This module implements:
1. ``CompactHandleEnvelope`` (``aegis-aip-v2``): zero-prose machine delta packets
   carrying content-addressed handles (``@sym:``, ``@ev:``, ``@fold:``, ``@obl:``,
   ``@art:``, ``@patch:``) instead of conversational transcripts.
2. ``SharedTaskDeltaBoard``: thread-safe multi-agent task/lease board with
   ``blocks``/``blocked_by`` propagation, hierarchical ``path:``/``file:``/``symbol:``
   conflict rejection, and ``since_seq`` delta synchronization.
3. ``GoalRoundDriver``: ``<goal_round>`` monotonic ``(goal_id, revision, round)``
   anti-drift state anchor rejecting stale goal revisions.
4. ``ToolResultPruner`` & ``compact_conversation_history``: UTF-8/Unicode-safe
   head+tail tool output pruning backed by content-addressed handles and
   ``toolPairingBalanced`` KV-cache-preserving turn compaction.
5. ``AdversarialAuditGate`` & ``ReconstructionObligationLedger``: author-blinded,
   CoT-stripped adversarial review with deterministic single-counterexample veto
   and ``floor(2N/3) + 1`` Byzantine supermajority receipt verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from .subagents import AgentCoordinationError, resource_keys_conflict


AIP_SCHEMA_V2 = "aegis-aip-v2"
GOAL_ROUND_SCHEMA_V1 = "aegis-goal-round-v1"
AUDIT_LEDGER_SCHEMA_V1 = "aegis-audit-obligation-ledger-v1"

_HANDLE_PREFIXES = ("@sym:", "@ev:", "@fold:", "@obl:", "@art:", "@patch:")
_AIP_KINDS = frozenset({"TASK_CLAIM", "LEASE_UPDATE", "DELTA_PUBLISH", "OBLIGATION_UPDATE", "HEARTBEAT"})
_TASK_BOARD_STATUSES = frozenset({"pending", "in_progress", "blocked", "completed", "failed"})
_COT_BLOCK_RE = re.compile(
    r"<(?:thinking|thought|Scratchpad|rationale)>.*?</(?:thinking|thought|Scratchpad|rationale)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_SUBJECTIVE_SCORE_RE = re.compile(
    r"\b(?:confidence|score|rating)\s*[:=]\s*(?:\d+(?:\.\d+)?\s*/\s*10|\d{1,3}%)\b",
    flags=re.IGNORECASE,
)


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise AgentCoordinationError("protocol payload is not canonical JSON") from error
    return encoded.encode("utf-8")


def _sha256_hex(value: object) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _bounded_id(value: object, name: str, *, max_len: int = 128) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value or len(value) > max_len:
        raise AgentCoordinationError(f"{name} must be bounded non-empty text")
    if any(ch.isspace() for ch in value):
        raise AgentCoordinationError(f"{name} must not contain whitespace")
    return value


def validate_compact_handle(handle: object, name: str = "compact handle") -> str:
    """Validate a compact typed handle (`@sym:<hex>`, `@ev:<hex>`, `@fold:<hex>`, etc.)."""

    if type(handle) is not str or len(handle) > 96 or "\x00" in handle:
        raise AgentCoordinationError(f"{name} must be bounded handle text")
    for prefix in _HANDLE_PREFIXES:
        if handle.startswith(prefix):
            digest = handle[len(prefix) :]
            if 8 <= len(digest) <= 64 and all(ch in "0123456789abcdef" for ch in digest):
                return handle
            raise AgentCoordinationError(f"{name} digest suffix must be lowercase hex [8..64]")
    raise AgentCoordinationError(f"{name} must start with one of {_HANDLE_PREFIXES}")


def make_compact_handle(
    kind: Literal["sym", "ev", "fold", "obl", "art", "patch"],
    content: bytes | str | Mapping[str, object],
    *,
    hex_chars: int = 24,
) -> str:
    """Mint a deterministic content-addressed compact handle."""

    if kind not in {"sym", "ev", "fold", "obl", "art", "patch"}:
        raise AgentCoordinationError(f"unsupported handle kind: {kind}")
    if not 8 <= hex_chars <= 64:
        raise AgentCoordinationError("hex_chars must be within [8, 64]")
    if isinstance(content, bytes):
        digest = hashlib.blake2b(content, digest_size=32).hexdigest()[:hex_chars]
    elif isinstance(content, str):
        digest = hashlib.blake2b(content.encode("utf-8"), digest_size=32).hexdigest()[:hex_chars]
    else:
        digest = hashlib.blake2b(_canonical_json_bytes(content), digest_size=32).hexdigest()[:hex_chars]
    return validate_compact_handle(f"@{kind}:{digest}")


@dataclass(frozen=True, slots=True)
class CompactHandleEnvelope:
    """Zero-prose machine-to-machine coordination frame (`aegis-aip-v2`)."""

    run_id: str
    sender_agent: str
    recipient_agent: str
    kind: str
    task_id: int
    delta_seq: int
    status: str
    handles: tuple[str, ...] = ()
    touched_resources: tuple[str, ...] = ()
    metric_deltas: tuple[tuple[str, int], ...] = ()
    goal_revision: int = 1
    goal_round: int = 1

    def _payload(self) -> dict[str, object]:
        return {
            "schema": AIP_SCHEMA_V2,
            "r": self.run_id,
            "s": self.sender_agent,
            "t": self.recipient_agent,
            "k": self.kind,
            "tid": self.task_id,
            "seq": self.delta_seq,
            "st": self.status,
            "h": list(self.handles),
            "res": list(self.touched_resources),
            "m": {key: val for key, val in self.metric_deltas},
            "grev": self.goal_revision,
            "grnd": self.goal_round,
        }

    @property
    def envelope_hash(self) -> str:
        return _sha256_hex(self._payload())

    def validate(self) -> None:
        _bounded_id(self.run_id, "run_id")
        _bounded_id(self.sender_agent, "sender_agent")
        _bounded_id(self.recipient_agent, "recipient_agent")
        if self.kind not in _AIP_KINDS:
            raise AgentCoordinationError(f"unsupported AIP-v2 kind: {self.kind}")
        if type(self.task_id) is not int or isinstance(self.task_id, bool) or self.task_id < 1:
            raise AgentCoordinationError("task_id must be a positive integer")
        if type(self.delta_seq) is not int or isinstance(self.delta_seq, bool) or self.delta_seq < 0:
            raise AgentCoordinationError("delta_seq must be a non-negative integer")
        if self.status not in _TASK_BOARD_STATUSES:
            raise AgentCoordinationError(f"unsupported task status: {self.status}")
        if len(self.handles) > 64:
            raise AgentCoordinationError("handles exceed bound of 64")
        seen_handles: set[str] = set()
        for handle in self.handles:
            validate_compact_handle(handle)
            if handle in seen_handles:
                raise AgentCoordinationError("duplicate handle in CompactHandleEnvelope")
            seen_handles.add(handle)
        if len(self.touched_resources) > 256:
            raise AgentCoordinationError("touched_resources exceed bound of 256")
        for resource in self.touched_resources:
            _bounded_id(resource, "touched_resource", max_len=512)
        if len(self.metric_deltas) > 32:
            raise AgentCoordinationError("metric_deltas exceed bound of 32")
        seen_metrics: set[str] = set()
        for key, val in self.metric_deltas:
            _bounded_id(key, "metric key", max_len=64)
            if type(val) is not int or isinstance(val, bool):
                raise AgentCoordinationError("metric delta value must be int")
            if key in seen_metrics:
                raise AgentCoordinationError("duplicate metric key")
            seen_metrics.add(key)
        if type(self.goal_revision) is not int or self.goal_revision < 1:
            raise AgentCoordinationError("goal_revision must be >= 1")
        if type(self.goal_round) is not int or self.goal_round < 1:
            raise AgentCoordinationError("goal_round must be >= 1")
        if len(_canonical_json_bytes(self._payload())) > 8_192:
            raise AgentCoordinationError("CompactHandleEnvelope exceeds 8 KiB wire cap")

    def to_wire_bytes(self, *, hmac_key: bytes | None = None) -> bytes:
        self.validate()
        payload = self._payload()
        if hmac_key is not None:
            if type(hmac_key) is not bytes or not hmac_key:
                raise AgentCoordinationError("AIP-v2 hmac_key must be non-empty bytes")
            payload["sig_alg"] = "hmac-sha256"
            payload["sig"] = hmac.new(hmac_key, _canonical_json_bytes(self._payload()), hashlib.sha256).hexdigest()
        else:
            payload["sig"] = self.envelope_hash
        return _canonical_json_bytes(payload)

    @classmethod
    def from_wire_bytes(cls, raw: bytes, *, hmac_key: bytes | None = None) -> CompactHandleEnvelope:
        if type(raw) is not bytes or not raw or len(raw) > 8_192:
            raise AgentCoordinationError("AIP-v2 wire bytes are empty or exceed 8 KiB")
        try:
            raw_decoded: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentCoordinationError("AIP-v2 wire bytes are not valid UTF-8 JSON") from error
        if not isinstance(raw_decoded, dict):
            raise AgentCoordinationError("AIP-v2 schema mismatch")
        decoded = cast(dict[str, object], raw_decoded)
        if decoded.get("schema") != AIP_SCHEMA_V2:
            raise AgentCoordinationError("AIP-v2 schema mismatch")
        metrics_raw = decoded.get("m")
        if not isinstance(metrics_raw, dict):
            raise AgentCoordinationError("AIP-v2 metrics must be an object")
        metrics_map = cast(dict[object, object], metrics_raw)
        metric_pairs: list[tuple[str, int]] = []
        for k, v in metrics_map.items():
            if not isinstance(k, str) or type(v) is not int or isinstance(v, bool):
                raise AgentCoordinationError("AIP-v2 metric entries must map str -> int")
            metric_pairs.append((k, v))
        handles_raw = decoded.get("h")
        res_raw = decoded.get("res")
        if not isinstance(handles_raw, list) or not isinstance(res_raw, list):
            raise AgentCoordinationError("AIP-v2 handle/resource sequences must be lists")
        handles_list = cast(list[object], handles_raw)
        res_list = cast(list[object], res_raw)
        if not all(isinstance(item, str) for item in handles_list) or not all(
            isinstance(item, str) for item in res_list
        ):
            raise AgentCoordinationError("AIP-v2 handles and resources must be strings")
        grev = decoded.get("grev", 1)
        grnd = decoded.get("grnd", 1)
        envelope = cls(
            run_id=cast(str, decoded.get("r")),
            sender_agent=cast(str, decoded.get("s")),
            recipient_agent=cast(str, decoded.get("t")),
            kind=cast(str, decoded.get("k")),
            task_id=cast(int, decoded.get("tid")),
            delta_seq=cast(int, decoded.get("seq")),
            status=cast(str, decoded.get("st")),
            handles=tuple(cast(list[str], handles_list)),
            touched_resources=tuple(cast(list[str], res_list)),
            metric_deltas=tuple(sorted(metric_pairs)),
            goal_revision=grev if type(grev) is int and not isinstance(grev, bool) else -1,
            goal_round=grnd if type(grnd) is int and not isinstance(grnd, bool) else -1,
        )
        envelope.validate()
        sig_raw = decoded.get("sig")
        sig_alg = decoded.get("sig_alg")
        if not isinstance(sig_raw, str):
            raise AgentCoordinationError("AIP-v2 signature missing")
        if hmac_key is not None:
            if type(hmac_key) is not bytes or not hmac_key:
                raise AgentCoordinationError("AIP-v2 hmac_key must be non-empty bytes")
            if sig_alg != "hmac-sha256":
                raise AgentCoordinationError("AIP-v2 HMAC signature algorithm required")
            expected_sig = hmac.new(hmac_key, _canonical_json_bytes(envelope._payload()), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_raw, expected_sig):
                raise AgentCoordinationError("AIP-v2 HMAC signature mismatch")
        else:
            if sig_alg is not None:
                raise AgentCoordinationError("AIP-v2 HMAC key required to verify signed envelope")
            if not hmac.compare_digest(sig_raw, envelope.envelope_hash):
                raise AgentCoordinationError("AIP-v2 envelope_hash mismatch")
        return envelope


def estimate_wire_tokens(raw_utf8: bytes | str) -> int:
    """Conservative deterministic token estimate (~4 UTF-8 bytes/token ceiling)."""

    data = raw_utf8.encode("utf-8") if isinstance(raw_utf8, str) else raw_utf8
    return max(1, (len(data) + 3) // 4)


def measure_protocol_token_compression(
    prose_payload: str,
    compact_envelope: CompactHandleEnvelope,
) -> dict[str, int | float]:
    """Compare UTF-8 wire bytes and estimated tokens between prose handoff and AIP-v2."""

    prose_bytes = len(prose_payload.encode("utf-8"))
    compact_bytes = len(compact_envelope.to_wire_bytes())
    prose_tokens = estimate_wire_tokens(prose_payload)
    compact_tokens = estimate_wire_tokens(compact_envelope.to_wire_bytes())
    saved_tokens = max(0, prose_tokens - compact_tokens)
    reduction_ratio = (saved_tokens / prose_tokens) if prose_tokens > 0 else 0.0
    return {
        "prose_bytes": prose_bytes,
        "compact_bytes": compact_bytes,
        "prose_tokens_est": prose_tokens,
        "compact_tokens_est": compact_tokens,
        "saved_tokens_est": saved_tokens,
        "token_reduction_ratio": round(reduction_ratio, 6),
    }


@dataclass(frozen=True, slots=True)
class TeamTaskRecord:
    """Immutable snapshot of one shared task on the multi-agent delta board."""

    task_id: int
    status: str
    owner_agent: str | None
    blocked_by: tuple[int, ...]
    blocks: tuple[int, ...]
    exclusive_resources: tuple[str, ...]
    compact_handles: tuple[str, ...]
    updated_seq: int
    lease_fence_token: int = 0
    lease_expires_at_ms: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "owner_agent": self.owner_agent,
            "blocked_by": list(self.blocked_by),
            "blocks": list(self.blocks),
            "exclusive_resources": list(self.exclusive_resources),
            "compact_handles": list(self.compact_handles),
            "updated_seq": self.updated_seq,
            "lease_fence_token": self.lease_fence_token,
            "lease_expires_at_ms": self.lease_expires_at_ms,
        }


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class TeamBoardDelta:
    """Incremental delta returned to polling/subscribing team agents."""

    latest_seq: int
    oldest_retained_seq: int
    resync_required: bool
    changed_tasks: tuple[TeamTaskRecord, ...]


class SharedTaskDeltaBoard:
    """Thread-safe shared task board with hierarchical lease conflict checks & delta sync."""

    def __init__(self, *, max_delta_log: int = 2_048) -> None:
        if type(max_delta_log) is not int or not 16 <= max_delta_log <= 65_536:
            raise AgentCoordinationError("max_delta_log must be within [16, 65536]")
        self._max_delta_log = max_delta_log
        self._lock = threading.RLock()
        self._seq = 0
        self._tasks: dict[int, TeamTaskRecord] = {}
        self._history: deque[tuple[int, int]] = deque()

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._seq

    def _has_dependency_path_locked(self, start_id: int, target_id: int) -> bool:
        visited: set[int] = set()
        stack = [start_id]
        while stack:
            cur = stack.pop()
            if cur == target_id:
                return True
            if cur in visited:
                continue
            visited.add(cur)
            rec = self._tasks.get(cur)
            if rec is not None:
                stack.extend(rec.blocked_by)
        return False

    def _record_deltas_locked(self, seq: int, task_ids: Iterable[int]) -> None:
        seen_ids: set[int] = set()
        for tid in task_ids:
            if tid not in seen_ids:
                seen_ids.add(tid)
                self._history.append((seq, tid))
        while len(self._history) > self._max_delta_log:
            oldest_seq = self._history[0][0]
            if oldest_seq == seq and len(seen_ids) <= self._max_delta_log:
                break
            while self._history and self._history[0][0] == oldest_seq:
                self._history.popleft()
            if oldest_seq == seq:
                break

    def _expire_stale_leases_locked(self, current_ms: int) -> list[int]:
        expired_ids = [
            tid
            for tid, rec in sorted(self._tasks.items())
            if rec.status == "in_progress"
            and rec.lease_expires_at_ms is not None
            and current_ms >= rec.lease_expires_at_ms
        ]
        if not expired_ids:
            return []
        self._seq += 1
        expire_seq = self._seq
        for tid in expired_ids:
            rec = self._tasks[tid]
            self._tasks[tid] = TeamTaskRecord(
                task_id=rec.task_id,
                status="pending",
                owner_agent=None,
                blocked_by=rec.blocked_by,
                blocks=rec.blocks,
                exclusive_resources=rec.exclusive_resources,
                compact_handles=rec.compact_handles,
                updated_seq=expire_seq,
                lease_fence_token=rec.lease_fence_token + 1,
                lease_expires_at_ms=None,
            )
        self._record_deltas_locked(expire_seq, expired_ids)
        return expired_ids

    def upsert_task(
        self,
        task_id: int,
        *,
        status: str = "pending",
        owner_agent: str | None = None,
        blocked_by: Iterable[int] = (),
        exclusive_resources: Iterable[str] = (),
        compact_handles: Iterable[str] = (),
        lease_ttl_ms: int | None = None,
        now_ms: int | None = None,
        expected_fence_token: int | None = None,
    ) -> TeamTaskRecord:
        """Create or update a task, enforcing DAG acyclicity and hierarchical resource leases."""

        if type(task_id) is not int or isinstance(task_id, bool) or task_id < 1:
            raise AgentCoordinationError("task_id must be a positive integer")
        if status not in _TASK_BOARD_STATUSES:
            raise AgentCoordinationError(f"unsupported task board status: {status}")
        if lease_ttl_ms is not None and (
            type(lease_ttl_ms) is not int or isinstance(lease_ttl_ms, bool) or lease_ttl_ms < 1
        ):
            raise AgentCoordinationError("lease_ttl_ms must be a positive integer")
        if now_ms is not None and (type(now_ms) is not int or isinstance(now_ms, bool) or now_ms < 0):
            raise AgentCoordinationError("now_ms must be a non-negative integer")
        current_ms = _now_epoch_ms() if now_ms is None else now_ms
        owner = None if owner_agent is None else _bounded_id(owner_agent, "owner_agent")
        deps = tuple(sorted(set(blocked_by)))
        for dep in deps:
            if type(dep) is not int or isinstance(dep, bool) or dep < 1 or dep == task_id:
                raise AgentCoordinationError("blocked_by must contain distinct positive task IDs")
        resources = tuple(sorted({_bounded_id(r, "exclusive_resource", max_len=512) for r in exclusive_resources}))
        handles = tuple(sorted({validate_compact_handle(h) for h in compact_handles}))

        with self._lock:
            if len(self._tasks) >= 1_024 and task_id not in self._tasks:
                raise AgentCoordinationError("SharedTaskDeltaBoard capacity (1024 tasks) exceeded")
            self._expire_stale_leases_locked(current_ms)
            existing = self._tasks.get(task_id)
            if expected_fence_token is not None and (
                existing is None or existing.lease_fence_token != expected_fence_token
            ):
                raise AgentCoordinationError(f"stale lease fence token for task {task_id}")
            for dep in deps:
                if self._has_dependency_path_locked(dep, task_id):
                    raise AgentCoordinationError("task dependency update introduces a cycle")
            if status == "in_progress":
                if owner is None:
                    raise AgentCoordinationError("in_progress task requires an owner_agent")
                unfinished_blockers = [
                    dep for dep in deps if (dep_rec := self._tasks.get(dep)) is None or dep_rec.status != "completed"
                ]
                if unfinished_blockers:
                    raise AgentCoordinationError(f"task {task_id} cannot start while blocked_by {unfinished_blockers}")
                for other_id, other in self._tasks.items():
                    if other_id == task_id or other.status != "in_progress":
                        continue
                    for r_self in resources:
                        for r_other in other.exclusive_resources:
                            if resource_keys_conflict(r_self, r_other):
                                raise AgentCoordinationError(
                                    f"hierarchical lease conflict between task {task_id} ({r_self}) "
                                    f"and active task {other_id} ({r_other})"
                                )

            blocks = tuple(
                sorted({tid for tid, rec in self._tasks.items() if tid != task_id and task_id in rec.blocked_by})
            )
            self._seq += 1
            seq = self._seq
            next_fence = (
                1
                if existing is None
                else (existing.lease_fence_token + 1 if status == "in_progress" else existing.lease_fence_token)
            )
            expires_at = current_ms + lease_ttl_ms if (status == "in_progress" and lease_ttl_ms is not None) else None
            record = TeamTaskRecord(
                task_id=task_id,
                status=status,
                owner_agent=owner,
                blocked_by=deps,
                blocks=blocks,
                exclusive_resources=resources,
                compact_handles=handles,
                updated_seq=seq,
                lease_fence_token=next_fence,
                lease_expires_at_ms=expires_at,
            )
            self._tasks[task_id] = record
            touched_ids = [task_id]

            # Synchronize reverse `blocks` edges on upstream tasks with the same single atomic seq.
            previous_deps: set[int] = set(existing.blocked_by) if existing is not None else set()
            current_deps: set[int] = set(deps)
            for added_dep in sorted(current_deps - previous_deps):
                upstream = self._tasks.get(added_dep)
                if upstream is not None and task_id not in upstream.blocks:
                    self._tasks[added_dep] = TeamTaskRecord(
                        task_id=upstream.task_id,
                        status=upstream.status,
                        owner_agent=upstream.owner_agent,
                        blocked_by=upstream.blocked_by,
                        blocks=tuple(sorted((*upstream.blocks, task_id))),
                        exclusive_resources=upstream.exclusive_resources,
                        compact_handles=upstream.compact_handles,
                        updated_seq=seq,
                        lease_fence_token=upstream.lease_fence_token,
                        lease_expires_at_ms=upstream.lease_expires_at_ms,
                    )
                    touched_ids.append(added_dep)
            for removed_dep in sorted(previous_deps - current_deps):
                upstream = self._tasks.get(removed_dep)
                if upstream is not None and task_id in upstream.blocks:
                    self._tasks[removed_dep] = TeamTaskRecord(
                        task_id=upstream.task_id,
                        status=upstream.status,
                        owner_agent=upstream.owner_agent,
                        blocked_by=upstream.blocked_by,
                        blocks=tuple(item for item in upstream.blocks if item != task_id),
                        exclusive_resources=upstream.exclusive_resources,
                        compact_handles=upstream.compact_handles,
                        updated_seq=seq,
                        lease_fence_token=upstream.lease_fence_token,
                        lease_expires_at_ms=upstream.lease_expires_at_ms,
                    )
                    touched_ids.append(removed_dep)
            self._record_deltas_locked(seq, touched_ids)
            return self._tasks[task_id]

    def heartbeat_task(
        self,
        task_id: int,
        *,
        owner_agent: str,
        lease_fence_token: int,
        extend_ttl_ms: int,
        now_ms: int | None = None,
    ) -> TeamTaskRecord:
        """Renew an active lease only if the caller holds the current fence token and unexpired lease."""

        owner = _bounded_id(owner_agent, "owner_agent")
        if type(extend_ttl_ms) is not int or isinstance(extend_ttl_ms, bool) or extend_ttl_ms < 1:
            raise AgentCoordinationError("extend_ttl_ms must be a positive integer")
        current_ms = _now_epoch_ms() if now_ms is None else now_ms
        with self._lock:
            self._expire_stale_leases_locked(current_ms)
            rec = self._tasks.get(task_id)
            if rec is None or rec.status != "in_progress":
                raise AgentCoordinationError(f"task {task_id} does not hold an active lease")
            if rec.owner_agent != owner or rec.lease_fence_token != lease_fence_token:
                raise AgentCoordinationError("heartbeat owner_agent or fence_token mismatch")
            self._seq += 1
            seq = self._seq
            updated = TeamTaskRecord(
                task_id=rec.task_id,
                status=rec.status,
                owner_agent=rec.owner_agent,
                blocked_by=rec.blocked_by,
                blocks=rec.blocks,
                exclusive_resources=rec.exclusive_resources,
                compact_handles=rec.compact_handles,
                updated_seq=seq,
                lease_fence_token=rec.lease_fence_token,
                lease_expires_at_ms=current_ms + extend_ttl_ms,
            )
            self._tasks[task_id] = updated
            self._record_deltas_locked(seq, (task_id,))
            return updated

    def rollback_task_cascade(
        self,
        task_id: int,
        *,
        status: Literal["failed", "pending"] = "failed",
    ) -> tuple[TeamTaskRecord, ...]:
        """Roll back `task_id` and transition uncompleted downstream dependents to `blocked` atomically."""

        if status not in {"failed", "pending"}:
            raise AgentCoordinationError("rollback status must be 'failed' or 'pending'")
        with self._lock:
            root = self._tasks.get(task_id)
            if root is None:
                raise AgentCoordinationError(f"unknown task_id {task_id}")
            self._seq += 1
            seq = self._seq
            self._tasks[task_id] = TeamTaskRecord(
                task_id=root.task_id,
                status=status,
                owner_agent=None,
                blocked_by=root.blocked_by,
                blocks=root.blocks,
                exclusive_resources=root.exclusive_resources,
                compact_handles=root.compact_handles,
                updated_seq=seq,
                lease_fence_token=root.lease_fence_token + 1,
                lease_expires_at_ms=None,
            )
            changed: list[TeamTaskRecord] = [self._tasks[task_id]]
            visited: set[int] = {task_id}
            queue = deque(root.blocks)
            while queue:
                dep_id = queue.popleft()
                if dep_id in visited:
                    continue
                visited.add(dep_id)
                dep_rec = self._tasks.get(dep_id)
                if dep_rec is None:
                    continue
                queue.extend(dep_rec.blocks)
                if dep_rec.status != "completed":
                    updated = TeamTaskRecord(
                        task_id=dep_rec.task_id,
                        status="blocked",
                        owner_agent=None,
                        blocked_by=dep_rec.blocked_by,
                        blocks=dep_rec.blocks,
                        exclusive_resources=dep_rec.exclusive_resources,
                        compact_handles=dep_rec.compact_handles,
                        updated_seq=seq,
                        lease_fence_token=dep_rec.lease_fence_token + 1,
                        lease_expires_at_ms=None,
                    )
                    self._tasks[dep_id] = updated
                    changed.append(updated)
            self._record_deltas_locked(seq, (rec.task_id for rec in changed))
            return tuple(changed)

    def _record_delta_locked(self, seq: int, task_id: int) -> None:
        self._record_deltas_locked(seq, (task_id,))

    def delta_since(self, since_seq: int = 0) -> TeamBoardDelta:
        """Return only task records modified strictly after `since_seq`."""

        if type(since_seq) is not int or isinstance(since_seq, bool) or since_seq < 0:
            raise AgentCoordinationError("since_seq must be a non-negative integer")
        with self._lock:
            oldest = self._history[0][0] if self._history else self._seq
            resync = bool(self._history) and since_seq < oldest - 1
            if resync:
                changed = tuple(self._tasks[tid] for tid in sorted(self._tasks))
            else:
                touched_ids: list[int] = []
                seen: set[int] = set()
                for seq, tid in self._history:
                    if seq > since_seq and tid not in seen:
                        seen.add(tid)
                        touched_ids.append(tid)
                changed = tuple(self._tasks[tid] for tid in touched_ids if tid in self._tasks)
            return TeamBoardDelta(
                latest_seq=self._seq,
                oldest_retained_seq=oldest,
                resync_required=resync,
                changed_tasks=changed,
            )


@dataclass(frozen=True, slots=True)
class GoalRoundAnchor:
    """Immutable `<goal_round>` anti-drift anchor injected at the top of agent turns."""

    goal_id: str
    revision: int
    round_index: int
    objective: str
    invariants: tuple[str, ...]
    active_obligation_ids: tuple[str, ...] = ()

    @property
    def objective_digest(self) -> str:
        return _sha256_hex({"objective": self.objective, "invariants": list(self.invariants)})

    def render_tag(self) -> str:
        payload = {
            "schema": GOAL_ROUND_SCHEMA_V1,
            "goal_id": self.goal_id,
            "revision": self.revision,
            "round": self.round_index,
            "objective_digest": self.objective_digest,
            "objective": self.objective,
            "invariants": list(self.invariants),
            "active_obligations": list(self.active_obligation_ids),
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"<goal_round>{body}</goal_round>"


class GoalRoundDriver:
    """Monotonic `(goal_id, revision, round)` state machine preventing long-horizon goal drift."""

    def __init__(
        self,
        goal_id: str,
        objective: str,
        *,
        invariants: Iterable[str] = (),
        active_obligation_ids: Iterable[str] = (),
    ) -> None:
        gid = _bounded_id(goal_id, "goal_id")
        if type(objective) is not str or not objective.strip() or len(objective) > 4_096:
            raise AgentCoordinationError("goal objective must be bounded non-empty text")
        inv = tuple(_bounded_text(item, "goal invariant", max_len=512) for item in invariants)
        obls = tuple(_bounded_id(item, "obligation_id") for item in active_obligation_ids)
        self._lock = threading.RLock()
        self._anchor = GoalRoundAnchor(
            goal_id=gid,
            revision=1,
            round_index=1,
            objective=objective.strip(),
            invariants=inv,
            active_obligation_ids=obls,
        )

    @property
    def current_anchor(self) -> GoalRoundAnchor:
        with self._lock:
            return self._anchor

    def advance_round(self, *, active_obligation_ids: Iterable[str] | None = None) -> GoalRoundAnchor:
        with self._lock:
            obls = (
                self._anchor.active_obligation_ids
                if active_obligation_ids is None
                else tuple(_bounded_id(item, "obligation_id") for item in active_obligation_ids)
            )
            self._anchor = GoalRoundAnchor(
                goal_id=self._anchor.goal_id,
                revision=self._anchor.revision,
                round_index=self._anchor.round_index + 1,
                objective=self._anchor.objective,
                invariants=self._anchor.invariants,
                active_obligation_ids=obls,
            )
            return self._anchor

    def revise_goal(
        self,
        new_objective: str,
        *,
        invariants: Iterable[str] | None = None,
        active_obligation_ids: Iterable[str] | None = None,
    ) -> GoalRoundAnchor:
        if type(new_objective) is not str or not new_objective.strip() or len(new_objective) > 4_096:
            raise AgentCoordinationError("new_objective must be bounded non-empty text")
        with self._lock:
            inv = (
                self._anchor.invariants
                if invariants is None
                else tuple(_bounded_text(item, "goal invariant", max_len=512) for item in invariants)
            )
            obls = (
                self._anchor.active_obligation_ids
                if active_obligation_ids is None
                else tuple(_bounded_id(item, "obligation_id") for item in active_obligation_ids)
            )
            self._anchor = GoalRoundAnchor(
                goal_id=self._anchor.goal_id,
                revision=self._anchor.revision + 1,
                round_index=1,
                objective=new_objective.strip(),
                invariants=inv,
                active_obligation_ids=obls,
            )
            return self._anchor

    update_objective = revise_goal

    def validate_agent_turn(
        self,
        *,
        goal_id: str,
        revision: int,
        round_index: int,
        objective_digest: str | None = None,
    ) -> None:
        """Fail closed when a worker reports work against a stale or drifted goal revision."""

        with self._lock:
            anchor = self._anchor
        if goal_id != anchor.goal_id:
            raise AgentCoordinationError(f"goal_id mismatch: expected {anchor.goal_id}, got {goal_id}")
        if revision != anchor.revision:
            raise AgentCoordinationError(f"stale or invalid goal revision: active={anchor.revision}, got={revision}")
        if round_index < 1 or round_index > anchor.round_index:
            raise AgentCoordinationError(f"invalid goal round: active={anchor.round_index}, got={round_index}")
        if objective_digest is not None and objective_digest != anchor.objective_digest:
            raise AgentCoordinationError("objective_digest drifted from active <goal_round> anchor")


def _bounded_text(value: object, name: str, *, max_len: int = 2_048) -> str:
    if type(value) is not str or not value.strip() or "\x00" in value or len(value) > max_len:
        raise AgentCoordinationError(f"{name} must be bounded non-empty text")
    return value.strip()


def _utf8_prefix_end(raw: bytes, max_bytes: int) -> int:
    if len(raw) <= max_bytes:
        return len(raw)
    idx = max_bytes
    while idx > 0 and (raw[idx] & 0b1100_0000) == 0b1000_0000:
        idx -= 1
    return idx


def _utf8_suffix_start(raw: bytes, max_bytes: int) -> int:
    if len(raw) <= max_bytes:
        return 0
    idx = len(raw) - max_bytes
    while idx < len(raw) and (raw[idx] & 0b1100_0000) == 0b1000_0000:
        idx += 1
    return idx


@dataclass(frozen=True, slots=True)
class PrunedToolResult:
    """Compact tool output preserving exact lossless content behind an `@art:<hex>` handle."""

    handle: str
    original_bytes: int
    original_lines: int
    pruned: bool
    compact_text: str


class ToolResultPruner:
    """UTF-8 char-boundary-safe head+tail tool output pruner with lossless handle store."""

    def __init__(self, *, max_inline_bytes: int = 2_048, head_bytes: int = 1_024, tail_bytes: int = 512) -> None:
        if max_inline_bytes < 128 or head_bytes < 32 or tail_bytes < 32:
            raise AgentCoordinationError("invalid ToolResultPruner byte thresholds")
        if head_bytes + tail_bytes >= max_inline_bytes:
            raise AgentCoordinationError("head_bytes + tail_bytes must be smaller than max_inline_bytes")
        self.max_inline_bytes = max_inline_bytes
        self.head_bytes = head_bytes
        self.tail_bytes = tail_bytes
        self._lock = threading.RLock()
        self._store: dict[str, bytes] = {}

    def prune(self, output: str) -> PrunedToolResult:
        if type(output) is not str:
            raise AgentCoordinationError("tool output must be str")
        raw = output.encode("utf-8")
        line_count = output.count("\n") + (1 if output else 0)
        handle = make_compact_handle("art", raw)
        with self._lock:
            self._store[handle] = raw
        if len(raw) <= self.max_inline_bytes:
            return PrunedToolResult(
                handle=handle,
                original_bytes=len(raw),
                original_lines=line_count,
                pruned=False,
                compact_text=output,
            )
        head_end = _utf8_prefix_end(raw, self.head_bytes)
        tail_start = max(head_end, _utf8_suffix_start(raw, self.tail_bytes))
        head_str = raw[:head_end].decode("utf-8")
        tail_str = raw[tail_start:].decode("utf-8")
        omitted_bytes = tail_start - head_end
        compact_text = (
            f"{head_str}\n"
            f"... [pruned {omitted_bytes} UTF-8 bytes across {line_count} lines; full artifact={handle}] ...\n"
            f"{tail_str}"
        )
        return PrunedToolResult(
            handle=handle,
            original_bytes=len(raw),
            original_lines=line_count,
            pruned=True,
            compact_text=compact_text,
        )

    def resolve(self, handle: str) -> str:
        validate_compact_handle(handle)
        with self._lock:
            raw = self._store.get(handle)
        if raw is None:
            raise KeyError(f"unknown pruned tool handle: {handle}")
        return raw.decode("utf-8")

    recall = resolve


def _extract_tool_use_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
        for call in cast(Sequence[object], tool_calls):
            if isinstance(call, Mapping):
                call_map = cast(Mapping[object, object], call)
                call_id = call_map.get("id")
                if isinstance(call_id, str):
                    ids.append(call_id)
    content = message.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        for block in cast(Sequence[object], content):
            if isinstance(block, Mapping):
                block_map = cast(Mapping[object, object], block)
                if block_map.get("type") == "tool_use":
                    block_id = block_map.get("id")
                    if isinstance(block_id, str):
                        ids.append(block_id)
    return tuple(ids)


def _extract_tool_result_ids(message: Mapping[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    if message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
        ids.append(str(message["tool_call_id"]))
    content = message.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        for block in cast(Sequence[object], content):
            if isinstance(block, Mapping):
                block_map = cast(Mapping[object, object], block)
                if block_map.get("type") == "tool_result":
                    tool_use_id = block_map.get("tool_use_id")
                    if isinstance(tool_use_id, str):
                        ids.append(tool_use_id)
    return tuple(ids)


def verify_tool_pairing_balanced(messages: Sequence[Mapping[str, Any]]) -> bool:
    """Return True iff every `tool_use`/`tool_calls` ID is matched by a subsequent `tool_result`/`tool` ID and vice versa."""

    open_ids: set[str] = set()
    for msg in messages:
        res_ids = _extract_tool_result_ids(msg)
        for rid in res_ids:
            if rid not in open_ids:
                return False
            open_ids.remove(rid)
        use_ids = _extract_tool_use_ids(msg)
        for uid in use_ids:
            if uid in open_ids:
                return False
            open_ids.add(uid)
    return not open_ids


def compact_conversation_history(
    messages: Sequence[Mapping[str, Any]],
    *,
    keep_recent_messages: int = 6,
    goal_anchor: GoalRoundAnchor | None = None,
) -> list[dict[str, Any]]:
    """Compact older turns into a fold message without breaking `toolPairingBalanced` or prefix KV-cache.

    Any immutable leading system-prompt messages (`messages[:prefix_end]`) stay bit-for-bit intact
    at the front so prompt KV caches remain reusable; `<goal_round>` updates append at the tail.
    """

    if keep_recent_messages < 1:
        raise AgentCoordinationError("keep_recent_messages must be >= 1")
    if not verify_tool_pairing_balanced(messages):
        raise AgentCoordinationError("input conversation history violates toolPairingBalanced")
    total = len(messages)
    prefix_end = 0
    while prefix_end < total and messages[prefix_end].get("role") == "system":
        prefix_end += 1

    if total - prefix_end <= keep_recent_messages:
        preserved = [dict(m) for m in messages]
        if goal_anchor is not None:
            preserved.append({"role": "system", "content": goal_anchor.render_tag()})
        return preserved

    # Walk cut_index backward until messages[cut_index:] has zero orphaned tool_results/tool_uses.
    cut_index = max(prefix_end, total - keep_recent_messages)
    while cut_index > prefix_end and not verify_tool_pairing_balanced(messages[cut_index:]):
        cut_index -= 1

    if cut_index <= prefix_end:
        preserved = [dict(m) for m in messages]
        if goal_anchor is not None:
            preserved.append({"role": "system", "content": goal_anchor.render_tag()})
        return preserved

    immutable_prefix = [dict(m) for m in messages[:prefix_end]]
    folded_slice = [dict(m) for m in messages[prefix_end:cut_index]]
    fold_handle = make_compact_handle("fold", {"turns": len(folded_slice), "digest": _sha256_hex(folded_slice)})
    tool_ids_folded = [uid for m in folded_slice for uid in _extract_tool_use_ids(m)]
    fold_summary = {
        "role": "system",
        "content": json.dumps(
            {
                "schema": "aegis-compacted-fold-v1",
                "fold_handle": fold_handle,
                "compacted_message_count": len(folded_slice),
                "compacted_tool_call_count": len(tool_ids_folded),
                "digest": _sha256_hex(folded_slice),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    result = [
        *immutable_prefix,
        fold_summary,
        *[dict(m) for m in messages[cut_index:]],
    ]
    if goal_anchor is not None:
        result.append({"role": "system", "content": goal_anchor.render_tag()})
    if not verify_tool_pairing_balanced(result):
        raise AgentCoordinationError("compacted history violates toolPairingBalanced")
    return result


def strip_persuasive_cot(text: str) -> str:
    """Remove `<thinking>`/`<rationale>` blocks and subjective confidence self-scores."""

    cleaned = _COT_BLOCK_RE.sub("", text)
    cleaned = _SUBJECTIVE_SCORE_RE.sub("[REDACTED_SELF_SCORE]", cleaned)
    return cleaned.strip()


@dataclass(frozen=True, slots=True)
class BlindedAuditPacket:
    """Context-isolated audit packet stripped of author identity and persuasive CoT."""

    submission_digest: str
    spec_digest: str
    diff_hunks: tuple[str, ...]
    evidence_handles: tuple[str, ...]
    sanitized_claims: tuple[str, ...]


class AdversarialAuditGate:
    """Blinds author submissions before dispatching to independent challenger agents."""

    @staticmethod
    def blind_submission(
        *,
        spec_text: str,
        diff_hunks: Sequence[str],
        evidence_handles: Sequence[str],
        raw_claims: Sequence[str],
        author_agent_id: str | None = None,
    ) -> BlindedAuditPacket:
        spec_clean = _bounded_text(spec_text, "spec_text", max_len=32_768)
        hunks = tuple(_bounded_text(h, "diff_hunk", max_len=32_768) for h in diff_hunks)
        handles = tuple(validate_compact_handle(h) for h in evidence_handles)
        sanitized: list[str] = []
        for claim in raw_claims:
            scrubbed = strip_persuasive_cot(_bounded_text(claim, "raw_claim", max_len=4_096))
            if author_agent_id and author_agent_id.strip():
                scrubbed = scrubbed.replace(author_agent_id.strip(), "[BLINDED_AUTHOR]")
            if scrubbed:
                sanitized.append(scrubbed)
        spec_digest = _sha256_hex(spec_clean)
        submission_digest = _sha256_hex(
            {
                "spec_digest": spec_digest,
                "diff_hunks": list(hunks),
                "evidence_handles": list(handles),
                "sanitized_claims": sanitized,
            }
        )
        return BlindedAuditPacket(
            submission_digest=submission_digest,
            spec_digest=spec_digest,
            diff_hunks=hunks,
            evidence_handles=handles,
            sanitized_claims=tuple(sanitized),
        )


@dataclass(frozen=True, slots=True)
class ChallengeFinding:
    """Structured finding raised by an adversarial challenger."""

    severity: Literal["FATAL_COUNTEREXAMPLE", "UNPROVEN_CLAIM", "MINOR_RISK"]
    description: str
    reproducible_check: str = ""

    def validate(self) -> None:
        if self.severity not in {"FATAL_COUNTEREXAMPLE", "UNPROVEN_CLAIM", "MINOR_RISK"}:
            raise AgentCoordinationError(f"invalid finding severity: {self.severity}")
        _bounded_text(self.description, "finding description", max_len=2_048)
        if self.severity == "FATAL_COUNTEREXAMPLE" and not self.reproducible_check.strip():
            raise AgentCoordinationError("FATAL_COUNTEREXAMPLE requires a non-empty reproducible_check")


@dataclass(frozen=True, slots=True)
class AuditVote:
    """Single independent auditor verdict over a `BlindedAuditPacket`."""

    auditor_id: str
    submission_digest: str
    approved: bool
    verified_receipt_ids: tuple[str, ...] = ()
    findings: tuple[ChallengeFinding, ...] = ()
    verified_receipt_digests: tuple[tuple[str, str], ...] = ()

    def validate(self) -> None:
        _bounded_id(self.auditor_id, "auditor_id")
        if len(self.submission_digest) != 64 or any(c not in "0123456789abcdef" for c in self.submission_digest):
            raise AgentCoordinationError("submission_digest must be 64-char SHA-256 hex")
        if type(self.approved) is not bool:
            raise AgentCoordinationError("approved must be bool")
        for rid in self.verified_receipt_ids:
            _bounded_id(rid, "verified_receipt_id")
        for rid, digest in self.verified_receipt_digests:
            _bounded_id(rid, "verified_receipt_digest_id")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise AgentCoordinationError("verified receipt output_digest must be 64-char hex")
        for finding in self.findings:
            finding.validate()


@dataclass(frozen=True, slots=True)
class AuditConsensusVerdict:
    """Deterministic fail-closed outcome from `ReconstructionObligationLedger`."""

    status: Literal[
        "QUORUM_ADMITTED",
        "VETOED_COUNTEREXAMPLE",
        "UNVERIFIED_OBLIGATIONS",
        "QUORUM_REJECTED",
    ]
    admitted: bool
    total_auditors: int
    required_quorum: int
    approved_votes: int
    veto_findings: tuple[ChallengeFinding, ...]
    missing_obligation_ids: tuple[str, ...]
    ledger_digest: str


def byzantine_quorum_threshold(total_auditors: int) -> int:
    """Strict >2/3 supermajority (`floor(2N/3) + 1`) matching `sac::byzantine_witness_threshold`."""

    if type(total_auditors) is not int or isinstance(total_auditors, bool) or total_auditors < 1:
        raise AgentCoordinationError("total_auditors must be >= 1")
    return (2 * total_auditors) // 3 + 1


@dataclass(frozen=True, slots=True)
class ObligationSpec:
    obligation_id: str
    description: str
    required_receipt_ids: tuple[str, ...]


class ReconstructionObligationLedger:
    """Fail-closed obligation ledger requiring receipt proofs, zero fatal counterexamples, and `floor(2N/3)+1` quorum."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._obligations: dict[str, ObligationSpec] = {}
        self._verified_receipts: dict[str, str] = {}

    def register_obligation(self, description: str, *, required_receipt_ids: Iterable[str]) -> str:
        desc = _bounded_text(description, "obligation description", max_len=1_024)
        receipts = tuple(sorted({_bounded_id(r, "required_receipt_id") for r in required_receipt_ids}))
        if not receipts:
            raise AgentCoordinationError("obligation must require at least one verification receipt")
        oid = "rol_" + _sha256_hex({"description": desc, "receipts": list(receipts)})[:24]
        with self._lock:
            self._obligations[oid] = ObligationSpec(
                obligation_id=oid,
                description=desc,
                required_receipt_ids=receipts,
            )
        return oid

    def record_verified_receipt(self, receipt_id: str, *, output_digest: str) -> None:
        rid = _bounded_id(receipt_id, "receipt_id")
        if len(output_digest) != 64 or any(c not in "0123456789abcdef" for c in output_digest):
            raise AgentCoordinationError("output_digest must be lowercase 64-char hex")
        with self._lock:
            self._verified_receipts[rid] = output_digest

    def evaluate_consensus(
        self,
        *,
        packet: BlindedAuditPacket,
        votes: Sequence[AuditVote],
        total_auditors: int,
    ) -> AuditConsensusVerdict:
        required = byzantine_quorum_threshold(total_auditors)
        if len(votes) > total_auditors:
            raise AgentCoordinationError("vote count exceeds total_auditors")
        seen_auditors: set[str] = set()
        vetoes: list[ChallengeFinding] = []
        approved_count = 0
        receipt_attestations: dict[str, int] = {}

        for vote in votes:
            vote.validate()
            if vote.submission_digest != packet.submission_digest:
                raise AgentCoordinationError("vote submission_digest does not match BlindedAuditPacket")
            if vote.auditor_id in seen_auditors:
                raise AgentCoordinationError(f"duplicate vote from auditor {vote.auditor_id}")
            seen_auditors.add(vote.auditor_id)
            vetoes.extend(finding for finding in vote.findings if finding.severity == "FATAL_COUNTEREXAMPLE")
            if vote.approved and not any(f.severity == "FATAL_COUNTEREXAMPLE" for f in vote.findings):
                approved_count += 1
            for rid in set(vote.verified_receipt_ids):
                receipt_attestations[rid] = receipt_attestations.get(rid, 0) + 1

        with self._lock:
            obligations = dict(self._obligations)
            verified_receipts = dict(self._verified_receipts)

        missing_obligations: list[str] = []
        for oid, obl in sorted(obligations.items()):
            satisfied = all(
                rid in verified_receipts and receipt_attestations.get(rid, 0) >= required
                for rid in obl.required_receipt_ids
            )
            if not satisfied:
                missing_obligations.append(oid)

        ledger_digest = _sha256_hex(
            {
                "schema": AUDIT_LEDGER_SCHEMA_V1,
                "submission_digest": packet.submission_digest,
                "total_auditors": total_auditors,
                "required_quorum": required,
                "approved_votes": approved_count,
                "veto_count": len(vetoes),
                "missing_obligations": missing_obligations,
            }
        )

        if vetoes:
            return AuditConsensusVerdict(
                status="VETOED_COUNTEREXAMPLE",
                admitted=False,
                total_auditors=total_auditors,
                required_quorum=required,
                approved_votes=approved_count,
                veto_findings=tuple(vetoes),
                missing_obligation_ids=tuple(missing_obligations),
                ledger_digest=ledger_digest,
            )
        if missing_obligations:
            return AuditConsensusVerdict(
                status="UNVERIFIED_OBLIGATIONS",
                admitted=False,
                total_auditors=total_auditors,
                required_quorum=required,
                approved_votes=approved_count,
                veto_findings=(),
                missing_obligation_ids=tuple(missing_obligations),
                ledger_digest=ledger_digest,
            )
        if approved_count < required:
            return AuditConsensusVerdict(
                status="QUORUM_REJECTED",
                admitted=False,
                total_auditors=total_auditors,
                required_quorum=required,
                approved_votes=approved_count,
                veto_findings=(),
                missing_obligation_ids=(),
                ledger_digest=ledger_digest,
            )
        return AuditConsensusVerdict(
            status="QUORUM_ADMITTED",
            admitted=True,
            total_auditors=total_auditors,
            required_quorum=required,
            approved_votes=approved_count,
            veto_findings=(),
            missing_obligation_ids=(),
            ledger_digest=ledger_digest,
        )


__all__ = [
    "AIP_SCHEMA_V2",
    "AUDIT_LEDGER_SCHEMA_V1",
    "GOAL_ROUND_SCHEMA_V1",
    "AdversarialAuditGate",
    "AuditConsensusVerdict",
    "AuditVote",
    "BlindedAuditPacket",
    "ChallengeFinding",
    "CompactHandleEnvelope",
    "GoalRoundAnchor",
    "GoalRoundDriver",
    "ObligationSpec",
    "PrunedToolResult",
    "ReconstructionObligationLedger",
    "SharedTaskDeltaBoard",
    "TeamBoardDelta",
    "TeamTaskRecord",
    "ToolResultPruner",
    "byzantine_quorum_threshold",
    "compact_conversation_history",
    "estimate_wire_tokens",
    "make_compact_handle",
    "measure_protocol_token_compression",
    "strip_persuasive_cot",
    "validate_compact_handle",
    "verify_tool_pairing_balanced",
]
