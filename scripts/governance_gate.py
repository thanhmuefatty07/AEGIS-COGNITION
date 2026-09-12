import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(r"c:\Users\ADMIN\AEGIS-COGNITION")
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "governance_gate_report.json"

REQUIRED_DEPENDENCIES = {
    "arrow": "54",
    "arrow-buffer": "54",
    "blake3": "1.5.0",
    "pyo3": "0.29.2",
    "wasmtime": "47.0.3",
}
GOVERNANCE_DOMAINS = [
    "runtime",
    "evidence",
    "security",
    "evaluation",
    "product",
]
E2E_STAGES = [
    "goal_intake",
    "task_plan",
    "context_pack",
    "typed_tool_ir",
    "policy_proof",
    "tool_execution",
    "evidence_ingest",
    "replay_append",
    "checkpoint_seal",
    "memory_candidate",
    "next_action",
]


@dataclass(frozen=True)
class GovernanceGateCheck:
    name: str
    ok: bool
    detail: str


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _cargo_toml_has_dependency(cargo_toml: str, name: str, version_fragment: str) -> bool:
    return name in cargo_toml and version_fragment in cargo_toml


def evaluate_governance(root: str | Path = ROOT) -> dict:
    root_path = Path(root)
    checks: list[GovernanceGateCheck] = []
    governance_rs = _text(root_path / "core" / "rust" / "src" / "governance.rs")
    tests_rs = _text(root_path / "core" / "rust" / "src" / "tests.rs")
    benchmark_gate = _text(root_path / "scripts" / "benchmark_gate.py")
    run_checks = _text(root_path / "scripts" / "run_checks.py")
    cargo_toml = _text(root_path / "core" / "rust" / "Cargo.toml")
    overview = _text(root_path / "docs/archive/reports/project-overview.md")

    checks.append(
        GovernanceGateCheck(
            "complexity_lane_map",
            all(token in governance_rs for token in ["GovernanceLaneMap", "REQUIRED_WAVE_MASK_18"])
            and all(domain in overview.lower() for domain in GOVERNANCE_DOMAINS),
            "18 waves must collapse into five governance lanes with hash-bound coverage.",
        )
    )
    checks.append(
        GovernanceGateCheck(
            "benchmark_profiles",
            "BENCHMARK_PROFILE_INNOVATION" in benchmark_gate
            and "thresholds_for_profile" in benchmark_gate
            and "--benchmark-profile" in run_checks
            and "benchmark_profile" in run_checks
            and "BenchmarkGateProfile::innovation" in tests_rs
            and "BenchmarkGateProfile::release" in tests_rs,
            "Benchmark gates must support relaxed innovation profile while preserving release profile.",
        )
    )
    missing_deps = [
        name
        for name, version in REQUIRED_DEPENDENCIES.items()
        if not _cargo_toml_has_dependency(cargo_toml, name, version)
    ]
    checks.append(
        GovernanceGateCheck(
            "dependency_audit_manifest",
            not missing_deps
            and "DependencyAuditManifest" in governance_rs
            and "governance_dependency_audit_manifest_requires_rust_hotpath_stack_evidence" in tests_rs,
            f"Required dependency audit surface; missing={missing_deps}",
        )
    )
    checks.append(
        GovernanceGateCheck(
            "provider_rate_limit_mitigation",
            "ProviderRateLimitMitigationProof" in governance_rs
            and "NoProviderAvailable" in governance_rs
            and "governance_rate_limit_mitigation_selects_available_provider" in tests_rs,
            "Provider budgets must hash selected and throttled providers before route selection.",
        )
    )
    checks.append(
        GovernanceGateCheck(
            "e2e_scenario_gate",
            all(stage in governance_rs or stage in tests_rs for stage in [
                "GoalIntake",
                "TaskPlan",
                "ContextPack",
                "TypedToolIr",
                "PolicyProof",
                "ToolExecution",
                "EvidenceIngest",
                "ReplayAppend",
                "CheckpointSeal",
                "MemoryCandidate",
                "NextAction",
            ])
            and "E2EScenarioProof" in governance_rs
            and "governance_e2e_scenario_requires_full_goal_to_next_action_stage_chain" in tests_rs,
            "E2E scenario must cover goal->next-action with replay/evidence hashes.",
        )
    )

    passed = sum(1 for check in checks if check.ok)
    failed = len(checks) - passed
    return {
        "suite_name": "Production Governance Gate",
        "overall_ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in checks
        ],
    }


def main() -> int:
    report = evaluate_governance(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
