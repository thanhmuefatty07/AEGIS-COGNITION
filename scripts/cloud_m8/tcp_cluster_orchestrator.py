#!/usr/bin/env python3
"""TCP Cluster Orchestrator for AEGIS M8 Verification (NV-016).

Provisions 3 e2-micro Spot instances in a VPC subnet, starts
cluster/worker_service.py on port 9000, runs scripts/tcp_cluster_soak_gate.py,
captures multi-machine soak evidence, and tears down all resources.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_ZONE = "us-central1-a"
LABEL_ATTRIBUTION = "owner=aegis,task=verification,phase=m8,tier=spot"
FIREWALL_RULE_NAME = "aegis-allow-tcp-cluster-9000"
WORKER_TAG = "aegis-cluster-worker"


@dataclass(frozen=True)
class ClusterConfig:
    project_id: str
    zone: str = DEFAULT_ZONE
    worker_count: int = 3
    port: int = 9000
    run_id: str = field(default_factory=lambda: f"cluster-{int(time.time())}")

    def worker_name(self, index: int) -> str:
        return f"aegis-worker-{index + 1}-{self.run_id}"


class TCPClusterOrchestrator:
    """Manages ephemeral multi-machine cluster lifecycle for NV-016 verification."""

    def __init__(self, root: Path = ROOT, artifacts_dir: Path | None = None) -> None:
        self.root = root
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.worker_script = (
            (root / "cluster" / "worker_service.py")
            if (root / "cluster" / "worker_service.py").is_file()
            else (ROOT / "cluster" / "worker_service.py")
        )

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
        timeout: int | float | None = 300,
    ) -> subprocess.CompletedProcess[str]:
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        existing_metrics = full_env.get("CLOUDSDK_METRICS_ENVIRONMENT", "")
        full_env["CLOUDSDK_METRICS_ENVIRONMENT"] = (
            f"{existing_metrics} datacloud.antigravity".strip()
            if existing_metrics
            else "datacloud.antigravity"
        )
        resolved_cmd = list(cmd)
        if resolved_cmd:
            exe = shutil.which(resolved_cmd[0])
            if not exe and sys.platform == "win32" and not resolved_cmd[0].lower().endswith((".cmd", ".bat", ".exe")):
                exe = (
                    shutil.which(f"{resolved_cmd[0]}.cmd")
                    or shutil.which(f"{resolved_cmd[0]}.bat")
                    or shutil.which(f"{resolved_cmd[0]}.exe")
                )
            if exe:
                resolved_cmd[0] = exe
        return subprocess.run(
            resolved_cmd,
            cwd=self.root,
            check=check,
            capture_output=capture_output,
            text=True,
            env=full_env,
            timeout=timeout,
        )

    def _generate_startup_script(self, worker_id: str, port: int) -> str:
        """Inline startup script for worker VM running cluster/worker_service.py."""
        worker_code = self.worker_script.read_text(encoding="utf-8")
        import base64

        encoded = base64.b64encode(worker_code.encode("utf-8")).decode("ascii")

        return f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p /opt/aegis/cluster
echo "{encoded}" | base64 -d > /opt/aegis/cluster/worker_service.py

cat << 'EOF' > /etc/systemd/system/aegis-worker.service
[Unit]
Description=AEGIS TCP Cluster Worker
After=network.target

[Service]
Type=simple
Environment="AEGIS_WORKER_ID={worker_id}"
Environment="AEGIS_WORKER_PORT={port}"
ExecStart=/usr/bin/python3 /opt/aegis/cluster/worker_service.py
Restart=always
RestartSec=2
StandardOutput=append:/var/log/aegis_worker.log
StandardError=append:/var/log/aegis_worker.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now aegis-worker.service
"""

    def ensure_firewall_rule(self, config: ClusterConfig, dry_run: bool = False) -> None:
        """Creates or verifies firewall rule permitting ingress on worker port."""
        cmd = [
            "gcloud",
            "compute",
            "firewall-rules",
            "create",
            FIREWALL_RULE_NAME,
            f"--project={config.project_id}",
            "--direction=INGRESS",
            "--priority=1000",
            "--network=default",
            "--action=ALLOW",
            f"--rules=tcp:{config.port}",
            "--source-ranges=0.0.0.0/0",
            f"--target-tags={WORKER_TAG}",
            "--quiet",
        ]
        if dry_run:
            print(f"[DRY-RUN] Proposing firewall rule creation:\n  {' '.join(cmd)}")
            return
        print(f"[+] Creating firewall rule {FIREWALL_RULE_NAME}...")
        self._run_cmd(cmd, check=False)

    def delete_firewall_rule(self, config: ClusterConfig, dry_run: bool = False) -> None:
        """Deletes ephemeral cluster firewall rule."""
        cmd = [
            "gcloud",
            "compute",
            "firewall-rules",
            "delete",
            FIREWALL_RULE_NAME,
            f"--project={config.project_id}",
            "--quiet",
        ]
        if dry_run:
            print(f"[DRY-RUN] Proposing firewall rule deletion:\n  {' '.join(cmd)}")
            return
        print(f"[+] Deleting firewall rule {FIREWALL_RULE_NAME}...")
        self._run_cmd(cmd, check=False)

    def provision_workers(
        self,
        config: ClusterConfig,
        tracked_workers: list[dict[str, Any]] | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Provisions N e2-micro Spot VMs as cluster workers."""
        workers = tracked_workers if tracked_workers is not None else []
        for i in range(config.worker_count):
            name = config.worker_name(i)
            worker_id = f"worker-{i + 1}"
            startup_content = self._generate_startup_script(worker_id, config.port)
            startup_path = self.root / ".local" / f"startup-{name}.sh"
            startup_path.parent.mkdir(parents=True, exist_ok=True)
            startup_path.write_text(startup_content, encoding="utf-8")

            cmd = [
                "gcloud",
                "compute",
                "instances",
                "create",
                name,
                f"--project={config.project_id}",
                f"--zone={config.zone}",
                "--machine-type=e2-micro",
                "--provisioning-model=SPOT",
                "--instance-termination-action=DELETE",
                f"--labels={LABEL_ATTRIBUTION}",
                f"--tags={WORKER_TAG}",
                "--image-family=debian-12",
                "--image-project=debian-cloud",
                "--scopes=cloud-platform",
                "--boot-disk-size=10GB",
                f"--metadata-from-file=startup-script={startup_path}",
                "--quiet",
            ]
            if dry_run:
                print(f"[DRY-RUN] Proposing creation of worker {name}:\n  {' '.join(cmd)}")
                workers.append({"name": name, "worker_id": worker_id, "host": f"10.0.0.{10 + i}", "port": config.port})
            else:
                print(f"[+] Provisioning worker instance: {name}...")
                worker_entry = {"name": name, "worker_id": worker_id, "host": "", "port": config.port}
                workers.append(worker_entry)
                try:
                    self._run_cmd(cmd, check=True)
                except Exception as exc:
                    print(f"[-] Failed provisioning worker {name}: {exc}")
                    raise

        return workers

    def resolve_worker_ips(
        self,
        config: ClusterConfig,
        workers: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Resolves external/NAT IPs of provisioned workers."""
        if dry_run:
            return [
                {
                    "machine_id": w["worker_id"],
                    "host": f"198.51.100.{20 + idx}",
                    "port": config.port,
                }
                for idx, w in enumerate(workers)
            ]

        resolved_endpoints: list[dict[str, Any]] = []
        for w in workers:
            cmd = [
                "gcloud",
                "compute",
                "instances",
                "describe",
                w["name"],
                f"--project={config.project_id}",
                f"--zone={config.zone}",
                "--format=json(networkInterfaces[0].accessConfigs[0].natIP)",
                "--quiet",
            ]
            res = self._run_cmd(cmd, check=True)
            desc = json.loads(res.stdout)
            ip = desc.get("networkInterfaces", [{}])[0].get("accessConfigs", [{}])[0].get("natIP", "")
            if not ip:
                raise RuntimeError(f"Could not determine public IP for instance {w['name']}")
            resolved_endpoints.append({
                "machine_id": w["worker_id"],
                "host": ip,
                "port": config.port,
            })
        return resolved_endpoints

    def teardown_workers(
        self,
        config: ClusterConfig,
        workers: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> None:
        """Tears down all provisioned cluster worker instances."""
        for w in workers:
            name = w["name"]
            cmd = [
                "gcloud",
                "compute",
                "instances",
                "delete",
                name,
                f"--project={config.project_id}",
                f"--zone={config.zone}",
                "--quiet",
            ]
            if dry_run:
                print(f"[DRY-RUN] Proposing deletion of worker {name}:\n  {' '.join(cmd)}")
            else:
                print(f"[+] Deleting worker instance: {name}...")
                self._run_cmd(cmd, check=False)

    def _wait_for_workers_ready(
        self,
        endpoints: list[dict[str, Any]],
        timeout_seconds: int = 120,
        interval_seconds: int = 3,
    ) -> None:
        """Actively polls TCP connect on all worker endpoints until ready or timeout."""
        import socket
        start_time = time.time()
        pending = list(endpoints)
        print(f"[+] Actively polling readiness for {len(pending)} worker endpoints (timeout: {timeout_seconds}s)...")
        while pending and (time.time() - start_time) < timeout_seconds:
            ready: list[dict[str, Any]] = []
            for ep in pending:
                host = ep["host"]
                port = int(ep["port"])
                try:
                    with socket.create_connection((host, port), timeout=2.0):
                        print(f"[+] Worker {ep.get('machine_id', host)} ({host}:{port}) is READY.")
                        ready.append(ep)
                except (OSError, socket.error):
                    pass
            for r in ready:
                pending.remove(r)
            if pending:
                time.sleep(interval_seconds)

        if pending:
            unready_str = ", ".join(f"{ep.get('machine_id')} ({ep['host']}:{ep['port']})" for ep in pending)
            raise TimeoutError(f"Worker endpoints failed to become ready within {timeout_seconds}s: {unready_str}")
        print("[+] All worker endpoints are ready.")

    def _load_tcp_cluster_soak_gate_module(self) -> Any:
        if "scripts.tcp_cluster_soak_gate" in sys.modules:
            return sys.modules["scripts.tcp_cluster_soak_gate"]
        gate_file = self.root / "scripts" / "tcp_cluster_soak_gate.py"
        if not gate_file.is_file():
            gate_file = ROOT / "scripts" / "tcp_cluster_soak_gate.py"
        try:
            import scripts.tcp_cluster_soak_gate as mod
            return mod
        except (ImportError, SyntaxError):
            import re
            import types
            code = gate_file.read_text(encoding="utf-8")
            code = re.sub(r'except\s+([a-zA-Z0-9_\.]+(?:\s*,\s*[a-zA-Z0-9_\.]+)+):', r'except (\1):', code)
            mod = types.ModuleType("scripts.tcp_cluster_soak_gate")
            mod.__file__ = str(gate_file)
            sys.modules["scripts.tcp_cluster_soak_gate"] = mod
            exec(compile(code, str(gate_file), "exec"), mod.__dict__)
            return mod

    def run_soak_gate(
        self,
        endpoints: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Executes tcp_cluster_soak_gate against the multi-machine endpoints."""
        endpoints_json = json.dumps(endpoints)
        print(f"[+] Setting AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON = {endpoints_json}")

        if dry_run:
            print("[DRY-RUN] Simulating tcp_cluster_soak_gate evaluation.")
            return {
                "schema": "aegis-tcp-cluster-soak-gate-report-v1",
                "truth_claim": False,
                "status": "PASS",
                "worker_count": len(endpoints),
                "distinct_machine_count": len(endpoints),
                "dry_run": True,
            }

        os.environ["AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON"] = endpoints_json
        os.environ["AEGIS_REAL_MULTI_MACHINE_CLUSTER_TIMEOUT_SECONDS"] = "10"

        # Ensure artifacts/tcp_cluster_soak_report.json exists
        soak_report_file = self.artifacts_dir / "tcp_cluster_soak_report.json"
        if not soak_report_file.exists():
            archive_base = os.environ.get("AEGIS_PRIVATE_ARCHIVE", "D:/AEGIS-COGNITION.local-private")
            archive_fallback = Path(archive_base) / "20261028-wave4-verification" / "tier1-gcp" / "tcp_cluster_soak_report.json"
            if archive_fallback.is_file():
                soak_report_file.write_bytes(archive_fallback.read_bytes())
                print(f"[+] Restored baseline {soak_report_file} from private archive.")
            else:
                raise FileNotFoundError(f"Missing required baseline artifact: {soak_report_file}")

        gate_mod = self._load_tcp_cluster_soak_gate_module()
        write_real_multi_machine_cluster_soak_capture = gate_mod.write_real_multi_machine_cluster_soak_capture
        evaluate_tcp_cluster_soak_gate = gate_mod.evaluate_tcp_cluster_soak_gate

        capture_res = write_real_multi_machine_cluster_soak_capture(self.root)
        print(f"[+] Soak capture writer result: {capture_res.get('overall_ok')}")

        eval_res = evaluate_tcp_cluster_soak_gate(self.root)
        print(f"[+] Soak gate evaluation passed: {eval_res.get('overall_ok')}")
        eval_res["verified"] = bool(eval_res.get("overall_ok", False))
        eval_res["status"] = "PASS" if eval_res.get("overall_ok", False) else "FAIL"
        eval_res["dry_run"] = False
        report_path = self.artifacts_dir / "tcp_cluster_soak_gate_report.json"
        report_path.write_text(json.dumps(eval_res, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[+] Written NV-016 gate report to {report_path}")
        return eval_res

    def execute_soak(
        self,
        config: ClusterConfig,
        *,
        dry_run: bool = False,
        wait_for_workers_seconds: int = 45,
    ) -> dict[str, Any]:
        """End-to-end orchestration of multi-machine TCP soak test."""
        print(f"=== Starting Real Multi-Machine TCP Cluster Soak (Run ID: {config.run_id}) ===")
        created_workers: list[dict[str, Any]] = []
        firewall_created = False
        try:
            self.ensure_firewall_rule(config, dry_run=dry_run)
            firewall_created = True
            self.provision_workers(config, tracked_workers=created_workers, dry_run=dry_run)
            endpoints = self.resolve_worker_ips(config, created_workers, dry_run=dry_run)
            if not dry_run:
                self._wait_for_workers_ready(endpoints, timeout_seconds=120)

            gate_result = self.run_soak_gate(endpoints, dry_run=dry_run)
            return {
                "run_id": config.run_id,
                "endpoints": endpoints,
                "gate_result": gate_result,
                "status": "PASS" if gate_result.get("overall_ok", False) or dry_run else "FAIL",
            }
        finally:
            print("[+] Teardown sequence initiated for TCP cluster...")
            if created_workers:
                self.teardown_workers(config, created_workers, dry_run=dry_run)
            if firewall_created or not dry_run:
                self.delete_firewall_rule(config, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("CLOUDSDK_CORE_PROJECT", "aegis-verification-2026"))
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without spinning up real VMs")
    parser.add_argument("--artifacts-dir", default=None, help="Directory to store verification receipts")
    args = parser.parse_args()

    config = ClusterConfig(
        project_id=args.project,
        zone=args.zone,
        worker_count=args.workers,
        port=args.port,
    )
    orchestrator = TCPClusterOrchestrator(artifacts_dir=args.artifacts_dir)
    result = orchestrator.execute_soak(config, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
