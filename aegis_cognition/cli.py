"""
AEGIS-COGNITION CLI — Beautiful terminal interface.

Usage:
    aegis init                        Interactive setup wizard
    aegis run "task description"      Run an AI agent task
    aegis examples                    Show example tasks
    aegis version                     Show version
    aegis config show                 Show current configuration
    aegis config set KEY VALUE        Set a configuration value

Setup flow (< 2 minutes):
    pip install aegis-cognition
    aegis init
    aegis run "Hello world"
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast

from .config import VALID_TRUST_LEVELS, load_config, redact_config, save_config


class _Msvcrt(Protocol):
    getch: Callable[[], bytes]


def main() -> int:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        _print_help()
        return 0

    command = sys.argv[1]

    if command == "init":
        _cmd_init()
    elif command == "run":
        return _cmd_run(sys.argv[2:])
    elif command == "examples":
        _cmd_examples()
    elif command == "version":
        _cmd_version()
    elif command == "config":
        return _cmd_config(sys.argv[2:])
    elif command in ("-h", "--help", "help"):
        _print_help()
    else:
        print(f"Unknown command: {command}")
        print("Run 'aegis --help' for available commands.")
        return 2
    return 0


def _print_help() -> None:
    print(
        """
  AEGIS-COGNITION v0.1.0 — Cryptographically-verified AI agent harness

  Commands:
    aegis init                        Interactive setup wizard
    aegis run <task>                  Run an AI agent task
    aegis examples                    Show example tasks
    aegis version                     Show version
    aegis config show                 Show current configuration
    aegis config set <key> <value>    Set a configuration value

  Quick Start:
    pip install aegis-cognition
    aegis init
    aegis run "Find trending repos on GitHub"

  Docs: https://docs.aegis-cognition.ai
""".strip()
        + "\n"
    )


# ── Commands ────────────────────────────────────────────────────────


def _cmd_init() -> None:
    """Interactive setup wizard."""
    print()
    print("  AEGIS-COGNITION Setup Wizard")
    print("  " + "-" * 40)
    print()
    print("  Let's get you set up in under 2 minutes.")
    print()

    config_dir = Path.home() / ".aegis"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Provider
    print("  [1/3] Choose your LLM provider:")
    print("    1. OpenAI (default)")
    print("    2. Anthropic")
    print("    3. OpenRouter")
    print("    4. Nvidia NIM")
    print("    5. Ollama (local)")
    print("    6. ChatGPT Web (local browser bridge)")
    print()

    choice = _prompt("  Provider [1-6]", "1")
    provider_map = {
        "1": "openai",
        "2": "anthropic",
        "3": "openrouter",
        "4": "nvidia",
        "5": "ollama",
        "6": "chatgpt-web",
    }
    provider = provider_map.get(choice, "openai")
    print(f"  Selected: {provider}")
    print()

    # Step 2: API key or local bridge settings
    api_key = ""
    base_url = ""
    model = ""
    if provider == "chatgpt-web":
        base_url = _prompt("  [2/3] Local bridge URL", "http://127.0.0.1:17841/v1")
        model = _prompt("  ChatGPT Web model", "chatgpt-web/high")
        print("  No API key is stored; sign in through the bridge's own browser window.")
    else:
        api_key = _prompt_secret(f"  [2/3] Enter your {provider} API key")
        if not api_key:
            print()
            print("  Skipping API key — you can set it later with:")
            print("    export OPENAI_API_KEY='sk-...'")
            print()

    # Step 3: Trust level
    print("  [3/3] Choose trust level:")
    print("    1. DEV      — Fast, no crypto sealing (recommended for development)")
    print("    2. STAGING  — Evidence tracked, not fail-closed")
    print("    3. PROD     — Full cryptographic sealing (slower)")
    print()

    level_choice = _prompt("  Trust level [1-3]", "1")
    level_map = {"1": "DEV", "2": "STAGING", "3": "PROD"}
    trust_level = level_map.get(level_choice, "DEV")
    print(f"  Selected: {trust_level}")
    print()

    # Write config atomically through the canonical configuration boundary.
    config_path = config_dir / "config.toml"
    save_config(
        {
            "llm": {
                "provider": provider,
                **({"base_url": base_url, "model": model} if provider == "chatgpt-web" else {"api_key": api_key}),
            },
            "trust": {"level": trust_level},
            "browser": {"enabled": True},
        },
        config_path,
    )

    print("  " + "-" * 40)
    print("  Configuration saved!")
    print()
    print("  You're ready to go. Try:")
    print('    aegis run "Find trending repos on GitHub"')
    print("    aegis examples")
    print()


def _cmd_run(args: list[str]) -> int:
    """Run an AI agent task."""
    if not args:
        print('Usage: aegis run "task description"')
        return 2

    task = " ".join(args)

    # Check config
    config_path = Path.home() / ".aegis" / "config.toml"
    if not config_path.exists():
        print()
        print("  No configuration found.")
        print("  Run 'aegis init' first to set up your API key.")
        print()
        return 1

    print()
    print(f"  Running: {task}")
    print("  " + "-" * 40)
    print()

    try:
        from .agent import Agent

        config = load_config(config_path)
        llm = _build_llm_from_config(config)
        agent = Agent(task=task, llm=llm)
        result = agent.run()
        print(f"  {result.output}")
        print()
    except Exception as e:
        print(f"  Error: {e}")
        print()
        return 1
    return 0


def _build_llm_from_config(config: dict[str, object]) -> object | None:
    """Build only the explicitly selected local provider; leave other providers unchanged."""

    raw_llm = config.get("llm")
    if not isinstance(raw_llm, Mapping):
        return None
    typed_llm = cast(Mapping[str, object], raw_llm)
    provider = typed_llm.get("provider")
    if not isinstance(provider, str) or provider.strip().lower() != "chatgpt-web":
        return None
    from core.python.chatgpt_web_client import ChatGPTWebClient

    return ChatGPTWebClient.from_mapping(typed_llm)


def _cmd_examples() -> None:
    """Show example tasks."""
    print(
        """
  Example Tasks:

  Web Research:
    aegis run "Find the top 5 AI agent frameworks on GitHub and compare their stars"

  Code Analysis:
    aegis run "Explain the security implications of unsafe Rust in this file"

  Browser Automation (requires playwright):
    aegis run "Go to amazon.com and find the best-selling laptop under $1000"

  Data Extraction:
    aegis run "Extract all email addresses from this text: ..."

  Translation:
    aegis run "Translate this document from English to Vietnamese"
""".strip()
        + "\n"
    )


def _cmd_version() -> None:
    """Show version."""
    from aegis_cognition import __version__

    print(f"aegis-cognition v{__version__}")


def _cmd_config(args: list[str]) -> int:
    """Show or set configuration."""
    config_path = Path.home() / ".aegis" / "config.toml"

    if not args:
        print("Usage: aegis config show | aegis config set <key> <value>")
        return 2

    subcmd = args[0]

    if subcmd == "show":
        try:
            config = load_config(config_path)
        except Exception as error:
            print(f"Configuration error: {error}")
            return 1
        if config:
            from .config import render_toml

            print(render_toml(redact_config(config)), end="")
        else:
            print("No configuration found. Run 'aegis init' first.")
        return 0
    elif subcmd == "set" and len(args) == 3:
        key, raw_value = args[1], args[2]
        try:
            config = load_config(config_path)
            _set_config_value(config, key, raw_value)
            save_config(config, config_path)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            print(f"Configuration error: {error}")
            return 1
        print(f"Set {key}.")
        return 0
    else:
        print("Usage: aegis config show | aegis config set <key> <value>")
        return 2


def _set_config_value(config: dict[str, object], key: str, raw_value: str) -> None:
    """Validate and set the small, documented CLI configuration surface."""

    section_name, separator, field_name = key.partition(".")
    allowed: dict[str, tuple[str, ...]] = {
        "llm": ("provider", "api_key", "base_url", "model"),
        "trust": ("level",),
        "browser": ("enabled",),
    }
    if not separator or section_name not in allowed or field_name not in allowed[section_name]:
        raise ValueError(f"unsupported configuration key: {key}")
    value = raw_value.strip()
    if not value:
        raise ValueError(f"configuration value for {key} cannot be empty")
    if section_name == "trust" and field_name == "level":
        value = value.upper()
        if value not in VALID_TRUST_LEVELS:
            raise ValueError(f"trust level must be one of {', '.join(sorted(VALID_TRUST_LEVELS))}")
    elif section_name == "browser" and field_name == "enabled":
        lowered = value.lower()
        if lowered not in {"true", "false"}:
            raise ValueError("browser.enabled must be true or false")
        value = lowered == "true"
    section = config.setdefault(section_name, {})
    if not isinstance(section, dict):
        raise ValueError(f"configuration section {section_name} is not a table")
    section[field_name] = value


# ── Helpers ─────────────────────────────────────────────────────────


def _prompt(text: str, default: str = "") -> str:
    """Prompt for input with default."""
    try:
        result = input(f"{text} [{default}]: ").strip()
        return result or default
    except EOFError, KeyboardInterrupt:
        print()
        sys.exit(0)


def _prompt_secret(text: str) -> str:
    """Prompt for secret (doesn't echo)."""
    try:
        import msvcrt

        print(f"{text}: ", end="", flush=True)
        chars: list[str] = []
        getch = cast(_Msvcrt, msvcrt).getch
        while True:
            ch: bytes = getch()
            if ch in (b"\r", b"\n"):
                print()
                break
            elif ch == b"\x08":  # Backspace
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
            elif ch == b"\x03":  # Ctrl+C
                print()
                sys.exit(0)
            else:
                chars.append(ch.decode("utf-8", errors="replace"))
                print("*", end="", flush=True)
        return "".join(chars)
    except ImportError:
        import getpass

        return getpass.getpass(f"{text}: ")


if __name__ == "__main__":
    raise SystemExit(main())
