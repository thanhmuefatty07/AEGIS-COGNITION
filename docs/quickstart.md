# Quick Start

Get your first AEGIS agent running in under 5 minutes.

## 1. Install

```bash
pip install aegis-cognition
```

## 2. Setup

```bash
aegis init
```

Follow the prompts:
1. Choose your LLM provider (OpenAI, Anthropic, OpenRouter, etc.)
2. Enter your API key
3. Choose trust level (DEV for now)

## 3. Run your first agent

```bash
aegis run "Find the top 5 AI agent frameworks on GitHub"
```

Or use the Python API:

```python
from aegis_cognition import Agent

agent = Agent(task="Find trending repos on GitHub")
result = agent.run()
print(result.output)
```

## That's it!

You just ran a cryptographically-verified AI agent.

## Next Steps

- [Agent API Reference](api/agent.md)
- [CLI Commands](api/cli.md)
- [Web Research Tutorial](tutorials/web-research.md)
- [Browser Automation](tutorials/browser-automation.md)
- [Examples](../examples/basic/hello_world.py)

## Common Issues

### "API key not found"
Run `aegis init` or set `OPENAI_API_KEY` environment variable.

### "Browser evidence collection failed"
Install Playwright: `playwright install chromium`

### "Rate limit hit"
AEGIS automatically falls back to reserve providers. If all are throttled, wait 30 seconds.

[Full troubleshooting guide →](troubleshooting/common-errors.md)