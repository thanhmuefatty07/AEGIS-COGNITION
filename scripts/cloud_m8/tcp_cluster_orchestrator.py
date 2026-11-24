#!/usr/bin/env python3
"""TCP Cluster Orchestrator for AEGIS M8 Verification (NV-016).

Provisions 3 `--no-address` Spot instances in the default VPC subnet, restricts
port 9000 firewall ingress strictly to VPC-internal CIDR (`10.128.0.0/9`) plus
IAP (`35.235.240.0/20`), runs `cluster/worker_service.py` under pinned CPython
3.14.7 (via uv), runs `scripts/tcp_cluster_soak_gate.py`, and tears down all VMs
and firewall rules cleanly.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloud_m8.provenance import (  # noqa: E402
    DEFAULT_PROJECT,
    DEFAULT_PYTHON_VERSION,
    DEFAULT_ZONE,
    IAP_TCP_CIDR,
    LABEL_ATTRIBUTION,
    VPC_INTERNAL_CIDR,
    PrivateArchiveUnavailableError,
    resolve_private_archive,
    run_cli_cmd,
)

FIREWALL_RULE_NAME = "aegis-allow-tcp-cluster-9000"
WORKER_TAG = "aegis-cluster-worker"


@dataclass(frozen=True)
class ClusterConfig:
    project_id: str = DEFAULT_PROJECT
    zone: str = DEFAULT_ZONE
    worker_count: int = 3
    port: int = 9000
    run_id: str = field(default_factory=lambda: f"cluster-{int(time.time())}")
    no_address: bool = True
    machine_type: str = "e2-micro"
    boot_disk_type: str = "pd-balanced"
    boot_disk_size_gb: int = 10

    def worker_name(self, index: int) -> str:
        return f"aegis-worker-{index + 1}-{self.run_id}"


class TCPClusterOrchestrator:
    """Manages ephemeral VPC-internal multi-machine cluster lifecycle for NV-016."""

    def __init__(self, root: Path = ROOT, artifacts_dir: Path | str | None = None) -> None:
        self.root = root
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (root / "artifacts")
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.worker_script = (
            (root / "cluster" / "worker_service.py")
            if (root / "cluster" / "worker_service.py").is_file()
            else (ROOT / "cluster" / "worker_service.py")
        )

    @property
    def _local_dir(self) -> Path:
        if self.artifacts_dir.resolve() != (self.root / "artifacts").resolve():
            return self.artifacts_dir / ".local"
        return self.root / ".local"

    def _run_cmd(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
        timeout: int | float | None = 300,
    ) -> subprocess.CompletedProcess[str]:
        return run_cli_cmd(
            cmd,
            cwd=self.root,
            env=env,
            check=check,
            capture_output=capture_output,
            timeout=timeout,
        )

    def _generate_startup_script(self, worker_id: str, port: int) -> str:
        """Build startup script for worker VM running cluster/worker_service.py on CPython 3.14.7."""
        worker_code = self.worker_script.read_text(encoding="utf-8")
        encoded = base64.b64encode(worker_code.encode("utf-8")).decode("ascii")

        return f"""#!/usr/bin/env bash
set -euo pipefail
mkdir -p /opt/aegis/cluster
echo "{encoded}" | base64 -d > /opt/aegis/cluster/worker_service.py

export HOME="/root"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:${{PATH}}"
fi
uv python install {DEFAULT_PYTHON_VERSION}
PYTHON_BIN="$(uv python find {DEFAULT_PYTHON_VERSION})"

cat << EOF > /etc/systemd/system/aegis-worker.service
[Unit]
Description=AEGIS TCP Cluster Worker
After=network.target

[Service]
Type=simple
Environment="AEGIS_WORKER_ID={worker_id}"
Environment="AEGIS_WORKER_PORT={port}"
ExecStart=${{PYTHON_BIN}} /opt/aegis/cluster/worker_service.py
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

    def build_firewall_create_command(self, config: ClusterConfig) -> list[str]:
        """Build VPC-internal + IAP restricted firewall rule command (zero 0.0.0.0/0 exposure)."""
        return [
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
            f"--source-ranges={VPC_INTERNAL_CIDR},{IAP_TCP_CIDR}",
            f"--target-tags={WORKER_TAG}",
            "--quiet",
        ]

    def ensure_firewall_rule(self, config: ClusterConfig, dry_run: bool = False) -> None:
        """Create or verify VPC-internal + IAP firewall rule permitting ingress on worker port."""
        cmd = self.build_firewall_create_command(config)
        if dry_run:
            print(f"[DRY-RUN] Proposing firewall rule creation:\n  {' '.join(cmd)}")
            return
        print(f"[+] Creating VPC-internal/IAP firewall rule {FIREWALL_RULE_NAME}...")
        self._run_cmd(cmd, check=False)

    def delete_firewall_rule(self, config: ClusterConfig, dry_run: bool = False) -> None:
        """Delete ephemeral cluster firewall rule."""
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

    def build_worker_create_command(
        self,
        config: ClusterConfig,
        worker_name: str,
        startup_path: Path,
    ) -> list[str]:
        """Build gcloud compute instances create command for a cluster worker VM."""
        cmd = [
            "gcloud",
            "compute",
            "instances",
            "create",
            worker_name,
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            f"--machine-type={config.machine_type}",
            "--provisioning-model=SPOT",
            "--instance-termination-action=DELETE",
            f"--labels={LABEL_ATTRIBUTION}",
            f"--tags={WORKER_TAG}",
            "--image-family=debian-12",
            "--image-project=debian-cloud",
            "--scopes=cloud-platform",
            f"--boot-disk-type={config.boot_disk_type}",
            f"--boot-disk-size={config.boot_disk_size_gb}GB",
            f"--metadata-from-file=startup-script={startup_path}",
            "--quiet",
        ]
        if config.no_address:
            cmd.insert(cmd.index("--quiet"), "--no-address")
        return cmd

    def provision_workers(
        self,
        config: ClusterConfig,
        tracked_workers: list[dict[str, Any]] | None = None,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Provision N Spot VMs as VPC-internal cluster workers."""
        workers = tracked_workers if tracked_workers is not None else []
        for i in range(config.worker_count):
            name = config.worker_name(i)
            worker_id = f"worker-{i + 1}"
            startup_path = self._local_dir / f"startup-{name}.sh"
            if not dry_run:
                startup_content = self._generate_startup_script(worker_id, config.port)
                startup_path.parent.mkdir(parents=True, exist_ok=True)
                startup_path.write_bytes(startup_content.replace("\r\n", "\n").encode("utf-8"))

            cmd = self.build_worker_create_command(config, name, startup_path)
            if dry_run:
                print(f"[DRY-RUN] Proposing creation of worker {name}:\n  {' '.join(cmd)}")
                host_ip = f"10.128.0.{10 + i}" if config.no_address else f"198.51.100.{20 + i}"
                workers.append({"name": name, "worker_id": worker_id, "host": host_ip, "port": config.port})
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

    @staticmethod
    def iap_tunnel_command(config: ClusterConfig, worker_name: str, local_port: int) -> list[str]:
        """Build an IAP tunnel command to forward a worker's TCP port safely."""
        return [
            "gcloud",
            "compute",
            "start-iap-tunnel",
            worker_name,
            str(config.port),
            f"--local-host-port=127.0.0.1:{local_port}",
            f"--project={config.project_id}",
            f"--zone={config.zone}",
            "--quiet",
        ]

    def resolve_worker_ips(
        self,
        config: ClusterConfig,
        workers: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Resolve VPC-internal networkIPs (preferred) or external natIPs of provisioned workers."""
        if dry_run:
            return [
                {
                    "machine_id": w["worker_id"],
                    "host": f"10.128.0.{10 + idx}" if config.no_address else f"198.51.100.{20 + idx}",
                    "port": config.port,
                }
                for idx, w in enumerate(workers)
            ]

        resolved_endpoints: list[dict[str, Any]] = []
        for w in workers:
            fmt = (
                "json(networkInterfaces[0].networkIP)"
                if config.no_address
                else "json(networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP)"
            )
            cmd = [
                "gcloud",
                "compute",
                "instances",
                "describe",
                w["name"],
                f"--project={config.project_id}",
                f"--zone={config.zone}",
                f"--format={fmt}",
                "--quiet",
            ]
            res = self._run_cmd(cmd, check=True)
            raw_desc: Any = json.loads(res.stdout)
            desc = cast(dict[str, Any], raw_desc) if isinstance(raw_desc, dict) else {}
            ifaces = desc.get("networkInterfaces")
            iface_list = (
                [cast(dict[str, Any], x) for x in cast(list[Any], ifaces) if isinstance(x, dict)]
                if isinstance(ifaces, list)
                else []
            )
            iface: dict[str, Any] = iface_list[0] if iface_list else {}
            if config.no_address:
                ip = str(iface.get("networkIP", "")).strip()
            else:
                acc_configs = iface.get("accessConfigs")
                acc_list = (
                    [cast(dict[str, Any], x) for x in cast(list[Any], acc_configs) if isinstance(x, dict)]
                    if isinstance(acc_configs, list)
                    else []
                )
                nat_ip = str(acc_list[0].get("natIP", "")).strip() if acc_list else ""
                ip = nat_ip or str(iface.get("networkIP", "")).strip()
            if not ip:
                raise RuntimeError(f"Could not determine IP for instance {w['name']}")
            resolved_endpoints.append(
                {
                    "machine_id": w["worker_id"],
                    "host": ip,
                    "port": config.port,
                }
            )
        return resolved_endpoints

    def teardown_workers(
        self,
        config: ClusterConfig,
        workers: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> None:
        """Tear down all provisioned cluster worker instances."""
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
        """Poll TCP connect on all worker endpoints until ready or timeout."""
        start_time = time.monotonic()
        pending = list(endpoints)
        print(f"[+] Polling readiness for {len(pending)} worker endpoints (timeout: {timeout_seconds}s)...")
        while pending and (time.monotonic() - start_time) < timeout_seconds:
            ready: list[dict[str, Any]] = []
            for ep in pending:
                host = ep["host"]
                port = int(ep["port"])
                try:
                    with socket.create_connection((host, port), timeout=2.0):
                        print(f"[+] Worker {ep.get('machine_id', host)} ({host}:{port}) is READY.")
                        ready.append(ep)
                except OSError:
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
        import importlib.util

        if "scripts.tcp_cluster_soak_gate" in sys.modules:
            return sys.modules["scripts.tcp_cluster_soak_gate"]
        gate_file = self.root / "scripts" / "tcp_cluster_soak_gate.py"
        if not gate_file.is_file():
            gate_file = ROOT / "scripts" / "tcp_cluster_soak_gate.py"
        spec = importlib.util.spec_from_file_location("scripts.tcp_cluster_soak_gate", gate_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load {gate_file}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["scripts.tcp_cluster_soak_gate"] = mod
        spec.loader.exec_module(mod)
        return mod

    def run_soak_gate(
        self,
        endpoints: list[dict[str, Any]],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute tcp_cluster_soak_gate against the multi-machine endpoints."""
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

        soak_report_file = self.artifacts_dir / "tcp_cluster_soak_report.json"
        if not soak_report_file.exists():
            try:
                archive_base = resolve_private_archive(self.root)
            except PrivateArchiveUnavailableError as exc:
                raise FileNotFoundError(f"Missing required baseline artifact {soak_report_file}; {exc}") from exc
            archive_fallback = (
                archive_base / "20261028-wave4-verification" / "tier1-gcp" / "tcp_cluster_soak_report.json"
            )
            if archive_fallback.is_file():
                soak_report_file.write_bytes(archive_fallback.read_bytes())
                print(f"[+] Restored baseline {soak_report_file} from private archive.")
            else:
                raise FileNotFoundError(f"Missing required baseline artifact: {soak_report_file}")

        gate_mod = self._load_tcp_cluster_soak_gate_module()
        capture_res = gate_mod.write_real_multi_machine_cluster_soak_capture(self.root)
        if not capture_res.get("overall_ok"):
            time.sleep(3)
            capture_res = gate_mod.write_real_multi_machine_cluster_soak_capture(self.root)
        print(f"[+] Soak capture writer result: {capture_res.get('overall_ok')}")

        eval_res = dict(gate_mod.evaluate_tcp_cluster_soak_gate(self.root))
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
        wait_for_workers_seconds: int = 120,
    ) -> dict[str, Any]:
        """End-to-end orchestration of multi-machine TCP soak test."""
        print(f"=== Starting Multi-Machine VPC TCP Cluster Soak (Run ID: {config.run_id}) ===")
        created_workers: list[dict[str, Any]] = []
        firewall_created = False
        try:
            self.ensure_firewall_rule(config, dry_run=dry_run)
            firewall_created = True
            self.provision_workers(config, tracked_workers=created_workers, dry_run=dry_run)
            endpoints = self.resolve_worker_ips(config, created_workers, dry_run=dry_run)
            if not dry_run:
                self._wait_for_workers_ready(endpoints, timeout_seconds=wait_for_workers_seconds)

            gate_result = self.run_soak_gate(endpoints, dry_run=dry_run)
            return {
                "run_id": config.run_id,
                "no_address": config.no_address,
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
    parser.add_argument("--project", default=os.environ.get("CLOUDSDK_CORE_PROJECT", DEFAULT_PROJECT))
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--allow-external-ip", action="store_true", help="Disable --no-address default")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without spinning up real VMs")
    parser.add_argument("--artifacts-dir", default=None, help="Directory to store verification receipts")
    args = parser.parse_args()

    config = ClusterConfig(
        project_id=args.project,
        zone=args.zone,
        worker_count=args.workers,
        port=args.port,
        no_address=not args.allow_external_ip,
    )
    orchestrator = TCPClusterOrchestrator(artifacts_dir=args.artifacts_dir)
    result = orchestrator.execute_soak(config, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
