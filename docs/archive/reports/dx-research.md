# AEGIS DX Research Report

## Executive Summary
This report analyzes Developer Experience (DX) patterns from competitors like Hermes Agent and Browser-Use to define the DX specification for the AEGIS framework. Our focus is on seamless onboarding, secure API key management, cross-platform compatibility, and intelligent provider auto-detection. By implementing a focused setup wizard and robust hidden input mechanics, AEGIS aims to offer an enterprise-grade but developer-friendly CLI experience out of the box, reducing time-to-first-inference to under 60 seconds.

## Dimension Specifications

### 1. API Key Input UX (Hidden Input, Cross-Platform)
To ensure keys are never echoed to the terminal or saved in shell history, AEGIS must implement a custom masked input function. 

- **Windows Implementation**: Utilize the `msvcrt` module. Read single characters via `msvcrt.getwch()`. Handle special function keys (which return `\x00` or `\xe0` first) by swallowing the next character. Echo a mask character (`*`) to `sys.stdout` and manually handle backspace (`\b \b`).
- **Unix/POSIX Implementation**: Utilize `termios` and `tty.setraw()`. Read characters via `sys.stdin.read(1)`. Restore the original terminal settings in a `finally` block to prevent terminal corruption.
- **Fallback**: If the terminal is not interactive (`sys.stdin.isatty() == False`), silently fall back to `getpass.getpass()`.

### 2. Provider Auto-Detection
Instead of forcing users to manually select their LLM provider, AEGIS will auto-detect the provider based on the format of the provided API key or endpoint url.

*Regex Patterns for Auto-Detection:*
- **OpenAI**: `^sk-(?:proj-)?[A-Za-z0-9\-_]{40,}$`
- **Anthropic**: `^sk-ant-[a-zA-Z0-9\-_]{40,}$`
- **Google/Gemini**: `^AIza[0-9A-Za-z_\-]{35}$`
- **Groq**: `^gsk_[A-Za-z0-9]{36}$`
- **Local LLM (Ollama/LMStudio)**: Detect URL inputs like `^http://(?:localhost|127\.0\.0\.1):\d{4,5}(?:/v1)?$` or dummy keys like `^ollama$`.

*Validation Endpoints:*
- OpenAI: `GET https://api.openai.com/v1/models`
- Anthropic: `GET https://api.anthropic.com/v1/models` (Requires `x-api-key`)

### 3. Setup Wizard Flow
A streamlined 3-step to 5-step maximum flow triggered via `aegis setup`.

1. **Provider Selection & Key Input**: 
   - Prompt: "Paste your LLM API Key (OpenAI, Anthropic, Gemini, Groq) or Local URL:"
   - *Action*: Auto-detect provider from regex.
2. **Model Selection**:
   - Fetch available models via the detected provider's API.
   - Prompt: "Select your default model: [List of top 3 + Other]"
3. **Execution Environment**:
   - Prompt: "Require physical witness (PROD) or allow degraded hot evidence (DEV)?"
4. **Tool Configuration (Optional)**:
   - "Enable Browser Automation (requires agent-browser)? (Y/n)"
5. **Confirmation**:
   - "Setup complete. Configuration saved to ~/.aegis/config.yaml."

### 4. Error Handling & Validation
- **Rate Limit Handling**: Utilize the `aegis_adapter.py` token bucket and `ProviderRateLimitError` to automatically trigger the fallback chain.
- **Pre-flight Checks**: Validate the API key against the provider's `/models` endpoint during the setup wizard before saving.
- **Friendly Errors**: Mask raw stack traces unless `--debug` is passed. Output actionable advice (e.g., "Invalid Anthropic key. Keys should start with `sk-ant-`").

### 5. Config Management
- Use a central directory: `~/.aegis/` (matching Hermes and Browser-Use patterns).
- Store API keys in `~/.aegis/.env` (mode 600 permissions).
- Store environment and agent configuration in `~/.aegis/config.yaml`.
- Support environment variable overrides (e.g., `AEGIS_TRUST_LEVEL=DEV aegis run`).

### 6. CLI Command Design
- `aegis setup`: Interactive onboarding wizard.
- `aegis run "<task>"`: Execute a one-shot task.
- `aegis config set <key> <value>`: Non-interactive configuration modifier.
- `aegis status`: Show loaded provider, current trust level, and available tools.

## Comparison Matrix: AEGIS vs. Hermes Agent

| Feature | Hermes Agent | AEGIS (Proposed) |
|---------|--------------|------------------|
| **Setup Wizard** | Extensive (~10 steps: Provider, Terminal, Tools, Messaging) | Streamlined (3-5 steps), focused on auto-detection |
| **API Key Input** | Masked input (`secret_prompt.py`) cross-platform | Masked input cross-platform (identical robust approach) |
| **Provider Selection** | Manual selection from a curses-based list | Auto-detected from API key regex prefix |
| **Trust/Validation** | Implicit via plugin ecosystem | Explicit `AEGIS_TRUST_LEVEL` (DEV/STAGING/PROD) |
| **Storage Pattern** | `~/.hermes/config.yaml` and `.env` | `~/.aegis/config.yaml` and `.env` |
| **Execution** | Background Service (`gateway.cmd` via `schtasks`) | Foreground CLI or Python SDK |
| **Failover** | Basic round-robin | Advanced Budget Ledger & Hot Engine Commit records |
