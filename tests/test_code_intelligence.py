import os
import subprocess
import time
from pathlib import Path

import pytest

from core.python.aegis.code_intelligence import (
    BoundedSnapshotWatcher,
    NativeSnapshotWatcher,
    SourceMapper,
    compare_snapshots,
    invalidate_dependencies,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_non_git_snapshot_extracts_supported_symbols_and_excludes_generated(tmp_path):
    (tmp_path / "app.py").write_text("import json\n\nclass App:\n    def run(self, value):\n        return value\n", encoding="utf-8")
    (tmp_path / "rust.rs").write_text("fn run(value: i32) -> i32 { value }\n", encoding="utf-8")
    (tmp_path / "ui.ts").write_text("export function render() { return true }\n", encoding="utf-8")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "generated.rs").write_text("fn generated() {}\n", encoding="utf-8")

    snapshot = SourceMapper(tmp_path).snapshot()

    assert snapshot.identity.vcs == "none"
    assert {record.relative_path for record in snapshot.files} == {"app.py", "rust.rs", "ui.ts"}
    app = next(record for record in snapshot.files if record.relative_path == "app.py")
    assert app.extraction_status == "EXTRACTED"
    assert {symbol.name for symbol in app.symbols} == {"App", "run"}
    assert app.imports == ("json",)


def test_syntax_error_is_labeled_and_duplicate_signature_is_candidate(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    source = "def shared(value):\n    return value\n"
    (tmp_path / "first.py").write_text(source, encoding="utf-8")
    (tmp_path / "second.py").write_text(source, encoding="utf-8")

    snapshot = SourceMapper(tmp_path).snapshot()

    broken = next(record for record in snapshot.files if record.relative_path == "broken.py")
    assert broken.extraction_status == "SYNTAX_ERROR"
    assert broken.error and "invalid" in broken.error
    assert any(item.reason == "duplicate-signature" for item in snapshot.lineage)


def test_syntax_error_keeps_previous_symbols_as_explicit_stale_fallback(tmp_path):
    source = tmp_path / "service.py"
    source.write_text("def serve(value):\n    return value\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    before = mapper.snapshot()
    source.write_text("def serve(:\n", encoding="utf-8")

    current = mapper.snapshot(before)

    record = next(record for record in current.files if record.relative_path == "service.py")
    assert record.extraction_status == "SYNTAX_ERROR_STALE_FALLBACK"
    assert {symbol.name for symbol in record.symbols} == {"serve"}
    assert current.stale_paths == ("service.py",)


def test_pattern_extractor_ignores_comments_and_strings(tmp_path):
    source = tmp_path / "ui.ts"
    source.write_text(
        '// function fake() {}\nconst text = "class AlsoFake {}";\nfunction real() { return true; }\n',
        encoding="utf-8",
    )

    snapshot = SourceMapper(tmp_path).snapshot()

    record = snapshot.files[0]
    assert record.extraction_status == "LEXICAL_CANDIDATE"
    assert {symbol.name for symbol in record.symbols} == {"text", "real"}


def test_dependency_invalidation_reaches_transitive_dependents(tmp_path):
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import c\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("value = 1\n", encoding="utf-8")
    snapshot = SourceMapper(tmp_path).snapshot()

    assert invalidate_dependencies(snapshot, ["c.py"]) == ("a.py", "b.py", "c.py")


def test_snapshot_limits_apply_against_previous_snapshots(tmp_path):
    for name in ("one.py", "two.py", "three.py"):
        (tmp_path / name).write_text(f"value = '{name}'\n", encoding="utf-8")
    before = SourceMapper(tmp_path).snapshot()
    mapper = SourceMapper(tmp_path, max_files=1)

    current = mapper.snapshot(before, watcher=BoundedSnapshotWatcher())

    assert len(current.files) == 1
    assert current.overflowed is True
    assert current.deleted_paths == ()


def test_snapshot_delta_distinguishes_rename_and_copy(tmp_path):
    old = tmp_path / "old.py"
    old.write_text("def stable():\n    return 1\n", encoding="utf-8")
    before_rename = SourceMapper(tmp_path).snapshot()
    old.rename(tmp_path / "moved.py")
    after_rename = SourceMapper(tmp_path).snapshot(before_rename)
    rename_delta = compare_snapshots(before_rename, after_rename)
    assert rename_delta.renamed == (("old.py", "moved.py"),)

    (tmp_path / "copy.py").write_text((tmp_path / "moved.py").read_text(encoding="utf-8"), encoding="utf-8")
    after_copy = SourceMapper(tmp_path).snapshot(after_rename)
    copy_delta = compare_snapshots(after_rename, after_copy)
    assert copy_delta.copied == (("moved.py", "copy.py"),)


def test_watcher_incremental_snapshot_marks_stale_and_deleted(tmp_path):
    changed = tmp_path / "changed.py"
    deleted = tmp_path / "deleted.py"
    changed.write_text("value = 1\n", encoding="utf-8")
    deleted.write_text("value = 2\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    before = mapper.snapshot()

    changed.write_text("value = 3\n", encoding="utf-8")
    deleted.unlink()
    watcher = BoundedSnapshotWatcher()
    watcher.notify("changed.py")
    current = mapper.snapshot(before, watcher=watcher)

    assert current.stale_paths == ("changed.py",)
    assert current.deleted_paths == ("deleted.py",)


def test_watcher_overflow_forces_full_rescan(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    before = mapper.snapshot()
    source.write_text("value = 2\n", encoding="utf-8")
    watcher = BoundedSnapshotWatcher(max_events=1)
    assert watcher.notify("source.py")
    assert watcher.notify("another.py") is False

    current = mapper.snapshot(before, watcher=watcher)

    assert current.overflowed is True
    assert current.stale_paths == ("source.py",)


def test_git_identity_tracks_branch_head_and_dirty_worktree(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "AEGIS Test")
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "main.py")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "branch", "-M", "main")
    clean = SourceMapper(tmp_path).identity()
    _git(tmp_path, "checkout", "-b", "feature")
    feature = SourceMapper(tmp_path).identity()
    source.write_text("value = 2\n", encoding="utf-8")
    dirty = SourceMapper(tmp_path).identity()

    assert clean.vcs == "git"
    assert clean.branch == "main"
    assert clean.head
    assert feature.branch == "feature"
    assert feature.checkout_id != clean.checkout_id
    assert dirty.dirty is True
    assert "main.py" in dirty.dirty_paths


def test_external_symlink_is_not_followed(tmp_path):
    outside = tmp_path.parent / f"aegis-code-intelligence-outside-{os.getpid()}"
    outside.write_text("secret = True\n", encoding="utf-8")
    link = tmp_path / "outside.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as error:
        outside.unlink(missing_ok=True)
        pytest.skip(f"symlink creation unavailable: {error}")
    try:
        snapshot = SourceMapper(tmp_path).snapshot()
    finally:
        link.unlink(missing_ok=True)
        outside.unlink(missing_ok=True)

    record = next(record for record in snapshot.files if record.relative_path == "outside.py")
    assert record.extraction_status == "SKIPPED_EXTERNAL_LINK"
    assert record.content_hash is None


def test_native_watcher_emits_bounded_relative_event(tmp_path):
    try:
        watcher = NativeSnapshotWatcher(tmp_path, max_events=32)
    except (ImportError, OSError, RuntimeError) as error:
        pytest.skip(f"native watcher unavailable: {error}")
    try:
        source = tmp_path / "watched.py"
        source.write_text("value = 1\n", encoding="utf-8")
        events = ()
        for _ in range(20):
            time.sleep(0.1)
            events = watcher.poll().drain()
            if events:
                break
        assert any(event.path == "watched.py" for event in events)
    finally:
        watcher.close()
