#!/usr/bin/env bash
# Real Multi-Machine Cluster Soak — Setup Script
#
# Build + start the 3-worker Docker cluster on a custom bridge network
# (172.20.0.0/24).  After this script exits successfully you can run
# the orchestrator-side soak probe from the host with
# scripts/tcp_cluster_soak_gate.py --write-capture (see CLUSTER_SOAK_REPORT).
#
# Requirements on the host:
#   - docker CLI + docker compose plugin (or docker-compose v1)
#   - 4 GB free RAM (each worker is minimal but Alpine + Python adds ~50 MB)
#   - port 9000 free on the default bridge
#
# Optional pre-step: enable NET_ADMIN on the workers so the operator
# can inject network chaos via `tc qdisc` after they come up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.cluster.yml"
NETWORK_NAME="aegis-cipher_aegis-cluster"

cd "${REPO_ROOT}"

echo "[setup] docker --version:"
docker --version
docker compose version || docker-compose --version

echo "[setup] building worker image…"
docker compose -f "${COMPOSE_FILE}" build worker-1 worker-2 worker-3

echo "[setup] starting workers (bridge mode)…"
docker compose -f "${COMPOSE_FILE}" up -d worker-1 worker-2 worker-3

# Coordinator lives on the same bridge so the host can drive the probe.
# On Docker Desktop on Windows the bridge subnet (172.20.0.0/24) is not
# routed on the host, so the orchestrator runs *inside* the coordinator
# container and the captured artifact is bind-mounted back to the host.
if [ "${WITH_COORDINATOR:-1}" = "1" ]; then
  echo "[setup] starting coordinator…"
  docker compose -f "${COMPOSE_FILE}" --profile orchestrator up -d
fi

echo "[setup] waiting for sockets to bind…"
for W in 1 2 3; do
  for _ in $(seq 1 30); do
    if docker exec "aegis-worker-${W}" python -c \
        "import socket;s=socket.socket();s.connect(('127.0.0.1',9000));s.close()" \
        >/dev/null 2>&1; then
      echo "  worker-${W}: listening"
      break
    fi
    sleep 1
  done
done

echo "[setup] verifying bridge IPs:"
docker network inspect "${NETWORK_NAME}" \
  | python -c "import json,sys;[print(' ',e['Name'],'->',e.get('IPv4Address','')) for e in json.load(sys.stdin)['Containers'].values()]"

echo
echo "[setup] ready.  Probe from inside the in-network coordinator:"
cat <<'EOF'
  docker exec aegis-coordinator sh -c '
    export AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON="[\
      {\"machine_id\":\"worker-1\",\"host\":\"172.20.0.11\",\"port\":9000},\
      {\"machine_id\":\"worker-2\",\"host\":\"172.20.0.12\",\"port\":9000},\
      {\"machine_id\":\"worker-3\",\"host\":\"172.20.0.13\",\"port\":9000}]"
    python scripts/tcp_cluster_soak_gate.py --write-capture'
  # artifact written to <repo>/artifacts/real_multi_machine_cluster_soak_capture.json
EOF
