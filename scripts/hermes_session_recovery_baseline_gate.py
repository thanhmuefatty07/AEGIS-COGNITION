import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "hermes_session_recovery_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-session-recovery-baseline"
BASELINE_DB_PATH = BASELINE_DIR / "hermes-state-recovery.db"
BASELINE_RECOVERY_PAYLOAD_PATH = BASELINE_DIR / "hermes-recovered-session.json"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_BINARY_RECOVERY_BENCHMARK_NAME = "replay_io_binary_mmap_recover_prefix"
HERMES_SESSION_RECOVERY_SPEEDUP_THRESHOLD = 3.0
BASELINE_ROUNDS = 7
BASELINE_BACKGROUND_SESSIONS = 96
BASELINE_MESSAGES_PER_BACKGROUND_SESSION = 24
BASELINE_RECOVERY_MESSAGES = 4096
BASELINE_CONTENT_BYTES = 768
BASELINE_TARGET_PARENT_SESSION = "acp-recovery-parent"
BASELINE_TARGET_SESSION = "acp-recovery-target"


@dataclass(frozen=True)
class HermesSessionRecoveryBaselineCheck:
    name: str
    ok: bool
    aegis_estimate_ns: float | None
    baseline_estimate_ns: float | None
    speedup: float | None
    threshold_speedup: float
    detail: str


@dataclass(frozen=True)
class HermesSessionRecoveryBaselineReport:
    suite_name: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[HermesSessionRecoveryBaselineCheck]
    physical_evidence: dict


def _criterion_estimate_ns(root: Path, name: str) -> float | None:
    path = root / "target" / "criterion" / name / "new" / "estimates.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    estimate = data.get("slope") or data.get("mean") or data.get("median")
    if not estimate or "point_estimate" not in estimate:
        return None
    return float(estimate["point_estimate"])


def _cargo_env() -> dict:
    env = os.environ.copy()
    py_root = Path(sys.executable).parent
    env["PYO3_PYTHON"] = sys.executable
    env["PATH"] = os.pathsep.join(
        [str(py_root), str(py_root / "DLLs"), str(py_root / "libs"), env.get("PATH", "")]
    )
    env.setdefault("RUSTFLAGS", "-C debuginfo=0")
    return env


def _blake3_hex(root: Path, payload: bytes) -> str:
    try:
        import blake3  # type: ignore

        return blake3.blake3(payload).hexdigest()
    except ImportError:
        result = subprocess.run(
            ["cargo", "run", "--quiet", "--bin", "aegis-nerve-cli", "--", "blake3-stdin"],
            cwd=root / "core" / "rust",
            input=payload,
            capture_output=True,
            env=_cargo_env(),
            check=True,
        )
        return result.stdout.decode("utf-8").strip()


def _git_commit(path: Path) -> str | None:
    repo_path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _source_refs(root: Path) -> dict[str, str]:
    refs = {
        "hermes_acp_session_contract": "artifacts/research/hermes-agent/acp_adapter/session.py:3",
        "hermes_acp_session_persist": "artifacts/research/hermes-agent/acp_adapter/session.py:423",
        "hermes_acp_session_restore": "artifacts/research/hermes-agent/acp_adapter/session.py:471",
        "hermes_state_get_session": "artifacts/research/hermes-agent/hermes_state.py:1359",
        "hermes_state_get_messages": "artifacts/research/hermes-agent/hermes_state.py:2179",
        "hermes_state_resolve_resume_session": "artifacts/research/hermes-agent/hermes_state.py:2412",
        "hermes_state_get_messages_as_conversation": "artifacts/research/hermes-agent/hermes_state.py:2477",
        "hermes_state_export_session": "artifacts/research/hermes-agent/hermes_state.py:3334",
        "hermes_trajectory_jsonl_append": "artifacts/research/hermes-agent/agent/trajectory.py:30",
        "aegis_binary_replay_recovery": "core/rust/src/replay.rs:3124",
    }
    return {key: str(root / value) for key, value in refs.items()}


def _connect_hermes_state_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            model_config TEXT,
            system_prompt TEXT,
            parent_session_id TEXT,
            cwd TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            title TEXT,
            preview TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            billing_provider TEXT,
            billing_base_url TEXT
        );
        CREATE INDEX idx_sessions_started ON sessions(started_at DESC);
        CREATE INDEX idx_sessions_parent_started ON sessions(parent_session_id, started_at DESC);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            finish_reason TEXT,
            reasoning TEXT,
            reasoning_content TEXT,
            reasoning_details TEXT,
            codex_reasoning_items TEXT,
            codex_message_items TEXT,
            platform_message_id TEXT,
            observed INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );
        CREATE INDEX idx_messages_session_id ON messages(session_id, id);
        """
    )
    return conn


def _content(session_id: str, msg_idx: int, size: int) -> str:
    prefix = (
        f"session={session_id} message={msg_idx:05d} "
        "policy replay witness checkpoint recovery browser evidence "
    )
    if len(prefix) >= size:
        return prefix[:size]
    return prefix + ("x" * (size - len(prefix)))


def _insert_session(conn: sqlite3.Connection, session_id: str, *, parent: str | None, messages: int) -> None:
    started_at = 1_777_100_000.0 + len(session_id)
    conn.execute(
        """
        INSERT INTO sessions(
            id, source, user_id, model, model_config, system_prompt, parent_session_id,
            cwd, started_at, ended_at, end_reason, title, preview, message_count,
            tool_call_count, archived, billing_provider, billing_base_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            "acp",
            "operator",
            "hermes-4-preview",
            json.dumps({"cwd": "/workspace/aegis", "provider": "nous"}, separators=(",", ":")),
            "Hermes ACP restore baseline",
            parent,
            "/workspace/aegis",
            started_at,
            None if messages else started_at + 1.0,
            None if messages else "compression",
            f"Hermes recovery {session_id}",
            "policy replay witness recovery",
            messages,
            messages // 3,
            0,
            "nous",
            "https://api.nous.example",
        ),
    )


def _populate_baseline_db(conn: sqlite3.Connection) -> None:
    with conn:
        _insert_session(conn, BASELINE_TARGET_PARENT_SESSION, parent=None, messages=0)
        _insert_session(
            conn,
            BASELINE_TARGET_SESSION,
            parent=BASELINE_TARGET_PARENT_SESSION,
            messages=BASELINE_RECOVERY_MESSAGES,
        )
        for session_idx in range(BASELINE_BACKGROUND_SESSIONS):
            _insert_session(
                conn,
                f"acp-background-{session_idx:04d}",
                parent=None,
                messages=BASELINE_MESSAGES_PER_BACKGROUND_SESSION,
            )

        def insert_messages(session_id: str, count: int, offset: int) -> None:
            for msg_idx in range(count):
                tool_call = {
                    "id": f"tool-{session_id}-{msg_idx:05d}",
                    "name": "session_search" if msg_idx % 5 == 0 else "memory",
                    "args": {
                        "query": "policy replay witness recovery",
                        "limit": 32,
                        "session_id": session_id,
                    },
                }
                reasoning_details = {
                    "checkpoint": msg_idx,
                    "lineage": [BASELINE_TARGET_PARENT_SESSION, session_id],
                }
                conn.execute(
                    """
                    INSERT INTO messages(
                        session_id, role, content, timestamp, tool_call_id, tool_calls,
                        tool_name, finish_reason, reasoning, reasoning_content,
                        reasoning_details, codex_reasoning_items, codex_message_items,
                        platform_message_id, observed, active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        session_id,
                        ("user", "assistant", "tool")[msg_idx % 3],
                        _content(session_id, msg_idx, BASELINE_CONTENT_BYTES),
                        1_777_200_000.0 + offset + msg_idx,
                        f"call-{msg_idx:05d}" if msg_idx % 3 == 2 else None,
                        json.dumps([tool_call], separators=(",", ":")) if msg_idx % 3 == 1 else None,
                        tool_call["name"] if msg_idx % 3 == 2 else None,
                        "stop" if msg_idx % 3 == 1 else None,
                        "hash-bound replay recovery note" if msg_idx % 11 == 0 else None,
                        "binary mmap prefix scan beats linear hydration" if msg_idx % 13 == 0 else None,
                        json.dumps(reasoning_details, separators=(",", ":")),
                        json.dumps([{"type": "reasoning", "index": msg_idx}], separators=(",", ":")),
                        json.dumps([{"type": "message", "index": msg_idx}], separators=(",", ":")),
                        f"platform-{session_id}-{msg_idx:05d}",
                        1 if msg_idx % 7 == 0 else 0,
                    ),
                )

        insert_messages(BASELINE_TARGET_SESSION, BASELINE_RECOVERY_MESSAGES, 0)
        for session_idx in range(BASELINE_BACKGROUND_SESSIONS):
            insert_messages(
                f"acp-background-{session_idx:04d}",
                BASELINE_MESSAGES_PER_BACKGROUND_SESSION,
                (session_idx + 1) * 10_000,
            )


def _json_loads_maybe(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _resolve_resume_session_id(conn: sqlite3.Connection, session_id: str) -> tuple[str, int]:
    row = conn.execute("SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
    if row is not None:
        return session_id, 0
    current = session_id
    seen = {current}
    for depth in range(1, 33):
        child = conn.execute(
            "SELECT id FROM sessions WHERE parent_session_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
            (current,),
        ).fetchone()
        if child is None:
            return session_id, depth - 1
        child_id = child["id"]
        if not child_id or child_id in seen:
            return session_id, depth - 1
        seen.add(child_id)
        msg_row = conn.execute("SELECT 1 FROM messages WHERE session_id = ? LIMIT 1", (child_id,)).fetchone()
        if msg_row is not None:
            return child_id, depth
        current = child_id
    return session_id, 32


def _restore_session_payload(conn: sqlite3.Connection, requested_session_id: str) -> tuple[dict, int]:
    resolved_session_id, lineage_hops = _resolve_resume_session_id(conn, requested_session_id)
    session_row = conn.execute("SELECT * FROM sessions WHERE id = ?", (resolved_session_id,)).fetchone()
    if session_row is None:
        raise ValueError(f"missing recovered session {resolved_session_id}")
    rows = conn.execute(
        """
        SELECT role, content, tool_call_id, tool_calls, tool_name, finish_reason,
               reasoning, reasoning_content, reasoning_details, codex_reasoning_items,
               codex_message_items, platform_message_id, observed
        FROM messages
        WHERE session_id = ? AND active = 1
        ORDER BY id
        """,
        (resolved_session_id,),
    ).fetchall()

    messages = []
    for row in rows:
        content = row["content"] or ""
        if row["role"] in {"user", "assistant"}:
            content = content.strip()
        msg = {"role": row["role"], "content": content}
        for key in ("tool_call_id", "tool_name", "finish_reason", "reasoning", "reasoning_content"):
            if row[key]:
                msg[key] = row[key]
        for key in ("tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items"):
            decoded = _json_loads_maybe(row[key])
            if decoded is not None:
                msg[key] = decoded
        if row["platform_message_id"]:
            msg["platform_message_id"] = row["platform_message_id"]
        if row["observed"]:
            msg["observed"] = bool(row["observed"])
        messages.append(msg)

    session = dict(session_row)
    session["model_config"] = _json_loads_maybe(session.get("model_config"))
    return {
        "requested_session_id": requested_session_id,
        "resolved_session_id": resolved_session_id,
        "lineage_hops": lineage_hops,
        "session": session,
        "messages": messages,
    }, lineage_hops


def _measure_hermes_recovery_ns(conn: sqlite3.Connection) -> tuple[float, str, int, int, bool, bytes]:
    samples: list[float] = []
    digests: list[str] = []
    recovered_count = 0
    lineage_hops = 0
    last_payload = b""
    import hashlib

    for _ in range(BASELINE_ROUNDS):
        start = time.perf_counter_ns()
        restored, lineage_hops = _restore_session_payload(conn, BASELINE_TARGET_PARENT_SESSION)
        payload = json.dumps(restored, sort_keys=True, separators=(",", ":")).encode("utf-8")
        hasher = hashlib.blake2b(digest_size=32)
        hasher.update(payload)
        digests.append(hasher.hexdigest())
        recovered_count = len(restored["messages"])
        last_payload = payload
        samples.append(time.perf_counter_ns() - start)
    return (
        float(statistics.median(samples)),
        digests[0],
        recovered_count,
        lineage_hops,
        len(set(digests)) == 1,
        last_payload,
    )


def _evidence_hash(root: Path, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(root, canonical)


def evaluate_hermes_session_recovery_baseline(
    root: str | Path = ROOT,
) -> HermesSessionRecoveryBaselineReport:
    root_path = Path(root)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect_hermes_state_db(BASELINE_DB_PATH)
    try:
        _populate_baseline_db(conn)
        (
            baseline_estimate_ns,
            logical_hash,
            recovered_message_count,
            lineage_hops,
            deterministic_digest_ok,
            recovered_payload,
        ) = _measure_hermes_recovery_ns(conn)
    finally:
        conn.close()

    BASELINE_RECOVERY_PAYLOAD_PATH.write_bytes(recovered_payload)
    db_bytes = BASELINE_DB_PATH.read_bytes()
    baseline_db_hash = _blake3_hex(root_path, db_bytes)
    recovered_payload_hash = _blake3_hex(root_path, recovered_payload)
    aegis_estimate_ns = _criterion_estimate_ns(root_path, AEGIS_BINARY_RECOVERY_BENCHMARK_NAME)
    speedup = (
        baseline_estimate_ns / aegis_estimate_ns
        if aegis_estimate_ns and aegis_estimate_ns > 0
        else None
    )
    source_refs = _source_refs(root_path)
    evidence = {
        "aegis_benchmark_name": AEGIS_BINARY_RECOVERY_BENCHMARK_NAME,
        "aegis_replacement_surface": "BinaryRunEventSegment mmap prefix recovery with per-record hash validation",
        "baseline_surface": "Hermes ACP restart recovery via SQLite sessions/messages plus JSON message hydration",
        "baseline_db_path": str(BASELINE_DB_PATH),
        "baseline_db_bytes": len(db_bytes),
        "baseline_db_hash_blake3": baseline_db_hash,
        "baseline_recovery_payload_path": str(BASELINE_RECOVERY_PAYLOAD_PATH),
        "baseline_recovery_payload_bytes": len(recovered_payload),
        "baseline_recovery_payload_hash_blake3": recovered_payload_hash,
        "baseline_rounds": BASELINE_ROUNDS,
        "baseline_background_sessions": BASELINE_BACKGROUND_SESSIONS,
        "baseline_messages_per_background_session": BASELINE_MESSAGES_PER_BACKGROUND_SESSION,
        "baseline_recovery_messages": BASELINE_RECOVERY_MESSAGES,
        "baseline_content_bytes": BASELINE_CONTENT_BYTES,
        "baseline_requested_session_id": BASELINE_TARGET_PARENT_SESSION,
        "baseline_resolved_session_id": BASELINE_TARGET_SESSION,
        "baseline_lineage_hops": lineage_hops,
        "baseline_recovered_message_count": recovered_message_count,
        "baseline_logical_hash_blake2b": logical_hash,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "hermes_repo_commit": _git_commit(HERMES_REPO_PATH),
        "hermes_source_refs": source_refs,
        "hermes_symbols": [
            "SessionManager._persist",
            "SessionManager._restore",
            "SessionDB.get_session",
            "SessionDB.resolve_resume_session_id",
            "SessionDB.get_messages_as_conversation",
            "save_trajectory",
        ],
        "aegis_symbols": [
            "BinaryRunEventSegment",
            "recover_valid_prefix",
            "binary_run_event_recovery_hash",
            "replay_io_binary_mmap_recover_prefix",
        ],
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(root_path, evidence)

    gate_predicates = {
        "criterion_estimate_present": aegis_estimate_ns is not None,
        "speedup_present": speedup is not None,
        "speedup_threshold_met": (
            speedup is not None and speedup >= HERMES_SESSION_RECOVERY_SPEEDUP_THRESHOLD
        ),
        "baseline_digest_deterministic": deterministic_digest_ok,
        "recovered_message_count_exact": recovered_message_count == BASELINE_RECOVERY_MESSAGES,
        "lineage_hops_exact": lineage_hops == 1,
        "baseline_db_hash_bound": len(baseline_db_hash) == 64,
        "recovered_payload_hash_bound": len(recovered_payload_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": evidence["hermes_repo_commit"] is not None,
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_SESSION_RECOVERY_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_estimate_ns / 1_000_000:.2f} ms Hermes SQLite/JSON recovery vs "
        f"{aegis_estimate_ns / 1_000_000:.2f} ms AEGIS binary mmap prefix recovery)"
        if speedup is not None and aegis_estimate_ns is not None
        else f"missing Criterion estimate for {AEGIS_BINARY_RECOVERY_BENCHMARK_NAME}"
    )
    check = HermesSessionRecoveryBaselineCheck(
        name="hermes_sqlite_json_session_recovery_vs_aegis_binary_mmap_prefix_recovery",
        ok=ok,
        aegis_estimate_ns=aegis_estimate_ns,
        baseline_estimate_ns=baseline_estimate_ns,
        speedup=speedup,
        threshold_speedup=HERMES_SESSION_RECOVERY_SPEEDUP_THRESHOLD,
        detail=detail,
    )
    return HermesSessionRecoveryBaselineReport(
        suite_name="Hermes Session Recovery Baseline Gate",
        overall_ok=check.ok,
        passed=1 if check.ok else 0,
        failed=0 if check.ok else 1,
        checks=[check],
        physical_evidence=evidence,
    )


def report_to_dict(report: HermesSessionRecoveryBaselineReport) -> dict:
    return {
        "suite_name": report.suite_name,
        "overall_ok": report.overall_ok,
        "passed": report.passed,
        "failed": report.failed,
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "aegis_estimate_ns": check.aegis_estimate_ns,
                "baseline_estimate_ns": check.baseline_estimate_ns,
                "speedup": check.speedup,
                "threshold_speedup": check.threshold_speedup,
                "detail": check.detail,
            }
            for check in report.checks
        ],
        "physical_evidence": report.physical_evidence,
    }


def main() -> int:
    report = evaluate_hermes_session_recovery_baseline(ROOT)
    payload = report_to_dict(report)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
