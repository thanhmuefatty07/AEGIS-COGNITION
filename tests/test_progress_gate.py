from scripts.progress_gate import evaluate_progress_gate


def test_progress_gate_is_not_ok_when_required_artifacts_are_missing(tmp_path) -> None:
    report = evaluate_progress_gate(tmp_path)

    assert report["overall_readiness_ppm"] == 0
    assert report["overall_ok"] is False
