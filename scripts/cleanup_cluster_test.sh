#!/usr/bin/env bash
# Real Multi-Machine Cluster Soak — Cleanup Script
#
# Tears down the 3-worker Docker cluster and the bridge network.
# Safe to run when workers are still up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.cluster.yml"

cd "${REPO_ROOT}"

echo "[cleanup] stopping + removing containers…"
docker compose -f "${COMPOSE_FILE}" down --remove-orphans || true

NETWORK_NAME="aegis-cipher_aegis-cluster"
if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "[cleanup] removing leftover bridge: ${NETWORK_NAME}"
  docker network rm "${NETWORK_NAME}" || true
fi

echo "[cleanup] remaining aegis* resources:"
docker ps -a --filter "name=aegis-" --format "table {{.Names}}\t{{.Status}}" || true
docker network ls --filter "name=aegis-" --format "table {{.Name}}" || true

echo "[cleanup] done."
