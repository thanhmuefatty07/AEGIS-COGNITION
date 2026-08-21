"""
Friendly error messages for AEGIS-COGNITION.

Transforms internal cryptographic/architectural errors into
actionable, user-friendly guidance — like Hermes and Cursor do.

Design principles:
    1. What happened (in plain English)
    2. How to fix it (specific, actionable steps)
    3. Where to learn more (docs link)
"""

from __future__ import annotations

from typing import NoReturn


def friendly_error(error: Exception) -> str:
    """Transform an internal error into a friendly, actionable message."""
    msg = str(error)
    error_type = type(error).__name__

    # ── Pattern matching ────────────────────────────────────────

    if "GoalIntakeProof" in msg and "SignedApprovalToken" in msg:
        return _approval_required()
    elif "GoalIntakeProof" in msg:
        return _goal_intake_failed(msg)
    elif "BrowserObservationPacket" in msg:
        return _browser_evidence_missing()
    elif "ReplayLedger" in msg and "hash chain" in msg:
        return _replay_chain_broken()
    elif "API key" in msg.lower() or "api_key" in msg.lower():
        return _api_key_missing()
    elif "AEGIS_TRUST_LEVEL" in msg:
        return _invalid_trust_level(msg)
    elif "Provider" in msg and "429" in msg:
        return _rate_limited()
    elif "Provider" in msg and "throttled" in msg.lower():
        return _all_providers_throttled()
    elif "unsafe" in msg.lower():
        return _rust_unsafe_error(msg)
    elif "panic" in msg.lower():
        return _rust_panic(msg)
    elif "wasmtime" in msg.lower():
        return _wasm_sandbox_error(msg)
    elif "sandbox" in msg.lower():
        return _sandbox_error(msg)
    elif "playwright" in msg.lower() or "browser" in msg.lower():
        return _browser_not_installed()
    elif "mmap" in msg.lower():
        return _mmap_error(msg)
    else:
        return _generic_error(msg, error_type)


def fail_friendly(error: Exception) -> NoReturn:
    """Print friendly error and exit."""
    print(friendly_error(error))
    raise SystemExit(1)


# ── Specific error templates ────────────────────────────────────────


def _approval_required() -> str:
    return """
  This task requires explicit approval.

  What happened:
    Your task involves sensitive operations (file writes, API calls with
    side effects) that require approval in PROD trust level.

  How to fix:
    Option 1 — Switch to DEV trust level (no approval needed):
      agent = Agent(task="...", trust_level="DEV")

    Option 2 — Provide an approval token:
      agent = Agent(task="...", approval_token="your-token")

  Docs: https://docs.aegis-cognition.ai/trust-levels
"""


def _goal_intake_failed(msg: str) -> str:
    return f"""
  Task validation failed.

  What happened:
    The task description couldn't be parsed into a valid goal.

  Details:
    {msg[:200]}

  How to fix:
    1. Make sure your task is a clear, plain-English description
    2. Avoid extremely short tasks (use at least 5 words)
    3. For sensitive tasks, set trust_level="DEV" during development

  Example:
    agent = Agent(task="Find the top 5 AI papers on arXiv this week")

  Docs: https://docs.aegis-cognition.ai/tasks
"""


def _browser_evidence_missing() -> str:
    return """
  Browser evidence collection failed.

  What happened:
    AEGIS tried to capture browser evidence (screenshot, DOM snapshot,
    network log) but the browser automation couldn't complete.

  How to fix:
    Option 1 — Install Playwright browsers:
      playwright install chromium

    Option 2 — Disable browser automation:
      agent = Agent(task="...", browser=False)

    Option 3 — Switch to DEV trust level (no browser evidence required):
      agent = Agent(task="...", trust_level="DEV")

  Docs: https://docs.aegis-cognition.ai/browser
"""


def _replay_chain_broken() -> str:
    return """
  Evidence chain integrity check failed.

  What happened:
    The BLAKE3 hash chain in the replay ledger detected a mismatch.
    This means some evidence was tampered with or corrupted.

  How to fix:
    This is a cryptographic integrity failure — it should never happen
    in normal operation. It may indicate:

    1. A file was manually edited in the artifacts directory
    2. Disk corruption
    3. A bug in AEGIS itself

    If this persists, please file an issue:
    https://github.com/aegis-cognition/aegis-cognition/issues

  Docs: https://docs.aegis-cognition.ai/evidence-chain
"""


def _api_key_missing() -> str:
    return """
  API key not found.

  What happened:
    AEGIS couldn't find your LLM provider's API key.

  How to fix:
    Run the setup wizard:
      aegis init

    Or set it as an environment variable:
      export OPENAI_API_KEY='sk-...'

  Docs: https://docs.aegis-cognition.ai/setup
"""


def _invalid_trust_level(msg: str) -> str:
    return f"""
  Invalid trust level.

  What happened:
    {msg}

  Valid trust levels:
    DEV      — Fast, no cryptographic sealing (for development)
    STAGING  — Balanced, evidence tracked but not fail-closed
    PROD     — Full cryptographic sealing, fail-closed

  Example:
    agent = Agent(task="...", trust_level="DEV")

  Docs: https://docs.aegis-cognition.ai/trust-levels
"""


def _rate_limited() -> str:
    return """
  Provider rate limit hit.

  What happened:
    Your LLM provider returned a 429 Too Many Requests.
    AEGIS automatically fell back to a reserve provider.

  The task may have completed successfully using a fallback provider.
  If it didn't, wait a few seconds and retry.

  To reduce rate limits:
    1. Upgrade your provider tier (e.g. OpenAI Tier 3+)
    2. Add more fallback providers:
       agent = Agent(task="...", fallback_providers=["anthropic", "openrouter"])

  Docs: https://docs.aegis-cognition.ai/providers
"""


def _all_providers_throttled() -> str:
    return """
  All providers are currently rate-limited.

  What happened:
    Every configured LLM provider returned a 429 Too Many Requests.

  How to fix:
    1. Wait 30 seconds and retry
    2. Add more providers to your configuration
    3. Upgrade to a paid provider tier with higher limits

  Docs: https://docs.aegis-cognition.ai/rate-limits
"""


def _rust_unsafe_error(msg: str) -> str:
    return f"""
  Internal engine error (unsafe Rust).

  What happened:
    A low-level unsafe operation in the Rust core encountered an
    unexpected state. This is a bug in AEGIS itself.

  Details:
    {msg[:200]}

  Please file an issue with this error message:
  https://github.com/aegis-cognition/aegis-cognition/issues
"""


def _rust_panic(msg: str) -> str:
    return f"""
  Internal engine panic.

  What happened:
    The Rust core encountered an unrecoverable error and panicked.
    This is always a bug — AEGIS should never panic in production.

  Details:
    {msg[:200]}

  Please file an issue:
  https://github.com/aegis-cognition/aegis-cognition/issues
"""


def _wasm_sandbox_error(msg: str) -> str:
    return f"""
  WebAssembly sandbox error.

  What happened:
    A tool running inside the Wasmtime sandbox encountered an error.

  The sandbox prevented any damage — the host system is safe.

  Details:
    {msg[:200]}

  If this is a tool you wrote, check:
    1. Fuel limits (the tool may have exhausted its computation budget)
    2. Memory limits (the tool may have tried to allocate too much)
    3. The tool's logic for errors

  Docs: https://docs.aegis-cognition.ai/sandbox
"""


def _sandbox_error(msg: str) -> str:
    return f"""
  Sandbox execution error.

  What happened:
    A tool running inside the sandbox failed. The host is safe.

  Details:
    {msg[:200]}

  Docs: https://docs.aegis-cognition.ai/sandbox
"""


def _browser_not_installed() -> str:
    return """
  Browser automation not available.

  What happened:
    AEGIS tried to use browser automation but Playwright browsers
    are not installed.

  How to fix:
    Install Playwright browsers:
      playwright install chromium

    Or install the browser extra:
      pip install aegis-cognition[browser]

    Or disable browser automation:
      agent = Agent(task="...", browser=False)

  Docs: https://docs.aegis-cognition.ai/browser
"""


def _mmap_error(msg: str) -> str:
    return f"""
  Memory-mapped file error.

  What happened:
    AEGIS couldn't read or write a memory-mapped evidence file.

  Details:
    {msg[:200]}

  How to fix:
    1. Check disk space: df -h
    2. Check file permissions in the artifacts directory
    3. Try running with a fresh artifacts directory:
       rm -rf artifacts/
       aegis run "your task"

  Docs: https://docs.aegis-cognition.ai/artifacts
"""


def _generic_error(msg: str, error_type: str) -> str:
    # Truncate very long messages
    if len(msg) > 300:
        msg = msg[:300] + "..."

    return f"""
  Something went wrong.

  Error: {error_type}
  Details: {msg}

  Need help?
    Docs:  https://docs.aegis-cognition.ai
    Issues: https://github.com/aegis-cognition/aegis-cognition/issues

  Quick fix — try the basics:
    1. Check your API key:   aegis config show
    2. Run setup again:      aegis init
    3. Try a simple task:    aegis run "Hello world"
"""
