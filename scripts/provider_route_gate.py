import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "provider_route_gate_report.json"


@dataclass(frozen=True)
class ProviderBudget:
    provider_id: str
    model_name: str
    remaining_requests: int
    remaining_tokens: int
    reset_epoch_ms: int

    def can_serve(self, required_tokens: int) -> bool:
        return self.remaining_requests > 0 and self.remaining_tokens >= required_tokens

    def digest(self) -> str:
        return _stable_hash(
            {
                "provider_id": self.provider_id,
                "model_name": self.model_name,
                "remaining_requests": self.remaining_requests,
                "remaining_tokens": self.remaining_tokens,
                "reset_epoch_ms": self.reset_epoch_ms,
            }
        )


def evaluate_provider_route_gate(root: str | Path = ROOT) -> dict[str, Any]:
    root_path = Path(root)
    request = {
        "request_hash": _stable_hash({"goal": "provider-budget-admission", "tokens": 1024}),
        "required_tokens": 1024,
        "route_policy": "low-latency-with-failover",
    }
    budgets = (
        ProviderBudget("fast", "fast-model", 0, 10_000, 1_000_000),
        ProviderBudget("reserve", "reserve-model", 5, 10_000, 1_000_000),
        ProviderBudget("depleted", "depleted-model", 4, 128, 1_000_000),
    )
    ordered = sorted(budgets, key=lambda budget: (budget.provider_id, budget.model_name))
    admitted = [budget for budget in ordered if budget.can_serve(request["required_tokens"])]
    throttled = [budget for budget in ordered if not budget.can_serve(request["required_tokens"])]
    selected = admitted[0] if admitted else None
    benchmark_source = _read_text(root_path / "core" / "rust" / "benches" / "nerve_bench.rs")
    orchestrator_source = _read_text(root_path / "core" / "rust" / "src" / "orchestrator.rs")
    cli_source = _read_text(root_path / "core" / "rust" / "src" / "cli" / "mod.rs")
    replay_source = _read_text(root_path / "core" / "rust" / "src" / "replay.rs")
    tests_source = _read_text(root_path / "core" / "rust" / "src" / "tests.rs")
    constitution_report = _read_json(root_path / "artifacts" / "constitution_audit_report.json")
    checks = [
        {
            "name": "selected_provider_available",
            "ok": selected is not None and selected.provider_id == "reserve",
            "detail": "reserve should be selected because fast/depleted are throttled",
        },
        {
            "name": "throttled_providers_hashed",
            "ok": len(throttled) == 2 and all(len(budget.digest()) == 64 for budget in throttled),
            "detail": "all unavailable providers must stay visible as hashes",
        },
        {
            "name": "benchmark_surface_declared",
            "ok": "bench_provider_route_admission_proof" in benchmark_source
            and "provider_route_admission_proof" in benchmark_source,
            "detail": "Criterion source must declare provider_route_admission_proof; hard threshold waits for refresh",
        },
        {
            "name": "constitution_surface_declared",
            "ok": _constitution_has_symbol(constitution_report, "inference_backend_contract"),
            "detail": "constitution audit must require provider route admission symbols",
        },
        {
            "name": "runtime_budget_admission_path_declared",
            "ok": "process_llm_request_with_budget_admission" in orchestrator_source
            and "route_request_with_budget" in orchestrator_source
            and "admission_proof.is_valid_for" in orchestrator_source,
            "detail": "NerveRuntime must validate admission proof before selecting an adapter",
        },
        {
            "name": "cli_budget_admission_wrapper_declared",
            "ok": "run_llm_with_budget_admission" in cli_source
            and "ProviderBudgetLedger" in cli_source
            and "ProviderRouteAdmissionProof" in cli_source,
            "detail": "operator-facing runtime helper must expose budget-admitted execution",
        },
        {
            "name": "runtime_fail_closed_tests_declared",
            "ok": "llm_runtime_budget_admission_uses_only_admitted_provider" in tests_source
            and "llm_runtime_budget_admission_fails_closed_before_execution" in tests_source,
            "detail": "tests must prove throttled providers are not executed and all-throttled requests fail before audit commit",
        },
        {
            "name": "runtime_replay_binding_declared",
            "ok": "process_llm_request_with_budget_admission_replay" in orchestrator_source
            and "run_llm_with_budget_admission_replay" in cli_source
            and "append_budget_admitted_llm_response_received" in replay_source
            and "llm_budget_admission_replay_binding_hash" in replay_source,
            "detail": "budget-admitted runtime execution must append a replay event bound to the provider admission proof",
        },
        {
            "name": "runtime_replay_binding_tests_declared",
            "ok": "llm_runtime_budget_admission_replay_records_proof_bound_response" in tests_source
            and "run_event_records_budget_admitted_llm_response_replay_binding" in tests_source,
            "detail": "tests must prove runtime and replay ledger bind LLM responses to admission proof hashes",
        },
    ]
    passed = sum(1 for check in checks if check["ok"])
    failed = len(checks) - passed
    payload = {
        "suite_name": "Provider Route Gate",
        "schema": "aegis-provider-route-gate-report-v1",
        "truth_claim": False,
        "verifier": "rust-provider-route-admission-proof",
        "request": request,
        "selected_provider_digest": None if selected is None else selected.digest(),
        "throttled_provider_digests": [budget.digest() for budget in throttled],
        "budget_ledger_digest": _stable_hash([budget.digest() for budget in ordered]),
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "overall_ok": failed == 0,
    }
    payload["report_digest"] = _stable_hash(payload)
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _constitution_has_symbol(payload: dict[str, Any], name: str) -> bool:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return False
    return any(isinstance(check, dict) and check.get("name") == name and check.get("ok") for check in checks)


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    report = evaluate_provider_route_gate(ROOT)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
