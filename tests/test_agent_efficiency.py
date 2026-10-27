from __future__ import annotations

import os
import subprocess
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
    repository = tmp_path / "plain-project"
    repository.mkdir()
    (repository / "source.txt").write_text("source", encoding="utf-8")
    scratch_root = repository / ".local" / "tmp" / "worktrees"
    created: list[Path] = []
    with DisposableWorktree(repository, lineage="root/child") as worktree:
        assert worktree.parent == scratch_root
        assert (worktree / "source.txt").read_text(encoding="utf-8") == "source"
        (worktree / "child.txt").write_text("child", encoding="utf-8")
        created.append(worktree)
        with DisposableWorktree(repository) as second_worktree:
            assert second_worktree.parent == scratch_root
            assert second_worktree != worktree
            created.append(second_worktree)
    assert all(not path.exists() for path in created)


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        capture_output=True,
        text=True,
        check=True,
    )


def _initialize_git_repository(repository: Path) -> None:
    repository.mkdir(parents=True)
    _git(repository, "init", "--initial-branch=main", "--quiet")
    (repository / "tracked.txt").write_text("committed source\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(
        repository,
        "-c",
        "user.name=WP03 test",
        "-c",
        "user.email=wp03@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )


def _registered_worktree_paths(repository: Path) -> set[Path]:
    output = _git(repository, "worktree", "list", "--porcelain").stdout
    return {
        Path(line.removeprefix("worktree ")).resolve() for line in output.splitlines() if line.startswith("worktree ")
    }


def _create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        created = subprocess.run(
            ("cmd.exe", "/c", "mklink", "/J", str(link), str(target)),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        assert created.returncode == 0, created.stderr or created.stdout
        assert link.is_junction()
        return

    link.symlink_to(target, target_is_directory=True)
    assert link.is_symlink()


def _remove_directory_link(link: Path) -> None:
    if link.is_junction():
        link.rmdir()
    else:
        link.unlink(missing_ok=True)


def test_disposable_git_worktree_uses_ref_without_copying_dirty_source(tmp_path: Path) -> None:
    repository = tmp_path / "git-project"
    _initialize_git_repository(repository)
    (repository / "tracked.txt").write_text("dirty source\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked source\n", encoding="utf-8")
    source_status = _git(repository, "status", "--short").stdout
    created: Path | None = None

    with DisposableWorktree(repository) as worktree:
        assert worktree.parent == repository / ".local" / "tmp" / "worktrees"
        assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "committed source\n"
        assert not (worktree / "untracked.txt").exists()
        assert worktree.resolve() in _registered_worktree_paths(repository)
        created = worktree

    assert created is not None
    assert not created.exists()
    assert created.resolve() not in _registered_worktree_paths(repository)
    assert _git(repository, "status", "--short").stdout == source_status


def test_disposable_worktree_copies_a_project_inside_an_ancestor_repository(tmp_path: Path) -> None:
    ancestor = tmp_path / "ancestor"
    _initialize_git_repository(ancestor)
    project = ancestor / "workspace"
    project.mkdir()
    (project / "source.py").write_text("def answer(): return 42\n", encoding="utf-8")

    with DisposableWorktree(project) as copy:
        assert (copy / "source.py").is_file()
        assert not (copy / "tracked.txt").exists()
        assert not (copy / ".git").exists()

    with pytest.raises(RuntimeError, match="requested Git ref requires"):
        DisposableWorktree(project, ref="HEAD").__enter__()


def test_disposable_git_worktree_cleans_up_after_body_failure(tmp_path: Path) -> None:
    repository = tmp_path / "git-project"
    _initialize_git_repository(repository)
    created: Path | None = None

    with pytest.raises(RuntimeError, match="synthetic body failure"), DisposableWorktree(repository) as worktree:
        assert worktree.parent == repository / ".local" / "tmp" / "worktrees"
        created = worktree
        raise RuntimeError("synthetic body failure")

    assert created is not None
    assert not created.exists()
    assert created.resolve() not in _registered_worktree_paths(repository)


def test_disposable_worktree_rejects_redirected_scratch_root(tmp_path: Path) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected_root = repository / ".local"
    _create_directory_link(redirected_root, outside)
    try:
        with pytest.raises(ValueError), DisposableWorktree(repository):
            pytest.fail("a redirected scratch escape must be rejected before copying")
        assert list(outside.iterdir()) == []
    finally:
        _remove_directory_link(redirected_root)


def test_disposable_worktree_does_not_fallback_when_requested_ref_is_missing(tmp_path: Path) -> None:
    repository = tmp_path / "git-project"
    _initialize_git_repository(repository)

    with pytest.raises(RuntimeError), DisposableWorktree(repository, ref="refs/heads/does-not-exist"):
        pytest.fail("a missing requested ref must not silently use the working tree")

    assert len(_registered_worktree_paths(repository)) == 1
    scratch = repository / ".local" / "tmp" / "worktrees"
    assert not scratch.exists() or list(scratch.iterdir()) == []


def test_disposable_worktree_rejects_nul_ref_before_creating_scratch(tmp_path: Path) -> None:
    repository = tmp_path / "git-project"
    _initialize_git_repository(repository)

    with pytest.raises(ValueError, match="ref must not contain NUL"):
        DisposableWorktree(repository, ref="HEAD\x00--help")

    assert not (repository / ".local" / "tmp" / "worktrees").exists()


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
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_secret = outside / "outside-secret.txt"
    outside_secret.write_text("synthetic-external-value", encoding="utf-8")
    link = repository / "linked-secrets"
    _create_directory_link(link, outside)

    try:
        with DisposableWorktree(repository) as worktree:
            assert not (worktree / "linked-secrets").exists()
            assert outside_secret.read_text(encoding="utf-8") == "synthetic-external-value"
    finally:
        _remove_directory_link(link)


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
    assert temporary_roots[0].parent == repository / ".local" / "tmp" / "worktrees"
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
    packet = AgentResultPacket(
        run_id="run", task_id=1, parent_task_id=None, attempt_id=1, status="SUCCEEDED", summary="bounded evidence"
    )
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
