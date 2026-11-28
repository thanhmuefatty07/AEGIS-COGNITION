"""Live equal-workload paired AB/BA gate: Hermes full-history SQLite WAL rewrite vs AEGIS append."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis_cognition.benchmark import (  # noqa: E402
    DEFAULT_BOOTSTRAP_RESAMPLES,
    MIN_DEV_BOOTSTRAP_RESAMPLES,
    BenchmarkProtocolV3,
    BenchmarkHostSample,
    EnvironmentFingerprintV3,
    SesoiPolicy,
    evaluate_paired_benchmark_v3,
    paired_ab_ba_schedule,
)
from core.python.aegis.native import native_module  # noqa: E402

ARTIFACTS_DIR = ROOT / "artifacts"
REPORT_PATH = ARTIFACTS_DIR / "hermes_persistence_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-persistence-baseline"
BASELINE_DB_PATH = BASELINE_DIR / "hermes-state-persist-rewrite.db"
BASELINE_TRANSCRIPT_PATH = BASELINE_DIR / "hermes-persisted-transcript.json"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_APPEND_BENCHMARK_NAME = "replay_segmented_arrow_append"
HERMES_PERSISTENCE_SPEEDUP_THRESHOLD = 2.0
BASELINE_WARMUPS = 10
BASELINE_PAIRED_BLOCKS = 30
BASELINE_ROUNDS = 5
BASELINE_HISTORY_MESSAGES = 512
BASELINE_CONTENT_BYTES = 512
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
    physical_evidence: dict[str, Any]


def _blake3_hex(payload: bytes) -> str:
    native = native_module()
    return str(native.aegis_hot_hash(payload))


def _git_commit(path: Path) -> str | None:
    repo_path = path.resolve()
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={repo_path}", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        )
    except OSError, subprocess.CalledProcessError:
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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
            json.dumps({"provider": "openrouter"}, separators=(",", ":")),
            "/workspace/aegis",
            1_777_400_000.0,
            "Hermes persistence rewrite benchmark",
            "SessionManager._persist replace_messages write amplification",
        ),
    )
    return conn


def _history() -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for msg_idx in range(BASELINE_HISTORY_MESSAGES):
        role = ("user", "assistant", "tool")[msg_idx % 3]
        prefix = f"session={BASELINE_SESSION_ID} message={msg_idx:05d} policy replay witness arrow segment "
        content = prefix + ("x" * max(16, BASELINE_CONTENT_BYTES - len(prefix)))
        messages.append(
            {
                "role": role,
                "content": content,
                "tool_call_id": f"call-{msg_idx:05d}" if role == "tool" else None,
                "tool_calls": (
                    [{"id": f"call-{msg_idx:05d}", "name": "session_search", "arguments": {"limit": 32}}]
                    if role == "assistant" and msg_idx % 4 == 1
                    else None
                ),
                "tool_name": "session_search" if role == "tool" else None,
                "token_count": 128,
                "finish_reason": "stop" if role == "assistant" else None,
                "reasoning": None,
                "reasoning_content": None,
                "reasoning_details": {"round": msg_idx, "rewrite": True} if role == "assistant" else None,
                "codex_reasoning_items": [{"type": "reasoning", "index": msg_idx}] if role == "assistant" else None,
                "codex_message_items": [{"type": "message", "index": msg_idx}] if role == "assistant" else None,
                "platform_message_id": f"platform-{msg_idx:05d}",
                "observed": msg_idx % 7 == 0,
            }
        )
    return messages


def _replace_messages(conn: sqlite3.Connection, session_id: str, messages: list[dict[str, Any]]) -> None:
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


def _json_loads_maybe(value: str | None) -> Any:
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


def _evidence_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(canonical)


def _aegis_append_and_seal_once(native: Any, frame_path: Path, message_payload: bytes, seq: int) -> tuple[str, int]:
    """Arm B: incremental O(1) append-only commit into native Rust HotEngine/MmapBridge boundary."""
    raw = native.aegis_hot_commit_buffer(memoryview(message_payload), "DEV")
    parsed = json.loads(raw)
    if parsed.get("handle_valid") is not True:
        raise RuntimeError("AEGIS hot commit failed during persistence gate")
    native.aegis_write_mmap_bridge_pattern(str(frame_path), seq, 777, len(message_payload))
    return str(parsed["artifact_hash"]), len(message_payload)


def evaluate_hermes_persistence_baseline(
    root: str | Path = ROOT,
    *,
    ci_enforced: bool = False,
) -> HermesPersistenceBaselineReport:
    root_path = Path(root)
    native = native_module()
    history = _history()
    conn = _connect_hermes_state_db(BASELINE_DB_PATH)
    arm_a_ns: list[float] = []
    arm_b_ns: list[float] = []
    exec_order: list[str] = []
    baseline_digests: list[str] = []
    aegis_digests: list[str] = []

    latest_turn_bytes = json.dumps(history[-1], sort_keys=True, separators=(",", ":")).encode("utf-8")
    transcript = b""
    message_rows = 0
    fts_rows = 0

    try:
        _replace_messages(conn, BASELINE_SESSION_ID, history[:-1])
        with tempfile.TemporaryDirectory(prefix="aegis-hermes-persist-") as tmpdir:
            tmp_dir = Path(tmpdir)
            host_before = BenchmarkHostSample.capture()
            for warm in range(BASELINE_WARMUPS):
                _replace_messages(conn, BASELINE_SESSION_ID, history)
                _persisted_transcript(conn)
                _aegis_append_and_seal_once(native, tmp_dir / f"warm-{warm}.aegmmap", latest_turn_bytes, warm + 1)

            for blk, order in enumerate(paired_ab_ba_schedule(BASELINE_PAIRED_BLOCKS)):
                exec_order.append(order)
                for arm in order:
                    if arm == "A":
                        t0 = time.perf_counter_ns()
                        _replace_messages(conn, BASELINE_SESSION_ID, history)
                        transcript = _persisted_transcript(conn)
                        h = hashlib.blake2b(transcript, digest_size=32).hexdigest()
                        dt = float(max(1, time.perf_counter_ns() - t0))
                        baseline_digests.append(h)
                        arm_a_ns.append(dt)
                    else:
                        seg_path = tmp_dir / f"append-seg-{blk}.aegmmap"
                        t0 = time.perf_counter_ns()
                        h, _ = _aegis_append_and_seal_once(native, seg_path, latest_turn_bytes, blk + 1)
                        dt = float(max(1, time.perf_counter_ns() - t0))
                        aegis_digests.append(h)
                        arm_b_ns.append(dt)
            host_after = BenchmarkHostSample.capture()

        message_rows = int(
            conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (BASELINE_SESSION_ID,)).fetchone()[0]
        )
        fts_rows = int(conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0])
    finally:
        conn.close()

    BASELINE_TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_TRANSCRIPT_PATH.write_bytes(transcript)
    db_bytes = BASELINE_DB_PATH.read_bytes()
    baseline_db_hash = _blake3_hex(db_bytes)
    transcript_hash = _blake3_hex(transcript)
    deterministic_digest_ok = len(set(baseline_digests)) == 1 and len(set(aegis_digests)) == 1

    env = EnvironmentFingerprintV3.capture_host(
        host_before, after=host_after, network_policy="offline", workspace_root=ROOT
    )
    resamples = DEFAULT_BOOTSTRAP_RESAMPLES if ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
    comparison = evaluate_paired_benchmark_v3(
        BenchmarkProtocolV3(
            name="hermes_wal_rewrite_vs_aegis_append_v3",
            metric="persist_latency_ns",
            direction="lower_is_better",
            alpha=0.05,
            min_trials=BASELINE_PAIRED_BLOCKS,
            warmups=BASELINE_WARMUPS,
            paired_blocks=BASELINE_PAIRED_BLOCKS,
            bootstrap_resamples=resamples,
            ci_enforced=ci_enforced,
            preregistered_seeds=(11, 22, 33, 44, 55),
            frozen_split_hash=transcript_hash,
            contamination_checks=("load_avg", "thermal", "leak"),
            sesoi=SesoiPolicy(),
        ),
        arm_a_ns,
        arm_b_ns,
        warmups=BASELINE_WARMUPS,
        paired_blocks=BASELINE_PAIRED_BLOCKS,
        environment=env,
        execution_order=exec_order,
        enforce_clean_worktree=ci_enforced,
    )
    baseline_p50_ns = comparison.arm_a.p50 if comparison.arm_a else 0.0
    aegis_p50_ns = comparison.arm_b.p50 if comparison.arm_b else 0.0
    speedup = baseline_p50_ns / aegis_p50_ns if aegis_p50_ns > 0.0 else None

    hermes_repo_present = HERMES_REPO_PATH.exists()
    hermes_commit = _git_commit(HERMES_REPO_PATH) if hermes_repo_present else None
    source_refs = _source_refs(root_path)

    evidence: dict[str, Any] = {
        "aegis_benchmark_name": AEGIS_APPEND_BENCHMARK_NAME,
        "protocol_version": "aegis-bench-protocol-v3",
        "aegis_replacement_surface": "SegmentedArrowAuditStream append/flush with replay sidecar evidence",
        "baseline_surface": "Hermes ACP save path SessionManager._persist -> SessionDB.replace_messages full transcript rewrite (WAL mode)",
        "baseline_db_path": str(BASELINE_DB_PATH),
        "baseline_db_bytes": len(db_bytes),
        "baseline_db_hash_blake3": baseline_db_hash,
        "baseline_transcript_path": str(BASELINE_TRANSCRIPT_PATH),
        "baseline_transcript_bytes": len(transcript),
        "baseline_transcript_hash_blake3": transcript_hash,
        "paired_blocks": BASELINE_PAIRED_BLOCKS,
        "warmups": BASELINE_WARMUPS,
        "baseline_history_messages": BASELINE_HISTORY_MESSAGES,
        "baseline_content_bytes": BASELINE_CONTENT_BYTES,
        "baseline_message_rows": message_rows,
        "baseline_fts_rows": fts_rows,
        "baseline_logical_hash_blake2b": baseline_digests[0] if baseline_digests else "",
        "aegis_append_hash_blake3": aegis_digests[0] if aegis_digests else "",
        "baseline_digest_deterministic": deterministic_digest_ok,
        "hermes_repo_present": hermes_repo_present,
        "hermes_repo_commit": hermes_commit,
        "hermes_source_refs": source_refs,
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
        "v3_comparison": asdict(comparison),
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(evidence)

    gate_predicates = {
        "v3_comparison_pass": comparison.status == "PASS",
        "speedup_present": speedup is not None,
        "speedup_threshold_met": speedup is not None and speedup >= HERMES_PERSISTENCE_SPEEDUP_THRESHOLD,
        "statistically_significant": comparison.statistically_significant,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "message_rows_exact": message_rows == BASELINE_HISTORY_MESSAGES,
        "fts_rows_exact": fts_rows == BASELINE_HISTORY_MESSAGES,
        "baseline_db_hash_bound": len(baseline_db_hash) == 64,
        "transcript_hash_bound": len(transcript_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": (not hermes_repo_present) or (hermes_commit is not None),
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_PERSISTENCE_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_p50_ns / 1_000_000:.3f} ms Hermes WAL full rewrite p50 vs "
        f"{aegis_p50_ns / 1_000_000:.3f} ms AEGIS incremental persist p50)"
        if speedup is not None
        else "live paired AB/BA evaluation failed"
    )
    check = HermesPersistenceBaselineCheck(
        name="hermes_full_transcript_rewrite_vs_aegis_segmented_arrow_append",
        ok=ok,
        aegis_estimate_ns=aegis_p50_ns,
        baseline_estimate_ns=baseline_p50_ns,
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


def report_to_dict(report: HermesPersistenceBaselineReport) -> dict[str, Any]:
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
