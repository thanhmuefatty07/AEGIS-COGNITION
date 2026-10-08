from __future__ import annotations

from pathlib import Path

import pytest

from aegis_cognition import agent_efficiency as agent_efficiency_module
from aegis_cognition.agent_efficiency import (
    ActionFusion,
    DisposableWorktree,
    EvidencePreservingReducer,
    ObservationPack,
    evaluate_capability_floor,
)
from aegis_cognition.subagents import AgentEvidenceBoard, AgentResultPacket, build_root_synthesizer


def test_observation_pack_sends_two_full_observations_then_recallable_projection(tmp_path: Path) -> None:
    pack = ObservationPack(archive_root=tmp_path, threshold_bytes=10, excerpt_bytes=5)
    text = "0123456789abcdef"
    assert not pack.pack("tool-1", text).archived
    assert not pack.pack("tool-1", text).archived
    projection = pack.pack("tool-1", text)
    assert projection.archived
    assert projection.recall_handle is not None
    assert pack.recall(projection.recall_handle) == text


def test_evidence_reducer_keeps_exact_quotes_and_falls_back_for_secrets() -> None:
    source = "\n".join(["test start"] * 4 + ["ERROR: deterministic failure"] + ["output"] * 1_200)
    result = EvidencePreservingReducer(minimum_bytes=100).reduce(source, command="pytest", exit_code=1)
    assert result.used_reduction
    assert result.receipt is not None
    assert result.receipt.verify(source)
    assert "ERROR: deterministic failure" in result.reduced
    secret = EvidencePreservingReducer(minimum_bytes=1).reduce("Authorization: Bearer abc", command="curl", exit_code=1)
    assert not secret.used_reduction


def test_action_fusion_runs_command_only_after_mutation_and_rejects_inspection(tmp_path: Path) -> None:
    path = tmp_path / "action-fusion-test.txt"
    path.write_text("before", encoding="utf-8")
    try:
        events: list[str] = []

        def mutate(target: Path) -> str:
            events.append("mutate")
            target.write_text("after", encoding="utf-8")
            return "changed"

        def command(target: Path) -> str:
            events.append("command")
            return target.read_text(encoding="utf-8")

        result = ActionFusion().run(path, mutate, command)
        assert events == ["mutate", "command"]
        assert result.command_output == "after"
        with pytest.raises(ValueError, match="intermediate inspection"):
            ActionFusion().run(path, mutate, command, requires_intermediate_inspection=True)
    finally:
        path.unlink(missing_ok=True)


def test_disposable_worktree_is_removed_after_use(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    with DisposableWorktree(tmp_path, lineage="root/child") as worktree:
        assert (worktree / "source.txt").read_text(encoding="utf-8") == "source"
        (worktree / "child.txt").write_text("child", encoding="utf-8")
        created = worktree
    assert not created.exists()


def test_disposable_worktree_copy_excludes_private_local_data(tmp_path: Path) -> None:
    repository = tmp_path / "plain-project"
    repository.mkdir()
    (repository / "source.txt").write_text("source", encoding="utf-8")
    (repository / ".env").write_text("synthetic-private-value", encoding="utf-8")
    local_data = repository / ".local"
    local_data.mkdir()
    (local_data / "private-archive.path").write_text("synthetic-private-path", encoding="utf-8")
    credentials = repository / "credentials"
    credentials.mkdir()
    (credentials / "provider.json").write_text("synthetic-credential", encoding="utf-8")

    with DisposableWorktree(repository) as worktree:
        assert (worktree / "source.txt").read_text(encoding="utf-8") == "source"
        assert not (worktree / ".env").exists()
        assert not (worktree / ".local").exists()
        assert not (worktree / "credentials").exists()


def test_disposable_worktree_copy_does_not_follow_symlinks(tmp_path: Path) -> None:
    repository = tmp_path / "plain-project"
    repository.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("synthetic-external-value", encoding="utf-8")
    link = repository / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows account")

    with DisposableWorktree(repository) as worktree:
        assert not (worktree / "linked-secret.txt").exists()
        assert outside.read_text(encoding="utf-8") == "synthetic-external-value"


def test_disposable_worktree_copy_failure_removes_partial_temporary_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "plain-project"
    repository.mkdir()
    temporary_roots: list[Path] = []
    create_temp_directory = agent_efficiency_module.tempfile.mkdtemp

    def capture_temp_directory(*args, **kwargs) -> str:
        temporary = Path(create_temp_directory(*args, **kwargs))
        temporary_roots.append(temporary)
        return str(temporary)

    def fail_after_partial_copy(_source: Path, destination: Path, *, ignore) -> None:
        destination.mkdir()
        (destination / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("synthetic copy failure")

    monkeypatch.setattr(agent_efficiency_module.tempfile, "mkdtemp", capture_temp_directory)
    monkeypatch.setattr(agent_efficiency_module.shutil, "copytree", fail_after_partial_copy)

    with pytest.raises(OSError, match="synthetic copy failure"):
        DisposableWorktree(repository).__enter__()

    assert len(temporary_roots) == 1
    assert not temporary_roots[0].exists()


def test_capability_floor_and_cost_are_separate() -> None:
    passed = evaluate_capability_floor(
        {"task-a": 1.0, "task-b": 0.8},
        {"task-a": 1.0, "task-b": 0.8},
        baseline_cost=10.0,
        candidate_cost=8.0,
    )
    failed = evaluate_capability_floor(
        {"task-a": 1.0},
        {"task-a": 0.9},
        baseline_cost=10.0,
        candidate_cost=8.0,
    )
    assert passed.floor_passed and passed.efficiency_improved
    assert not failed.floor_passed and failed.efficiency_improved


@pytest.mark.asyncio
async def test_selective_evidence_board_feeds_root_without_transcript() -> None:
    board = AgentEvidenceBoard(max_entries=4)
    packet = AgentResultPacket(run_id="run", task_id=1, parent_task_id=None, attempt_id=1, status="SUCCEEDED", summary="bounded evidence")
    board.publish(packet.to_message(recipient_id="root"))
    snapshot = board.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["summary"] == "bounded evidence"
    assert "prompt" not in snapshot[0]

    prompts: list[str] = []

    async def invoker(prompt: str) -> str:
        prompts.append(prompt)
        return "root"

    root = build_root_synthesizer(invoker, evidence_board=board)
    assert await root((packet,)) == "root"
    assert "bounded evidence" in prompts[0]
