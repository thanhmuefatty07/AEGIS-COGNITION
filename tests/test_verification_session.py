from __future__ import annotations

from pathlib import Path

import pytest

from aegis_cognition.verification import VerificationFacade, VerificationSessionError


def _session(tmp_path: Path) -> tuple[VerificationFacade, str]:
    facade = VerificationFacade()
    profile = facade.inspect_project(tmp_path)
    requirements = facade.create_contract(
        "implement the requested behavior",
        expected_behavior="the function returns the documented value",
        source_revision=profile.source_revision,
        policy_hash="policy-1",
    )
    session = facade.start_session(profile, requirements)
    return facade, session.session_id


def test_packet_exists_before_change_and_uses_shadow_authority(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    packet = facade.get_agent_packet(session_id)
    assert packet.can_start is True
    assert packet.authority_state == "SHADOW_ONLY"
    assert "selection_authority_disabled" in packet.known_risks
    assert packet.plan["selection_mode"] == "SHADOW_ONLY"


def test_change_invalidates_provisional_evidence_and_unknown_scope_widens(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    result = facade.observe_change(session_id, ["src/module.py"], source_revision="revision-b")
    assert result["evidence_invalidated"] is True
    assert result["scope_policy"] == "WIDEN_TO_RETAINED_SUITE_ON_UNKNOWN"
    feedback = facade.get_feedback(session_id)
    assert feedback["provisional"] is True
    assert feedback["final_assurance"] is False


def test_test_candidate_without_oracle_or_with_skip_is_rejected(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, "test body", ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "oracle_missing" in evaluated.error_codes

    proposal = facade.propose_test_change(
        session_id,
        "pytest.mark.skip\ndef test_feature(): assert 3 == 3",
        ["tests/test_feature.py"],
    )
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "test_weakening_detected" in evaluated.error_codes


def test_candidate_is_not_applied_in_shadow_mode(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(
        session_id,
        "def test_feature(): assert result == 3",
        ["tests/test_feature.py"],
    )
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "CANDIDATE"
    result = facade.apply_test_change(session_id, evaluated.proposal_id)
    assert result["status"] == "BLOCKED_SHADOW_ONLY"


def test_invalid_path_and_missing_session_fail_closed(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    with pytest.raises(VerificationSessionError, match="escapes"):
        facade.observe_change(session_id, ["../outside.py"])
    with pytest.raises(VerificationSessionError, match="not found"):
        facade.get_feedback("missing-session")


def test_incomplete_requirement_cannot_start_agent_packet(tmp_path: Path) -> None:
    facade = VerificationFacade()
    profile = facade.inspect_project(tmp_path)
    requirements = facade.create_contract("implement something", source_revision=profile.source_revision)
    session = facade.start_session(profile, requirements)
    packet = facade.get_agent_packet(session.session_id)
    assert packet.can_start is False
    assert "requirements_incomplete" in packet.known_risks
