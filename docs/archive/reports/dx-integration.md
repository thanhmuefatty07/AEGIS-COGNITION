# AEGIS-COGNITION DX Integration Guide

Welcome to the new Developer Experience (DX) for AEGIS-COGNITION. This guide explains how to use the CLI and how it bridges with the Rust Core securely.

## 1. Installation

Install the Python bridge with the new CLI entrypoints. Ensure you are in the project root or the `core/python` directory.

```bash
cd core/python
pip install -e .
```

This installs the `aegis` command globally in your Python environment.

## 2. Initialization & Setup Wizard

Initialize your environment with the interactive setup wizard. The wizard supports cross-platform hidden input (so your API keys are never leaked to the terminal history) and auto-detects your LLM Provider.

```bash
aegis init
```

**What it does:**
- Prompts for your API key securely.
- Automatically detects if it's OpenAI, Anthropic, Gemini, Groq, or Local (like Ollama).
- Asks for your preferred `AEGIS_TRUST_LEVEL` (PROD, DEV, STAGING).
- Saves the structural configuration to `~/.aegis/config.yaml` and the sensitive keys to `~/.aegis/.env` (with restricted 600 permissions).

## 3. Running an Agent

Run tasks seamlessly via the CLI:

```bash
aegis run "Phân tích mã nguồn và tìm lỗi bảo mật"
```

### How the Bridging Works
1. **Environment Injection**: The CLI calls `apply_config_to_runtime()`, which loads `~/.aegis/.env` and `config.yaml` directly into Python's `os.environ`.
2. **Rust Core Awareness**: Because Python and Rust share the same process environment in PyO3/Mmap integrations, the Rust Core (`llm.rs`, `hot_engine.rs`) reads `AEGIS_TRUST_LEVEL` natively. No configuration parsing logic was duplicated in Rust, maintaining the strict boundary.
3. **Graceful Degradation**: The new `AegisAgent` wrapper in `aegis_adapter.py` catches `ProviderRateLimitError` (HTTP 429). It logs a friendly message to the user (`⚠️ Provider bị giới hạn...`) and intelligently applies exponential backoff, allowing the underlying Rust engine to naturally record a `DynamicProviderFallbackProof` to the verifiable ledger if fallback routes are engaged.
