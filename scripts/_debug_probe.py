"""Debug helper: probe 1 worker, dump decision tree for _cluster_worker_response_valid."""
import json
import sys
import socket
import hashlib
import pathlib

sys.path.insert(0, r"C:\Users\ADMIN\AEGIS-COGNITION\scripts")
import tcp_cluster_soak_gate as g

REPORT_PATH = pathlib.Path(r"C:\Users\ADMIN\AEGIS-COGNITION\artifacts\tcp_cluster_soak_report.json")
report = json.loads(REPORT_PATH.read_text())["report"]
adm = dict(report["real_multi_machine_cluster_admission"])
adm["required_worker_count"] = report["worker_count"]
adm["required_round_trip_count"] = report["tcp_round_trip_count"]

EP = {"machine_id": "worker-1", "host": "172.20.0.11", "port": 9000}
req = g._cluster_worker_request(report, adm, EP, 0)
body = (json.dumps(req, sort_keys=True, separators=(",", ":")) + "\n").encode()

s = socket.create_connection((EP["host"], EP["port"]), timeout=5)
s.sendall(body)
data = s.recv(1024 * 1024)
s.close()

resp = json.loads(data.decode())

print("== request (request_hash, schema, work_index) ==")
print(f"  schema: {req['schema']}")
print(f"  work_index: {req['work_index']}")
print(f"  network_contract_hash[:4]: {req['network_contract_hash'][:4]}")
print(f"  topology_contract_hash[:4]: {req['topology_contract_hash'][:4]}")

print()
print("== response ==")
for k, v in sorted(resp.items()):
    if isinstance(v, list) and len(v) > 6:
        print(f"  {k}: <list[{len(v)}]: first 6 = {v[:6]}>")
    else:
        print(f"  {k}: {v!r}")

print()
print("== _cluster_worker_response_valid decision tree ==")
checks = []
checks.append(("schema == aegis-real-cluster-worker-response-v1", resp.get("schema") == "aegis-real-cluster-worker-response-v1"))
checks.append(("truth_claim is False", resp.get("truth_claim") is False))
checks.append(("candidate_only is True", resp.get("candidate_only") is True))
checks.append(("direct_worker_commit_rejected is True", resp.get("direct_worker_commit_rejected") is True))
checks.append(("side_effect_partition_paused is True", resp.get("side_effect_partition_paused") is True))
checks.append(("network_contract_hash match", resp.get("network_contract_hash") == adm["network_contract_hash"]))
checks.append(("topology_contract_hash match", resp.get("topology_contract_hash") == adm["topology_contract_hash"]))
wmid = resp.get("worker_machine_id_hash", "")
expected_mid_hash = hashlib.sha256(EP["machine_id"].encode("utf-8")).hexdigest()
checks.append(("worker_machine_id_hash match", isinstance(wmid, str) and wmid == expected_mid_hash))

all_ok = True
for name, ok in checks:
    mark = "OK  " if ok else "FAIL"
    if not ok:
        all_ok = False
    print(f"  [{mark}] {name}")

print()
print(f"== worker_machine_id_hash expected = {expected_mid_hash}")
print(f"== worker_machine_id_hash got      = {wmid}")
print()
print("OVERALL: " + ("VALID" if all_ok else "INVALID"))
