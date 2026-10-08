from __future__ import annotations

import hashlib
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

try:
    from core.python.aegis.capability_state import CapabilityStateConflict, CapabilityStateError, CapabilityStateStore
except ImportError:
    from aegis.capability_state import CapabilityStateConflict, CapabilityStateError, CapabilityStateStore


def _update_capability_state_in_process(path: str, capability_id: str, barrier: Any, count: int) -> None:
    store = CapabilityStateStore(path)
    barrier.wait(timeout=10)
    for _ in range(count):
        for _attempt in range(1000):
            state = store.get()
            try:
                store.set(
                    kind="extension",
                    capability_id=capability_id,
                    descriptor_hash="e" * 64,
                    enabled=False,
                    approved=False,
                    expected_revision=state["revision"],
                )
                break
            except CapabilityStateConflict:
                continue
        else:
            raise AssertionError("capability state updates did not make progress")


def test_capability_state_reads_existing_v1_records_without_remote_origin_fields(tmp_path) -> None:
    path = tmp_path / "capability-state.json"
    path.write_text(
        json.dumps(
            {
                "schema": "aegis-capability-state-v1",
                "revision": 1,
                "records": [
                    {
                        "kind": "skill",
                        "id": "local-skill",
                        "descriptor_hash": "a" * 64,
                        "enabled": True,
                        "approved": False,
                        "updated_at_ms": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert CapabilityStateStore(path).get()["records"] == [
        {
            "kind": "skill",
            "id": "local-skill",
            "descriptor_hash": "a" * 64,
            "enabled": True,
            "approved": False,
            "updated_at_ms": 1,
        }
    ]


def test_capability_state_binds_remote_skill_to_server_uri_and_manifest(tmp_path) -> None:
    store = CapabilityStateStore(tmp_path / "capability-state.json")
    server_id = "docs-server"
    uri = "skill://research/SKILL.md"
    capability_id = f"mcp:{server_id}:{hashlib.sha256(uri.encode()).hexdigest()}"

    result = store.set(
        kind="skill",
        capability_id=capability_id,
        descriptor_hash="b" * 64,
        enabled=True,
        approved=True,
        expected_revision=0,
        source_server_id=server_id,
        resource_uri=uri,
        skill_name="research",
        skill_description="Research trusted sources",
    )

    record = result["records"][0]
    assert record["id"] == capability_id
    assert record["source_server_id"] == server_id
    assert record["resource_uri"] == uri
    assert record["descriptor_hash"] == "b" * 64
    assert record["skill_name"] == "research"
    assert record["skill_description"] == "Research trusted sources"
    assert CapabilityStateStore(tmp_path / "capability-state.json").get() == result


def test_capability_state_rejects_remote_skill_metadata_without_valid_origin(tmp_path) -> None:
    with pytest.raises(CapabilityStateError, match="only a remote skill"):
        CapabilityStateStore(tmp_path / "capability-state.json").set(
            kind="skill",
            capability_id="local-skill",
            descriptor_hash="a" * 64,
            enabled=False,
            approved=False,
            expected_revision=0,
            skill_name="research",
            skill_description="Research trusted sources",
        )


@pytest.mark.parametrize(
    ("kind", "capability_id", "server_id", "uri", "message"),
    [
        ("extension", "plugin", "docs-server", "skill://research/SKILL.md", "only a skill"),
        ("skill", "mcp:other:hash", "docs-server", "skill://research/SKILL.md", "does not match"),
        ("skill", "mcp:docs-server:hash", "docs-server", "skill://research/../secret", "does not match"),
    ],
)
def test_capability_state_rejects_invalid_remote_skill_identity(
    tmp_path, kind: str, capability_id: str, server_id: str, uri: str, message: str
) -> None:
    with pytest.raises(CapabilityStateError, match=message):
        CapabilityStateStore(tmp_path / "capability-state.json").set(
            kind=kind,
            capability_id=capability_id,
            descriptor_hash="c" * 64,
            enabled=False,
            approved=False,
            expected_revision=0,
            source_server_id=server_id,
            resource_uri=uri,
        )


def test_capability_state_instances_serialize_concurrent_revision_updates(tmp_path) -> None:
    path = tmp_path / "capability-state.json"
    stores = (CapabilityStateStore(path), CapabilityStateStore(path))
    assert stores[0]._lock is stores[1]._lock
    assert stores[0]._lock is not CapabilityStateStore(tmp_path / "other-capability-state.json")._lock

    def update_repeatedly(store: CapabilityStateStore, capability_id: str) -> None:
        for _ in range(20):
            while True:
                state = store.get()
                try:
                    store.set(
                        kind="extension",
                        capability_id=capability_id,
                        descriptor_hash="d" * 64,
                        enabled=False,
                        approved=False,
                        expected_revision=state["revision"],
                    )
                except CapabilityStateConflict:
                    continue
                break

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(update_repeatedly, stores[0], "plugin-a"),
            executor.submit(update_repeatedly, stores[1], "plugin-b"),
        ]
        for future in futures:
            future.result()

    final = stores[0].get()
    assert final["revision"] == 40
    assert {record["id"] for record in final["records"]} == {"plugin-a", "plugin-b"}


def test_capability_state_serializes_revision_updates_across_processes(tmp_path) -> None:
    path = tmp_path / "capability-state.json"
    context = multiprocessing.get_context("spawn")
    count = 12
    capability_ids = ("process-a", "process-b", "process-c", "process-d")
    barrier = context.Barrier(len(capability_ids))
    processes = [
        context.Process(
            target=_update_capability_state_in_process,
            args=(str(path), capability_id, barrier, count),
        )
        for capability_id in capability_ids
    ]

    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0] * len(processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    final = CapabilityStateStore(path).get()
    assert final["revision"] == count * len(capability_ids)
    assert {record["id"] for record in final["records"]} == set(capability_ids)
