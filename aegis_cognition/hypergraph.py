"""Research-grade Memory Hypergraph & AST Workspace Graph Engine for AEGIS-COGNITION.

Provides a unified Python API backed by native Rust (`aegis_nerve.aegis_hypergraph_analyze_json`)
when compiled, with deterministic pure-Python parity fallback for uninstalled/pure-Python test runs.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

HYPERGRAPH_SCHEMA_V1 = "aegis-hypergraph-analysis-v1"
LSH_THRESHOLD = 0.7
ENTROPY_THRESHOLD = 2.5
SHORT_LABEL_MIN_CHARS = 6
MERGE_THRESHOLD = 92.0
COMMUNITY_BOOST = 5.0
MIN_COMMUNITY_COHESION = 0.05
HALF_LIFE_DEFAULT_DAYS = 30.0
MIN_REFLECT_CORROBORATION = 2

type ProvenanceKind = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]
type NodeKindLiteral = Literal["symbol", "file", "module", "concept", "memory", "session", "task", "evidence"]

_VARIANT_ANTONYM_PAIRS: tuple[tuple[str, str], ...] = (
    ("read", "write"),
    ("reader", "writer"),
    ("encode", "decode"),
    ("encoder", "decoder"),
    ("serialize", "deserialize"),
    ("pack", "unpack"),
    ("push", "pop"),
    ("enqueue", "dequeue"),
    ("send", "recv"),
    ("sender", "receiver"),
    ("inbound", "outbound"),
    ("input", "output"),
    ("request", "response"),
    ("client", "server"),
    ("source", "target"),
    ("caller", "callee"),
    ("from", "to"),
    ("lock", "unlock"),
    ("acquire", "release"),
    ("allow", "deny"),
    ("accept", "reject"),
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("start", "stop"),
    ("open", "close"),
    ("min", "max"),
    ("pre", "post"),
    ("before", "after"),
    ("hot", "cold"),
    ("sync", "async"),
    ("local", "remote"),
    ("public", "private"),
    ("mutable", "immutable"),
    ("true", "false"),
    ("left", "right"),
    ("up", "down"),
)

_STOP_CONNECTORS: frozenset[str] = frozenset({"a", "an", "the", "of", "for", "in", "on", "by", "with", "and", "or"})


def normalize_repo_relative_path(raw: str) -> str:
    unified = raw.strip().replace("\\", "/")
    if unified.startswith("./"):
        unified = unified[2:]
    return "/".join(seg for seg in unified.split("/") if seg and seg != ".")


def normalize_entity_label(raw: str) -> str:
    """Unicode NFKC + combining-mark-preserving (M*) + camelCase-splitting normalization."""
    nfkc = unicodedata.normalize("NFKC", raw)
    chars = list(nfkc)
    out: list[str] = []
    for idx, ch in enumerate(chars):
        if idx > 0 and ch.isupper():
            prev = chars[idx - 1]
            next_is_lower = idx + 1 < len(chars) and chars[idx + 1].islower()
            if prev.islower() or prev.isdigit() or (prev.isupper() and next_is_lower):
                out.append(" ")
        cat = unicodedata.category(ch)
        if ch.isalnum() or cat.startswith("M"):
            out.append(ch.lower())
        else:
            out.append(" ")
    return " ".join("".join(out).split())


def shannon_char_entropy(normalized: str) -> float:
    compact = [ch for ch in normalized if not ch.isspace()]
    total = len(compact)
    if total <= 1:
        return 0.0
    counts: dict[str, int] = defaultdict(int)
    for ch in compact:
        counts[ch] += 1
    inv = 1.0 / total
    return -sum((c * inv) * math.log2(c * inv) for c in counts.values())


def short_label_blocked(norm_a: str, norm_b: str) -> bool:
    if norm_a == norm_b:
        return False
    len_a = sum(1 for ch in norm_a if not ch.isspace())
    len_b = sum(1 for ch in norm_b if not ch.isspace())
    return len_a < SHORT_LABEL_MIN_CHARS or len_b < SHORT_LABEL_MIN_CHARS


def _extract_numeric_tokens(text: str) -> list[str]:
    runs = re.findall(r"[0-9]+", text)
    return [r.lstrip("0") or "0" for r in runs]


def numeric_tokens_differ(norm_a: str, norm_b: str) -> bool:
    nums_a = _extract_numeric_tokens(norm_a)
    nums_b = _extract_numeric_tokens(norm_b)
    if not nums_a and not nums_b:
        return False
    return nums_a != nums_b


def is_variant_pair(norm_a: str, norm_b: str) -> bool:
    toks_a = set(norm_a.split())
    toks_b = set(norm_b.split())
    only_a = toks_a - toks_b
    only_b = toks_b - toks_a
    if not only_a or not only_b:
        return False
    for left, right in _VARIANT_ANTONYM_PAIRS:
        if (left in only_a and right in only_b) or (right in only_a and left in only_b):
            return True
    return False


def content_token_swap(norm_a: str, norm_b: str) -> bool:
    if norm_a == norm_b:
        return False
    toks_a = [t for t in norm_a.split() if t not in _STOP_CONNECTORS]
    toks_b = [t for t in norm_b.split() if t not in _STOP_CONNECTORS]
    if len(toks_a) < 2 or len(toks_b) < 2:
        return False
    shared = set(toks_a) & set(toks_b)
    if len(shared) < 2:
        return False
    return [t for t in toks_a if t in shared] != [t for t in toks_b if t in shared]


def jaro_winkler_score_100(a: str, b: str) -> float:
    if a == b:
        return 100.0
    len_a, len_b = len(a), len(b)
    if len_a == 0 or len_b == 0:
        return 0.0
    match_dist = max(0, max(len_a, len_b) // 2 - 1)
    a_matched = [False] * len_a
    b_matched = [False] * len_b
    matches = 0
    for i in range(len_a):
        start = max(0, i - match_dist)
        end = min(len_b, i + match_dist + 1)
        for j in range(start, end):
            if not b_matched[j] and a[i] == b[j]:
                a_matched[i] = True
                b_matched[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i in range(len_a):
        if not a_matched[i]:
            continue
        while not b_matched[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1
    jaro = (matches / len_a + matches / len_b + (matches - transpositions / 2.0) / matches) / 3.0
    prefix = 0
    for ca, cb in zip(a[:4], b[:4], strict=False):
        if ca == cb:
            prefix += 1
        else:
            break
    return max(0.0, min(1.0, jaro + prefix * 0.1 * (1.0 - jaro))) * 100.0


@dataclass(slots=True)
class HypergraphNodeSpec:
    node_id: int
    label: str
    kind: NodeKindLiteral = "concept"
    summary: str = ""
    source_file: str | None = None
    ast_fingerprint: int = 0
    lifecycle_rank: int = 1
    is_synthetic: bool = False
    community_id: int | None = None
    created_at_ms: int = 1
    code_fingerprint: int | None = None

    def to_rust_dict(self) -> dict[str, Any]:
        return {
            "node_id": int(self.node_id),
            "label": self.label,
            "summary": self.summary,
            "kind": self.kind,
            "source_file": normalize_repo_relative_path(self.source_file) if self.source_file else None,
            "ast_fingerprint": int(self.ast_fingerprint) & 0xFFFF_FFFF_FFFF_FFFF,
            "lifecycle_rank": max(0, min(255, int(self.lifecycle_rank))),
            "is_synthetic": bool(self.is_synthetic),
            "community_id": self.community_id,
            "created_at_ms": max(1, int(self.created_at_ms)),
            "code_fingerprint": (
                int(self.code_fingerprint) & 0xFFFF_FFFF_FFFF_FFFF if self.code_fingerprint is not None else None
            ),
            "quantized_embedding": None,
        }


@dataclass(slots=True)
class HypergraphEdgeSpec:
    hyperedge_id: int
    relation: str
    sources: tuple[int, ...]
    targets: tuple[int, ...]
    provenance: ProvenanceKind = "EXTRACTED"
    confidence: float = 1.0
    weight: float = 1.0
    valid_from_ms: int = 1
    invalidated_at_ms: int | None = None
    code_fingerprint: int | None = None

    def to_rust_dict(self) -> dict[str, Any]:
        return {
            "hyperedge_id": int(self.hyperedge_id),
            "relation": self.relation,
            "sources": [int(s) for s in self.sources],
            "targets": [int(t) for t in self.targets],
            "provenance": self.provenance,
            "confidence": max(0.0, min(1.0, float(self.confidence))),
            "weight": max(0.0, min(1.0, float(self.weight))),
            "valid_from_ms": max(1, int(self.valid_from_ms)),
            "invalidated_at_ms": int(self.invalidated_at_ms) if self.invalidated_at_ms is not None else None,
            "code_fingerprint": (
                int(self.code_fingerprint) & 0xFFFF_FFFF_FFFF_FFFF if self.code_fingerprint is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ReflectEvaluation:
    disposition: Literal[
        "NEUTRAL_UNVERIFIED",
        "CORROBORATED",
        "CONTESTED",
        "DEPRECATED",
        "STALE_CODE_FINGERPRINT",
    ]
    effective_score: float
    decayed_boost: float
    corroboration_count: int
    contradiction_count: int


def evaluate_experiential_reflect(
    base_fidelity: float,
    *,
    supporting_outcomes: int,
    contradicting_outcomes: int,
    elapsed_days: float,
    half_life_days: float = HALF_LIFE_DEFAULT_DAYS,
    code_fingerprint_current: bool = True,
) -> ReflectEvaluation:
    base = max(0.0, min(1.0, float(base_fidelity))) if math.isfinite(base_fidelity) else 0.0
    hl = half_life_days if math.isfinite(half_life_days) and half_life_days > 0 else HALF_LIFE_DEFAULT_DAYS
    if not code_fingerprint_current:
        return ReflectEvaluation(
            "STALE_CODE_FINGERPRINT", min(0.25, base * 0.25), 0.0, supporting_outcomes, contradicting_outcomes
        )
    if supporting_outcomes > 0 and contradicting_outcomes > 0:
        ratio = supporting_outcomes / (supporting_outcomes + contradicting_outcomes)
        return ReflectEvaluation(
            "CONTESTED", min(0.70, base * (0.40 + 0.35 * ratio)), 0.0, supporting_outcomes, contradicting_outcomes
        )
    if contradicting_outcomes > 0:
        raw_pen = 1.0 - math.exp(-0.45 * contradicting_outcomes)
        decay_factor = math.pow(2.0, -max(0.0, elapsed_days) / hl)
        pen = raw_pen * decay_factor
        return ReflectEvaluation(
            "DEPRECATED", max(0.0, min(1.0, base * (1.0 - 0.85 * pen))), -pen, 0, contradicting_outcomes
        )
    if supporting_outcomes < MIN_REFLECT_CORROBORATION:
        return ReflectEvaluation("NEUTRAL_UNVERIFIED", base, 0.0, supporting_outcomes, 0)
    raw_boost = min(0.50, 1.0 - math.exp(-0.30 * (supporting_outcomes - 1)))
    decayed_boost = raw_boost * math.pow(2.0, -max(0.0, elapsed_days) / hl)
    return ReflectEvaluation(
        "CORROBORATED",
        max(0.0, min(1.0, base + (1.0 - base) * decayed_boost)),
        decayed_boost,
        supporting_outcomes,
        0,
    )


def remap_communities_jaccard(
    previous_node_to_comm: dict[int, int],
    current_communities: dict[int, list[int]],
) -> dict[int, list[int]]:
    if not previous_node_to_comm:
        return {cid: list(members) for cid, members in current_communities.items()}
    prev_sets: dict[int, set[int]] = defaultdict(set)
    for nid, cid in previous_node_to_comm.items():
        prev_sets[int(cid)].add(int(nid))
    curr_sets = {int(cid): set(int(x) for x in members) for cid, members in current_communities.items()}
    candidates: list[tuple[float, int, int, int]] = []
    for pid, pset in prev_sets.items():
        for cid, cset in curr_sets.items():
            inter = len(pset & cset)
            if inter == 0:
                continue
            union = max(1, len(pset | cset))
            candidates.append((inter / union, inter, -pid, -cid))
    candidates.sort(reverse=True)
    used_prev: set[int] = set()
    assigned_curr: dict[int, int] = {}
    for _, _, neg_pid, neg_cid in candidates:
        pid, cid = -neg_pid, -neg_cid
        if pid in used_prev or cid in assigned_curr:
            continue
        used_prev.add(pid)
        assigned_curr[cid] = pid
    next_id = max(prev_sets.keys(), default=0) + 1
    remapped: dict[int, list[int]] = {}
    for cid, members in sorted(current_communities.items()):
        if cid in assigned_curr:
            target = assigned_curr[cid]
        else:
            while next_id in used_prev:
                next_id += 1
            target = next_id
            used_prev.add(target)
            next_id += 1
        remapped[target] = sorted(int(m) for m in members)
    return remapped


@dataclass
class HypergraphEngine:
    """Authoritative Python facade over the Rust MemoryHypergraph with pure-Python fallback."""

    nodes: dict[int, HypergraphNodeSpec] = field(
        default_factory=cast(Callable[[], dict[int, HypergraphNodeSpec]], dict)
    )
    hyperedges: dict[int, HypergraphEdgeSpec] = field(
        default_factory=cast(Callable[[], dict[int, HypergraphEdgeSpec]], dict)
    )
    reflections: dict[int, tuple[int, int, int, int | None]] = field(
        default_factory=cast(Callable[[], dict[int, tuple[int, int, int, int | None]]], dict)
    )

    def upsert_node(self, spec: HypergraphNodeSpec) -> None:
        if spec.node_id <= 0 or not spec.label.strip():
            raise ValueError("node_id must be positive and label non-empty")
        existing = self.nodes.get(spec.node_id)
        if existing is not None:
            existing.lifecycle_rank = max(existing.lifecycle_rank, spec.lifecycle_rank)
            if spec.ast_fingerprint:
                existing.ast_fingerprint = spec.ast_fingerprint
            if spec.summary:
                existing.summary = spec.summary
            if spec.source_file:
                existing.source_file = normalize_repo_relative_path(spec.source_file)
            if spec.code_fingerprint is not None:
                existing.code_fingerprint = spec.code_fingerprint
            if spec.community_id is not None:
                existing.community_id = spec.community_id
            existing.label = spec.label
        else:
            if spec.source_file:
                spec.source_file = normalize_repo_relative_path(spec.source_file)
            self.nodes[spec.node_id] = spec

    def upsert_edge(self, spec: HypergraphEdgeSpec) -> None:
        if spec.hyperedge_id <= 0 or not spec.relation.strip() or not spec.sources or not spec.targets:
            raise ValueError("invalid hyperedge spec")
        if len(spec.sources) == 1 and len(spec.targets) == 1 and spec.sources[0] == spec.targets[0]:
            raise ValueError("self-loop binary hyperedge is forbidden")
        for endpoint in (*spec.sources, *spec.targets):
            if endpoint not in self.nodes:
                raise ValueError(f"hyperedge endpoint {endpoint} missing from hypergraph")
        self.hyperedges[spec.hyperedge_id] = spec

    def invalidate_edge(self, hyperedge_id: int, invalidated_at_ms: int) -> bool:
        edge = self.hyperedges.get(hyperedge_id)
        if edge is None:
            return False
        if invalidated_at_ms < edge.valid_from_ms:
            raise ValueError("invalidation timestamp precedes valid_from_ms")
        edge.invalidated_at_ms = invalidated_at_ms
        return True

    def record_reflect(
        self,
        node_id: int,
        *,
        supporting: bool,
        observed_at_ms: int,
        code_fingerprint: int | None = None,
    ) -> ReflectEvaluation:
        if node_id not in self.nodes:
            raise KeyError(f"node {node_id} not found")
        sup, con, last_ms, fp = self.reflections.get(
            node_id, (0, 0, observed_at_ms, self.nodes[node_id].code_fingerprint)
        )
        if supporting:
            sup += 1
        else:
            con += 1
        last_ms = max(last_ms, observed_at_ms)
        if code_fingerprint is not None:
            fp = code_fingerprint
        self.reflections[node_id] = (sup, con, last_ms, fp)
        return self.evaluate_reflect(node_id, now_ms=observed_at_ms)

    def evaluate_reflect(self, node_id: int, *, now_ms: int) -> ReflectEvaluation:
        node = self.nodes[node_id]
        base = 0.80 if node.lifecycle_rank >= 3 else 0.70 if node.lifecycle_rank == 2 else 0.55
        rec = self.reflections.get(node_id)
        if rec is None:
            return ReflectEvaluation("NEUTRAL_UNVERIFIED", base, 0.0, 0, 0)
        sup, con, last_ms, rec_fp = rec
        fp_ok = True
        if node.code_fingerprint is not None and rec_fp is not None:
            fp_ok = node.code_fingerprint == rec_fp
        elapsed_days = max(0.0, (now_ms - last_ms) / 86_400_000.0)
        return evaluate_experiential_reflect(
            base,
            supporting_outcomes=sup,
            contradicting_outcomes=con,
            elapsed_days=elapsed_days,
            code_fingerprint_current=fp_ok,
        )

    def diff(self, newer: HypergraphEngine) -> dict[str, Any]:
        """Compute structural graph_diff between `self` (baseline) and `newer` snapshot."""
        old_keys = {(n.kind, normalize_entity_label(n.label), n.source_file or ""): n for n in self.nodes.values()}
        new_keys = {(n.kind, normalize_entity_label(n.label), n.source_file or ""): n for n in newer.nodes.values()}
        added_nodes = sorted(new_keys[k].label for k in (set(new_keys) - set(old_keys)))
        removed_nodes = sorted(old_keys[k].label for k in (set(old_keys) - set(new_keys)))
        modified_nodes = sorted(
            new_keys[k].label
            for k in (set(old_keys) & set(new_keys))
            if old_keys[k].code_fingerprint != new_keys[k].code_fingerprint
            or old_keys[k].ast_fingerprint != new_keys[k].ast_fingerprint
            or old_keys[k].summary != new_keys[k].summary
        )

        def _edge_sigs(eng: HypergraphEngine) -> set[tuple[str, tuple[str, ...], tuple[str, ...], str]]:
            sigs: set[tuple[str, tuple[str, ...], tuple[str, ...], str]] = set()
            for e in eng.hyperedges.values():
                if e.invalidated_at_ms is not None:
                    continue
                s_lbls = tuple(sorted(eng.nodes[s].label for s in e.sources if s in eng.nodes))
                t_lbls = tuple(sorted(eng.nodes[t].label for t in e.targets if t in eng.nodes))
                sigs.add((e.relation, s_lbls, t_lbls, e.provenance))
            return sigs

        old_edges = _edge_sigs(self)
        new_edges = _edge_sigs(newer)
        return {
            "added_nodes": added_nodes,
            "removed_nodes": removed_nodes,
            "modified_nodes": modified_nodes,
            "added_edge_count": len(new_edges - old_edges),
            "removed_edge_count": len(old_edges - new_edges),
        }

    def analyze(
        self,
        *,
        deduplicate: bool = True,
        dedup_guards: dict[str, bool] | None = None,
        cluster_leiden: bool = True,
        max_community_size: int = 64,
        previous_communities: dict[int, int] | None = None,
        changed_paths: tuple[str, ...] | list[str] = (),
        blast_max_hops: int = 3,
        query: str | None = None,
        top_k: int = 10,
        at_timestamp_ms: int | None = None,
        retrieval_ablation: dict[str, bool] | None = None,
        prefer_native: bool = True,
    ) -> dict[str, Any]:
        merged_dedup_guards = (
            {
                "enable_entropy_guard": True,
                "enable_short_label_guard": True,
                "enable_numeric_guard": True,
                "enable_variant_guard": True,
                "enable_token_swap_guard": True,
                "enable_crossfile_guard": True,
                "enable_community_boost": True,
                **dedup_guards,
            }
            if dedup_guards is not None
            else None
        )
        merged_retrieval_ablation = (
            {
                "enable_bm25": True,
                "enable_vector": True,
                "enable_ppr": True,
                "enable_reflect": True,
                **retrieval_ablation,
            }
            if retrieval_ablation is not None
            else None
        )
        payload: dict[str, Any] = {
            "nodes": [n.to_rust_dict() for n in self.nodes.values()],
            "hyperedges": [e.to_rust_dict() for e in self.hyperedges.values()],
            "deduplicate": bool(deduplicate),
            "dedup_guards": merged_dedup_guards,
            "cluster_leiden": bool(cluster_leiden),
            "max_community_size": int(max_community_size),
            "previous_communities": (
                {str(k): int(v) for k, v in previous_communities.items()} if previous_communities else None
            ),
            "changed_paths": [normalize_repo_relative_path(p) for p in changed_paths],
            "blast_max_hops": int(blast_max_hops),
            "query": query,
            "top_k": int(top_k),
            "at_timestamp_ms": at_timestamp_ms,
            "retrieval_ablation": merged_retrieval_ablation,
        }
        if prefer_native and not self.reflections:
            try:
                try:
                    import aegis_nerve  # type: ignore[import-not-found]
                except ImportError:
                    from . import aegis_nerve  # type: ignore[attr-defined]

                fn = getattr(cast(object, aegis_nerve), "aegis_hypergraph_analyze_json", None)
                if callable(fn):
                    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    if len(encoded.encode("utf-8")) <= 16 * 1024 * 1024:
                        raw_result = fn(encoded)
                        if isinstance(raw_result, (str, bytes, bytearray)):
                            result = cast(dict[str, Any], json.loads(raw_result))
                            result["authority"] = "rust_native"
                            return result
            except ImportError:
                pass
        return self._analyze_python_fallback(
            deduplicate=deduplicate,
            dedup_guards=dedup_guards,
            cluster_leiden=cluster_leiden,
            max_community_size=max_community_size,
            previous_communities=previous_communities,
            changed_paths=changed_paths,
            blast_max_hops=blast_max_hops,
            query=query,
            top_k=top_k,
            at_timestamp_ms=at_timestamp_ms,
            retrieval_ablation=retrieval_ablation,
        )

    def _analyze_python_fallback(
        self,
        *,
        deduplicate: bool,
        dedup_guards: dict[str, bool] | None,
        cluster_leiden: bool,
        max_community_size: int,
        previous_communities: dict[int, int] | None,
        changed_paths: tuple[str, ...] | list[str],
        blast_max_hops: int,
        query: str | None,
        top_k: int,
        at_timestamp_ms: int | None,
        retrieval_ablation: dict[str, bool] | None,
    ) -> dict[str, Any]:
        guards = {
            "enable_entropy_guard": True,
            "enable_short_label_guard": True,
            "enable_numeric_guard": True,
            "enable_variant_guard": True,
            "enable_token_swap_guard": True,
            "enable_crossfile_guard": True,
            "enable_community_boost": True,
            **(dedup_guards or {}),
        }
        working_nodes = {
            nid: HypergraphNodeSpec(**{k: getattr(n, k) for k in n.__dataclass_fields__})
            for nid, n in self.nodes.items()
        }
        working_edges = {
            eid: HypergraphEdgeSpec(**{k: getattr(e, k) for k in e.__dataclass_fields__})
            for eid, e in self.hyperedges.items()
        }

        dedup_report: dict[str, Any] | None = None
        if deduplicate and len(working_nodes) > 1:
            ordered = sorted(working_nodes.keys())
            norms = {nid: normalize_entity_label(working_nodes[nid].label) for nid in ordered}
            ents = {nid: shannon_char_entropy(norms[nid]) for nid in ordered}
            parent = {nid: nid for nid in ordered}

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
            for nid in ordered:
                pref = "".join(ch for ch in norms[nid] if not ch.isspace())[:4]
                buckets[(working_nodes[nid].kind, pref)].append(nid)
                buckets[(working_nodes[nid].kind, norms[nid])].append(nid)

            pairs: set[tuple[int, int]] = set()
            for b in buckets.values():
                uniq = sorted(set(b))[:64]
                for i in range(len(uniq)):
                    for j in range(i + 1, len(uniq)):
                        pairs.add((uniq[i], uniq[j]))

            for a, b in sorted(pairs):
                na, nb = working_nodes[a], working_nodes[b]
                if na.is_synthetic or nb.is_synthetic:
                    continue
                if (
                    guards["enable_crossfile_guard"]
                    and na.kind in {"symbol", "file"}
                    and nb.kind in {"symbol", "file"}
                    and na.source_file
                    and nb.source_file
                    and na.source_file != nb.source_file
                ):
                    continue
                norm_a, norm_b = norms[a], norms[b]
                if norm_a != norm_b:
                    if guards["enable_entropy_guard"] and (ents[a] < ENTROPY_THRESHOLD or ents[b] < ENTROPY_THRESHOLD):
                        continue
                    if guards["enable_short_label_guard"] and short_label_blocked(norm_a, norm_b):
                        continue
                    if guards["enable_numeric_guard"] and numeric_tokens_differ(norm_a, norm_b):
                        continue
                    if guards["enable_variant_guard"] and is_variant_pair(norm_a, norm_b):
                        continue
                    if guards["enable_token_swap_guard"] and content_token_swap(norm_a, norm_b):
                        continue
                    jw = jaro_winkler_score_100(norm_a, norm_b)
                    boost = (
                        COMMUNITY_BOOST
                        if guards["enable_community_boost"]
                        and na.community_id is not None
                        and na.community_id == nb.community_id
                        else 0.0
                    )
                    if jw + boost < MERGE_THRESHOLD:
                        continue
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

            clusters: dict[int, list[int]] = defaultdict(list)
            for nid in ordered:
                clusters[find(nid)].append(nid)
            remap: dict[int, int] = {}
            surviving: dict[int, HypergraphNodeSpec] = {}
            for members in clusters.values():
                survivor_id = max(
                    members,
                    key=lambda m: (
                        working_nodes[m].lifecycle_rank,
                        int(working_nodes[m].ast_fingerprint != 0),
                        len(working_nodes[m].label),
                        -m,
                    ),
                )
                surviving[survivor_id] = working_nodes[survivor_id]
                for m in members:
                    remap[m] = survivor_id
            remapped_edges: dict[int, HypergraphEdgeSpec] = {}
            for eid, edge in working_edges.items():
                srcs = tuple(sorted({remap.get(s, s) for s in edge.sources}))
                tgts = tuple(sorted({remap.get(t, t) for t in edge.targets}))
                if len(srcs) == 1 and len(tgts) == 1 and srcs[0] == tgts[0]:
                    continue
                edge.sources = srcs
                edge.targets = tgts
                remapped_edges[eid] = edge
            dedup_report = {
                "initial_node_count": len(working_nodes),
                "final_node_count": len(surviving),
                "merged_node_count": len(working_nodes) - len(surviving),
                "candidate_pairs_evaluated": len(pairs),
                "canonical_remap": {str(k): v for k, v in remap.items()},
            }
            working_nodes = surviving
            working_edges = remapped_edges

        def edge_active(e: HypergraphEdgeSpec) -> bool:
            if at_timestamp_ms is None:
                return e.invalidated_at_ms is None
            return e.valid_from_ms <= at_timestamp_ms and (
                e.invalidated_at_ms is None or at_timestamp_ms < e.invalidated_at_ms
            )

        def edge_eff_weight(e: HypergraphEdgeSpec) -> float:
            mult = (
                1.0
                if e.provenance == "EXTRACTED"
                else 0.75 * e.confidence
                if e.provenance == "INFERRED"
                else min(0.35, 0.35 * e.confidence)
            )
            return max(0.0, min(1.0, e.weight * mult))

        # Connected-component / modularity clustering fallback
        communities_out: list[dict[str, Any]] = []
        if cluster_leiden and working_nodes:
            adj: dict[int, dict[int, float]] = {nid: defaultdict(float) for nid in working_nodes}
            for e in working_edges.values():
                if not edge_active(e):
                    continue
                w = edge_eff_weight(e)
                for s in e.sources:
                    for t in e.targets:
                        if s != t and s in adj and t in adj:
                            adj[s][t] += w
                            adj[t][s] += w
            comm = {nid: nid for nid in sorted(working_nodes)}
            deg = {nid: sum(adj[nid].values()) for nid in working_nodes}
            two_m = max(1e-9, sum(deg.values()))
            sigma = dict(deg)
            for _ in range(8):
                moved = False
                for u in sorted(working_nodes):
                    if deg[u] <= 0:
                        continue
                    old_c = comm[u]
                    sigma[old_c] -= deg[u]
                    c_weights: dict[int, float] = defaultdict(float)
                    for v, w in adj[u].items():
                        c_weights[comm[v]] += w
                    best_c = old_c
                    best_gain = c_weights.get(old_c, 0.0) - deg[u] * sigma[old_c] / two_m
                    for cand_c, w_in in sorted(c_weights.items()):
                        gain = w_in - deg[u] * sigma[cand_c] / two_m
                        if gain > best_gain + 1e-12:
                            best_gain = gain
                            best_c = cand_c
                    comm[u] = best_c
                    sigma[best_c] += deg[u]
                    moved = moved or (best_c != old_c)
                if not moved:
                    break
            raw_groups: dict[int, list[int]] = defaultdict(list)
            for nid, c in comm.items():
                raw_groups[c].append(nid)

            def _cohesion_and_conductance(members: list[int]) -> tuple[float, float]:
                if len(members) <= 1:
                    return (1.0, 0.0)
                mset = set(members)
                internal_pairs = 0
                cut_weight = 0.0
                vol = 0.0
                for u in members:
                    for v, w in adj[u].items():
                        vol += w
                        if v in mset:
                            if u < v:
                                internal_pairs += 1
                        else:
                            cut_weight += w
                max_pairs = len(members) * (len(members) - 1) // 2
                cohesion = internal_pairs / max(1, max_pairs)
                denom = min(vol, max(0.0, two_m - vol))
                conductance = cut_weight / denom if denom > 1e-9 else 0.0
                return (cohesion, conductance)

            split_groups: list[list[int]] = []
            max_cap = max(2, max_community_size)
            for _, members in sorted(raw_groups.items(), key=lambda kv: min(kv[1])):
                ordered_m = sorted(members)
                coh, _ = _cohesion_and_conductance(ordered_m)
                if len(ordered_m) > max_cap or (len(ordered_m) > 2 and coh < MIN_COMMUNITY_COHESION):
                    chunk_sz = min(max_cap, max(2, len(ordered_m) // 2))
                    split_groups.extend(
                        ordered_m[start_i : start_i + chunk_sz] for start_i in range(0, len(ordered_m), chunk_sz)
                    )
                else:
                    split_groups.append(ordered_m)

            canonical_groups = {idx + 1: grp for idx, grp in enumerate(split_groups)}
            if previous_communities:
                canonical_groups = remap_communities_jaccard(previous_communities, canonical_groups)
            for cid, members in sorted(canonical_groups.items()):
                for m in members:
                    working_nodes[m].community_id = cid
                coh, cond = _cohesion_and_conductance(members)
                communities_out.append(
                    {
                        "community_id": cid,
                        "node_ids": members,
                        "internal_cohesion": round(coh, 6),
                        "conductance": round(cond, 6),
                    }
                )

        # Blast radius
        affected_out: list[dict[str, Any]] = []
        norm_paths = {normalize_repo_relative_path(p) for p in changed_paths if p.strip()}
        if norm_paths:
            best_imp: dict[int, tuple[int, float, str, str]] = {}
            frontier: list[tuple[int, float]] = []
            for nid, node in working_nodes.items():
                if (node.source_file and node.source_file in norm_paths) or (
                    node.kind == "file" and normalize_repo_relative_path(node.label) in norm_paths
                ):
                    best_imp[nid] = (0, 1.0, "direct_file_anchor", "EXTRACTED")
                    frontier.append((nid, 1.0))
            imp_adj: dict[int, list[tuple[int, float, str, str]]] = defaultdict(list)
            for e in working_edges.values():
                if not edge_active(e):
                    continue
                ew = edge_eff_weight(e)
                for s in e.sources:
                    for t in e.targets:
                        if s != t:
                            imp_adj[t].append((s, ew * 0.85, e.relation, e.provenance))
                            imp_adj[s].append((t, ew * 0.65, e.relation, e.provenance))
            for hop in range(1, max(1, min(6, blast_max_hops)) + 1):
                nxt: dict[int, float] = {}
                for curr_id, curr_score in frontier:
                    for nbr, mult, rel, prov in imp_adj.get(curr_id, ()):
                        sc = curr_score * mult
                        if sc < 0.05:
                            continue
                        prev = best_imp.get(nbr)
                        if prev is None or sc > prev[1] + 1e-9:
                            best_imp[nbr] = (hop, sc, rel, prov)
                            nxt[nbr] = max(nxt.get(nbr, 0.0), sc)
                if not nxt:
                    break
                frontier = list(nxt.items())
            for nid, (hop, sc, rel, prov) in best_imp.items():
                n = working_nodes[nid]
                affected_out.append(
                    {
                        "node_id": nid,
                        "label": n.label,
                        "kind": n.kind,
                        "source_file": n.source_file,
                        "hop_distance": hop,
                        "impact_score": sc,
                        "via_relation": rel,
                        "via_provenance": prov,
                    }
                )
            affected_out.sort(key=lambda x: (-x["impact_score"], x["hop_distance"], x["node_id"]))

        # Hybrid retrieval fallback (BM25 + char-shingle cosine + 15-step PPR + reflect)
        retrieval_hits: list[dict[str, Any]] = []
        if query and query.strip() and working_nodes:
            ab = {
                "enable_bm25": True,
                "enable_vector": True,
                "enable_ppr": True,
                "enable_reflect": True,
                **(retrieval_ablation or {}),
            }
            q_norm = normalize_entity_label(query)
            q_terms = q_norm.split()
            q_shingles = {q_norm[i : i + 3] for i in range(max(1, len(q_norm) - 2))}
            n_docs = len(working_nodes)
            tf_by_id: dict[int, dict[str, int]] = {}
            df: dict[str, int] = defaultdict(int)
            dl_by_id: dict[int, int] = {}
            for nid, node in working_nodes.items():
                doc = normalize_entity_label(f"{node.label} {node.label} {node.summary}")
                toks = doc.split()
                tf: dict[str, int] = defaultdict(int)
                for t in toks:
                    tf[t] += 1
                for t in tf:
                    df[t] += 1
                tf_by_id[nid] = tf
                dl_by_id[nid] = max(1, len(toks))
            avgdl = max(1.0, sum(dl_by_id.values()) / n_docs)
            bm25: dict[int, float] = {}
            vec_sc: dict[int, float] = {}
            for nid, node in working_nodes.items():
                if ab["enable_bm25"]:
                    s = 0.0
                    for qt in q_terms:
                        f = tf_by_id[nid].get(qt, 0)
                        if f > 0:
                            idf = math.log((n_docs - df[qt] + 0.5) / (df[qt] + 0.5) + 1.0)
                            s += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * dl_by_id[nid] / avgdl))
                    bm25[nid] = s
                else:
                    bm25[nid] = 0.0
                if ab["enable_vector"]:
                    doc = normalize_entity_label(f"{node.label} {node.summary}")
                    d_shingles = {doc[i : i + 3] for i in range(max(1, len(doc) - 2))}
                    vec_sc[nid] = len(q_shingles & d_shingles) / math.sqrt(max(1, len(q_shingles) * len(d_shingles)))
                else:
                    vec_sc[nid] = 0.0
            max_b = max(bm25.values(), default=0.0) or 1.0
            max_v = max(vec_sc.values(), default=0.0) or 1.0
            for nid in working_nodes:
                bm25[nid] /= max_b
                vec_sc[nid] /= max_v

            ppr: dict[int, float] = {nid: 0.0 for nid in working_nodes}
            if ab["enable_ppr"]:
                seeds = {
                    nid: 0.65 * bm25[nid] + 0.35 * vec_sc[nid]
                    for nid in working_nodes
                    if (0.65 * bm25[nid] + 0.35 * vec_sc[nid]) > 0.05
                }
                seed_sum = sum(seeds.values())
                if seed_sum > 0:
                    seeds = {nid: val / seed_sum for nid, val in seeds.items()}
                    out_adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
                    for e in working_edges.values():
                        if not edge_active(e):
                            continue
                        ew = edge_eff_weight(e)
                        for s in e.sources:
                            for t in e.targets:
                                if s != t and s in working_nodes and t in working_nodes:
                                    out_adj[s].append((t, ew))
                                    out_adj[t].append((s, ew * 0.85))
                    rank = {nid: seeds.get(nid, 0.0) for nid in working_nodes}
                    for _ in range(15):
                        nxt_r = {nid: 0.15 * seeds.get(nid, 0.0) for nid in working_nodes}
                        for u, r_val in rank.items():
                            nbrs = out_adj.get(u, [])
                            w_sum = sum(w for _, w in nbrs)
                            if w_sum > 0 and r_val > 0:
                                for v, w in nbrs:
                                    nxt_r[v] += 0.85 * r_val * (w / w_sum)
                            elif r_val > 0:
                                for s_id, s_mass in seeds.items():
                                    nxt_r[s_id] += 0.85 * r_val * s_mass
                        rank = nxt_r
                    max_p = max(rank.values(), default=0.0) or 1.0
                    ppr = {nid: val / max_p for nid, val in rank.items()}

            now_val = at_timestamp_ms if at_timestamp_ms is not None else 1_700_000_000_000
            for nid, node in working_nodes.items():
                if node.is_synthetic:
                    continue
                b, v, p = bm25[nid], vec_sc[nid], ppr[nid]
                active_tiers = int(b > 0.01) + int(v > 0.01) + int(p > 0.01)
                if active_tiers == 0:
                    continue
                fused = 0.45 * b + 0.25 * v + 0.30 * p
                ref_eval = (
                    self.evaluate_reflect(nid, now_ms=now_val)
                    if nid in self.nodes
                    else ReflectEvaluation("NEUTRAL_UNVERIFIED", 0.7, 0.0, 0, 0)
                )
                if ab["enable_reflect"]:
                    if ref_eval.disposition == "CORROBORATED":
                        fused *= 1.0 + 0.35 * ref_eval.decayed_boost
                    elif ref_eval.disposition == "CONTESTED":
                        fused *= 0.72
                    elif ref_eval.disposition == "STALE_CODE_FINGERPRINT":
                        fused *= 0.25
                retrieval_hits.append(
                    {
                        "node_id": nid,
                        "label": node.label,
                        "kind": node.kind,
                        "origin_tier": "HybridFused" if active_tiers >= 2 else "HotLexicalBm25",
                        "fused_score": fused,
                        "bm25_score": b,
                        "vector_score": v,
                        "ppr_score": p,
                        "reflect_disposition": ref_eval.disposition,
                    }
                )
            retrieval_hits.sort(key=lambda x: (-x["fused_score"], x["node_id"]))
            retrieval_hits = retrieval_hits[: max(1, top_k)]

        # God nodes (exclude synthetic and structural hub File/Module nodes) & surprising connections
        in_deg: dict[int, int] = defaultdict(int)
        out_deg: dict[int, int] = defaultdict(int)
        incident_he: dict[int, int] = defaultdict(int)
        comm_sizes: dict[int, int] = defaultdict(int)
        for node in working_nodes.values():
            if node.community_id is not None:
                comm_sizes[node.community_id] += 1
        for e in working_edges.values():
            if not edge_active(e):
                continue
            for s in e.sources:
                out_deg[s] += len(e.targets)
                incident_he[s] += 1
            for t in e.targets:
                in_deg[t] += len(e.sources)
                incident_he[t] += 1

        god_nodes_out: list[dict[str, Any]] = []
        for nid, node in working_nodes.items():
            if node.is_synthetic or node.kind in ("file", "module"):
                continue
            tot_deg = in_deg[nid] + out_deg[nid]
            if tot_deg == 0:
                continue
            god_nodes_out.append(
                {
                    "node_id": nid,
                    "label": node.label,
                    "kind": node.kind,
                    "in_degree": in_deg[nid],
                    "out_degree": out_deg[nid],
                    "total_degree": tot_deg,
                    "incident_hyperedges": incident_he[nid],
                    "centrality_score": round(tot_deg + 0.5 * incident_he[nid], 4),
                }
            )
        god_nodes_out.sort(key=lambda x: (-x["centrality_score"], x["node_id"]))
        god_nodes_out = god_nodes_out[: max(1, top_k)]

        surprising_out: list[dict[str, Any]] = []
        for e in working_edges.values():
            if not edge_active(e) or e.relation in ("contains", "defines", "has_method"):
                continue
            prov_factor = 1.35 if e.provenance == "INFERRED" else 1.15 if e.provenance == "AMBIGUOUS" else 1.0
            for s in e.sources:
                for t in e.targets:
                    ns = working_nodes.get(s)
                    nt = working_nodes.get(t)
                    if ns is None or nt is None or ns.is_synthetic or nt.is_synthetic:
                        continue
                    if ns.kind in ("file", "module") or nt.kind in ("file", "module"):
                        continue
                    cs, ct = ns.community_id, nt.community_id
                    if cs is None or ct is None or cs == ct:
                        continue
                    bridge_mass = math.log1p(comm_sizes[cs] * comm_sizes[ct])
                    specificity = 1.0 / math.sqrt(max(1, in_deg[s] + out_deg[s]) * max(1, in_deg[t] + out_deg[t]))
                    surprise = round(prov_factor * bridge_mass * (0.5 + specificity) * max(0.2, e.confidence), 6)
                    surprising_out.append(
                        {
                            "hyperedge_id": e.hyperedge_id,
                            "source_id": s,
                            "target_id": t,
                            "source_label": ns.label,
                            "target_label": nt.label,
                            "relation": e.relation,
                            "provenance": e.provenance,
                            "source_community": cs,
                            "target_community": ct,
                            "surprise_score": surprise,
                        }
                    )
        surprising_out.sort(key=lambda x: (-x["surprise_score"], x["hyperedge_id"]))
        surprising_out = surprising_out[: max(1, top_k)]

        return {
            "schema": HYPERGRAPH_SCHEMA_V1,
            "authority": "python_fallback",
            "node_count": len(working_nodes),
            "hyperedge_count": len(working_edges),
            "dedup_report": dedup_report,
            "communities": communities_out,
            "god_nodes": god_nodes_out,
            "surprising_connections": surprising_out,
            "affected_nodes": affected_out,
            "retrieval_hits": retrieval_hits,
        }


def build_workspace_hypergraph(
    workspace_root: Path,
    *,
    include_globs: tuple[str, ...] = ("aegis_cognition/*.py", "core/python/**/*.py", "core/rust/src/**/*.rs"),
    max_files: int = 250,
) -> HypergraphEngine:
    """Extract a deterministic AST & dependency hypergraph directly from repository sources."""
    root = workspace_root.resolve()
    engine = HypergraphEngine()
    next_node_id = 1
    next_edge_id = 1
    file_node_by_rel: dict[str, int] = {}
    symbol_nodes_by_name: dict[str, list[int]] = defaultdict(list)

    matched_files: list[Path] = []
    seen_paths: set[Path] = set()
    for pattern in include_globs:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path not in seen_paths:
                seen_paths.add(path)
                matched_files.append(path)
                if len(matched_files) >= max_files:
                    break
        if len(matched_files) >= max_files:
            break

    file_contents: dict[str, str] = {}
    for path in matched_files:
        rel = normalize_repo_relative_path(path.relative_to(root).as_posix())
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        file_contents[rel] = text
        fp = int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "little")
        fid = next_node_id
        next_node_id += 1
        file_node_by_rel[rel] = fid
        engine.upsert_node(
            HypergraphNodeSpec(
                node_id=fid,
                label=rel,
                kind="file",
                summary=f"Source file {rel}",
                source_file=rel,
                ast_fingerprint=fp,
                lifecycle_rank=2,
                code_fingerprint=fp,
            )
        )

    rust_item_re = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)"
    )
    rust_use_re = re.compile(r"^\s*use\s+crate::([A-Za-z0-9_:]+)")

    pending_calls: list[tuple[int, str]] = []
    pending_bases: list[tuple[int, str]] = []
    pending_imports: list[tuple[int, str]] = []

    for rel, text in sorted(file_contents.items()):
        fid = file_node_by_rel[rel]
        file_fp = engine.nodes[fid].code_fingerprint
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    sid = next_node_id
                    next_node_id += 1
                    doc = ast.get_docstring(node) or ""
                    ast_fp = int.from_bytes(
                        hashlib.blake2b(
                            ast.dump(node, include_attributes=False).encode("utf-8"), digest_size=8
                        ).digest(),
                        "little",
                    )
                    engine.upsert_node(
                        HypergraphNodeSpec(
                            node_id=sid,
                            label=node.name,
                            kind="symbol",
                            summary=doc[:240],
                            source_file=rel,
                            ast_fingerprint=ast_fp,
                            lifecycle_rank=3,
                            code_fingerprint=file_fp,
                        )
                    )
                    symbol_nodes_by_name[node.name].append(sid)
                    engine.upsert_edge(
                        HypergraphEdgeSpec(
                            hyperedge_id=next_edge_id,
                            relation="defines",
                            sources=(fid,),
                            targets=(sid,),
                            provenance="EXTRACTED",
                            confidence=1.0,
                            weight=1.0,
                        )
                    )
                    next_edge_id += 1
                    if isinstance(node, ast.ClassDef):
                        pending_bases.extend((sid, base.id) for base in node.bases if isinstance(base, ast.Name))
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            if isinstance(sub.func, ast.Name):
                                pending_calls.append((sid, sub.func.id))
                            elif isinstance(sub.func, ast.Attribute):
                                pending_calls.append((sid, sub.func.attr))
                elif isinstance(node, ast.ImportFrom) and node.module:
                    pending_imports.append((fid, node.module.replace(".", "/")))
        elif rel.endswith(".rs"):
            for line in text.splitlines():
                m = rust_item_re.match(line)
                if m:
                    sym_name = m.group(1)
                    sid = next_node_id
                    next_node_id += 1
                    sym_fp = int.from_bytes(
                        hashlib.blake2b(f"{rel}::{sym_name}".encode(), digest_size=8).digest(),
                        "little",
                    )
                    engine.upsert_node(
                        HypergraphNodeSpec(
                            node_id=sid,
                            label=sym_name,
                            kind="symbol",
                            summary=f"Rust symbol {sym_name} in {rel}",
                            source_file=rel,
                            ast_fingerprint=sym_fp,
                            lifecycle_rank=3,
                            code_fingerprint=file_fp,
                        )
                    )
                    symbol_nodes_by_name[sym_name].append(sid)
                    engine.upsert_edge(
                        HypergraphEdgeSpec(
                            hyperedge_id=next_edge_id,
                            relation="defines",
                            sources=(fid,),
                            targets=(sid,),
                            provenance="EXTRACTED",
                            confidence=1.0,
                            weight=1.0,
                        )
                    )
                    next_edge_id += 1
                um = rust_use_re.match(line)
                if um:
                    mod_seg = um.group(1).split("::")[0]
                    pending_imports.append((fid, f"core/rust/src/{mod_seg}"))

    seen_edges: set[tuple[int, int, str]] = set()
    for src_sid, base_name in pending_bases:
        for tgt_sid in symbol_nodes_by_name.get(base_name, ())[:4]:
            if src_sid != tgt_sid and (src_sid, tgt_sid, "inherits") not in seen_edges:
                seen_edges.add((src_sid, tgt_sid, "inherits"))
                engine.upsert_edge(
                    HypergraphEdgeSpec(
                        hyperedge_id=next_edge_id,
                        relation="inherits",
                        sources=(src_sid,),
                        targets=(tgt_sid,),
                        provenance="EXTRACTED",
                        confidence=1.0,
                        weight=0.95,
                    )
                )
                next_edge_id += 1

    for src_sid, callee_name in pending_calls:
        targets = symbol_nodes_by_name.get(callee_name, ())
        if not targets or len(targets) > 4:
            continue
        prov: ProvenanceKind = "EXTRACTED" if len(targets) == 1 else "INFERRED"
        conf = 0.95 if len(targets) == 1 else 0.70
        for tgt_sid in targets:
            if src_sid != tgt_sid and (src_sid, tgt_sid, "calls") not in seen_edges:
                seen_edges.add((src_sid, tgt_sid, "calls"))
                engine.upsert_edge(
                    HypergraphEdgeSpec(
                        hyperedge_id=next_edge_id,
                        relation="calls",
                        sources=(src_sid,),
                        targets=(tgt_sid,),
                        provenance=prov,
                        confidence=conf,
                        weight=0.85,
                    )
                )
                next_edge_id += 1

    for src_fid, mod_hint in pending_imports:
        for cand_suffix in (
            f"{mod_hint}.py",
            f"{mod_hint}/__init__.py",
            f"{mod_hint}.rs",
            f"{mod_hint}/mod.rs",
            f"aegis_cognition/{mod_hint}.py",
        ):
            tgt_fid = file_node_by_rel.get(cand_suffix)
            if tgt_fid is not None and tgt_fid != src_fid and (src_fid, tgt_fid, "imports") not in seen_edges:
                seen_edges.add((src_fid, tgt_fid, "imports"))
                engine.upsert_edge(
                    HypergraphEdgeSpec(
                        hyperedge_id=next_edge_id,
                        relation="imports",
                        sources=(src_fid,),
                        targets=(tgt_fid,),
                        provenance="EXTRACTED",
                        confidence=1.0,
                        weight=0.90,
                    )
                )
                next_edge_id += 1

    return engine


__all__ = [
    "COMMUNITY_BOOST",
    "ENTROPY_THRESHOLD",
    "HALF_LIFE_DEFAULT_DAYS",
    "HYPERGRAPH_SCHEMA_V1",
    "LSH_THRESHOLD",
    "MERGE_THRESHOLD",
    "MIN_COMMUNITY_COHESION",
    "MIN_REFLECT_CORROBORATION",
    "HypergraphEdgeSpec",
    "HypergraphEngine",
    "HypergraphNodeSpec",
    "ReflectEvaluation",
    "build_workspace_hypergraph",
    "content_token_swap",
    "evaluate_experiential_reflect",
    "is_variant_pair",
    "jaro_winkler_score_100",
    "normalize_entity_label",
    "normalize_repo_relative_path",
    "numeric_tokens_differ",
    "remap_communities_jaccard",
    "shannon_char_entropy",
    "short_label_blocked",
]
