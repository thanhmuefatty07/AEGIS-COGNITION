import hashlib
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.python.aegis import code_intelligence as code_intelligence_module
from core.python.aegis.code_intelligence import (
    BoundedSnapshotWatcher,
    CodeIntelligenceError,
    NativeSnapshotWatcher,
    SourceMapper,
    build_repository_map,
    compare_snapshots,
    invalidate_dependencies,
)


@pytest.fixture(autouse=True)
def _isolate_temporary_git_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


def _git(root: Path, *args: str) -> str:
    final_args = list(args)
    if final_args and final_args[0] == "init" and not any(a.startswith("--initial-branch") for a in final_args):
        final_args.insert(1, "--initial-branch=main")
    result = subprocess.run(
        ["git", "-C", str(root), *final_args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_non_git_snapshot_extracts_supported_symbols_and_excludes_generated(tmp_path):
    (tmp_path / "app.py").write_text(
        "import json\n\nclass App:\n    def run(self, value):\n        return value\n", encoding="utf-8"
    )
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


def test_repository_map_is_query_ranked_bounded_and_does_not_include_source_text(tmp_path):
    (tmp_path / "memory.py").write_text(
        "def recall_memory(value):\n    return 'private-source-body'\n",
        encoding="utf-8",
    )
    (tmp_path / "other.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8")
    snapshot = SourceMapper(tmp_path).snapshot()

    repository_map = build_repository_map(snapshot, query="recall memory", token_budget=512, max_files=2)
    repeated = build_repository_map(snapshot, query="recall memory", token_budget=512, max_files=2)

    assert repository_map.schema == "aegis-repository-map-v1"
    assert repository_map.entries[0].relative_path == "memory.py"
    assert repository_map.entries[0].symbols[0].name == "recall_memory"
    assert "private-source-body" not in repository_map.rendered
    assert repository_map.token_count <= repository_map.token_budget
    assert repository_map.map_hash == repeated.map_hash


def test_repository_map_reports_bounded_incomplete_snapshot(tmp_path):
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(f"def run_{index}():\n    return {index}\n", encoding="utf-8")

    snapshot = SourceMapper(tmp_path).snapshot()
    repository_map = build_repository_map(snapshot, token_budget=256, max_files=1)

    assert repository_map.complete is False
    assert repository_map.omitted_files == 2
    assert repository_map.token_count <= 256


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
    (tmp_path / "b.py").write_text("from pkg import c\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "c.py").write_text("value = 1\n", encoding="utf-8")
    snapshot = SourceMapper(tmp_path).snapshot()

    assert invalidate_dependencies(snapshot, ["pkg/c.py"]) == ("a.py", "b.py", "pkg/c.py")


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


@pytest.mark.parametrize(
    ("limit_name", "limit", "dirty_names"),
    [
        ("MAX_GIT_METADATA_PATHS", 1, ("one.py", "two.py")),
        (
            "MAX_GIT_METADATA_BYTES",
            64 * 1024,
            tuple(f"source_{index:05}.py" for index in range(5_000)),
        ),
    ],
)
def test_git_identity_fails_closed_when_dirty_metadata_exceeds_budget(
    tmp_path, monkeypatch, limit_name, limit, dirty_names
):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "AEGIS Test")
    for name in dirty_names:
        (tmp_path / name).write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(code_intelligence_module, limit_name, limit, raising=False)

    with pytest.raises(CodeIntelligenceError, match="dirty metadata exceeds the resource limit"):
        SourceMapper(tmp_path).identity()


def test_git_ignore_rejects_oversized_path_input_before_running_git(tmp_path, monkeypatch):
    mapper = SourceMapper(tmp_path)
    monkeypatch.setattr(code_intelligence_module, "MAX_GIT_METADATA_BYTES", 1, raising=False)

    def unexpected_git_call(*args, **kwargs):
        pytest.fail("oversized ignore input must be rejected before starting Git")

    monkeypatch.setattr(code_intelligence_module.subprocess, "run", unexpected_git_call)

    with pytest.raises(CodeIntelligenceError, match=r"ignore policy.*resource limit"):
        mapper._git_ignored_paths(("source.py",))


def test_git_identity_limits_dirty_paths_to_scanned_subdirectory(tmp_path):
    repository = tmp_path / "repository"
    workspace = repository / "workspace"
    sibling = repository / "sibling"
    workspace.mkdir(parents=True)
    sibling.mkdir()
    source = workspace / "main.py"
    outside = sibling / "outside.py"
    source.write_text("value = 1\n", encoding="utf-8")
    outside.write_text("value = 1\n", encoding="utf-8")
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "AEGIS Test")
    _git(repository, "add", "workspace/main.py", "sibling/outside.py")
    _git(repository, "-c", "commit.gpgsign=false", "commit", "-m", "initial")

    outside.write_text("value = 2\n", encoding="utf-8")
    outside_only = SourceMapper(workspace).identity()
    source.write_text("value = 2\n", encoding="utf-8")
    (workspace / "untracked.py").write_text("value = 3\n", encoding="utf-8")
    scoped_changes = SourceMapper(workspace).identity()

    assert outside_only.vcs == "git"
    assert outside_only.repository_root == str(repository.resolve())
    assert outside_only.dirty is False
    assert outside_only.dirty_paths == ()
    assert scoped_changes.dirty is True
    assert scoped_changes.dirty_paths == ("main.py", "untracked.py")


def test_external_directory_link_is_not_followed(tmp_path):
    outside = tmp_path.parent / f"aegis-code-intelligence-outside-{os.getpid()}"
    outside.mkdir()
    outside_source = outside / "outside.py"
    outside_source.write_text("secret = True\n", encoding="utf-8")
    link = tmp_path / "outside"
    try:
        if os.name == "nt":
            created = subprocess.run(
                ("cmd.exe", "/c", "mklink", "/J", str(link), str(outside)),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            assert created.returncode == 0, created.stderr or created.stdout
            assert link.is_junction()
        else:
            link.symlink_to(outside, target_is_directory=True)
        snapshot = SourceMapper(tmp_path).snapshot()
    finally:
        if link.is_junction():
            link.rmdir()
        else:
            link.unlink(missing_ok=True)
        outside_source.unlink(missing_ok=True)
        outside.rmdir()

    assert not any(record.relative_path.startswith("outside/") for record in snapshot.files)


def test_external_file_symlink_is_not_followed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    link = workspace / "outside.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"file symlink creation unavailable: {error}")

    try:
        snapshot = SourceMapper(workspace).snapshot()
    finally:
        link.unlink(missing_ok=True)

    assert not any(record.relative_path == "outside.py" for record in snapshot.files)


def test_source_mapper_rejects_reparse_entries_without_following_targets(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.py").write_text("def inside(): return True\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.py").write_text("def outside(): return True\n", encoding="utf-8")
    link = workspace / "linked.py"
    if os.name == "nt":
        created = subprocess.run(
            ("cmd.exe", "/c", "mklink", "/J", str(link), str(outside)),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("Windows junction creation is unavailable")
        assert link.is_junction()
    else:
        link.symlink_to(outside, target_is_directory=True)

    link_follow_arguments: list[bool] = []
    attempted_directory_opens: list[Path] = []

    class ObservedEntry:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name

        def is_dir(self, *, follow_symlinks=True):
            if Path(self._entry.path) == link:
                link_follow_arguments.append(follow_symlinks)
            return self._entry.is_dir(follow_symlinks=follow_symlinks)

        def stat(self, *, follow_symlinks=True):
            if Path(self._entry.path) == link:
                link_follow_arguments.append(follow_symlinks)
            return self._entry.stat(follow_symlinks=follow_symlinks)

    if os.name == "nt":
        original_open_directory = code_intelligence_module._windows_open_directory_handle

        def observe_directory_open(path):
            attempted_directory_opens.append(Path(path))
            return original_open_directory(path)

        monkeypatch.setattr(code_intelligence_module, "_windows_open_directory_handle", observe_directory_open)
    else:
        original_scandir = os.scandir

        @contextmanager
        def observed_scandir(path):
            with original_scandir(path) as entries:
                yield (ObservedEntry(entry) for entry in entries)

        monkeypatch.setattr(code_intelligence_module.os, "scandir", observed_scandir)
    try:
        snapshot = SourceMapper(workspace).snapshot()
    finally:
        if link.is_junction():
            link.rmdir()
        else:
            link.unlink(missing_ok=True)

    paths = {record.relative_path for record in snapshot.files}
    if os.name == "nt":
        assert link not in attempted_directory_opens
    else:
        assert link_follow_arguments
        assert True not in link_follow_arguments
    assert "inside.py" in paths
    assert "linked.py" not in paths
    assert "linked.py/module.py" not in paths


def test_project_identity_omits_dirty_sources_beneath_reparse_entries(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.py").write_text("def inside(): return True\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.py").write_text("def outside(): return True\n", encoding="utf-8")
    link = workspace / "linked"
    if os.name == "nt":
        created = subprocess.run(
            ("cmd.exe", "/c", "mklink", "/J", str(link), str(outside)),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("Windows junction creation is unavailable")
        assert link.is_junction()
    else:
        link.symlink_to(outside, target_is_directory=True)

    fake_git = SimpleNamespace(
        repository_root=str(workspace),
        common_git_dir=str(workspace / ".git"),
        checkout_path=str(workspace),
        branch="main",
        head="test-head",
        dirty_paths=("inside.py", "linked/module.py"),
    )
    ignore_queries: list[tuple[str, ...]] = []
    monkeypatch.setattr(code_intelligence_module, "_git_identity", lambda _root: fake_git)
    monkeypatch.setattr(
        SourceMapper,
        "_git_ignored_paths",
        lambda _self, paths: (ignore_queries.append(paths), frozenset())[1],
    )
    try:
        identity = SourceMapper(workspace).identity()
    finally:
        if link.is_junction():
            link.rmdir()
        else:
            link.unlink(missing_ok=True)

    assert ignore_queries == [("inside.py",)]
    assert identity.dirty is True
    assert identity.dirty_paths == ("inside.py",)


@pytest.mark.skipif(os.name != "nt", reason="reproduces a Windows junction replacement during directory scan")
def test_source_mapper_does_not_publish_names_from_directory_replaced_by_junction(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source_directory = workspace / "src"
    source_directory.mkdir(parents=True)
    (source_directory / "inside.py").write_text("def public_entry(): return True\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "PRIVATE_CUSTOMER_EXPORT.py").write_text(
        "def private_customer_export(): return 'sensitive'\n", encoding="utf-8"
    )
    saved_directory = workspace / "src-original"
    original_scandir = os.scandir
    original_open_directory = code_intelligence_module._windows_open_directory_handle if os.name == "nt" else None
    attempted = False
    swapped = False
    blocked = False

    def swap_if_needed(path):
        nonlocal attempted, swapped, blocked
        if Path(path) == source_directory and not attempted:
            attempted = True
            try:
                source_directory.rename(saved_directory)
            except OSError:
                blocked = True
            else:
                created = subprocess.run(
                    ("cmd.exe", "/c", "mklink", "/J", str(source_directory), str(outside)),
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if created.returncode != 0:
                    saved_directory.rename(source_directory)
                    pytest.skip("Windows junction creation is unavailable")
                swapped = True

    def replace_with_junction(path):
        swap_if_needed(path)
        return original_scandir(path)

    if os.name == "nt":

        def replace_before_native_open(path):
            swap_if_needed(path)
            return original_open_directory(path)

        monkeypatch.setattr(code_intelligence_module, "_windows_open_directory_handle", replace_before_native_open)
    else:
        monkeypatch.setattr(code_intelligence_module.os, "scandir", replace_with_junction)
    try:
        snapshot = SourceMapper(workspace).snapshot()
        repository_map = build_repository_map(snapshot, query="private customer export", token_budget=512)
    finally:
        link = workspace / "src"
        if link.is_junction():
            link.rmdir()
        elif link.is_symlink():
            link.unlink(missing_ok=True)
        if saved_directory.exists():
            saved_directory.rename(source_directory)

    paths = {record.relative_path for record in snapshot.files}
    assert attempted
    assert blocked or swapped
    if blocked:
        assert "src/inside.py" in paths
    else:
        assert not any(path.startswith("src/") for path in paths)
    assert "src/PRIVATE_CUSTOMER_EXPORT.py" not in paths
    assert "PRIVATE_CUSTOMER_EXPORT.py" not in repository_map.rendered


@pytest.mark.skipif(os.name != "nt", reason="reproduces a Windows ancestor junction swap after enumeration")
def test_source_mapper_does_not_path_check_child_after_ancestor_replaced_by_junction(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source_directory = workspace / "src"
    child_directory = source_directory / "child"
    child_directory.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside_child = outside / "child"
    outside_child.mkdir(parents=True)
    (outside_child / "outside.py").write_text("def outside_secret(): return True\n", encoding="utf-8")
    saved_directory = workspace / "src-original"
    original_scandir = code_intelligence_module._scandir_directory
    original_path_is_symlink = Path.is_symlink
    original_isjunction = os.path.isjunction
    path_checks_after_swap: list[Path] = []
    swapped = False

    @contextmanager
    def replace_ancestor_after_scan(path):
        nonlocal swapped
        with original_scandir(path) as entries:
            yield entries
        if Path(path) == source_directory and not swapped:
            source_directory.rename(saved_directory)
            created = subprocess.run(
                ("cmd.exe", "/c", "mklink", "/J", str(source_directory), str(outside)),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if created.returncode != 0:
                saved_directory.rename(source_directory)
                pytest.skip("Windows junction creation is unavailable")
            swapped = True

    def record_path_is_symlink(path):
        if swapped and path == source_directory / "child":
            path_checks_after_swap.append(Path(path))
        return original_path_is_symlink(path)

    def record_isjunction(path):
        if swapped and Path(path) == source_directory / "child":
            path_checks_after_swap.append(Path(path))
        return original_isjunction(path)

    monkeypatch.setattr(code_intelligence_module, "_scandir_directory", replace_ancestor_after_scan)
    monkeypatch.setattr(Path, "is_symlink", record_path_is_symlink)
    monkeypatch.setattr(os.path, "isjunction", record_isjunction)
    try:
        with pytest.raises(code_intelligence_module._SourceTraversalLimit):
            tuple(SourceMapper(workspace)._walk_files(use_git_ignore=False))
    finally:
        link = workspace / "src"
        if link.is_junction():
            link.rmdir()
        if saved_directory.exists():
            saved_directory.rename(source_directory)

    assert swapped
    assert path_checks_after_swap == []


@pytest.mark.skipif(os.name != "nt", reason="parses the Windows directory information buffer")
def test_windows_directory_entries_reject_empty_filename_in_directory_record():
    import ctypes

    calls = 0

    def query_directory(_handle, information_class, buffer, buffer_size):
        nonlocal calls
        calls += 1
        assert information_class == 0xE
        assert buffer_size == 64 * 1024
        if calls == 1:
            base = buffer.value
            ctypes.c_uint32.from_address(base + 56).value = 0x10  # FILE_ATTRIBUTE_DIRECTORY
            ctypes.c_uint32.from_address(base + 60).value = 0  # FileNameLength
            return True
        ctypes.set_last_error(18)  # ERROR_NO_MORE_FILES
        return False

    with pytest.raises(OSError, match="directory entry name length"):
        list(code_intelligence_module._windows_directory_entries(object(), (None, None, None, None, query_directory)))

    assert calls == 1


@pytest.mark.parametrize(
    ("attributes", "expected"),
    ((0x10, False), (0x10 | code_intelligence_module.stat_module.FILE_ATTRIBUTE_REPARSE_POINT, True)),
)
@pytest.mark.skipif(os.name != "nt", reason="uses Windows handle metadata")
def test_windows_reparse_detection_uses_file_attributes_supported_by_fat_volumes(attributes, expected):
    import ctypes
    from ctypes import wintypes

    handle = object()
    information_calls = []

    def unsupported_file_attribute_tag_info(*_args):
        ctypes.set_last_error(87)
        return False

    def get_file_information(actual_handle, information):
        information_calls.append(actual_handle)
        ctypes.cast(information, ctypes.POINTER(wintypes.DWORD)).contents.value = attributes
        return True

    api = (None, None, None, None, unsupported_file_attribute_tag_info, get_file_information)

    assert code_intelligence_module._windows_handle_has_reparse_point(handle, api) is expected
    assert information_calls == [handle]


@pytest.mark.skipif(os.name != "nt", reason="checks Windows directory scanning before reparse resolution")
def test_windows_scandir_rejects_reparse_path_before_resolving_target(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "linked"
    created = subprocess.run(
        ("cmd.exe", "/c", "mklink", "/J", str(link), str(outside)),
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    api = code_intelligence_module._windows_final_path_api()
    original_get_final_path = api[1]
    resolved_paths: list[str] = []

    def record_final_path(handle, buffer, capacity, flags):
        result = original_get_final_path(handle, buffer, capacity, flags)
        if result and result < capacity:
            resolved_paths.append(buffer.value)
        return result

    monkeypatch.setattr(
        code_intelligence_module,
        "_windows_final_path_api",
        lambda: (api[0], record_final_path, *api[2:]),
    )
    try:
        with (
            pytest.raises(OSError),
            code_intelligence_module._scandir_directory(link) as entries,
        ):
            list(entries)
    finally:
        if link.is_junction():
            link.rmdir()

    outside_key = code_intelligence_module._windows_path_key(str(outside))
    assert outside_key not in {code_intelligence_module._windows_path_key(path) for path in resolved_paths}


def test_source_mapper_checks_opened_file_identity_before_reading(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    outside = tmp_path.parent / f"aegis-code-intelligence-race-{os.getpid()}"
    source.write_text("def safe(): return 1\n", encoding="utf-8")
    outside.write_text("secret = 'outside-root'\n", encoding="utf-8")
    open_file = os.open

    def redirect_open(path, flags, *args, **kwargs):
        if Path(path) == source or (str(path) == "source.py" and kwargs.get("dir_fd") is not None):
            redirected_kwargs = {key: value for key, value in kwargs.items() if key != "dir_fd"}
            return open_file(outside, flags, *args, **redirected_kwargs)
        return open_file(path, flags, *args, **kwargs)

    if os.name == "nt":

        def redirect_windows_open(_root, _parts):
            return open_file(outside, os.O_RDONLY | getattr(os, "O_BINARY", 0))

        monkeypatch.setattr(code_intelligence_module, "_windows_open_file_beneath_root", redirect_windows_open)
    else:
        monkeypatch.setattr(os, "open", redirect_open)
    try:
        snapshot = SourceMapper(tmp_path).snapshot()
    finally:
        outside.unlink(missing_ok=True)

    assert not any(record.relative_path == "source.py" for record in snapshot.files)


def test_source_mapper_rejects_ancestor_junction_swapped_before_open(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source_directory = workspace / "src"
    source_directory.mkdir(parents=True)
    source = source_directory / "module.py"
    inside_bytes = b"def inside_workspace(): return True\n"
    source.write_bytes(inside_bytes)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_bytes = b"secret = 'outside-workspace'\n"
    (outside / "module.py").write_bytes(outside_bytes)
    saved_directory = workspace / "src-original"
    open_file = os.open
    original_stat = Path.stat
    swapped = False

    def swap_ancestor() -> None:
        nonlocal swapped
        if swapped:
            return
        source_directory.rename(saved_directory)
        if os.name == "nt":
            created = subprocess.run(
                ("cmd.exe", "/c", "mklink", "/J", str(source_directory), str(outside)),
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if created.returncode != 0:
                saved_directory.rename(source_directory)
                pytest.skip("Windows junction creation is unavailable")
        else:
            source_directory.symlink_to(outside, target_is_directory=True)
        swapped = True

    def redirect_metadata_before_stat(path, *args, **kwargs):
        if Path(path) == source and kwargs.get("follow_symlinks") is False and not swapped:
            swap_ancestor()
        return original_stat(path, *args, **kwargs)

    def redirect_ancestor_before_open(path, flags, *args, **kwargs):
        if not swapped and (Path(path) == source or str(path) == "src"):
            swap_ancestor()
        return open_file(path, flags, *args, **kwargs)

    if os.name == "nt":
        safe_open = code_intelligence_module._windows_open_file_beneath_root

        def replace_before_windows_open(root, parts):
            if not swapped:
                swap_ancestor()
            return safe_open(root, parts)

        monkeypatch.setattr(code_intelligence_module, "_windows_open_file_beneath_root", replace_before_windows_open)
    else:
        monkeypatch.setattr(Path, "stat", redirect_metadata_before_stat)
        monkeypatch.setattr(code_intelligence_module.os, "open", redirect_ancestor_before_open)
    try:
        snapshot = SourceMapper(workspace).snapshot()
    finally:
        link = workspace / "src"
        if link.is_junction():
            link.rmdir()
        elif link.is_symlink():
            link.unlink(missing_ok=True)
        if saved_directory.exists():
            saved_directory.rename(source_directory)

    assert swapped
    assert not any(record.relative_path == "src/module.py" for record in snapshot.files)
    assert not any(hashlib.sha256(outside_bytes).hexdigest() == record.content_hash for record in snapshot.files)


@pytest.mark.skipif(os.name != "nt", reason="checks Windows no-reparse open behavior")
def test_source_reader_blocks_reparse_ancestor_at_native_open(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "module.py").write_text("secret = 'outside-workspace'\n", encoding="utf-8")
    source_directory = workspace / "src"
    created = subprocess.run(
        ("cmd.exe", "/c", "mklink", "/J", str(source_directory), str(outside)),
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("Windows junction creation is unavailable")

    class UnicodeString(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        )

    class ObjectAttributes(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        )

    original_os_open = os.open
    native_open = code_intelligence_module._windows_nt_file_api()[0]
    status_to_error = code_intelligence_module._windows_nt_file_api()[1]
    os_open_attempts = []
    native_open_attempts = []

    def record_os_open(path, *args, **kwargs):
        os_open_attempts.append(path)
        return original_os_open(path, *args, **kwargs)

    def record_native_open(*args):
        attributes = ctypes.cast(args[2], ctypes.POINTER(ObjectAttributes)).contents
        object_name = ctypes.cast(attributes.ObjectName, ctypes.POINTER(UnicodeString)).contents
        status = native_open(*args)
        native_open_attempts.append((attributes.Attributes, object_name.Buffer, status, attributes.RootDirectory))
        return status

    monkeypatch.setattr(code_intelligence_module.os, "open", record_os_open)
    monkeypatch.setattr(
        code_intelligence_module,
        "_windows_nt_file_api",
        lambda: (record_native_open, status_to_error),
    )
    try:
        with pytest.raises(code_intelligence_module.SourceFileReadError) as raised:
            code_intelligence_module.read_file_beneath_root(workspace, "src/module.py", max_bytes=256)
    finally:
        source_directory.rmdir()

    assert raised.value.reason == "unsafe_path"
    assert os_open_attempts == []
    assert len(native_open_attempts) == 2
    root_attributes, root_name, root_status, root_directory = native_open_attempts[0]
    assert root_name == "\\??\\" + str(workspace)
    assert root_attributes & 0x1000  # OBJ_DONT_REPARSE
    assert root_status >= 0
    assert not root_directory

    attributes, relative_name, status, root_directory = native_open_attempts[1]
    assert relative_name == "src\\module.py"
    assert attributes & 0x1000  # OBJ_DONT_REPARSE
    assert status & 0xFFFFFFFF == code_intelligence_module._WINDOWS_STATUS_REPARSE_POINT_ENCOUNTERED
    assert root_directory


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (r"C:\repo\branch\..\workspace", r"\??\C:\repo\workspace"),
        (r"\\?\C:\repo\workspace", r"\??\C:\repo\workspace"),
        (r"\\server\share\repo", r"\??\UNC\server\share\repo"),
        (r"\\?\unc\server\share\repo", r"\??\UNC\server\share\repo"),
        (r"\\?\Volume{abc}\repo", r"\??\Volume{abc}\repo"),
    ),
)
def test_windows_nt_path_converts_supported_workspace_namespaces(path, expected):
    assert code_intelligence_module._windows_nt_path(path) == expected


@pytest.mark.parametrize(
    "path",
    (
        r"\\.\C:\repo",
        r"\\?\GLOBALROOT\Device\HarddiskVolume1\repo",
        r"C:relative",
        "C:\\repo\\\x00name",
        "relative",
    ),
)
def test_windows_nt_path_rejects_device_or_unrooted_workspace_namespaces(path):
    with pytest.raises(code_intelligence_module.SourceFileReadError, match="workspace root"):
        code_intelligence_module._windows_nt_path(path)


@pytest.mark.parametrize("fail_stage", ("file_handle_close", "root_handle_close"))
@pytest.mark.skipif(os.name != "nt", reason="checks Windows native-handle cleanup")
def test_windows_source_open_attempts_all_cleanup_and_closes_transferred_descriptor(tmp_path, monkeypatch, fail_stage):
    import ctypes
    import msvcrt

    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    api = code_intelligence_module._windows_final_path_api()
    close_handle = api[3]
    close_attempts = []

    def fail_one_close(handle):
        close_attempts.append(handle.value)
        result = close_handle(handle)
        if len(close_attempts) == 1:
            ctypes.set_last_error(5)
            return False
        return result

    if fail_stage == "root_handle_close":
        real_open_osfhandle = msvcrt.open_osfhandle
        transferred_descriptors = []

        def capture_transferred_descriptor(handle, flags):
            descriptor = real_open_osfhandle(handle, flags)
            transferred_descriptors.append(descriptor)
            return descriptor

        monkeypatch.setattr(msvcrt, "open_osfhandle", capture_transferred_descriptor)
    else:

        def fail_descriptor_transfer(_handle, _flags):
            raise OSError("forced descriptor conversion failure")

        monkeypatch.setattr(msvcrt, "open_osfhandle", fail_descriptor_transfer)

    monkeypatch.setattr(
        code_intelligence_module,
        "_windows_final_path_api",
        lambda: (*api[:3], fail_one_close, api[4], api[5]),
    )

    with pytest.raises(OSError):
        code_intelligence_module._windows_open_file_beneath_root(tmp_path, (source.name,))

    if fail_stage == "file_handle_close":
        assert len(close_attempts) == 2, "a failed file-handle close must not skip closing the workspace root"
    else:
        assert len(close_attempts) == 1
        assert len(transferred_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(transferred_descriptors[0])


@pytest.mark.skipif(os.name != "nt", reason="checks Windows no-reparse workspace-root open behavior")
def test_source_reader_does_not_open_workspace_root_through_ancestor_junction(tmp_path, monkeypatch):
    parent = tmp_path / "workspace-parent"
    workspace = parent / "workspace"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside_workspace = outside / "workspace"
    outside_workspace.mkdir(parents=True)
    (outside_workspace / "source.py").write_text("secret = 'outside-workspace'\n", encoding="utf-8")
    moved_parent = tmp_path / "workspace-parent-original"
    parent.rename(moved_parent)
    observed_handle_paths = []
    original_final_path = code_intelligence_module._windows_final_handle_path

    def record_final_handle_path(handle, get_final_path):
        path = original_final_path(handle, get_final_path)
        observed_handle_paths.append(path)
        return path

    monkeypatch.setattr(code_intelligence_module, "_windows_final_handle_path", record_final_handle_path)
    try:
        created = subprocess.run(
            ("cmd.exe", "/d", "/c", "mklink", "/J", str(parent), str(outside)),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("Windows junction creation is unavailable")

        with pytest.raises(code_intelligence_module.SourceFileReadError) as raised:
            code_intelligence_module.read_file_beneath_root(workspace, "source.py", max_bytes=256)
    finally:
        if parent.is_junction():
            parent.rmdir()
        if moved_parent.exists():
            moved_parent.rename(parent)

    assert raised.value.reason == "unsafe_path"
    outside_key = code_intelligence_module._windows_path_key(str(outside))
    assert not any(
        code_intelligence_module._windows_path_key(path).startswith(outside_key) for path in observed_handle_paths
    ), "workspace-root acquisition followed an ancestor junction before rejecting it"


@pytest.mark.parametrize("path_verification_fails", (False, True))
@pytest.mark.skipif(os.name != "nt", reason="checks byte accounting after a Windows handle moves")
def test_source_reader_accounts_bytes_when_post_read_path_verification_fails(
    tmp_path, monkeypatch, path_verification_fails
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.py"
    content = b"source payload for path-check regression\n"
    source.write_bytes(content)
    actual_handle_path = code_intelligence_module._windows_handle_path
    handle_path_checks = 0

    def report_moved_path(file_descriptor):
        nonlocal handle_path_checks
        handle_path_checks += 1
        if handle_path_checks == 1:
            return actual_handle_path(file_descriptor)
        if path_verification_fails:
            raise OSError("opened path could not be verified")
        return str(tmp_path / "outside" / source.name)

    monkeypatch.setattr(code_intelligence_module, "_windows_handle_path", report_moved_path)
    with pytest.raises(code_intelligence_module.SourceFileReadError) as raised:
        code_intelligence_module.read_file_beneath_root(workspace, "source.py", max_bytes=len(content))

    assert handle_path_checks == 2
    assert raised.value.reason == "unsafe_path"
    assert raised.value.bytes_read == len(content)


def test_native_watcher_emits_relative_event(tmp_path):
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


def test_ignored_local_blob_does_not_consume_source_content_budget(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text(".local/\nsrc/00ignored.py\n", encoding="utf-8")
    private = tmp_path / ".local" / "private.bin"
    private.parent.mkdir()
    private.write_bytes(b"x" * 128)
    source = tmp_path / "src" / "actual.py"
    source.parent.mkdir()
    (source.parent / "00ignored.py").write_bytes(b"x" * 128)
    source.write_bytes(b"def f():\n    return 12345678\n")
    assert source.stat().st_size == 29

    snapshot = SourceMapper(tmp_path, max_files=1, max_file_bytes=1024, max_total_bytes=64).snapshot()

    assert {record.relative_path for record in snapshot.files} == {"src/actual.py"}
    assert snapshot.overflowed is False


def test_git_ignore_checks_are_batched_across_workspace_directories(tmp_path, monkeypatch):
    _git(tmp_path, "init")
    for index in range(40):
        source = tmp_path / f"branch-{index:03}" / "leaf" / "module.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 1\n", encoding="utf-8")

    check_ignore_calls = []
    original_run = code_intelligence_module.subprocess.run

    def counted_run(arguments, *args, **kwargs):
        if arguments[0] == "git" and "check-ignore" in arguments:
            check_ignore_calls.append(arguments)
        return original_run(arguments, *args, **kwargs)

    monkeypatch.setattr(code_intelligence_module.subprocess, "run", counted_run)

    snapshot = SourceMapper(tmp_path).snapshot()

    assert len(snapshot.files) == 40
    assert len(check_ignore_calls) <= 2


def test_directory_traversal_limit_marks_snapshot_incomplete_without_deletions(tmp_path, monkeypatch):
    _git(tmp_path, "init")
    source = tmp_path / "kept.py"
    source.write_text("value = 1\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    previous = mapper.snapshot()

    (tmp_path / "branch" / "leaf" / "deeper").mkdir(parents=True)
    monkeypatch.setattr(code_intelligence_module, "MAX_DIRECTORIES", 2, raising=False)

    snapshot = mapper.snapshot(previous)

    assert snapshot.overflowed is True
    assert {record.relative_path for record in snapshot.files} == {"kept.py"}
    assert snapshot.deleted_paths == ()


def test_gitignored_directory_does_not_consume_directory_traversal_limit(tmp_path, monkeypatch):
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    ignored_source = tmp_path / "ignored" / "not_indexed.py"
    ignored_source.parent.mkdir()
    ignored_source.write_text("value = 0\n", encoding="utf-8")
    source = tmp_path / "src" / "kept.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(code_intelligence_module, "MAX_DIRECTORIES", 2, raising=False)

    snapshot = SourceMapper(tmp_path).snapshot()

    assert {record.relative_path for record in snapshot.files} == {"src/kept.py"}
    assert snapshot.overflowed is False


@pytest.mark.parametrize("fail_during", ("success", "scandir", "iteration"))
def test_posix_directory_scan_closes_open_descriptor_on_all_exits(monkeypatch, fail_during):
    closed_descriptors = []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            if fail_during == "iteration":
                raise PermissionError("scan failed")
            return iter(())

    entries = Entries()

    def scandir(_descriptor):
        if fail_during == "scandir":
            raise PermissionError("scan failed")
        return entries

    monkeypatch.setattr(
        code_intelligence_module,
        "os",
        SimpleNamespace(name="posix", scandir=scandir, close=closed_descriptors.append),
    )
    monkeypatch.setattr(code_intelligence_module, "_open_posix_directory", lambda _path: 41)

    if fail_during == "scandir":
        with (
            pytest.raises(PermissionError, match="scan failed"),
            code_intelligence_module._scandir_directory(Path("/workspace")),
        ):
            pass
    elif fail_during == "iteration":
        with (
            pytest.raises(PermissionError, match="scan failed"),
            code_intelligence_module._scandir_directory(Path("/workspace")) as iterator,
        ):
            list(iterator)
    else:
        with code_intelligence_module._scandir_directory(Path("/workspace")) as iterator:
            assert list(iterator) == []

    assert closed_descriptors == [41]


def test_unreadable_directory_marks_snapshot_incomplete_without_deletions(tmp_path, monkeypatch):
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    (source_directory / "previous.py").write_text("value = 1\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    previous = mapper.snapshot()
    original_scan = code_intelligence_module._scandir_directory

    def fail_source_directory(path):
        if path == source_directory:
            raise PermissionError("scan failed")
        return original_scan(path)

    monkeypatch.setattr(code_intelligence_module, "_scandir_directory", fail_source_directory)

    current = mapper.snapshot(previous)

    assert current.overflowed is True
    assert current.deleted_paths == ()


def test_unclassifiable_directory_entry_marks_snapshot_incomplete_without_deletions(tmp_path, monkeypatch):
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    (source_directory / "previous.py").write_text("value = 1\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    previous = mapper.snapshot()

    class DirectoryEntry:
        name = "source"

        def stat(self, *, follow_symlinks=True):
            assert follow_symlinks is False
            raise PermissionError("metadata unavailable")

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter((DirectoryEntry(),))

    @contextmanager
    def controlled_scandir(_path):
        yield Entries()

    monkeypatch.setattr(code_intelligence_module, "_scandir_directory", controlled_scandir)

    current = mapper.snapshot(previous)

    assert current.overflowed is True
    assert current.deleted_paths == ()


def test_directory_entry_enumeration_is_bounded_before_names_are_materialized(tmp_path, monkeypatch):
    _git(tmp_path, "init")
    (tmp_path / "kept.py").write_text("value = 1\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    previous = mapper.snapshot()
    enumerated = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def stat(self, *, follow_symlinks=True):
            del follow_symlinks
            return SimpleNamespace(st_mode=0)

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            nonlocal enumerated
            for index in range(8):
                enumerated += 1
                yield Entry(f"asset-{index}.bin")

    monkeypatch.setattr(code_intelligence_module, "MAX_TRAVERSAL_ENTRIES", 3, raising=False)

    @contextmanager
    def controlled_scandir(_path):
        yield Entries()

    monkeypatch.setattr(code_intelligence_module, "_scandir_directory", controlled_scandir)

    snapshot = mapper.snapshot(previous)

    assert enumerated == 4
    assert snapshot.files == ()
    assert snapshot.overflowed is True
    assert snapshot.deleted_paths == ()


def test_non_git_directory_entry_overflow_fails_closed(tmp_path, monkeypatch):
    mapper = SourceMapper(tmp_path)
    enumerated = 0

    class Entry:
        def __init__(self, name):
            self.name = name

        def stat(self, *, follow_symlinks=True):
            del follow_symlinks
            return SimpleNamespace(st_mode=0)

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            nonlocal enumerated
            for index in range(8):
                enumerated += 1
                yield Entry(f"asset-{index}.bin")

    monkeypatch.setattr(code_intelligence_module, "MAX_TRAVERSAL_ENTRIES", 3)

    @contextmanager
    def controlled_scandir(_path):
        yield Entries()

    monkeypatch.setattr(code_intelligence_module, "_scandir_directory", controlled_scandir)

    with pytest.raises(CodeIntelligenceError, match="ignore policy could not be evaluated"):
        tuple(mapper._walk_files(use_git_ignore=False))

    assert enumerated == 4


def test_source_policy_applies_nested_ignore_negation_and_filters_non_source(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text(
        "root-secret.py\n*.generated.py\n!src/keep.generated.py\nsrc/tracked-secret.py\nignored-dir/\n",
        encoding="utf-8",
    )
    (tmp_path / "root-secret.py").write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "keep.generated.py").write_text("def keep(): return 1\n", encoding="utf-8")
    (tmp_path / "src" / "drop.generated.py").write_text("def drop(): return 1\n", encoding="utf-8")
    tracked_secret = tmp_path / "src" / "tracked-secret.py"
    tracked_secret.write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / ".gitignore").write_text("private.py\n!keep.py\n", encoding="utf-8")
    (tmp_path / "pkg" / "private.py").write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "pkg" / "keep.py").write_text("def keep(): return 2\n", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "README.md").write_text("private docs do not belong in the code map\n", encoding="utf-8")
    (tmp_path / ".env.py").write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "ignored-dir" / "deep.py").parent.mkdir()
    (tmp_path / "ignored-dir" / "deep.py").write_text("def private(): return 1\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "third_party.py").write_text("def vendor(): return 1\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "output.py").write_text("def generated(): return 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-f", "src/tracked-secret.py")

    snapshot = SourceMapper(tmp_path).snapshot()

    paths = {record.relative_path for record in snapshot.files}
    assert paths == {"pkg/keep.py", "src/keep.generated.py"}
    assert "src/tracked-secret.py" not in snapshot.identity.dirty_paths
    repository_map = build_repository_map(snapshot, token_budget=512)
    assert "root-secret.py" not in repository_map.rendered
    assert "private.py" not in repository_map.rendered
    assert "deep.py" not in repository_map.rendered
    assert "logo.png" not in repository_map.rendered
    assert "README.md" not in repository_map.rendered
    assert ".env.py" not in repository_map.rendered
    assert "third_party.py" not in repository_map.rendered
    assert "output.py" not in repository_map.rendered


def test_non_git_source_root_with_gitignore_fails_closed(tmp_path):
    (tmp_path / ".gitignore").write_text("private.py\n", encoding="utf-8")
    (tmp_path / "private.py").write_text("secret = True\n", encoding="utf-8")

    with pytest.raises(CodeIntelligenceError, match="ignore policy"):
        SourceMapper(tmp_path).snapshot()


def test_unborn_git_worktree_still_applies_ignore_policy(tmp_path):
    _git(tmp_path, "init")
    (tmp_path / ".gitignore").write_text("private.py\n", encoding="utf-8")
    (tmp_path / "private.py").write_text("secret = True\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("def main(): return 1\n", encoding="utf-8")

    snapshot = SourceMapper(tmp_path).snapshot()

    assert snapshot.identity.vcs == "git"
    assert {record.relative_path for record in snapshot.files} == {"main.py"}


def test_oversized_and_unreadable_inputs_do_not_consume_source_content_budget(tmp_path, monkeypatch):
    too_large = tmp_path / "a-large.py"
    too_large.write_bytes(b"#" * 128)
    unreadable = tmp_path / "b-unreadable.py"
    unreadable.write_bytes(b"#" * 29)
    binary = tmp_path / "c-binary.py"
    binary.write_bytes(b"\xff" * 32)
    source = tmp_path / "src" / "actual.py"
    source.parent.mkdir()
    source.write_bytes(b"def f():\n    return 12345678\n")
    open_file = os.open

    def fail_unreadable(path, flags, *args, **kwargs):
        if path == unreadable:
            raise PermissionError(f"access denied: {path}")
        return open_file(path, flags, *args, **kwargs)

    if os.name == "nt":
        windows_open = code_intelligence_module._windows_open_file_beneath_root

        def fail_unreadable_windows(root, parts):
            if Path(root).joinpath(*parts) == unreadable:
                raise PermissionError(f"access denied: {unreadable}")
            return windows_open(root, parts)

        monkeypatch.setattr(code_intelligence_module, "_windows_open_file_beneath_root", fail_unreadable_windows)
    else:
        monkeypatch.setattr(os, "open", fail_unreadable)
    snapshot = SourceMapper(tmp_path, max_file_bytes=64, max_total_bytes=64).snapshot()

    records = {record.relative_path: record for record in snapshot.files}
    assert records["a-large.py"].extraction_status == "SIZE_LIMIT"
    assert records["b-unreadable.py"].extraction_status == "READ_ERROR"
    assert "c-binary.py" not in records
    assert str(tmp_path) not in (records["b-unreadable.py"].error or "")
    assert records["src/actual.py"].extraction_status == "EXTRACTED"
    assert snapshot.overflowed is True


def test_invalid_utf8_source_files_count_toward_file_limit(tmp_path):
    for name in ("a-invalid.py", "b-invalid.py", "c-invalid.py"):
        (tmp_path / name).write_bytes(b"\xff")

    snapshot = SourceMapper(tmp_path, max_files=2, max_file_bytes=8, max_total_bytes=32).snapshot()

    assert snapshot.files == ()
    assert snapshot.overflowed is True


def test_invalid_utf8_source_bytes_count_toward_content_limit(tmp_path):
    (tmp_path / "a-invalid.py").write_bytes(b"\xff" * 64)
    (tmp_path / "z-source.py").write_bytes(b"def f():\n    return 12345678\n")

    snapshot = SourceMapper(tmp_path, max_files=2, max_file_bytes=64, max_total_bytes=64).snapshot()

    records = {record.relative_path: record for record in snapshot.files}
    assert "a-invalid.py" not in records
    assert records["z-source.py"].extraction_status == "SIZE_LIMIT"
    assert snapshot.overflowed is True


def test_source_policy_preserves_unicode_source(tmp_path):
    source = tmp_path / "unicode.py"
    source.write_text("def café():\n    return 'mémoire'\n", encoding="utf-8")

    snapshot = SourceMapper(tmp_path).snapshot()

    record = snapshot.files[0]
    assert record.relative_path == "unicode.py"
    assert record.extraction_status == "EXTRACTED"
    assert {symbol.name for symbol in record.symbols} == {"café"}


def test_incremental_snapshot_rechecks_same_size_content_even_when_mtime_is_restored(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    previous = mapper.snapshot()
    before = source.stat()
    source.write_text("def answer():\n    return 2\n", encoding="utf-8")
    assert source.stat().st_size == before.st_size
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    extraction_calls = 0
    extract_text = code_intelligence_module._extract_text

    def count_extractions(language, text):
        nonlocal extraction_calls
        extraction_calls += 1
        return extract_text(language, text)

    monkeypatch.setattr(code_intelligence_module, "_extract_text", count_extractions)
    incremental = mapper.snapshot(previous)
    assert extraction_calls == 1
    assert incremental.stale_paths == ("main.py",)

    fresh = mapper.snapshot()
    assert incremental.revision == fresh.revision
    assert incremental.files == fresh.files


def test_incremental_rename_delete_and_overflow_resync_match_fresh_snapshot(tmp_path, monkeypatch):
    original = tmp_path / "original.py"
    deleted = tmp_path / "deleted.py"
    original.write_text("def stable(): return 1\n", encoding="utf-8")
    deleted.write_text("def remove(): return 2\n", encoding="utf-8")
    mapper = SourceMapper(tmp_path)
    before = mapper.snapshot()
    original.rename(tmp_path / "renamed.py")
    deleted.unlink()
    watcher = BoundedSnapshotWatcher(max_events=1)
    assert watcher.notify("renamed.py") is True
    assert watcher.notify("deleted.py") is False

    incremental = mapper.snapshot(before, watcher=watcher)
    fresh = mapper.snapshot()
    delta = compare_snapshots(before, incremental)

    assert incremental.overflowed is True
    assert incremental.revision == fresh.revision
    assert incremental.files == fresh.files
    assert delta.renamed == (("original.py", "renamed.py"),)
    assert delta.deleted == ("deleted.py",)


def test_incremental_snapshot_reuses_hash_verified_extractions_and_indexes_paths(tmp_path, monkeypatch):
    for index in range(64):
        (tmp_path / f"module_{index:03}.py").write_text(
            f"def symbol_{index}(value):\n    return value + {index}\n", encoding="utf-8"
        )
    mapper = SourceMapper(tmp_path)
    previous = mapper.snapshot()
    extraction_calls = 0
    lookup_visits = 0
    record_visits = 0
    extract_text = code_intelligence_module._extract_text

    class CountingRecords(tuple):
        def __iter__(self):
            nonlocal record_visits
            for record in super().__iter__():
                record_visits += 1
                yield record

    def count_extractions(language, text):
        nonlocal extraction_calls
        extraction_calls += 1
        return extract_text(language, text)

    def count_linear_lookup(records, relative_path):
        nonlocal lookup_visits
        for record in records:
            lookup_visits += 1
            if record.relative_path == relative_path:
                return record
        return None

    monkeypatch.setattr(code_intelligence_module, "_extract_text", count_extractions)
    monkeypatch.setattr(code_intelligence_module, "_file_by_path", count_linear_lookup, raising=False)
    warm = mapper.snapshot(replace(previous, files=CountingRecords(previous.files)))

    assert warm.revision == previous.revision
    assert extraction_calls == 0
    assert lookup_visits == 0
    assert record_visits == len(previous.files)


def test_source_reader_rejects_hard_link_to_file_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inside = workspace / "inside.py"
    inside.write_bytes(b"inside_value = 1\n")
    _, inside_raw = code_intelligence_module.read_file_beneath_root(workspace, "inside.py", max_bytes=128)
    assert inside_raw == b"inside_value = 1\n"

    outside = tmp_path / "outside.py"
    outside.write_bytes(b"outside_value = 'synthetic private fixture'\n")
    linked = workspace / "module.py"
    os.link(outside, linked)
    assert linked.stat().st_nlink == 2

    with pytest.raises(code_intelligence_module.SourceFileReadError) as raised:
        code_intelligence_module.read_file_beneath_root(workspace, "module.py", max_bytes=128)

    assert raised.value.reason == "unsafe_path"
