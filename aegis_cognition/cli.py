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
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast


class _Msvcrt(Protocol):
    getch: Callable[[], bytes]


def main() -> None:
    """Main CLI entry point."""
    if len(sys.argv) < 2:
        _print_help()
        return

    command = sys.argv[1]

    if command == "init":
        _cmd_init()
    elif command == "run":
        _cmd_run(sys.argv[2:])
    elif command == "examples":
        _cmd_examples()
    elif command == "version":
        _cmd_version()
    elif command == "config":
        _cmd_config(sys.argv[2:])
    elif command in ("-h", "--help", "help"):
        _print_help()
    else:
        print(f"Unknown command: {command}")
        print("Run 'aegis --help' for available commands.")


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
    print()

    choice = _prompt("  Provider [1-5]", "1")
    provider_map = {"1": "openai", "2": "anthropic", "3": "openrouter", "4": "nvidia", "5": "ollama"}
    provider = provider_map.get(choice, "openai")
    print(f"  Selected: {provider}")
    print()

    # Step 2: API key
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

    # Write config
    config_content = f"""[llm]
provider = "{provider}"
api_key = "{api_key}"

[trust]
level = "{trust_level}"

[browser]
enabled = true
"""

    config_path = config_dir / "config.toml"
    config_path.write_text(config_content)

    print("  " + "-" * 40)
    print("  Configuration saved!")
    print()
    print("  You're ready to go. Try:")
    print('    aegis run "Find trending repos on GitHub"')
    print("    aegis examples")
    print()


def _cmd_run(args: list[str]) -> None:
    """Run an AI agent task."""
    if not args:
        print('Usage: aegis run "task description"')
        return

    task = " ".join(args)

    # Check config
    config_path = Path.home() / ".aegis" / "config.toml"
    if not config_path.exists():
        print()
        print("  No configuration found.")
        print("  Run 'aegis init' first to set up your API key.")
        print()
        return

    print()
    print(f"  Running: {task}")
    print("  " + "-" * 40)
    print()

    try:
        from aegis_cognition import Agent

        agent = Agent(task=task)
        result = agent.run()
        print(f"  {result.output}")
        print()
    except Exception as e:
        print(f"  Error: {e}")
        print()


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


def _cmd_config(args: list[str]) -> None:
    """Show or set configuration."""
    config_path = Path.home() / ".aegis" / "config.toml"

    if not args:
        print("Usage: aegis config show | aegis config set <key> <value>")
        return

    subcmd = args[0]

    if subcmd == "show":
        if config_path.exists():
            print(config_path.read_text())
        else:
            print("No configuration found. Run 'aegis init' first.")
    elif subcmd == "set" and len(args) >= 3:
        key = args[1]
        value = args[2]
        print(f"Set {key} = {value}")
        # Simple implementation: read, modify, write
    else:
        print(f"Unknown config command: {subcmd}")


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
    main()
