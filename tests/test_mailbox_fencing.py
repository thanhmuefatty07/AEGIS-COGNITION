from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from multiprocessing import get_context
from threading import Barrier

import pytest

from aegis_cognition.subagents import AGENT_MESSAGE_SCHEMA_V1, AgentCoordinationError, AgentMailbox, AgentMessage

_TERMINAL_RETENTION_MS = 30 * 24 * 60 * 60 * 1000


def _request_message() -> AgentMessage:
    return AgentMessage(
        schema=AGENT_MESSAGE_SCHEMA_V1,
        message_kind="TASK_REQUEST",
        run_id="lease-fencing-test",
        sender_id="root",
        recipient_id="subagent:1",
        task_id=1,
        parent_task_id=None,
        attempt_id=1,
        idempotency_key="lease-fencing-test:task-1:request",
        payload={"prompt": "bounded test work"},
    )


def test_acknowledged_message_is_retained_until_30_days_then_idempotency_expires(tmp_path) -> None:
    mailbox = AgentMailbox(tmp_path / "terminal-retention.db")
    message = _request_message()
    delivery_id = mailbox.enqueue(message, now_ms=1)
    delivery = mailbox.claim("worker", lease_ms=100, now_ms=10)
    assert delivery is not None
    assert (
        mailbox.ack(
            delivery_id,
            "worker",
            expected_attempt=delivery.attempts,
            now_ms=20,
        )
        is True
    )

    assert mailbox.enqueue(message, now_ms=20 + _TERMINAL_RETENTION_MS - 1) == delivery_id
    replacement_id = mailbox.enqueue(message, now_ms=20 + _TERMINAL_RETENTION_MS)

    assert replacement_id != delivery_id
    assert mailbox.counts() == {"READY": 1, "LEASED": 0, "ACKED": 0, "DEAD": 0}


def test_dead_letter_from_nack_is_pruned_at_retention_boundary(tmp_path) -> None:
    mailbox = AgentMailbox(tmp_path / "dead-retention.db", max_attempts=1)
    message = replace(_request_message(), idempotency_key="dead-nack")
    delivery_id = mailbox.enqueue(message, now_ms=1)
    delivery = mailbox.claim("worker", lease_ms=100, now_ms=10)
    assert delivery is not None
    assert (
        mailbox.nack(
            delivery_id,
            "worker",
            expected_attempt=delivery.attempts,
            now_ms=11,
            error="handler failed",
        )
        == "DEAD"
    )

    mailbox.enqueue(replace(message, idempotency_key="keep-alive"), now_ms=11 + _TERMINAL_RETENTION_MS - 1)
    assert mailbox.counts()["DEAD"] == 1
    mailbox.enqueue(replace(message, idempotency_key="trigger-prune"), now_ms=11 + _TERMINAL_RETENTION_MS)

    assert mailbox.counts()["DEAD"] == 0


def test_dead_letter_from_expired_lease_is_pruned_at_retention_boundary(tmp_path) -> None:
    mailbox = AgentMailbox(tmp_path / "expired-dead-retention.db", max_attempts=1)
    mailbox.enqueue(replace(_request_message(), idempotency_key="expired-dead"), now_ms=1)
    delivery = mailbox.claim("worker-a", lease_ms=10, now_ms=10)
    assert delivery is not None

    assert mailbox.claim("worker-b", now_ms=20) is None
    assert mailbox.counts()["DEAD"] == 1
    mailbox.enqueue(
        replace(_request_message(), idempotency_key="trigger-expired-prune"), now_ms=20 + _TERMINAL_RETENTION_MS
    )

    assert mailbox.counts()["DEAD"] == 0


def test_retention_cleanup_never_deletes_ready_or_leased_messages(tmp_path) -> None:
    mailbox = AgentMailbox(tmp_path / "active-retention.db")
    leased_message = replace(_request_message(), idempotency_key="active-leased")
    ready_message = replace(_request_message(), idempotency_key="active-ready")
    mailbox.enqueue(leased_message, now_ms=1)
    leased = mailbox.claim("worker", lease_ms=10, now_ms=10)
    assert leased is not None
    mailbox.enqueue(ready_message, now_ms=11)

    mailbox.enqueue(
        replace(_request_message(), idempotency_key="retention-trigger"), now_ms=11 + _TERMINAL_RETENTION_MS
    )

    assert mailbox.counts() == {"READY": 2, "LEASED": 1, "ACKED": 0, "DEAD": 0}


def test_opening_old_mailbox_migrates_and_preserves_legacy_rows(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-mailbox.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE agent_mailbox_messages (
                delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                message_hash TEXT NOT NULL,
                payload BLOB NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('READY', 'LEASED', 'ACKED', 'DEAD')),
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at_ms INTEGER NOT NULL,
                lease_owner TEXT,
                lease_until_ms INTEGER,
                created_at_ms INTEGER NOT NULL,
                acked_at_ms INTEGER,
                last_error TEXT
            );
            CREATE INDEX idx_agent_mailbox_ready
                ON agent_mailbox_messages (state, available_at_ms, delivery_id);
            CREATE INDEX idx_agent_mailbox_lease
                ON agent_mailbox_messages (state, lease_until_ms);
            """
        )
        connection.executemany(
            "INSERT INTO agent_mailbox_messages "
            "(delivery_id, idempotency_key, message_hash, payload, state, attempts, "
            "available_at_ms, created_at_ms, acked_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (1, "legacy-acked", "hash-1", b"acked", "ACKED", 1, 100, 1, 99),
                (2, "legacy-dead", "hash-2", b"dead", "DEAD", 5, 200, 2, None),
                (3, "legacy-ready", "hash-3", b"ready", "READY", 0, 300, 3, None),
            ),
        )
    monkeypatch.setattr(AgentMailbox, "_now_ms", staticmethod(lambda: 1_000))

    mailbox = AgentMailbox(path)

    with sqlite3.connect(path) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(agent_mailbox_messages)")}
        terminal_times = dict(
            connection.execute(
                "SELECT delivery_id, terminal_at_ms FROM agent_mailbox_messages ORDER BY delivery_id"
            ).fetchall()
        )
        indexes = {str(row[1]) for row in connection.execute("PRAGMA index_list(agent_mailbox_messages)")}
    assert "terminal_at_ms" in columns
    assert terminal_times == {1: 99, 2: 1_000, 3: None}
    assert "idx_agent_mailbox_terminal" in indexes
    assert mailbox.counts() == {"READY": 1, "LEASED": 0, "ACKED": 1, "DEAD": 1}


def test_opening_mailbox_prunes_expired_terminal_rows_without_new_enqueue(tmp_path, monkeypatch) -> None:
    path = tmp_path / "startup-retention.db"
    now_ms = 1
    monkeypatch.setattr(AgentMailbox, "_now_ms", staticmethod(lambda: now_ms))
    mailbox = AgentMailbox(path)
    message = _request_message()
    delivery_id = mailbox.enqueue(message)
    delivery = mailbox.claim("worker", lease_ms=100)
    assert delivery is not None
    assert mailbox.ack(delivery_id, "worker", expected_attempt=delivery.attempts, now_ms=2) is True

    now_ms = 2 + _TERMINAL_RETENTION_MS
    reopened = AgentMailbox(path)

    assert reopened.counts() == {"READY": 0, "LEASED": 0, "ACKED": 0, "DEAD": 0}


def _claim_from_process(path: str, consumer_id: str, start, results) -> None:
    mailbox = AgentMailbox(path)
    start.wait(timeout=10)
    delivery = mailbox.claim(consumer_id, lease_ms=100, now_ms=10)
    results.put(None if delivery is None else delivery.delivery_id)


def _claim_and_wait(path: str, results, keep_alive) -> None:
    mailbox = AgentMailbox(path, max_attempts=2)
    delivery = mailbox.claim("interrupted-worker", lease_ms=10, now_ms=10)
    results.put(None if delivery is None else (delivery.delivery_id, delivery.attempts))
    keep_alive.wait()


def test_reclaimed_lease_fences_stale_ack_and_nack_from_same_consumer(tmp_path) -> None:
    path = tmp_path / "mailbox.db"
    mailbox = AgentMailbox(path, max_attempts=3)
    mailbox.enqueue(_request_message(), now_ms=1)

    expired = mailbox.claim("worker-a", lease_ms=10, now_ms=10)
    reopened = AgentMailbox(path, max_attempts=3)
    current = reopened.claim("worker-a", lease_ms=10, now_ms=20)
    assert expired is not None and expired.attempts == 1
    assert current is not None and current.attempts == 2

    assert (
        reopened.ack(
            expired.delivery_id,
            expired.consumer_id,
            expected_attempt=expired.attempts,
            now_ms=21,
        )
        is False
    )
    with pytest.raises(AgentCoordinationError, match="mailbox delivery is not owned"):
        reopened.nack(
            expired.delivery_id,
            expired.consumer_id,
            expected_attempt=expired.attempts,
            now_ms=21,
        )

    assert reopened.counts() == {"READY": 0, "LEASED": 1, "ACKED": 0, "DEAD": 0}
    assert (
        reopened.ack(
            current.delivery_id,
            current.consumer_id,
            expected_attempt=current.attempts,
            now_ms=22,
        )
        is True
    )


def test_concurrent_claims_lease_a_delivery_to_only_one_consumer(tmp_path) -> None:
    path = tmp_path / "concurrent-mailbox.db"
    mailbox = AgentMailbox(path)
    mailbox.enqueue(_request_message(), now_ms=1)
    start = Barrier(2)

    def claim(consumer_id: str):
        start.wait(timeout=2)
        return AgentMailbox(path).claim(consumer_id, lease_ms=100, now_ms=10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(executor.submit(claim, consumer_id) for consumer_id in ("worker-a", "worker-b"))
        deliveries = tuple(future.result(timeout=5) for future in futures)

    assert sum(delivery is not None for delivery in deliveries) == 1
    assert mailbox.counts() == {"READY": 0, "LEASED": 1, "ACKED": 0, "DEAD": 0}


def test_separate_processes_cannot_claim_the_same_delivery(tmp_path) -> None:
    path = tmp_path / "process-mailbox.db"
    mailbox = AgentMailbox(path)
    mailbox.enqueue(_request_message(), now_ms=1)

    context = get_context("spawn")
    start = context.Barrier(2)
    results = context.Queue()
    workers = tuple(
        context.Process(
            target=_claim_from_process,
            args=(str(path), consumer_id, start, results),
        )
        for consumer_id in ("process-a", "process-b")
    )
    started_workers = []

    try:
        for worker in workers:
            worker.start()
            started_workers.append(worker)
        for worker in started_workers:
            worker.join(timeout=20)

        assert all(not worker.is_alive() for worker in started_workers)
        assert [worker.exitcode for worker in started_workers] == [0, 0]
        claimed_ids = [results.get(timeout=5) for _ in started_workers]
    finally:
        for worker in started_workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        results.close()
        results.join_thread()

    assert sum(delivery_id is not None for delivery_id in claimed_ids) == 1
    assert mailbox.counts() == {"READY": 0, "LEASED": 1, "ACKED": 0, "DEAD": 0}


def test_expired_lease_recovers_after_worker_process_is_terminated(tmp_path) -> None:
    path = tmp_path / "crashed-worker-mailbox.db"
    mailbox = AgentMailbox(path, max_attempts=2)
    mailbox.enqueue(_request_message(), now_ms=1)

    context = get_context("spawn")
    results = context.Queue()
    keep_alive = context.Event()
    worker = context.Process(
        target=_claim_and_wait,
        args=(str(path), results, keep_alive),
    )
    started = False
    try:
        worker.start()
        started = True
        claimed = results.get(timeout=10)
        assert claimed is not None
        first_delivery_id, first_attempt = claimed

        worker.terminate()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert worker.exitcode is not None

        recovered = mailbox.claim("recovery-worker", lease_ms=10, now_ms=20)
        assert recovered is not None
        assert recovered.delivery_id == first_delivery_id
        assert recovered.attempts == first_attempt + 1
        assert (
            mailbox.ack(
                first_delivery_id,
                "interrupted-worker",
                expected_attempt=first_attempt,
                now_ms=21,
            )
            is False
        )
        assert (
            mailbox.ack(
                recovered.delivery_id,
                recovered.consumer_id,
                expected_attempt=recovered.attempts,
                now_ms=21,
            )
            is True
        )
    finally:
        if started:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=10)
        results.close()
        results.join_thread()

    assert mailbox.counts() == {"READY": 0, "LEASED": 0, "ACKED": 1, "DEAD": 0}
