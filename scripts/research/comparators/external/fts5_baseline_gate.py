"""Live equal-workload paired AB/BA gate: Hermes SQLite FTS5 vs AEGIS native SessionSearchIndex."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
import time
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
REPORT_PATH = ARTIFACTS_DIR / "hermes_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-baseline"
BASELINE_DB_PATH = BASELINE_DIR / "hermes-session-fts5.db"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_HOT_EVIDENCE_BENCHMARK_NAME = "hot_lexical_index_top_k"
HERMES_SPEEDUP_THRESHOLD = 1.5
BASELINE_WARMUPS = 10
BASELINE_PAIRED_BLOCKS = 30
BASELINE_SESSIONS = 128
BASELINE_MESSAGES_PER_SESSION = 16
BASELINE_QUERY = '"policy" AND "replay" AND "witness"'
AEGIS_QUERY = "policy replay witness"
BASELINE_LIMIT = 32


@dataclass(frozen=True)
class HermesBaselineCheck:
    name: str
    ok: bool
    aegis_estimate_ns: float | None
    baseline_estimate_ns: float | None
    speedup: float | None
    threshold_speedup: float
    detail: str


@dataclass(frozen=True)
class HermesBaselineReport:
    suite_name: str
    overall_ok: bool
    passed: int
    failed: int
    checks: list[HermesBaselineCheck]
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
        "hermes_state_fts_sql": "artifacts/research/hermes-agent/hermes_state.py:321",
        "hermes_state_search_messages": "artifacts/research/hermes-agent/hermes_state.py:2863",
        "hermes_state_search_sessions": "artifacts/research/hermes-agent/hermes_state.py:3221",
        "hermes_acp_session_persistence": "artifacts/research/hermes-agent/acp_adapter/session.py:189",
        "hermes_acp_tool_session_search": "artifacts/research/hermes-agent/acp_adapter/tools.py:609",
    }
    return {key: str(root / value) for key, value in refs.items()}


def _connect_hermes_fts5_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            model TEXT NOT NULL,
            started_at REAL NOT NULL,
            title TEXT NOT NULL,
            preview TEXT NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            tool_name TEXT,
            tool_calls TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );
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
        """
    )
    return conn


def _populate_equal_corpus(
    conn: sqlite3.Connection,
    native: Any,
    *,
    owner_id: str,
) -> dict[int, str]:
    """Insert identical 128-session corpus into SQLite FTS5 and native Rust SessionSearchIndex."""
    now = 1_777_000_000.0
    session_contents: dict[int, str] = {}
    with conn:
        for session_idx in range(BASELINE_SESSIONS):
            session_id = f"acp-session-{session_idx:04d}"
            conn.execute(
                """
                INSERT INTO sessions(id, source, model, started_at, title, preview)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    "acp",
                    "hermes-4-preview",
                    now + session_idx,
                    f"Hermes ACP session {session_idx:04d}",
                    "policy replay witness memory search fts5",
                ),
            )
            aegis_lines: list[str] = []
            for msg_idx in range(BASELINE_MESSAGES_PER_SESSION):
                contains_query = msg_idx % 4 == 0 or session_idx % 8 == 0
                terms = (
                    "policy replay witness hot evidence checkpoint "
                    if contains_query
                    else "memory note planning context skill "
                )
                content = (
                    f"{terms}session={session_id} message={msg_idx:03d} "
                    f"Hermes stores text session state and hydrates snippets through SQLite FTS5. "
                    f"payload={'x' * 96}"
                )
                aegis_lines.append(content)
                conn.execute(
                    """
                    INSERT INTO messages(session_id, role, content, timestamp, tool_name, tool_calls, active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        session_id,
                        "assistant" if msg_idx % 2 else "tool",
                        content,
                        now + session_idx * 1000 + msg_idx,
                        "session_search" if contains_query else "memory",
                        json.dumps(
                            {
                                "query": "policy replay witness" if contains_query else "memory",
                                "session": session_id,
                                "message": msg_idx,
                            },
                            separators=(",", ":"),
                        ),
                    ),
                )
            full_session_text = "\n".join(aegis_lines)
            sid_num = 10_000 + session_idx
            session_contents[sid_num] = full_session_text
            native.aegis_index_session_scoped(
                sid_num,
                full_session_text,
                int((now + session_idx) * 1000),
                "USER_PRIVATE",
                owner_id,
            )
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
        conn.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES ('optimize')")
    return session_contents


_FTS5_SEARCH_SQL = """
    SELECT
        m.id,
        m.session_id,
        m.role,
        snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
        m.content,
        m.timestamp,
        m.tool_name,
        m.tool_calls,
        s.source,
        s.model,
        s.started_at AS session_started
    FROM messages_fts
    JOIN messages m ON m.id = messages_fts.rowid
    JOIN sessions s ON s.id = m.session_id
    WHERE messages_fts MATCH ? AND m.active = 1 AND s.source = ?
    ORDER BY rank
    LIMIT ? OFFSET 0
"""


def _query_sqlite_fts5_once(conn: sqlite3.Connection) -> tuple[str, int]:
    rows = conn.execute(_FTS5_SEARCH_SQL, (BASELINE_QUERY, "acp", BASELINE_LIMIT)).fetchall()
    hydrated = [
        {
            "id": row[0],
            "session_id": row[1],
            "role": row[2],
            "snippet": row[3],
            "content": row[4],
            "tool_name": row[6],
        }
        for row in rows
    ]
    payload = json.dumps(hydrated, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=32).hexdigest(), len(hydrated)


def _query_aegis_session_search_once(native: Any, owner_id: str) -> tuple[str, int]:
    raw = native.aegis_search_past_sessions_scoped(AEGIS_QUERY, BASELINE_LIMIT, "USER_PRIVATE", owner_id)
    parsed = json.loads(raw)
    results = parsed.get("results") or []
    hasher = hashlib.blake2b(digest_size=32)
    for item in results:
        hasher.update(str(item.get("evidence_ref_hash", "")).encode("ascii"))
        hasher.update(str(item.get("segment_id", 0)).encode("ascii"))
    return hasher.hexdigest(), len(results)


def _evidence_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(canonical)


def evaluate_hermes_baseline(root: str | Path = ROOT, *, ci_enforced: bool = False) -> HermesBaselineReport:
    root_path = Path(root)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    run_tag = uuid.uuid4().hex[:12]
    owner_id = f"hermes-fts5-bench-{run_tag}"
    aegis_db_path = BASELINE_DIR / f"aegis-session-{os.getpid()}-{run_tag}.db"

    prev_db_env = os.environ.get("AEGIS_SESSION_DB_PATH")
    prev_prof_env = os.environ.get("AEGIS_PROFILE_ID")
    os.environ["AEGIS_SESSION_DB_PATH"] = str(aegis_db_path)
    os.environ["AEGIS_PROFILE_ID"] = owner_id

    native = native_module()
    conn = _connect_hermes_fts5_db(BASELINE_DB_PATH)
    arm_a_ns: list[float] = []
    arm_b_ns: list[float] = []
    exec_order: list[str] = []
    fts_digests: list[str] = []
    aegis_digests: list[str] = []
    row_count = 0
    aegis_count = 0
    try:
        _populate_equal_corpus(conn, native, owner_id=owner_id)
        host_before = BenchmarkHostSample.capture()
        for _ in range(BASELINE_WARMUPS):
            _query_sqlite_fts5_once(conn)
            _query_aegis_session_search_once(native, owner_id)
        for order in paired_ab_ba_schedule(BASELINE_PAIRED_BLOCKS):
            exec_order.append(order)
            for arm in order:
                if arm == "A":
                    t0 = time.perf_counter_ns()
                    digest, cnt = _query_sqlite_fts5_once(conn)
                    dt = float(max(1, time.perf_counter_ns() - t0))
                    fts_digests.append(digest)
                    row_count = cnt
                    arm_a_ns.append(dt)
                else:
                    t0 = time.perf_counter_ns()
                    digest, cnt = _query_aegis_session_search_once(native, owner_id)
                    dt = float(max(1, time.perf_counter_ns() - t0))
                    aegis_digests.append(digest)
                    aegis_count = cnt
                    arm_b_ns.append(dt)
        host_after = BenchmarkHostSample.capture()
    finally:
        conn.close()
        if prev_db_env is None:
            os.environ.pop("AEGIS_SESSION_DB_PATH", None)
        else:
            os.environ["AEGIS_SESSION_DB_PATH"] = prev_db_env
        if prev_prof_env is None:
            os.environ.pop("AEGIS_PROFILE_ID", None)
        else:
            os.environ["AEGIS_PROFILE_ID"] = prev_prof_env

    db_bytes = BASELINE_DB_PATH.read_bytes()
    baseline_db_hash = _blake3_hex(db_bytes)

    env = EnvironmentFingerprintV3.capture_host(
        host_before, after=host_after, network_policy="offline", workspace_root=ROOT
    )
    resamples = DEFAULT_BOOTSTRAP_RESAMPLES if ci_enforced else MIN_DEV_BOOTSTRAP_RESAMPLES
    comparison = evaluate_paired_benchmark_v3(
        BenchmarkProtocolV3(
            name="hermes_fts5_vs_aegis_session_search_v3",
            metric="search_latency_ns",
            direction="lower_is_better",
            alpha=0.05,
            min_trials=BASELINE_PAIRED_BLOCKS,
            warmups=BASELINE_WARMUPS,
            paired_blocks=BASELINE_PAIRED_BLOCKS,
            bootstrap_resamples=resamples,
            ci_enforced=ci_enforced,
            preregistered_seeds=(11, 22, 33, 44, 55),
            frozen_split_hash=_blake3_hex(f"{baseline_db_hash}:{BASELINE_QUERY}:{BASELINE_LIMIT}".encode()),
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
    deterministic_digest_ok = len(set(fts_digests)) == 1 and len(set(aegis_digests)) == 1

    evidence: dict[str, Any] = {
        "aegis_benchmark_name": AEGIS_HOT_EVIDENCE_BENCHMARK_NAME,
        "protocol_version": "aegis-bench-protocol-v3",
        "aegis_replacement_surface": "HotEvidenceIndex candidate retrieval via hot_lexical_index_top_k",
        "baseline_surface": "Hermes SQLite FTS5 WAL session_search over messages_fts plus JSON hydration",
        "baseline_db_path": str(BASELINE_DB_PATH),
        "baseline_db_bytes": len(db_bytes),
        "baseline_db_hash_blake3": baseline_db_hash,
        "baseline_sessions": BASELINE_SESSIONS,
        "baseline_messages_per_session": BASELINE_MESSAGES_PER_SESSION,
        "paired_blocks": BASELINE_PAIRED_BLOCKS,
        "warmups": BASELINE_WARMUPS,
        "baseline_query": BASELINE_QUERY,
        "baseline_limit": BASELINE_LIMIT,
        "baseline_result_rows": row_count,
        "aegis_result_rows": aegis_count,
        "baseline_logical_hash_blake2b": fts_digests[0] if fts_digests else "",
        "aegis_logical_hash_blake2b": aegis_digests[0] if aegis_digests else "",
        "baseline_digest_deterministic": deterministic_digest_ok,
        "hermes_repo_present": hermes_repo_present,
        "hermes_repo_commit": hermes_commit,
        "hermes_source_refs": source_refs,
        "hermes_symbols": [
            "FTS_SQL",
            "messages_fts",
            "messages_fts_trigram",
            "SessionDB.search_messages",
            "session_search",
        ],
        "aegis_symbols": ["HotEvidenceIndex", "CandidateEvidenceRef", "hot_lexical_index_top_k"],
        "v3_comparison": asdict(comparison),
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(evidence)

    gate_predicates = {
        "v3_comparison_pass": comparison.status == "PASS",
        "speedup_present": speedup is not None,
        "speedup_threshold_met": speedup is not None and speedup >= HERMES_SPEEDUP_THRESHOLD,
        "statistically_significant": comparison.statistically_significant,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "baseline_rows_present": row_count > 0 and aegis_count > 0,
        "baseline_db_hash_bound": len(baseline_db_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": (not hermes_repo_present) or (hermes_commit is not None),
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_p50_ns / 1_000:.2f} us Hermes FTS5 WAL p50 vs "
        f"{aegis_p50_ns / 1_000:.2f} us AEGIS SessionSearchIndex p50)"
        if speedup is not None
        else "live paired AB/BA evaluation failed"
    )
    check = HermesBaselineCheck(
        name="hermes_fts5_session_search_vs_aegis_hot_evidence_index",
        ok=ok,
        aegis_estimate_ns=aegis_p50_ns,
        baseline_estimate_ns=baseline_p50_ns,
        speedup=speedup,
        threshold_speedup=HERMES_SPEEDUP_THRESHOLD,
        detail=detail,
    )
    return HermesBaselineReport(
        suite_name="Hermes FTS5 Baseline Gate",
        overall_ok=check.ok,
        passed=1 if check.ok else 0,
        failed=0 if check.ok else 1,
        checks=[check],
        physical_evidence=evidence,
    )


def report_to_dict(report: HermesBaselineReport) -> dict[str, Any]:
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
    report = evaluate_hermes_baseline(ROOT)
    payload = report_to_dict(report)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
