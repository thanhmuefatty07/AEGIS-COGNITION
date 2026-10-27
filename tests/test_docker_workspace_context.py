from __future__ import annotations

import fnmatch
import json
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _dockerignore_pattern_matches(candidate: str, pattern: str) -> bool:
    path_parts = candidate.split("/")
    pattern_parts = pattern.split("/")

    def matches(path_index: int, pattern_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return path_index == len(path_parts)
        if pattern_parts[pattern_index] == "**":
            return matches(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and matches(path_index + 1, pattern_index)
            )
        return (
            path_index < len(path_parts)
            and fnmatch.fnmatchcase(path_parts[path_index], pattern_parts[pattern_index])
            and matches(path_index + 1, pattern_index + 1)
        )

    return matches(0, 0)


def _dockerignore_patterns() -> list[tuple[bool, str]]:
    patterns: list[tuple[bool, str]] = []
    for line in (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        included = pattern.startswith("!")
        pattern = pattern.lstrip("!").strip("/")
        if pattern and pattern != ".":
            patterns.append((included, pattern))
    return patterns


def _ignored_by_dockerignore(path: Path, context_root: Path, patterns: list[tuple[bool, str]]) -> bool:
    relative_parts = path.relative_to(context_root).as_posix().split("/")
    ignored = False
    for included, pattern in patterns:
        candidates = ["/".join(relative_parts[:index]) for index in range(1, len(relative_parts) + 1)]
        if any(_dockerignore_pattern_matches(candidate, pattern) for candidate in candidates):
            ignored = not included
    return ignored


def _tracked_cargo_context(destination: Path, *, local_markers: bool = False) -> Path:
    destination.mkdir()
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain.toml",
            "LICENSE.txt",
            "core/rust",
            "aegis-plugins",
            "pocs",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr

    for relative_path in result.stdout.split("\0"):
        if not relative_path:
            continue
        source = REPOSITORY_ROOT / relative_path
        if source.is_file():
            target = destination / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    if local_markers:
        markers = (
            "core/rust/src/.env.private",
            "core/rust/src/.local/private-archive.marker",
            "core/rust/target/private-build.marker",
            "core/rust/target_check/private-build.marker",
            "core/rust/target_plan_check/private-build.marker",
            "core/rust/private_archive/report.marker",
            "aegis-plugins/.env.private",
            "aegis-plugins/aegis-search-sdk/.local/private-archive.marker",
            "pocs/.env.private",
            "pocs/semantic_cache_poc/.local/private-archive.marker",
        )
        for relative_path in markers:
            marker = destination / relative_path
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("synthetic private marker\n", encoding="utf-8")

    return destination


def _dockerfile_lines(text: str) -> list[str]:
    logical_lines: list[str] = []
    pending = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending += stripped[:-1] + " " if stripped.endswith("\\") else stripped
        if not stripped.endswith("\\"):
            logical_lines.append(pending)
            pending = ""
    if pending:
        logical_lines.append(pending)
    return logical_lines


def _simulate_rust_builder_copy_plan(source_root: Path, destination: Path) -> Path:
    """Model current COPY and .dockerignore inputs; no Docker/BuildKit daemon is invoked."""
    destination.mkdir()
    stage_name: str | None = None
    ignore_patterns = _dockerignore_patterns()

    for line in _dockerfile_lines((REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")):
        words = shlex.split(line)
        instruction = words[0].upper()
        if instruction == "FROM":
            stage_name = words[words.index("AS") + 1] if "AS" in words else None
            continue
        if instruction != "COPY" or stage_name != "rust-builder":
            continue

        assert len(words) >= 3, f"invalid COPY instruction: {line}"
        assert not words[1].startswith("--"), f"unsupported COPY option: {line}"
        sources = words[1:-1]
        target_text = words[-1]
        target_parts = PurePosixPath(target_text).parts
        assert all(source != "." for source in sources), "the builder must not COPY the checkout root"

        for source_text in sources:
            source_parts = PurePosixPath(source_text).parts
            assert source_parts and not PurePosixPath(source_text).is_absolute()
            assert ".." not in source_parts, f"COPY source escapes the build context: {source_text}"
            source = source_root.joinpath(*source_parts)
            assert source.exists(), f"COPY source is missing from the repository: {source_text}"
            assert not _ignored_by_dockerignore(source, source_root, ignore_patterns), (
                f"COPY source is excluded by .dockerignore: {source_text}"
            )
            target = destination.joinpath(*target_parts)

            if source.is_dir():

                def ignore_entries(directory: str, names: list[str]) -> list[str]:
                    return [
                        name
                        for name in names
                        if _ignored_by_dockerignore(Path(directory) / name, source_root, ignore_patterns)
                    ]

                shutil.copytree(
                    source,
                    target,
                    dirs_exist_ok=True,
                    ignore=ignore_entries,
                )
            else:
                if len(sources) > 1 or target_text.endswith("/"):
                    target = target / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    return destination


def test_simulated_rust_builder_copy_plan_excludes_private_markers_and_is_cargo_complete(
    tmp_path: Path,
) -> None:
    source_root = _tracked_cargo_context(tmp_path / "checkout", local_markers=True)
    source_marker = source_root / "core/rust/src/target_feature.rs"
    source_marker.write_text("// synthetic source file must remain in the build context\n", encoding="utf-8")
    ignore_patterns = _dockerignore_patterns()
    for cargo_cache_marker in (
        "core/rust/target/private-build.marker",
        "core/rust/target_check/private-build.marker",
        "core/rust/target_plan_check/private-build.marker",
    ):
        assert _ignored_by_dockerignore(source_root / cargo_cache_marker, source_root, ignore_patterns), (
            cargo_cache_marker
        )
    context = _simulate_rust_builder_copy_plan(source_root, tmp_path / "rust-builder-context")
    copied_source_marker = context / "core/rust/src/target_feature.rs"
    assert copied_source_marker.is_file(), "source files named target_* must not be filtered"
    assert copied_source_marker.read_text(encoding="utf-8") == (
        "// synthetic source file must remain in the build context\n"
    )
    cargo = shutil.which("cargo")
    assert cargo is not None, "Cargo is required to verify the Docker builder context"

    result = subprocess.run(
        [
            cargo,
            "metadata",
            "--locked",
            "--no-deps",
            "--format-version",
            "1",
            "--manifest-path",
            "core/rust/Cargo.toml",
        ],
        cwd=context,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    metadata = json.loads(result.stdout)
    workspace = tomllib.loads((source_root / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]
    expected_packages = {
        tomllib.loads((source_root / member / "Cargo.toml").read_text(encoding="utf-8"))["package"]["name"]
        for member in workspace["members"]
    }
    actual_packages = {package["name"] for package in metadata["packages"]}
    assert actual_packages == expected_packages

    cli_target = next(
        target
        for package in metadata["packages"]
        for target in package["targets"]
        if target["name"] == "aegis-nerve-cli" and "bin" in target["kind"]
    )
    assert Path(cli_target["src_path"]).is_file()
    assert not (context / ".local").exists()
    assert not any(
        path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example")
        for path in context.rglob("*")
    )
    for private_marker in (
        "core/rust/target_check/private-build.marker",
        "core/rust/target/private-build.marker",
        "core/rust/target_plan_check/private-build.marker",
        "core/rust/private_archive/report.marker",
        "core/rust/src/.local/private-archive.marker",
        "core/rust/src/.env.private",
        "aegis-plugins/.env.private",
        "aegis-plugins/aegis-search-sdk/.local/private-archive.marker",
        "pocs/.env.private",
        "pocs/semantic_cache_poc/.local/private-archive.marker",
    ):
        assert not (context / private_marker).exists(), private_marker


def test_external_docker_base_images_are_pinned_by_digest() -> None:
    dockerfile = REPOSITORY_ROOT / "Dockerfile"
    from_lines = [
        shlex.split(line)
        for line in _dockerfile_lines(dockerfile.read_text(encoding="utf-8"))
        if shlex.split(line)[0].upper() == "FROM"
    ]

    assert from_lines, "Dockerfile must declare at least one base image"
    for instruction in from_lines:
        image = instruction[1]
        _, separator, digest = image.partition("@sha256:")
        assert separator and len(digest) == 64 and all(character in "0123456789abcdef" for character in digest), (
            f"Docker base image must be pinned by a SHA-256 OCI digest: {image}"
        )


def test_rust_cli_dockerfile_command_sets_pyo3_extension_build_flag() -> None:
    """Check Dockerfile configuration only; this does not build or inspect the CLI binary."""
    dockerfile = REPOSITORY_ROOT / "Dockerfile"
    build_lines = [
        shlex.split(line)
        for line in _dockerfile_lines(dockerfile.read_text(encoding="utf-8"))
        if line.startswith("RUN ") and "cargo build --locked --release" in line
    ]

    assert len(build_lines) == 1, "Dockerfile must define one locked release CLI build"
    assert build_lines[0][1:4] == ["PYO3_BUILD_EXTENSION_MODULE=1", "cargo", "build"]
    assert "--bin" in build_lines[0] and "aegis-nerve-cli" in build_lines[0]
