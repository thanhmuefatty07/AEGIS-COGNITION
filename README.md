# AEGIS-COGNITION

> Cryptographically-verified AI agent harness — drop-in developer experience.

[![Python 3.14–3.15](https://img.shields.io/badge/python-3.14--3.15-blue.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-1.97.1-orange.svg)](https://www.rust-lang.org/)
[![License](https://img.shields.io/badge/license-BUSL--1.1-green.svg)](core/rust/AEGIS-COGNITION/COMMERCIAL_CLOSURE_REPORT.md)
[![Status](https://img.shields.io/badge/status-evidence--in--progress-yellow.svg)](docs/architecture/BASELINE.md)

## Quick Start (2 minutes)

```bash
pip install aegis-cognition
aegis init
aegis run "Find trending repos on GitHub"
```

That's it. First agent runs in under 3 minutes.

## Why AEGIS?

| Feature | AEGIS | Hermes | LangGraph | Browser-Use |
|---------|-------|--------|-----------|-------------|
| **Setup time** | 2 min | 2 min | 3 min | 2 min |
| **API complexity** | 1 import | 1 import | 3 imports | 1 import |
| **Cryptographic evidence** | Yes (BLAKE3) | No | No | No |
| **Replay audit trail** | Yes (Arrow IPC) | FTS5 SQLite | Checkpoints | Trajectories |
| **Sandbox isolation** | Wasmtime (fuel+epoch) | No | No | No |
| **Provider fallback** | Automatic 429 → reserve | Config-only | Config-only | Config-only |
| **FTS5 search speed** | **69x faster** | Baseline | N/A | N/A |
| **JSON-RPC speed** | **29,443x faster** | Baseline | N/A | N/A |

## Python API

```python
from aegis_cognition import Agent

# Simple task
agent = Agent(task="Find trending repos on GitHub")
result = agent.run()
print(result.output)

# With browser automation
agent = Agent(
    task="Go to amazon.com and find the best laptop under $1000",
    browser=True,
    trust_level="DEV"
)
result = agent.run()

# One-liner
from aegis_cognition import run
result = run("Hello world")
```

## CLI

```bash
# Setup
aegis init

# Run tasks
aegis run "Search for AI papers on arXiv"
aegis run "Translate this text to Vietnamese"

# See examples
aegis examples

# Check config
aegis config show
```

## Trust Levels

| Level | Speed | Crypto | Evidence | Use Case |
|-------|-------|--------|----------|----------|
| **DEV** | Fast (< 5µs) | BLAKE3 hash only | Degraded OK | Local development |
| **STAGING** | Medium | BLAKE3 + Arrow IPC | Gaps recorded | CI/CD testing |
| **PROD** | Slower | Full chain + mmap | Fail-closed | Production deployment |

## Architecture

```
from aegis_cognition import Agent
         │
    ┌────▼────┐  1. Task → GoalIntakeProof
    │  Agent  │  2. LLM call with provider fallback
    └────┬────┘  3. Browser evidence (optional)
         │       4. Evidence commit → Hot Engine (InMemoryArena)
    ┌────▼────┐  5. Async seal → Cold Ledger (Arrow IPC)
    │ Result  │  6. Replay ledger append
    └─────────┘
```

## Benchmarks

| Benchmark | AEGIS | Hermes | Speedup |
|-----------|-------|--------|---------|
| FTS5 session search | 124 µs | 8,590 µs | **69x** |
| JSON-RPC context hydration | 1.5 µs | 44,140 µs | **29,443x** |
| Session recovery | 1.97 ms | 236.61 ms | **119x** |
| Persistence write | 29.5 ms | 2,252 ms | **76x** |

All benchmarks verified with deterministic BLAKE3 evidence hashes.

## Documentation

- [Quick Start Guide](docs/quickstart.md)
- [Agent API Reference](docs/api/agent.md)
- [CLI Commands](docs/api/cli.md)
- [Tutorials](docs/tutorials/)
- [Examples](examples/)
- [Error Guide](docs/troubleshooting/common-errors.md)
- [Security Architecture](docs/architecture.md)

## Requirements

- Python 3.14.x (production default) or 3.15.x (forward-compatibility lane)
- Rust toolchain (auto-downloaded for pre-built binaries)
- LLM API key (OpenAI, Anthropic, OpenRouter, or Nvidia NIM)
- Optional: Playwright (for browser automation)

## License

BUSL-1.1 — Free for development and non-production use.
Enterprise license required for production deployment.
See business model in `core/rust/AEGIS-COGNITION/COMMERCIAL_CLOSURE_REPORT.md`.

## Status

- Production readiness: **NOT CLAIMED** — see `docs/architecture/BASELINE.md`
- Resource authority: Rust-owned `HardwareProfile` + bounded admission contracts
- Benchmark status: **NOT VERIFIED** on the current hardware/resource architecture
- Sandbox status: scoped evidence required; no absolute escape guarantee is made

---

Built with Rust + Python + BLAKE3 + Arrow IPC + Wasmtime.  
[Website](https://aegis-cognition.ai) · [Docs](https://docs.aegis-cognition.ai) · [GitHub](https://github.com/aegis-cognition/aegis-cognition)
