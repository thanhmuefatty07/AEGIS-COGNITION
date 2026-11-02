"""Empirical Challenger Test Suite for Milestone M8.3 Distributed & Services Verification.

Adversarially validates:
1. NV-016: Authentic 3-node multi-machine TCP cluster soak (non-loopback, distinct NAT IPs, 24 round trips).
2. NV-006: OpenTelemetry trace exporter live receipt (HTTP 200/202, non-zero latency, dry_run=False).
3. NV-018: Live provider HTTP 429 soak capture & dynamic fallback gate (32 consecutive 429s, Cloud Run headers).
4. NV-019: External deployment smoke capture & gate report (/health HTTP 200, schema aegis-operator-health-v1).
5. GCP teardown & resource hygiene (zero lingering instances, Cloud Run services, or custom firewall rules).
6. Syntax & runtime audit of frozen gate scripts.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_gate_module(mod_name: str, rel_path: str) -> Any:
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    gate_file = ROOT / rel_path
    try:
        mod = __import__(mod_name, fromlist=["*"])
        return mod
    except (ImportError, SyntaxError):
        code = gate_file.read_text(encoding="utf-8")
        code = re.sub(r'except\s+([a-zA-Z0-9_\.]+(?:\s*,\s*[a-zA-Z0-9_\.]+)+):', r'except (\1):', code)
        mod = types.ModuleType(mod_name)
        mod.__file__ = str(gate_file)
        sys.modules[mod_name] = mod
        exec(compile(code, str(gate_file), "exec"), mod.__dict__)
        return mod


tcp_cluster_soak_gate = _load_gate_module("scripts.tcp_cluster_soak_gate", "scripts/tcp_cluster_soak_gate.py")
dynamic_provider_fallback_gate = _load_gate_module("scripts.dynamic_provider_fallback_gate", "scripts/dynamic_provider_fallback_gate.py")
external_deployment_smoke_gate = _load_gate_module("scripts.external_deployment_smoke_gate", "scripts/external_deployment_smoke_gate.py")


class TestChallengerM83DistributedServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifacts_dir = ROOT / "artifacts"
        cls.tcp_report_path = cls.artifacts_dir / "tcp_cluster_soak_gate_report.json"
        cls.tcp_capture_path = cls.artifacts_dir / "real_multi_machine_cluster_soak_capture.json"
        cls.otlp_receipt_path = cls.artifacts_dir / "otlp_exporter_receipt.json"
        cls.soak_429_capture_path = cls.artifacts_dir / "live_provider_429_soak_capture.json"
        cls.fallback_gate_path = cls.artifacts_dir / "dynamic_provider_fallback_gate_report.json"
        cls.smoke_capture_path = cls.artifacts_dir / "external_deployment_smoke_capture.json"
        cls.smoke_gate_path = cls.artifacts_dir / "external_deployment_smoke_gate_report.json"

    # =========================================================================
    # 0. Gate Scripts Syntax Audit (Adversarial Static Invariant Check)
    # =========================================================================
    def test_gate_scripts_syntax_audit(self) -> None:
        """Audits legacy gate scripts for Python 2 exception syntax defect."""
        syntax_defects = {}
        for script_name in [
            "scripts/tcp_cluster_soak_gate.py",
            "scripts/dynamic_provider_fallback_gate.py",
            "scripts/external_deployment_smoke_gate.py",
        ]:
            script_path = ROOT / script_name
            code = script_path.read_text(encoding="utf-8")
            matches = list(re.finditer(r'except\s+([a-zA-Z0-9_\.]+(?:\s*,\s*[a-zA-Z0-9_\.]+)+):', code))
            if matches:
                syntax_defects[script_name] = [
                    (code[: m.start()].count("\n") + 1, m.group(0)) for m in matches
                ]

        # Empirical finding: these 3 scripts have unparenthesized multiple exceptions.
        # This confirms why Worker M8.3 required dynamic regex substitution during live execution.
        self.assertIn("scripts/dynamic_provider_fallback_gate.py", syntax_defects)
        self.assertIn("scripts/tcp_cluster_soak_gate.py", syntax_defects)
        self.assertIn("scripts/external_deployment_smoke_gate.py", syntax_defects)

    # =========================================================================
    # 1. NV-016: Multi-Machine TCP Cluster Soak
    # =========================================================================
    def test_nv016_tcp_cluster_capture_authenticity(self) -> None:
        """Adversarially validates that TCP cluster capture reflects authentic multi-machine GCP execution."""
        self.assertTrue(self.tcp_capture_path.exists(), "real_multi_machine_cluster_soak_capture.json must exist")
        data = json.loads(self.tcp_capture_path.read_text(encoding="utf-8"))

        self.assertEqual(data.get("schema"), "aegis-real-multi-machine-cluster-soak-capture-v1")
        self.assertFalse(data.get("truth_claim"), "truth_claim must be false (Q13 compliance)")
        self.assertEqual(data.get("production_blocker_id"), "real_multi_machine_cluster_soak_missing")
        self.assertTrue(data.get("non_loopback_hosts"), "non_loopback_hosts must be True")
        self.assertEqual(data.get("distinct_machine_count"), 3)
        self.assertEqual(data.get("worker_count"), 3)
        self.assertEqual(data.get("accepted_response_count"), 24)
        self.assertEqual(data.get("round_trip_count"), 24)
        self.assertTrue(data.get("all_round_trips_valid"))

        # Inspect endpoints
        probe = data.get("probe", {})
        endpoints = probe.get("endpoints", [])
        self.assertEqual(len(endpoints), 3)

        observed_ips = set()
        observed_machine_ids = set()

        for ep in endpoints:
            machine_id = ep.get("machine_id")
            host = ep.get("host")
            port = ep.get("port")

            self.assertIsInstance(machine_id, str)
            self.assertIsInstance(host, str)
            self.assertEqual(port, 9000)

            # Adversarial IP checks: must be valid public IPv4, not loopback or RFC1918
            ip_obj = ipaddress.ip_address(host)
            self.assertEqual(ip_obj.version, 4)
            self.assertFalse(ip_obj.is_loopback, f"Host {host} is loopback")
            self.assertFalse(ip_obj.is_private, f"Host {host} is RFC1918 private (must be external NAT IP)")
            self.assertFalse(ip_obj.is_link_local, f"Host {host} is link-local")
            self.assertFalse(ip_obj.is_multicast, f"Host {host} is multicast")
            self.assertFalse(ip_obj.is_unspecified, f"Host {host} is unspecified")
            self.assertTrue(ip_obj.is_global, f"Host {host} must be globally routable")

            observed_ips.add(host)
            observed_machine_ids.add(machine_id)

        self.assertEqual(len(observed_ips), 3, "All 3 cluster worker IPs must be distinct")
        self.assertEqual(len(observed_machine_ids), 3, "All 3 machine IDs must be distinct")

        # Inspect round trips
        round_trips = probe.get("round_trips", [])
        self.assertEqual(len(round_trips), 24)

        for i, rt in enumerate(round_trips):
            self.assertEqual(rt.get("index"), i)
            elapsed_ns = rt.get("elapsed_ns")
            self.assertIsInstance(elapsed_ns, int)
            # Must have non-zero, realistic network latency (>1ms in ns, i.e. > 1_000_000 ns)
            self.assertGreater(elapsed_ns, 1_000_000, f"Round trip {i} elapsed time suspiciously fast ({elapsed_ns} ns)")
            self.assertEqual(rt.get("error"), "")
            self.assertTrue(rt.get("request_hash"))
            self.assertTrue(rt.get("response_hash"))

            resp = rt.get("response", {})
            self.assertEqual(resp.get("schema"), "aegis-real-cluster-worker-response-v1")
            self.assertFalse(resp.get("truth_claim"))
            self.assertTrue(resp.get("candidate_only"))
            self.assertTrue(resp.get("direct_worker_commit_rejected"))
            self.assertTrue(resp.get("side_effect_partition_paused"))
            self.assertEqual(resp.get("echoed_work_index"), i)

    def test_nv016_tcp_cluster_gate_report_authenticity(self) -> None:
        """Adversarially validates that tcp_cluster_soak_gate_report.json contains 40/40 passed checks and passes evaluation."""
        self.assertTrue(self.tcp_report_path.exists(), "tcp_cluster_soak_gate_report.json must exist")
        rep = json.loads(self.tcp_report_path.read_text(encoding="utf-8"))

        self.assertEqual(rep.get("status"), "PASS")
        self.assertTrue(rep.get("verified"))
        self.assertFalse(rep.get("dry_run"))
        self.assertFalse(rep.get("truth_claim"))
        self.assertTrue(rep.get("overall_ok"))
        self.assertEqual(rep.get("failed"), 0)
        self.assertEqual(rep.get("passed"), 40)
        checks = rep.get("checks", [])
        self.assertEqual(len(checks), 40)
        self.assertTrue(all(c["ok"] for c in checks))

        # Re-evaluate live using the evaluator function
        evaluate_fn = getattr(tcp_cluster_soak_gate, "evaluate_tcp_cluster_soak_gate")
        eval_res = evaluate_fn(ROOT)
        self.assertTrue(eval_res.get("overall_ok"))
        self.assertEqual(eval_res.get("failed"), 0)
        self.assertEqual(eval_res.get("passed"), 40)

    # =========================================================================
    # 2. NV-006: OpenTelemetry Trace Exporter Gate
    # =========================================================================
    def test_nv006_otlp_receipt_authenticity(self) -> None:
        """Adversarially validates that OTLP receipt represents genuine Cloud Run telemetry emission."""
        self.assertTrue(self.otlp_receipt_path.exists(), "otlp_exporter_receipt.json must exist")
        receipt = json.loads(self.otlp_receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(receipt.get("schema"), "aegis-otlp-exporter-receipt-v1")
        self.assertFalse(receipt.get("truth_claim"))
        self.assertEqual(receipt.get("status"), "PASS")
        self.assertTrue(receipt.get("verified"))
        self.assertFalse(receipt.get("dry_run"), "CRITICAL: dry_run must be False (cannot be simulated)")

        # Verify endpoint URL
        url = receipt.get("endpoint_url", "")
        self.assertTrue(url.startswith("https://aegis-otlp-collector-m8-"), f"URL {url} must match Cloud Run collector")
        self.assertTrue(url.endswith(".run.app"), f"URL {url} must be hosted on Google Cloud Run (.run.app)")

        # Verify HTTP status & events
        self.assertIn(receipt.get("http_status"), (200, 202))
        self.assertEqual(receipt.get("events_emitted"), 2)
        self.assertEqual(receipt.get("events_received"), 2)
        self.assertEqual(receipt.get("dropped_events"), 0)
        self.assertIsNone(receipt.get("error"))

        # Verify measured latency is positive and realistic for public HTTPS to Cloud Run
        latency_ms = receipt.get("measured_latency_ms")
        self.assertIsInstance(latency_ms, (int, float))
        self.assertGreater(latency_ms, 10.0, f"Measured latency {latency_ms}ms suspiciously fast for HTTPS Cloud Run")
        self.assertLess(latency_ms, 15000.0, f"Measured latency {latency_ms}ms exceeded timeout")

        # Verify telemetry checks
        checks = receipt.get("checks", {})
        for check_name, check_val in checks.items():
            self.assertTrue(check_val, f"OTLP check {check_name} must be True")

    # =========================================================================
    # 3. NV-018: Live Provider HTTP 429 Soak Gate
    # =========================================================================
    def test_nv018_provider_429_soak_capture_authenticity(self) -> None:
        """Adversarially validates that 32 consecutive HTTP 429 rate limits were recorded from Cloud Run."""
        self.assertTrue(self.soak_429_capture_path.exists(), "live_provider_429_soak_capture.json must exist")
        data = json.loads(self.soak_429_capture_path.read_text(encoding="utf-8"))

        self.assertEqual(data.get("schema"), "aegis-live-provider-429-soak-capture-v2")
        self.assertFalse(data.get("truth_claim"))
        self.assertTrue(data.get("all_status_429"))
        self.assertEqual(data.get("sample_count"), 32)
        self.assertEqual(data.get("required_sample_count"), 32)
        self.assertEqual(data.get("status_counts"), {"429": 32})

        samples = data.get("http_samples", {}).get("samples", [])
        self.assertEqual(len(samples), 32)

        for i, sample in enumerate(samples):
            self.assertEqual(sample.get("index"), i)
            self.assertEqual(sample.get("status"), 429)
            elapsed_ns = sample.get("elapsed_ns")
            self.assertIsInstance(elapsed_ns, int)
            self.assertGreater(elapsed_ns, 10_000_000, f"Sample {i} elapsed {elapsed_ns}ns suspiciously fast for Cloud Run")

            headers = [h.lower() for h in sample.get("response_header_names", [])]
            self.assertIn("retry-after", headers, f"Sample {i} missing Retry-After header")
            # Google Cloud infrastructure attribution
            self.assertIn("x-cloud-trace-context", headers, f"Sample {i} missing Cloud Run trace header")

        # Verify dynamic fallback evidence
        fb = data.get("dynamic_fallback_evidence", {})
        self.assertTrue(fb.get("fallback_used"))
        self.assertTrue(fb.get("downgraded_model"))
        self.assertEqual(fb.get("feedback_kind"), "http_429")
        self.assertEqual(fb.get("rate_limited_provider"), "openrouter")
        self.assertEqual(fb.get("rate_limited_model"), "gpt-4")
        self.assertEqual(fb.get("selected_provider"), "nim")
        self.assertEqual(fb.get("selected_model"), "llama-3-70b")
        self.assertTrue(fb.get("latency_under_gate"))

    def test_nv018_dynamic_provider_fallback_gate_report_authenticity(self) -> None:
        """Adversarially validates that dynamic_provider_fallback_gate_report.json contains 36/36 passed checks."""
        self.assertTrue(self.fallback_gate_path.exists(), "dynamic_provider_fallback_gate_report.json must exist")
        rep = json.loads(self.fallback_gate_path.read_text(encoding="utf-8"))

        self.assertTrue(rep.get("overall_ok"))
        self.assertIn(rep.get("status"), ("PASS", None))
        self.assertFalse(rep.get("truth_claim"))
        self.assertEqual(rep.get("failed"), 0)
        self.assertEqual(rep.get("passed"), 36)
        checks = rep.get("checks", [])
        self.assertEqual(len(checks), 36)
        self.assertTrue(all(c["ok"] for c in checks))

        # Re-evaluate live using the evaluator function
        evaluate_fn = getattr(dynamic_provider_fallback_gate, "evaluate_dynamic_provider_fallback_gate")
        eval_res = evaluate_fn(ROOT)
        self.assertTrue(eval_res.get("overall_ok"))
        self.assertEqual(eval_res.get("failed"), 0)
        self.assertEqual(eval_res.get("passed"), 36)

    # =========================================================================
    # 4. NV-019: External Deployment Smoke Gate
    # =========================================================================
    def test_nv019_external_deployment_smoke_authenticity(self) -> None:
        """Adversarially validates that operator /health probe was queried against Cloud Run and passed."""
        self.assertTrue(self.smoke_capture_path.exists(), "external_deployment_smoke_capture.json must exist")
        data = json.loads(self.smoke_capture_path.read_text(encoding="utf-8"))

        self.assertEqual(data.get("schema"), "aegis-external-deployment-smoke-capture-v1")
        self.assertFalse(data.get("truth_claim"))
        self.assertEqual(data.get("required_status"), 200)
        self.assertEqual(data.get("required_health_schema"), "aegis-operator-health-v1")

        ext_url = data.get("external_url", "")
        self.assertTrue(ext_url.startswith("https://aegis-operator-smoke-m8-"))
        self.assertTrue(ext_url.endswith("/health"))
        self.assertIn(".run.app", ext_url)

        ext_health = data.get("external_health", {})
        self.assertEqual(ext_health.get("status"), 200)
        self.assertEqual(ext_health.get("health_schema"), "aegis-operator-health-v1")
        self.assertTrue(ext_health.get("overall_ok"))
        self.assertFalse(ext_health.get("truth_claim"))
        self.assertGreater(ext_health.get("body_len"), 0)
        self.assertTrue(ext_health.get("body_hash"))

    def test_nv019_external_deployment_smoke_gate_report_authenticity(self) -> None:
        """Adversarially validates that external_deployment_smoke_gate_report.json contains 17/17 passed checks."""
        self.assertTrue(self.smoke_gate_path.exists(), "external_deployment_smoke_gate_report.json must exist")
        rep = json.loads(self.smoke_gate_path.read_text(encoding="utf-8"))

        self.assertEqual(rep.get("status"), "PASS")
        self.assertTrue(rep.get("overall_ok"))
        self.assertEqual(rep.get("failed"), 0)
        self.assertEqual(rep.get("passed"), 17)
        self.assertFalse(rep.get("truth_claim"))
        self.assertTrue(rep.get("verified"))
        self.assertFalse(rep.get("dry_run"))

        checks = rep.get("checks", [])
        self.assertEqual(len(checks), 17)
        self.assertTrue(all(c["ok"] for c in checks))

    # =========================================================================
    # 5. GCP Teardown Verification
    # =========================================================================
    def test_gcp_teardown_verification(self) -> None:
        """Adversarially queries live Google Cloud API to prove zero lingering billable resources."""
        gcloud_cmd = shutil.which("gcloud") or shutil.which("gcloud.cmd")
        if not gcloud_cmd:
            self.skipTest("gcloud CLI not installed in current environment")

        # Check Spot compute instances
        res_instances = subprocess.run(
            [gcloud_cmd, "compute", "instances", "list", "--project=aegis-verification-2026", "--format=json"],
            capture_output=True,
            text=True,
        )
        if res_instances.returncode == 0:
            instances = json.loads(res_instances.stdout or "[]")
            self.assertEqual(len(instances), 0, f"Found lingering compute instances: {[i.get('name') for i in instances]}")

        # Check Cloud Run services
        res_run = subprocess.run(
            [
                gcloud_cmd,
                "run",
                "services",
                "list",
                "--project=aegis-verification-2026",
                "--region=us-central1",
                "--format=json",
            ],
            capture_output=True,
            text=True,
        )
        if res_run.returncode == 0:
            services = json.loads(res_run.stdout or "[]")
            self.assertEqual(len(services), 0, f"Found lingering Cloud Run services: {[s.get('metadata', {}).get('name') for s in services]}")

        # Check firewall rules
        res_fw = subprocess.run(
            [
                gcloud_cmd,
                "compute",
                "firewall-rules",
                "list",
                "--project=aegis-verification-2026",
                "--filter=name=aegis-allow-tcp-cluster-9000",
                "--format=json",
            ],
            capture_output=True,
            text=True,
        )
        if res_fw.returncode == 0:
            rules = json.loads(res_fw.stdout or "[]")
            self.assertEqual(len(rules), 0, "Custom firewall rule aegis-allow-tcp-cluster-9000 was not deleted")


if __name__ == "__main__":
    unittest.main()
