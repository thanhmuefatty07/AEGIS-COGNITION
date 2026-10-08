#!/usr/bin/env bash
# Local Docker Bridge Smoke Test — Cleanup Script
#
# Tears down the 3-worker Docker cluster and the bridge network.
# Safe to run when workers are still up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.cluster.yml"
PROJECT_NAME="aegis-cluster-test"
NETWORK_NAME="${PROJECT_NAME}_aegis-cluster"

cd "${REPO_ROOT}"

if docker compose version >/dev/null 2>&1; then
  compose() { docker compose --project-name "${PROJECT_NAME}" --file "${COMPOSE_FILE}" "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose --project-name "${PROJECT_NAME}" --file "${COMPOSE_FILE}" "$@"; }
else
  echo "[cleanup] Docker Compose plugin or standalone docker-compose is required" >&2
  exit 1
fi

echo "[cleanup] stopping + removing containers…"
compose down --remove-orphans || true

if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "[cleanup] removing leftover bridge: ${NETWORK_NAME}"
  docker network rm "${NETWORK_NAME}" || true
fi

echo "[cleanup] remaining aegis* resources:"
docker ps -a --filter "name=aegis-" --format "table {{.Names}}\t{{.Status}}" || true
docker network ls --filter "name=aegis-" --format "table {{.Name}}" || true

echo "[cleanup] done."
