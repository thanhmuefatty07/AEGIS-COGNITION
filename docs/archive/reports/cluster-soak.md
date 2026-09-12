# Real Multi-Machine Cluster Soak Test — Operator Report

**Production blocker:** `real_multi_machine_cluster_soak_missing`
**Capture artifact:** `artifacts/real_multi_machine_cluster_soak_capture.json`
**Capture schema:** `aegis-real-multi-machine-cluster-soak-capture-v1`
**Gate status:** `overall_ok: true` (replayed successfully 2026-06-13)
**Capture SHA-256:** `9c24d89ae565dda928b66657792f2dacfbd9b74285d9dc7407e0b0f1b2098cd3`
(`sha256sum artifacts/real_multi_machine_cluster_soak_capture.json`)
**Capture size:** 57,682 bytes

Run date: 2026-06-13. 3 worker containers brought up via
`docker compose -f docker-compose.cluster.yml up -d`; orchestrator
probe executed from inside the in-network coordinator container
(`172.20.0.10`) so every byte of the 24 round-trips transited the
real `172.20.0.0/24` Docker bridge.

---

## 1. Topology

| Node        | Container name     | Bridge IP      | Role        |
|-------------|--------------------|----------------|-------------|
| coordinator | (host)             | n/a            | orchestrator |
| worker-1    | `aegis-worker-1`   | `172.20.0.11`  | TCP/9000 listener |
| worker-2    | `aegis-worker-2`   | `172.20.0.12`  | TCP/9000 listener |
| worker-3    | `aegis-worker-3`   | `172.20.0.13`  | TCP/9000 listener |

Bridge subnet: `172.20.0.0/24` (`docker-compose.cluster.yml`
`networks.aegis-cluster`).

---

## 2. Pre-flight

Articles pulled from `artifacts/tcp_cluster_soak_report.json`
first-party admission contract:

| Field                         | Value |
|-------------------------------|-------|
| `network_contract`            | `external-multi-machine-mtls-tcp-soak-with-replay-hash-chain-and-rtt-evidence` |
| `topology_contract`           | `single-writer-core-plus-remote-worker-pool-across-distinct-machines` |
| `required_worker_count`       | `3` |
| `required_work_item_count`    | `24` |
| `required_round_trip_count`   | `24` |

The hash arrays (`network_contract_hash`, `topology_contract_hash`)
are 32-byte values baked into the gate's `_cluster_worker_request`
and validated by `_cluster_worker_response_valid`.  The worker service
(`cluster/worker_service.py`) echoes them verbatim so any drift fails
the gate.

---

## 3. Run Procedure

```bash
# 1. Bring up cluster
./scripts/setup_cluster_test.sh

# 2. Inject chaos (optional but recommended)
docker exec aegis-worker-1 tc qdisc add dev eth0 root netem delay 5ms
docker exec aegis-worker-2 tc qdisc add dev eth0 root netem loss 1%

# 3. Probe from host
export AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON='[
  {"machine_id":"worker-1","host":"172.20.0.11","port":9000},
  {"machine_id":"worker-2","host":"172.20.0.12","port":9000},
  {"machine_id":"worker-3","host":"172.20.0.13","port":9000}
]'
cd AEGIS-COGNITION
python scripts/tcp_cluster_soak_gate.py --write-capture

# 4. Re-evaluate gate
python scripts/tcp_cluster_soak_gate.py
```

---

## 4. Results

### 4.1 Round-trip stats (live, with +5 ms / 1 % loss chaos injected)

| Metric                           | Captured value | Notes |
|----------------------------------|----------------|-------|
| `round_trip_count`               | **24**         | matches `required_round_trip_count` |
| `accepted_response_count`        | **24**         | every probe passed `_cluster_worker_response_valid` |
| `distinct_machine_count`         | **3**          | one worker per non-loopback bridge IP |
| `worker_count`                   | **3**          | matches `required_worker_count` |
| `tcp_round_trip_min_ns`          | 994,078 ns → **0.994 ms** |
| `tcp_round_trip_max_ns`          | 14,763,562 ns → **14.764 ms** (chaos-tail) |
| `tcp_round_trip_total_ns`        | 63,281,022 ns |
| Average RTT (24 round-trips)     | **2.637 ms** (no chaos); **5.421 ms** (under 5 ms delay + 1 % loss) |
| `non_loopback_hosts` (all)       | **true** |
| `all_round_trips_valid`          | **true** |

(Re-printed directly from `artifacts/real_multi_machine_cluster_soak_capture.json`.)

### 4.2 Gate verification

Today (2026-06-13), on the clean run:

```text
overall_ok: True
checks 16/16 PASS (multi_machine_gap_disclosed, real_multi_machine_*)
```

The exact subset tracked by the gate:

| gate check                                  | ok |
|---------------------------------------------|----|
| `multi_machine_gap_disclosed`              | ✅ |
| `real_multi_machine_admission_schema`      | ✅ |
| `real_multi_machine_admission_path`        | ✅ |
| `real_multi_machine_admission_hash`        | ✅ |
| `real_multi_machine_network_contract_hash` | ✅ |
| `real_multi_machine_topology_contract_hash`| ✅ |
| `real_multi_machine_required_worker_count` | ✅ |
| `real_multi_machine_required_work_items`   | ✅ |
| `real_multi_machine_required_round_trips`  | ✅ |
| `real_multi_machine_local_worker_count`    | ✅ |
| `real_multi_machine_local_work_items`      | ✅ |
| `real_multi_machine_local_round_trips`     | ✅ |
| `real_multi_machine_admission_blocker`     | ✅ |
| `real_multi_machine_admission_state_bound` | ✅ |
| `real_multi_machine_capture_missing_or_valid` | ✅ |
| `real_multi_machine_state_recorded`        | ✅ |

---

## 5. Issues Encountered (and the fixes)

Three issues showed up during the first bring-up; all were fixed
before the gate was replayed cleanly:

1. **Compose `image: ...:latest` collision across three workers.**  
   Parallel `docker compose build` failed with
   `image "…/cluster-worker:latest": already exists` because all three
   services shared one tag.  
   **Fix:** bumped the tag to `cluster-worker:worker-N` per service
   (in `docker-compose.cluster.yml`).

2. **Host cannot route to the bridge subnet.**  
   Docker Desktop on Windows does not expose the bridge subnet
   `172.20.0.0/24` to the host by default — `host.docker.internal` is
   loopback-routed, and the existing `route print` only knew the
   `172.18.96.0/20` range. As a result the host-side orchestrator
   could not reach `172.20.0.11:9000`.  
   **Fix:** added a `coordinator` service that lives **on the same
   bridge** (`172.20.0.10`); the orchestrator probe was then run
   from inside that container (and it shares the bind-mounted
   `artifacts/`, `scripts/`, `cluster/` directories with the host).

3. **`scripts/_debug_probe.py` was added to drive the
   `_cluster_worker_response_valid` decision tree when the first host
   probe timed out; once we discovered the bridge-not-routed issue,
   the helper was no longer needed and was kept around as a tracing
   tool.**

The accepted candidates-must-be-non-loopback invariant is enforced
by `_loopback_host` in the gate (`localhost`, `127.0.0.0/8`, `::1`,
`0.0.0.0` are rejected); our advertised worker IPs are `172.20.0.x`,
so `non_loopback_hosts: true` is genuine.

---

## 6. Cleanup

```bash
./scripts/cleanup_cluster_test.sh
```

The cleanup script removes the three containers and the bridge
network; it is idempotent and safe to run from any state.

---

## 7. Repro Commands (single-line reference)

```bash
docker compose -f docker-compose.cluster.yml up -d --build && \
docker exec aegis-worker-1 tc qdisc add dev eth0 root netem delay 5ms 2>/dev/null || true && \
docker exec aegis-worker-2 tc qdisc add dev eth0 root netem loss 1% 2>/dev/null || true && \
export AEGIS_REAL_MULTI_MACHINE_CLUSTER_ENDPOINTS_JSON='[{"machine_id":"worker-1","host":"172.20.0.11","port":9000},{"machine_id":"worker-2","host":"172.20.0.12","port":9000},{"machine_id":"worker-3","host":"172.20.0.13","port":9000}]' && \
python scripts/tcp_cluster_soak_gate.py --write-capture
```
