# AEGIS DX Implementation Specification

## Overview
This specification details the implementation of the AEGIS Developer Experience (DX) improvements as outlined in the DX Research Report. The focus is on the CLI onboarding (`aegis setup`), provider auto-detection, and integration with the existing `aegis_adapter.py` and `llm.rs` structures without altering the core architecture.

## 1. CLI Commands & Setup Wizard (`aegis_cli.py`)
A new CLI entrypoint `aegis_cli.py` (or the `aegis` script) will be created.

### 1.1 Hidden API Key Input
Cross-platform hidden input ensures keys are never echoed to the terminal or saved in shell history.

```python
import sys, os

def get_masked_input(prompt: str) -> str:
    print(prompt, end='', flush=True)
    password = []
    if os.name == 'nt':
        import msvcrt
        while True:
            char = msvcrt.getwch()
            if char in ('\r', '\n'):
                print('')
                break
            elif char == '\b':
                if password:
                    password.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif char in ('\x00', '\xe0'):
                msvcrt.getwch() # Swallow special keys
            else:
                password.append(char)
                sys.stdout.write('*')
                sys.stdout.flush()
    else:
        if not sys.stdin.isatty():
            import getpass
            return getpass.getpass("")
        import termios, tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            while True:
                char = sys.stdin.read(1)
                if char in ('\r', '\n'):
                    sys.stdout.write('\r\n')
                    break
                elif char in ('\b', '\x7f'):
                    if password:
                        password.pop()
                        sys.stdout.write('\b \b')
                        sys.stdout.flush()
                elif char == '\x03': # Ctrl+C
                    raise KeyboardInterrupt
                else:
                    password.append(char)
                    sys.stdout.write('*')
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ''.join(password)
```

### 1.2 Provider Auto-Detection Regex
The wizard detects the provider seamlessly based on key formats.

```python
import re

PROVIDER_REGEXES = {
    "OpenAI": re.compile(r"^sk-(?:proj-)?[A-Za-z0-9\-_]{40,}$"),
    "Anthropic": re.compile(r"^sk-ant-[a-zA-Z0-9\-_]{40,}$"),
    "Gemini": re.compile(r"^AIza[0-9A-Za-z_\-]{35}$"),
    "Groq": re.compile(r"^gsk_[A-Za-z0-9]{36}$"),
    "Local": re.compile(r"^(?:http://(?:localhost|127\.0\.0\.1):\d{4,5}(?:/v1)?|ollama)$")
}

def detect_provider(api_key: str) -> str | None:
    for provider, pattern in PROVIDER_REGEXES.items():
        if pattern.match(api_key):
            return provider
    return None
```

### 1.3 Setup Flow Pseudocode
```python
def setup_wizard():
    api_key = get_masked_input("Paste your LLM API Key or Local URL: ")
    provider = detect_provider(api_key)
    if not provider:
        print("Could not auto-detect provider. Please check your key.")
        return
    
    # Pre-flight check against provider API could be inserted here
    print(f"Detected Provider: {provider}")
    
    trust_level = input("Require physical witness (PROD) or allow degraded hot evidence (DEV)? [PROD/DEV]: ").upper()
    if trust_level not in ("PROD", "DEV", "STAGING"):
        trust_level = "PROD"
        
    enable_browser = input("Enable Browser Automation? (Y/n): ").lower() == 'y'
    
    # Save to ~/.aegis/config.yaml and ~/.aegis/.env
    save_config(provider, api_key, trust_level, enable_browser)
    print("Setup complete. Configuration saved to ~/.aegis/config.yaml.")
```

## 2. Integration Hooks (Non-Intrusive)

### 2.1 Hooking into Python `aegis_adapter.py`
The CLI runner acts as an orchestrator that bridges `~/.aegis/config.yaml` to `AegisAdapter`. No modifications to `aegis_adapter.py` are needed.

- **Trust Level Injection**: The `config.yaml` provides the trust level, which the CLI passes directly via `AegisAdapter(trust_level=config.trust_level)` or by setting `os.environ["AEGIS_TRUST_LEVEL"]`. `aegis_adapter.py` natively respects this via `_normalize_trust_level()`.
- **Provider Instantiation**: The loaded `provider` maps to a specific LLM instantiation (e.g., `ChatOpenAI`), which is passed to `AegisAdapter(llm=client)`.
- **Rate Limit & Budget Catching**: The CLI configures budgets and passes them as a mapping to `AegisAdapter(provider_budgets=...)`. If budgets expire or rate limits are hit, `aegis_adapter.py` raises `ProviderRateLimitError`. The CLI catches this, gracefully degrading the experience (e.g., prompting the user or invoking secondary fallback providers without dumping a raw stack trace).

### 2.2 Hooking into Rust `llm.rs`
The Rust engine relies on configuration structures like `ProviderConfig` and `ProviderRuntimeBudget`.
The DX CLI layer exports the configuration for the Rust backend to consume (via JSON, TOML, or environment injection).

- **`ProviderConfig` Mapping**:
  - `provider_id`: Mapped exactly to the auto-detected provider (e.g., `"openai"`, `"anthropic"`).
  - `endpoint`: Uses default API URLs unless overwritten by the "Local" URL pattern detection.
- **`ProviderBudgetLedger` Integration**:
  - When the CLI passes budget limits, they construct a `ProviderRuntimeBudget` in Rust.
  - This ensures `llm.rs::ProviderRuntimeBudget::can_serve()` correctly enforces limits matching the user-defined DX configuration.
- **Dynamic Fallbacks (`DynamicProviderFallbackProof`)**:
  - Rust natively handles fallback proofs. The CLI merely provisions the `fallback_providers` list in `config.yaml`, which informs Rust which `ProviderConfig` items are valid alternates during `ProviderRuntimeFeedbackKind::Http429` events.

## 3. Configuration Management

The file structure established by the wizard cleanly partitions sensitive secrets from structural configuration.

**`~/.aegis/config.yaml`**
```yaml
provider: "anthropic"
trust_level: "DEV"
tools:
  browser_automation: true
```

**`~/.aegis/.env` (Permissions: Mode 600)**
```env
ANTHROPIC_API_KEY="sk-ant-..."
```
