"""Static CI linter banning toy Rust and Python benchmark anti-patterns."""

from __future__ import annotations

import argparse
import ast
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_TIMER_CALL_NAMES = frozenset(
    {
        "perf_counter",
        "perf_counter_ns",
        "monotonic",
        "monotonic_ns",
        "process_time",
        "process_time_ns",
    }
)
_TRACEMALLOC_CALL_NAMES = frozenset(
    {
        "start",
        "get_traced_memory",
        "reset_peak",
        "take_snapshot",
    }
)
_AEGIS_IMPORT_PREFIXES = ("aegis_cognition", "aegis_nerve", "core", "aegis")


@dataclass(frozen=True)
class LintFinding:
    rule_id: str
    language: str
    path: str
    line: int
    message: str


@dataclass(frozen=True)
class LintReport:
    schema: str
    ok: bool
    rust_files_checked: int
    python_files_checked: int
    findings: tuple[LintFinding, ...]


def _mask_rust_comments_and_strings(source: str) -> str:
    """Mask Rust comments and string/char literals with spaces while preserving line numbers."""

    out = list(source)
    n = len(source)
    i = 0
    while i < n:
        ch = source[i]
        # Line comment
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                out[i] = " "
                i += 1
            continue
        # Nested block comment
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            depth = 1
            while i < n and depth > 0:
                if source[i] == "/" and i + 1 < n and source[i + 1] == "*":
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                    depth += 1
                elif source[i] == "*" and i + 1 < n and source[i + 1] == "/":
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                    depth -= 1
                else:
                    if source[i] != "\n":
                        out[i] = " "
                    i += 1
            continue
        # Raw string r#"..."# / br#"..."#
        raw_start = i
        if ch in {"r", "b"}:
            j = i + 1
            if ch == "b" and j < n and source[j] == "r":
                j += 1
            elif ch == "b":
                j = -1
            if j != -1:
                hashes = 0
                while j < n and source[j] == "#":
                    hashes += 1
                    j += 1
                if j < n and source[j] == '"':
                    j += 1
                    while j < n:
                        if source[j] == '"':
                            matched = 0
                            while j + 1 + matched < n and matched < hashes and source[j + 1 + matched] == "#":
                                matched += 1
                            if matched == hashes:
                                end = j + 1 + hashes
                                for k in range(raw_start, end):
                                    if out[k] != "\n":
                                        out[k] = " "
                                out[raw_start] = '"'
                                out[end - 1] = '"'
                                i = end
                                break
                        j += 1
                    else:
                        for k in range(raw_start, n):
                            if out[k] != "\n":
                                out[k] = " "
                        i = n
                    continue
        # Normal / byte string literal
        if ch == '"' or (ch == "b" and i + 1 < n and source[i + 1] == '"'):
            str_start = i
            if ch == "b":
                out[i] = " "
                i += 1
            i += 1
            while i < n:
                if source[i] == "\\" and i + 1 < n:
                    if out[i] != "\n":
                        out[i] = " "
                    if out[i + 1] != "\n":
                        out[i + 1] = " "
                    i += 2
                    continue
                if source[i] == '"':
                    i += 1
                    break
                if out[i] != "\n":
                    out[i] = " "
                i += 1
            if str_start + 1 < i - 1:
                out[str_start] = '"'
            continue
        # Char literal vs lifetime ('a / 'static vs '\'' / 'x')
        if ch == "'" and i + 2 < n:
            if source[i + 1] == "\\":
                j = i + 2
                while j < n and source[j] != "'" and source[j] != "\n":
                    j += 1
                if j < n and source[j] == "'":
                    for k in range(i + 1, j):
                        out[k] = " "
                    i = j + 1
                    continue
            elif source[i + 2] == "'":
                out[i + 1] = " "
                i += 3
                continue
        i += 1
    return "".join(out)


def _line_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _extract_balanced_parens(text: str, open_paren_idx: int) -> tuple[str, int] | None:
    if open_paren_idx >= len(text) or text[open_paren_idx] != "(":
        return None
    depth = 1
    i = open_paren_idx + 1
    n = len(text)
    while i < n and depth > 0:
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_idx + 1 : i], i + 1
        i += 1
    return None


def _split_top_level_args(arg_text: str) -> list[tuple[str, int]]:
    """Split comma-separated arguments at nesting depth 0, tracking closure param pipes."""

    args: list[tuple[str, int]] = []
    paren = 0
    brace = 0
    bracket = 0
    in_pipes = False
    start = 0
    n = len(arg_text)
    i = 0
    while i < n:
        c = arg_text[i]
        if c == "(":
            paren += 1
        elif c == ")":
            paren = max(0, paren - 1)
        elif c == "{":
            brace += 1
        elif c == "}":
            brace = max(0, brace - 1)
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket = max(0, bracket - 1)
        elif c == "|" and paren == 0 and brace == 0 and bracket == 0:
            if i + 1 < n and arg_text[i + 1] == "|":
                i += 1
            else:
                in_pipes = not in_pipes
        elif c == "," and paren == 0 and brace == 0 and bracket == 0 and not in_pipes:
            args.append((arg_text[start:i], start))
            start = i + 1
        i += 1
    args.append((arg_text[start:], start))
    return args


def lint_rust_bench_source(source: str, path: str = "<mem>") -> list[LintFinding]:
    """Check a Rust Criterion benchmark source against Phase 0 anti-toy rules."""

    masked = _mask_rust_comments_and_strings(source)
    findings: list[LintFinding] = []

    # 1. Check sample_size(n) < 100 outside explicit quick-dev guards.
    for match in re.finditer(r"\.\s*sample_size\s*\(", masked):
        open_idx = match.end() - 1
        extracted = _extract_balanced_parens(masked, open_idx)
        if extracted is None:
            continue
        arg_text, _ = extracted
        int_match = re.fullmatch(r"\s*([0-9][0-9_]*)\s*", arg_text)
        if int_match:
            val = int(int_match.group(1).replace("_", ""))
            if val < 100:
                context_window = source[max(0, match.start() - 350) : match.start()]
                if "AEGIS_BENCH_QUICK" not in context_window:
                    findings.append(
                        LintFinding(
                            rule_id="RUST_SMALL_SAMPLE_SIZE",
                            language="rust",
                            path=path,
                            line=_line_for_offset(masked, match.start()),
                            message=f"Criterion sample_size({val}) < 100 is rejected outside AEGIS_BENCH_QUICK guard.",
                        )
                    )

    # 2. Check discarded Results (`let _ =`).
    findings.extend(
        LintFinding(
            rule_id="RUST_DISCARDED_RESULT",
            language="rust",
            path=path,
            line=_line_for_offset(masked, match.start()),
            message="Benchmark uses `let _ =` discarding a fallible Result or value; handle or check explicitly.",
        )
        for match in re.finditer(r"\blet\s+_\s*=", masked)
    )

    # 3. Inspect Criterion timed measurement bodies (`.iter(...)`, `.iter_custom(...)`, `.iter_batched*(setup, routine, ...)`).
    timed_checks = (
        (r"\.\s*clone\s*\(", "RUST_CLONE_IN_ITER", "`.clone()` inside Criterion timed measurement closure."),
        (r"\bvec\s*!", "RUST_VEC_MACRO_IN_ITER", "`vec![]` allocation inside Criterion timed measurement closure."),
        (
            r"\bformat\s*!",
            "RUST_FORMAT_MACRO_IN_ITER",
            "`format!()` allocation inside Criterion timed measurement closure.",
        ),
        (
            r"\bremove_dir_all\b",
            "RUST_FS_DELETE_IN_ITER",
            "`remove_dir_all` inside Criterion timed measurement closure.",
        ),
        (
            r"\bbench_hash\s*\(",
            "RUST_HASH_IN_ITER",
            "`bench_hash()` inside Criterion timed measurement closure.",
        ),
    )

    for match in re.finditer(r"\.\s*(iter_batched_ref|iter_batched|iter_custom|iter)\s*\(", masked):
        method = match.group(1)
        open_idx = match.end() - 1
        extracted = _extract_balanced_parens(masked, open_idx)
        if extracted is None:
            continue
        arg_text, _ = extracted
        args = _split_top_level_args(arg_text)
        if method in {"iter", "iter_custom"}:
            if not arg_text.strip():
                # Iterator::iter() takes 0 args; skip non-Criterion iterator calls.
                continue
            routine_body, routine_rel_offset = args[0]
        else:
            if len(args) < 2:
                continue
            routine_body, routine_rel_offset = args[1]

        routine_abs_offset = open_idx + 1 + routine_rel_offset
        if not routine_body.strip():
            continue

        for pattern, rule_id, desc in timed_checks:
            findings.extend(
                LintFinding(
                    rule_id=rule_id,
                    language="rust",
                    path=path,
                    line=_line_for_offset(masked, routine_abs_offset + hit.start()),
                    message=desc,
                )
                for hit in re.finditer(pattern, routine_body)
            )

        if not re.search(r"\bblack_box\s*\(", routine_body):
            findings.append(
                LintFinding(
                    rule_id="RUST_MISSING_BLACK_BOX",
                    language="rust",
                    path=path,
                    line=_line_for_offset(masked, routine_abs_offset),
                    message=f"Criterion `{method}` measurement closure is missing `black_box(...)` barrier.",
                )
            )

    return findings


def _collect_docstring_nodes(tree: ast.AST) -> set[int]:
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_ids.add(id(first.value))
    return docstring_ids


def _has_aegis_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _AEGIS_IMPORT_PREFIXES or alias.name.startswith("core.python"):
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in _AEGIS_IMPORT_PREFIXES or node.module.startswith("core.python"):
                return True
    return False


def _call_attr_and_module(node: ast.Call) -> tuple[str | None, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else None
        return owner, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, ""


def _scope_contains_timer(nodes: Sequence[ast.stmt]) -> bool:
    for stmt in nodes:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Call):
                owner, attr = _call_attr_and_module(child)
                if attr in _TIMER_CALL_NAMES and owner in {None, "time"}:
                    return True
    return False


def _scope_contains_tracemalloc(nodes: Sequence[ast.stmt]) -> int | None:
    for stmt in nodes:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Call):
                owner, attr = _call_attr_and_module(child)
                if owner == "tracemalloc" and attr in _TRACEMALLOC_CALL_NAMES:
                    return child.lineno
    return None


def _is_benchmark_or_eval_script(rel_path: str) -> bool:
    name = Path(rel_path).name
    if name in {
        "anti_toy_benchmark_lint.py",
        "benchmark_gate.py",
        "benchmark_preflight.py",
        "benchmark_wrapper.py",
    }:
        return name == "benchmark_gate.py"
    return (
        "benchmark" in name
        or name.endswith("_baseline_gate.py")
        or name == "extreme_testing_suite.py"
        or "live_eval" in name
    )


def _is_cross_arm_comparator_path(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    name = Path(normalized).name
    return (
        name == "sota_baseline_gate.py"
        or "/research/comparators/" in normalized
        or (name.endswith("_baseline_gate.py"))
        or name
        in {
            "extreme_testing_suite.py",
            "resource_policy_benchmark.py",
            "communication_payload_benchmark.py",
            "memory_context_benchmark.py",
            "memory_context_live_eval.py",
        }
    )


def lint_python_benchmark_source(
    source: str,
    path: str = "<mem>",
    *,
    is_cross_arm_comparator: bool = False,
) -> list[LintFinding]:
    """Check a Python benchmark/comparator script against Phase 0 anti-toy rules."""

    tree = ast.parse(source, filename=path)
    docstring_ids = _collect_docstring_nodes(tree)
    has_aegis = _has_aegis_import(tree)
    findings: list[LintFinding] = []

    # 1. Fail if PROVEN / NV-* claims are emitted while importing zero AEGIS modules.
    proven_lines: list[int] = []
    for node in ast.walk(tree):
        if id(node) in docstring_ids:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "PROVEN":
            proven_lines.append(node.lineno)
    if proven_lines and not has_aegis:
        findings.append(
            LintFinding(
                rule_id="PYTHON_PROVEN_WITHOUT_AEGIS_IMPORT",
                language="python",
                path=path,
                line=proven_lines[0],
                message='Benchmark/gate emits "PROVEN" while importing zero AEGIS modules.',
            )
        )

    # 2. Fail if tracemalloc is active inside any function or loop that measures latency/wall/cpu time.
    scopes: list[tuple[str, Sequence[ast.stmt]]] = [("<module>", tree.body)]
    scopes.extend(
        (node.name, node.body) for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for scope_name, body in scopes:
        if scope_name == "<module>":
            top_stmts = [s for s in body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            trace_line = _scope_contains_tracemalloc(top_stmts)
            if trace_line is not None and _scope_contains_timer(top_stmts):
                findings.append(
                    LintFinding(
                        rule_id="PYTHON_TRACEMALLOC_IN_TIMED_LOOP",
                        language="python",
                        path=path,
                        line=trace_line,
                        message="tracemalloc instrumentation shares top-level scope with latency timers.",
                    )
                )
        else:
            trace_line = _scope_contains_tracemalloc(body)
            if trace_line is not None and _scope_contains_timer(body):
                findings.append(
                    LintFinding(
                        rule_id="PYTHON_TRACEMALLOC_IN_TIMED_LOOP",
                        language="python",
                        path=path,
                        line=trace_line,
                        message=f"tracemalloc runs inside timed function `{scope_name}`; separate allocation pass from timing.",
                    )
                )

    # 3. Fail if cross-arm comparator reads Criterion estimates.json / target/criterion.
    if is_cross_arm_comparator:
        for node in ast.walk(tree):
            if id(node) in docstring_ids:
                continue
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (node.value == "estimates.json" or "estimates.json" in node.value or node.value == "criterion")
            ):
                findings.append(
                    LintFinding(
                        rule_id="PYTHON_CRITERION_CACHE_CROSS_ARM",
                        language="python",
                        path=path,
                        line=node.lineno,
                        message="Cross-arm comparator must not read cached Criterion `estimates.json`.",
                    )
                )
            elif isinstance(node, ast.FunctionDef) and node.name == "_criterion_estimate_ns":
                findings.append(
                    LintFinding(
                        rule_id="PYTHON_CRITERION_CACHE_CROSS_ARM",
                        language="python",
                        path=path,
                        line=node.lineno,
                        message="Cross-arm comparator defines forbidden `_criterion_estimate_ns` cache reader.",
                    )
                )

    return findings


def run_lint(root: Path = ROOT) -> LintReport:
    """Lint all Rust benches under `core/rust/benches` and Python benchmark/comparator scripts."""

    findings: list[LintFinding] = []
    rust_files: list[Path] = []
    benches_dir = root / "core" / "rust" / "benches"
    if benches_dir.exists():
        rust_files = sorted(benches_dir.rglob("*.rs"))
    for rpath in rust_files:
        rel = rpath.relative_to(root).as_posix()
        findings.extend(lint_rust_bench_source(rpath.read_text(encoding="utf-8"), path=rel))

    python_files: list[Path] = []
    scripts_dir = root / "scripts"
    if scripts_dir.exists():
        for ppath in sorted(scripts_dir.rglob("*.py")):
            rel = ppath.relative_to(root).as_posix()
            if _is_benchmark_or_eval_script(rel) or _is_cross_arm_comparator_path(rel):
                python_files.append(ppath)
                findings.extend(
                    lint_python_benchmark_source(
                        ppath.read_text(encoding="utf-8"),
                        path=rel,
                        is_cross_arm_comparator=_is_cross_arm_comparator_path(rel),
                    )
                )

    return LintReport(
        schema="aegis-anti-toy-benchmark-lint-v1",
        ok=not findings,
        rust_files_checked=len(rust_files),
        python_files_checked=len(python_files),
        findings=tuple(findings),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static CI anti-toy benchmark linter")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = run_lint(args.root)
    if args.json:
        payload = {
            "schema": report.schema,
            "ok": report.ok,
            "rust_files_checked": report.rust_files_checked,
            "python_files_checked": report.python_files_checked,
            "finding_count": len(report.findings),
            "findings": [asdict(f) for f in report.findings],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"anti_toy_benchmark_lint: ok={report.ok} "
            f"rust_files={report.rust_files_checked} python_files={report.python_files_checked} "
            f"findings={len(report.findings)}"
        )
        for item in report.findings:
            print(f"  [{item.rule_id}] {item.path}:{item.line}: {item.message}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
