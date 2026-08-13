"""
AEGIS Agent — Simple, drop-in AI agent API.

Usage:
    from aegis_cognition import Agent
    
    agent = Agent(task="Find trending repos on GitHub")
    result = agent.run()
    print(result.output)

    # Or the convenience shortcut:
    from aegis_cognition import run
    result = run("Hello world")
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# ── Configuration ──────────────────────────────────────────────────

_CONFIG_DIR = Path.home() / ".aegis"
_CONFIG_FILE = _CONFIG_DIR / "config.toml"


def _load_config() -> dict[str, Any]:
    """Load configuration from ~/.aegis/config.toml, or return defaults."""
    if not _CONFIG_FILE.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]
    with open(_CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def _get_api_key() -> str | None:
    """Get API key from config or environment."""
    config = _load_config()
    llm = config.get("llm", {})
    return llm.get("api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("AEGIS_API_KEY")


def _get_trust_level() -> str:
    """Get trust level from config or environment."""
    config = _load_config()
    trust = config.get("trust", {})
    return trust.get("level") or os.environ.get("AEGIS_TRUST_LEVEL") or "DEV"


# ── Agent ──────────────────────────────────────────────────────────

class Agent:
    """
    Simple AI Agent that runs tasks with cryptographic evidence tracking.

    Parameters:
        task: The task description (plain English).
        llm: LLM model string (e.g. "gpt-4", "claude-sonnet-4").
        trust_level: Evidence trust level: "DEV" (fast), "STAGING", or "PROD" (full crypto).
        browser: Enable browser automation (requires `pip install aegis-cognition[browser]`).
        max_steps: Maximum agent steps before timeout.
        **kwargs: Additional provider-specific options.

    Examples:
        >>> agent = Agent(task="Find trending repos on GitHub")
        >>> result = agent.run()
        >>> print(result.output)
    """

    def __init__(
        self,
        task: str,
        *,
        llm: str | None = None,
        trust_level: str | None = None,
        browser: bool = False,
        max_steps: int = 100,
        **kwargs: Any,
    ) -> None:
        self.task = task
        self.llm = llm
        self.trust_level = trust_level or _get_trust_level()
        self.browser = browser
        self.max_steps = max_steps
        self._kwargs = kwargs
        self._config = _load_config()
        self._api_key = _get_api_key()

        self._validate()

    def _validate(self) -> None:
        """Validate configuration and provide friendly errors."""
        if not self._api_key:
            raise ConfigError.api_key_missing()

        valid_levels = {"DEV", "STAGING", "PROD"}
        if self.trust_level.upper() not in valid_levels:
            raise ConfigError.invalid_trust_level(self.trust_level)

    def _prepare_rag_and_prompt(self, task: str) -> tuple[str, str]:
        """
        Runs RAG retrieval and compiles the v3.1 system prompt.
        Returns (formatted_task, system_context).
        """
        task_type = self._kwargs.get("task_type", "R1")
        
        # 1. Run RAG retrieval
        from aegis_cognition.rag import RAGManager
        from aegis_cognition.prompt import PromptBuilder
        
        rag_mgr = RAGManager(top_k=self._kwargs.get("top_k", 3))
        rag_context = rag_mgr.retrieve_and_format(task)
        
        # 2. Build system instructions
        builder = PromptBuilder(
            trust_level=self.trust_level,
            task_type=task_type,
            security_level=self._kwargs.get("security_level"),
            constraints=self._kwargs.get("constraints"),
        )
        
        system_context = builder.build(
            task,
            primary_objective=self._kwargs.get("primary_objective"),
            secondary_objectives=self._kwargs.get("secondary_objectives"),
            output_format=self._kwargs.get("output_format", "JSON"),
            output_requirements=self._kwargs.get("output_requirements"),
            output_limitations=self._kwargs.get("output_limitations"),
            steps=self._kwargs.get("steps"),
            risks=self._kwargs.get("risks"),
            error_handlers=self._kwargs.get("error_handlers"),
            completion_conditions=self._kwargs.get("completion_conditions"),
        )
        
        # 3. If RAG context is retrieved, append it
        formatted_task = task
        if rag_context:
            formatted_task = f"{task}\n\n{rag_context}"
            
        return formatted_task, system_context

    def _index_completed_run(self, task: str, output: Any, aegis_result: Any) -> None:
        """Indexes the successful session transcript for future RAG recall."""
        try:
            from aegis_adapter import LearningManager
            mgr = LearningManager()
            
            # Use hot_commit artifact hash as the session_id
            session_id = aegis_result.hot_commit.artifact_hash
            content = f"Task: {task}\nOutput: {output}"
            
            mgr.index_session(session_id=session_id, content=content)
        except Exception as e:
            print(f"\u26a0\ufe0f Auto-indexing session failed: {e}", file=sys.stderr)

    def run(self) -> RunResult:
        """
        Execute the task and return results.

        Returns:
            RunResult with .output, .provider, .trust_level, .hot_commit
        """
        # Load the existing Friendly Gateway adapter
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "python"))
        from aegis_adapter import AegisAdapter

        formatted_task, system_context = self._prepare_rag_and_prompt(self.task)

        adapter = AegisAdapter(
            task=formatted_task,
            llm=self.llm,
            trust_level=self.trust_level,
        )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            aegis_result = asyncio.run(adapter.run(formatted_task, system_context=system_context))
        else:
            raise RuntimeError(
                "Agent.run() cannot be called inside an async event loop. "
                "Use `await agent.arun()` or call from a sync context."
            )

        self._index_completed_run(self.task, aegis_result.output, aegis_result)

        return RunResult(
            task=aegis_result.task,
            output=aegis_result.output,
            trust_level=aegis_result.trust_level,
            provider=aegis_result.provider,
            hot_commit=aegis_result.hot_commit,
        )

    async def arun(self) -> RunResult:
        """Async version of run()."""
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "python"))
        from aegis_adapter import AegisAdapter

        formatted_task, system_context = self._prepare_rag_and_prompt(self.task)

        adapter = AegisAdapter(
            task=formatted_task,
            llm=self.llm,
            trust_level=self.trust_level,
        )
        aegis_result = await adapter.run(formatted_task, system_context=system_context)
        
        self._index_completed_run(self.task, aegis_result.output, aegis_result)
        
        return RunResult(
            task=aegis_result.task,
            output=aegis_result.output,
            trust_level=aegis_result.trust_level,
            provider=aegis_result.provider,
            hot_commit=aegis_result.hot_commit,
        )

    def __repr__(self) -> str:
        return f"Agent(task={self.task!r}, trust_level={self.trust_level!r})"


# ── RunResult ──────────────────────────────────────────────────────

class RunResult:
    """Result returned by Agent.run()."""

    def __init__(
        self,
        task: str,
        output: Any,
        trust_level: str,
        provider: str | None,
        hot_commit: Any,
    ) -> None:
        self.task = task
        self.output = output
        self.trust_level = trust_level
        self.provider = provider
        self.hot_commit = hot_commit

    def __repr__(self) -> str:
        return f"RunResult(output={str(self.output)[:80]!r}, provider={self.provider!r})"


# ── Convenience function ───────────────────────────────────────────

def run(task: str, **kwargs: Any) -> RunResult:
    """Quick one-liner to run a task."""
    return Agent(task=task, **kwargs).run()


# ── Friendly Errors ────────────────────────────────────────────────

class ConfigError(RuntimeError):
    """Friendly configuration error with actionable fix suggestions."""

    @classmethod
    def api_key_missing(cls) -> "ConfigError":
        return cls(
            "\n"
            "  No API key found.\n"
            "\n"
            "  How to fix:\n"
            "    Run the setup wizard:\n"
            "      aegis init\n"
            "\n"
            "    Or set the environment variable:\n"
            "      export OPENAI_API_KEY='sk-...'\n"
            "\n"
            "  Docs: https://docs.aegis-cognition.ai/setup\n"
        )

    @classmethod
    def invalid_trust_level(cls, level: str) -> "ConfigError":
        return cls(
            f"\n"
            f"  Invalid trust level: '{level}'\n"
            f"\n"
            f"  Valid trust levels:\n"
            f"    DEV      — Fast, no cryptographic sealing (for development)\n"
            f"    STAGING  — Balanced, evidence tracked but not fail-closed\n"
            f"    PROD     — Full cryptographic sealing, fail-closed\n"
            f"\n"
            f"  Example:\n"
            f"    agent = Agent(task='...', trust_level='DEV')\n"
            f"\n"
            f"  Docs: https://docs.aegis-cognition.ai/trust-levels\n"
        )


class ProviderError(RuntimeError):
    """Friendly provider error with retry guidance."""

    @classmethod
    def rate_limited(cls, provider: str) -> "ProviderError":
        return cls(
            f"\n"
            f"  Rate limit hit on provider: {provider}\n"
            f"\n"
            f"  AEGIS automatically fell back to a reserve provider.\n"
            f"  The task completed successfully — no action needed.\n"
            f"\n"
            f"  If this happens frequently, consider:\n"
            f"    1. Upgrading your {provider} tier\n"
            f"    2. Adding more fallback providers (see docs)\n"
            f"\n"
            f"  Docs: https://docs.aegis-cognition.ai/providers\n"
        )

    @classmethod
    def all_throttled(cls) -> "ProviderError":
        return cls(
            "\n"
            "  All providers are currently rate-limited.\n"
            "\n"
            "  How to fix:\n"
            "    1. Wait a few seconds and retry\n"
            "    2. Add more providers: aegis config add-provider\n"
            "    3. Upgrade to PRO tier for higher rate limits\n"
            "\n"
            "  Docs: https://docs.aegis-cognition.ai/rate-limits\n"
        )