"""One AESE façade shared by runtime, desktop, CLI and future API callers."""

from __future__ import annotations

import re
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


def _contains_oracle(patch_text: str) -> bool:
    return bool(
        re.search(
            r"\b(assert|assert!|assert_eq!|assert_ne!|expect\s*\(|should\.|to_equal\(|t\.(?:error|fatal|fail)|Assert\.|assertThat\(|assertEquals\(|assertTrue\()",
            patch_text,
        )
    )


def _contains_weakening(patch_text: str) -> bool:
    return bool(
        re.search(
            r"\b(skip|xfail|xskip|xdescribe|pytest\.mark\.skip|pytest\.mark\.xfail|it\.skip|test\.skip|TODO)\b",
            patch_text,
            flags=re.IGNORECASE,
        )
    )


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
        stale = bool(record.session.latest_assessment_id and revision != record.session.current_revision)
        state = SessionState.STALE.value if stale else SessionState.FEEDBACK_READY.value
        self._update_session(record, current_revision=revision, source_revision=revision, state=state, status=state)
        decision: dict[str, object] = {
            "changed_paths": paths,
            "source_revision": revision,
            "evidence_invalidated": stale or bool(paths),
            "scope_policy": "WIDEN_TO_RETAINED_SUITE_ON_UNKNOWN",
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
        if not isinstance(result, Mapping):
            raise VerificationSessionError("execution result must be a mapping")
        typed_result = cast(Mapping[str, object], result)
        raw_source_revision = typed_result.get("source_revision", record.session.current_revision)
        if type(raw_source_revision) is not str or not raw_source_revision.strip():
            raise VerificationSessionError("execution result source revision is invalid")
        if raw_source_revision != record.session.current_revision:
            run["status"] = "STALE"
            run["stale_reason"] = "source_revision_mismatch"
            self._event(record, "EXECUTION_RESULT_REJECTED_STALE", {"run_id": run_id})
            raise VerificationSessionError("execution result source revision is stale")

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
        receipts = tuple(record.receipts.values())
        receipt_ids = tuple(record.receipts)
        artifact_ids = tuple(record.artifacts)
        if receipts:
            statuses = {receipt.status for receipt in receipts}
            if statuses == {"PASS"}:
                requirement_status = {
                    requirement.requirement_id: "PROVISIONAL_PASS" for requirement in record.requirements
                }
                failure_reasons = ("evidence_promotion_disabled_shadow_mode",)
            elif statuses.intersection({"FAIL", "ERROR", "TIMEOUT", "CANCELLED", "MALFORMED", "ZERO_TESTS"}):
                requirement_status = {requirement.requirement_id: "FAIL" for requirement in record.requirements}
                failure_reasons = ("verification_run_not_passing",)
            else:
                requirement_status = {requirement.requirement_id: "INCOMPLETE" for requirement in record.requirements}
                failure_reasons = ("verification_run_incomplete",)
            assessment_run_id = receipts[0].run_id
            execution_status = "LAB_BOUND"
        else:
            requirement_status = {requirement.requirement_id: "INCOMPLETE" for requirement in record.requirements}
            failure_reasons = ("execution_not_bound_to_lab_in_vertical_slice",)
            assessment_run_id = next(iter(record.runs), "not-executed")
            execution_status = "NOT_EXECUTED"
        assessment = VerificationAssessment(
            session_id=session_id,
            source_revision=record.session.current_revision,
            plan_hash=record.plan.plan_hash,
            policy_hash=record.profile.policy_hash,
            assessment_id=_new_id("aese-assessment"),
            run_id=assessment_run_id,
            requirement_status=requirement_status,
            receipt_ids=receipt_ids,
            artifact_ids=artifact_ids,
            final_assurance=False,
            provisional=True,
            failure_reasons=failure_reasons,
        )
        assessment.validate()
        self._update_session(
            record,
            latest_assessment_id=assessment.assessment_id,
            state=SessionState.FEEDBACK_READY.value,
            status=SessionState.FEEDBACK_READY.value,
        )
        return {
            "session": record.session.as_dict(),
            "plan": record.plan.as_dict(),
            "assessment": assessment.as_dict(),
            "events": tuple(event.as_dict() for event in record.events),
            "receipts": tuple(receipt.as_dict() for receipt in receipts),
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
