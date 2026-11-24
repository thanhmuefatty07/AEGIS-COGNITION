#!/usr/bin/env python3
"""Deterministic generator and verifier for the Canonical Capability Ownership Map.

Produces:
- docs/architecture/capability_ownership.json
- docs/architecture/CAPABILITY_OWNERSHIP.md

Verifies that 100% of:
1. Public Python SDK symbols in aegis_cognition/_public_exports.py (__all__)
2. Public Rust core modules in core/rust/src/lib.rs
3. Workspace plugin crates in Cargo.toml (aegis-plugins/*)
4. CLI commands (aegis_cognition/cli.py and core/rust/src/cli)
5. Operator API routes (core/python/operator_api.py)
are mapped to a single canonical owner, consumer list, test suite, and performance budget.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
JSON_OUT = ROOT / "docs" / "architecture" / "capability_ownership.json"
MD_OUT = ROOT / "docs" / "architecture" / "CAPABILITY_OWNERSHIP.md"

PYTHON_MODULE_METADATA: dict[str, dict[str, Any]] = {
    "aegis_cognition/agent.py": {
        "capability_id": "CAP-PY-AGENT",
        "domain": "Friendly Gateway Agent & Single-Call Entrypoint",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/__init__.py", "aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/cli.py", "aegis_cognition/application.py", "tests/test_public_route_boundary.py"],
        "owning_tests": ["tests/test_public_route_boundary.py", "core/python/tests.py"],
        "performance_budget": "Agent initialization <= 5 ms; zero blocking network I/O during construction",
    },
    "aegis_cognition/subagents.py": {
        "capability_id": "CAP-PY-SUBAGENTS",
        "domain": "Multi-Agent Coordination, Mailboxes, Evidence Board & Supervisor",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/agent.py", "core/rust/src/agent_coordination.rs"],
        "owning_tests": ["tests/test_subagents.py", "tests/test_dynamic_subagent_spawning.py"],
        "performance_budget": "Plan DAG validation <= 2 ms per 32-node graph; bounded mailbox journal reads",
    },
    "core/python/aegis/cache_economics.py": {
        "capability_id": "CAP-PY-CACHE-ECONOMICS",
        "domain": "Prompt Cache Pricing, Prefix Hashing & Economic Context Compaction",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/agent.py", "core/python/aegis/provider.py"],
        "owning_tests": ["tests/test_cache_economics.py"],
        "performance_budget": "Prefix hashing & cache cost estimation <= 1 ms per prompt plan",
    },
    "aegis_cognition/agent_efficiency.py": {
        "capability_id": "CAP-PY-AGENT-EFFICIENCY",
        "domain": "Observation Projection, Action Fusion, Capability Floor & Disposable Worktrees",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/verification/execution.py", "aegis_cognition/agent.py"],
        "owning_tests": ["tests/test_agent_efficiency.py"],
        "performance_budget": "Evidence-preserving reduction <= 5 ms per observation pack",
    },
    "aegis_cognition/benchmark.py": {
        "capability_id": "CAP-PY-BENCHMARK",
        "domain": "Benchmark Protocol v2, Trial Recording & Statistical Evaluation",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["scripts/benchmark_gate.py", "aegis-plugins/aegis-bench"],
        "owning_tests": ["tests/test_benchmark_protocol.py"],
        "performance_budget": "Bootstrap CI & protocol validation <= 25 ms per 100 trials",
    },
    "aegis_cognition/aese.py": {
        "capability_id": "CAP-PY-AESE-MODEL",
        "domain": "AESE Adaptive Measurement, Cross-Hardware Prediction & Evidence Ledger",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["scripts/aese_claim_graph.py", "scripts/aese_validation_corpus.py"],
        "owning_tests": ["tests/test_aese_inventory.py", "tests/test_aese_claim_graph.py"],
        "performance_budget": "Anchor selection & cross-hardware analytic prediction <= 5 ms",
    },
    "aegis_cognition/lab.py": {
        "capability_id": "CAP-PY-LAB-SDK",
        "domain": "Python Lab Orchestration Façade, Simulation/Electrical/Process Cells & Unit Registry",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py", "core/rust/src/ffi/lab.rs"],
        "consumers": ["aegis_cognition/verification/facade.py", "aegis_cognition/desktop_service.py"],
        "owning_tests": ["tests/test_lab_platform.py", "tests/test_lab_scientific_Computing.py"],
        "performance_budget": "Cell dispatch overhead <= 2 ms; deterministic state transitions delegated to core/rust/src/lab.rs",
    },
    "aegis_cognition/research_adapters.py": {
        "capability_id": "CAP-PY-RESEARCH-ADAPTERS",
        "domain": "Public Research Routing (X App-Only & Reddit RSS Query Providers)",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/subagents.py"],
        "owning_tests": ["tests/test_research_vision_adapters.py"],
        "performance_budget": "Bounded HTTP response parsing <= 10 ms per feed payload",
    },
    "aegis_cognition/code_reuse.py": {
        "capability_id": "CAP-PY-CODE-REUSE",
        "domain": "Verified Code Reuse Assessment & Exact Artifact Materialization",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/verification/facade.py"],
        "owning_tests": ["tests/test_code_reuse.py"],
        "performance_budget": "Reuse candidate hash & license verification <= 5 ms per file",
    },
    "aegis_cognition/extensions.py": {
        "capability_id": "CAP-PY-EXTENSIONS",
        "domain": "Extension Manifest Discovery, MCP Stdio/HTTP Providers & Skill Catalog",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/desktop_service.py", "aegis_cognition/lab.py"],
        "owning_tests": ["tests/test_extensions.py", "tests/test_mcp_resources_and_vision.py"],
        "performance_budget": "Manifest discovery & schema validation <= 10 ms per extension directory",
    },
    "aegis_cognition/vision_adapters.py": {
        "capability_id": "CAP-PY-VISION-ADAPTERS",
        "domain": "Browser Capture & Vision Observation Subagent Adapters",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/subagents.py"],
        "owning_tests": ["tests/test_research_vision_adapters.py", "tests/test_mcp_resources_and_vision.py"],
        "performance_budget": "Image digest & observation envelope validation <= 5 ms",
    },
    "aegis_cognition/goal_contract.py": {
        "capability_id": "CAP-PY-GOAL-CONTRACT",
        "domain": "Goal Contracts, Acceptance Predicates, Budgets & Side-Effect Idempotency Keys",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/agent.py", "core/rust/src/goal_intake.rs"],
        "owning_tests": ["tests/test_goal_contract.py"],
        "performance_budget": "Contract validation & predicate evaluation <= 1 ms",
    },
    "aegis_cognition/verification/__init__.py": {
        "capability_id": "CAP-PY-VERIFICATION",
        "domain": "Development Verification Sessions, Project Profiling & Local Test Execution",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": [
            "aegis_cognition/_public_exports.py",
            "aegis_cognition/verification/contracts.py",
            "aegis_cognition/verification/facade.py",
            "aegis_cognition/verification/execution.py",
            "aegis_cognition/verification/session.py",
        ],
        "consumers": ["aegis_cognition/cli.py", "aegis_cognition/lab.py"],
        "owning_tests": ["tests/test_verification_session.py"],
        "performance_budget": "Command argv construction <= 5 ms; output capture bounded to 8 MiB",
    },
    "aegis_cognition/metrics.py": {
        "capability_id": "CAP-PY-METRICS",
        "domain": "Runtime Metrics Collector & Summary Export",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py"],
        "consumers": ["aegis_cognition/agent.py", "aegis_cognition/observability.py"],
        "owning_tests": ["core/python/tests.py"],
        "performance_budget": "Metric increment/observation <= 50 us",
    },
    "aegis_cognition/observability.py": {
        "capability_id": "CAP-PY-OBSERVABILITY",
        "domain": "Correlation Context & Runtime Telemetry Bridge",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py", "core/rust/src/telemetry.rs"],
        "consumers": ["aegis_cognition/agent.py", "scripts/otel_trace_exporter_gate.py"],
        "owning_tests": ["tests/test_observability.py"],
        "performance_budget": "Span emission & JSONL serialization <= 500 us",
    },
    "aegis_cognition/runtime.py": {
        "capability_id": "CAP-PY-RUNTIME-BRIDGE",
        "domain": "Python Runtime Resource & Cooperative Placement FFI Façade",
        "layer": "L1_ffi_bridge",
        "adapter_or_facade_paths": [
            "aegis_cognition/_public_exports.py",
            "core/rust/src/ffi/runtime.rs",
            "core/rust/src/runtime.rs",
            "core/rust/src/placement.rs",
        ],
        "consumers": ["aegis_cognition/lab.py", "scripts/resource_policy_benchmark.py"],
        "owning_tests": ["tests/test_resource_runtime.py"],
        "performance_budget": "Admission & lease token round-trip <= 1 ms",
    },
    "aegis_cognition/hypergraph.py": {
        "capability_id": "CAP-PY-HYPERGRAPH",
        "domain": "Memory Hypergraph Facade, 10-Stage Entity Deduplication, Leiden Clustering & Hybrid PPR Retrieval",
        "layer": "L1_ffi_bridge",
        "adapter_or_facade_paths": [
            "aegis_cognition/_public_exports.py",
            "core/rust/src/memory/hypergraph.rs",
            "core/rust/src/ffi/learning.rs",
        ],
        "consumers": ["aegis_cognition/mcp_server.py", "scripts/memory_context_benchmark.py"],
        "owning_tests": ["tests/test_hypergraph_and_team_bus.py"],
        "performance_budget": "1,000-node dedup + Leiden + hybrid query <= 250 ms; zero silent alias false merges",
    },
    "aegis_cognition/team_protocol.py": {
        "capability_id": "CAP-PY-TEAM-PROTOCOL",
        "domain": "AIP-v2 Compact Handle Envelopes, Shared Task Delta Board, GoalRound Anti-Drift & Blinded Adversarial Audit",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": [
            "aegis_cognition/_public_exports.py",
            "core/rust/src/agent_coordination.rs",
            "core/rust/src/sac.rs",
        ],
        "consumers": ["aegis_cognition/mcp_server.py", "scripts/memory_context_benchmark.py"],
        "owning_tests": ["tests/test_hypergraph_and_team_bus.py"],
        "performance_budget": "CompactHandleEnvelope encode/decode <= 250 us; floor(2N/3)+1 quorum & counterexample veto <= 1 ms",
    },
    "aegis_cognition/mcp_server.py": {
        "capability_id": "CAP-PY-MCP-SERVER",
        "domain": "Bidirectional AEGIS MCP Hub Server, Progressive Tool Catalog & Isolated Harness Delegation Adapters",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/_public_exports.py", "aegis_cognition/cli.py"],
        "consumers": ["aegis_cognition/cli.py", "tests/test_mcp_hub_and_harnesses.py"],
        "owning_tests": ["tests/test_mcp_hub_and_harnesses.py"],
        "performance_budget": "Compact schema validation <= 13 KiB per tool; loopback HTTP & stdio JSON-RPC dispatch <= 5 ms",
    },
    "aegis_cognition/_public_exports.py": {
        "capability_id": "CAP-PY-PUBLIC-EXPORTS",
        "domain": "Package Version & Root Public Symbol Table",
        "layer": "L2_python_sdk",
        "adapter_or_facade_paths": ["aegis_cognition/__init__.py"],
        "consumers": ["aegis_cognition/cli.py", "tests/test_public_route_boundary.py"],
        "owning_tests": ["tests/test_public_route_boundary.py"],
        "performance_budget": "O(1) version lookup",
    },
}

RUST_MODULE_DOMAINS: dict[str, tuple[str, str, list[str]]] = {
    "agent_coordination": ("Rust Multi-Agent Graph & Message Validator", "L0_core_rust", ["core/rust/src/ffi/mod.rs", "aegis_cognition/subagents.py"]),
    "bridge_mmap": ("Zero-Copy Shared-Memory Mmap Ring Buffer Bridge", "L0_core_rust", ["core/rust/src/ffi/bridge.rs", "core/python/aegis/mmap_bridge.py"]),
    "browser_witness": ("Cryptographic Browser DOM/Action Witness Verification", "L0_core_rust", ["core/rust/src/ffi/mod.rs", "aegis-plugins/aegis-browser"]),
    "circuit_breaker": ("Provider & Tool Fault Isolation Circuit Breaker", "L0_core_rust", ["core/rust/src/tool_gateway.rs", "core/rust/src/llm.rs"]),
    "cli": ("Native Rust CLI (`aegis-nerve-cli`) & Gate Runner", "L3_surfaces", ["core/rust/src/bin/aegis_nerve_cli.rs", "Dockerfile"]),
    "connections": ("Authenticated Peer & Provider Connection Registry", "L0_core_rust", ["core/rust/src/ffi/connections.rs", "core/python/aegis/connections.py"]),
    "context": ("Deterministic Execution Context & Token Budget Window", "L0_core_rust", ["core/rust/src/orchestrator.rs", "core/rust/src/runtime.rs"]),
    "conversations": ("Immutable Conversation Turn & Hash-Chain Ledger", "L0_core_rust", ["core/rust/src/ffi/conversations.rs", "core/python/aegis/conversations.py"]),
    "descriptor": ("Canonical Capability & Schema Descriptor Hashing", "L0_core_rust", ["core/rust/src/schema.rs", "core/rust/src/skill_registry.rs"]),
    "distributed": ("Single-Writer Distributed Cluster Lease & Candidate Verification", "L0_core_rust", ["cluster/worker_service.py", "scripts/distributed_cluster_soak_gate.py"]),
    "eac": ("Evidence-Authenticated Computation (EAC) Proof Certificates", "L0_core_rust", ["core/rust/src/replay.rs", "core/rust/src/hot_engine.rs"]),
    "evidence_index": ("Content-Addressable Evidence Index & Merkle Digests", "L0_core_rust", ["core/rust/src/ffi/evidence.rs", "core/python/aegis/evidence.py"]),
    "execution": ("OS-Enforced Execution Lanes (Rayon CPU, Tokio I/O, Accelerator, Sandbox)", "L0_core_rust", ["core/rust/src/runtime.rs", "aegis_cognition/runtime.py"]),
    "ffi": ("PyO3 Native Boundary (`aegis_nerve`) Exposing Rust Authorities to Python", "L1_ffi_bridge", ["aegis_cognition/runtime.py", "core/python/aegis_adapter.py"]),
    "goal_intake": ("Goal Intake Validation & Budget Enforcement", "L0_core_rust", ["aegis_cognition/goal_contract.py", "core/rust/src/orchestrator.rs"]),
    "governance": ("Constitutional Policy & Trust-Tier Governance Authority", "L0_core_rust", ["core/rust/src/policy.rs", "core/rust/src/guardrail.rs"]),
    "gt96": ("GT96 Hardware/Runtime Traceability & Determinism Invariants", "L0_core_rust", ["scripts/aese_affected_closure.py", "docs/architecture/GT96_TRACEABILITY.md"]),
    "guardrail": ("Input/Output Safety Guardrails & Secret/PII Interception", "L0_core_rust", ["core/rust/src/tool_gateway.rs", "core/rust/src/governance.rs"]),
    "harness": ("Deterministic Verification & Replay Test Harness", "L0_core_rust", ["core/rust/src/tests.rs", "scripts/run_checks.py"]),
    "hot_engine": ("Orthogonal Hot-Path Evidence Arena & Async Shadow Sealer", "L0_core_rust", ["core/rust/src/replay.rs", "scripts/constitution_audit.py"]),
    "integrations": ("External Tool & Provider Protocol Integration Seams", "L0_core_rust", ["core/rust/src/tool_gateway.rs", "aegis_cognition/extensions.py"]),
    "ipc": ("Framed Length-Prefixed IPC Transport & Channel Guards", "L0_core_rust", ["core/rust/src/bridge_mmap.rs", "desktop/src-tauri/src/main.rs"]),
    "lab": ("Canonical Deterministic LabController State Machine & Event Hash-Chain", "L0_core_rust", ["core/rust/src/ffi/lab.rs", "aegis_cognition/lab.py"]),
    "layout": ("Memory Layout & Cache-Line Alignment Primitives", "L0_core_rust", ["core/rust/src/shm.rs", "core/rust/src/bridge_mmap.rs"]),
    "learning": ("Bounded Episodic Learning & Skill Promotion Authority", "L0_core_rust", ["core/rust/src/ffi/learning.rs", "core/python/aegis/learning.py"]),
    "licensing": ("Third-Party License & Attribution Compliance Verifier", "L0_core_rust", ["scripts/supply_chain_gate.py", "aegis_cognition/code_reuse.py"]),
    "llm": ("LLM Provider Routing, Retry/Fallback & Token Accounting", "L0_core_rust", ["core/python/aegis/provider.py", "scripts/live_provider_429_soak_gate.py"]),
    "memory": ("Tiered Working/Episodic Memory Store & Retrieval Index", "L0_core_rust", ["core/rust/src/context.rs", "core/rust/src/learning.rs"]),
    "message": ("Wire-Format Message Framing, Checksums & Schema Validation", "L0_core_rust", ["core/rust/src/bridge_mmap.rs", "core/rust/src/ipc.rs"]),
    "mvcc": ("Multi-Version Concurrency Control Snapshot Isolation Store", "L0_core_rust", ["core/rust/src/memory.rs", "core/rust/src/task_ledger.rs"]),
    "orchestrator": ("Core Agent Turn Orchestrator & Lease Coordinator", "L0_core_rust", ["core/rust/src/runtime.rs", "aegis_cognition/agent.py"]),
    "physical": ("Physical Unit Dimensional Analysis & ODE Constraint Verifier", "L0_core_rust", ["core/rust/src/lab.rs", "aegis_cognition/lab.py"]),
    "placement": ("Hardware-Aware Cooperative Task Placement & Calibration", "L0_core_rust", ["core/rust/src/runtime.rs", "aegis_cognition/runtime.py"]),
    "policy": ("Execution & Resource Policy Evaluation Engine", "L0_core_rust", ["core/rust/src/resource.rs", "core/rust/src/governance.rs"]),
    "replay": ("Cryptographic Replay Ledger, Endurance & Chaos Verification", "L0_core_rust", ["core/rust/src/hot_engine.rs", "scripts/replay_endurance_gate.py"]),
    "resource": ("Resource Contract Admission, Capacity Feedback & Lease Tokens", "L0_core_rust", ["core/rust/src/runtime.rs", "schemas/resource-contract-v1.json"]),
    "resource_platform": ("OS-Level Resource Enforcement (Linux cgroup v2/rlimit, Windows Job Objects, macOS rlimit)", "L0_core_rust", ["core/rust/src/execution.rs", "core/rust/src/resource.rs"]),
    "runtime": ("Runtime Lease Scheduler, Queue Draining & Expiration Reaper", "L0_core_rust", ["core/rust/src/ffi/runtime.rs", "aegis_cognition/runtime.py"]),
    "sac": ("Self-Auditing Contract (SAC) State Invariant Checker", "L0_core_rust", ["core/rust/src/replay.rs", "core/rust/src/governance.rs"]),
    "sandbox": ("Untrusted Process & Filesystem/Network Isolation Policy", "L0_core_rust", ["core/rust/src/execution.rs", "aegis-plugins/aegis-sandbox"]),
    "schema": ("JSON Schema & Wire Contract Registry", "L0_core_rust", ["schemas/resource-contract-v1.json", "schemas/lease-token-v1.json"]),
    "shm": ("POSIX/Windows Shared Memory Segment Lifecycle", "L0_core_rust", ["core/rust/src/bridge_mmap.rs", "core/rust/src/layout.rs"]),
    "skill_registry": ("Verified Skill Manifest Admission & Digest Registry", "L0_core_rust", ["aegis_cognition/lab.py", "aegis-plugins/aegis-skills"]),
    "speculative": ("Speculative Execution Branching & Rollback Guard", "L0_core_rust", ["core/rust/src/mvcc.rs", "core/rust/src/orchestrator.rs"]),
    "task_ledger": ("Durable Task State Machine & Audit Trail", "L0_core_rust", ["core/rust/src/orchestrator.rs", "core/rust/src/replay.rs"]),
    "telemetry": ("Structured Runtime Telemetry & OpenTelemetry Span Export", "L0_core_rust", ["aegis_cognition/observability.py", "schemas/runtime-telemetry-v1.json"]),
    "tool_gateway": ("Capability-Gated Tool Invocation Gateway", "L0_core_rust", ["aegis_cognition/extensions.py", "core/rust/src/sandbox.rs"]),
    "wasm_plugins": ("Wasmtime 50.0.3 Fuel- & Memory-Bounded Guest Plugin Host", "L0_core_rust", ["core/rust/src/tool_gateway.rs", "scripts/quickjs_cold_start_gate.py"]),
}


def _resolve_import_module(module_name: str | None, level: int) -> str:
    if level == 0:
        assert module_name is not None
        candidate = ROOT / (module_name.replace(".", "/") + ".py")
        if candidate.is_file():
            return candidate.relative_to(ROOT).as_posix()
        init_candidate = ROOT / module_name.replace(".", "/") / "__init__.py"
        if init_candidate.is_file():
            return init_candidate.relative_to(ROOT).as_posix()
        raise ValueError(f"Cannot resolve absolute import {module_name}")
    base = ROOT / "aegis_cognition"
    for _ in range(level - 1):
        base = base.parent
    if module_name:
        candidate = base / (module_name.replace(".", "/") + ".py")
        if candidate.is_file():
            return candidate.relative_to(ROOT).as_posix()
        init_candidate = base / module_name.replace(".", "/") / "__init__.py"
        if init_candidate.is_file():
            return init_candidate.relative_to(ROOT).as_posix()
    raise ValueError(f"Cannot resolve relative import level={level} module={module_name}")


def parse_python_public_exports() -> tuple[list[str], dict[str, str]]:
    exports_path = ROOT / "aegis_cognition" / "_public_exports.py"
    tree = ast.parse(exports_path.read_text(encoding="utf-8"), filename=str(exports_path))
    all_symbols: list[str] = []
    symbol_to_owner: dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    assert isinstance(node.value, ast.List)
                    for elt in node.value.elts:
                        assert isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        all_symbols.append(elt.value)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            owner_path = _resolve_import_module(node.module, node.level)
            for alias in node.names:
                exported_name = alias.asname or alias.name
                symbol_to_owner[exported_name] = owner_path
        elif isinstance(node, ast.FunctionDef) and node.name in ("version",):
            symbol_to_owner[node.name] = "aegis_cognition/_public_exports.py"

    return all_symbols, symbol_to_owner


def parse_rust_public_modules() -> list[str]:
    lib_rs = (ROOT / "core" / "rust" / "src" / "lib.rs").read_text(encoding="utf-8")
    return re.findall(r"^pub mod ([a-z0-9_]+);", lib_rs, flags=re.MULTILINE)


def parse_workspace_plugins() -> list[str]:
    cargo = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    members = cargo["workspace"]["default-members"]
    return [m for m in members if m.startswith("aegis-plugins/")]


def build_ownership_payload() -> dict[str, Any]:
    all_symbols, symbol_to_owner = parse_python_public_exports()
    unmapped_py = [sym for sym in all_symbols if sym not in symbol_to_owner]
    if unmapped_py:
        raise ValueError(f"Unmapped Python public exports: {unmapped_py}")

    grouped_py: dict[str, list[str]] = {}
    for sym in all_symbols:
        owner = symbol_to_owner[sym]
        grouped_py.setdefault(owner, []).append(sym)

    python_capabilities: list[dict[str, Any]] = []
    for owner_path, symbols in sorted(grouped_py.items()):
        if owner_path not in PYTHON_MODULE_METADATA:
            raise ValueError(f"Missing metadata for Python owner module: {owner_path}")
        meta = PYTHON_MODULE_METADATA[owner_path]
        python_capabilities.append(
            {
                "capability_id": meta["capability_id"],
                "domain": meta["domain"],
                "layer": meta["layer"],
                "canonical_owner": owner_path,
                "adapter_or_facade_paths": meta["adapter_or_facade_paths"],
                "public_symbols": sorted(symbols),
                "symbol_count": len(symbols),
                "consumers": meta["consumers"],
                "owning_tests": meta["owning_tests"],
                "performance_budget": meta["performance_budget"],
            }
        )

    rust_modules = parse_rust_public_modules()
    unmapped_rust = [m for m in rust_modules if m not in RUST_MODULE_DOMAINS]
    if unmapped_rust:
        raise ValueError(f"Unmapped Rust public modules in core/rust/src/lib.rs: {unmapped_rust}")

    rust_capabilities: list[dict[str, Any]] = []
    for mod_name in sorted(rust_modules):
        domain, layer, consumers = RUST_MODULE_DOMAINS[mod_name]
        mod_file = f"core/rust/src/{mod_name}.rs"
        if not (ROOT / mod_file).is_file():
            mod_file = f"core/rust/src/{mod_name}/mod.rs"
        rust_capabilities.append(
            {
                "capability_id": f"CAP-RS-{mod_name.upper().replace('_', '-')}",
                "module": mod_name,
                "domain": domain,
                "layer": layer,
                "canonical_owner": mod_file,
                "consumers": consumers,
                "owning_tests": ["core/rust/src/tests.rs"],
                "performance_budget": "Deterministic execution; zero unbounded allocation on hot path",
            }
        )

    plugin_crates = parse_workspace_plugins()
    plugin_capabilities: list[dict[str, Any]] = []
    for crate_path in sorted(plugin_crates):
        crate_name = crate_path.split("/")[-1]
        plugin_capabilities.append(
            {
                "capability_id": f"CAP-PLUGIN-{crate_name.upper()}",
                "crate": crate_name,
                "layer": "L0_core_rust",
                "canonical_owner": f"{crate_path}/src/lib.rs",
                "role": "Standalone reusable Rust workspace crate (contract/library seam; runtime enforcement owned by core/rust and aegis_cognition)",
                "consumers": ["docs/integrations/aegis-plugins.md", "Cargo.toml"],
                "owning_tests": [f"{crate_path}/src/lib.rs"],
            }
        )

    surface_capabilities: list[dict[str, Any]] = [
        {
            "capability_id": "CAP-SURF-PYTHON-CLI",
            "surface": "Python CLI (`aegis`)",
            "layer": "L3_surfaces",
            "canonical_owner": "aegis_cognition/cli.py",
            "commands": ["init", "run", "examples", "version", "config", "verify", "mcp-serve"],
            "consumers": ["pyproject.toml", "tests/test_public_route_boundary.py", "tests/test_mcp_hub_and_harnesses.py"],
            "owning_tests": ["tests/test_public_route_boundary.py", "tests/test_mcp_hub_and_harnesses.py"],
            "performance_budget": "CLI `--help` / `version` / `--dry-run` <= 100 ms",
        },
        {
            "capability_id": "CAP-SURF-RUST-CLI",
            "surface": "Native Rust CLI (`aegis-nerve-cli`)",
            "layer": "L3_surfaces",
            "canonical_owner": "core/rust/src/cli/mod.rs",
            "commands": ["gate-runner", "replay-verify", "worker"],
            "consumers": ["Dockerfile", "deploy/docker-compose.yml", "deploy/kubernetes.yaml"],
            "owning_tests": ["core/rust/src/tests.rs"],
            "performance_budget": "Cold start <= 25 ms",
        },
        {
            "capability_id": "CAP-SURF-OPERATOR-API",
            "surface": "Read-Only Operator Evidence HTTP API",
            "layer": "L3_surfaces",
            "canonical_owner": "core/python/operator_api.py",
            "entrypoint": "core/python/operator_api_server.py",
            "healthcheck": "core/python/operator_api_healthcheck.py",
            "routes": ["/", "/health", "/snapshot", "/artifacts", "/production-closure"],
            "consumers": [
                "Dockerfile",
                "deploy/docker-compose.yml",
                "deploy/kubernetes.yaml",
                "scripts/external_deployment_smoke_gate.py",
            ],
            "owning_tests": [
                "tests/test_public_route_boundary.py",
                "tests/test_operator_api_healthcheck.py",
                "core/python/tests.py",
            ],
            "performance_budget": "GET /health <= 50 ms; bounded 64 KiB health response; single HTTP server authority (ThreadingHTTPServer)",
        },
        {
            "capability_id": "CAP-SURF-DESKTOP-SERVICE",
            "surface": "Desktop Backend JSON-RPC Service & Tauri Host",
            "layer": "L3_surfaces",
            "canonical_owner": "aegis_cognition/desktop_service.py",
            "adapter_or_facade_paths": ["desktop/src-tauri/src/main.rs"],
            "consumers": ["desktop/src-tauri/src/main.rs", "tests/test_desktop_protocol.py"],
            "owning_tests": ["tests/test_desktop_protocol.py", "tests/test_desktop_shell.py"],
            "performance_budget": "Frame parse & dispatch <= 10 ms per RPC request",
        },
    ]

    overlap_resolutions: list[dict[str, str]] = [
        {
            "domain": "Operator API HTTP Server",
            "canonical_owner": "core/python/operator_api.py (serve_operator_evidence_api / ThreadingHTTPServer)",
            "resolution": "Removed duplicate Starlette ASGI server (create_app); tests/test_public_route_boundary.py exercises the real ThreadingHTTPServer over loopback HTTP sockets (ADR-022).",
            "adr": "docs/adr/ADR-022-operator-api-single-server.md",
        },
        {
            "domain": "Execution Lanes vs Local Verification Command Builder",
            "canonical_owner": "core/rust/src/execution.rs (OS sandbox & CPU/IO/Accelerator lanes) vs aegis_cognition/verification/execution.py (bounded test-runner argv builder & JUnit/Cargo parser)",
            "resolution": "Strict separation of responsibilities: verification/execution.py constructs bounded test commands and parses test reports inside ProcessExecutionCell scopes; core/rust/src/execution.rs owns OS process isolation and execution lanes (ADR-023).",
            "adr": "docs/adr/ADR-023-execution-boundary.md",
        },
        {
            "domain": "Lab State Authority vs Python Lab SDK Façade",
            "canonical_owner": "core/rust/src/lab.rs (deterministic LabController state machine) vs aegis_cognition/lab.py (Python developer SDK, cells, and unit registry)",
            "resolution": "core/rust/src/lab.rs is the single state & hash-chain authority exposed via core/rust/src/ffi/lab.rs; aegis_cognition/lab.py owns high-level Python cell ergonomics and delegates state transitions to Rust (ADR-024).",
            "adr": "docs/adr/ADR-024-lab-authority-boundary.md",
        },
        {
            "domain": "Core Gateway Adapters (conversations, connections, learning, evidence, mmap_bridge)",
            "canonical_owner": "core/rust/src/{conversations,connections,learning,evidence_index,bridge_mmap}.rs",
            "resolution": "core/python/aegis/*.py modules are thin typed Python DTO/FFI adapters over core/rust/src/ffi/*.rs (ADR-005, ADR-021).",
            "adr": "docs/adr/ADR-021-foundation-convergence.md",
        },
    ]

    unsigned_payload = {
        "schema": "aegis-capability-ownership-v1",
        "truth_claim": False,
        "summary": {
            "python_public_export_count": len(all_symbols),
            "python_owner_module_count": len(python_capabilities),
            "rust_public_module_count": len(rust_capabilities),
            "workspace_plugin_crate_count": len(plugin_capabilities),
            "surface_capability_count": len(surface_capabilities),
            "unmapped_export_count": 0,
            "duplicate_canonical_owner_count": 0,
        },
        "layer_hierarchy": [
            "L0_core_rust",
            "L1_ffi_bridge",
            "L2_python_sdk",
            "L3_surfaces",
        ],
        "python_sdk_capabilities": python_capabilities,
        "rust_core_capabilities": rust_capabilities,
        "workspace_plugin_capabilities": plugin_capabilities,
        "surface_capabilities": surface_capabilities,
        "overlap_resolutions": overlap_resolutions,
    }
    digest = hashlib.sha256(
        json.dumps(unsigned_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **unsigned_payload,
        "artifact_hash": digest,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    lines = [
        "# Canonical Capability Ownership Map",
        "",
        "This document is deterministically generated by `scripts/generate_capability_ownership.py` from `docs/architecture/capability_ownership.json`.",
        "",
        "## Summary",
        "",
        f"- **Schema**: `{payload['schema']}`",
        f"- **Artifact SHA-256**: `{payload['artifact_hash']}`",
        f"- **Python Public Exports Mapped (`aegis_cognition._public_exports.__all__`)**: `{s['python_public_export_count']}` (`0` unmapped)",
        f"- **Python Canonical Owner Modules**: `{s['python_owner_module_count']}`",
        f"- **Rust Core Public Modules (`core/rust/src/lib.rs`)**: `{s['rust_public_module_count']}` (`0` unmapped)",
        f"- **Workspace Plugin Crates (`aegis-plugins/*`)**: `{s['workspace_plugin_crate_count']}`",
        f"- **Product Surfaces**: `{s['surface_capability_count']}`",
        "",
        "## Strict 4-Layer Hierarchy",
        "",
        "1. **`L0_core_rust` (`core/rust/src/*`, `aegis-plugins/*`)**: Canonical cryptographic, replay, resource, sandbox, placement, and deterministic state-machine authorities.",
        "2. **`L1_ffi_bridge` (`core/rust/src/ffi/*`, `aegis_cognition/runtime.py`, `core/python/aegis/*`)**: Thin PyO3 and shared-memory adapters; never duplicate `L0` state authority.",
        "3. **`L2_python_sdk` (`aegis_cognition/*`)**: Developer-facing Python SDK, verification session façade, multi-agent coordination, and domain cells.",
        "4. **`L3_surfaces` (`aegis_cognition/cli.py`, `core/rust/src/cli/*`, `core/python/operator_api.py`, `aegis_cognition/desktop_service.py`)**: CLI, HTTP, and Desktop RPC entrypoints.",
        "",
        "## Overlap Resolutions & Architectural Decisions",
        "",
        "| Domain | Canonical Owner | Resolution | Governing ADR |",
        "| :--- | :--- | :--- | :--- |",
    ]
    lines.extend(
        f"| {item['domain']} | `{item['canonical_owner']}` | {item['resolution']} | `{item['adr']}` |"
        for item in payload["overlap_resolutions"]
    )

    lines.extend(
        [
            "",
            "## Python SDK Capabilities (`L1` / `L2`)",
            "",
            "| Capability ID | Canonical Owner | Layer | Exported Symbols | Owning Tests | Performance Budget |",
            "| :--- | :--- | :--- | ---: | :--- | :--- |",
        ]
    )
    for cap in payload["python_sdk_capabilities"]:
        tests_str = ", ".join(f"`{t}`" for t in cap["owning_tests"])
        lines.append(
            f"| `{cap['capability_id']}` | `{cap['canonical_owner']}` | `{cap['layer']}` | {cap['symbol_count']} | {tests_str} | {cap['performance_budget']} |"
        )

    lines.extend(
        [
            "",
            "## Rust Core Capabilities (`L0` / `L1` / `L3`)",
            "",
            "| Capability ID | Module | Canonical Owner | Layer | Domain |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    lines.extend(
        f"| `{cap['capability_id']}` | `{cap['module']}` | `{cap['canonical_owner']}` | `{cap['layer']}` | {cap['domain']} |"
        for cap in payload["rust_core_capabilities"]
    )

    lines.extend(
        [
            "",
            "## Workspace Plugin Crates (`aegis-plugins/*`)",
            "",
            "| Capability ID | Crate | Canonical Owner | Role |",
            "| :--- | :--- | :--- | :--- |",
        ]
    )
    lines.extend(
        f"| `{cap['capability_id']}` | `{cap['crate']}` | `{cap['canonical_owner']}` | {cap['role']} |"
        for cap in payload["workspace_plugin_capabilities"]
    )

    lines.extend(
        [
            "",
            "## Product Surfaces (`L3`)",
            "",
            "| Capability ID | Surface | Canonical Owner | Owning Tests | Performance Budget |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for cap in payload["surface_capabilities"]:
        tests_str = ", ".join(f"`{t}`" for t in cap["owning_tests"])
        lines.append(
            f"| `{cap['capability_id']}` | {cap['surface']} | `{cap['canonical_owner']}` | {tests_str} | {cap['performance_budget']} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify capability ownership artifacts.")
    parser.add_argument("--check", action="store_true", help="Verify artifacts are up to date without writing.")
    args = parser.parse_args()

    payload = build_ownership_payload()
    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    md_text = render_markdown(payload)

    if args.check:
        if not JSON_OUT.is_file() or not MD_OUT.is_file():
            print("capability ownership artifacts missing", file=sys.stderr)
            return 1
        if JSON_OUT.read_text(encoding="utf-8") != json_text:
            print("docs/architecture/capability_ownership.json is out of date", file=sys.stderr)
            return 1
        if MD_OUT.read_text(encoding="utf-8") != md_text:
            print("docs/architecture/CAPABILITY_OWNERSHIP.md is out of date", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "status": "CAPABILITY_OWNERSHIP_VERIFIED",
                    "artifact_hash": payload["artifact_hash"],
                    "python_public_export_count": payload["summary"]["python_public_export_count"],
                    "rust_public_module_count": payload["summary"]["rust_public_module_count"],
                }
            )
        )
        return 0

    JSON_OUT.write_text(json_text, encoding="utf-8")
    MD_OUT.write_text(md_text, encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "CAPABILITY_OWNERSHIP_GENERATED",
                "artifact_hash": payload["artifact_hash"],
                "python_public_export_count": payload["summary"]["python_public_export_count"],
                "rust_public_module_count": payload["summary"]["rust_public_module_count"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
