from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from aegis_cognition.cli import main
from core.python.operator_api import (
    HEALTH_SCHEMA,
    PRODUCTION_CLOSURE_SCHEMA,
    SNAPSHOT_SCHEMA,
    serve_operator_evidence_api,
)


def _http_get(root: Path, path: str) -> httpx.Response:
    server = serve_operator_evidence_api(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        return httpx.get(f"http://{host}:{port}{path}", timeout=5.0)
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


def test_cli_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "aegis-cognition" in captured.out


def test_cli_init_dry_run_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["init", "--dry-run"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Setup Wizard (dry run)" in captured.out
    assert "Dry run completed successfully" in captured.out


def test_cli_config_list_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    exit_code = main(["config", "list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No configuration found" in captured.out or "[" in captured.out


def test_cli_verify_dry_run_subcommand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    valid_config = tmp_path / ".aegis" / "config.toml"
    valid_config.parent.mkdir(parents=True, exist_ok=True)
    valid_config.write_text("[trust]\nlevel = 'DEV'\n", encoding="utf-8")
    exit_code = main(["verify", "--dry-run"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "dry run" in captured.out.lower()
    assert "configuration valid" in captured.out


def test_cli_verify_dry_run_subcommand_rejects_invalid_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    missing_exit_code = main(["verify", "--dry-run"])
    missing_captured = capsys.readouterr()
    assert missing_exit_code == 1
    assert "configuration file not found" in missing_captured.err.lower()

    bad_config = tmp_path / ".aegis" / "config.toml"
    bad_config.parent.mkdir(parents=True, exist_ok=True)
    bad_config.write_text("corrupted [ toml syntax", encoding="utf-8")
    exit_code = main(["verify", "--dry-run"])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert "configuration error" in captured.err.lower()

    bad_config.write_text("[trust]\nlevel = 'INVALID_TRUST_TIER'\n", encoding="utf-8")
    exit_code = main(["verify", "--dry-run"])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert "invalid trust level" in captured.err.lower()


def test_operator_api_health_endpoint(tmp_path: Path) -> None:
    response = _http_get(tmp_path, "/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert data.get("schema") == HEALTH_SCHEMA
    assert data.get("truth_claim") is False


def test_operator_api_snapshot_endpoint(tmp_path: Path) -> None:
    response = _http_get(tmp_path, "/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert data.get("schema") == SNAPSHOT_SCHEMA
    assert "artifact_summaries" in data
    assert "snapshot_digest" in data


def test_operator_api_artifacts_endpoint(tmp_path: Path) -> None:
    response = _http_get(tmp_path, "/artifacts")
    assert response.status_code == 200
    data = response.json()
    assert data.get("schema") == "aegis-operator-artifact-index-v1"
    assert "artifacts" in data
    assert "snapshot_digest" in data


def test_operator_api_production_closure_endpoint_missing_artifact(tmp_path: Path) -> None:
    response = _http_get(tmp_path, "/production-closure")
    assert response.status_code == 503
    data = response.json()
    assert data.get("schema") == PRODUCTION_CLOSURE_SCHEMA
    assert data.get("overall_ok") is False
    assert "missing production closure workflow artifact" in data.get("error", "")


def test_operator_api_production_closure_endpoint_valid_artifact(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_file = artifacts_dir / "production_closure_workflow_report.json"
    report_payload = {
        "schema": "aegis-production-closure-workflow-v1",
        "workflow_hash": "a" * 64,
        "overall_ok": True,
        "execute": True,
        "production_deployable": True,
        "active_blocker_count": 0,
        "ready_to_execute_count": 1,
        "blocked_preflight_count": 0,
        "closed_blocker_count": 0,
        "steps": [
            {
                "blocker_id": "BLK-001",
                "ready_to_execute": True,
            }
        ],
    }
    report_file.write_text(json.dumps(report_payload), encoding="utf-8")

    response = _http_get(tmp_path, "/production-closure")
    assert response.status_code == 200
    data = response.json()
    assert data.get("schema") == PRODUCTION_CLOSURE_SCHEMA
    assert data.get("overall_ok") is True
    assert data.get("ready_blocker_ids") == ["BLK-001"]
