from __future__ import annotations

from dataclasses import replace

import pytest

from aegis_cognition.code_reuse import (
    CodeReuseError,
    CodeReusePolicy,
    assess_code_reuse,
    build_local_reuse_candidate,
    materialize_exact,
)

try:
    from core.python.aegis.code_intelligence import SourceMapper
except ImportError:
    from aegis.code_intelligence import SourceMapper  # type: ignore[import-not-found]


def test_reuse_candidate_is_snapshot_bound_and_exact_materialization_is_explicit(tmp_path) -> None:
    source = tmp_path / "source.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    snapshot = SourceMapper(tmp_path).snapshot()
    candidate = build_local_reuse_candidate(
        snapshot,
        "source.py",
        start_line=1,
        end_line=2,
    )

    assessment = assess_code_reuse(candidate, generation_tokens=40, verification_tokens=3)
    assert assessment.decision == "REUSE_EXACT"
    assert assessment.savings_tokens == 37

    receipt = materialize_exact(
        candidate,
        target_root=tmp_path,
        target_relative_path="copied.py",
    )
    assert (tmp_path / "copied.py").read_text(encoding="utf-8") == "def answer():\n    return 42\n"
    assert receipt.target_hash == candidate.snippet_hash
    with pytest.raises(CodeReuseError, match="target exists"):
        materialize_exact(candidate, target_root=tmp_path, target_relative_path="copied.py")
    overwritten = materialize_exact(
        candidate,
        target_root=tmp_path,
        target_relative_path="copied.py",
        overwrite=True,
    )
    assert overwritten.target_hash == candidate.snippet_hash


def test_reuse_rejects_stale_source_unknown_license_and_non_cheaper_patch(tmp_path) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    snapshot = SourceMapper(tmp_path).snapshot()
    candidate = build_local_reuse_candidate(snapshot, "source.py", start_line=1, end_line=1)

    source.write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(CodeReuseError, match="changed after candidate"):
        materialize_exact(candidate, target_root=tmp_path, target_relative_path="stale.py")

    unknown = replace(candidate, license_id="UNKNOWN")
    rejected = assess_code_reuse(unknown, generation_tokens=40, policy=CodeReusePolicy())
    assert rejected.decision == "REJECT"

    generated = assess_code_reuse(candidate, generation_tokens=4, adaptation_tokens=2, verification_tokens=2)
    assert generated.decision == "GENERATE"


def test_reuse_rejects_current_directory_as_a_target_path(tmp_path) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    snapshot = SourceMapper(tmp_path).snapshot()
    candidate = build_local_reuse_candidate(snapshot, "source.py", start_line=1, end_line=1)

    with pytest.raises(CodeReuseError, match="relative, normalized"):
        materialize_exact(candidate, target_root=tmp_path, target_relative_path=".")
