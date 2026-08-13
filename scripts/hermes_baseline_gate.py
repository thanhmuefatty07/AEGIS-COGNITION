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
REPORT_PATH = ARTIFACTS_DIR / "hermes_baseline_gate_report.json"
BASELINE_DIR = ROOT / "target" / "aegis-hermes-baseline"
BASELINE_DB_PATH = BASELINE_DIR / "hermes-session-fts5.db"
HERMES_REPO_PATH = ROOT / "artifacts" / "research" / "hermes-agent"
AEGIS_HOT_EVIDENCE_BENCHMARK_NAME = "hot_lexical_index_top_k"
HERMES_SPEEDUP_THRESHOLD = 3.0
BASELINE_ROUNDS = 7
BASELINE_SESSIONS = 128
BASELINE_MESSAGES_PER_SESSION = 48
BASELINE_QUERY = '"policy" AND "replay" AND "witness"'
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
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
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


def _populate_baseline_db(conn: sqlite3.Connection) -> None:
    now = 1_777_000_000.0
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
            for msg_idx in range(BASELINE_MESSAGES_PER_SESSION):
                contains_query = msg_idx % 5 == 0 or session_idx % 11 == 0
                terms = (
                    "policy replay witness hot evidence checkpoint "
                    if contains_query
                    else "memory note planning context skill "
                )
                content = (
                    f"{terms}session={session_id} message={msg_idx:03d} "
                    f"Hermes stores text session state and hydrates snippets through SQLite FTS5. "
                    f"payload={'x' * 192}"
                )
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
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('optimize')")
        conn.execute("INSERT INTO messages_fts_trigram(messages_fts_trigram) VALUES ('optimize')")


def _measure_hermes_fts5_ns(conn: sqlite3.Connection) -> tuple[float, str, int, bool]:
    samples: list[float] = []
    digests: list[str] = []
    row_count = 0
    sql = """
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
    import hashlib

    for _ in range(BASELINE_ROUNDS):
        start = time.perf_counter_ns()
        rows = conn.execute(sql, (BASELINE_QUERY, "acp", BASELINE_LIMIT)).fetchall()
        hydrated = []
        for row in rows:
            tool_calls = json.loads(row[7]) if row[7] else None
            hydrated.append(
                {
                    "id": row[0],
                    "session_id": row[1],
                    "role": row[2],
                    "snippet": row[3],
                    "content": row[4],
                    "timestamp": row[5],
                    "tool_name": row[6],
                    "tool_calls": tool_calls,
                    "source": row[8],
                    "model": row[9],
                    "session_started": row[10],
                }
            )
        payload = json.dumps(hydrated, sort_keys=True, separators=(",", ":")).encode("utf-8")
        hasher = hashlib.blake2b(digest_size=32)
        hasher.update(payload)
        digests.append(hasher.hexdigest())
        row_count = len(hydrated)
        samples.append(time.perf_counter_ns() - start)
    return float(statistics.median(samples)), digests[0], row_count, len(set(digests)) == 1


def _evidence_hash(root: Path, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _blake3_hex(root, canonical)


def evaluate_hermes_baseline(root: str | Path = ROOT) -> HermesBaselineReport:
    root_path = Path(root)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect_hermes_fts5_db(BASELINE_DB_PATH)
    try:
        _populate_baseline_db(conn)
        baseline_estimate_ns, logical_hash, row_count, deterministic_digest_ok = _measure_hermes_fts5_ns(conn)
    finally:
        conn.close()

    db_bytes = BASELINE_DB_PATH.read_bytes()
    baseline_db_hash = _blake3_hex(root_path, db_bytes)
    aegis_estimate_ns = _criterion_estimate_ns(root_path, AEGIS_HOT_EVIDENCE_BENCHMARK_NAME)
    speedup = (
        baseline_estimate_ns / aegis_estimate_ns
        if aegis_estimate_ns and aegis_estimate_ns > 0
        else None
    )
    source_refs = _source_refs(root_path)
    evidence = {
        "aegis_benchmark_name": AEGIS_HOT_EVIDENCE_BENCHMARK_NAME,
        "aegis_replacement_surface": "HotEvidenceIndex candidate retrieval via hot_lexical_index_top_k",
        "baseline_surface": "Hermes SQLite FTS5 session_search over messages_fts plus JSON hydration",
        "baseline_db_path": str(BASELINE_DB_PATH),
        "baseline_db_bytes": len(db_bytes),
        "baseline_db_hash_blake3": baseline_db_hash,
        "baseline_sessions": BASELINE_SESSIONS,
        "baseline_messages_per_session": BASELINE_MESSAGES_PER_SESSION,
        "baseline_rounds": BASELINE_ROUNDS,
        "baseline_query": BASELINE_QUERY,
        "baseline_limit": BASELINE_LIMIT,
        "baseline_result_rows": row_count,
        "baseline_logical_hash_blake2b": logical_hash,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "hermes_repo_commit": _git_commit(HERMES_REPO_PATH),
        "hermes_source_refs": source_refs,
        "hermes_symbols": [
            "FTS_SQL",
            "messages_fts",
            "messages_fts_trigram",
            "SessionDB.search_messages",
            "session_search",
        ],
        "aegis_symbols": ["HotEvidenceIndex", "CandidateEvidenceRef", "hot_lexical_index_top_k"],
    }
    evidence["evidence_hash_blake3"] = _evidence_hash(root_path, evidence)

    gate_predicates = {
        "criterion_estimate_present": aegis_estimate_ns is not None,
        "speedup_present": speedup is not None,
        "speedup_threshold_met": speedup is not None and speedup >= HERMES_SPEEDUP_THRESHOLD,
        "baseline_digest_deterministic": deterministic_digest_ok,
        "baseline_rows_present": row_count > 0,
        "baseline_db_hash_bound": len(baseline_db_hash) == 64,
        "evidence_hash_bound": len(evidence["evidence_hash_blake3"]) == 64,
        "hermes_repo_commit_bound": evidence["hermes_repo_commit"] is not None,
    }
    evidence["gate_predicates"] = gate_predicates
    ok = all(gate_predicates.values())
    detail = (
        f"{speedup:.2f}x >= {HERMES_SPEEDUP_THRESHOLD:.2f}x "
        f"({baseline_estimate_ns / 1_000:.2f} us Hermes FTS5 baseline vs "
        f"{aegis_estimate_ns / 1_000:.2f} us AEGIS hot evidence)"
        if speedup is not None and aegis_estimate_ns is not None
        else f"missing Criterion estimate for {AEGIS_HOT_EVIDENCE_BENCHMARK_NAME}"
    )
    check = HermesBaselineCheck(
        name="hermes_fts5_session_search_vs_aegis_hot_evidence_index",
        ok=ok,
        aegis_estimate_ns=aegis_estimate_ns,
        baseline_estimate_ns=baseline_estimate_ns,
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


def report_to_dict(report: HermesBaselineReport) -> dict:
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
