"""Passive project discovery for the AESE control plane."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from collections.abc import Iterable

from .contracts import ProjectProfile, UNKNOWN, canonical_hash


_IGNORED_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "target", "__pycache__", ".local"}
_MAX_FILES = 20_000
_MAX_FINGERPRINT_BYTES = 4 * 1024 * 1024


def _safe_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_files(root: Path) -> Iterable[Path]:
    count = 0
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(directory for directory in directories if directory not in _IGNORED_DIRECTORIES)
        for filename in sorted(files):
            count += 1
            if count > _MAX_FILES:
                return
            path = Path(current) / filename
            if not path.is_symlink():
                yield path


def _source_revision(root: Path, files: tuple[Path, ...]) -> tuple[str, bool]:
    try:
        result = subprocess.run(
            ("git", "rev-parse", "--verify", "HEAD"),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            dirty = (
                subprocess.run(
                    ("git", "status", "--porcelain", "--untracked-files=no"),
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                ).stdout.strip()
                != ""
            )
            head = result.stdout.strip()
            if not dirty:
                return head, False
            status = subprocess.run(
                ("git", "status", "--porcelain=v1", "--untracked-files=all"),
                cwd=root,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            fingerprint = _dirty_workspace_fingerprint(root, status.stdout if status.returncode == 0 else "")
            if fingerprint is None:
                return UNKNOWN, True
            return f"{head}+WORKTREE:{fingerprint}", True
    except OSError, subprocess.SubprocessError:
        pass
    digest = hashlib.sha256()
    total = 0
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            return UNKNOWN, True
        total += len(data)
        if total > _MAX_FINGERPRINT_BYTES:
            return UNKNOWN, True
        digest.update(_safe_relative(path, root).encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return f"WORKSPACE:{digest.hexdigest()}", True


def _status_paths(status: str) -> tuple[str, ...]:
    paths: set[str] = set()
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[-1]
        normalized = raw_path.strip().replace("\\", "/")
        if normalized:
            paths.add(normalized)
    return tuple(sorted(paths))


def _dirty_workspace_fingerprint(root: Path, status: str) -> str | None:
    """Hash the exact dirty files so HEAD cannot certify a worktree change."""

    paths = _status_paths(status)
    if not paths:
        return None
    digest = hashlib.sha256()
    total = 0
    for relative in paths:
        candidate = (root / relative).resolve()
        try:
            _safe_relative(candidate, root)
        except ValueError:
            return None
        try:
            data = candidate.read_bytes()
        except OSError:
            return None
        total += len(data)
        if total > _MAX_FINGERPRINT_BYTES:
            return None
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def workspace_change_paths(root: Path) -> tuple[str, ...]:
    """Return bounded git worktree paths for change interception."""

    resolved = root.expanduser().resolve()
    try:
        result = subprocess.run(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=resolved,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return ()
    if result.returncode != 0:
        return ()
    return _status_paths(result.stdout)


def _languages(files: tuple[Path, ...]) -> tuple[str, ...]:
    suffixes = {path.suffix.casefold() for path in files}
    languages: set[str] = set()
    if ".py" in suffixes or any(path.name == "pyproject.toml" for path in files):
        languages.add("python")
    if ".rs" in suffixes or any(path.name == "Cargo.toml" for path in files):
        languages.add("rust")
    if ".js" in suffixes or ".jsx" in suffixes or any(path.name == "package.json" for path in files):
        languages.add("javascript")
    if ".ts" in suffixes or ".tsx" in suffixes:
        languages.add("typescript")
    if ".go" in suffixes or any(path.name == "go.mod" for path in files):
        languages.add("go")
    if ".cs" in suffixes or any(path.suffix == ".csproj" for path in files):
        languages.add("dotnet")
    if (
        ".java" in suffixes
        or ".kt" in suffixes
        or any(path.name in {"pom.xml", "build.gradle", "build.gradle.kts"} for path in files)
    ):
        languages.add("jvm")
    if (
        ".c" in suffixes
        or ".h" in suffixes
        or ".cpp" in suffixes
        or any(path.name in {"CMakeLists.txt", "Makefile"} for path in files)
    ):
        languages.add("cpp")
    if ".rb" in suffixes or any(path.name == "Gemfile" for path in files):
        languages.add("ruby")
    if ".php" in suffixes or any(path.name == "composer.json" for path in files):
        languages.add("php")
    if ".swift" in suffixes or any(path.name == "Package.swift" for path in files):
        languages.add("swift")
    if ".dart" in suffixes or any(path.name == "pubspec.yaml" for path in files):
        languages.add("dart")
    return tuple(sorted(languages))


def _frameworks(files: tuple[Path, ...]) -> tuple[str, ...]:
    names = {path.name for path in files}
    frameworks: set[str] = set()
    if "pytest.ini" in names or "pyproject.toml" in names or "conftest.py" in names:
        frameworks.add("pytest")
    if "Cargo.toml" in names:
        frameworks.add("cargo")
    if "package.json" in names:
        frameworks.add("node")
    if "go.mod" in names:
        frameworks.add("go-test")
    if "pom.xml" in names:
        frameworks.add("junit-maven")
    if "build.gradle" in names or "build.gradle.kts" in names:
        frameworks.add("junit-gradle")
    if "CMakeLists.txt" in names:
        frameworks.add("ctest")
    return tuple(sorted(frameworks))


def inspect_project(root: Path, *, project_id: str | None = None) -> ProjectProfile:
    """Inspect metadata only; test/build commands are intentionally not run."""

    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError("project root must be an existing directory")
    files = tuple(_iter_files(resolved))
    relative = tuple(_safe_relative(path, resolved) for path in files)
    manifests = tuple(
        path
        for path in relative
        if Path(path).name
        in {
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "CMakeLists.txt",
            "Gemfile",
            "composer.json",
            "Package.swift",
            "pubspec.yaml",
        }
    )
    test_roots = tuple(
        path for path in relative if Path(path).name in {"tests", "test", "__tests__"} or "/tests/" in f"/{path}/"
    )
    build_roots = tuple(
        path for path in relative if Path(path).name in {"Cargo.toml", "CMakeLists.txt", "pyproject.toml"}
    )
    lockfiles = tuple(
        path
        for path in relative
        if Path(path).name
        in {"Cargo.lock", "poetry.lock", "uv.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.sum"}
    )
    ci_files = tuple(path for path in relative if path.startswith(".github/workflows/"))
    generated = tuple(
        path for path in relative if any(part in {"generated", "gen", "dist", "build"} for part in Path(path).parts)
    )
    ffi = tuple(path for path in relative if path.endswith((".so", ".dll", ".dylib", ".pyd")))
    revision, dirty = _source_revision(resolved, files)
    stable_id = project_id or f"project-{canonical_hash({'root_path': str(resolved)})[:16]}"
    return ProjectProfile(
        project_id=stable_id,
        root_path=str(resolved),
        languages=_languages(files),
        frameworks=_frameworks(files),
        manifests=manifests,
        test_roots=test_roots,
        build_roots=build_roots,
        generated_sources=generated,
        lockfiles=lockfiles,
        ci_files=ci_files,
        ffi_boundaries=ffi,
        dirty_workspace=dirty,
        dynamic_dependencies=not bool(lockfiles),
        source_revision=revision,
        discovery_status="PASSIVE_COMPLETE" if revision != UNKNOWN else "PASSIVE_INCOMPLETE",
    )


__all__ = ["inspect_project", "workspace_change_paths"]
