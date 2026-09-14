"""Measure bounded Memory Agent context against an unbounded baseline.

The benchmark is deliberately provider-neutral.  It measures the exact token
accounting heuristic used by ``ContextCompiler`` and the desktop prompt
renderer, so results remain reproducible without sending private source or
conversation data to a model provider.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import time
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "aegis-memory-context-benchmark-v1"
ACCOUNTING = "estimated-byte-heuristic-v1"


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _estimate_tokens(text: str) -> int:
    """Match ContextCompiler's provider-neutral accounting contract."""

    return max(1, (len(text.encode("utf-8")) + 3) // 4) + 8


def _prompt(paths: list[str], total: int, message: str) -> str:
    return "\n".join(
        [
            "[AEGIS LOCAL WORKSPACE CONTEXT]",
            "source_revision: benchmark-revision",
            f"source_files_total: {total}",
            f"source_files_selected: {len(paths)}",
            f"source_files_omitted: {max(0, total - len(paths))}",
            "source_file_selection: bounded lexical path candidates; not dependency proof",
            f"source_files: {', '.join(paths)}",
            "[PERSISTED CONVERSATION]",
            "[RECALLED AUTHORIZED CONTEXT]",
            "(none)",
            "[CURRENT USER MESSAGE]",
            message,
        ]
    )


def _path_records(count: int) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for index in range(count):
        if index % 11 == 0:
            path = f"src/memory/recall_policy_{index:04d}.py"
        elif index % 17 == 0:
            path = f"src/workspace/graph_projection_{index:04d}.ts"
        else:
            path = f"src/modules/module_{index % 23:02d}/component_{index:04d}.py"
        records.append({"relative_path": path})
    return records


def _path_case(count: int, message: str) -> dict[str, Any]:
    from aegis_cognition.desktop_service import _select_source_paths_for_prompt

    records = _path_records(count)
    all_paths = sorted(item["relative_path"] for item in records)
    selected = _select_source_paths_for_prompt(records, message)
    baseline_prompt = _prompt(all_paths, count, message)
    bounded_prompt = _prompt(selected, count, message)
    baseline_tokens = _estimate_tokens(baseline_prompt)
    bounded_tokens = _estimate_tokens(bounded_prompt)
    saved = baseline_tokens - bounded_tokens
    return {
        "workload": "workspace_source_paths",
        "input_files": count,
        "baseline_tokens": baseline_tokens,
        "bounded_tokens": bounded_tokens,
        "tokens_saved": saved,
        "reduction_percent": round(saved * 100 / baseline_tokens, 3) if baseline_tokens else 0.0,
        "selected_files": len(selected),
        "omitted_files": count - len(selected),
        "deterministic": selected == _select_source_paths_for_prompt(records, message),
    }


def _tracked_workspace_case(message: str) -> dict[str, Any]:
    """Measure the selector against this checkout without exporting source text."""

    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("cannot enumerate the checked-out workspace") from error
    paths = sorted({line.strip() for line in completed.stdout.splitlines() if line.strip()})
    if not paths:
        raise RuntimeError("checked-out workspace has no tracked files")
    from aegis_cognition.desktop_service import _select_source_paths_for_prompt

    records = [{"relative_path": path} for path in paths]
    selected = _select_source_paths_for_prompt(records, message)
    repeated = _select_source_paths_for_prompt(records, message)
    baseline_prompt = _prompt(paths, len(paths), message)
    bounded_prompt = _prompt(selected, len(paths), message)
    baseline_tokens = _estimate_tokens(baseline_prompt)
    bounded_tokens = _estimate_tokens(bounded_prompt)
    saved = baseline_tokens - bounded_tokens
    return {
        "workload": "checked_out_workspace_paths",
        "input_files": len(paths),
        "baseline_tokens": baseline_tokens,
        "bounded_tokens": bounded_tokens,
        "tokens_saved": saved,
        "reduction_percent": round(saved * 100 / baseline_tokens, 3) if baseline_tokens else 0.0,
        "selected_files": len(selected),
        "omitted_files": len(paths) - len(selected),
        "selection_cap_respected": len(selected) <= 64,
        "deterministic": selected == repeated,
    }


def _relevance_case() -> dict[str, Any]:
    """Check a small, explicit relevance oracle rather than only counting bytes."""

    from aegis_cognition.desktop_service import _select_source_paths_for_prompt

    message = "Explain memory recall and workspace graph implementation"
    records = [
        {"relative_path": "docs/README.md"},
        {"relative_path": "src/memory/recall_policy.py"},
        {"relative_path": "src/workspace/graph_projection.ts"},
        {"relative_path": "tests/test_memory_context.py"},
    ]
    selected = _select_source_paths_for_prompt(records, message)
    repeated = _select_source_paths_for_prompt(records, message)
    expected = {
        "src/memory/recall_policy.py",
        "src/workspace/graph_projection.ts",
        "tests/test_memory_context.py",
    }
    retained = sorted(expected.intersection(selected))
    return {
        "workload": "lexical_relevance_oracle",
        "expected_relevant_files": sorted(expected),
        "retained_relevant_files": retained,
        "relevance_recall_percent": round(len(retained) * 100 / len(expected), 3),
        "oracle_pass": retained == sorted(expected),
        "deterministic": selected == repeated,
    }


@dataclass(frozen=True)
class _Record:
    session_id: int
    content_hash: str
    timestamp: int
    scope_kind: str
    owner_id: str
    content: str


class _Learning:
    def __init__(self, records: list[_Record]) -> None:
        self.records = {record.session_id: record for record in records}

    def read_session_record_scoped(self, session_id: int, *, scope_kind: str, owner_id: str) -> _Record | None:
        record = self.records.get(session_id)
        if record is None or record.scope_kind != scope_kind or record.owner_id != owner_id:
            return None
        return record


def _context_records(count: int) -> list[_Record]:
    return [
        _Record(
            session_id=index + 1,
            content_hash=f"hash-{index + 1}",
            timestamp=1_700_000_000_000 + index,
            scope_kind="USER_PRIVATE",
            owner_id="benchmark-owner",
            content=(
                f"Memory item {index + 1}: workspace decision about bounded context retrieval, "
                "source authority, deterministic ordering, and evidence retention. "
            )
            * 4,
        )
        for index in range(count)
    ]


def _context_case(count: int, token_budget: int) -> dict[str, Any]:
    from core.python.aegis.context_compiler import ContextCompiler

    records = _context_records(count)
    learning = _Learning(records)
    candidates = [
        SimpleNamespace(segment_id=record.session_id, evidence_ref_hash=record.content_hash, score=1.0 - index / count)
        for index, record in enumerate(records)
    ]
    # Keep the baseline envelope identical to ContextCompiler._render so the
    # comparison measures omitted context rather than formatting overhead.
    baseline_blocks = ["[AUTHORIZED HYDRATED CONTEXT — SOURCE BOUND]"]
    for record in records:
        baseline_blocks.extend(
            (
                f"[SOURCE session=0x{record.session_id:x} hash={record.content_hash} "
                f"scope={record.scope_kind} owner={record.owner_id}]",
                record.content,
                "[/SOURCE]",
            )
        )
    baseline_blocks.append("[/AUTHORIZED HYDRATED CONTEXT]")
    baseline_rendered = "\n".join(baseline_blocks)
    bounded = ContextCompiler(learning, token_budget=token_budget, candidate_cap=min(100, count)).compile(
        "bounded context benchmark",
        candidates,
        scope_kind="USER_PRIVATE",
        owner_id="benchmark-owner",
        mandatory_session_ids=(1,),
    )
    baseline_tokens = _estimate_tokens(baseline_rendered)
    bounded_tokens = _estimate_tokens(bounded.rendered)
    saved = baseline_tokens - bounded_tokens
    repeat = ContextCompiler(learning, token_budget=token_budget, candidate_cap=min(100, count)).compile(
        "bounded context benchmark",
        candidates,
        scope_kind="USER_PRIVATE",
        owner_id="benchmark-owner",
        mandatory_session_ids=(1,),
    )
    return {
        "workload": "authorized_recalled_context",
        "input_records": count,
        "token_budget": token_budget,
        "baseline_tokens": baseline_tokens,
        "bounded_tokens": bounded_tokens,
        "tokens_saved": saved,
        "reduction_percent": round(saved * 100 / baseline_tokens, 3) if baseline_tokens else 0.0,
        "selected_records": len(bounded.items),
        "mandatory_retained": any(item.session_id == 1 and item.mandatory for item in bounded.items),
        "budget_respected": bounded.token_count <= token_budget,
        "deterministic": bounded.manifest_hash == repeat.manifest_hash,
        "accounting": bounded.accounting,
    }


def _scope_isolation_case() -> dict[str, Any]:
    """Verify that a candidate cannot hydrate another owner's private record."""

    from core.python.aegis.context_compiler import ContextCompiler

    record = _Record(
        session_id=99,
        content_hash="private-hash",
        timestamp=1_700_000_000_099,
        scope_kind="USER_PRIVATE",
        owner_id="owner-a",
        content="private owner-a context that must never cross the owner boundary",
    )
    candidate = SimpleNamespace(segment_id=record.session_id, evidence_ref_hash=record.content_hash, score=1.0)
    learning = _Learning([record])
    compilation = ContextCompiler(learning, token_budget=512).compile(
        "owner boundary probe",
        [candidate],
        scope_kind="USER_PRIVATE",
        owner_id="owner-b",
    )
    leaked = "private owner-a context" in compilation.rendered
    repeated = ContextCompiler(learning, token_budget=512).compile(
        "owner boundary probe",
        [candidate],
        scope_kind="USER_PRIVATE",
        owner_id="owner-b",
    )
    return {
        "workload": "scope_isolation_negative_probe",
        "selected_records": len(compilation.items),
        "content_leaked": leaked,
        "oracle_pass": not leaked and not compilation.items,
        "deterministic": compilation.manifest_hash == repeated.manifest_hash,
    }


def _stale_source_case() -> dict[str, Any]:
    """Verify that a mandatory candidate with a changed hash fails closed."""

    from core.python.aegis.context_compiler import ContextCompilationError, ContextCompiler

    record = _Record(
        session_id=7,
        content_hash="current-hash",
        timestamp=1_700_000_000_007,
        scope_kind="USER_PRIVATE",
        owner_id="benchmark-owner",
        content="current authorized source",
    )
    candidate = SimpleNamespace(segment_id=record.session_id, evidence_ref_hash="stale-hash", score=1.0)
    try:
        ContextCompiler(_Learning([record]), token_budget=512).compile(
            "stale source probe",
            [candidate],
            scope_kind="USER_PRIVATE",
            owner_id="benchmark-owner",
            mandatory_session_ids=(record.session_id,),
        )
    except ContextCompilationError as error:
        return {
            "workload": "stale_mandatory_source_negative_probe",
            "error_code": error.code,
            "oracle_pass": error.code == "STALE_SOURCE",
            "deterministic": True,
        }
    return {
        "workload": "stale_mandatory_source_negative_probe",
        "error_code": None,
        "oracle_pass": False,
        "deterministic": True,
    }


def _accounting_consistency_case() -> dict[str, Any]:
    """Check benchmark accounting against the production compiler helper."""

    from core.python.aegis.context_compiler import ContextCompiler

    samples = ["ascii text", "Tiếng Việt có dấu", "emoji 🔐 and punctuation — stable"]
    pairs = [
        {
            "sample": sample,
            "benchmark_tokens": _estimate_tokens(sample),
            "production_tokens": ContextCompiler._estimate_tokens(sample),
        }
        for sample in samples
    ]
    return {
        "workload": "accounting_consistency_oracle",
        "samples": pairs,
        "oracle_pass": all(item["benchmark_tokens"] == item["production_tokens"] for item in pairs),
        "deterministic": pairs == [
            {
                "sample": sample,
                "benchmark_tokens": _estimate_tokens(sample),
                "production_tokens": ContextCompiler._estimate_tokens(sample),
            }
            for sample in samples
        ],
    }


def _selection_stability_case() -> dict[str, Any]:
    """Check order independence and monotonicity of bounded selection."""

    from core.python.aegis.context_compiler import ContextCompiler

    records = _context_records(24)
    candidates = [
        SimpleNamespace(segment_id=record.session_id, evidence_ref_hash=record.content_hash, score=1.0 - index / 24)
        for index, record in enumerate(records)
    ]
    learning = _Learning(records)
    ordered = ContextCompiler(learning, token_budget=4096).compile(
        "selection stability probe", candidates, owner_id="benchmark-owner"
    )
    reversed_order = ContextCompiler(learning, token_budget=4096).compile(
        "selection stability probe", list(reversed(candidates)), owner_id="benchmark-owner"
    )
    budgets = (1024, 2048, 4096)
    selected_by_budget = []
    for budget in budgets:
        compilation = ContextCompiler(learning, token_budget=budget).compile(
            "selection stability probe", candidates, owner_id="benchmark-owner"
        )
        selected_by_budget.append({item.session_id for item in compilation.items})
    monotonic = all(left.issubset(right) for left, right in pairwise(selected_by_budget))
    return {
        "workload": "selection_stability_oracle",
        "order_independent": ordered.manifest_hash == reversed_order.manifest_hash,
        "monotonic_with_budget": monotonic,
        "budgets": list(budgets),
        "selected_counts": [len(items) for items in selected_by_budget],
        "oracle_pass": ordered.manifest_hash == reversed_order.manifest_hash and monotonic,
        "deterministic": True,
    }


def run() -> dict[str, Any]:
    message = "Explain the memory recall and workspace graph implementation"
    path_cases = [_path_case(count, message) for count in (16, 64, 128, 512)]
    context_cases = [_context_case(count, 4096) for count in (8, 32, 64)]
    tracked_case = _tracked_workspace_case(message)
    relevance_case = _relevance_case()
    scope_case = _scope_isolation_case()
    stale_case = _stale_source_case()
    accounting_case = _accounting_consistency_case()
    stability_case = _selection_stability_case()
    cases = [
        *path_cases,
        tracked_case,
        *context_cases,
        relevance_case,
        scope_case,
        stale_case,
        accounting_case,
        stability_case,
    ]
    if not all(case.get("deterministic", False) for case in cases):
        raise RuntimeError("benchmark selection is not deterministic")
    if not all(case.get("mandatory_retained", True) for case in context_cases):
        raise RuntimeError("mandatory context source was not retained")
    if not all(case.get("budget_respected", True) for case in context_cases):
        raise RuntimeError("context compiler exceeded its token budget")
    if not tracked_case["selection_cap_respected"]:
        raise RuntimeError("workspace selector exceeded its source cap")
    if not relevance_case["oracle_pass"]:
        raise RuntimeError("relevance oracle did not retain all required paths")
    if not scope_case["oracle_pass"]:
        raise RuntimeError("scope isolation oracle failed")
    if not stale_case["oracle_pass"]:
        raise RuntimeError("stale mandatory source was not rejected")
    if not accounting_case["oracle_pass"]:
        raise RuntimeError("benchmark accounting diverged from production accounting")
    if not stability_case["oracle_pass"]:
        raise RuntimeError("context selection is not stable under order or budget changes")
    quantitative_cases = [case for case in cases if "baseline_tokens" in case and "bounded_tokens" in case]
    total_baseline = sum(int(case["baseline_tokens"]) for case in quantitative_cases)
    total_bounded = sum(int(case["bounded_tokens"]) for case in quantitative_cases)
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "commit": _commit(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "accounting": ACCOUNTING,
        "scope": "provider-neutral prompt/context accounting; no network calls",
        "quantitative_case_count": len(quantitative_cases),
        "oracle_case_count": len(cases) - len(quantitative_cases),
        "cases": cases,
        "aggregate": {
            "baseline_tokens": total_baseline,
            "bounded_tokens": total_bounded,
            "tokens_saved": total_baseline - total_bounded,
            "reduction_percent": round((total_baseline - total_bounded) * 100 / total_baseline, 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON evidence to this path")
    args = parser.parse_args()
    started = time.perf_counter()
    result = run()
    result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
