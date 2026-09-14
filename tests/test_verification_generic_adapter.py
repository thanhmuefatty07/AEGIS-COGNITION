from __future__ import annotations

from aegis_cognition.verification.adapters import GenericBoundedAdapter


def test_adapter_builds_structured_command_without_shell() -> None:
    adapter = GenericBoundedAdapter()
    command = adapter.build_command(
        executable="pytest",
        argv=("-q", "tests/test_feature.py"),
        working_directory="C:/workspace",
        timeout_seconds=30.0,
        environment={"PYTHONHASHSEED": "0"},
    )
    assert command.argv == ("-q", "tests/test_feature.py")


def test_adapter_rejects_zero_test_pass_and_malformed_result() -> None:
    adapter = GenericBoundedAdapter()
    zero = adapter.parse_result({"status": "PASS", "discovered": 0})
    assert zero.status == "MALFORMED"
    malformed = adapter.parse_result({"status": "opaque", "discovered": 1})
    assert malformed.status == "MALFORMED"


def test_adapter_classifies_timeout_cancellation_and_build_failures() -> None:
    adapter = GenericBoundedAdapter()
    assert adapter.classify_failure(exit_code=1, timed_out=True) == "TIMEOUT"
    assert adapter.classify_failure(exit_code=1, cancelled=True) == "CANCELLATION"
    assert adapter.classify_failure(exit_code=1, output="collection error") == "COLLECTION_OR_BUILD_ERROR"
