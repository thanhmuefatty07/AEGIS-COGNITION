from __future__ import annotations

import asyncio
import json
import pytest
from dataclasses import replace
from types import SimpleNamespace

from aegis_cognition.config import trust_policy_hash
from aegis_cognition.goal_contract import (
    AcceptancePredicate,
    DEFAULT_GOAL_CONTRACT_LIMITS,
    EvidencePolicy,
    ExecutionBinding,
    GoalBudgetContract,
    GoalContract,
    GoalContractError,
    GoalContractLimits,
    GoalLifecycle,
    GoalProgress,
    GoalVerification,
    GoalVerdict,
    StaleGoalGeneration,
    TargetDescriptor,
    external_side_effect_key,
)
from aegis_cognition.lab import (
    ClaimRecord,
    ExperimentSpec,
    HypothesisRecord,
    LabApplication,
    LabMissionSpec,
    LabRun,
    ObservationRecord,
    SearchProgram,
    SourceRecord,
)


def _budget() -> GoalBudgetContract:
    return GoalBudgetContract(
        token_limit=100,
        attempt_limit=20,
        wall_time_limit_ms=10_000,
        reserved_tokens=30,
    )


def _predicate(predicate_id: str = "tests") -> AcceptancePredicate:
    return AcceptancePredicate(
        predicate_id=predicate_id,
        description="the required test suite passes",
        evaluator="pytest",
        inputs=("test-report",),
        expected_result=True,
        evidence_refs=("test-report",),
        reproducibility_requirements=("same-revision",),
    )


def _target(revision: str = "abc123") -> TargetDescriptor:
    return TargetDescriptor(
        kind="repository",
        stable_id="aegis-cognition",
        revision_or_digest=revision,
        read_roots=("C:/repo",),
        write_roots=("C:/repo",),
        network_allowlist=("https://pypi.org",),
        owner="operator",
    )


def _contract() -> GoalContract:
    return GoalContract(
        goal_id="goal-1",
        objective="make the testable change",
        target=_target(),
        acceptance=(_predicate(),),
        scope=("read code", "write code"),
        non_goals=("publish remotely",),
        policy_digest="policy-digest",
        budget=_budget(),
        evidence_policy=EvidencePolicy(
            required_record_types=("source",),
            minimum_sources=1,
            require_independent_verifier=True,
            trusted_verifier_ids=("independent-verifier",),
        ),
        author_id="operator",
        effective_epoch=1,
        created_at_ms=123,
    )


def _verification(
    contract: GoalContract,
    *,
    result: bool = True,
    evidence_complete: bool = True,
    verifier_id: str = "independent-verifier",
    independent: bool = True,
    evidence_refs: tuple[str, ...] = ("test-report",),
) -> GoalVerification:
    return GoalVerification(
        contract_hash=contract.contract_hash,
        generation=contract.generation,
        verifier_id=verifier_id,
        independent=independent,
        predicate_results=(("tests", result),),
        evidence_refs=evidence_refs,
        evidence_complete=evidence_complete,
    )


def _execution_binding(contract: GoalContract, **overrides: object) -> ExecutionBinding:
    fields: dict[str, object] = {
        "goal_contract_hash": contract.contract_hash,
        "generation": contract.generation,
        "target_digest": contract.target.canonical_digest(),
        "plan_digest": "c" * 64,
        "mission_id": "mission-1",
        "task_id": 7,
        "attempt_id": 2,
        "owner_id": contract.target.owner,
        "principal_id": "agent-1",
    }
    fields.update(overrides)
    return ExecutionBinding(**fields)


def test_execution_binding_round_trips_canonically_and_is_frozen() -> None:
    contract = _contract()
    binding = _execution_binding(
        contract,
        evidence_digest="d" * 64,
        completion_digest="e" * 64,
    )

    payload = binding.to_dict()
    assert payload["schema"] == "aegis-execution-binding-v1"
    assert len(binding.binding_hash) == 64
    assert ExecutionBinding.from_dict(payload) == binding
    assert binding.canonical_json() == json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    with pytest.raises(AttributeError):
        binding.task_id = 8  # type: ignore[misc]


def test_execution_binding_manifest_hash_is_optional_for_legacy_and_hash_bound_when_present() -> None:
    contract = _contract()
    binding = _execution_binding(contract, execution_cell_manifest_hash="a" * 64)
    payload = binding.to_dict()
    assert payload["execution_cell_manifest_hash"] == "a" * 64
    assert ExecutionBinding.from_dict(payload) == binding

    tampered = {**payload, "execution_cell_manifest_hash": "b" * 64}
    with pytest.raises(GoalContractError, match="hash mismatch"):
        ExecutionBinding.from_dict(tampered)

    legacy_payload = _execution_binding(contract).to_dict()
    assert "execution_cell_manifest_hash" not in legacy_payload
    assert ExecutionBinding.from_dict(legacy_payload) == _execution_binding(contract)


def test_execution_binding_backend_identity_is_hash_bound_and_requires_manifest() -> None:
    contract = _contract()
    binding = _execution_binding(
        contract,
        execution_cell_manifest_hash="a" * 64,
        backend_kind="python-local",
        resource_policy_hash="b" * 64,
    )
    payload = binding.to_dict()
    assert payload["backend_kind"] == "python-local"
    assert payload["resource_policy_hash"] == "b" * 64
    assert ExecutionBinding.from_dict(payload) == binding

    with pytest.raises(ValueError, match="requires an execution-cell manifest"):
        _execution_binding(
            contract,
            backend_kind="python-local",
            resource_policy_hash="b" * 64,
        )
    malformed = dict(payload)
    malformed.pop("resource_policy_hash")
    with pytest.raises(GoalContractError, match="missing or unknown"):
        ExecutionBinding.from_dict(malformed)


def test_execution_binding_selected_cell_identity_round_trips_and_is_complete() -> None:
    contract = _contract()
    binding = _execution_binding(
        contract,
        execution_cell_manifest_hash="a" * 64,
        execution_cell_id="wasm-cell",
        execution_action_kind="tool_call",
        backend_kind="wasmtime",
        resource_policy_hash="b" * 64,
    )
    payload = binding.to_dict()
    assert payload["execution_cell_id"] == "wasm-cell"
    assert payload["execution_action_kind"] == "tool_call"
    assert ExecutionBinding.from_dict(payload) == binding

    with pytest.raises(ValueError, match="selected cell identity"):
        _execution_binding(
            contract,
            execution_cell_manifest_hash="a" * 64,
            execution_cell_id="wasm-cell",
            execution_action_kind="tool_call",
        )


def test_execution_binding_rejects_tampering_and_unknown_fields() -> None:
    payload = _execution_binding(_contract()).to_dict()

    changed_plan = {**payload, "plan_digest": "f" * 64}
    with pytest.raises(GoalContractError, match="hash mismatch"):
        ExecutionBinding.from_dict(changed_plan)

    changed_hash = {**payload, "binding_hash": "0" * 64}
    with pytest.raises(GoalContractError, match="hash mismatch"):
        ExecutionBinding.from_dict(changed_hash)

    with pytest.raises(GoalContractError, match="missing or unknown"):
        ExecutionBinding.from_dict({**payload, "unexpected": True})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema", "aegis-execution-binding-v999"),
        ("goal_contract_hash", "not-a-digest"),
        ("target_digest", "a" * 63),
        ("plan_digest", "A" * 64),
        ("mission_id", ""),
        ("task_id", 0),
        ("attempt_id", 0),
        ("owner_id", ""),
        ("principal_id", ""),
        ("evidence_digest", "not-a-digest"),
        ("completion_digest", "not-a-digest"),
    ),
)
def test_execution_binding_rejects_malformed_fields(field: str, value: object) -> None:
    payload = _execution_binding(_contract()).to_dict()
    payload[field] = value
    with pytest.raises((GoalContractError, ValueError)):
        ExecutionBinding.from_dict(payload)


def test_execution_binding_validates_contract_generation_and_owner_continuity() -> None:
    contract = _contract()
    binding = _execution_binding(contract)
    assert binding.validate_against(contract) is None

    with pytest.raises(StaleGoalGeneration, match="different contract generation"):
        binding.validate_against(replace(contract, objective="changed objective"))

    with pytest.raises(StaleGoalGeneration, match="different contract generation"):
        replace(binding, generation=binding.generation + 1).validate_against(contract)

    with pytest.raises(GoalContractError, match="owner"):
        replace(binding, owner_id="different-owner").validate_against(contract)

    with pytest.raises(GoalContractError, match="target digest"):
        replace(binding, target_digest="b" * 64).validate_against(contract)


def test_target_digest_is_deterministic_and_matches_native_projection_vector() -> None:
    target = TargetDescriptor(
        kind="fixture",
        stable_id="fixture-1",
        revision_or_digest="revision-1",
        read_roots=("fixture://input",),
        owner="test-author",
    )

    assert target.canonical_digest() == target.canonical_digest()
    assert target.canonical_digest() == (
        "126fb47163792f9cf0c6ffd0daf18247294283c23d5a74fc3f35fa2046860494"
    )


def _explicit_lab_contract(*, evidence_ref: str = "test-report", minimum_sources: int = 1) -> GoalContract:
    return replace(
        _contract(),
        acceptance=(replace(_contract().acceptance[0], evidence_refs=(evidence_ref,)),),
        evidence_policy=EvidencePolicy(
            required_record_types=("source",),
            minimum_sources=minimum_sources,
            require_independent_verifier=True,
            trusted_verifier_ids=("independent-verifier",),
        ),
        budget=GoalBudgetContract.from_lab_budget(
            token_limit=400,
            attempt_limit=20,
            finalization_reserve=80,
            recovery_reserve=40,
        ),
        policy_digest=trust_policy_hash("DEV"),
    )


def _completed_explicit_run(
    *, evidence_ref: str = "test-report", minimum_sources: int = 1
) -> tuple[LabRun, GoalContract]:
    contract = _explicit_lab_contract(evidence_ref=evidence_ref, minimum_sources=minimum_sources)
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )
    run.add_source(
        SourceRecord(
            "test-report",
            "https://example.test/report",
            "content-hash",
            "snapshot-hash",
            1,
            trust_tier=1,
            relation="supports",
        )
    )
    run.add_claim(ClaimRecord("claim-1", "The required test passed.", ("test-report",), 9_000, "supported"))
    run.add_hypothesis(
        HypothesisRecord("hypothesis-1", "The change satisfies the contract.", 5_000, ("test fails",), ("claim-1",))
    )
    run.add_experiment(
        ExperimentSpec("experiment-1", "hypothesis-1", "bounded", ("result",), ("same revision",), (1, 2, 3, 4, 5), 1)
    )
    run.add_observation(ObservationRecord("observation-1", "experiment-1", 1, 1.0, "score", "raw", "env"))
    run.dossier(finalize=True)
    assert run.state == "completed"
    return run, contract


def test_python_hashes_match_the_native_goal_contract_vectors() -> None:
    contract = GoalContract(
        goal_id="goal-1",
        objective="test a hypothesis",
        target=TargetDescriptor(
            kind="fixture",
            stable_id="fixture-1",
            revision_or_digest="revision-1",
            read_roots=("fixture://input",),
            owner="test-author",
        ),
        acceptance=(
            AcceptancePredicate(
                predicate_id="reproducible-result",
                description="the report contains a reproducible result",
                evaluator="aegis.test.reproducibility",
                inputs=("report",),
                evidence_refs=("source-1",),
                reproducibility_requirements=("same-inputs",),
            ),
        ),
        scope=("local deterministic fixture",),
        policy_digest="a" * 64,
        budget=GoalBudgetContract(
            token_limit=100,
            attempt_limit=20,
            reserved_tokens=30,
        ),
        evidence_policy=EvidencePolicy(
            required_record_types=("source",),
            minimum_sources=1,
            require_independent_verifier=True,
            trusted_verifier_ids=("independent-verifier",),
        ),
        author_id="test-author",
        effective_epoch=1,
        created_at_ms=1,
    )
    assert contract.contract_hash == "7c11c990dc9671b4ff16cd039c4885cd68e81ab017bc0999067211daf55cee9f"
    verification = GoalVerification(
        contract_hash=contract.contract_hash,
        generation=1,
        verifier_id="independent-verifier",
        independent=True,
        predicate_results=(("reproducible-result", True),),
        evidence_refs=("source-1",),
        evidence_complete=True,
    )
    assert verification.verification_hash == "43f965d51e2d05394e33b2ca5f13488087d98c008adcf7cedf8dc4f000000c09"


def test_contract_keeps_target_and_acceptance_out_of_model_text() -> None:
    contract = _contract()
    assert contract.target.is_bound
    assert contract.acceptance[0].evaluator == "pytest"
    assert not hasattr(contract.acceptance[0], "status")
    assert contract.contract_hash == GoalContract.from_dict(contract.to_dict()).contract_hash
    assert replace(contract, acceptance=tuple(reversed(contract.acceptance))).contract_hash == contract.contract_hash
    assert contract.contract_hash != replace(
        contract,
        target=_target("different-revision"),
    ).contract_hash


def test_goal_contract_limits_reject_untrusted_wire_before_hashing() -> None:
    contract = _contract()
    exact_text = replace(
        contract,
        objective="x" * DEFAULT_GOAL_CONTRACT_LIMITS.text_bytes,
    )
    assert GoalContract.from_dict(exact_text.to_dict()) == exact_text

    oversized_text = exact_text.to_dict()
    oversized_text["objective"] += "x"
    with pytest.raises(GoalContractError, match="string exceeds the byte limit"):
        GoalContract.from_dict(oversized_text)

    oversized_sequence = contract.to_dict()
    oversized_sequence["scope"] = [f"scope-{index:03d}" for index in range(257)]
    with pytest.raises(GoalContractError, match="sequence exceeds the item limit"):
        GoalContract.from_dict(oversized_sequence)

    oversized_key = contract.to_dict()
    oversized_key["k" * (DEFAULT_GOAL_CONTRACT_LIMITS.identifier_bytes + 1)] = True
    with pytest.raises(GoalContractError, match="object key exceeds the byte limit"):
        GoalContract.from_dict(oversized_key)

    with pytest.raises(GoalContractError, match="string exceeds the byte limit"):
        replace(
            contract,
            objective="x" * (DEFAULT_GOAL_CONTRACT_LIMITS.text_bytes + 1),
        )

    oversized_binding = _execution_binding(contract).to_dict()
    oversized_binding["mission_id"] = "m" * (DEFAULT_GOAL_CONTRACT_LIMITS.identifier_bytes + 1)
    with pytest.raises(GoalContractError, match="string exceeds the byte limit"):
        ExecutionBinding.from_dict(oversized_binding)

    oversized_verification = _verification(contract).to_dict()
    oversized_verification["verifier_id"] = "v" * (DEFAULT_GOAL_CONTRACT_LIMITS.identifier_bytes + 1)
    with pytest.raises(GoalContractError, match="string exceeds the byte limit"):
        GoalVerification.from_dict(oversized_verification)

    with pytest.raises(ValueError, match="total sequence limit"):
        GoalContractLimits(max_sequence_items=256, max_total_sequence_items=128)


def test_admission_rejects_ambiguous_legacy_contract() -> None:
    legacy = GoalContract(
        goal_id="legacy-goal",
        objective="legacy mission",
        target=TargetDescriptor(),
        budget=_budget(),
        author_id="operator",
        created_at_ms=123,
    )
    with pytest.raises(GoalContractError):
        legacy.validate_for_admission()


def test_evolution_is_explicit_and_stale_updates_are_rejected() -> None:
    contract = _contract()
    with pytest.raises(StaleGoalGeneration):
        contract.evolve(
            expected_generation=0,
            author_id="operator",
            effective_epoch=2,
            reason="stale request",
        )
    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="removed acceptance",
            acceptance=(),
        )
    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="weaken required predicate",
            acceptance=(replace(contract.acceptance[0], severity="advisory"),),
        )
    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="replace evaluator",
            acceptance=(replace(contract.acceptance[0], evaluator="manual-review"),),
        )
    evolved = contract.evolve(
        expected_generation=1,
        author_id="reviewer",
        effective_epoch=2,
        reason="tighten target revision",
        target=_target("def456"),
    )
    assert evolved.generation == 2
    assert evolved.parent_contract_hash == contract.contract_hash
    assert evolved.parent_goal_id == contract.goal_id
    assert evolved.author_id == "reviewer"
    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="widen scope",
            scope=(*contract.scope, "publish"),
        )
    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="retarget another repository",
            target=replace(_target(), stable_id="other-repository"),
        )
    with pytest.raises(GoalContractError, match="retarget or unbind"):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="drop target revision binding",
            target=replace(_target(), revision_or_digest="unbound"),
        )
    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=1,
            author_id="operator",
            effective_epoch=2,
            reason="expand write surface",
            target=replace(_target(), write_roots=("C:/repo", "C:/other")),
        )


def test_target_root_narrowing_is_semantic_but_sibling_expansion_is_rejected() -> None:
    contract = _contract()
    parent_target = replace(
        contract.target,
        read_roots=("fixture://repo",),
        write_roots=("fixture://repo",),
    )
    contract = replace(contract, target=parent_target)
    narrowed_target = replace(
        parent_target,
        read_roots=("fixture://repo/src",),
        write_roots=("fixture://repo/output",),
    )

    evolved = contract.evolve(
        expected_generation=contract.generation,
        author_id="reviewer",
        effective_epoch=contract.effective_epoch + 1,
        reason="narrow target roots",
        target=narrowed_target,
    )
    assert evolved.target == narrowed_target

    child = contract.spawn_child(
        expected_generation=contract.generation,
        child_goal_id="child-narrow-root",
        author_id="child-agent",
        effective_epoch=contract.effective_epoch + 1,
        reason="delegate source-only work",
        target=narrowed_target,
    )
    assert child.target == narrowed_target

    with pytest.raises(GoalContractError):
        contract.evolve(
            expected_generation=contract.generation,
            author_id="reviewer",
            effective_epoch=contract.effective_epoch + 1,
            reason="sibling root expansion",
            target=replace(parent_target, read_roots=("fixture://other",)),
        )


def test_bound_target_owner_cannot_be_transferred_by_evolution_or_delegation() -> None:
    contract = _contract()
    transferred_target = replace(contract.target, owner="different-operator")

    with pytest.raises(GoalContractError, match="target ownership"):
        contract.evolve(
            expected_generation=contract.generation,
            author_id="reviewer",
            effective_epoch=contract.effective_epoch + 1,
            reason="transfer target ownership",
            target=transferred_target,
        )

    with pytest.raises(GoalContractError, match="target ownership"):
        contract.spawn_child(
            expected_generation=contract.generation,
            child_goal_id="child-owner-transfer",
            author_id="child-agent",
            effective_epoch=contract.effective_epoch + 1,
            reason="delegate to a different target owner",
            target=transferred_target,
        )


def test_child_goal_inherits_only_a_bounded_parent_subset() -> None:
    contract = _contract()
    child = contract.spawn_child(
        expected_generation=contract.generation,
        child_goal_id="child-goal-1",
        author_id="child-agent",
        effective_epoch=2,
        reason="delegate read-only test analysis",
        scope=("read code",),
        target=replace(
            contract.target,
            write_roots=(),
            network_allowlist=(),
            external_side_effects=(),
        ),
        budget=replace(
            contract.budget,
            token_limit=50,
            attempt_limit=5,
            reserved_tokens=10,
        ),
    )
    assert child.generation == 1
    assert child.parent_goal_id == contract.goal_id
    assert child.parent_contract_hash == contract.contract_hash
    assert GoalContract.from_dict(child.to_dict()) == child
    with pytest.raises(GoalContractError, match="expand parent scope"):
        contract.spawn_child(
            expected_generation=contract.generation,
            child_goal_id="child-goal-2",
            author_id="child-agent",
            effective_epoch=2,
            reason="widen scope",
            scope=(*contract.scope, "publish"),
        )
    with pytest.raises(GoalContractError, match="remove parent acceptance"):
        contract.spawn_child(
            expected_generation=contract.generation,
            child_goal_id="child-goal-acceptance-removed",
            author_id="child-agent",
            effective_epoch=2,
            reason="drop acceptance",
            acceptance=(),
        )
    with pytest.raises(GoalContractError, match="weaken a required"):
        contract.spawn_child(
            expected_generation=contract.generation,
            child_goal_id="child-goal-acceptance-weakened",
            author_id="child-agent",
            effective_epoch=2,
            reason="weaken acceptance",
            acceptance=(replace(contract.acceptance[0], severity="advisory"),),
        )
    with pytest.raises(GoalContractError, match="expand budget"):
        contract.spawn_child(
            expected_generation=contract.generation,
            child_goal_id="child-goal-3",
            author_id="child-agent",
            effective_epoch=2,
            reason="widen budget",
            budget=GoalBudgetContract(token_limit=101, attempt_limit=20, reserved_tokens=30),
        )
    legacy = GoalContract(
        goal_id="legacy-parent",
        objective="legacy",
        target=TargetDescriptor(),
        budget=_budget(),
        author_id="operator",
        created_at_ms=123,
    )
    with pytest.raises(GoalContractError, match="unbound parent target"):
        legacy.spawn_child(
            expected_generation=legacy.generation,
            child_goal_id="legacy-child",
            author_id="child-agent",
            effective_epoch=1,
            reason="bind target",
            target=_target(),
        )


def test_progress_verdict_is_separate_from_lifecycle_and_evidence() -> None:
    contract = _contract()
    verification = _verification(contract)
    assert GoalVerification.from_dict(verification.to_dict()) == verification
    with pytest.raises(GoalContractError, match="not trusted"):
        _verification(contract, verifier_id="untrusted-verifier").validate_against(contract)
    advisory_contract = replace(
        contract,
        acceptance=(
            contract.acceptance[0],
            replace(
                _predicate("advisory"),
                severity="advisory",
                evidence_refs=("advisory-report",),
            ),
        ),
    )
    advisory_verification = _verification(advisory_contract, evidence_refs=("test-report",))
    advisory_verification = replace(
        advisory_verification,
        predicate_results=(("advisory", True), ("tests", True)),
    )
    with pytest.raises(GoalContractError, match="predicate evidence"):
        advisory_verification.validate_against(advisory_contract)
    malformed_verification = verification.to_dict()
    malformed_verification["verification_hash"] = "0" * 64
    with pytest.raises(GoalContractError):
        GoalVerification.from_dict(malformed_verification)
    progress = GoalProgress.from_contract(contract)
    assert GoalProgress.from_dict(progress.to_dict(), contract=contract) == progress
    malformed = progress.to_dict()
    malformed["criterion_status"] = []
    with pytest.raises(GoalContractError):
        GoalProgress.from_dict(malformed, contract=contract)
    with pytest.raises(ValueError, match="contract_hash"):
        GoalProgress(contract_hash="g" * 64, generation=contract.generation)
    malformed = progress.to_dict()
    malformed["criterion_status"] = [["tests", "open"], "ignored-invalid-entry"]
    with pytest.raises(GoalContractError):
        GoalProgress.from_dict(malformed, contract=contract)
    malformed = progress.to_dict()
    malformed["unexpected"] = "ignored"
    with pytest.raises(GoalContractError):
        GoalProgress.from_dict(malformed, contract=contract)
    progress = progress.transition(
        expected_generation=1,
        next_lifecycle=GoalLifecycle.ADMITTED,
    )
    progress = progress.transition(
        expected_generation=1,
        next_lifecycle=GoalLifecycle.ACTIVE,
    )
    progress = progress.transition(
        expected_generation=1,
        next_lifecycle=GoalLifecycle.SETTLED,
    )
    inconclusive = progress.settle(
        expected_generation=1,
        verification=_verification(contract, evidence_complete=False),
        contract=contract,
    )
    assert inconclusive.verdict is GoalVerdict.INCONCLUSIVE
    failed = progress.settle(
        expected_generation=1,
        verification=_verification(contract, result=False),
        contract=contract,
    )
    assert failed.verdict is GoalVerdict.FAILED
    verified = progress.settle(
        expected_generation=1,
        verification=_verification(contract),
        contract=contract,
    )
    assert verified.verdict is GoalVerdict.VERIFIED
    with pytest.raises(GoalContractError):
        verified.settle(
            expected_generation=1,
            verification=_verification(contract, result=False),
            contract=contract,
        )
    with pytest.raises(GoalContractError):
        progress.settle(
            expected_generation=1,
            verification=GoalVerification(
                contract_hash=contract.contract_hash,
                generation=contract.generation,
                verifier_id="independent-verifier",
                independent=True,
                predicate_results=(("unknown", True),),
                evidence_refs=("test-report",),
                evidence_complete=True,
            ),
            contract=contract,
        )
    with pytest.raises(GoalContractError):
        progress.settle(
            expected_generation=1,
            verification=_verification(contract, independent=False),
            contract=contract,
        )
    with pytest.raises(GoalContractError):
        progress.settle(
            expected_generation=1,
            verification=_verification(contract, evidence_refs=()),
            contract=contract,
        )
    with pytest.raises(StaleGoalGeneration):
        progress.transition(expected_generation=2, next_lifecycle=GoalLifecycle.ACTIVE)


def test_malformed_contract_fields_fail_as_contract_errors() -> None:
    payload = _contract().to_dict()
    del payload["acceptance"][0]["evaluator"]
    with pytest.raises(GoalContractError):
        GoalContract.from_dict(payload)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda payload: payload["target"].update(
            {"read_roots": ["C:/repo/z", "C:/repo/a"]}
        ),
        lambda payload: payload.update({"scope": ["write code", "read code"]}),
        lambda payload: payload.update(
            {
                "acceptance": [
                    {**payload["acceptance"][0], "predicate_id": "zzz"},
                    payload["acceptance"][0],
                ]
            }
        ),
        lambda payload: payload["evidence_policy"].update(
            {"required_record_types": ["source", "claim"]}
        ),
    ),
)
def test_contract_wire_rejects_noncanonical_sequence_order(mutate) -> None:
    payload = _contract().to_dict()
    mutate(payload)
    with pytest.raises(GoalContractError, match="canonical order"):
        GoalContract.from_dict(payload)
    payload = _contract().to_dict()
    payload["target"]["unexpected"] = "ignored"
    with pytest.raises(GoalContractError):
        GoalContract.from_dict(payload)
    payload = _contract().to_dict()
    payload["unexpected"] = "ignored"
    with pytest.raises(GoalContractError):
        GoalContract.from_dict(payload)


def test_lab_run_exposes_goal_contract_without_replacing_legacy_run_identity() -> None:
    run = LabRun(
        "bounded lab task",
        max_steps=2,
        authority_mode="projection_only",
        token_budget=100,
        finalization_reserve=20,
        recovery_reserve=10,
    )
    context = run.controller_context()
    assert context["goal_contract"]["goal_id"] == run.goal_contract.goal_id
    assert context["goal_contract"]["contract_hash"] == run.goal_contract.contract_hash
    assert len(context["target_digest"]) == 64
    assert context["goal_progress"]["lifecycle"] == GoalLifecycle.DRAFT.value
    assert context["goal_progress"]["verdict"] is None
    run.transition("researching")
    active_progress = run.goal_progress_snapshot()
    assert active_progress.lifecycle is GoalLifecycle.ACTIVE
    assert active_progress.verdict is None
    run.abort()
    cancelled_progress = run.goal_progress_snapshot()
    assert cancelled_progress.lifecycle is GoalLifecycle.CANCELLED
    assert cancelled_progress.verdict is None
    payload = run.to_payload()
    assert payload["goal_progress"]["lifecycle"] == GoalLifecycle.CANCELLED.value
    restored = LabRun.from_payload(payload)
    assert restored.mission_id == run.mission_id
    assert restored.goal_contract == run.goal_contract
    tampered = dict(payload)
    tampered["goal_progress"] = {
        **payload["goal_progress"],
        "lifecycle": GoalLifecycle.ACTIVE.value,
    }
    with pytest.raises(ValueError):
        LabRun.from_payload(tampered)


def test_bound_goal_target_cannot_bypass_admission_on_create_or_restore() -> None:
    base = _explicit_lab_contract()
    incomplete = replace(base, acceptance=())
    with pytest.raises(GoalContractError, match="at least one acceptance"):
        LabRun(
            incomplete.objective,
            max_steps=4,
            external_attempt_budget=incomplete.budget.attempt_limit,
            scope=incomplete.scope,
            non_goals=incomplete.non_goals,
            goal_contract=incomplete,
            token_budget=incomplete.budget.token_limit,
            finalization_reserve=80,
            recovery_reserve=40,
            trust_policy_hash=incomplete.policy_digest,
        )

    legacy_run = LabRun(
        "snapshot goal admission boundary",
        max_steps=2,
        authority_mode="projection_only",
        token_budget=100,
        finalization_reserve=20,
        recovery_reserve=10,
    )
    tampered = legacy_run.to_payload()
    tampered["goal_contract"] = replace(
        legacy_run.goal_contract,
        target=_target(),
    ).to_dict()
    with pytest.raises(ValueError, match="goal contract admission"):
        LabRun.from_payload(tampered)


def test_explicit_goal_binding_is_carried_by_execution_admission_and_settlement() -> None:
    contract = replace(
        _contract(),
        budget=GoalBudgetContract.from_lab_budget(
            token_limit=100,
            attempt_limit=20,
            finalization_reserve=20,
            recovery_reserve=10,
        ),
        policy_digest=trust_policy_hash("DEV"),
    )
    run = LabRun(
        contract.objective,
        max_steps=2,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=20,
        recovery_reserve=10,
        trust_policy_hash=contract.policy_digest,
    )
    execution_id, _ = run.admit_tool_execution(
        tool_name="fixture-tool",
        input_payload={"case": "goal-binding"},
        policy_payload={"effect": "local"},
        effect_class="local_reversible",
    )
    admission = run.events[-1].payload
    assert admission["goal_id"] == contract.goal_id
    assert admission["goal_generation"] == contract.generation
    assert admission["goal_contract_hash"] == contract.contract_hash
    assert len(admission["target_digest"]) == 64

    run.record_tool_execution(
        tool_name="fixture-tool",
        execution_id=execution_id,
        admission_id=f"{execution_id}-admission",
        input_payload={"case": "goal-binding"},
        policy_payload={"effect": "local"},
        result={"ok": True},
        effect_class="local_reversible",
    )
    settlement = run.events[-1].payload
    assert settlement["goal_contract_hash"] == contract.contract_hash
    assert settlement["target_digest"] == admission["target_digest"]


def test_explicit_goal_target_allowlists_generic_external_effect_keys() -> None:
    base = _explicit_lab_contract()
    external_key = external_side_effect_key("fixture.write", "external_write")
    contract = replace(
        base,
        target=replace(base.target, external_side_effects=(external_key,)),
    )
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )

    execution_id, _ = run.admit_tool_execution(
        tool_name="fixture.write",
        input_payload={"value": "declared"},
        policy_payload={"effect": "external_write"},
        effect_class="external_write",
    )
    assert execution_id.startswith("tool-fixture.write-")

    with pytest.raises(GoalContractError, match="not declared"):
        run.admit_tool_execution(
            tool_name="fixture.other_write",
            input_payload={"value": "undeclared"},
            policy_payload={"effect": "external_write"},
            effect_class="external_write",
        )


def test_explicit_goal_without_external_effect_declaration_fails_closed() -> None:
    contract = _explicit_lab_contract()
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )
    assert contract.target.external_side_effects == ()
    with pytest.raises(GoalContractError, match="not declared"):
        run.admit_tool_execution(
            tool_name="fixture.write",
            input_payload={"value": "undeclared"},
            policy_payload={"effect": "external_write"},
            effect_class="external_write",
        )


def test_native_required_run_rejects_external_effect_without_explicit_target() -> None:
    run = LabRun(
        "native external effect declaration",
        require_native_authority=True,
        trust_level="DEV",
    )
    with pytest.raises(GoalContractError, match="explicit goal target declaration"):
        run.admit_tool_execution(
            tool_name="fixture.write",
            input_payload={"value": "must-not-run"},
            policy_payload={"effect": "external_write"},
            effect_class="external_write",
        )


def test_registered_managed_internal_memory_effect_is_replay_bound() -> None:
    run = LabRun(
        "managed internal memory effect",
        trust_level="DEV",
        trust_policy_hash=trust_policy_hash("DEV"),
    )
    run.bind_execution_cell_manifest(
        (
            {
                "cell_id": "post-completion-effect",
                "action_kinds": ("post_completion_effect",),
                "capabilities": ("memory_write",),
                "effect_classes": ("memory_write",),
                "trust_levels": ("DEV",),
            },
        )
    )
    execution_id, admission_id = run.admit_tool_execution(
        tool_name="memory.index_session",
        input_payload={"session": "managed"},
        policy_payload={"effect": "memory_write"},
        effect_class="memory_write",
        managed_internal=True,
    )
    run.record_tool_execution(
        tool_name="memory.index_session",
        execution_id=execution_id,
        admission_id=admission_id,
        input_payload={"session": "managed"},
        policy_payload={"effect": "memory_write"},
        result={"status": "COMMITTED"},
        effect_class="memory_write",
        managed_internal=True,
    )
    assert run.tool_executions == {execution_id}

    with pytest.raises(GoalContractError, match="not registered"):
        run.admit_tool_execution(
            tool_name="fixture.write",
            input_payload={"session": "must-not-run"},
            policy_payload={"effect": "memory_write"},
            effect_class="memory_write",
            managed_internal=True,
        )


def test_target_path_allowlist_is_effect_sensitive_and_boundary_safe() -> None:
    target = replace(
        _target(),
        read_roots=("C:/repo/input", "fixture://dataset"),
        write_roots=("C:/repo/output",),
    )
    assert target.allows_path("C:/repo/input/report.json", "read_only")
    assert target.allows_path("fixture://dataset/chunk-1", "read_only")
    assert not target.allows_path("C:/repo/input-archive/report.json", "read_only")
    assert target.allows_path("C:/repo/output/report.json", "local_reversible")
    assert not target.allows_path("C:/repo/input/report.json", "local_reversible")
    assert target.allows_path("C:/repo/output/report.json", "compute")
    assert not target.allows_path("C:/repo/input/report.json", "unregistered_effect")


def test_target_uri_paths_remove_dot_segments_and_reject_ambiguous_escapes() -> None:
    target = replace(
        _target(),
        read_roots=("fixture://dataset/input",),
    )
    assert target.allows_path("fixture://dataset/input/./report.json", "read_only")
    assert not target.allows_path("fixture://dataset/input/../secret.json", "read_only")
    assert not target.allows_path("fixture://dataset/input/%2e%2e/secret.json", "read_only")
    assert not target.allows_path("fixture://dataset/input/%2Fsecret.json", "read_only")
    assert not target.allows_path("fixture://dataset/input\\..\\secret.json", "read_only")

    with pytest.raises(ValueError, match="dot segments"):
        TargetDescriptor(
            kind="fixture",
            stable_id="dataset",
            revision_or_digest="revision",
            read_roots=("fixture://dataset/input/../secret",),
            owner="operator",
        )


def test_target_rejects_noncanonical_external_keys_and_bad_network_ports() -> None:
    with pytest.raises(ValueError, match="tool_name::effect_class"):
        TargetDescriptor(
            kind="repository",
            stable_id="repo",
            revision_or_digest="revision",
            external_side_effects=("tool::effect::extra",),
            owner="operator",
        )
    malformed_network_target = replace(_target(), network_allowlist=("https://pypi.org:not-a-port",))
    assert malformed_network_target.network_hosts() == ()
    with pytest.raises(GoalContractError, match="invalid or ambiguous host"):
        replace(_contract(), target=malformed_network_target).validate_for_admission()

    for entry in (
        "https://user:p@pypi.org",
        "https://pypi.org/path",
        "https://pypi.org?query=1",
        "https://*.pypi.org",
        "pypi.org/path",
    ):
        with pytest.raises(GoalContractError, match="invalid or ambiguous host"):
            replace(_contract(), target=replace(_target(), network_allowlist=(entry,))).validate_for_admission()

    valid_target = replace(_target(), network_allowlist=("HTTPS://pypi.org/", "localhost"))
    valid_contract = replace(_contract(), target=valid_target, policy_digest="a" * 64)
    valid_contract.validate_for_admission()


def test_explicit_goal_rejects_generic_tool_path_outside_target() -> None:
    contract = _explicit_lab_contract()
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )
    with pytest.raises(GoalContractError, match="target path"):
        run.admit_tool_execution(
            tool_name="fixture.read",
            input_payload={"path": "C:/repo-other/secret.txt"},
            policy_payload={"effect": "read_only"},
            effect_class="read_only",
        )


def test_explicit_external_effect_lease_requires_local_coordination(tmp_path) -> None:
    base = _explicit_lab_contract()
    external_key = external_side_effect_key("fixture.write", "external_write")
    contract = replace(
        base,
        target=replace(base.target, external_side_effects=(external_key,)),
    )
    with pytest.raises(GoalContractError, match="lease directory"):
        LabApplication._acquire_goal_effect_leases({"lab_goal_contract": contract})

    leases = LabApplication._acquire_goal_effect_leases(
        {
            "lab_goal_contract": contract,
            "lab_external_effect_lease_directory": str(tmp_path),
        }
    )
    try:
        assert len(leases) == 1
        assert leases[0].effect_key == external_key
        assert leases[0].acquired
    finally:
        for lease in reversed(leases):
            lease.release()


def test_target_network_allowlist_binds_search_program_and_rejects_widening() -> None:
    base = _explicit_lab_contract()
    assert base.target.network_hosts() == ("pypi.org",)
    assert base.target.allows_network_host("pypi.org")
    assert not base.target.allows_network_host("evil.example")
    run = LabRun(
        base.objective,
        max_steps=4,
        external_attempt_budget=base.budget.attempt_limit,
        scope=base.scope,
        non_goals=base.non_goals,
        goal_contract=base,
        token_budget=base.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=base.policy_digest,
    )
    program = SearchProgram.from_mappings(
        ({"kind": "fetch", "url": "https://pypi.org/"},),
    )
    bound = LabApplication._bind_search_program_to_goal_target(run, program)
    assert bound.allowed_hosts == ("pypi.org",)

    widened = replace(program, allowed_hosts=("evil.example",))
    with pytest.raises(GoalContractError, match="outside the goal target"):
        LabApplication._bind_search_program_to_goal_target(run, widened)


def test_explicit_goal_without_network_target_rejects_network_search() -> None:
    base = _explicit_lab_contract()
    contract = replace(base, target=replace(base.target, network_allowlist=()))
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )
    program = SearchProgram.from_mappings(
        ({"kind": "fetch", "url": "https://pypi.org/"},),
    )
    with pytest.raises(GoalContractError, match="does not authorize"):
        LabApplication._bind_search_program_to_goal_target(run, program)


def test_explicit_goal_binds_generic_network_tool_to_target_host() -> None:
    contract = _explicit_lab_contract()
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )
    config = type(
        "ToolConfig",
        (),
        {
            "max_steps": 2,
            "trust_level": "DEV",
            "task": contract.objective,
            "options": {
                "lab_allow_external_writes": False,
                "tool_calls": [
                    {
                        "tool_name": "fixture.network",
                        "effect_class": "network_read",
                        "input": {"url": "https://evil.example/"},
                    }
                ],
                "tool_runner": lambda *_args, **_kwargs: {"unexpected": True},
            },
        },
    )()
    application = LabApplication(
        config=config,
        gateway_factory=lambda **_: object(),
        telemetry=None,
        correlation=None,
    )
    application._active_run = run
    import asyncio

    application._prepare_execution_cells(run, config.options)
    asyncio.run(application._run_tool_calls(run, config.options))
    assert "tool_network_target_not_allowlisted" in run.blockers
    assert run.tool_execution_admissions == {}


def test_direct_lab_run_rejects_unbound_explicit_contract() -> None:
    contract = replace(_explicit_lab_contract(), policy_digest="unbound")
    with pytest.raises(GoalContractError, match="bound policy digest"):
        LabRun(
            contract.objective,
            max_steps=4,
            external_attempt_budget=contract.budget.attempt_limit,
            scope=contract.scope,
            non_goals=contract.non_goals,
            goal_contract=contract,
            token_budget=contract.budget.token_limit,
            finalization_reserve=80,
            recovery_reserve=40,
        )


def test_direct_lab_run_rejects_unsupported_record_type_policy() -> None:
    contract = replace(
        _explicit_lab_contract(),
        evidence_policy=replace(
            _explicit_lab_contract().evidence_policy,
            required_record_types=("test-report",),
        ),
    )
    with pytest.raises(GoalContractError, match="unsupported record types"):
        LabRun(
            contract.objective,
            max_steps=4,
            external_attempt_budget=contract.budget.attempt_limit,
            scope=contract.scope,
            non_goals=contract.non_goals,
            goal_contract=contract,
            token_budget=contract.budget.token_limit,
            finalization_reserve=80,
            recovery_reserve=40,
            trust_policy_hash=contract.policy_digest,
        )


def test_independent_goal_verification_is_event_bound_and_restorable() -> None:
    run, contract = _completed_explicit_run()
    progress = run.record_goal_verification(_verification(contract))

    assert progress.lifecycle is GoalLifecycle.SETTLED
    assert progress.verdict is GoalVerdict.VERIFIED
    verification_events = [
        event for event in run.events if event.kind == "review_recorded"
    ]
    assert len(verification_events) == 1
    assert verification_events[0].payload["record_type"] == "goal_verification"
    assert verification_events[0].payload["goal_contract_hash"] == contract.contract_hash
    restored = LabRun.from_payload(run.to_payload())
    assert restored.goal_progress_snapshot().verdict is GoalVerdict.VERIFIED

    tampered = run.to_payload()
    for event in tampered["events"]:
        if event["kind"] == "review_recorded":
            event["payload"]["verification"]["verifier_id"] = "forged"
            break
    with pytest.raises(ValueError):
        LabRun.from_payload(tampered)


def test_complete_goal_verification_requires_lab_evidence_and_source_quorum() -> None:
    run, contract = _completed_explicit_run(evidence_ref="missing-report")
    with pytest.raises(GoalContractError, match="absent"):
        run.record_goal_verification(_verification(contract, evidence_refs=("missing-report",)))

    run, contract = _completed_explicit_run(minimum_sources=2)
    with pytest.raises(GoalContractError, match="minimum source count"):
        run.record_goal_verification(_verification(contract))


def test_goal_verification_can_reference_retained_non_source_evidence() -> None:
    run, contract = _completed_explicit_run(evidence_ref="claim-1")
    progress = run.record_goal_verification(
        _verification(contract, evidence_refs=("claim-1", "test-report"))
    )
    assert progress.verdict is GoalVerdict.VERIFIED


def test_deterministic_lab_evaluator_links_predicates_to_retained_records() -> None:
    base = _explicit_lab_contract()
    contract = replace(
        base,
        acceptance=(
            replace(
                base.acceptance[0],
                evaluator="aegis.lab.record_exists",
                inputs=("claim", "claim-1"),
                evidence_refs=("claim-1",),
            ),
        ),
        evidence_policy=EvidencePolicy(
            required_record_types=("source", "claim"),
            minimum_sources=1,
            require_independent_verifier=False,
            trusted_verifier_ids=(),
        ),
    )
    run = LabRun(
        contract.objective,
        max_steps=4,
        external_attempt_budget=contract.budget.attempt_limit,
        scope=contract.scope,
        non_goals=contract.non_goals,
        goal_contract=contract,
        token_budget=contract.budget.token_limit,
        finalization_reserve=80,
        recovery_reserve=40,
        trust_policy_hash=contract.policy_digest,
    )
    run.add_source(
        SourceRecord(
            "test-report",
            "https://example.test/report",
            "content-hash",
            "snapshot-hash",
            1,
            trust_tier=1,
            relation="supports",
        )
    )
    run.add_claim(ClaimRecord("claim-1", "The required test passed.", ("test-report",), 9_000, "supported"))
    run.add_hypothesis(
        HypothesisRecord(
            "hypothesis-1",
            "The change satisfies the contract.",
            5_000,
            ("test fails",),
            ("claim-1",),
        )
    )
    run.add_experiment(
        ExperimentSpec(
            "experiment-1",
            "hypothesis-1",
            "bounded",
            ("result",),
            ("same revision",),
            (1, 2, 3, 4, 5),
            1,
        )
    )
    run.add_observation(
        ObservationRecord("observation-1", "experiment-1", 1, 1.0, "score", "raw", "env")
    )
    run.dossier(finalize=True)

    verification = run.evaluate_goal_verification()
    assert verification.verifier_id == "aegis.lab.deterministic-v1"
    assert verification.independent is False
    assert verification.evidence_complete is True
    assert verification.predicate_results == (("tests", True),)
    assert run.record_goal_verification(verification).verdict is GoalVerdict.VERIFIED


def test_deterministic_lab_evaluator_does_not_execute_unknown_code() -> None:
    base = _explicit_lab_contract()
    contract = replace(
        base,
        acceptance=(
            replace(
                base.acceptance[0],
                evaluator="python:os.remove_everything",
                inputs=(),
            ),
        ),
        evidence_policy=replace(base.evidence_policy, require_independent_verifier=False, trusted_verifier_ids=()),
    )
    run, _ = _completed_explicit_run()
    with pytest.raises(GoalContractError, match="unsupported deterministic Lab evaluator"):
        run._evaluate_goal_predicate(contract.acceptance[0])


def test_lab_application_uses_explicit_contract_policy_as_authoritative() -> None:
    contract = _explicit_lab_contract()
    captured: list[LabRun] = []

    async def empty_context(*_args, **_kwargs):
        return ""

    async def empty_lane(*_args, **_kwargs):
        return None

    async def fake_gateway(*_args, **_kwargs):
        return SimpleNamespace(output="bounded synthesis")

    async def empty_post_effect(*_args, **_kwargs):
        return None

    config = SimpleNamespace(
        task=contract.objective,
        max_steps=4,
        browser=False,
        trust_level="DEV",
        options={
            "lab_goal_contract": contract,
            "lab_goal_contract_explicit": True,
        },
    )
    application = LabApplication(
        config=config,
        gateway_factory=lambda **_: object(),
        telemetry=None,
        correlation=None,
        run_sink=captured.append,
    )
    application._prepare_execution_cells = lambda *_args, **_kwargs: None
    application._run_context_retrieval = empty_context
    application._run_skill_requests = empty_lane
    application._run_tool_calls = empty_lane
    application._run_admitted_gateway = fake_gateway
    application._run_post_completion_effect = empty_post_effect

    asyncio.run(application._run_unleased())

    assert len(captured) == 1
    run = captured[0]
    assert run.goal_contract == contract
    assert run.scope == contract.scope
    assert run.non_goals == contract.non_goals
    assert run.token_budget == contract.budget.token_limit
    assert run.external_attempt_budget == contract.budget.attempt_limit
    assert run.finalization_reserve + run.recovery_reserve == contract.budget.reserved_tokens


def test_mission_spec_preserves_compatibility_but_marks_unbound_contract() -> None:
    spec = LabMissionSpec(
        "legacy lab task",
        scope=("read-only",),
        non_goals=("publish",),
    )
    contract = spec.to_goal_contract(
        policy_digest="policy-digest",
        budget=_budget(),
    )
    assert not contract.has_explicit_acceptance
    assert not contract.target.is_bound
    with pytest.raises(GoalContractError):
        contract.validate_for_admission()
