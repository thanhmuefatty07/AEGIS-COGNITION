"""Build the Phase 0 inventory for the adaptive evidence system.

The inventory is deliberately descriptive. It does not infer supported claims,
replace an existing runner, or promote an evidence item. Unknown contract
fields remain explicit until a maintainer maps them from source and history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Final


ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT: Final[Path] = ROOT / "quality" / "registry" / "current_inventory.json"
DISPOSITIONS: Final[frozenset[str]] = frozenset(
    {
        "RETAIN_UNCHANGED",
        "WRAP",
        "MIGRATE",
        "SUPERSEDE",
        "ARCHIVE",
        "DELETE_PROVEN_REDUNDANT",
    }
)
SCOPES: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("tests", "PYTHON_TEST", (".py",)),
    ("core/rust/tests", "RUST_INTEGRATION_TEST", (".rs",)),
    ("core/rust/benches", "RUST_BENCHMARK", (".rs",)),
    ("fuzz/fuzz_targets", "FUZZ_TARGET", (".rs",)),
    ("core/rust/src", "RUST_UNIT_TEST", (".rs",)),
    ("scripts", "SCRIPT_OR_GATE", (".py",)),
    (".github/workflows", "HOSTED_WORKFLOW", (".yml", ".yaml")),
)
RUST_TEST_RE: Final[re.Pattern[str]] = re.compile(r"#\[(?:cfg\(test\)|test)\]")
PYTHON_BENCHMARK_STEMS: Final[frozenset[str]] = frozenset(
    {
        "benchmark_gate",
        "benchmark_preflight",
        "benchmark_wrapper",
        "communication_payload_benchmark",
        "resource_policy_benchmark",
    }
)


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tracked_paths() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return sorted(item.decode("utf-8") for item in output.split(b"\0") if item)


def _scope_for(path: str) -> tuple[str, str] | None:
    normalized = path.replace("\\", "/")
    for prefix, kind, suffixes in SCOPES:
        if not (normalized == prefix or normalized.startswith(f"{prefix}/")):
            continue
        if Path(normalized).suffix.lower() in suffixes:
            return kind, prefix
    return None


def _domain(path: str) -> str:
    lowered = path.lower()
    if any(token in lowered for token in ("fuzz", "sanit", "secret", "security")):
        return "SECURITY"
    if any(token in lowered for token in ("bench", "perf", "resource", "communication")):
        return "PERFORMANCE"
    if any(token in lowered for token in ("release", "package", "wheel", "supply", "rollback")):
        return "RELEASE"
    if any(token in lowered for token in ("evidence", "document", "constitution", "architecture", "audit")):
        return "EVIDENCE"
    if any(token in lowered for token in ("lab", "replay", "runtime", "contract")):
        return "RUNTIME"
    return "UNKNOWN"


def _runner(kind: str, path: str) -> str:
    if kind == "PYTHON_TEST":
        return "pytest"
    if kind == "RUST_INTEGRATION_TEST":
        return "cargo test"
    if kind == "RUST_BENCHMARK":
        return "criterion/cargo bench"
    if kind == "FUZZ_TARGET":
        return "cargo fuzz"
    if kind == "RUST_UNIT_TEST":
        return "cargo test"
    if kind == "PYTHON_BENCHMARK":
        return "python benchmark entrypoint"
    if kind == "HOSTED_WORKFLOW_JOB":
        return "GitHub Actions job"
    if kind == "HOSTED_WORKFLOW":
        return "GitHub Actions"
    if path.endswith("suite_evidence.py"):
        return "delegated runner (argv)"
    return "python entrypoint"


def _item(path: str, kind: str, scope: str, target: str | None = None) -> dict[str, object]:
    normalized = path.replace("\\", "/")
    locator = normalized if target is None else f"{normalized}#{target}"
    stable_id = f"AESE-{kind}-{hashlib.sha256(locator.encode()).hexdigest()[:16].upper()}"
    return {
        "stable_id": stable_id,
        "path": normalized,
        "target": target,
        "kind": kind,
        "scope": scope,
        "runner_backend": _runner(kind, normalized),
        "owner": "UNKNOWN_OWNER",
        "domain": _domain(normalized),
        # Directive-standard aliases are kept explicit so downstream AESE
        # consumers do not need to infer them from descriptive fields.  These
        # values are inventory metadata, never claims that a runner passed.
        "current_status": "NOT_VERIFIED",
        "risk": "UNKNOWN",
        "cost": "UNKNOWN",
        "platform": "UNKNOWN",
        "release_critical": "UNKNOWN",
        "claims_supported": [],
        "invariants_checked": [],
        "inputs": [],
        "outputs": [],
        "side_effects": ["UNKNOWN"],
        "dependencies": [],
        "runtime_class": "UNKNOWN",
        "resource_cost": "UNKNOWN",
        "determinism": "UNKNOWN",
        "platform_requirement": "UNKNOWN",
        "hardware_requirement": "UNKNOWN",
        "security_criticality": "UNKNOWN",
        "release_criticality": "UNKNOWN",
        "failure_semantics": "UNKNOWN",
        "evidence_produced": [],
        "known_defects": [],
        "migration_disposition": "RETAIN_UNCHANGED",
        "replacement_id": None,
        "supersession_rationale": "No stronger replacement evidence is recorded; preserve the existing authoritative path.",
        "rollback_path": "Re-run the existing native runner directly.",
        "mapping_status": "NOT_MAPPED",
    }


def build_inventory(root: Path = ROOT) -> dict[str, object]:
    if root.resolve() != ROOT.resolve():
        raise ValueError(f"inventory root must be {ROOT}")
    tracked = _tracked_paths()
    selected: list[dict[str, object]] = []
    selected_paths: set[str] = set()
    for path in tracked:
        scoped = _scope_for(path)
        if scoped is None:
            continue
        kind, scope = scoped
        if kind == "RUST_UNIT_TEST" and not RUST_TEST_RE.search((ROOT / path).read_text(encoding="utf-8")):
            continue
        if kind == "SCRIPT_OR_GATE" and Path(path).stem in PYTHON_BENCHMARK_STEMS:
            kind = "PYTHON_BENCHMARK"
        selected.append(_item(path, kind, scope))
        selected_paths.add(path)
        if kind == "HOSTED_WORKFLOW":
            workflow_text = (ROOT / path).read_text(encoding="utf-8")
            jobs_section = workflow_text.split("\njobs:\n", 1)
            jobs_text = jobs_section[1] if len(jobs_section) == 2 else ""
            selected.extend(
                _item(path, "HOSTED_WORKFLOW_JOB", scope, match.group(1))
                for match in re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", jobs_text, re.MULTILINE)
            )

    head = _run_git("rev-parse", "HEAD").strip()
    diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    status = _run_git("status", "--porcelain=v1")
    epoch_material = f"HEAD={head}\nSTATUS={status}".encode() + diff
    missing = [
        f"{prefix}/** ({','.join(suffixes)})"
        for prefix, _kind, suffixes in SCOPES
        if not any(path == prefix or path.startswith(f"{prefix}/") for path in selected_paths)
    ]
    dispositions: dict[str, int] = {}
    for entry in selected:
        disposition = str(entry["migration_disposition"])
        dispositions[disposition] = dispositions.get(disposition, 0) + 1
    source_tree_material = b"".join(
        f"{path}\0{_sha256_file(ROOT / path)}\n".encode() for path in sorted(selected_paths)
    )
    return {
        "schema": "aese-current-evidence-system-inventory-v1",
        "source_head": head,
        "source_tree_sha256": _sha256_bytes(source_tree_material),
        "worktree_epoch": _sha256_bytes(epoch_material),
        "worktree_status": "CLEAN" if not status else "DIRTY",
        "phase": "PHASE_0_REBIND_REALITY",
        "status": "INVENTORY_COMPLETE_DISPOSITIONS_RETAINED_MAPPING_PENDING",
        "direct_cutover": "PROHIBITED",
        "deletion": "PROHIBITED_UNTIL_SUPERSESSION_PROOF",
        "scope_policy": "tracked_files_only; native runners remain authoritative",
        "scope_counts": {
            "tracked_files": len(tracked),
            "inventoried_items": len(selected),
            "represented_paths": len(selected_paths),
            "missing_scopes": len(missing),
            "workflow_jobs": sum(1 for entry in selected if entry["kind"] == "HOSTED_WORKFLOW_JOB"),
        },
        "missing_scopes": missing,
        "disposition_counts": dispositions,
        "items": sorted(selected, key=lambda entry: str(entry["path"])),
        "limitations": [
            "Claims, owners, dependencies, cost, platform and failure semantics remain UNKNOWN until source/history mapping.",
            "Inventory completeness is not evidence that any check passed or that a replacement is valid.",
            "Dynamic imports, generated commands and workflow matrix expansion require later graph reconciliation.",
        ],
    }


def validate_inventory(actual: dict[str, object], expected: dict[str, object]) -> list[str]:
    """Return drift messages while ignoring provenance that changes per run.

    The source-tree digest and item records are the stable authority. The
    checkout commit and worktree epoch are retained as provenance but are not
    compared, because a docs-only commit can preserve the same inventoried
    source tree.
    """

    errors: list[str] = []
    errors.extend(
        f"{key} differs"
        for key in ("schema", "phase", "status", "direct_cutover", "deletion")
        if actual.get(key) != expected.get(key)
    )
    errors.extend(
        f"{key} differs"
        for key in ("source_tree_sha256", "scope_counts", "missing_scopes", "disposition_counts", "items")
        if actual.get(key) != expected.get(key)
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    parser.add_argument("--check", action="store_true", help="fail if the recorded inventory drifts")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    expected = build_inventory()
    if args.check:
        try:
            actual = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"inventory check failed: {exc}")
            return 1
        if not isinstance(actual, dict):
            print("inventory check failed: root must be an object")
            return 1
        errors = validate_inventory(actual, expected)
        if errors:
            print(json.dumps({"status": "DRIFT", "errors": errors}, sort_keys=True))
            return 1
        print(json.dumps({"status": "PASS", "items": expected["scope_counts"]}, sort_keys=True))
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": expected["status"], "items": expected["scope_counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
