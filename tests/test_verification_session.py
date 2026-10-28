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


def _passing_result(source_revision: str, working_directory: Path) -> dict[str, object]:
    return {
        "source_revision": source_revision,
        "command_id": "command-1",
        "adapter": "fixture",
        "framework": "pytest",
        "toolchain": "python-3.14",
        "platform": "test-platform",
        "executable": "python",
        "argv": ("-m", "pytest", "tests/test_example.py"),
        "working_directory": str(working_directory),
        "environment_fingerprint": {},
        "timeout_seconds": 5.0,
        "cancellation_requested": False,
        "exit_code": 0,
        "exit_semantics": "PROCESS_EXIT",
        "status": "PASS",
        "discovered": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 0,
        "filtered": 0,
        "ignored": 0,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:01+00:00",
    }


def test_cancelled_run_rejects_late_result_without_reopening_session(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    run_id = str(facade.request_deep_run(session_id)["run_id"])
    facade.cancel_run(session_id, run_id)
    before = facade.get_feedback(session_id)

    with pytest.raises(VerificationSessionError, match=r"cancelled|terminal"):
        facade.record_execution_result(
            session_id,
            run_id,
            _passing_result(str(before["source_revision"]), tmp_path),
        )

    assert facade.inspect_run(session_id, run_id)["status"] == "CANCELLED"
    assert facade.get_feedback(session_id) == before
    report = facade.read_report(session_id)
    assert report["session"]["state"] == "CANCELLED"
    assert report["assessment"]["receipt_ids"] == []


def test_read_report_is_repeatable_and_does_not_change_session(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    run_id = str(facade.request_deep_run(session_id)["run_id"])
    before_result = facade.get_feedback(session_id)
    facade.record_execution_result(
        session_id,
        run_id,
        _passing_result(str(before_result["source_revision"]), tmp_path),
    )
    before_report = facade.get_feedback(session_id)

    first = facade.read_report(session_id)
    second = facade.read_report(session_id)

    assert first == second
    assert first["session"]["state"] == before_report["status"]
    assert facade.get_feedback(session_id) == before_report


def test_changed_inputs_keep_old_receipt_as_history_but_out_of_current_assessment(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    run_id = str(facade.request_deep_run(session_id)["run_id"])
    before_result = facade.get_feedback(session_id)
    facade.record_execution_result(
        session_id,
        run_id,
        _passing_result(str(before_result["source_revision"]), tmp_path),
    )
    facade.observe_change(session_id, ["src/module.py"], source_revision="revision-b")
    before_report = facade.get_feedback(session_id)

    report = facade.read_report(session_id)

    assert report["session"]["state"] == "STALE"
    assert report["execution"] == "STALE"
    assert set(report["assessment"]["requirement_status"].values()) == {"INCOMPLETE"}
    assert report["assessment"]["receipt_ids"] == []
    assert len(report["receipts"]) == 1
    assert facade.get_feedback(session_id) == before_report


def test_input_change_invalidates_requested_run_before_late_result(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    run_id = str(facade.request_deep_run(session_id)["run_id"])
    facade.observe_change(session_id, ["src/module.py"], source_revision="revision-b")

    with pytest.raises(VerificationSessionError, match=r"stale|terminal"):
        facade.record_execution_result(session_id, run_id, _passing_result("revision-b", tmp_path))

    assert facade.inspect_run(session_id, run_id)["status"] == "STALE"
    assert facade.read_report(session_id)["assessment"]["receipt_ids"] == []


def test_unbound_pass_receipt_cannot_pass_every_requirement(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    run_id = str(facade.request_deep_run(session_id)["run_id"])
    before_result = facade.get_feedback(session_id)
    facade.record_execution_result(
        session_id,
        run_id,
        _passing_result(str(before_result["source_revision"]), tmp_path),
    )

    assessment = facade.read_report(session_id)["assessment"]

    assert set(assessment["requirement_status"].values()) == {"INCOMPLETE"}
    assert "receipt_obligation_binding_missing" in assessment["error_codes"]


def test_deleted_or_commented_assertion_is_rejected(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    deleted_patch = "@@ -1,3 +1,2 @@\n def test_feature():\n-    assert result == 3\n+    result = compute()\n"
    proposal = facade.propose_test_change(session_id, deleted_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "oracle_missing" in evaluated.error_codes
    assert "test_weakening_detected" in evaluated.error_codes

    commented_patch = "def test_feature():\n    # assert result == 3\n    pass\n"
    proposal2 = facade.propose_test_change(session_id, commented_patch, ["tests/test_feature.py"])
    evaluated2 = facade.evaluate_test_change(session_id, proposal2.proposal_id)
    assert evaluated2.status == "REJECTED"
    assert "oracle_missing" in evaluated2.error_codes


@pytest.mark.parametrize(
    "tautology_patch",
    [
        "def test_feature():\n    assert True\n",
        "def test_feature():\n    assert not False\n",
        "def test_feature():\n    assert result == result\n",
        "#[test]\nfn test_feature() {\n    assert_eq!(value, value);\n}\n",
    ],
)
def test_tautological_oracle_is_rejected(tmp_path: Path, tautology_patch: str) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, tautology_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "tautological_oracle_detected" in evaluated.error_codes
    assert "test_weakening_detected" in evaluated.error_codes


@pytest.mark.parametrize(
    "spoof_patch",
    [
        "def test_feature():\n    sys.exit(0)\n    assert result == 3\n",
        "def test_feature():\n    os._exit(0)\n    assert result == 3\n",
        'def test_feature():\n    print("1 passed in 0.01s")\n    assert result == 3\n',
        "def test_feature():\n    try:\n        assert result == 3\n    except AssertionError:\n        pass\n",
        "def pytest_runtest_makereport(item, call):\n    assert item is not None\n",
    ],
)
def test_runner_spoofing_in_test_candidate_is_rejected(tmp_path: Path, spoof_patch: str) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, spoof_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "runner_spoofing_detected" in evaluated.error_codes
    assert "test_weakening_detected" in evaluated.error_codes


@pytest.mark.parametrize(
    "bloat_patch",
    [
        "def test_feature():\n    time.sleep(2.0)\n    assert result == 3\n",
        "def test_dup():\n    assert a == 1\ndef test_dup():\n    assert b == 2\n",
    ],
)
def test_bloated_test_candidate_is_rejected(tmp_path: Path, bloat_patch: str) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, bloat_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "test_bloat_detected" in evaluated.error_codes
    assert "test_weakening_detected" in evaluated.error_codes


def test_contradictory_pass_execution_result_fails_closed_to_malformed(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    run_id = str(facade.request_deep_run(session_id)["run_id"])
    before = facade.get_feedback(session_id)
    bad_result = _passing_result(str(before["source_revision"]), tmp_path)
    bad_result["passed"] = 0
    bad_result["discovered"] = 0

    receipt = facade.record_execution_result(session_id, run_id, bad_result)
    assert receipt.status == "MALFORMED"


def test_observe_change_emits_owning_candidate_test_hints(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_widget.py").write_text("def test_widget(): assert True\n", encoding="utf-8")

    facade, session_id = _session(tmp_path)
    result = facade.observe_change(session_id, ["src/widget.py"], source_revision="rev-2")
    assert result["candidate_test_hints"] == ("tests/test_widget.py",)
    assert result["scope_policy"] == "WIDEN_TO_RETAINED_SUITE_ON_UNKNOWN"


@pytest.mark.parametrize(
    "string_or_doc_patch",
    [
        'def test_fake():\n    msg = "assert result == 3"\n',
        'def test_fake():\n    """\n    assert result == 3\n    """\n    pass\n',
        "#[test]\nfn test_fake() {\n    /* assert_eq!(actual, expected); */\n}\n",
    ],
)
def test_oracle_inside_string_literal_or_multiline_comment_is_rejected(
    tmp_path: Path, string_or_doc_patch: str
) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, string_or_doc_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "oracle_missing" in evaluated.error_codes
    assert not evaluated.oracle_present


@pytest.mark.parametrize(
    "ast_tautology_patch",
    [
        "def test_alias():\n    a = compute()\n    b = a\n    assert a == b\n",
        "def test_const_math():\n    assert 1 + 1 == 2\n",
    ],
)
def test_ast_aliased_and_constant_tautologies_are_rejected(tmp_path: Path, ast_tautology_patch: str) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, ast_tautology_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "tautological_oracle_detected" in evaluated.error_codes


@pytest.mark.parametrize(
    "smuggled_patch",
    [
        "+assert result == 3\nos._exit(0)\n",
        'def test_dynamic_exit():\n    getattr(sys, "exit")(0)\n    assert result == 3\n',
        'def test_import_exit():\n    __import__("os")._exit(0)\n    assert result == 3\n',
    ],
)
def test_hybrid_diff_smuggling_and_dynamic_runner_spoofing_are_rejected(tmp_path: Path, smuggled_patch: str) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, smuggled_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "runner_spoofing_detected" in evaluated.error_codes


def test_removing_skip_directive_or_naming_test_skip_is_accepted(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    unskip_diff = (
        "@@ -1,3 +1,2 @@\n"
        "-@pytest.mark.skip(reason='old')\n"
        " def test_skip_invalid_rows():\n"
        "+    assert skip_invalid_rows([1, 2]) == [1, 2]\n"
    )
    proposal = facade.propose_test_change(session_id, unskip_diff, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "CANDIDATE"
    assert evaluated.oracle_present
    assert evaluated.error_codes == ()


def test_observe_change_omits_nonexistent_test_hint_when_root_exists(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "missing_test_target.py").write_text("x = 1\n", encoding="utf-8")
    facade, session_id = _session(tmp_path)
    result = facade.observe_change(session_id, ["src/missing_test_target.py"], source_revision="rev-3")
    assert result["candidate_test_hints"] == ()


@pytest.mark.parametrize(
    "spoof_variant",
    [
        "def test_exit_empty():\n    sys.exit()\n    assert result == 3\n",
        "def test_exit_none():\n    sys.exit(None)\n    assert result == 3\n",
        "from sys import exit as quit_now\ndef test_imported_exit():\n    quit_now()\n    assert result == 3\n",
        "import sys as s\ndef test_aliased_sys_exit():\n    s.exit(None)\n    assert result == 3\n",
        "def test_raise_system_exit():\n    raise SystemExit(0)\n    assert result == 3\n",
        "def test_bare_except():\n    try:\n        assert result == 3\n    except:\n        pass\n",
        "@@ -2,2 +2,3 @@\n+    except (ValueError, AssertionError): pass\n+    assert result == 3\n",
        "def test_multiline_except():\n    try:\n        assert result == 3\n    except (\n        AssertionError,\n        ValueError,\n    ):\n        return None\n",
    ],
)
def test_exit_and_multiline_swallowing_except_variants_are_rejected(tmp_path: Path, spoof_variant: str) -> None:
    facade, session_id = _session(tmp_path)
    proposal = facade.propose_test_change(session_id, spoof_variant, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "runner_spoofing_detected" in evaluated.error_codes
    assert "test_weakening_detected" in evaluated.error_codes


def test_net_assertion_reduction_is_rejected_as_weakening(tmp_path: Path) -> None:
    facade, session_id = _session(tmp_path)
    reduced_patch = (
        "@@ -1,4 +1,3 @@\n"
        " def test_feature():\n"
        "-    assert result.status == 200\n"
        "-    assert result.payload == {'ok': True}\n"
        "+    assert result.status == 200\n"
    )
    proposal = facade.propose_test_change(session_id, reduced_patch, ["tests/test_feature.py"])
    evaluated = facade.evaluate_test_change(session_id, proposal.proposal_id)
    assert evaluated.status == "REJECTED"
    assert "test_weakening_detected" in evaluated.error_codes
