#!/usr/bin/env bash
# Local Docker Bridge Smoke Test — Setup Script
#
# Build + start three workers on one host. This is a local TCP smoke test,
# not evidence for the NV-016 multi-machine cluster soak.
#
# Requirements on the host:
#   - Docker CLI with Compose plugin, or the standalone docker-compose CLI
#   - enough Docker resources to build and run three Python workers
#
# Optional pre-step: enable NET_ADMIN on the workers so the operator
# can inject network chaos via `tc qdisc` after they come up.

set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: ./scripts/setup_cluster_test.sh"
  echo "Local Docker Bridge Smoke Test — starts 3 containerized workers on one host."
  echo "DISCLAIMER: This is a single-host local TCP smoke check only; it does NOT satisfy the NV-016 multi-machine soak contract."
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.cluster.yml"
PROJECT_NAME="aegis-cluster-test"
NETWORK_NAME="${PROJECT_NAME}_aegis-cluster"

cd "${REPO_ROOT}"

echo "[setup] docker --version:"
docker --version
if docker compose version >/dev/null 2>&1; then
  compose() { docker compose --project-name "${PROJECT_NAME}" --file "${COMPOSE_FILE}" "$@"; }
  docker compose version
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose --project-name "${PROJECT_NAME}" --file "${COMPOSE_FILE}" "$@"; }
  docker-compose --version
else
  echo "[setup] Docker Compose plugin or standalone docker-compose is required" >&2
  exit 1
fi

echo "[setup] building worker image…"
compose build worker-1 worker-2 worker-3

echo "[setup] starting workers (bridge mode)…"
compose up -d worker-1 worker-2 worker-3

# Coordinator lives on the same bridge so the host can drive the probe.
# On Docker Desktop on Windows the bridge subnet (172.20.0.0/24) is not
# routed on the host, so the orchestrator runs *inside* the coordinator
# container and the captured artifact is bind-mounted back to the host.
if [ "${WITH_COORDINATOR:-1}" = "1" ]; then
  echo "[setup] starting coordinator…"
  compose --profile orchestrator up -d
fi

echo "[setup] waiting for sockets to bind…"
for W in 1 2 3; do
  worker_ready=0
  for _ in $(seq 1 30); do
    if docker exec "aegis-worker-${W}" python -c \
        "import socket;s=socket.socket();s.connect(('127.0.0.1',9000));s.close()" \
        >/dev/null 2>&1; then
      echo "  worker-${W}: listening"
      worker_ready=1
      break
    fi
    sleep 1
  done
  if [ "$worker_ready" -ne 1 ]; then
    echo "[setup] worker-${W} did not open its socket after 30 attempts" >&2
    exit 1
  fi
done

echo "[setup] verifying bridge IPs:"
docker network inspect "${NETWORK_NAME}" \
  | python -c "import json,sys;[print(' ',e['Name'],'->',e.get('IPv4Address','')) for e in json.load(sys.stdin)['Containers'].values()]"

echo
echo "[setup] local single-host bridge smoke test is ready."
echo "[setup] this setup does not satisfy the NV-016 multi-machine soak contract."
