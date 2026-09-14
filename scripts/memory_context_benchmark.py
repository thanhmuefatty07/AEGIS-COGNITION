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
    except OSError, subprocess.CalledProcessError:
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
        "deterministic": bounded.manifest_hash == repeat.manifest_hash,
        "accounting": bounded.accounting,
    }


def run() -> dict[str, Any]:
    message = "Explain the memory recall and workspace graph implementation"
    path_cases = [_path_case(count, message) for count in (16, 64, 128, 512)]
    context_cases = [_context_case(count, 4096) for count in (8, 32, 64)]
    cases = [*path_cases, *context_cases]
    if not all(case["deterministic"] for case in cases):
        raise RuntimeError("benchmark selection is not deterministic")
    if not all(case.get("mandatory_retained", True) for case in context_cases):
        raise RuntimeError("mandatory context source was not retained")
    total_baseline = sum(int(case["baseline_tokens"]) for case in cases)
    total_bounded = sum(int(case["bounded_tokens"]) for case in cases)
    return {
        "schema": SCHEMA,
        "status": "PASS",
        "commit": _commit(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "accounting": ACCOUNTING,
        "scope": "provider-neutral prompt/context accounting; no network calls",
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
