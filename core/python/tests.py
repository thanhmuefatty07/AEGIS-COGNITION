from unittest.mock import patch
from tempfile import TemporaryDirectory
import asyncio
import hashlib
import json
import os
import struct
import sys
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from .bridge_mmap import MMAP_BRIDGE_HEADER_BYTES, MmapBridgeFrame, execute_mmap_wasm_bridge_frame
from .browser_live_collector import (
    BrowserLiveCollectorArtifact,
    BrowserLiveCollectorProducer,
    REQUIRED_BROWSER_ARTIFACT_KINDS,
    build_browser_live_collector_artifacts,
)
from .browser_runtime_adapter import (
    BrowserRuntimeCaptureError,
    BrowserRuntimeCollectorAdapter,
    capture_browser_runtime_snapshot,
    collect_browser_network_log,
    publish_hot_browser_evidence,
)
from .browser_playwright_runtime import (
    PlaywrightBrowserRuntimeSession,
    capture_playwright_action,
)
from .browser_ops_bench import BrowserOpsBench, BrowserOpsBenchPredicate, BrowserOpsBenchTask
from .aegis_adapter import Agent, ProviderRateLimitError, commit_hot_evidence, trust_policy_snapshot
from .orchestrator import build_message, to_zero_copy, validate_runtime_message
from .integration import build_bridge_message, validate_bridge_batch, validate_bridge_contract, validate_bridge_smoke
from .preflight import check_build_preflight
from .service import ExternalServiceManifest, build_service_manifest
from .operator_api import OperatorEvidenceApi, build_operator_evidence_snapshot, resolve_operator_root, serve_operator_evidence_api
from scripts.dependency_audit_gate import evaluate_dependency_audit_gate
from scripts.dynamic_provider_fallback_gate import evaluate_dynamic_provider_fallback_gate, write_live_provider_429_soak_capture
from scripts.e2e_release_gate import evaluate_e2e_release_gate
from scripts.external_deployment_smoke_gate import evaluate_external_deployment_smoke_gate, write_external_deployment_smoke_capture
from scripts.provider_route_gate import evaluate_provider_route_gate
from scripts.quickjs_cold_start_gate import (
    SEMANTIC_CORPUS_CASES,
    evaluate_quickjs_cold_start_gate,
    write_full_quickjs_interpreter_cold_start_capture,
)
from scripts.supply_chain_gate import (
    evaluate_supply_chain_gate,
    write_release_signed_attestation_artifacts,
    _stable_hash as stable_supply_hash,
)
from scripts.tcp_cluster_soak_gate import evaluate_tcp_cluster_soak_gate, write_real_multi_machine_cluster_soak_capture


def test_message_validates_and_converts():
    message = build_message(1, 2, b'abc')
    assert validate_runtime_message(message)
    zero_copy = to_zero_copy(message)
    assert zero_copy.is_valid()
    assert zero_copy.message_id == 1
    assert zero_copy.session_id == 2


def test_bridge_contract_validates():
    message = build_message(1, 2, b'abc')
    result = validate_bridge_contract(message)
    assert result.message_ok
    assert result.zero_copy_ok
    assert result.schema_ok
    assert result.alignment_ok
    assert result.layout_ok
    assert result.metadata_ok
    assert result.descriptor_ok
    assert result.bridge_ok


def test_build_bridge_message():
    zero_copy = build_bridge_message(1, 2, b'abc')
    assert zero_copy.is_valid()


def test_bridge_contract_rejects_empty_payload():
    message = build_message(1, 2, b'')
    result = validate_bridge_contract(message)
    assert not result.message_ok or not result.bridge_ok


def test_bridge_smoke_ok():
    message = build_message(1, 2, b'abc')
    smoke = validate_bridge_smoke(message)
    assert smoke.overall_ok
    assert smoke.contract.bridge_ok
    assert smoke.payload_size_ok
    assert smoke.identity_ok


def test_bridge_batch_ok():
    messages = [
        build_message(1, 2, b'abc'),
        build_message(2, 3, b'def'),
        build_message(3, 4, b'ghi'),
    ]
    batch = validate_bridge_batch(messages)
    assert batch.overall_ok
    assert batch.total_messages == 3
    assert batch.passed_messages == 3



def test_build_preflight_reports_missing_toolchain_cleanly():
    with patch("core.python.preflight.which", return_value=None):
        result = check_build_preflight(r'c:\Users\ADMIN\AEGIS-COGNITION')
        assert result.python_bridge_present
        assert result.rust_core_present
        assert not result.cargo_present
        assert not result.rustc_present
        assert not result.ready_to_benchmark


def test_build_preflight_reports_present_toolchain_cleanly():
    with patch("core.python.preflight.which", return_value="/path/to/binary"):
        result = check_build_preflight(r'c:\Users\ADMIN\AEGIS-COGNITION')
        assert result.python_bridge_present
        assert result.rust_core_present
        assert result.cargo_present
        assert result.rustc_present
        assert result.ready_to_benchmark



def test_service_manifest_reports_packaging_state():
    message = build_message(1, 2, b'abc')
    manifest = build_service_manifest(message)
    assert manifest.service_name == 'aegis-cognition'
    assert manifest.version == '0.1.0'
    assert manifest.bridge_ready
    assert manifest.contract_ok
    assert manifest.runtime_surface_ok
    assert manifest.packaging_ready


def test_service_manifest_type_is_frozen_dataclass():
    message = build_message(1, 2, b'abc')
    manifest = build_service_manifest(message)
    assert isinstance(manifest, ExternalServiceManifest)


def test_operator_evidence_snapshot_summarizes_hash_bound_reports():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        (artifacts / "run_checks_report.json").write_text(
            json.dumps(
                {
                    "benchmark_gate": {"overall_ok": True},
                    "constitution_audit": {"overall_ok": True},
                    "report_hash": "ab" * 32,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (artifacts / "benchmark_gate_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 2, "failed": 0, "checks": []}, sort_keys=True),
            encoding="utf-8",
        )
        (artifacts / "constitution_audit_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 3, "failed": 0, "checks": []}, sort_keys=True),
            encoding="utf-8",
        )
        (artifacts / "e2e_release_gate_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 11, "failed": 0, "checks": []}, sort_keys=True),
            encoding="utf-8",
        )
        (artifacts / "provider_route_gate_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 4, "failed": 0, "checks": []}, sort_keys=True),
            encoding="utf-8",
        )
        (artifacts / "dependency_audit_gate_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 6, "failed": 0, "checks": []}, sort_keys=True),
            encoding="utf-8",
        )

        snapshot = build_operator_evidence_snapshot(
            root,
            (
                "run_checks_report.json",
                "benchmark_gate_report.json",
                "constitution_audit_report.json",
                "e2e_release_gate_report.json",
                "provider_route_gate_report.json",
                "dependency_audit_gate_report.json",
            ),
        )

        assert not snapshot.truth_claim
        assert snapshot.verifier == "rust-replay-and-artifact-gates"
        assert snapshot.release_gate_observed_ok
        assert snapshot.missing_artifacts == ()
        assert snapshot.snapshot_digest.hex
        summaries = {summary.logical_name: summary for summary in snapshot.artifact_summaries}
        assert summaries["run_checks_report.json"].nonzero_hash_field_count == 1
        assert summaries["benchmark_gate_report.json"].passed == 2
        assert summaries["e2e_release_gate_report.json"].passed == 11
        assert summaries["provider_route_gate_report.json"].passed == 4
        assert summaries["dependency_audit_gate_report.json"].passed == 6


def test_operator_root_resolves_from_artifacts_env(monkeypatch):
    monkeypatch.delenv("AEGIS_OPERATOR_ROOT", raising=False)
    monkeypatch.setenv("AEGIS_ARTIFACTS_DIR", "/var/lib/aegis/artifacts")

    assert resolve_operator_root() == Path("/var/lib/aegis").resolve()


def test_supply_chain_gate_records_missing_release_attestation_without_production_claim():
    with TemporaryDirectory() as tmp:
        root = _minimal_supply_chain_root(Path(tmp))
        report = evaluate_supply_chain_gate(root)

        assert report["overall_ok"] is True
        assert report["external_signed_attestation_present"] is False
        assert report["production_release_blocked_without_external_attestation"] is True
        assert report["release_attestation"]["status"] == "missing"
        assert report["release_attestation"]["attestation_state_recorded"] is True


def test_supply_chain_gate_admits_hash_bound_external_release_attestation():
    with TemporaryDirectory() as tmp:
        root = _minimal_supply_chain_root(Path(tmp))
        writer = write_release_signed_attestation_artifacts(
            root,
            external_verifier="cosign",
            signature_bundle_hash="aa" * 32,
            transparency_log_entry_hash="bb" * 32,
            certificate_identity_hash="cc" * 32,
        )

        report = evaluate_supply_chain_gate(root)

        assert writer["schema"] == "aegis-release-signed-attestation-artifact-writer-v1"
        assert writer["truth_claim"] is False
        assert writer["writer_valid"] is True
        assert writer["artifacts_written"] is True
        assert len(writer["writer_evidence_hash"]) == 64
        assert report["overall_ok"] is True
        assert report["external_signed_attestation_present"] is True
        assert report["production_release_blocked_without_external_attestation"] is False
        assert report["release_attestation"]["status"] == "verified"
        assert report["release_attestation"]["external_verifier"] == "cosign"


def test_supply_chain_release_attestation_writer_rejects_missing_external_verifier():
    with TemporaryDirectory() as tmp:
        root = _minimal_supply_chain_root(Path(tmp))
        writer = write_release_signed_attestation_artifacts(
            root,
            external_verifier="",
            signature_bundle_hash="aa" * 32,
            transparency_log_entry_hash="bb" * 32,
            certificate_identity_hash="cc" * 32,
        )
        report = evaluate_supply_chain_gate(root)

        assert writer["writer_valid"] is False
        assert writer["artifacts_written"] is False
        assert writer["attestation_hash"] == ""
        assert "external verifier" in writer["error"]
        assert not (root / "artifacts" / "release_signed_attestation.json").exists()
        assert not (root / "artifacts" / "release_signed_attestation_verification.json").exists()
        assert report["overall_ok"] is True
        assert report["external_signed_attestation_present"] is False
        assert report["release_attestation"]["status"] == "missing"


def test_supply_chain_gate_rejects_mismatched_release_attestation():
    with TemporaryDirectory() as tmp:
        root = _minimal_supply_chain_root(Path(tmp))
        attestation = {
            "schema": "aegis-release-signed-attestation-v1",
            "subject_hash": "11" * 32,
            "signature": {
                "signature_bundle_hash": "aa" * 32,
                "transparency_log_entry_hash": "bb" * 32,
                "certificate_identity_hash": "cc" * 32,
            },
        }
        attestation_hash = stable_supply_hash(attestation)
        (root / "artifacts" / "release_signed_attestation.json").write_text(
            json.dumps(attestation, sort_keys=True),
            encoding="utf-8",
        )
        (root / "artifacts" / "release_signed_attestation_verification.json").write_text(
            json.dumps(
                {
                    "schema": "aegis-release-signed-attestation-verification-v1",
                    "verified": True,
                    "external_verifier": "cosign",
                    "verified_subject_hash": "11" * 32,
                    "attestation_hash": attestation_hash,
                    "signature_bundle_hash": "aa" * 32,
                    "transparency_log_entry_hash": "bb" * 32,
                    "certificate_identity_hash": "cc" * 32,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        report = evaluate_supply_chain_gate(root)

        assert report["overall_ok"] is False
        assert report["release_attestation"]["status"] == "invalid"
        assert report["release_attestation"]["external_signed_attestation_present"] is False


def _minimal_supply_chain_root(root: Path) -> Path:
    (root / "core" / "rust" / "src").mkdir(parents=True)
    (root / "core" / "python").mkdir(parents=True)
    (root / "artifacts").mkdir()
    (root / "Cargo.lock").write_text(
        'version = 3\n[[package]]\nname = "x"\nversion = "0.1.0"\nsource = "registry+https://example.invalid"\nchecksum = "abc"\n',
        encoding="utf-8",
    )
    (root / "Cargo.toml").write_text('[workspace]\nmembers = ["core/rust"]\n', encoding="utf-8")
    (root / "core" / "rust" / "Cargo.toml").write_text(
        '[package]\nname = "aegis-nerve"\nversion = "0.1.0"\n'
        '[dependencies]\nwasmtime = "0"\n'
        '[features]\ndefault = []\nnetwork-h3 = []\n',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "aegis-cognition"\nversion = "0.1.0"\ndependencies = ["openai>=1.30.0"]\n',
        encoding="utf-8",
    )
    (root / "core" / "python" / "pyproject.toml").write_text(
        '[project]\nname = "aegis-cognition-core-python"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (root / "core" / "rust" / "src" / "sandbox.rs").write_text(
        "Config::new().consume_fuel(true).epoch_interruption(true); "
        "store.set_epoch_deadline(1); StoreLimitsBuilder::new().memory_size(1); "
        "let config = SandboxConfig { network_isolated: true }; self.network_isolated;",
        encoding="utf-8",
    )
    (root / "core" / "rust" / "src" / "tool_gateway.rs").write_text("tool gateway", encoding="utf-8")
    return root


def test_e2e_release_gate_binds_goal_to_next_action_artifacts():
    class FakeDeploymentManifest:
        def to_report(self):
            return {
                "overall_ok": True,
                "topology": {
                    "schema": "aegis-deployment-topology-contract-v1",
                    "contract_invariants": {
                        "single_writer_placement_enforced": True,
                        "durable_storage_defined": True,
                    },
                    "durable_volumes": {
                        "replay-ledger": {"append_only": True},
                        "shadow-seals": {"append_only": True},
                    },
                },
                "topology_hash": "aa" * 32,
                "topology_contract_hash": "ab" * 32,
                "service_boundary_hash": "bb" * 32,
                "service_placement_hash": "bc" * 32,
                "network_edge_hash": "bd" * 32,
                "storage_volume_hash": "be" * 32,
                "rollback_plan_hash": "bf" * 32,
                "operator_runbook_hash": "ca" * 32,
                "production_blocker_hash": "99" * 32,
                "active_production_blocker_hash": "ee" * 32,
                "production_blockers": [
                    {
                        "id": "external_signed_attestation_missing",
                        "blocks_production": True,
                        "evidence_artifact": "supply_chain_gate_report.json",
                    }
                ],
                "active_production_blockers": [
                    {
                        "id": "external_signed_attestation_missing",
                        "blocks_production": True,
                        "evidence_artifact": "supply_chain_gate_report.json",
                        "active": True,
                    }
                ],
                "production_deployable": False,
                "external_signed_attestation_present": False,
                "release_manifest_hash": "cc" * 32,
                "deployment_manifest_hash": "dd" * 32,
                "topology_cannot_weaken_policy": True,
                "remote_workers_candidate_only": True,
                "service_placements_cover_boundaries": True,
                "single_writer_placement_enforced": True,
                "network_edges_candidate_only": True,
                "durable_storage_defined": True,
                "topology_contract_materialized": True,
                "durable_volumes_materialized": True,
                "health_checks_defined": True,
                "operator_rollback_defined": True,
                "operator_runbook_defined": True,
                "production_blockers_declared": True,
                "production_packaging_smoke_present": True,
                "production_packaging_smoke_hash": "fa" * 32,
                "external_deployment_smoke_hash": "fb" * 32,
                "release_attestation_hash": "ff" * 32,
            }

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        (artifacts / "governance_gate_report.json").write_text(
            json.dumps(
                {
                    "overall_ok": True,
                    "checks": [{"name": "provider_rate_limit_mitigation", "ok": True}],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (artifacts / "provider_route_gate_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 4, "failed": 0}, sort_keys=True),
            encoding="utf-8",
        )
        (artifacts / "dependency_audit_gate_report.json").write_text(
            json.dumps({"overall_ok": True, "passed": 6, "failed": 0}, sort_keys=True),
            encoding="utf-8",
        )
        (artifacts / "agentic_sdk_context_report.json").write_text(
            json.dumps(
                {
                    "report": {
                        "active_task_id": 1,
                        "context_pack_digest": [1],
                        "context_pack_candidate_proof_hash": [2],
                        "sdk_run_replay_binding_hash": [3],
                        "candidate_list_hash": [4],
                        "next_action_packet_hash": [5],
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (artifacts / "browser_ops_bench_verification_report.json").write_text(
            json.dumps(
                {
                    "report": {
                        "proof_hash": [6],
                        "verified_records_hash": [7],
                        "scorecard_file_hash": [8],
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (artifacts / "replay_endurance_report.json").write_text(
            json.dumps(
                {
                    "report": {
                        "segmented_arrow_audit_proof_hash": [9],
                        "run_checkpoint_hash": [10],
                        "replay_determinism_proof_hash": [11],
                        "next_action_packet_hash": [12],
                    }
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (artifacts / "benchmark_gate_report.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {"name": "task_ledger_ready_queue_10k", "ok": True},
                        {"name": "browser_tool_gateway_ingest", "ok": True},
                        {"name": "wasmtime_execute_cached_module", "ok": True},
                        {"name": "policy_datalog_closure_proof", "ok": True},
                        {"name": "browser_action_plan_live_manifest_gateway_ingest", "ok": True},
                        {"name": "pav_ast_distance_registered", "ok": True},
                    ]
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        with (
            patch("scripts.e2e_release_gate.evaluate_supply_chain_gate", return_value={
                "overall_ok": True,
                "sbom": {"sbom_index_hash": "aa" * 32},
                "provenance": {"provenance_hash": "bb" * 32},
                "wasi_deny_by_default": {
                    "wasmtime_fuel_enabled": True,
                    "wasmtime_epoch_enabled": True,
                    "wasmtime_memory_limited": True,
                    "sandbox_network_isolated": True,
                },
                "release_attestation": {
                    "attestation_state_recorded": True,
                    "expected_subject_hash": "dd" * 32,
                    "release_attestation_evidence_hash": "ee" * 32,
                },
                "production_release_blocked_without_external_attestation": True,
            }),
            patch("scripts.e2e_release_gate.evaluate_dynamic_provider_fallback_gate", return_value={
                "overall_ok": True,
                "artifact_sha256": "ad" * 32,
                "dynamic_provider_fallback_evidence": {
                    "fallback_used": True,
                    "downgraded_model": True,
                    "throttled_provider_count": 1,
                    "latency_under_gate": True,
                },
                "live_provider_429_soak_admission": {
                    "schema": "aegis-live-provider-429-soak-admission-v1",
                    "expected_capture_path": "artifacts/live_provider_429_soak_capture.json",
                },
                "live_provider_429_soak_admission_hash": [1],
                "live_provider_429_soak_capture_missing_or_valid": True,
                "live_provider_429_soak_admission_blocker_id": "live_provider_429_soak_missing",
                "live_provider_429_soak_state_recorded": True,
            }),
            patch("scripts.e2e_release_gate.evaluate_cluster_loopback_gate", return_value={
                "overall_ok": True,
                "artifact_sha256": "aa" * 32,
                "loopback_cluster_evidence": {
                    "worker_count": 3,
                    "work_item_count": 32,
                    "accepted_count": 32,
                    "max_logical_rtt_ticks": 4,
                },
                "production_cluster_release_blocked_without_real_multi_node_soak": True,
            }),
            patch("scripts.e2e_release_gate.evaluate_tcp_cluster_soak_gate", return_value={
                "overall_ok": True,
                "artifact_sha256": "bb" * 32,
                "tcp_cluster_soak_evidence": {
                    "worker_count": 3,
                    "work_item_count": 24,
                    "tcp_worker_connections": 3,
                    "tcp_round_trip_count": 24,
                },
                "real_multi_machine_cluster_admission": {
                    "schema": "aegis-real-multi-machine-cluster-soak-admission-v1",
                    "expected_capture_path": "artifacts/real_multi_machine_cluster_soak_capture.json",
                },
                "real_multi_machine_cluster_admission_hash": [1],
                "real_multi_machine_cluster_capture_missing_or_valid": True,
                "real_multi_machine_cluster_admission_blocker_id": "real_multi_machine_cluster_soak_missing",
                "real_multi_machine_cluster_state_recorded": True,
                "production_cluster_release_blocked_without_real_multi_node_soak": True,
            }),
            patch("scripts.e2e_release_gate.evaluate_quickjs_cold_start_gate", return_value={
                "overall_ok": True,
                "artifact_sha256": "cc" * 32,
                "quickjs_cold_start_evidence": {
                    "sample_count": 5,
                    "cold_start_total_ns": 100,
                    "warm_cached_total_ns": 10,
                    "bridge_probe_executed": True,
                },
                "full_interpreter_admission": {
                    "schema": "aegis-quickjs-full-interpreter-admission-v1",
                    "expected_runtime_path": "artifacts/quickjs_full_interpreter.wasm",
                },
                "full_interpreter_admission_hash": [1],
                "full_interpreter_semantic_capture_missing_or_valid": True,
                "full_interpreter_admission_blocker_id": "full_quickjs_interpreter_cold_start_missing",
                "full_interpreter_state_recorded": True,
                "production_quickjs_release_blocked_without_full_interpreter": True,
            }),
            patch("scripts.e2e_release_gate.evaluate_hot_browser_shadow_gate", return_value={
                "overall_ok": True,
                "artifact_sha256": "dd" * 32,
                "hot_browser_shadow_evidence": {
                    "artifact_count": 8,
                    "hot_commit_count": 8,
                    "cold_seal_receipt_count": 8,
                    "shadow_seal_replay_recorded": True,
                    "shadow_arrow_archive_recovered": True,
                    "shadow_arrow_archive_replay_matches": True,
                    "shadow_arrow_archive_contains_shadow_seal": True,
                    "no_file_roundtrip_on_hot_path": True,
                },
            }),
            patch("scripts.e2e_release_gate.evaluate_shadow_sealer_soak_gate", return_value={
                "overall_ok": True,
                "artifact_sha256": "ae" * 32,
                "shadow_sealer_soak_evidence": {
                    "sample_count": 128,
                    "hot_submit_under_gate": True,
                    "hot_submissions_completed_before_receipts": True,
                    "cold_payload_files_materialized": True,
                    "cold_payload_file_hashes_match": True,
                    "cold_payload_sync_requested": True,
                },
            }),
            patch("scripts.e2e_release_gate.evaluate_production_packaging_smoke_gate", return_value={
                "overall_ok": True,
                "production_packaging_smoke_present": True,
                "package_surface_hash": "af" * 32,
                "smoke_evidence_hash": "ba" * 32,
                "smoke_evidence": {
                    "friendly_gateway_hash": "bb" * 32,
                    "service_manifest_hash": "bc" * 32,
                    "artifact_write_hash": "bd" * 32,
                },
            }),
            patch("scripts.e2e_release_gate.evaluate_external_deployment_smoke_gate", return_value={
                "overall_ok": True,
                "external_deployment_smoke_evidence_hash": "be" * 32,
                "packaging_smoke_hash": "bf" * 32,
                "descriptor_index_hash": "ca" * 32,
                "runtime_probe_hash": "cb" * 32,
                "local_operator_health_hash": "cc" * 32,
                "external_health_hash": "cd" * 32,
                "external_deployment_smoke_admission": {
                    "schema": "aegis-external-deployment-smoke-admission-v1",
                    "expected_capture_path": "artifacts/external_deployment_smoke_capture.json",
                },
                "external_deployment_smoke_admission_hash": [1],
                "external_deployment_smoke_admission_missing": True,
                "external_deployment_smoke_admission_blocker_id": "external_deployment_smoke_missing",
                "external_deployment_smoke_capture_missing_or_valid": True,
                "external_deployment_smoke_state_recorded": True,
                "container_attestation_state_recorded": True,
            }),
            patch("scripts.e2e_release_gate.build_deployment_manifest", return_value=FakeDeploymentManifest()),
        ):
            report = evaluate_e2e_release_gate(root)
        assert report["truth_claim"] is False
        assert report["overall_ok"] is True
        assert report["passed"] == 20
        assert report["digest_algorithm"] == "sha256-artifact-index"
        assert report["production_deployable"] is False
        assert report["production_gap_disclosed"] is True
        assert report["external_signed_attestation_present"] is False
        assert report["active_production_blocker_hash"] == "ee" * 32
        assert report["production_blocker_hash"] == "99" * 32
        assert [blocker["id"] for blocker in report["active_production_blockers"]] == [
            "external_signed_attestation_missing"
        ]
        assert len(report["report_digest"]) == 64

        (artifacts / "benchmark_gate_report.json").write_text(
            json.dumps({"checks": []}, sort_keys=True),
            encoding="utf-8",
        )
        with (
            patch("scripts.e2e_release_gate.evaluate_supply_chain_gate", return_value={"overall_ok": True}),
            patch("scripts.e2e_release_gate.evaluate_cluster_loopback_gate", return_value={"overall_ok": True}),
            patch("scripts.e2e_release_gate.evaluate_tcp_cluster_soak_gate", return_value={"overall_ok": True}),
            patch("scripts.e2e_release_gate.evaluate_quickjs_cold_start_gate", return_value={"overall_ok": True}),
            patch("scripts.e2e_release_gate.evaluate_hot_browser_shadow_gate", return_value={"overall_ok": True}),
            patch("scripts.e2e_release_gate.build_deployment_manifest", return_value=FakeDeploymentManifest()),
        ):
            tampered = evaluate_e2e_release_gate(root)
        assert tampered["overall_ok"] is False


def test_provider_route_gate_reports_selected_and_throttled_providers():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        benches = root / "core" / "rust" / "benches"
        src = root / "core" / "rust" / "src"
        cli = src / "cli"
        artifacts.mkdir(parents=True)
        benches.mkdir(parents=True)
        cli.mkdir(parents=True)
        (benches / "nerve_bench.rs").write_text(
            "fn bench_provider_route_admission_proof() { provider_route_admission_proof }",
            encoding="utf-8",
        )
        (src / "orchestrator.rs").write_text(
            "process_llm_request_with_budget_admission route_request_with_budget "
            "admission_proof.is_valid_for process_llm_request_with_budget_admission_replay",
            encoding="utf-8",
        )
        (cli / "mod.rs").write_text(
            "run_llm_with_budget_admission ProviderBudgetLedger ProviderRouteAdmissionProof "
            "run_llm_with_budget_admission_replay",
            encoding="utf-8",
        )
        (src / "replay.rs").write_text(
            "append_budget_admitted_llm_response_received llm_budget_admission_replay_binding_hash",
            encoding="utf-8",
        )
        (src / "tests.rs").write_text(
            "llm_runtime_budget_admission_uses_only_admitted_provider "
            "llm_runtime_budget_admission_fails_closed_before_execution "
            "llm_runtime_budget_admission_replay_records_proof_bound_response "
            "run_event_records_budget_admitted_llm_response_replay_binding",
            encoding="utf-8",
        )
        (artifacts / "constitution_audit_report.json").write_text(
            json.dumps(
                {"checks": [{"name": "inference_backend_contract", "ok": True}]},
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        report = evaluate_provider_route_gate(root)
        assert report["truth_claim"] is False
        assert report["overall_ok"] is True
        assert len(report["selected_provider_digest"]) == 64
        assert len(report["throttled_provider_digests"]) == 2

        (benches / "nerve_bench.rs").write_text("", encoding="utf-8")
        tampered = evaluate_provider_route_gate(root)
        assert tampered["overall_ok"] is False


def test_dependency_audit_gate_binds_required_rust_hotpath_dependencies():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        rust = root / "core" / "rust"
        src = rust / "src"
        src.mkdir(parents=True)
        (rust / "Cargo.toml").write_text(
            """
[dependencies]
arrow = "54"
arrow-buffer = "54"
blake3 = "1.5.0"
pyo3 = { version = "0.22", features = ["extension-module"] }
wasmtime = "22.0.0"
""",
            encoding="utf-8",
        )
        packages = "\n".join(
            f"""
[[package]]
name = "{name}"
version = "{version}"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "{idx:064x}"
"""
            for idx, (name, version) in enumerate(
                (
                    ("arrow", "54.3.1"),
                    ("arrow-buffer", "54.3.1"),
                    ("blake3", "1.5.0"),
                    ("pyo3", "0.22.6"),
                    ("wasmtime", "22.0.1"),
                ),
                start=1,
            )
        )
        (root / "Cargo.lock").write_text(f"version = 4\n{packages}", encoding="utf-8")
        (src / "sandbox.rs").write_text("Wasmtime fuel memory epoch", encoding="utf-8")

        report = evaluate_dependency_audit_gate(root)
        assert report["truth_claim"] is False
        assert report["overall_ok"] is True
        assert report["passed"] == 6
        assert all(record["record_digest"] for record in report["records"])

        (root / "Cargo.lock").write_text("version = 4\n", encoding="utf-8")
        tampered = evaluate_dependency_audit_gate(root)
        assert tampered["overall_ok"] is False


def test_aegis_adapter_dev_mode_runs_without_rust_extension():
    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
            result = Agent("inspect page", llm=lambda task: {"done": task}).run_sync()

    assert result.schema == "aegis-friendly-gateway-run-v1"
    assert result.truth_claim is False
    assert result.output == {"done": "inspect page"}
    assert result.hot_commit.schema == "aegis-hot-arena-commit-v1"
    assert result.hot_commit.verifier == "python-dev-fallback"
    assert result.hot_commit.admission == "degraded_warning"
    assert result.hot_commit.trust_level == "DEV"
    assert result.hot_commit.physical_witness_required is False
    assert result.hot_commit.fail_closed is False
    assert result.trust_policy.schema == "aegis-friendly-trust-policy-v1"
    assert result.trust_policy.trust_level == "DEV"
    assert result.trust_policy.degraded_hot_evidence_allowed is True
    assert result.trust_policy.missing_artifact_policy == "warn_continue"
    assert result.trust_policy.truth_claim is False
    assert len(result.trust_policy.trust_policy_hash) == 64
    assert len(result.hot_commit.artifact_hash) == 64
    assert len(result.hot_commit.storage_ref_hash) == 64


def test_aegis_adapter_langgraph_style_invoke_batch_preserves_evidence():
    calls: list[str] = []

    class RunnableLlm:
        model = "dev-runnable"

        def invoke(self, task, **kwargs):
            calls.append(task)
            return {"echo": task, "mode": kwargs.get("mode", "sync")}

    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
            agent = Agent(llm=RunnableLlm(), provider="local/dev")
            output = agent.invoke({"input": "inspect page"}, mode="sync")
            batch = agent.batch(
                [
                    {"messages": [{"role": "user", "content": "open tab"}]},
                    {"task": "summarize dom"},
                ],
                mode="batch",
            )

    assert output == {"echo": "inspect page", "mode": "sync"}
    assert batch == [
        {"echo": "open tab", "mode": "batch"},
        {"echo": "summarize dom", "mode": "batch"},
    ]
    assert calls == ["inspect page", "open tab", "summarize dom"]
    assert agent.last_result is not None
    assert agent.last_result.output == {"echo": "summarize dom", "mode": "batch"}
    assert agent.last_result.truth_claim is False
    assert agent.last_result.provider_route.selected_provider == "local/dev"
    assert agent.last_result.hot_commit.verifier == "python-dev-fallback"
    assert len(agent.last_result.hot_commit.artifact_hash) == 64
    assert tuple(result.task for result in agent.last_batch_results) == ("open tab", "summarize dom")
    assert len(agent.last_results) == 3
    assert all(result.truth_claim is False for result in agent.last_results)


def test_aegis_adapter_langgraph_style_ainvoke_accepts_message_input():
    async def async_llm(task, **kwargs):
        return {"async_echo": task, "tag": kwargs["tag"]}

    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
            agent = Agent(llm=async_llm)
            output = asyncio.run(
                agent.ainvoke(
                    {"messages": [{"role": "user", "content": "click login"}]},
                    tag="browser-use",
                )
            )

    assert output == {"async_echo": "click login", "tag": "browser-use"}
    assert agent.last_result is not None
    assert agent.last_result.task == "click login"
    assert agent.last_result.truth_claim is False
    assert agent.last_result.trust_policy.missing_artifact_policy == "warn_continue"
    assert len(agent.last_result.provider_route.route_hash) == 64


def test_aegis_adapter_trust_policy_snapshot_modes_are_hash_bound():
    dev = trust_policy_snapshot("DEV")
    staging = trust_policy_snapshot("STAGING")
    prod = trust_policy_snapshot("PROD")

    assert dev.degraded_hot_evidence_allowed is True
    assert dev.fail_closed is False
    assert dev.rust_extension_required is False
    assert dev.missing_artifact_policy == "warn_continue"
    assert staging.degraded_hot_evidence_allowed is False
    assert staging.fail_closed is False
    assert staging.rust_extension_required is True
    assert staging.missing_artifact_policy == "record_gap_without_truth_claim"
    assert prod.physical_witness_required is True
    assert prod.fail_closed is True
    assert prod.rust_extension_required is True
    assert prod.dual_approval_required is True
    assert prod.missing_artifact_policy == "fail_closed"
    assert len({dev.trust_policy_hash, staging.trust_policy_hash, prod.trust_policy_hash}) == 3
    assert all(len(item.trust_policy_hash) == 64 and item.truth_claim is False for item in (dev, staging, prod))


def test_aegis_adapter_prod_requires_rust_hot_engine_extension():
    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "PROD"}):
            with pytest.raises(RuntimeError, match="Rust aegis_nerve extension"):
                commit_hot_evidence(b"prod evidence")


def _write_dynamic_provider_fallback_fixture(root: Path) -> None:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    provider_set = "openrouter:gpt-4->nim:llama-3-70b"
    capture_contract = "external-http-429-capture-with-request-redaction-and-provider-response-hashes"
    admission = {
        "schema": "aegis-live-provider-429-soak-admission-v1",
        "expected_capture_path": "artifacts/live_provider_429_soak_capture.json",
        "provider_set": provider_set,
        "provider_set_hash": _sha256_array(provider_set),
        "capture_contract": capture_contract,
        "capture_contract_hash": _sha256_array(capture_contract),
        "required_sample_count": 32,
        "latency_gate_ns": 1_000_000,
        "expected_feedback_kind": "http_429",
        "primary_provider": "openrouter",
        "primary_model": "gpt-4",
        "fallback_provider": "nim",
        "fallback_model": "llama-3-70b",
        "production_blocker_id": "live_provider_429_soak_missing",
        "admission_hash": _sha256_array("live-provider-429-fixture-admission"),
    }
    report = {
        "schema": "aegis-dynamic-provider-fallback-report-v1",
        "sample_count": 32,
        "latency_gate_ns": 1_000_000,
        "feedback_kind": "http_429",
        "rate_limited_provider": "openrouter",
        "rate_limited_model": "gpt-4",
        "selected_provider": "nim",
        "selected_model": "llama-3-70b",
        "fallback_used": True,
        "downgraded_model": True,
        "previous_ledger_hash": _sha256_array("previous-ledger"),
        "updated_ledger_hash": _sha256_array("updated-ledger"),
        "feedback_hash": _sha256_array("feedback"),
        "admission_proof_hash": _sha256_array("admission-proof"),
        "fallback_proof_hash": _sha256_array("fallback-proof"),
        "throttled_provider_count": 1,
        "latency_min_ns": 25_000,
        "latency_max_ns": 125_000,
        "latency_total_ns": 1_000_000,
        "latency_under_gate": True,
        "all_samples_validated": True,
        "report_hash": _sha256_array("report"),
        "live_provider_traffic_present": False,
        "live_provider_429_soak_admission": admission,
    }
    (artifacts / "dynamic_provider_fallback_report.json").write_text(
        json.dumps({"schema_version": 1, "report": report}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _sha256_array(value: str) -> list[int]:
    return list(hashlib.sha256(value.encode("utf-8")).digest())


def _write_quickjs_cold_start_fixture(root: Path, *, write_runtime: bool) -> None:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    semantic_corpus = "aegis quickjs full interpreter semantic cold-start corpus v1"
    sandbox_contract = "wasmtime quickjs runtime deterministic stdout no network no host writes"
    admission = {
        "schema": "aegis-quickjs-full-interpreter-admission-v1",
        "expected_runtime_path": "artifacts/quickjs_full_interpreter.wasm",
        "semantic_corpus": semantic_corpus,
        "semantic_corpus_hash": _sha256_array(semantic_corpus),
        "sandbox_contract": sandbox_contract,
        "sandbox_contract_hash": _sha256_array(sandbox_contract),
        "required_cold_start_sample_count": 5,
        "required_fuel_limit": 10_000,
        "production_blocker_id": "full_quickjs_interpreter_cold_start_missing",
        "admission_hash": _sha256_array("quickjs-full-interpreter-admission-fixture"),
    }
    report = {
        "schema": "aegis-quickjs-cold-start-report-v1",
        "sample_count": 5,
        "fuel_limit": 10_000,
        "script_bytes": 64,
        "abi_packet_bytes": 168,
        "wasm_module_bytes": 4096,
        "cold_start_min_ns": 1000,
        "cold_start_max_ns": 2000,
        "cold_start_total_ns": 7500,
        "warm_cached_min_ns": 100,
        "warm_cached_max_ns": 500,
        "warm_cached_total_ns": 1500,
        "cold_cache_entries_after_first": 1,
        "warm_cache_entries_before": 1,
        "warm_cache_entries_after": 1,
        "cold_fuel_consumed_total": 500,
        "warm_fuel_consumed_total": 250,
        "cold_artifact_hash": _sha256_array("cold-artifact"),
        "warm_artifact_hash": _sha256_array("warm-artifact"),
        "script_blake3": _sha256_array("script"),
        "wrapper_blake3": _sha256_array("wrapper"),
        "invocation_blake3": _sha256_array("invocation"),
        "report_hash": _sha256_array("quickjs-report"),
        "bridge_probe_executed": True,
        "full_quickjs_interpreter_present": False,
        "full_interpreter_admission": admission,
    }
    (artifacts / "quickjs_cold_start_report.json").write_text(
        json.dumps({"schema_version": 1, "report": report}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if write_runtime:
        (artifacts / "quickjs_full_interpreter.wasm").write_bytes(b"\0asm\x01\0\0\0quickjs-fixture")


def _write_quickjs_fixture_runner(root: Path) -> Path:
    runner_path = root / "quickjs_fixture_runner.py"
    case_map = {case["script"]: case["expected_stdout"] for case in SEMANTIC_CORPUS_CASES}
    runner_path.write_text(
        "\n".join(
            (
                "import json",
                "import pathlib",
                "import sys",
                f"CASES = {json.dumps(case_map, sort_keys=True)!r}",
                "script = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')",
                "print(json.loads(CASES)[script])",
            )
        ),
        encoding="utf-8",
    )
    return runner_path


def test_quickjs_full_interpreter_capture_producer_writes_semantic_capture():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_quickjs_cold_start_fixture(root, write_runtime=True)
        runner_path = _write_quickjs_fixture_runner(root)

        producer_report = write_full_quickjs_interpreter_cold_start_capture(
            root,
            runner=[sys.executable, str(runner_path), "{script}"],
        )
        gate = evaluate_quickjs_cold_start_gate(root)
        capture_path = root / "artifacts" / "quickjs_full_interpreter_cold_start_capture.json"
        capture = json.loads(capture_path.read_text(encoding="utf-8"))

        assert producer_report["overall_ok"] is True
        assert producer_report["capture_written"] is True
        assert producer_report["runtime_wasm_present"] is True
        assert producer_report["runner_config_present"] is True
        assert gate["overall_ok"] is True
        assert gate["real_quickjs_interpreter_cold_start_present"] is True
        assert gate["production_quickjs_release_blocked_without_full_interpreter"] is False
        assert gate["full_interpreter_admission"]["admission_status"] == (
            "candidate-present-semantic-cold-start-gate-required"
        )
        assert capture["schema"] == "aegis-quickjs-full-interpreter-cold-start-capture-v1"
        assert capture["truth_claim"] is False
        assert capture["sample_count"] == 5
        assert capture["semantic_case_count"] == len(SEMANTIC_CORPUS_CASES)
        assert capture["expected_run_count"] == 5 * len(SEMANTIC_CORPUS_CASES)
        assert capture["valid_run_count"] == capture["expected_run_count"]
        assert capture["all_semantic_runs_valid"] is True
        assert capture["probe"]["runner_config_source"] == "injected"


def test_quickjs_full_interpreter_capture_missing_runtime_preserves_blocker():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_quickjs_cold_start_fixture(root, write_runtime=False)

        producer_report = write_full_quickjs_interpreter_cold_start_capture(root)
        gate = evaluate_quickjs_cold_start_gate(root)
        capture_path = root / "artifacts" / "quickjs_full_interpreter_cold_start_capture.json"

        assert producer_report["overall_ok"] is False
        assert producer_report["capture_written"] is False
        assert producer_report["runtime_wasm_present"] is False
        assert producer_report["runner_config_present"] is False
        assert capture_path.exists() is False
        assert gate["overall_ok"] is True
        assert gate["real_quickjs_interpreter_cold_start_present"] is False
        assert gate["production_quickjs_release_blocked_without_full_interpreter"] is True
        assert gate["full_interpreter_admission_missing"] is True
        assert gate["full_interpreter_admission_blocker_id"] == "full_quickjs_interpreter_cold_start_missing"


def _write_tcp_cluster_soak_fixture(root: Path) -> dict:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    network_contract = "external-multi-machine-mtls-tcp-soak-with-replay-hash-chain-and-rtt-evidence"
    topology_contract = "single-writer-core-plus-remote-worker-pool-across-distinct-machines"
    admission = {
        "schema": "aegis-real-multi-machine-cluster-soak-admission-v1",
        "expected_capture_path": "artifacts/real_multi_machine_cluster_soak_capture.json",
        "network_contract": network_contract,
        "network_contract_hash": _sha256_array(network_contract),
        "topology_contract": topology_contract,
        "topology_contract_hash": _sha256_array(topology_contract),
        "required_worker_count": 3,
        "required_work_item_count": 24,
        "required_round_trip_count": 24,
        "local_loopback_worker_count": 3,
        "local_loopback_work_item_count": 24,
        "local_loopback_round_trip_count": 24,
        "production_blocker_id": "real_multi_machine_cluster_soak_missing",
        "admission_hash": _sha256_array("real-multi-machine-cluster-admission-fixture"),
    }
    report = {
        "schema": "aegis-tcp-cluster-soak-report-v1",
        "worker_count": 3,
        "work_item_count": 24,
        "accepted_count": 24,
        "duplicate_count": 1,
        "replay_event_count": 25,
        "replay_hash_chain_valid": True,
        "tcp_listener_bound": True,
        "tcp_worker_connections": 3,
        "tcp_round_trip_count": 24,
        "tcp_round_trip_min_ns": 1000,
        "tcp_round_trip_max_ns": 3000,
        "tcp_round_trip_total_ns": 48_000,
        "tcp_payload_bytes_sent": 24 * (1 + 228),
        "tcp_payload_bytes_received": 24 * 156,
        "worker_response_count": 24,
        "direct_worker_commit_rejected": True,
        "side_effect_partition_paused": True,
        "network_trace_hash": _sha256_array("network-trace"),
        "candidate_result_hash": _sha256_array("candidate-result"),
        "replay_last_hash": _sha256_array("replay-last"),
        "report_hash": _sha256_array("tcp-cluster-report"),
        "loopback_tcp_only": True,
        "multi_machine_real_cluster": False,
        "real_multi_machine_cluster_admission": admission,
    }
    (artifacts / "tcp_cluster_soak_report.json").write_text(
        json.dumps({"schema_version": 1, "report": report}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def _real_multi_machine_probe_fixture(report: dict) -> dict:
    admission = report["real_multi_machine_cluster_admission"]
    endpoints = [
        {"machine_id": "worker-a", "host": "10.1.0.11", "port": 41001},
        {"machine_id": "worker-b", "host": "10.1.0.12", "port": 41002},
        {"machine_id": "worker-c", "host": "10.1.0.13", "port": 41003},
    ]
    round_trips = []
    for index in range(report["tcp_round_trip_count"]):
        endpoint = endpoints[index % len(endpoints)]
        response = {
            "schema": "aegis-real-cluster-worker-response-v1",
            "truth_claim": False,
            "candidate_only": True,
            "direct_worker_commit_rejected": True,
            "side_effect_partition_paused": True,
            "network_contract_hash": admission["network_contract_hash"],
            "topology_contract_hash": admission["topology_contract_hash"],
            "worker_machine_id_hash": hashlib.sha256(endpoint["machine_id"].encode("utf-8")).hexdigest(),
        }
        encoded_response = json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8")
        round_trips.append(
            {
                "index": index,
                "endpoint_hash": hashlib.sha256(json.dumps(endpoint, sort_keys=True).encode("utf-8")).hexdigest(),
                "machine_id_hash": hashlib.sha256(endpoint["machine_id"].encode("utf-8")).hexdigest(),
                "host_hash": hashlib.sha256(endpoint["host"].encode("utf-8")).hexdigest(),
                "port": endpoint["port"],
                "elapsed_ns": 2000 + index,
                "request_hash": hashlib.sha256(f"request-{index}".encode("utf-8")).hexdigest(),
                "response_len": len(encoded_response),
                "response_hash": hashlib.sha256(encoded_response).hexdigest(),
                "response": response,
                "error": "",
            }
        )
    return {
        "schema": "aegis-real-multi-machine-cluster-probe-v1",
        "endpoint_config_present": True,
        "requested_round_trip_count": report["tcp_round_trip_count"],
        "timeout_seconds": 5,
        "source_host_hash": hashlib.sha256(b"source-host").hexdigest(),
        "endpoints": endpoints,
        "round_trips": round_trips,
        "error": "",
    }


def test_real_multi_machine_cluster_capture_producer_writes_valid_candidate_capture():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = _write_tcp_cluster_soak_fixture(root)
        probe = _real_multi_machine_probe_fixture(report)

        producer_report = write_real_multi_machine_cluster_soak_capture(root, probe_override=probe)
        gate = evaluate_tcp_cluster_soak_gate(root)
        capture_path = root / "artifacts" / "real_multi_machine_cluster_soak_capture.json"
        capture = json.loads(capture_path.read_text(encoding="utf-8"))

        assert producer_report["overall_ok"] is True
        assert producer_report["capture_written"] is True
        assert producer_report["endpoint_config_present"] is True
        assert producer_report["worker_count"] == 3
        assert producer_report["round_trip_count"] == 24
        assert producer_report["distinct_machine_count"] == 3
        assert gate["overall_ok"] is True
        assert gate["real_multi_node_cluster_test_present"] is True
        assert gate["production_cluster_release_blocked_without_real_multi_node_soak"] is False
        assert gate["real_multi_machine_cluster_admission"]["admission_status"] == (
            "candidate-present-real-multi-machine-soak-gate-required"
        )
        assert capture["schema"] == "aegis-real-multi-machine-cluster-soak-capture-v1"
        assert capture["truth_claim"] is False
        assert capture["worker_count"] == 3
        assert capture["distinct_machine_count"] == 3
        assert capture["non_loopback_hosts"] is True
        assert capture["accepted_response_count"] == 24
        assert capture["all_round_trips_valid"] is True
        assert capture["response_bodies_redacted"] is True


def test_real_multi_machine_cluster_capture_missing_endpoints_preserves_blocker():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_tcp_cluster_soak_fixture(root)

        with patch.dict(os.environ, {"AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON": ""}, clear=False):
            producer_report = write_real_multi_machine_cluster_soak_capture(root)
        gate = evaluate_tcp_cluster_soak_gate(root)
        capture_path = root / "artifacts" / "real_multi_machine_cluster_soak_capture.json"

        assert producer_report["overall_ok"] is False
        assert producer_report["capture_written"] is False
        assert producer_report["endpoint_config_present"] is False
        assert "AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON unset" in producer_report["error"]
        assert capture_path.exists() is False
        assert gate["overall_ok"] is True
        assert gate["real_multi_node_cluster_test_present"] is False
        assert gate["production_cluster_release_blocked_without_real_multi_node_soak"] is True
        assert gate["real_multi_machine_cluster_admission_missing"] is True
        assert gate["real_multi_machine_cluster_admission_blocker_id"] == "real_multi_machine_cluster_soak_missing"


def _write_external_deployment_smoke_fixture(root: Path) -> None:
    artifacts = root / "artifacts"
    deploy = root / "deploy"
    artifacts.mkdir(parents=True, exist_ok=True)
    deploy.mkdir(parents=True, exist_ok=True)
    (artifacts / "production_packaging_smoke_gate_report.json").write_text(
        json.dumps(
            {
                "schema": "aegis-production-packaging-smoke-gate-report-v1",
                "truth_claim": False,
                "overall_ok": True,
                "production_packaging_smoke_present": True,
                "smoke_evidence_hash": hashlib.sha256(b"packaging-smoke").hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text(
        "\n".join(
            (
                "ENV AEGIS_OPERATOR_ROOT=/var/lib/aegis",
                "ENV AEGIS_ARTIFACTS_DIR=/var/lib/aegis/artifacts",
                'CMD ["python", "-m", "core.python.operator_api_healthcheck", "--root", "/var/lib/aegis"]',
            )
        ),
        encoding="utf-8",
    )
    (deploy / "docker-compose.yml").write_text(
        "\n".join(
            (
                "services:",
                "  aegis:",
                "    environment:",
                "      AEGIS_OPERATOR_ROOT: /var/lib/aegis",
                "      AEGIS_ARTIFACTS_DIR: /var/lib/aegis/artifacts",
                '    command: ["python", "-m", "core.python.operator_api_healthcheck", "--root", "/var/lib/aegis"]',
                "    volumes:",
                "      - evidence-artifacts:/var/lib/aegis/artifacts:ro",
            )
        ),
        encoding="utf-8",
    )
    (deploy / "kubernetes.yaml").write_text(
        "\n".join(
            (
                "apiVersion: apps/v1",
                "kind: Deployment",
                "metadata:",
                "  name: aegis",
                "spec:",
                "  template:",
                "    spec:",
                "      volumes:",
                "        - name: aegis-evidence-artifacts",
                "          emptyDir: {}",
                "      containers:",
                "        - name: aegis",
                "          env:",
                "            - name: AEGIS_OPERATOR_ROOT",
                "              value: /var/lib/aegis",
                "            - name: AEGIS_ARTIFACTS_DIR",
                "              value: /var/lib/aegis/artifacts",
                '          args: ["--root", "/var/lib/aegis"]',
            )
        ),
        encoding="utf-8",
    )


def test_external_deployment_smoke_capture_producer_writes_valid_health_capture():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_external_deployment_smoke_fixture(root)
        requested_urls: list[str] = []

        def requester(request, timeout_seconds):
            requested_urls.append(request.full_url)
            assert timeout_seconds == 10
            payload = {
                "schema": "aegis-operator-health-v1",
                "truth_claim": False,
                "release_gate_observed_ok": True,
                "missing_artifact_count": 0,
                "artifact_count": 1,
                "snapshot_digest": {"algorithm": "sha256", "hex": hashlib.sha256(b"snapshot").hexdigest()},
            }
            return {"status": 200, "body": json.dumps(payload, sort_keys=True).encode("utf-8")}

        with patch.dict(
            os.environ,
            {"AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL": "https://aegis.example.test"},
            clear=False,
        ):
            producer_report = write_external_deployment_smoke_capture(root, requester=requester)
            gate = evaluate_external_deployment_smoke_gate(root, requester=requester)

        capture_path = root / "artifacts" / "external_deployment_smoke_capture.json"
        capture = json.loads(capture_path.read_text(encoding="utf-8"))

        assert producer_report["overall_ok"] is True
        assert producer_report["capture_written"] is True
        assert requested_urls == [
            "https://aegis.example.test/health",
            "https://aegis.example.test/health",
        ]
        assert gate["overall_ok"] is True
        assert gate["external_deployment_smoke_present"] is True
        assert gate["production_release_blocked_without_external_deployment_smoke"] is False
        assert gate["external_deployment_smoke_admission"]["admission_status"] == (
            "candidate-present-external-deployment-smoke-gate-required"
        )
        assert capture["schema"] == "aegis-external-deployment-smoke-capture-v1"
        assert capture["truth_claim"] is False
        assert capture["external_url"] == "https://aegis.example.test"
        assert capture["external_health"]["overall_ok"] is True
        assert capture["external_health"]["status"] == 200
        assert capture["external_health"]["health_schema"] == "aegis-operator-health-v1"
        assert capture["external_health"]["truth_claim"] is False


def test_external_deployment_smoke_capture_missing_url_preserves_blocker():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_external_deployment_smoke_fixture(root)

        with patch.dict(os.environ, {"AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL": ""}, clear=False):
            producer_report = write_external_deployment_smoke_capture(root)
            gate = evaluate_external_deployment_smoke_gate(root)

        capture_path = root / "artifacts" / "external_deployment_smoke_capture.json"

        assert producer_report["overall_ok"] is False
        assert producer_report["capture_written"] is False
        assert producer_report["external_url_present"] is False
        assert capture_path.exists() is False
        assert gate["overall_ok"] is True
        assert gate["external_deployment_smoke_present"] is False
        assert gate["production_release_blocked_without_external_deployment_smoke"] is True
        assert gate["external_deployment_smoke_admission_missing"] is True


def test_aegis_adapter_dev_provider_fallback_records_route_evidence():
    calls = []

    def throttled(task):
        calls.append(("openrouter/gpt-4", task))
        raise ProviderRateLimitError("HTTP 429")

    def reserve(task):
        calls.append(("nim/llama-3-70b", task))
        return {"provider": "reserve", "task": task}

    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
            result = Agent(
                "inspect page",
                llm=throttled,
                provider="openrouter/gpt-4",
                fallback_providers=[("nim/llama-3-70b", reserve)],
            ).run_sync()

    assert calls == [
        ("openrouter/gpt-4", "inspect page"),
        ("nim/llama-3-70b", "inspect page"),
    ]
    assert result.output == {"provider": "reserve", "task": "inspect page"}
    assert result.provider == "nim/llama-3-70b"
    assert result.provider_route.schema == "aegis-friendly-provider-route-v1"
    assert result.provider_route.selected_provider == "nim/llama-3-70b"
    assert result.provider_route.fallback_used is True
    assert result.provider_route.downgraded_model is True
    assert result.provider_route.throttled_provider_count == 1
    assert result.provider_route.attempted_providers == ("openrouter/gpt-4", "nim/llama-3-70b")
    assert result.provider_route.truth_claim is False
    assert len(result.provider_route.route_hash) == 64
    assert result.hot_commit.trust_level == "DEV"


def test_dynamic_provider_live_429_capture_producer_writes_redacted_valid_capture():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_dynamic_provider_fallback_fixture(root)
        requested_indexes: list[int] = []

        def requester(request, timeout_seconds, index):
            requested_indexes.append(index)
            assert timeout_seconds == 10
            assert request.full_url == "https://provider.example.test/always-429"
            assert request.get_method() == "POST"
            assert request.headers["Authorization"] == "Bearer secret-token"
            return {
                "status": 429,
                "body": f"rate limited {index}".encode("utf-8"),
                "headers": {"Retry-After": "1", "X-Request-Id": f"req-{index}"},
            }

        with patch.dict(
            os.environ,
            {
                "AEGIS_LIVE_PROVIDER_429_SOAK_URL": "https://provider.example.test/always-429",
                "AEGIS_LIVE_PROVIDER_429_SOAK_METHOD": "POST",
                "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON": '{"Authorization":"Bearer secret-token"}',
            },
            clear=False,
        ):
            producer_report = write_live_provider_429_soak_capture(root, requester=requester)

        gate = evaluate_dynamic_provider_fallback_gate(root)
        capture_path = root / "artifacts" / "live_provider_429_soak_capture.json"
        capture = json.loads(capture_path.read_text(encoding="utf-8"))

        assert producer_report["overall_ok"] is True
        assert producer_report["capture_written"] is True
        assert requested_indexes == list(range(32))
        assert gate["overall_ok"] is True
        assert gate["live_provider_traffic_present"] is True
        assert gate["production_provider_release_blocked_without_live_429_soak"] is False
        assert gate["live_provider_429_soak_admission"]["admission_status"] == (
            "candidate-present-live-soak-gate-required"
        )
        assert capture["schema"] == "aegis-live-provider-429-soak-capture-v1"
        assert capture["truth_claim"] is False
        assert capture["request_redacted"] is True
        assert capture["response_bodies_redacted"] is True
        assert capture["status_counts"] == {"429": 32}
        assert capture["sample_count"] == 32
        assert capture["all_status_429"] is True
        assert capture["http_samples"]["request_header_names"] == ["authorization"]
        assert "secret-token" not in json.dumps(capture, sort_keys=True)


def test_dynamic_provider_live_429_capture_missing_url_preserves_blocker():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_dynamic_provider_fallback_fixture(root)

        with patch.dict(
            os.environ,
            {
                "AEGIS_LIVE_PROVIDER_429_SOAK_URL": "",
                "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON": "",
            },
            clear=False,
        ):
            producer_report = write_live_provider_429_soak_capture(root)

        gate = evaluate_dynamic_provider_fallback_gate(root)
        capture_path = root / "artifacts" / "live_provider_429_soak_capture.json"

        assert producer_report["overall_ok"] is False
        assert producer_report["capture_written"] is False
        assert producer_report["soak_url_present"] is False
        assert "AEGIS_LIVE_PROVIDER_429_SOAK_URL unset" in producer_report["error"]
        assert capture_path.exists() is False
        assert gate["overall_ok"] is True
        assert gate["live_provider_traffic_present"] is False
        assert gate["production_provider_release_blocked_without_live_429_soak"] is True
        assert gate["live_provider_429_soak_admission_missing"] is True


def test_aegis_adapter_provider_budget_skips_exhausted_primary_and_hashes_evidence():
    calls = []

    def exhausted_primary(task):
        calls.append(("openrouter/gpt-4", task))
        raise AssertionError("budget-blocked provider must not be invoked")

    def reserve(task):
        calls.append(("nim/llama-3-70b", task))
        return {"provider": "reserve", "task": task}

    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
            result = Agent(
                "inspect page",
                llm=exhausted_primary,
                provider="openrouter/gpt-4",
                fallback_providers=[("nim/llama-3-70b", reserve)],
                required_tokens=1024,
                provider_budgets={
                    "openrouter/gpt-4": {
                        "remaining_requests": 0,
                        "remaining_tokens": 32000,
                        "reset_epoch_ms": 1000000,
                    },
                    "nim/llama-3-70b": {
                        "remaining_requests": 4,
                        "remaining_tokens": 32000,
                        "reset_epoch_ms": 1000000,
                    },
                },
            ).run_sync()

    budgets = {record.provider: record for record in result.provider_budget.budgets}
    assert calls == [("nim/llama-3-70b", "inspect page")]
    assert result.output == {"provider": "reserve", "task": "inspect page"}
    assert result.provider == "nim/llama-3-70b"
    assert result.provider_budget.schema == "aegis-friendly-provider-budget-evidence-v1"
    assert result.provider_budget.truth_claim is False
    assert result.provider_budget.budgeted is True
    assert result.provider_budget.required_tokens == 1024
    assert result.provider_budget.provider_count == 2
    assert result.provider_budget.admitted_provider_count == 1
    assert result.provider_budget.skipped_providers == ("openrouter/gpt-4",)
    assert result.provider_budget.selected_provider == "nim/llama-3-70b"
    assert result.provider_budget.feedback_kind == "none"
    assert len(result.provider_budget.budget_ledger_hash) == 64
    assert len(result.provider_budget.budget_evidence_hash) == 64
    assert budgets["openrouter/gpt-4"].admitted is False
    assert budgets["openrouter/gpt-4"].rejection_reason == "remaining_requests_exhausted"
    assert budgets["nim/llama-3-70b"].admitted is True
    assert budgets["nim/llama-3-70b"].rejection_reason == ""
    assert all(len(record.budget_hash) == 64 and record.truth_claim is False for record in budgets.values())
    assert result.provider_route.provider_budget_hash == result.provider_budget.budget_evidence_hash
    assert result.provider_route.attempted_providers == ("nim/llama-3-70b",)
    assert result.provider_route.fallback_used is True
    assert result.provider_route.throttled_provider_count == 0
    assert result.hot_commit.handle_valid is True


def test_aegis_adapter_prod_all_providers_throttled_fails_before_hot_commit():
    def throttled(task):
        raise ProviderRateLimitError("HTTP 429")

    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "PROD"}):
            with pytest.raises(ProviderRateLimitError, match="all providers throttled"):
                Agent(
                    "inspect page",
                    llm=throttled,
                    provider="openrouter/gpt-4",
                    fallback_providers=[("nim/llama-3-70b", throttled)],
                ).run_sync()


def test_aegis_adapter_dev_browser_action_capture_commits_hot_evidence_before_cold_publish():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()
        output_root = Path(tmp) / "browser-cold"
        metadata_seen_during_hot_commit: list[bool] = []
        seen_payloads: list[bytes] = []

        def publisher(payload: bytes) -> dict[str, object]:
            metadata_seen_during_hot_commit.extend(output_root.rglob("producer-metadata.json"))
            seen_payloads.append(payload)
            digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
            return {
                "byte_len": len(payload),
                "artifact_hash": digest,
                "storage_ref_hash": hashlib.blake2b(
                    b"storage" + digest.encode("ascii"),
                    digest_size=32,
                ).hexdigest(),
                "trust_level": "DEV",
                "admission": "degraded_warning",
                "verifier": "python-dev-fallback",
                "handle_valid": True,
            }

        with patch.dict("sys.modules", {"aegis_nerve": None}):
            with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
                result = Agent("inspect page").capture_browser_action_sync(
                    browser_session=session,
                    action=session.mutate_after_action,
                    output_root=output_root,
                    run_id=101,
                    action_id=202,
                    sequence_number=303,
                    hot_evidence_publisher=publisher,
                )

        assert result.schema == "aegis-friendly-browser-action-result-v1"
        assert result.truth_claim is False
        assert result.trust_level == "DEV"
        assert result.trust_policy.missing_artifact_policy == "warn_continue"
        assert result.no_file_roundtrip_on_hot_path is True
        assert len(result.browser_action_result_hash) == 64
        assert result.capture.action_result == "clicked"
        assert result.hot_evidence.schema == "aegis-hot-browser-evidence-batch-v1"
        assert result.hot_evidence.truth_claim is False
        assert result.hot_evidence.artifact_count == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert len(result.hot_evidence.commits) == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert len(seen_payloads) == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert metadata_seen_during_hot_commit == []
        assert result.capture.producer_run.metadata_path.exists()
        assert result.hot_evidence.total_bytes == sum(len(payload) for payload in seen_payloads)
        assert all(commit.trust_level == "DEV" for commit in result.hot_evidence.commits)
        assert all(commit.handle_valid for commit in result.hot_evidence.commits)


def test_aegis_adapter_default_browser_action_uses_batch_hot_evidence_without_rust_extension():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()

        with patch.dict("sys.modules", {"aegis_nerve": None}):
            with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
                result = Agent("inspect page").capture_browser_action_sync(
                    browser_session=session,
                    action=session.mutate_after_action,
                    output_root=Path(tmp),
                    run_id=707,
                    action_id=808,
                    sequence_number=909,
                )

        assert result.schema == "aegis-friendly-browser-action-result-v1"
        assert result.hot_evidence.schema == "aegis-hot-browser-evidence-batch-v1"
        assert result.hot_evidence.verifier == "python-dev-batch-fallback"
        assert result.hot_evidence.artifact_count == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert result.hot_evidence.no_file_roundtrip_on_hot_path is True
        assert result.hot_evidence.total_bytes == sum(
            commit.byte_len for commit in result.hot_evidence.commits
        )
        assert all(commit.verifier == "python-dev-batch-fallback" for commit in result.hot_evidence.commits)
        assert all(commit.handle_valid for commit in result.hot_evidence.commits)
        assert len(result.hot_evidence.batch_digest) == 64
        assert result.capture.producer_run.metadata_path.exists()


def test_aegis_adapter_browser_action_accepts_session_bound_action():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()

        with patch.dict("sys.modules", {"aegis_nerve": None}):
            result = Agent(
                "inspect page",
                trust_level="DEV",
            ).capture_browser_action_sync(
                browser_session=session,
                action=lambda browser: browser.mutate_after_action(),
                output_root=Path(tmp),
                run_id=404,
                action_id=505,
                sequence_number=606,
            )

        assert result.schema == "aegis-friendly-browser-action-result-v1"
        assert result.capture.action_result == "clicked"
        assert result.capture.after.url == "https://example.test/after"
        assert result.hot_evidence.run_id == 404
        assert result.hot_evidence.action_id == 505
        assert result.hot_evidence.sequence_number == 606
        assert len(result.browser_action_result_hash) == 64
        assert result.hot_evidence.no_file_roundtrip_on_hot_path is True


def test_aegis_adapter_hot_first_browser_action_returns_hot_evidence_before_cold_publish():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()
        output_root = Path(tmp) / "browser-cold"
        publish_entered = threading.Event()
        publish_release = threading.Event()
        producer = _BlockingBrowserLiveCollectorProducer(
            output_root,
            publish_entered,
            publish_release,
        )

        with patch.dict("sys.modules", {"aegis_nerve": None}):
            with patch.dict(os.environ, {"AEGIS_TRUST_LEVEL": "DEV"}):
                result = Agent("inspect page").capture_browser_action_hot_first_sync(
                    browser_session=session,
                    action=lambda browser: browser.mutate_after_action(),
                    producer=producer,
                    run_id=111,
                    action_id=222,
                    sequence_number=333,
                )

        assert result.schema == "aegis-friendly-browser-hot-first-action-result-v1"
        assert result.truth_claim is False
        assert result.trust_level == "DEV"
        assert result.trust_policy.missing_artifact_policy == "warn_continue"
        assert result.no_file_roundtrip_on_hot_path is True
        assert result.hot_returned_before_cold_publish is True
        assert result.capture.action_result == "clicked"
        assert result.hot_evidence.schema == "aegis-hot-browser-evidence-batch-v1"
        assert result.hot_evidence.verifier == "python-dev-batch-fallback"
        assert result.hot_evidence.artifact_count == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert result.hot_evidence.no_file_roundtrip_on_hot_path is True
        assert result.capture.cold_publish.schema == "aegis-browser-runtime-cold-publish-handle-v1"
        assert result.capture.cold_publish.background_publish_started is True
        assert result.capture.cold_publish.hot_returned_before_cold_publish is True
        assert len(result.capture.cold_publish.cold_publish_handle_hash) == 64
        assert len(result.browser_action_result_hash) == 64
        assert publish_entered.wait(timeout=1)
        assert result.capture.cold_publish.done() is False
        assert list(output_root.rglob("producer-metadata.json")) == []

        publish_release.set()
        producer_run = result.capture.cold_publish.wait(timeout=2)

        assert producer_run.metadata_path.exists()
        assert result.capture.cold_publish.done() is True
        assert producer_run.run_id == 111
        assert producer_run.action_id == 222
        assert producer_run.sequence_number == 333
        assert producer_run.artifact_paths["url_after"].read_text() == "https://example.test/after"


def test_operator_evidence_snapshot_digest_changes_after_artifact_tamper():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        for name in ("run_checks_report.json", "benchmark_gate_report.json", "constitution_audit_report.json"):
            (artifacts / name).write_text(
                json.dumps({"overall_ok": True, "checks": [], "report_hash": "cd" * 32}, sort_keys=True),
                encoding="utf-8",
            )

        artifact_names = (
            "run_checks_report.json",
            "benchmark_gate_report.json",
            "constitution_audit_report.json",
        )
        before = build_operator_evidence_snapshot(root, artifact_names)
        (artifacts / "run_checks_report.json").write_text(
            json.dumps({"overall_ok": False, "checks": [], "report_hash": "ef" * 32}, sort_keys=True),
            encoding="utf-8",
        )
        after = build_operator_evidence_snapshot(root, artifact_names)

        assert before.snapshot_digest.hex != after.snapshot_digest.hex
        assert not after.release_gate_observed_ok


def test_operator_evidence_api_serves_read_only_health_and_snapshot():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        artifact_names = (
            "run_checks_report.json",
            "benchmark_gate_report.json",
            "constitution_audit_report.json",
        )
        for name in artifact_names:
            (artifacts / name).write_text(
                json.dumps({"overall_ok": True, "passed": 1, "failed": 0, "checks": []}, sort_keys=True),
                encoding="utf-8",
            )

        api = OperatorEvidenceApi(root, artifact_names)
        health = api.handle_get("/health").json()
        snapshot = api.handle_get("/snapshot").json()
        missing = api.handle_get("/nope").json()

        assert health["schema"] == "aegis-operator-health-v1"
        assert health["truth_claim"] is False
        assert health["release_gate_observed_ok"] is True
        assert snapshot["schema"] == "aegis-operator-evidence-snapshot-v1"
        assert snapshot["truth_claim"] is False
        assert missing["error"] == "unknown read-only operator endpoint"


def test_operator_evidence_api_serves_production_closure_workflow_summary():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        closure_payload = {
            "schema": "aegis-production-closure-workflow-v1",
            "truth_claim": False,
            "overall_ok": True,
            "workflow_hash": "ab" * 32,
            "execute": False,
            "active_blocker_count": 2,
            "ready_to_execute_count": 1,
            "blocked_preflight_count": 1,
            "closed_blocker_count": 0,
            "steps": [
                {
                    "blocker_id": "live_provider_429_soak_missing",
                    "ready_to_execute": True,
                    "missing_required_env": [],
                    "missing_required_artifacts": [],
                    "missing_expected_artifacts": ["artifacts/live_provider_429_soak_capture.json"],
                },
                {
                    "blocker_id": "external_deployment_smoke_missing",
                    "ready_to_execute": False,
                    "missing_required_env": ["AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL"],
                    "missing_required_artifacts": [],
                    "missing_expected_artifacts": ["artifacts/external_deployment_smoke_capture.json"],
                },
            ],
        }
        (artifacts / "production_closure_workflow_report.json").write_text(
            json.dumps(closure_payload, sort_keys=True),
            encoding="utf-8",
        )

        api = OperatorEvidenceApi(root, ("production_closure_workflow_report.json",))
        response = api.handle_get("/production-closure")
        payload = response.json()

        assert response.status == 200
        assert payload["schema"] == "aegis-operator-production-closure-v1"
        assert payload["truth_claim"] is False
        assert payload["workflow_hash"] == "ab" * 32
        assert payload["active_blocker_count"] == 2
        assert payload["ready_to_execute_count"] == 1
        assert payload["blocked_preflight_count"] == 1
        assert payload["closed_blocker_count"] == 0
        assert payload["ready_blocker_ids"] == ["live_provider_429_soak_missing"]
        assert payload["blocked_blocker_ids"] == ["external_deployment_smoke_missing"]
        assert payload["artifact_digest"]["algorithm"] == "blake3"


def test_operator_evidence_api_production_closure_fails_closed_when_artifact_missing():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "artifacts").mkdir()

        api = OperatorEvidenceApi(root, ("production_closure_workflow_report.json",))
        response = api.handle_get("/production-closure")
        payload = response.json()

        assert response.status == 503
        assert payload["schema"] == "aegis-operator-production-closure-v1"
        assert payload["truth_claim"] is False
        assert payload["overall_ok"] is False
        assert payload["error"] == "missing production closure workflow artifact"


def test_operator_evidence_http_server_serves_production_closure_route():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        (artifacts / "production_closure_workflow_report.json").write_text(
            json.dumps(
                {
                    "schema": "aegis-production-closure-workflow-v1",
                    "truth_claim": False,
                    "overall_ok": True,
                    "workflow_hash": "cd" * 32,
                    "execute": False,
                    "active_blocker_count": 1,
                    "ready_to_execute_count": 0,
                    "blocked_preflight_count": 1,
                    "closed_blocker_count": 0,
                    "steps": [
                        {
                            "blocker_id": "external_deployment_smoke_missing",
                            "ready_to_execute": False,
                            "missing_required_env": ["AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL"],
                            "missing_required_artifacts": [],
                            "missing_expected_artifacts": ["artifacts/external_deployment_smoke_capture.json"],
                        }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        server = serve_operator_evidence_api(root, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            with urlopen(f"http://{host}:{port}/production-closure", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                status = response.status
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        assert status == 200
        assert payload["schema"] == "aegis-operator-production-closure-v1"
        assert payload["truth_claim"] is False
        assert payload["workflow_hash"] == "cd" * 32
        assert payload["blocked_blocker_ids"] == ["external_deployment_smoke_missing"]


def test_operator_evidence_snapshot_fail_closed_for_missing_artifact():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "artifacts").mkdir()
        snapshot = build_operator_evidence_snapshot(
            root,
            (
                "run_checks_report.json",
                "benchmark_gate_report.json",
                "constitution_audit_report.json",
            ),
        )

        assert not snapshot.release_gate_observed_ok
        assert snapshot.missing_artifacts == (
            "run_checks_report.json",
            "benchmark_gate_report.json",
            "constitution_audit_report.json",
        )


def test_bridge_batch_empty():
    batch = validate_bridge_batch([])
    assert not batch.overall_ok
    assert batch.total_messages == 0
    assert batch.passed_messages == 0


def test_bridge_batch_mixed_invalid():
    messages = [
        build_message(1, 2, b'abc'),
        build_message(2, 3, b''),  # Invalid (empty payload)
    ]
    batch = validate_bridge_batch(messages)
    assert not batch.overall_ok
    assert batch.total_messages == 2
    assert batch.passed_messages == 1


def test_deployment_manifest_valid():
    from scripts.deployment_manifest import build_deployment_manifest
    manifest = build_deployment_manifest(r'c:\Users\ADMIN\AEGIS-COGNITION')
    assert manifest.root == r'c:\Users\ADMIN\AEGIS-COGNITION'
    assert manifest.python_bridge_ready
    assert manifest.rust_core_ready
    assert manifest.packaging_ready
    assert manifest.service_surface_ready
    assert manifest.cargo_manifest_ready
    assert manifest.docs_ready
    assert manifest.artifacts_dir_ready
    assert manifest.operator_api_ready


def test_benchmark_gate_accepts_estimate_under_threshold():
    from scripts.benchmark_gate import evaluate_benchmarks

    with TemporaryDirectory() as tmp:
        estimate_dir = Path(tmp) / "target" / "criterion" / "runtime_ingest" / "new"
        estimate_dir.mkdir(parents=True)
        (estimate_dir / "estimates.json").write_text(
            json.dumps({"slope": {"point_estimate": 500.0}}),
            encoding="utf-8",
        )
        report = evaluate_benchmarks(tmp, {"runtime_ingest": 1_000.0})
        assert report.overall_ok
        assert report.passed == 1
        assert report.failed == 0


def test_benchmark_gate_rejects_missing_estimate():
    from scripts.benchmark_gate import evaluate_benchmarks

    with TemporaryDirectory() as tmp:
        report = evaluate_benchmarks(tmp, {"runtime_ingest": 1_000.0})
        assert not report.overall_ok
        assert report.failed == 1


def test_benchmark_cache_manifest_is_fail_closed():
    import scripts.run_checks as run_checks

    class BenchmarkReport:
        def __init__(self, overall_ok):
            self.overall_ok = overall_ok

    class FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    with TemporaryDirectory() as tmp:
        manifest_path = Path(tmp) / "artifacts" / ".benchmark_manifest.json"
        run_calls = {"count": 0}

        def fake_run(*args, **kwargs):
            run_calls["count"] += 1
            return FakeCompletedProcess()

        with (
            patch("scripts.run_checks.BENCHMARK_MANIFEST_PATH", manifest_path),
            patch("scripts.run_checks.criterion_artifacts_present", return_value=True),
            patch("scripts.run_checks.benchmark_content_hash", return_value="h1"),
            patch("scripts.run_checks.subprocess.run", side_effect=fake_run),
        ):
            run_checks._write_benchmark_manifest(manifest_path, "h1")
            with patch("scripts.run_checks.evaluate_benchmarks", return_value=BenchmarkReport(True)):
                state = run_checks.ensure_fresh_benchmarks(
                    tmp,
                    force_fresh=False,
                    benchmark_profile="release",
                )
            assert state["cache_hit"]
            assert not state["ran_benchmark"]
            assert run_calls["count"] == 0
            assert manifest_path.exists()

            run_checks._write_benchmark_manifest(manifest_path, "h1")
            with patch(
                "scripts.run_checks.evaluate_benchmarks",
                return_value=BenchmarkReport(False),
            ):
                state = run_checks.ensure_fresh_benchmarks(
                    tmp,
                    force_fresh=False,
                    benchmark_profile="release",
                )
            assert not state["cache_hit"]
            assert not state["ran_benchmark"]
            assert state["invalid_benchmark_manifest_removed"]
            assert not manifest_path.exists()

            run_checks._write_benchmark_manifest(manifest_path, "old")
            with patch("scripts.run_checks.evaluate_benchmarks", return_value=BenchmarkReport(True)):
                state = run_checks.ensure_fresh_benchmarks(
                    tmp,
                    force_fresh=False,
                    benchmark_profile="release",
                )
            assert state["stale_content_hash"]
            assert not state["ran_benchmark"]
            assert state["refresh_required_for_manifest_update"]
            assert state["validated_existing_artifacts"]
            assert json.loads(manifest_path.read_text(encoding="utf-8"))["content_hash_blake3"] == "old"

            with patch(
                "scripts.run_checks.evaluate_benchmarks",
                side_effect=[BenchmarkReport(True), BenchmarkReport(True)],
            ):
                state = run_checks.ensure_fresh_benchmarks(
                    tmp,
                    force_fresh=True,
                    benchmark_profile="release",
                )
            assert state["ran_benchmark"]
            assert state["post_benchmark_gate_ok"]
            assert json.loads(manifest_path.read_text(encoding="utf-8"))["content_hash_blake3"] == "h1"


def test_run_checks_production_readiness_summary_exposes_active_blockers():
    import scripts.run_checks as run_checks

    active_blockers = (
        {
            "id": "external_signed_attestation_missing",
            "blocks_production": True,
            "evidence_artifact": "supply_chain_gate_report.json",
            "active": True,
        },
        {
            "id": "real_multi_machine_cluster_soak_missing",
            "blocks_production": True,
            "evidence_artifact": "tcp_cluster_soak_gate_report.json",
            "active": True,
        },
        {
            "id": "full_quickjs_interpreter_cold_start_missing",
            "blocks_production": True,
            "evidence_artifact": "quickjs_cold_start_gate_report.json",
            "active": True,
        },
        {
            "id": "live_provider_429_soak_missing",
            "blocks_production": True,
            "evidence_artifact": "dynamic_provider_fallback_gate_report.json",
            "active": True,
        },
        {
            "id": "external_deployment_smoke_missing",
            "blocks_production": True,
            "evidence_artifact": "external_deployment_smoke_gate_report.json",
            "active": True,
        },
    )

    class FakeDeployment:
        production_deployable = False
        production_blocker_hash = "11" * 32
        active_production_blocker_hash = "22" * 32
        release_attestation_hash = "33" * 32
        external_signed_attestation_present = False
        topology_hash = "44" * 32
        topology_contract_hash = "55" * 32
        active_production_blockers = active_blockers

    summary = run_checks._production_readiness_summary(
        FakeDeployment(),
        {
            "production_deployable": False,
            "active_production_blocker_hash": "22" * 32,
            "active_production_blockers": list(active_blockers),
        },
    )

    assert summary["schema"] == "aegis-run-checks-production-readiness-v1"
    assert summary["truth_claim"] is False
    assert summary["production_deployable"] is False
    assert summary["production_gap_disclosed"] is True
    assert summary["deployment_manifest_production_deployable"] is False
    assert summary["e2e_release_gate_production_deployable"] is False
    assert summary["external_signed_attestation_present"] is False
    assert summary["deployment_topology_defined"] is True
    assert summary["active_production_blocker_count"] == 5
    assert summary["active_production_blocker_ids"] == [
        "external_signed_attestation_missing",
        "real_multi_machine_cluster_soak_missing",
        "full_quickjs_interpreter_cold_start_missing",
        "live_provider_429_soak_missing",
        "external_deployment_smoke_missing",
    ]
    assert summary["active_production_blockers"] == list(active_blockers)
    assert summary["closure_packet_count"] == 5
    assert len(summary["closure_packet_set_hash"]) == 64
    assert [packet["blocker_id"] for packet in summary["closure_packets"]] == summary["active_production_blocker_ids"]
    assert all(packet["schema"] == "aegis-production-blocker-closure-packet-v1" for packet in summary["closure_packets"])
    assert all(len(packet["closure_packet_hash"]) == 64 for packet in summary["closure_packets"])
    assert summary["closure_packets"][0]["expected_artifacts"] == [
        "artifacts/release_signed_attestation.json",
        "artifacts/release_signed_attestation_verification.json",
    ]
    assert "AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON" in summary["closure_packets"][1]["required_env"]
    assert "artifacts/quickjs_full_interpreter.wasm" in summary["closure_packets"][2]["required_artifacts"]
    assert "AEGIS_LIVE_PROVIDER_429_SOAK_URL" in summary["closure_packets"][3]["required_env"]
    assert "AEGIS_EXTERNAL_DEPLOYMENT_SMOKE_URL" in summary["closure_packets"][4]["required_env"]
    assert len(summary["readiness_hash"]) == 64


def test_production_readiness_standalone_writer_materializes_hash_bound_report():
    import scripts.production_readiness as readiness

    active_blockers = (
        {
            "id": "external_deployment_smoke_missing",
            "blocks_production": True,
            "evidence_artifact": "external_deployment_smoke_gate_report.json",
            "active": True,
        },
    )

    class FakeDeployment:
        production_deployable = False
        production_blocker_hash = "11" * 32
        active_production_blocker_hash = "22" * 32
        release_attestation_hash = "33" * 32
        external_signed_attestation_present = True
        topology_hash = "44" * 32
        topology_contract_hash = "55" * 32
        active_production_blockers = active_blockers

    report = readiness.build_production_readiness_report(
        FakeDeployment(),
        {
            "production_deployable": False,
            "active_production_blocker_hash": "22" * 32,
        },
    )

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "artifacts" / "production_readiness_report.json"
        with patch("scripts.production_readiness.evaluate_production_readiness", return_value=report):
            written = readiness.write_production_readiness_report(root)

        payload = json.loads(target.read_text(encoding="utf-8"))
        assert written == report
        assert payload["schema"] == "aegis-run-checks-production-readiness-v1"
        assert payload["truth_claim"] is False
        assert payload["production_deployable"] is False
        assert payload["active_production_blocker_ids"] == ["external_deployment_smoke_missing"]
        assert payload["closure_packet_count"] == 1
        assert payload["closure_packets"][0]["producer_command"] == [
            "python",
            "scripts/external_deployment_smoke_gate.py",
            "--write-capture",
        ]
        assert payload["closure_packets"][0]["expected_artifacts"] == [
            "artifacts/external_deployment_smoke_capture.json"
        ]
        assert payload["closure_packets"][0]["closure_packet_hash"]
        assert len(payload["closure_packet_set_hash"]) == 64
        assert len(payload["readiness_hash"]) == 64


def test_production_closure_workflow_preflights_missing_external_inputs_without_truth_claim():
    import scripts.production_closure as closure
    import scripts.production_readiness as readiness

    active_blockers = [
        {
            "id": "external_signed_attestation_missing",
            "blocks_production": True,
            "evidence_artifact": "supply_chain_gate_report.json",
            "active": True,
        },
        {
            "id": "live_provider_429_soak_missing",
            "blocks_production": True,
            "evidence_artifact": "dynamic_provider_fallback_gate_report.json",
            "active": True,
        },
    ]
    readiness_report = {
        "schema": "aegis-run-checks-production-readiness-v1",
        "truth_claim": False,
        "production_deployable": False,
        "active_production_blocker_count": 2,
        "active_production_blocker_ids": [blocker["id"] for blocker in active_blockers],
        "active_production_blockers": active_blockers,
        "closure_packets": readiness.production_blocker_closure_packets(active_blockers),
    }

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        workflow = closure.build_production_closure_workflow(root, readiness_report, env={})

    assert workflow["schema"] == "aegis-production-closure-workflow-v1"
    assert workflow["truth_claim"] is False
    assert workflow["production_deployable"] is False
    assert workflow["active_blocker_count"] == 2
    assert workflow["ready_to_execute_count"] == 0
    assert workflow["closed_blocker_count"] == 0
    assert len(workflow["workflow_hash"]) == 64
    signed, provider = workflow["steps"]
    assert signed["blocker_id"] == "external_signed_attestation_missing"
    assert signed["producer_available"] is False
    assert "artifacts/release_signed_attestation.json" in signed["missing_expected_artifacts"]
    assert provider["blocker_id"] == "live_provider_429_soak_missing"
    assert "AEGIS_LIVE_PROVIDER_429_SOAK_URL" in provider["missing_required_env"]
    assert "artifacts/dynamic_provider_fallback_report.json" in provider["missing_required_artifacts"]


def test_production_closure_execute_runs_ready_commands_through_resolved_python():
    import scripts.production_closure as closure
    import scripts.production_readiness as readiness

    active_blockers = [
        {
            "id": "live_provider_429_soak_missing",
            "blocks_production": True,
            "evidence_artifact": "dynamic_provider_fallback_gate_report.json",
            "active": True,
        },
    ]
    readiness_report = {
        "schema": "aegis-run-checks-production-readiness-v1",
        "truth_claim": False,
        "production_deployable": False,
        "active_production_blocker_count": 1,
        "active_production_blocker_ids": ["live_provider_429_soak_missing"],
        "active_production_blockers": active_blockers,
        "closure_packets": readiness.production_blocker_closure_packets(active_blockers),
    }
    env = {
        "AEGIS_LIVE_PROVIDER_429_SOAK_URL": "https://example.invalid/429",
        "AEGIS_LIVE_PROVIDER_429_SOAK_METHOD": "GET",
        "AEGIS_LIVE_PROVIDER_429_SOAK_HEADERS_JSON": "{}",
        "AEGIS_LIVE_PROVIDER_429_SOAK_BODY": "",
        "AEGIS_LIVE_PROVIDER_429_SOAK_TIMEOUT_SECONDS": "1",
    }
    commands: list[list[str]] = []

    def fake_runner(command, cwd, timeout_seconds):
        commands.append(list(command))
        return {
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "timed_out": False,
        }

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        (artifacts / "dynamic_provider_fallback_report.json").write_text("{}", encoding="utf-8")
        workflow = closure.run_production_closure_workflow(
            root,
            readiness_report=readiness_report,
            after_readiness_report=readiness_report,
            env=env,
            execute=True,
            runner=fake_runner,
        )

    assert workflow["execute"] is True
    assert workflow["ready_to_execute_count"] == 1
    assert workflow["command_result_count"] == 2
    assert workflow["command_failure_count"] == 0
    assert len(commands) == 2
    assert commands[0][0] == sys.executable
    assert commands[0][1:] == ["scripts/dynamic_provider_fallback_gate.py", "--write-capture"]
    assert commands[1][0] == sys.executable
    assert commands[1][1:] == ["scripts/dynamic_provider_fallback_gate.py"]


def test_python_hotpath_gate_accepts_mmap_payload_view():
    from scripts.python_hotpath_gate import evaluate_python_hotpaths, report_to_dict

    with TemporaryDirectory() as tmp:
        report = evaluate_python_hotpaths(tmp)
        payload = report_to_dict(report)
        assert report.overall_ok
        assert report.passed == 1
        assert payload["checks"][0]["name"] == "python_mmap_bridge_payload_view"
        assert payload["checks"][0]["estimate_ns"] <= payload["checks"][0]["threshold_ns"]


def test_hermes_session_recovery_baseline_gate_binds_physical_artifacts():
    import scripts.hermes_session_recovery_baseline_gate as gate

    with TemporaryDirectory() as tmp:
        baseline_dir = Path(tmp) / "hermes-session-recovery"
        with (
            patch("scripts.hermes_session_recovery_baseline_gate.BASELINE_DIR", baseline_dir),
            patch("scripts.hermes_session_recovery_baseline_gate.BASELINE_DB_PATH", baseline_dir / "state.db"),
            patch(
                "scripts.hermes_session_recovery_baseline_gate.BASELINE_RECOVERY_PAYLOAD_PATH",
                baseline_dir / "recovered.json",
            ),
            patch("scripts.hermes_session_recovery_baseline_gate._criterion_estimate_ns", return_value=1.0),
        ):
            report = gate.evaluate_hermes_session_recovery_baseline(Path.cwd())
        payload = gate.report_to_dict(report)
        evidence = payload["physical_evidence"]
        assert report.overall_ok
        assert payload["checks"][0]["name"] == (
            "hermes_sqlite_json_session_recovery_vs_aegis_binary_mmap_prefix_recovery"
        )
        assert payload["checks"][0]["speedup"] >= payload["checks"][0]["threshold_speedup"]
        assert evidence["baseline_recovered_message_count"] == evidence["baseline_recovery_messages"]
        assert evidence["baseline_lineage_hops"] == 1
        assert len(evidence["baseline_db_hash_blake3"]) == 64
        assert len(evidence["baseline_recovery_payload_hash_blake3"]) == 64
        assert len(evidence["evidence_hash_blake3"]) == 64
        assert Path(evidence["baseline_db_path"]).exists()
        assert Path(evidence["baseline_recovery_payload_path"]).exists()


def test_hermes_persistence_baseline_gate_binds_physical_artifacts():
    import scripts.hermes_persistence_baseline_gate as gate

    with TemporaryDirectory() as tmp:
        baseline_dir = Path(tmp) / "hermes-persistence"
        with (
            patch("scripts.hermes_persistence_baseline_gate.BASELINE_DIR", baseline_dir),
            patch("scripts.hermes_persistence_baseline_gate.BASELINE_DB_PATH", baseline_dir / "state.db"),
            patch("scripts.hermes_persistence_baseline_gate.BASELINE_TRANSCRIPT_PATH", baseline_dir / "transcript.json"),
            patch("scripts.hermes_persistence_baseline_gate._criterion_estimate_ns", return_value=1.0),
        ):
            report = gate.evaluate_hermes_persistence_baseline(Path.cwd())
        payload = gate.report_to_dict(report)
        evidence = payload["physical_evidence"]
        assert report.overall_ok
        assert payload["checks"][0]["name"] == (
            "hermes_full_transcript_rewrite_vs_aegis_segmented_arrow_append"
        )
        assert payload["checks"][0]["speedup"] >= payload["checks"][0]["threshold_speedup"]
        assert evidence["baseline_message_rows"] == evidence["baseline_history_messages"]
        assert evidence["baseline_fts_rows"] == evidence["baseline_history_messages"]
        assert len(evidence["baseline_db_hash_blake3"]) == 64
        assert len(evidence["baseline_transcript_hash_blake3"]) == 64
        assert len(evidence["evidence_hash_blake3"]) == 64
        assert Path(evidence["baseline_db_path"]).exists()
        assert Path(evidence["baseline_transcript_path"]).exists()


def test_hermes_rpc_baseline_gate_uses_aggregate_mmap_overhead():
    import scripts.hermes_rpc_baseline_gate as gate

    with TemporaryDirectory() as tmp:
        baseline_dir = Path(tmp) / "hermes-rpc"
        with (
            patch("scripts.hermes_rpc_baseline_gate.BASELINE_DIR", baseline_dir),
            patch("scripts.hermes_rpc_baseline_gate.BASELINE_PAYLOAD_PATH", baseline_dir / "payload.jsonl"),
            patch("scripts.hermes_rpc_baseline_gate.BASELINE_MMAP_PATH", baseline_dir / "context.aegmmap"),
            patch("scripts.hermes_rpc_baseline_gate._measure_json_rpc_context_ns") as json_rpc_measure,
            patch("scripts.hermes_rpc_baseline_gate._measure_mmap_payload_view_ns", return_value=1_000.0),
        ):
            json_rpc_measure.return_value = (
                1_000_000.0,
                "ab" * 32,
                gate.BASELINE_FRAME_COUNT,
                True,
            )
            report = gate.evaluate_hermes_rpc_baseline(Path.cwd())
        payload = gate.report_to_dict(report)
        evidence = payload["physical_evidence"]
        assert report.overall_ok
        assert evidence["aegis_aggregate_mmap_frame_count"] == 1
        assert evidence["aegis_aggregate_mmap_replaces_json_rpc_frames"] == gate.BASELINE_FRAME_COUNT
        assert evidence["aegis_mmap_overhead_percent"] == 0.1


def test_replay_chaos_scorecard_gate_accepts_valid_artifact():
    from scripts.run_checks import generate_replay_chaos_scorecard

    class FakeCompletedProcess:
        returncode = 0
        stdout = "replay_chaos_scorecard_hash=" + ("ab" * 32)
        stderr = ""

    def fake_run(command, cwd, capture_output, text, env):
        artifact_path = Path(command[-1])
        artifact_hash = [1] * 32
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bench_name": "ReplayChaosBench",
                    "scorecard": {
                        "success": True,
                        "crash_recovery_passed": True,
                        "physical_witness_hash": artifact_hash,
                        "replay_hash": artifact_hash,
                    },
                    "report": {
                        "all_recoveries_valid": True,
                        "report_hash": artifact_hash,
                        "crash_points_exercised": 3,
                        "segmented_arrow_audit_proof_hash": [4] * 32,
                        "segmented_arrow_audit_logical_replay_hash": [5] * 32,
                        "segmented_arrow_audit_mmap_evidence_hash": [6] * 32,
                        "segmented_arrow_audit_segment_witness_hash": [7] * 32,
                        "segmented_arrow_audit_mmap_buffer_count": 2,
                        "column_scan_full_acceptance_count": 1,
                        "column_scan_missing_tail_rejection_count": 2,
                    },
                    "io_evidence": {
                        "mmap_backing_used": True,
                        "stream_reader_materializes_events": True,
                        "materialized_event_count": 3,
                        "materialized_run_event_bytes": 384,
                        "materialized_hash_bytes": 320,
                    },
                    "write_evidence": {
                        "staged_temp_file_used": True,
                        "temp_file_synced_before_publish": True,
                        "publish_completed": True,
                        "parent_directory_sync_attempted": True,
                        "replace_existing_supported": True,
                        "publish_write_through_requested": os.name == "nt",
                        "logical_payload_bytes": 4096,
                        "logical_payload_hash": [2] * 32,
                        "evidence_hash": [3] * 32,
                    },
                }
            ),
            encoding="utf-8",
        )
        return FakeCompletedProcess()

    with TemporaryDirectory() as tmp:
        with patch("scripts.run_checks.subprocess.run", side_effect=fake_run):
            report = generate_replay_chaos_scorecard(tmp)
        assert report["overall_ok"]
        assert report["artifact_hash_blake3"] == "ab" * 32
        assert report["validation"]["ok"]
        assert report["artifact"]["io_evidence"]["mmap_backing_used"]


def test_python_skeleton_generation_fallback():
    from .orchestrator import generate_skeleton
    code = "impl Test {\n    fn hello() {\n        print(123)\n    }\n}"
    skeleton = generate_skeleton(code)
    assert "todo" in skeleton
    assert "123" not in skeleton


def test_python_analyze_compile_errors_fallback():
    from .orchestrator import analyze_compile_errors
    logs = "info: checking\nerror[E1234]: bad type\nwarning: unused"
    errors = analyze_compile_errors(logs)
    assert len(errors) == 1
    assert "error[E1234]" in errors[0]


def test_ffi_boundary_robustness():
    # 1. Extremely large payload (10 MB)
    large_payload = b'a' * 10 * 1024 * 1024
    message = build_message(99, 100, large_payload)
    assert validate_runtime_message(message)
    zero_copy = to_zero_copy(message)
    assert zero_copy.is_valid()
    assert zero_copy.payload_len == len(large_payload)

    # 2. Payload with binary/non-UTF8 characters
    binary_payload = bytes(range(256))
    bin_msg = build_message(101, 102, binary_payload)
    assert validate_runtime_message(bin_msg)
    bin_zc = to_zero_copy(bin_msg)
    assert bin_zc.is_valid()
    assert bin_zc.payload_len == 256

    # 3. Payload with extreme Unicode string
    unicode_payload = "𠜎 𠜱 𠝹 𠱓 𠱸 𠲖 𠲧 𠲼 𠴕 𠴚 𠴛 𠴜 𠵅 𠵆 𠵇 𠵈 𠵉 𠵊 𠵋 𠵌 𠵍 𠵎 𠵏 𠵐 𠵑 𠵒 𠵓 𠵔 𠵕 𠵖 𠵗 𠵘 𠵙 𠵚 𠵛 𠵜 𠵝 𠵞 𠵟 𠵠 𠵡 𠵢 𠵣 𠵤 𠵥 𠵦 𠵧 𠵨 𠵩 𠵪".encode('utf-8')
    uni_msg = build_message(103, 104, unicode_payload)
    assert validate_runtime_message(uni_msg)
    uni_zc = to_zero_copy(uni_msg)
    assert uni_zc.is_valid()
    assert uni_zc.payload_len == len(unicode_payload)


def test_python_mmap_bridge_exposes_payload_memoryview_without_copy():
    payload = bytes((index * 31 + 7) & 0xFF for index in range(4096))
    header = bytearray(MMAP_BRIDGE_HEADER_BYTES)
    header[0:8] = b"AEGMMAP1"
    struct.pack_into("<II", header, 8, 1, MMAP_BRIDGE_HEADER_BYTES)
    struct.pack_into("<Q", header, 16, 0xAE1515)
    struct.pack_into("<II", header, 24, 1, 64)
    header[32:48] = (77).to_bytes(16, "little")
    header[48:64] = (88).to_bytes(16, "little")
    struct.pack_into("<QQ", header, 64, MMAP_BRIDGE_HEADER_BYTES, len(payload))
    header[80:112] = bytes(range(32))

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "frame.aegmmap"
        path.write_bytes(header + payload)
        with MmapBridgeFrame(path) as frame:
            view = frame.payload_view()
            try:
                assert isinstance(view, memoryview)
                assert frame.header.message_id == 77
                assert frame.header.session_id == 88
                assert frame.header.payload_offset == MMAP_BRIDGE_HEADER_BYTES
                assert len(view) == len(payload)
                assert view[0] == payload[0]
                assert view[-1] == payload[-1]
            finally:
                view.release()


def test_python_mmap_wasm_execution_requires_rust_wasmtime_extension():
    with patch.dict("sys.modules", {"aegis_nerve": None}):
        with pytest.raises(RuntimeError, match="Rust-owned mmap Wasm|aegis_nerve extension"):
            execute_mmap_wasm_bridge_frame("missing.aegmmap", 1000)


def test_browser_live_collector_producer_writes_exact_artifacts_and_metadata():
    artifacts = build_browser_live_collector_artifacts(
        url_before="https://example.test/before",
        url_after="https://example.test/after",
        dom_snapshot_before="<html><button>before</button></html>",
        dom_snapshot_after="<html><button>after</button></html>",
        screenshot_before=b"\x89PNG-before",
        screenshot_after=b"\x89PNG-after",
        accessibility_tree_after={"role": "button", "name": "after"},
        network_log=[{"url": "https://example.test/api", "status": 200}],
    )
    with TemporaryDirectory() as tmp:
        producer = BrowserLiveCollectorProducer(Path(tmp))
        run = producer.publish(
            run_id=0xA11CE,
            action_id=0xBEEF,
            sequence_number=7,
            artifacts=list(reversed(artifacts)),
        )

        assert run.run_directory.name == (
            "run-000000000000000000000000000a11ce-"
            "action-0000000000000000000000000000beef-seq-0000000000000007"
        )
        assert tuple(run.artifact_paths) == REQUIRED_BROWSER_ARTIFACT_KINDS
        assert run.artifact_paths["url_before"].read_bytes() == b"https://example.test/before"
        assert run.artifact_paths["dom_snapshot_after"].name == "04-dom-after.html"
        assert run.artifact_paths["network_log"].read_text(encoding="utf-8") == (
            '{"status":200,"url":"https://example.test/api"}'
        )

        metadata = json.loads(run.metadata_path.read_text(encoding="utf-8"))
        assert metadata["schema"] == "aegis-browser-live-collector-producer-v1"
        assert metadata["truth_claim"] is False
        assert metadata["verifier"] == "rust-browser-live-collector-run"
        assert sorted(metadata["artifact_paths"]) == sorted(REQUIRED_BROWSER_ARTIFACT_KINDS)

        run.artifact_paths["url_after"].write_bytes(b"tampered")
        metadata_after_tamper = json.loads(run.metadata_path.read_text(encoding="utf-8"))
        assert metadata_after_tamper["truth_claim"] is False


def test_browser_live_collector_producer_rejects_missing_duplicate_empty_and_oversized():
    artifacts = build_browser_live_collector_artifacts(
        url_before="https://example.test/before",
        url_after="https://example.test/after",
        dom_snapshot_before="<html>before</html>",
        dom_snapshot_after="<html>after</html>",
        screenshot_before=b"before",
        screenshot_after=b"after",
        accessibility_tree_after="{}",
        network_log="{}",
    )
    with TemporaryDirectory() as tmp:
        producer = BrowserLiveCollectorProducer(Path(tmp), max_bytes=16)
        with pytest.raises(ValueError, match="exactly eight"):
            producer.publish(1, 2, 3, artifacts[:-1])

        duplicate = artifacts[:-1] + [artifacts[0]]
        with pytest.raises(ValueError, match="duplicate"):
            producer.publish(1, 2, 3, duplicate)

        empty = [
            BrowserLiveCollectorArtifact(artifact.kind, b"" if artifact.kind == "url_before" else artifact.bytes)
            for artifact in artifacts
        ]
        with pytest.raises(ValueError, match="empty"):
            producer.publish(1, 2, 3, empty)

        oversized = [
            BrowserLiveCollectorArtifact(
                artifact.kind,
                b"x" * 17 if artifact.kind == "screenshot_after" else artifact.bytes,
            )
            for artifact in artifacts
        ]
        with pytest.raises(ValueError, match="oversized"):
            producer.publish(1, 2, 3, oversized)


class _FakePage:
    def __init__(self) -> None:
        self.url = "https://example.test/before"
        self.dom = "<html><button>before</button></html>"
        self.screenshot_bytes = b"before-shot"
        self.ax = {"role": "button", "name": "before"}

    async def content(self) -> str:
        return self.dom

    async def screenshot(self, full_page: bool = True) -> bytes:
        assert full_page
        return self.screenshot_bytes

    async def accessibility_snapshot(self) -> dict[str, str]:
        return self.ax


class _FakeBrowserSession:
    def __init__(self) -> None:
        self.page = _FakePage()
        self._network = [{"url": "https://example.test/bootstrap", "status": 204}]
        self.cleared = False

    async def get_current_page(self) -> _FakePage:
        return self.page

    async def clear_network_log(self) -> None:
        self.cleared = True
        self._network = []

    async def drain_network_log(self) -> list[dict[str, int | str]]:
        drained = list(self._network)
        self._network = []
        return drained

    async def mutate_after_action(self) -> str:
        self.page.url = "https://example.test/after"
        self.page.dom = "<html><button>after</button></html>"
        self.page.screenshot_bytes = b"after-shot"
        self.page.ax = {"role": "button", "name": "after"}
        self._network.append({"url": "https://example.test/api", "status": 200})
        return "clicked"


class _BlockingBrowserLiveCollectorProducer(BrowserLiveCollectorProducer):
    def __init__(
        self,
        output_root: Path,
        publish_entered: threading.Event,
        publish_release: threading.Event,
    ) -> None:
        super().__init__(output_root)
        self.publish_entered = publish_entered
        self.publish_release = publish_release

    def publish(self, *args, **kwargs):
        self.publish_entered.set()
        if not self.publish_release.wait(timeout=2):
            raise TimeoutError("cold publish release was not signaled")
        return super().publish(*args, **kwargs)


def _test_hot_batch_record(
    artifacts: list[BrowserLiveCollectorArtifact],
    verifier: str = "test-batch-hot-arena",
) -> dict[str, object]:
    commits = []
    total_bytes = 0
    batch_hasher = hashlib.blake2b(digest_size=32)
    batch_hasher.update(verifier.encode("utf-8"))
    for index, artifact in enumerate(artifacts):
        digest = hashlib.blake2b(artifact.bytes, digest_size=32).hexdigest()
        storage_ref = hashlib.blake2b(
            b"batch-storage" + index.to_bytes(8, "little") + digest.encode("ascii"),
            digest_size=32,
        ).hexdigest()
        total_bytes += len(artifact.bytes)
        batch_hasher.update(index.to_bytes(8, "little"))
        batch_hasher.update(digest.encode("ascii"))
        batch_hasher.update(storage_ref.encode("ascii"))
        commits.append(
            {
                "byte_len": len(artifact.bytes),
                "artifact_hash": digest,
                "storage_ref_hash": storage_ref,
                "trust_level": "DEV",
                "admission": "degraded_warning",
                "verifier": verifier,
                "handle_valid": True,
            }
        )
    return {
        "schema": "aegis-hot-arena-commit-batch-v1",
        "truth_claim": False,
        "verifier": verifier,
        "trust_level": "DEV",
        "artifact_count": len(commits),
        "total_bytes": total_bytes,
        "no_file_roundtrip_on_hot_path": True,
        "batch_digest": batch_hasher.hexdigest(),
        "arena_live_bytes": total_bytes,
        "arena_live_slots": len(commits),
        "arena_total_commits": len(commits),
        "arena_total_degraded_commits": len(commits),
        "commits": commits,
    }


def test_browser_runtime_adapter_captures_action_and_publishes_artifacts():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()
        adapter = BrowserRuntimeCollectorAdapter(BrowserLiveCollectorProducer(Path(tmp)))

        async def scenario():
            return await adapter.capture_action(
                browser_session=session,
                run_id=11,
                action_id=22,
                sequence_number=33,
                action=session.mutate_after_action,
            )

        capture = __import__("asyncio").run(scenario())

        assert session.cleared
        assert capture.action_result == "clicked"
        assert capture.before.url == "https://example.test/before"
        assert capture.after.url == "https://example.test/after"
        assert capture.producer_run.artifact_paths["url_before"].read_text() == "https://example.test/before"
        assert capture.producer_run.artifact_paths["url_after"].read_text() == "https://example.test/after"
        assert capture.producer_run.artifact_paths["screenshot_before"].read_bytes() == b"before-shot"
        assert capture.producer_run.artifact_paths["screenshot_after"].read_bytes() == b"after-shot"
        assert capture.producer_run.artifact_paths["accessibility_tree_after"].read_text() == (
            '{"name":"after","role":"button"}'
        )
        assert capture.producer_run.artifact_paths["network_log"].read_text() == (
            '{"status":200,"url":"https://example.test/api"}'
        )


def test_browser_runtime_adapter_hot_evidence_commits_before_cold_publish():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()
        seen_payloads: list[bytes] = []

        def publisher(payload: bytes) -> dict[str, object]:
            seen_payloads.append(payload)
            digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
            return {
                "byte_len": len(payload),
                "artifact_hash": digest,
                "storage_ref_hash": hashlib.blake2b(
                    b"storage" + digest.encode("ascii"),
                    digest_size=32,
                ).hexdigest(),
                "trust_level": "DEV",
                "admission": "degraded_warning",
                "verifier": "python-dev-fallback",
                "handle_valid": True,
            }

        adapter = BrowserRuntimeCollectorAdapter(
            BrowserLiveCollectorProducer(Path(tmp)),
            hot_evidence_publisher=publisher,
        )

        async def scenario():
            return await adapter.capture_action(
                browser_session=session,
                run_id=11,
                action_id=22,
                sequence_number=33,
                action=session.mutate_after_action,
            )

        capture = __import__("asyncio").run(scenario())

        assert capture.hot_evidence is not None
        assert capture.hot_evidence.schema == "aegis-hot-browser-evidence-batch-v1"
        assert capture.hot_evidence.truth_claim is False
        assert capture.hot_evidence.artifact_count == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert len(capture.hot_evidence.commits) == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert len(seen_payloads) == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert all(commit.handle_valid for commit in capture.hot_evidence.commits)
        assert all(len(commit.artifact_hash) == 64 for commit in capture.hot_evidence.commits)
        assert capture.hot_evidence.total_bytes == sum(len(payload) for payload in seen_payloads)
        assert len(capture.hot_evidence.batch_digest) == 64
        assert capture.producer_run.metadata_path.exists()


def test_browser_runtime_adapter_batch_hot_evidence_commits_before_cold_publish():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()
        output_root = Path(tmp) / "browser-cold"
        seen_batch_kinds: list[str] = []
        metadata_seen_during_hot_batch: list[Path] = []

        def batch_publisher(artifacts: list[BrowserLiveCollectorArtifact]) -> dict[str, object]:
            metadata_seen_during_hot_batch.extend(output_root.rglob("producer-metadata.json"))
            seen_batch_kinds.extend(artifact.kind for artifact in artifacts)
            commits = []
            total_bytes = 0
            batch_hasher = hashlib.blake2b(digest_size=32)
            batch_hasher.update(b"test-hot-arena-batch")
            for index, artifact in enumerate(artifacts):
                digest = hashlib.blake2b(artifact.bytes, digest_size=32).hexdigest()
                storage_ref = hashlib.blake2b(
                    b"batch-storage" + index.to_bytes(8, "little") + digest.encode("ascii"),
                    digest_size=32,
                ).hexdigest()
                total_bytes += len(artifact.bytes)
                batch_hasher.update(index.to_bytes(8, "little"))
                batch_hasher.update(digest.encode("ascii"))
                batch_hasher.update(storage_ref.encode("ascii"))
                commits.append(
                    {
                        "byte_len": len(artifact.bytes),
                        "artifact_hash": digest,
                        "storage_ref_hash": storage_ref,
                        "trust_level": "DEV",
                        "admission": "degraded_warning",
                        "verifier": "test-batch-hot-arena",
                        "handle_valid": True,
                    }
                )
            return {
                "schema": "aegis-hot-arena-commit-batch-v1",
                "truth_claim": False,
                "verifier": "test-batch-hot-arena",
                "trust_level": "DEV",
                "artifact_count": len(commits),
                "total_bytes": total_bytes,
                "no_file_roundtrip_on_hot_path": True,
                "batch_digest": batch_hasher.hexdigest(),
                "arena_live_bytes": total_bytes,
                "arena_live_slots": len(commits),
                "arena_total_commits": len(commits),
                "arena_total_degraded_commits": len(commits),
                "commits": commits,
            }

        adapter = BrowserRuntimeCollectorAdapter(
            BrowserLiveCollectorProducer(output_root),
            hot_evidence_batch_publisher=batch_publisher,
        )

        async def scenario():
            return await adapter.capture_action(
                browser_session=session,
                run_id=11,
                action_id=22,
                sequence_number=33,
                action=session.mutate_after_action,
            )

        capture = __import__("asyncio").run(scenario())

        assert capture.hot_evidence is not None
        assert capture.hot_evidence.schema == "aegis-hot-browser-evidence-batch-v1"
        assert capture.hot_evidence.verifier == "test-batch-hot-arena"
        assert capture.hot_evidence.artifact_count == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert tuple(seen_batch_kinds) == REQUIRED_BROWSER_ARTIFACT_KINDS
        assert metadata_seen_during_hot_batch == []
        assert capture.producer_run.metadata_path.exists()
        assert capture.hot_evidence.no_file_roundtrip_on_hot_path is True
        assert len(capture.hot_evidence.batch_digest) == 64
        assert all(commit.verifier == "test-batch-hot-arena" for commit in capture.hot_evidence.commits)


def test_browser_runtime_adapter_hot_first_returns_before_cold_publish_and_waits_for_metadata():
    with TemporaryDirectory() as tmp:
        session = _FakeBrowserSession()
        output_root = Path(tmp) / "browser-cold"
        publish_entered = threading.Event()
        publish_release = threading.Event()
        adapter = BrowserRuntimeCollectorAdapter(
            _BlockingBrowserLiveCollectorProducer(output_root, publish_entered, publish_release),
            hot_evidence_batch_publisher=lambda artifacts: _test_hot_batch_record(
                artifacts,
                verifier="test-hot-first-batch",
            ),
        )

        async def scenario():
            return await adapter.capture_action_hot_first(
                browser_session=session,
                run_id=44,
                action_id=55,
                sequence_number=66,
                action=session.mutate_after_action,
            )

        capture = __import__("asyncio").run(scenario())

        assert capture.hot_evidence.schema == "aegis-hot-browser-evidence-batch-v1"
        assert capture.hot_evidence.verifier == "test-hot-first-batch"
        assert capture.hot_evidence.artifact_count == len(REQUIRED_BROWSER_ARTIFACT_KINDS)
        assert capture.hot_evidence.no_file_roundtrip_on_hot_path is True
        assert capture.cold_publish.schema == "aegis-browser-runtime-cold-publish-handle-v1"
        assert capture.cold_publish.truth_claim is False
        assert capture.cold_publish.background_publish_started is True
        assert capture.cold_publish.hot_returned_before_cold_publish is True
        assert len(capture.cold_publish.cold_publish_handle_hash) == 64
        assert publish_entered.wait(timeout=1)
        assert capture.cold_publish.done() is False
        assert list(output_root.rglob("producer-metadata.json")) == []

        publish_release.set()
        producer_run = capture.cold_publish.wait(timeout=2)

        assert producer_run.metadata_path.exists()
        assert capture.cold_publish.done() is True
        assert producer_run.run_id == 44
        assert producer_run.action_id == 55
        assert producer_run.sequence_number == 66
        assert producer_run.artifact_paths["url_after"].read_text() == "https://example.test/after"


def test_publish_hot_browser_evidence_rejects_byte_length_drift():
    artifacts = build_browser_live_collector_artifacts(
        url_before="before",
        url_after="after",
        dom_snapshot_before="<html>before</html>",
        dom_snapshot_after="<html>after</html>",
        screenshot_before=b"before",
        screenshot_after=b"after",
        accessibility_tree_after={"ok": True},
        network_log=[],
    )

    def bad_publisher(payload: bytes) -> dict[str, object]:
        digest = hashlib.blake2b(payload, digest_size=32).hexdigest()
        return {
            "byte_len": len(payload) + 1,
            "artifact_hash": digest,
            "storage_ref_hash": digest,
            "handle_valid": True,
        }

    with pytest.raises(ValueError, match="byte length mismatch"):
        publish_hot_browser_evidence(
            run_id=1,
            action_id=2,
            sequence_number=3,
            artifacts=artifacts,
            publisher=bad_publisher,
        )


def test_browser_runtime_snapshot_and_network_helpers_are_duck_typed():
    class PageWithEvaluate:
        url = "https://example.test/eval"

        async def evaluate(self, script: str) -> str:
            assert script == "document.documentElement.outerHTML"
            return "<html>eval</html>"

        async def screenshot(self) -> str:
            return "c2NyZWVuc2hvdA=="

    class Session:
        page = PageWithEvaluate()
        network_events = '[{"url":"https://example.test/eval","status":201}]'

    async def scenario():
        snapshot = await capture_browser_runtime_snapshot(Session())
        network = await collect_browser_network_log(Session())
        return snapshot, network

    snapshot, network = __import__("asyncio").run(scenario())
    assert snapshot.url == "https://example.test/eval"
    assert snapshot.dom_snapshot == "<html>eval</html>"
    assert snapshot.screenshot == b"screenshot"
    assert snapshot.accessibility_tree == b'{"source":"browser-runtime-adapter","unavailable":true}'
    assert network == [{"url": "https://example.test/eval", "status": 201}]


def test_browser_runtime_adapter_fails_before_publish_without_dom_or_screenshot():
    class BadPage:
        url = "https://example.test/bad"

    with TemporaryDirectory() as tmp:
        adapter = BrowserRuntimeCollectorAdapter(BrowserLiveCollectorProducer(Path(tmp)))

        async def scenario():
            return await adapter.capture_action(
                browser_session=BadPage(),
                run_id=1,
                action_id=2,
                sequence_number=3,
                action=lambda: None,
            )

        with pytest.raises(BrowserRuntimeCaptureError, match="DOM"):
            __import__("asyncio").run(scenario())
        assert list(Path(tmp).iterdir()) == []


class _FakePlaywrightRequest:
    def __init__(self, url: str, method: str = "GET", resource_type: str = "document") -> None:
        self.url = url
        self.method = method
        self.resource_type = resource_type


class _FakePlaywrightResponse:
    def __init__(self, request: _FakePlaywrightRequest, status: int = 200) -> None:
        self.request = request
        self.url = request.url
        self.status = status


class _FakePlaywrightPage:
    def __init__(self) -> None:
        self.url = "https://playwright.test/before"
        self.dom = "<html><button id='go'>before</button></html>"
        self.screenshot_bytes = b"pw-before-shot"
        self.ax = {"role": "button", "name": "before"}
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    async def content(self) -> str:
        return self.dom

    async def screenshot(self, full_page: bool = False) -> bytes:
        assert full_page is True
        return self.screenshot_bytes

    async def accessibility_snapshot(self) -> dict[str, str]:
        return self.ax

    async def click(self, selector: str) -> str:
        assert selector == "#go"
        request = _FakePlaywrightRequest("https://playwright.test/api", "POST", "xhr")
        self.handlers["request"](request)
        self.url = "https://playwright.test/after"
        self.dom = "<html><button id='go'>after</button></html>"
        self.screenshot_bytes = b"pw-after-shot"
        self.ax = {"role": "button", "name": "after"}
        self.handlers["response"](_FakePlaywrightResponse(request, 202))
        return "clicked"


def test_playwright_runtime_session_captures_network_and_artifacts():
    with TemporaryDirectory() as tmp:
        page = _FakePlaywrightPage()

        async def scenario():
            return await capture_playwright_action(
                page=page,
                output_root=Path(tmp),
                run_id=77,
                action_id=88,
                sequence_number=1,
                action=lambda session: session.click("#go"),
            )

        capture = __import__("asyncio").run(scenario())
        network_log = capture.producer_run.artifact_paths["network_log"].read_text()
        metadata = json.loads(capture.producer_run.metadata_path.read_text(encoding="utf-8"))

        assert capture.action_result == "clicked"
        assert capture.before.url == "https://playwright.test/before"
        assert capture.after.url == "https://playwright.test/after"
        assert capture.producer_run.artifact_paths["screenshot_before"].read_bytes() == b"pw-before-shot"
        assert capture.producer_run.artifact_paths["screenshot_after"].read_bytes() == b"pw-after-shot"
        assert '"phase":"request"' in network_log
        assert '"phase":"response"' in network_log
        assert '"status":202' in network_log
        assert metadata["truth_claim"] is False
        assert metadata["verifier"] == "rust-browser-live-collector-run"


def test_playwright_runtime_session_drains_network_deterministically():
    page = _FakePlaywrightPage()
    session = PlaywrightBrowserRuntimeSession(page)
    request = _FakePlaywrightRequest("https://playwright.test/first", "GET", "fetch")
    page.handlers["request"](request)
    page.handlers["response"](_FakePlaywrightResponse(request, 204))

    async def scenario():
        first = await session.drain_network_log()
        second = await session.drain_network_log()
        await session.clear_network_log()
        return first, second

    first, second = __import__("asyncio").run(scenario())
    assert [event["phase"] for event in first] == ["request", "response"]
    assert first[1]["status"] == 204
    assert second == []


def test_browser_ops_bench_runs_artifact_bound_read_only_scorecard():
    with TemporaryDirectory() as tmp:
        bench = BrowserOpsBench(Path(tmp), suite_name="unit-browser-ops")
        task = BrowserOpsBenchTask(
            task_id="click-button",
            run_id=101,
            action_id=202,
            sequence_number=1,
            action=lambda session: session.mutate_after_action(),
            predicates=(
                BrowserOpsBenchPredicate(
                    name="url-after",
                    artifact_kind="url_after",
                    contains_utf8="/after",
                ),
                BrowserOpsBenchPredicate(
                    name="dom-after",
                    artifact_kind="dom_snapshot_after",
                    contains_utf8="after",
                ),
                BrowserOpsBenchPredicate(
                    name="network-api",
                    artifact_kind="network_log",
                    contains_utf8="/api",
                ),
            ),
        )

        async def scenario():
            return await bench.run_tasks((task,), lambda _: _FakeBrowserSession())

        scorecard = __import__("asyncio").run(scenario())
        report_path = bench.write_scorecard(scorecard)
        payload = json.loads(report_path.read_text(encoding="utf-8"))

        assert scorecard.overall_ok
        assert scorecard.passed == 1
        assert scorecard.failed == 0
        assert payload["schema"] == "aegis-browser-ops-bench-scorecard-v1"
        assert payload["truth_claim"] is False
        assert payload["verifier"] == "rust-browser-live-collector-run"
        assert payload["records"][0]["ok"] is True
        assert Path(payload["records"][0]["producer_metadata_path"]).exists()


def test_browser_ops_bench_records_failed_predicate_without_truth_claim():
    with TemporaryDirectory() as tmp:
        bench = BrowserOpsBench(Path(tmp), suite_name="unit-browser-ops")
        task = BrowserOpsBenchTask(
            task_id="missing-dom-token",
            run_id=303,
            action_id=404,
            sequence_number=1,
            action=lambda session: session.mutate_after_action(),
            predicates=(
                BrowserOpsBenchPredicate(
                    name="dom-after-missing",
                    artifact_kind="dom_snapshot_after",
                    contains_utf8="not-present",
                ),
            ),
        )

        async def scenario():
            return await bench.run_task(_FakeBrowserSession(), task)

        record = __import__("asyncio").run(scenario())
        assert not record.ok
        assert record.error == ""
        assert record.predicate_results[0].detail == "substring missing from artifact"
        assert Path(record.producer_metadata_path).exists()


def test_browser_ops_bench_records_capture_error_without_publishing():
    class BadPage:
        url = "https://example.test/bad"

    with TemporaryDirectory() as tmp:
        bench = BrowserOpsBench(Path(tmp), suite_name="unit-browser-ops")
        task = BrowserOpsBenchTask(
            task_id="bad-page",
            run_id=505,
            action_id=606,
            sequence_number=1,
            action=lambda session: None,
            predicates=(
                BrowserOpsBenchPredicate(
                    name="url-after",
                    artifact_kind="url_after",
                    contains_utf8="bad",
                ),
            ),
        )

        async def scenario():
            return await bench.run_task(BadPage(), task)

        record = __import__("asyncio").run(scenario())
        assert not record.ok
        assert "BrowserRuntimeCaptureError" in record.error
        assert record.producer_metadata_path == ""
        assert not (Path(tmp) / "collector-runs").exists()


def test_prompt_builder():
    from aegis_cognition.prompt import PromptBuilder
    
    # Test DEV trust level
    builder = PromptBuilder(trust_level="DEV", task_type="R1")
    prompt = builder.build("Run some local check")
    
    assert "[SYSTEM INSTRUCTIONS - AEGIS-COGNITION PROTOCOL v3.1]" in prompt
    assert "- MỨC ĐỘ TIN CẬY: DEV" in prompt
    assert "- LOẠI NHIỆM VỤ: R1" in prompt
    assert "- LỚP BẢO MẬT: WITNESS" in prompt
    assert "[PHÂN TÍCH NHIỆM VỤ]" in prompt
    assert "[QUY TRÌNH THỰC HIỆN]" in prompt
    assert "[ĐIỀU KIỆN HOÀN THÀNH]" in prompt
    assert "[PHƯƠNG THỨC XỬ LÝ LỖI]" in prompt
    assert "[ĐẦU RA YÊU CẦU]" in prompt
    assert "Mục tiêu chính: Run some local check" in prompt

    # Test PROD trust level
    builder = PromptBuilder(trust_level="PROD", task_type="R4")
    prompt = builder.build("Destructive action")
    assert "- MỨC ĐỘ TIN CẬY: PROD" in prompt
    assert "- LOẠI NHIỆM VỤ: R4" in prompt
    assert "- LỚP BẢO MẬT: PHYSICAL_WITNESS" in prompt
    assert "Yêu cầu bằng chứng vật lý" in prompt
    assert "Yêu cầu sự đồng ý từ 2 người" in prompt


def test_rag_manager():
    from aegis_cognition.rag import RAGManager
    from unittest.mock import MagicMock
    
    mock_lm = MagicMock()
    # Mock search result
    class MockCandidate:
        def __init__(self, ref_hash, segment_id, score, tier):
            self.evidence_ref_hash = ref_hash
            self.segment_id = segment_id
            self.score = score
            self.tier = tier

    class MockSearchResult:
        def __init__(self, results):
            self.results = results

    mock_lm.search_past.return_value = MockSearchResult([
        MockCandidate("aabbcc", 101, 0.985, "ColdVectorExpansion")
    ])
    
    mgr = RAGManager(learning_manager=mock_lm)
    context = mgr.retrieve_and_format("test query")
    
    assert "[RETRIEVED CONTEXT]" in context
    assert "[RETRIEVAL STRATEGY]" in context
    assert "Nguồn dữ liệu: Past Session Transcripts, Evidence Index, AST Signatures" in context
    assert "aabbcc" in context
    assert "Session Segment: 101" in context
    assert "Match Tier: ColdVectorExpansion" in context
    
    # Test empty results
    mock_lm.search_past.return_value = MockSearchResult([])
    context_empty = mgr.retrieve_and_format("test query")
    assert context_empty == ""


def test_nim_client_invoke_methods():
    from core.python.nim_client import NIMClient, NIMConfig
    from unittest.mock import MagicMock, patch
    
    config = NIMConfig(api_key="nvapi-test-key", model="test-model")
    
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        
        # Mock completions response
        mock_choice = MagicMock()
        mock_choice.message.content = "Reply from model"
        mock_choice.finish_reason = "stop"
        
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 15
        
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_response.model = "test-model"
        
        mock_client.chat.completions.create.return_value = mock_response
        
        client = NIMClient(config)
        
        # Test invoke
        reply = client.invoke("Hello model", system_context="Custom system instructions")
        assert reply == "Reply from model"
        mock_client.chat.completions.create.assert_called_once()
        
        # Check system message in call args
        args, kwargs = mock_client.chat.completions.create.call_args
        messages = kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Custom system instructions"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Hello model"
        
        # Test async ainvoke
        reply_async = __import__("asyncio").run(client.ainvoke("Hello model async"))
        assert reply_async == "Reply from model"


def test_learning_manager_bridge_calls():
    from .aegis_adapter import LearningManager
    from unittest.mock import MagicMock
    
    mock_bridge = MagicMock()
    mock_bridge.aegis_get_learning_stats.return_value = '{"schema": "aegis-learning-stats-v1", "ledger_events": 5, "improved_skills": 1, "nudged_memories": 2, "indexed_sessions": 3, "user_models": 0}'
    mock_bridge.aegis_search_past_sessions.return_value = '{"schema": "aegis-session-search-result-v1", "query": "test", "top_k": 5, "count": 1, "results": [{"evidence_ref_hash": "aabbcc", "segment_id": 12, "tier": "ColdVectorExpansion", "score": 0.95, "epoch_hash": "eeff"}], "tier": "ColdVectorExpansion", "gate": "CandidateOnly"}'
    mock_bridge.aegis_index_session.return_value = "aabbcc"
    
    mgr = LearningManager(rust_bridge=mock_bridge)
    
    # Test get_stats
    stats = mgr.get_stats()
    assert stats.ledger_events == 5
    assert stats.improved_skills == 1
    assert stats.nudged_memories == 2
    assert stats.indexed_sessions == 3
    
    # Test search_past
    search = mgr.search_past("test query")
    assert search.count == 1
    assert search.results[0].evidence_ref_hash == "aabbcc"
    assert search.results[0].segment_id == 12
    assert search.results[0].score == 0.95
    
    # Test index_session
    hash_result = mgr.index_session("0x12", "session transcript", 170000000)
    assert hash_result == "aabbcc"
    mock_bridge.aegis_index_session.assert_called_once_with(18, "session transcript", 170000000)

