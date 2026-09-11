import sys
from pathlib import Path

from aegis_cognition import cli
from aegis_cognition.config import load_config


def _patch_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))


def test_main_returns_nonzero_for_unknown_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["aegis", "unknown"])

    assert cli.main() == 2
    assert "Unknown command: unknown" in capsys.readouterr().out


def test_run_returns_nonzero_when_configuration_is_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_home(monkeypatch, tmp_path)

    assert cli._cmd_run(["task"]) == 1
    assert "No configuration found" in capsys.readouterr().out


def test_config_set_persists_typed_values_without_echoing_secret(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_home(monkeypatch, tmp_path)

    cli._cmd_config(["set", "llm.api_key", "secret-value"])
    cli._cmd_config(["set", "trust.level", "staging"])
    cli._cmd_config(["set", "browser.enabled", "false"])

    output = capsys.readouterr().out
    assert "secret-value" not in output
    assert "Set llm.api_key." in output
    assert load_config(tmp_path / ".aegis" / "config.toml") == {
        "llm": {"api_key": "secret-value"},
        "trust": {"level": "STAGING"},
        "browser": {"enabled": False},
    }


def test_config_show_redacts_secrets_and_preserves_valid_toml(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_home(monkeypatch, tmp_path)
    cli._cmd_config(["set", "llm.api_key", "secret-value"])
    capsys.readouterr()

    cli._cmd_config(["show"])
    output = capsys.readouterr().out

    assert "secret-value" not in output
    assert 'api_key = "<redacted>"' in output
    assert load_config(tmp_path / ".aegis" / "config.toml")["llm"]["api_key"] == "secret-value"


def test_config_set_rejects_unknown_key_without_mutating_file(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_home(monkeypatch, tmp_path)
    cli._cmd_config(["set", "trust.level", "DEV"])
    path = tmp_path / ".aegis" / "config.toml"
    before = path.read_text(encoding="utf-8")
    capsys.readouterr()

    cli._cmd_config(["set", "llm.unknown", "value"])
    output = capsys.readouterr().out

    assert "unsupported configuration key" in output
    assert path.read_text(encoding="utf-8") == before
