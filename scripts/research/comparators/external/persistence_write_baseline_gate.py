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
REPORT_PATH = ARTIFACTS_DIR / "hermes_persistence_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-persistence-baseline"
BASELINE_DB_PATH = BASELINE_DIR / "hermes-state-persist-rewrite.db"
BASELINE_TRANSCRIPT_PATH = BASELINE_DIR / "hermes-persisted-transcript.json"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_APPEND_BENCHMARK_NAME = "replay_segmented_arrow_append"
HERMES_PERSISTENCE_SPEEDUP_THRESHOLD = 3.0
BASELINE_ROUNDS = 5
BASELINE_HISTORY_MESSAGES = 4096
BASELINE_CONTENT_BYTES = 640
BASELINE_SESSION_ID = "acp-persist-rewrite-session"


@dataclass(frozen=True)
class HermesPersistenceBaselineCheck:
    name: str
    ok: bool
    aegis_estimate_ns: float | None
    baseline_estimate_ns: float | None
    speedup: float | None
    threshold_speedup: float
    detail: str


@dataclass(frozen=True)
class HermesPersistenceBaselineReport:
    suite_name: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[HermesPersistenceBaselineCheck]
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
        "hermes_acp_session_persist": "artifacts/research/hermes-agent/acp_adapter/session.py:423",
        "hermes_acp_replace_messages_call": "artifacts/research/hermes-agent/acp_adapter/session.py:467",
        "hermes_state_append_message": "artifacts/research/hermes-agent/hermes_state.py:1996",
        "hermes_state_replace_messages": "artifacts/research/hermes-agent/hermes_state.py:2093",
        "hermes_state_get_messages": "artifacts/research/hermes-agent/hermes_state.py:2179",
        "aegis_segmented_arrow_append": "core/rust/src/replay.rs:2937",
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
            model TEXT,
            model_config TEXT,
            cwd TEXT,
            started_at REAL NOT NULL,
            title TEXT,
            preview TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            token_count INTEGER,
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
        CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content) VALUES (
                new.id,
                COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
            );
        END;
        CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.id;
        END;
        CREATE TRIGGER messages_fts_update AFTER UPDATE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.id;
            INSERT INTO messages_fts(rowid, content) VALUES (
                new.id,
                COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
            );
        END;
        CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(content, tokenize='trigram');
        CREATE TRIGGER messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts_trigram(rowid, content) VALUES (
                new.id,
                COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
            );
        END;
        CREATE TRIGGER messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts_trigram WHERE rowid = old.id;
        END;
        CREATE TRIGGER messages_fts_trigram_update AFTER UPDATE ON messages BEGIN
            DELETE FROM messages_fts_trigram WHERE rowid = old.id;
            INSERT INTO messages_fts_trigram(rowid, content) VALUES (
                new.id,
                COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
            );
        END;
        """
    )
    conn.execute(
        """
        INSERT INTO sessions(id, source, model, model_config, cwd, started_at, title, preview)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            BASELINE_SESSION_ID,
            "acp",
            "hermes-4-preview",
            json.dumps({"cwd": "/workspace/aegis"}, separators=(",", ":")),
            "/workspace/aegis",
            1_777_300_000.0,
            "Hermes ACP persist rewrite baseline",
            "policy replay witness append-only replacement",
        ),
    )
    conn.commit()
    return conn


def _content(msg_idx: int) -> str:
    prefix = (
        f"session={BASELINE_SESSION_ID} message={msg_idx:05d} "
        "policy replay witness checkpoint evidence fts rewrite "
    )
    if len(prefix) >= BASELINE_CONTENT_BYTES:
        return prefix[:BASELINE_CONTENT_BYTES]
    return prefix + ("x" * (BASELINE_CONTENT_BYTES - len(prefix)))


def _history() -> list[dict]:
    messages = []
    for msg_idx in range(BASELINE_HISTORY_MESSAGES):
        role = ("user", "assistant", "tool")[msg_idx % 3]
        tool_calls = None
        if role == "assistant":
            tool_calls = [
                {
                    "id": f"tool-{msg_idx:05d}",
                    "name": "session_search" if msg_idx % 5 == 0 else "memory",
                    "args": {
                        "query": "policy replay witness append",
                        "limit": 32,
                        "message": msg_idx,
                    },
                }
            ]
        messages.append(
            {
                "role": role,
                "content": _content(msg_idx),
                "tool_call_id": f"call-{msg_idx:05d}" if role == "tool" else None,
                "tool_calls": tool_calls,
                "tool_name": "session_search" if role == "tool" and msg_idx % 5 == 0 else None,
                "token_count": BASELINE_CONTENT_BYTES // 4,
                "finish_reason": "stop" if role == "assistant" else None,
                "reasoning": "whole transcript rewrite" if role == "assistant" and msg_idx % 11 == 0 else None,
                "reasoning_content": "append-only replay avoids this path" if role == "assistant" and msg_idx % 13 == 0 else None,
                "reasoning_details": {"round": msg_idx, "rewrite": True} if role == "assistant" else None,
                "codex_reasoning_items": [{"type": "reasoning", "index": msg_idx}] if role == "assistant" else None,
                "codex_message_items": [{"type": "message", "index": msg_idx}] if role == "assistant" else None,
                "platform_message_id": f"platform-{msg_idx:05d}",
                "observed": msg_idx % 7 == 0,
            }
        )
    return messages


def _replace_messages(conn: sqlite3.Connection, session_id: str, messages: list[dict]) -> None:
    with conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute(
            "UPDATE sessions SET message_count = 0, tool_call_count = 0 WHERE id = ?",
            (session_id,),
        )
        now_ts = 1_777_400_000.0
        total_tool_calls = 0
        for msg in messages:
            role = msg.get("role", "unknown")
            tool_calls = msg.get("tool_calls")
            reasoning_details = msg.get("reasoning_details") if role == "assistant" else None
            codex_reasoning_items = msg.get("codex_reasoning_items") if role == "assistant" else None
            codex_message_items = msg.get("codex_message_items") if role == "assistant" else None
            tool_calls_json = json.dumps(tool_calls, separators=(",", ":")) if tool_calls else None
            conn.execute(
                """
                INSERT INTO messages(
                    session_id, role, content, tool_call_id, tool_calls, tool_name,
                    timestamp, token_count, finish_reason, reasoning, reasoning_content,
                    reasoning_details, codex_reasoning_items, codex_message_items,
                    platform_message_id, observed
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    msg.get("content"),
                    msg.get("tool_call_id"),
                    tool_calls_json,
                    msg.get("tool_name"),
                    now_ts,
                    msg.get("token_count"),
                    msg.get("finish_reason"),
                    msg.get("reasoning") if role == "assistant" else None,
                    msg.get("reasoning_content") if role == "assistant" else None,
                    json.dumps(reasoning_details, separators=(",", ":")) if reasoning_details else None,
                    json.dumps(codex_reasoning_items, separators=(",", ":")) if codex_reasoning_items else None,
                    json.dumps(codex_message_items, separators=(",", ":")) if codex_message_items else None,
                    msg.get("platform_message_id"),
                    1 if msg.get("observed") else 0,
                ),
            )
            if tool_calls is not None:
                total_tool_calls += len(tool_calls) if isinstance(tool_calls, list) else 1
            now_ts += 0.000001
        conn.execute(
            "UPDATE sessions SET message_count = ?, tool_call_count = ? WHERE id = ?",
            (len(messages), total_tool_calls, session_id),
        )


def _json_loads_maybe(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _persisted_transcript(conn: sqlite3.Connection) -> bytes:
    rows = conn.execute(
        """
        SELECT role, content, tool_call_id, tool_calls, tool_name, timestamp,
               token_count, finish_reason, reasoning, reasoning_content,
               reasoning_details, codex_reasoning_items, codex_message_items,
               platform_message_id, observed
        FROM messages
        WHERE session_id = ? AND active = 1
        ORDER BY id
        """,
        (BASELINE_SESSION_ID,),
    ).fetchall()
    messages = []
    for row in rows:
        msg = dict(row)
        for key in ("tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items"):
            decoded = _json_loads_maybe(msg.get(key))
            if decoded is not None:
                msg[key] = decoded
        messages.append(msg)
    payload = {
        "session_id": BASELINE_SESSION_ID,
        "message_count": len(messages),
        "messages": messages,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _measure_hermes_persist_ns(conn: sqlite3.Connection, history: list[dict]) -> tuple[float, str, int, int, bool, bytes]:
    samples: list[float] = []
    digests: list[str] = []
    import hashlib

    for _ in range(BASELINE_ROUNDS):
        start = time.perf_counter_ns()
        _replace_messages(conn, BASELINE_SESSION_ID, history)
        transcript = _persisted_transcript(conn)
        hasher = hashlib.blake2b(digest_size=32)
        hasher.update(transcript)
        digests.append(hasher.hexdigest())
        samples.append(time.perf_counter_ns() - start)
    message_rows = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
        (BASELINE_SESSION_ID,),
    ).fetchone()[0]
    fts_rows = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    return (
        float(statistics.median(samples)),
        digests[0],
        int(message_rows),
        int(fts_rows),
        len(set(digests)) == 1,
        transcript,
    )


def _evidence_hash(root: Path, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(root, canonical)


def evaluate_hermes_persistence_baseline(root: str | Path = ROOT) -> HermesPersistenceBaselineReport:
    root_path = Path(root)
    history = _history()
    conn = _connect_hermes_state_db(BASELINE_DB_PATH)
    try:
        _replace_messages(conn, BASELINE_SESSION_ID, history[:-1])
        (
            baseline_estimate_ns,
            logical_hash,
            message_rows,
            fts_rows,
            deterministic_digest_ok,
            transcript,
        ) = _measure_hermes_persist_ns(conn, history)
    finally:
        conn.close()

    BASELINE_TRANSCRIPT_PATH.write_bytes(transcript)
    db_bytes = BASELINE_DB_PATH.read_bytes()
    baseline_db_hash = _blake3_hex(root_path, db_bytes)
    transcript_hash = _blake3_hex(root_path, transcript)
    aegis_estimate_ns = _criterion_estimate_ns(root_path, AEGIS_APPEND_BENCHMARK_NAME)
    speedup = (
        baseline_estimate_ns / aegis_estimate_ns
        if aegis_estimate_ns and aegis_estimate_ns > 0
        else None
    )
    evidence = {
        "aegis_benchmark_name": AEGIS_APPEND_BENCHMARK_NAME,
        "aegis_replacement_surface": "SegmentedArrowAuditStream append/flush with replay sidecar evidence",
        "baseline_surface": "Hermes ACP save path SessionManager._persist -> SessionDB.replace_messages full transcript rewrite",
        "baseline_db_path": str(BASELINE_DB_PATH),
        "baseline_db_bytes": len(db_bytes),
        "baseline_db_hash_blake3": baseline_db_hash,
        "baseline_transcript_path": str(BASELINE_TRANSCRIPT_PATH),
        "baseline_transcript_bytes": len(transcript),
        "baseline_transcript_hash_blake3": transcript_hash,
        "baseline_rounds": BASELINE_ROUNDS,
        "baseline_history_messages": BASELINE_HISTORY_MESSAGES,
        "baseline_content_bytes": BASELINE_CONTENT_BYTES,
        "baseline_message_rows": message_rows,
        "baseline_fts_rows": fts_rows,
        "baseline_logical_hash_blake2b": logical_hash,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "hermes_repo_commit": _git_commit(HERMES_REPO_PATH),
        "hermes_source_refs": _source_refs(root_path),
        "hermes_symbols": [
            "SessionManager._persist",
            "SessionDB.replace_messages",
            "DELETE FROM messages WHERE session_id = ?",
            "messages_fts_insert",
            "messages_fts_trigram_insert",
        ],
        "aegis_symbols": [
            "SegmentedArrowAuditStream",
            "RunEventSegmentEntry",
            "RunEventSegmentCommitProof",
            "replay_segmented_arrow_append",
        ],
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(root_path, evidence)

    gate_predicates = {
        "criterion_estimate_present": aegis_estimate_ns is not None,
        "speedup_present": speedup is not None,
        "speedup_threshold_met": speedup is not None and speedup >= HERMES_PERSISTENCE_SPEEDUP_THRESHOLD,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "message_rows_exact": message_rows == BASELINE_HISTORY_MESSAGES,
        "fts_rows_exact": fts_rows == BASELINE_HISTORY_MESSAGES,
        "baseline_db_hash_bound": len(baseline_db_hash) == 64,
        "transcript_hash_bound": len(transcript_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": evidence["hermes_repo_commit"] is not None,
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_PERSISTENCE_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_estimate_ns / 1_000_000:.2f} ms Hermes full transcript rewrite vs "
        f"{aegis_estimate_ns / 1_000_000:.2f} ms AEGIS segmented Arrow append)"
        if speedup is not None and aegis_estimate_ns is not None
        else f"missing Criterion estimate for {AEGIS_APPEND_BENCHMARK_NAME}"
    )
    check = HermesPersistenceBaselineCheck(
        name="hermes_full_transcript_rewrite_vs_aegis_segmented_arrow_append",
        ok=ok,
        aegis_estimate_ns=aegis_estimate_ns,
        baseline_estimate_ns=baseline_estimate_ns,
        speedup=speedup,
        threshold_speedup=HERMES_PERSISTENCE_SPEEDUP_THRESHOLD,
        detail=detail,
    )
    return HermesPersistenceBaselineReport(
        suite_name="Hermes Persistence Write-Amplification Baseline Gate",
        overall_ok=check.ok,
        passed=1 if check.ok else 0,
        failed=0 if check.ok else 1,
        checks=[check],
        physical_evidence=evidence,
    )


def report_to_dict(report: HermesPersistenceBaselineReport) -> dict:
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
    report = evaluate_hermes_persistence_baseline(ROOT)
    payload = report_to_dict(report)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
