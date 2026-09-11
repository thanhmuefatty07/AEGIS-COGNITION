from __future__ import annotations

from pathlib import Path

from scripts import release_evidence


def test_git_revision_fails_closed_for_dirty_worktree(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_check_output(command, **kwargs):
        calls.append(list(command))
        return " M source.py\n"

    monkeypatch.setattr(release_evidence.subprocess, "check_output", fake_check_output)

    assert release_evidence.git_revision(Path(".")) == "WORKTREE_DIRTY"
    assert calls == [["git", "status", "--porcelain=v1", "--untracked-files=all"]]


def test_git_revision_uses_declared_sha_only_for_clean_worktree(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_check_output(command, **kwargs):
        calls.append(list(command))
        if command[1] == "status":
            return ""
        return "abc123\n"

    monkeypatch.setenv("GITHUB_SHA", "declared-sha")
    monkeypatch.setattr(release_evidence.subprocess, "check_output", fake_check_output)

    assert release_evidence.git_revision(Path(".")) == "declared-sha"
    assert calls == [["git", "status", "--porcelain=v1", "--untracked-files=all"]]
