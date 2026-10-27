"""One AESE façade shared by runtime, desktop, CLI and future API callers."""

from __future__ import annotations

import ast
import re
import textwrap
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .contracts import (
    ArtifactReference,
    ContractMetadata,
    DevelopmentVerificationSession,
    ExecutionReceipt,
    ProjectProfile,
    TestChangeProposal,
    VerificationAssessment,
    VerificationEvent,
    VerificationPlan,
    VerificationRequirement,
    canonical_hash,
)
from .discovery import inspect_project as discover_project
from .session import (
    SessionState,
    VerificationSessionError,
    compile_requirements,
    validate_change_paths,
)


AESE_MODE = "SHADOW"
SELECTIVE_TEST_AUTHORITY = "DISABLED"
TEST_SKIPPING_AUTHORITY = "DISABLED"
EVIDENCE_PROMOTION = "DISABLED"


@dataclass(frozen=True)
class AgentImplementationPacket:
    """Bounded information sent before source implementation begins."""

    session_id: str
    project_profile: dict[str, object]
    requirements: tuple[dict[str, object], ...]
    plan: dict[str, object]
    known_risks: tuple[str, ...]
    rules: tuple[str, ...]
    feedback_cursor: int
    source_revision: str
    can_start: bool
    authority_state: str = "SHADOW_ONLY"


@dataclass
class _SessionRecord:
    profile: ProjectProfile
    requirements: tuple[VerificationRequirement, ...]
    plan: VerificationPlan
    session: DevelopmentVerificationSession
    events: list[VerificationEvent]
    proposals: dict[str, TestChangeProposal]
    feedback: list[dict[str, object]]
    runs: dict[str, dict[str, object]]
    receipts: dict[str, ExecutionReceipt]
    artifacts: dict[str, ArtifactReference]


def _rebind[T: ContractMetadata](contract: T, **changes: object) -> T:
    plan_hash = cast(str, changes.get("plan_hash", contract.plan_hash))
    payload = dict(changes)
    payload["contract_hash"] = ""
    payload["plan_hash"] = plan_hash
    return replace(contract, **payload)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


_ORACLE_PATTERN = re.compile(
    r"\b(assert|assert!|assert_eq!|assert_ne!|expect\s*\(|should\.|to_equal\(|t\.(?:error|fatal|fail)|Assert\.|assertThat\(|assertEquals\(|assertTrue\()"
)
_WEAKENING_DIRECTIVE_PATTERN = re.compile(
    r"(?:@\s*|\b(?:pytest\.mark|pytest|unittest|it|test|describe)\s*\.\s*)(?:skip|skipIf|skipUnless|xfail)\b"
    r"|\b(?:xfail|xskip|xdescribe|xit|xtest|TODO|FIXME)\b"
    r"|#\s*\[\s*ignore\b",
    flags=re.IGNORECASE,
)
_TAUTOLOGICAL_ORACLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bassert\s*\(?\s*(?:True|not\s+False|1|\"[^\"]+\"|'[^']+')\s*\)?(?:\s*[,#]|$)"),
    re.compile(r"\bassert\s+([A-Za-z0-9_.\"']+)\s*(?:==|is|<=|>=)\s*\1(?:\b|\s*[,#]|$)"),
    re.compile(r"\bassert_eq!\s*\(\s*([^,()]+?)\s*,\s*\1\s*\)"),
    re.compile(r"\b(?:assertEquals|assertSame)\s*\(\s*([^,()]+?)\s*,\s*\1\s*\)"),
    re.compile(r"\bassertTrue\s*\(\s*(?:true|True|1\s*==\s*1)\s*\)"),
    re.compile(r"\bexpect\s*\(\s*([^()]+?)\s*\)\s*\.\s*(?:toBe|toEqual|toStrictEqual)\s*\(\s*\1\s*\)"),
)
_RUNNER_SPOOFING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:os\._exit|sys\.exit|process\.exit|std::process::exit)\s*\("),
    re.compile(r"\bpytest\.exit\s*\("),
    re.compile(
        r"\b(?:--junitxml|--override-ini|PYTEST_ADDOPTS|PYTEST_PLUGINS|pytest_runtest_makereport|pytest_sessionfinish|pytest_terminal_summary)\b"
    ),
    re.compile(
        r"\b(?:print|write|println!|eprintln!|console\.log)\b[^\n]*(?:test result:\s*ok\.|[1-9]\d*\s+passed\s+in\s+\d)"
    ),
    re.compile(
        r"\bexcept(?:\s+[^:\n]*\b(?:AssertionError|BaseException|Exception)\b[^:\n]*)?\s*:\s*(?:pass\b|\.\.\.|return\b)"
    ),
    re.compile(r"\bsuppress\s*\([^)\n]*\b(?:AssertionError|BaseException|Exception)\b"),
)
_TEST_SLEEP_BLOAT_PATTERN = re.compile(
    r"\b(?:time\.sleep|asyncio\.sleep)\s*\(\s*(?:[1-9]\d*(?:\.\d+)?|0\.[5-9]\d*)\s*\)"
)
_TEST_FN_DEF_PATTERN = re.compile(r"^\s*(?:async\s+def|def|fn)\s+(test_[A-Za-z0-9_]+)\s*\(")


def _scrub_comments_and_strings(text: str, *, scrub_strings: bool) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    state = "CODE"
    while i < n:
        ch = text[i]
        if state == "CODE":
            if text.startswith('"""', i):
                state = "TRIPLE_DOUBLE"
                out.append('""' if scrub_strings else '"""')
                i += 3
                continue
            if text.startswith("'''", i):
                state = "TRIPLE_SINGLE"
                out.append("''" if scrub_strings else "'''")
                i += 3
                continue
            if text.startswith("//", i):
                state = "LINE_COMMENT"
                i += 2
                continue
            if text.startswith("/*", i):
                state = "BLOCK_COMMENT"
                i += 2
                continue
            if ch == "#":
                state = "LINE_COMMENT"
                i += 1
                continue
            if ch == '"':
                state = "DOUBLE"
                out.append('"')
                i += 1
                continue
            if ch == "'":
                state = "SINGLE"
                out.append("'")
                i += 1
                continue
            if ch == "`":
                state = "BACKTICK"
                out.append("`")
                i += 1
                continue
            out.append(ch)
            i += 1
        elif state == "LINE_COMMENT":
            if ch == "\n":
                state = "CODE"
                out.append("\n")
            i += 1
        elif state == "BLOCK_COMMENT":
            if text.startswith("*/", i):
                state = "CODE"
                i += 2
                continue
            if ch == "\n":
                out.append("\n")
            i += 1
        elif state == "TRIPLE_DOUBLE":
            if ch == "\\" and i + 1 < n:
                if not scrub_strings:
                    out.append(text[i : i + 2])
                i += 2
                continue
            if text.startswith('"""', i):
                state = "CODE"
                if not scrub_strings:
                    out.append('"""')
                i += 3
                continue
            if ch == "\n":
                out.append("\n")
            elif not scrub_strings:
                out.append(ch)
            i += 1
        elif state == "TRIPLE_SINGLE":
            if ch == "\\" and i + 1 < n:
                if not scrub_strings:
                    out.append(text[i : i + 2])
                i += 2
                continue
            if text.startswith("'''", i):
                state = "CODE"
                if not scrub_strings:
                    out.append("'''")
                i += 3
                continue
            if ch == "\n":
                out.append("\n")
            elif not scrub_strings:
                out.append(ch)
            i += 1
        elif state in ("SINGLE", "DOUBLE", "BACKTICK"):
            quote = "'" if state == "SINGLE" else ('"' if state == "DOUBLE" else "`")
            if ch == "\\" and i + 1 < n:
                if not scrub_strings:
                    out.append(text[i : i + 2])
                i += 2
                continue
            if ch == quote:
                state = "CODE"
                out.append(quote)
                i += 1
                continue
            if ch == "\n":
                if state != "BACKTICK":
                    state = "CODE"
                    out.append(quote)
                out.append("\n")
            elif not scrub_strings:
                out.append(ch)
            i += 1
    return "".join(out)


def _extract_patch_raw_lines(patch_text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw_lines = patch_text.splitlines()
    non_empty = [line for line in raw_lines if line.strip()]
    has_diff_headers = any(line.startswith(("@@", "diff --git", "--- ", "+++ ")) for line in non_empty)
    has_diff_markers = any(len(line) > 1 and line[0] in "+-" and line[1] not in "+-" for line in non_empty)
    all_lines_valid_diff = bool(non_empty) and all(
        line.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "+", "-", " ", "\\")) for line in non_empty
    )
    is_unified_diff = has_diff_headers or (has_diff_markers and all_lines_valid_diff)
    added_raw: list[str] = []
    removed_raw: list[str] = []
    for raw in raw_lines:
        if raw.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "\\")):
            continue
        if is_unified_diff:
            if raw.startswith("+"):
                added_raw.append(raw[1:])
            elif raw.startswith("-"):
                removed_raw.append(raw[1:])
            elif not raw.startswith(" ") and raw.strip():
                # Malformed unprefixed line in a diff hunk is treated as added so it cannot smuggle code
                added_raw.append(raw)
        else:
            added_raw.append(raw[1:] if len(raw) > 1 and raw[0] == "+" and raw[1] != "+" else raw)
    return tuple(added_raw), tuple(removed_raw)


def _extract_patch_code_lines(patch_text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    added_raw, removed_raw = _extract_patch_raw_lines(patch_text)
    added_scrubbed = _scrub_comments_and_strings("\n".join(added_raw), scrub_strings=False)
    removed_scrubbed = _scrub_comments_and_strings("\n".join(removed_raw), scrub_strings=False)
    added = tuple(line.rstrip() for line in added_scrubbed.splitlines() if line.strip())
    removed = tuple(line.rstrip() for line in removed_scrubbed.splitlines() if line.strip())
    return added, removed


def _analyze_python_ast_hazards(added_raw: tuple[str, ...]) -> tuple[bool, bool]:
    source = textwrap.dedent("\n".join(added_raw)).strip()
    if not source:
        return False, False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False, False

    has_tautology = False
    has_spoofing = False

    def _is_pure_constant_expr(node: ast.AST) -> bool:
        return not any(
            isinstance(
                child,
                (
                    ast.Name,
                    ast.Call,
                    ast.Attribute,
                    ast.Subscript,
                    ast.Await,
                    ast.Yield,
                    ast.YieldFrom,
                    ast.NamedExpr,
                ),
            )
            for child in ast.walk(node)
        )

    def _resolve_expr(node: ast.AST, aliases: dict[str, str]) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, f"NAME:{node.id}")
        return ast.dump(node)

    def _check_statements(stmts: list[ast.stmt], parent_aliases: dict[str, str]) -> None:
        nonlocal has_tautology
        aliases = dict(parent_aliases)
        for stmt in stmts:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target_id = stmt.targets[0].id
                if isinstance(stmt.value, ast.Name):
                    aliases[target_id] = aliases.get(stmt.value.id, f"NAME:{stmt.value.id}")
                elif _is_pure_constant_expr(stmt.value):
                    aliases[target_id] = ast.dump(stmt.value)
                else:
                    aliases[target_id] = f"VAR:{target_id}:{id(stmt)}"
            elif isinstance(stmt, ast.Assert):
                if _is_pure_constant_expr(stmt.test):
                    has_tautology = True
                elif (
                    isinstance(stmt.test, ast.Compare)
                    and len(stmt.test.ops) == 1
                    and len(stmt.test.comparators) == 1
                    and isinstance(stmt.test.ops[0], (ast.Eq, ast.Is, ast.LtE, ast.GtE))
                ):
                    lhs = _resolve_expr(stmt.test.left, aliases)
                    rhs = _resolve_expr(stmt.test.comparators[0], aliases)
                    if lhs == rhs:
                        has_tautology = True
            elif isinstance(
                stmt,
                (
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                    ast.If,
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.With,
                    ast.AsyncWith,
                ),
            ):
                _check_statements(stmt.body, aliases)

    _check_statements(tree.body, {})

    exit_modules = {"sys", "os", "pytest"}
    exit_callables = {"exit", "_exit"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"sys", "os", "pytest"}:
                    exit_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module in {"sys", "os", "pytest", "builtins", "posix", "nt"}:
            for alias in node.names:
                if alias.name in {"exit", "_exit"}:
                    exit_callables.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in exit_modules
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in {"exit", "_exit"}
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"exit", "_exit"}
                    and (
                        (isinstance(node.func.value, ast.Name) and node.func.value.id in exit_modules)
                        or (
                            isinstance(node.func.value, ast.Call)
                            and isinstance(node.func.value.func, ast.Name)
                            and node.func.value.func.id == "__import__"
                        )
                    )
                )
                or (isinstance(node.func, ast.Name) and node.func.id in exit_callables)
            ):
                has_spoofing = True
        elif isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if (isinstance(exc, ast.Name) and exc.id == "SystemExit") or (
                isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit"
            ):
                has_spoofing = True
        elif isinstance(node, ast.ExceptHandler):
            catches_assertion = node.type is None or any(
                isinstance(child, ast.Name) and child.id in {"AssertionError", "Exception", "BaseException"}
                for child in ast.walk(node.type)
            )
            if (
                catches_assertion
                and node.body
                and all(
                    isinstance(stmt, (ast.Pass, ast.Return))
                    or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
                    for stmt in node.body
                )
            ):
                has_spoofing = True

    return has_tautology, has_spoofing


def _line_has_tautological_oracle(line: str) -> bool:
    return any(pattern.search(line) is not None for pattern in _TAUTOLOGICAL_ORACLE_PATTERNS)


def _contains_oracle(patch_text: str) -> bool:
    if _contains_tautological_oracle(patch_text):
        return False
    added_raw, _ = _extract_patch_raw_lines(patch_text)
    scrubbed_text = _scrub_comments_and_strings("\n".join(added_raw), scrub_strings=True)
    return any(_ORACLE_PATTERN.search(line) for line in scrubbed_text.splitlines())


def _contains_tautological_oracle(patch_text: str) -> bool:
    added_lines, _ = _extract_patch_code_lines(patch_text)
    if any(_line_has_tautological_oracle(line) for line in added_lines):
        return True
    added_raw, _ = _extract_patch_raw_lines(patch_text)
    ast_tautology, _ = _analyze_python_ast_hazards(added_raw)
    return ast_tautology


def _contains_runner_spoofing(patch_text: str) -> bool:
    added_lines, _ = _extract_patch_code_lines(patch_text)
    joined = "\n".join(added_lines)
    if any(pattern.search(joined) is not None for pattern in _RUNNER_SPOOFING_PATTERNS):
        return True
    added_raw, _ = _extract_patch_raw_lines(patch_text)
    _, ast_spoofing = _analyze_python_ast_hazards(added_raw)
    return ast_spoofing


def _contains_test_bloat(patch_text: str) -> bool:
    added_lines, _ = _extract_patch_code_lines(patch_text)
    seen_test_names: set[str] = set()
    for line in added_lines:
        if _TEST_SLEEP_BLOAT_PATTERN.search(line):
            return True
        match = _TEST_FN_DEF_PATTERN.match(line)
        if match is not None:
            test_name = match.group(1)
            if test_name in seen_test_names:
                return True
            seen_test_names.add(test_name)
    return False


def _contains_weakening(patch_text: str) -> bool:
    added_raw, removed_raw = _extract_patch_raw_lines(patch_text)
    added_no_strings = _scrub_comments_and_strings("\n".join(added_raw), scrub_strings=True)
    if _WEAKENING_DIRECTIVE_PATTERN.search(added_no_strings) or _WEAKENING_DIRECTIVE_PATTERN.search(
        "\n".join(added_raw)
    ):
        return True
    if _contains_tautological_oracle(patch_text):
        return True
    if _contains_runner_spoofing(patch_text):
        return True
    if _contains_test_bloat(patch_text):
        return True
    removed_no_strings = _scrub_comments_and_strings("\n".join(removed_raw), scrub_strings=True)
    removed_oracle_count = sum(1 for line in removed_no_strings.splitlines() if _ORACLE_PATTERN.search(line))
    added_oracle_count = sum(1 for line in added_no_strings.splitlines() if _ORACLE_PATTERN.search(line))
    return removed_oracle_count > added_oracle_count


def _candidate_test_hints_for_paths(changed_paths: tuple[str, ...], root_path: str) -> tuple[str, ...]:
    hints: list[str] = []
    root = Path(root_path) if root_path else None
    for rel_path in changed_paths:
        posix = Path(rel_path).as_posix()
        lowered = posix.casefold()
        if lowered.startswith("tests/") or "/tests/" in lowered or Path(posix).name.startswith("test_"):
            if posix not in hints:
                hints.append(posix)
            continue
        stem = Path(posix).stem
        if not stem or stem == "__init__":
            continue
        candidate = f"tests/test_{stem}.py"
        if root is not None:
            try:
                if (root / candidate).is_file() and candidate not in hints:
                    hints.append(candidate)
            except OSError:
                pass
            continue
        if candidate not in hints:
            hints.append(candidate)
    return tuple(hints[:16])


class VerificationFacade:
    """In-memory AESE control plane for the first integrated vertical slice.

    The store is intentionally process-local in this phase.  It is a control
    plane seam, not a replacement for the existing durable Lab ledger.  A
    future durable implementation must preserve the same contracts and
    replay rules rather than add a second execution authority.
    """

    execution_authority = "EXISTING_LAB_EXECUTION_CELL_REGISTRY_AND_TOOL_CALL_PATH"

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRecord] = {}

    def inspect_project(self, root: str | Path, *, project_id: str | None = None) -> ProjectProfile:
        return discover_project(Path(root), project_id=project_id)

    def create_contract(
        self,
        task_description: str,
        *,
        expected_behavior: str | None = None,
        source_revision: str = "UNKNOWN",
        policy_hash: str = "UNKNOWN",
    ) -> tuple[VerificationRequirement, ...]:
        compiled = compile_requirements(
            task_description,
            expected_behavior=expected_behavior,
            source_revision=source_revision,
            policy_hash=policy_hash,
        )
        return compiled.requirements

    def start_session(
        self,
        profile: ProjectProfile,
        requirements: tuple[VerificationRequirement, ...] | list[VerificationRequirement],
        *,
        risk_level: str = "UNKNOWN",
    ) -> DevelopmentVerificationSession:
        profile.validate()
        typed_requirements = tuple(requirements)
        if not typed_requirements:
            raise VerificationSessionError("a verification session requires a contract")
        for requirement in typed_requirements:
            requirement.validate()
        session_id = _new_id("aese-session")
        plan = VerificationPlan(
            session_id=session_id,
            source_revision=profile.source_revision,
            policy_hash=profile.policy_hash,
            plan_id=_new_id("aese-plan"),
            requirement_ids=tuple(requirement.requirement_id for requirement in typed_requirements),
            test_ids=(),
            required_test_classes=tuple(
                sorted(
                    {
                        test_class
                        for requirement in typed_requirements
                        for test_class in requirement.required_test_classes
                    }
                )
            ),
            selection_mode="SHADOW_ONLY",
            widened=True,
            widening_reasons=("passive_discovery_only", "unknown_dependency_closure", "selection_authority_disabled"),
            risk_level=risk_level,
            execution_policy="LOCAL_DEFAULT_LAB_AUTHORITY",
        )
        bound_requirements = tuple(
            _rebind(
                requirement,
                session_id=session_id,
                plan_hash=plan.plan_hash,
                source_revision=profile.source_revision,
                policy_hash=profile.policy_hash,
            )
            for requirement in typed_requirements
        )
        state = SessionState.CONTRACT_READY.value
        session = DevelopmentVerificationSession(
            session_id=session_id,
            project_id=profile.project_id,
            baseline_revision=profile.source_revision,
            current_revision=profile.source_revision,
            requirement_ids=tuple(requirement.requirement_id for requirement in bound_requirements),
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            policy_hash=profile.policy_hash,
            source_revision=profile.source_revision,
            status=state,
            state=state,
        )
        record = _SessionRecord(
            profile=profile,
            requirements=bound_requirements,
            plan=plan,
            session=session,
            events=[],
            proposals={},
            feedback=[],
            runs={},
            receipts={},
            artifacts={},
        )
        self._sessions[session_id] = record
        self._event(record, "SESSION_STARTED", {"state": state, "plan_hash": plan.plan_hash})
        return session

    def get_agent_packet(self, session_id: str) -> AgentImplementationPacket:
        record = self._record(session_id)
        incomplete = any(requirement.compilation_status != "READY" for requirement in record.requirements)
        self._update_session(record, state=SessionState.PACKET_READY.value, status=SessionState.PACKET_READY.value)
        packet = AgentImplementationPacket(
            session_id=session_id,
            project_profile=record.profile.as_dict(),
            requirements=tuple(requirement.as_dict() for requirement in record.requirements),
            plan=record.plan.as_dict(),
            known_risks=(
                "selection_authority_disabled",
                "test_skipping_authority_disabled",
                "evidence_promotion_disabled",
                *(("requirements_incomplete",) if incomplete else ()),
            ),
            rules=(
                "Do not treat provisional feedback as final assurance.",
                "Unknown or dynamic dependencies widen the retained scope.",
                "Do not modify files outside the workspace.",
                "Test candidates require an independent oracle and cannot weaken requirements.",
            ),
            feedback_cursor=record.session.event_cursor,
            source_revision=record.session.current_revision,
            can_start=not incomplete,
        )
        self._event(record, "AGENT_PACKET_ISSUED", {"can_start": packet.can_start})
        return packet

    def observe_change(
        self,
        session_id: str,
        changed_paths: list[str] | tuple[str, ...],
        *,
        source_revision: str | None = None,
    ) -> dict[str, object]:
        record = self._record(session_id)
        paths = validate_change_paths(changed_paths)
        revision = source_revision or record.session.current_revision
        if not revision.strip():
            raise VerificationSessionError("source revision cannot be empty")
        changed = bool(paths) or revision != record.session.current_revision
        stale = False
        if changed:
            stale = bool(record.receipts)
            for run in record.runs.values():
                if run.get("status") in {"REQUESTED", "RECEIPT_RECORDED"}:
                    run["status"] = "STALE"
                    run["stale_reason"] = "source_changed"
                    stale = True
        terminal = record.session.state in {SessionState.CANCELLED.value, SessionState.COMPLETED.value}
        state = (
            record.session.state
            if terminal
            else SessionState.STALE.value
            if stale
            else SessionState.FEEDBACK_READY.value
        )
        self._update_session(record, current_revision=revision, source_revision=revision, state=state, status=state)
        decision: dict[str, object] = {
            "changed_paths": paths,
            "source_revision": revision,
            "evidence_invalidated": changed,
            "scope_policy": "WIDEN_TO_RETAINED_SUITE_ON_UNKNOWN",
            "candidate_test_hints": _candidate_test_hints_for_paths(paths, record.profile.root_path),
            "status": state,
        }
        record.feedback.append(decision)
        self._event(record, "CHANGE_OBSERVED", decision)
        return decision

    def get_feedback(self, session_id: str) -> dict[str, object]:
        record = self._record(session_id)
        return {
            "session_id": session_id,
            "cursor": record.session.event_cursor,
            "status": record.session.state,
            "source_revision": record.session.current_revision,
            "feedback": tuple(record.feedback[-8:]),
            "provisional": True,
            "final_assurance": False,
            "authority": self.execution_authority,
        }

    def propose_test_change(
        self,
        session_id: str,
        patch_text: str,
        changed_paths: list[str] | tuple[str, ...],
        *,
        requirement_ids: list[str] | tuple[str, ...] = (),
    ) -> TestChangeProposal:
        record = self._record(session_id)
        paths = validate_change_paths(changed_paths)
        proposal = TestChangeProposal(
            session_id=session_id,
            source_revision=record.session.current_revision,
            plan_hash=record.plan.plan_hash,
            policy_hash=record.profile.policy_hash,
            proposal_id=_new_id("aese-test-proposal"),
            base_revision=record.session.current_revision,
            patch_text=patch_text,
            changed_paths=paths,
            requirement_ids=tuple(requirement_ids),
            operation="ADD_OR_MODIFY_TEST",
            oracle_present=False,
            weakening_detected=False,
            status="CANDIDATE",
        )
        proposal.validate()
        record.proposals[proposal.proposal_id] = proposal
        self._event(record, "TEST_CHANGE_PROPOSED", {"proposal_id": proposal.proposal_id})
        return proposal

    def evaluate_test_change(self, session_id: str, proposal_id: str) -> TestChangeProposal:
        record = self._record(session_id)
        proposal = record.proposals.get(proposal_id)
        if proposal is None:
            raise VerificationSessionError("test proposal was not found")
        errors: list[str] = []
        if proposal.base_revision != record.session.current_revision:
            errors.append("base_revision_stale")
        if not proposal.changed_paths or any(
            not ("test" in path.casefold() or "spec" in path.casefold()) for path in proposal.changed_paths
        ):
            errors.append("path_is_not_test_scoped")
        oracle_present = _contains_oracle(proposal.patch_text)
        weakening = _contains_weakening(proposal.patch_text)
        if not oracle_present:
            errors.append("oracle_missing")
        if weakening:
            errors.append("test_weakening_detected")
        if _contains_tautological_oracle(proposal.patch_text):
            errors.append("tautological_oracle_detected")
        if _contains_runner_spoofing(proposal.patch_text):
            errors.append("runner_spoofing_detected")
        if _contains_test_bloat(proposal.patch_text):
            errors.append("test_bloat_detected")
        status = "REJECTED" if errors else "CANDIDATE"
        evaluated = _rebind(
            proposal,
            oracle_present=oracle_present,
            weakening_detected=weakening,
            status=status,
            error_codes=tuple(sorted(set(errors))),
        )
        evaluated.validate()
        record.proposals[proposal_id] = evaluated
        self._event(record, "TEST_CHANGE_EVALUATED", {"proposal_id": proposal_id, "status": status, "errors": errors})
        return evaluated

    def apply_test_change(self, session_id: str, proposal_id: str) -> dict[str, object]:
        record = self._record(session_id)
        proposal = record.proposals.get(proposal_id)
        if proposal is None:
            raise VerificationSessionError("test proposal was not found")
        if proposal.status != "CANDIDATE" or not proposal.oracle_present or proposal.weakening_detected:
            raise VerificationSessionError("only an evaluated, non-weakening candidate can be retained")
        result: dict[str, object] = {
            "status": "BLOCKED_SHADOW_ONLY",
            "proposal_id": proposal_id,
            "reason": "workspace mutation and evidence promotion are disabled in AESE shadow mode",
            "evidence_promotion": EVIDENCE_PROMOTION,
        }
        self._event(record, "TEST_CHANGE_RETAINED_AS_CANDIDATE", result)
        return result

    def request_deep_run(self, session_id: str, *, reason: str = "task_requested") -> dict[str, object]:
        record = self._record(session_id)
        if record.session.state in {SessionState.CANCELLED.value, SessionState.COMPLETED.value}:
            raise VerificationSessionError("session is not runnable")
        run_id = _new_id("aese-run")
        run: dict[str, object] = {
            "run_id": run_id,
            "session_id": session_id,
            "status": "REQUESTED",
            "reason": reason,
            "source_revision": record.session.current_revision,
            "plan_hash": record.plan.plan_hash,
            "policy_hash": record.profile.policy_hash,
            "execution_authority": self.execution_authority,
            "selection_mode": "SHADOW_ONLY",
            "receipt_ids": (),
        }
        record.runs[run_id] = run
        self._update_session(
            record, state=SessionState.DEEP_RUN_REQUESTED.value, status=SessionState.DEEP_RUN_REQUESTED.value
        )
        self._event(record, "DEEP_RUN_REQUESTED", run)
        return dict(run)

    def record_execution_result(
        self,
        session_id: str,
        run_id: str,
        result: object,
    ) -> ExecutionReceipt:
        """Record one result already executed through the existing Lab cell.

        The façade accepts only the bounded structured projection returned by
        the Lab adapter.  It never executes a command itself.  A source
        revision mismatch is rejected as stale evidence rather than being
        re-labelled as a current result.
        """

        record = self._record(session_id)
        run = record.runs.get(run_id)
        if run is None:
            raise VerificationSessionError("verification run was not found")
        if run.get("status") not in {"REQUESTED", "RECEIPT_RECORDED"}:
            raise VerificationSessionError("verification run is cancelled, stale, or terminal")
        if not isinstance(result, Mapping):
            raise VerificationSessionError("execution result must be a mapping")
        typed_result = cast(Mapping[str, object], result)
        raw_source_revision = typed_result.get("source_revision")
        if type(raw_source_revision) is not str or not raw_source_revision.strip():
            raise VerificationSessionError("execution result source revision is invalid")
        run_source_revision = run.get("source_revision")
        if (
            type(run_source_revision) is not str
            or run_source_revision != record.session.current_revision
            or raw_source_revision != run_source_revision
            or run.get("plan_hash") != record.plan.plan_hash
            or run.get("policy_hash", record.profile.policy_hash) != record.profile.policy_hash
        ):
            run["status"] = "STALE"
            run["stale_reason"] = "run_binding_mismatch"
            self._event(record, "EXECUTION_RESULT_REJECTED_STALE", {"run_id": run_id})
            raise VerificationSessionError("execution result is stale or does not match its run binding")

        def text(name: str, default: str = "UNKNOWN") -> str:
            value = typed_result.get(name, default)
            if type(value) is not str or not value.strip() or "\x00" in value:
                raise ValueError(f"execution result field is invalid: {name}")
            return value

        def non_negative_int(name: str) -> int:
            value = typed_result.get(name, 0)
            if type(value) is not int or value < 0:
                raise ValueError(f"execution result count is invalid: {name}")
            return value

        status_value = typed_result.get("status")
        status = status_value.upper() if type(status_value) is str else "MALFORMED"
        if status not in {"PASS", "FAIL", "ZERO_TESTS", "MALFORMED", "TIMEOUT", "CANCELLED", "ERROR"}:
            status = "MALFORMED"
        try:
            command_id = text("command_id")
            adapter = text("adapter")
            framework = text("framework")
            toolchain = text("toolchain")
            platform = text("platform")
            executable = text("executable")
            working_directory = text("working_directory")
            exit_semantics = text("exit_semantics")
            stdout_reference = text("stdout_reference", f"memory://aese/{run_id}/stdout")
            stderr_reference = text("stderr_reference", f"memory://aese/{run_id}/stderr")
            started_at = text("started_at", datetime.now(UTC).isoformat())
            finished_at = text("finished_at", datetime.now(UTC).isoformat())
            raw_argv = typed_result.get("argv", ())
            if not isinstance(raw_argv, (tuple, list)):
                raise ValueError("execution result argv is invalid")
            typed_argv = cast(tuple[object, ...] | list[object], raw_argv)
            if any(type(item) is not str for item in typed_argv):
                raise ValueError("execution result argv is invalid")
            argv = tuple(cast(str, item) for item in typed_argv)
            raw_environment = typed_result.get("environment_fingerprint", {})
            environment_items = (
                tuple(cast(Mapping[object, object], raw_environment).items())
                if isinstance(raw_environment, Mapping)
                else ()
            )
            if not isinstance(raw_environment, Mapping) or any(
                type(key) is not str or type(value) is not str for key, value in environment_items
            ):
                raise ValueError("execution result environment fingerprint is invalid")
            environment = {cast(str, key): cast(str, value) for key, value in environment_items}
            timeout_seconds = typed_result.get("timeout_seconds", 1.0)
            if (
                type(timeout_seconds) not in (int, float)
                or isinstance(timeout_seconds, bool)
                or not float(cast(int | float, timeout_seconds)) > 0
            ):
                raise ValueError("execution result timeout is invalid")
            cancellation_requested = typed_result.get("cancellation_requested", False)
            if type(cancellation_requested) is not bool:
                raise ValueError("execution result cancellation flag is invalid")
            exit_code = typed_result.get("exit_code")
            if exit_code is not None and type(exit_code) is not int:
                raise ValueError("execution result exit code is invalid")
            counts = {
                name: non_negative_int(name)
                for name in ("discovered", "passed", "failed", "skipped", "filtered", "ignored")
            }
            if (
                sum(counts[name] for name in ("passed", "failed", "skipped", "filtered", "ignored"))
                > counts["discovered"]
            ):
                raise ValueError("execution result counts exceed discovered tests")
            if status == "PASS" and (
                exit_code != 0 or counts["passed"] < 1 or counts["failed"] > 0 or cancellation_requested
            ):
                raise ValueError("execution result PASS status contradicts counts or exit code")
            artifact_ids: list[str] = []
            for stream in ("stdout", "stderr"):
                digest = typed_result.get(f"{stream}_hash")
                size = typed_result.get(f"{stream}_size", 0)
                if type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) and type(size) is int and size >= 0:
                    artifact_id = f"{run_id}:{command_id}:{stream}"
                    artifact = ArtifactReference(
                        session_id=session_id,
                        source_revision=record.session.current_revision,
                        plan_hash=record.plan.plan_hash,
                        policy_hash=record.profile.policy_hash,
                        artifact_id=artifact_id,
                        kind=f"{stream}_output",
                        uri=f"memory://aese/{run_id}/{command_id}/{stream}",
                        artifact_hash=digest,
                        size_bytes=size,
                        media_type="text/plain",
                        redacted=True,
                        status="RECORDED",
                    )
                    artifact.validate()
                    record.artifacts[artifact_id] = artifact
                    artifact_ids.append(artifact_id)
            receipt = ExecutionReceipt(
                session_id=session_id,
                source_revision=record.session.current_revision,
                plan_hash=record.plan.plan_hash,
                policy_hash=record.profile.policy_hash,
                attempt=1,
                generation=0,
                status=status,
                run_id=run_id,
                adapter=adapter,
                framework=framework,
                toolchain=toolchain,
                platform=platform,
                executable=executable,
                argv=argv,
                working_directory=working_directory,
                environment_fingerprint=environment,
                resource_class="LOCAL_PROCESS_CELL_BOUNDED_WALL_TIME",
                timeout_seconds=float(cast(int | float, timeout_seconds)),
                cancellation_requested=cancellation_requested,
                exit_code=exit_code,
                exit_semantics=exit_semantics,
                discovered=counts["discovered"],
                passed=counts["passed"],
                failed=counts["failed"],
                skipped=counts["skipped"],
                filtered=counts["filtered"],
                ignored=counts["ignored"],
                artifact_ids=tuple(artifact_ids),
                stdout_reference=stdout_reference,
                stderr_reference=stderr_reference,
                started_at=started_at,
                finished_at=finished_at,
                error_codes=tuple(
                    code for code in (str(typed_result.get("failure_class", "")).strip(),) if code and code != "None"
                ),
            )
            receipt.validate()
        except TypeError, ValueError:
            status = "MALFORMED"
            receipt = ExecutionReceipt(
                session_id=session_id,
                source_revision=record.session.current_revision,
                plan_hash=record.plan.plan_hash,
                policy_hash=record.profile.policy_hash,
                status=status,
                run_id=run_id,
                adapter="unknown",
                framework="unknown",
                toolchain="UNKNOWN",
                platform="UNKNOWN",
                executable="UNKNOWN",
                argv=(),
                working_directory=record.profile.root_path,
                resource_class="LOCAL_PROCESS_CELL_BOUNDED_WALL_TIME",
                timeout_seconds=1.0,
                exit_semantics="MALFORMED_RESULT",
                stdout_reference=f"memory://aese/{run_id}/stdout",
                stderr_reference=f"memory://aese/{run_id}/stderr",
                started_at=datetime.now(UTC).isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
                error_codes=("MALFORMED_RESULT",),
            )
            receipt.validate()
        receipt_id = _new_id("aese-receipt")
        record.receipts[receipt_id] = receipt
        receipt_ids = tuple(cast(tuple[str, ...], run.get("receipt_ids", ())))
        run["receipt_ids"] = (*receipt_ids, receipt_id)
        run["status"] = "RECEIPT_RECORDED"
        run["last_result_status"] = receipt.status
        self._update_session(record, state=SessionState.FEEDBACK_READY.value, status=SessionState.FEEDBACK_READY.value)
        self._event(
            record,
            "EXECUTION_RECEIPT_RECORDED",
            {"run_id": run_id, "receipt_id": receipt_id, "status": receipt.status},
        )
        return receipt

    def inspect_run(self, session_id: str, run_id: str) -> dict[str, object]:
        record = self._record(session_id)
        run = record.runs.get(run_id)
        if run is None:
            raise VerificationSessionError("run was not found")
        return dict(run)

    def cancel_run(self, session_id: str, run_id: str) -> dict[str, object]:
        record = self._record(session_id)
        run = record.runs.get(run_id)
        if run is None:
            raise VerificationSessionError("run was not found")
        run["status"] = "CANCELLED"
        run["cancelled"] = True
        self._update_session(record, state=SessionState.CANCELLED.value, status=SessionState.CANCELLED.value)
        self._event(record, "RUN_CANCELLED", run)
        return dict(run)

    def resume_session(self, session_id: str) -> DevelopmentVerificationSession:
        record = self._record(session_id)
        if record.session.state == SessionState.CANCELLED.value:
            raise VerificationSessionError("cancelled sessions cannot be resumed")
        self._update_session(record, state=SessionState.ACTIVE.value, status=SessionState.ACTIVE.value)
        self._event(record, "SESSION_RESUMED", {"source_revision": record.session.current_revision})
        return record.session

    def read_report(self, session_id: str) -> dict[str, object]:
        record = self._record(session_id)
        history_receipts = tuple(record.receipts.values())
        terminal = record.session.state in {
            SessionState.STALE.value,
            SessionState.CANCELLED.value,
            SessionState.COMPLETED.value,
        }
        current_receipt_pairs: list[tuple[str, ExecutionReceipt]] = []
        if not terminal:
            for receipt_id, receipt in record.receipts.items():
                run = record.runs.get(receipt.run_id)
                if (
                    run is not None
                    and run.get("status") == "RECEIPT_RECORDED"
                    and receipt.source_revision == record.session.current_revision
                    and receipt.plan_hash == record.plan.plan_hash
                    and receipt.policy_hash == record.profile.policy_hash
                    and run.get("source_revision") == record.session.current_revision
                    and run.get("plan_hash") == record.plan.plan_hash
                    and run.get("policy_hash", record.profile.policy_hash) == record.profile.policy_hash
                ):
                    current_receipt_pairs.append((receipt_id, receipt))
        current_receipts = tuple(receipt for _, receipt in current_receipt_pairs)
        current_receipt_ids = tuple(receipt_id for receipt_id, _ in current_receipt_pairs)
        current_artifact_ids = tuple(
            dict.fromkeys(artifact_id for receipt in current_receipts for artifact_id in receipt.artifact_ids)
        )
        requirement_status = {requirement.requirement_id: "INCOMPLETE" for requirement in record.requirements}
        if current_receipts:
            statuses = {receipt.status for receipt in current_receipts}
            if statuses == {"PASS"}:
                failure_reasons = ("evidence_promotion_disabled_shadow_mode",)
            elif statuses.intersection({"FAIL", "ERROR", "TIMEOUT", "CANCELLED", "MALFORMED", "ZERO_TESTS"}):
                failure_reasons = ("verification_run_not_passing",)
            else:
                failure_reasons = ("verification_run_incomplete",)
            error_codes = ("receipt_obligation_binding_missing",)
            assessment_run_id = current_receipts[0].run_id
            execution_status = "LAB_BOUND"
        elif record.session.state == SessionState.CANCELLED.value:
            failure_reasons = ("execution_cancelled",)
            error_codes = ()
            assessment_run_id = "not-executed"
            execution_status = "CANCELLED"
        elif history_receipts or record.session.state == SessionState.STALE.value:
            failure_reasons = ("evidence_not_bound_to_current_inputs",)
            error_codes = ()
            assessment_run_id = "not-executed"
            execution_status = "STALE"
        else:
            failure_reasons = ("execution_not_bound_to_lab_in_vertical_slice",)
            error_codes = ()
            assessment_run_id = "not-executed"
            execution_status = "NOT_EXECUTED"
        assessment_payload = {
            "session_id": session_id,
            "source_revision": record.session.current_revision,
            "plan_hash": record.plan.plan_hash,
            "policy_hash": record.profile.policy_hash,
            "run_id": assessment_run_id,
            "requirement_status": requirement_status,
            "receipt_ids": current_receipt_ids,
            "artifact_ids": current_artifact_ids,
            "error_codes": error_codes,
            "failure_reasons": failure_reasons,
        }
        assessment = VerificationAssessment(
            session_id=session_id,
            source_revision=record.session.current_revision,
            plan_hash=record.plan.plan_hash,
            policy_hash=record.profile.policy_hash,
            assessment_id=f"aese-assessment-{canonical_hash(assessment_payload)}",
            run_id=assessment_run_id,
            requirement_status=requirement_status,
            receipt_ids=current_receipt_ids,
            artifact_ids=current_artifact_ids,
            error_codes=error_codes,
            final_assurance=False,
            provisional=True,
            failure_reasons=failure_reasons,
        )
        assessment.validate()
        return {
            "session": record.session.as_dict(),
            "plan": record.plan.as_dict(),
            "assessment": assessment.as_dict(),
            "events": tuple(event.as_dict() for event in record.events),
            "receipts": tuple(receipt.as_dict() for receipt in history_receipts),
            "artifacts": tuple(artifact.as_dict() for artifact in record.artifacts.values()),
            "execution": execution_status,
            "final_assurance": False,
            "promotion": EVIDENCE_PROMOTION,
        }

    def _record(self, session_id: str) -> _SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise VerificationSessionError("verification session was not found")
        return record

    def _update_session(self, record: _SessionRecord, **changes: object) -> None:
        changes.setdefault("plan_hash", record.plan.plan_hash)
        changes.setdefault(
            "source_revision", cast(str, changes.get("current_revision", record.session.source_revision))
        )
        changes.setdefault("status", changes.get("state", record.session.status))
        changes["event_cursor"] = record.session.event_cursor
        record.session = _rebind(record.session, **changes)

    def _event(self, record: _SessionRecord, event_type: str, payload: object) -> VerificationEvent:
        cursor = record.session.event_cursor + 1
        event = VerificationEvent(
            session_id=record.session.session_id,
            source_revision=record.session.current_revision,
            plan_hash=record.plan.plan_hash,
            policy_hash=record.profile.policy_hash,
            status=record.session.state,
            event_id=_new_id("aese-event"),
            cursor=cursor,
            event_type=event_type,
            payload_hash=canonical_hash(payload),
            detail=event_type,
        )
        event.validate()
        record.events.append(event)
        record.session = _rebind(record.session, event_cursor=cursor, status=record.session.status)
        return event


__all__ = ["AESE_MODE", "AgentImplementationPacket", "VerificationFacade"]
