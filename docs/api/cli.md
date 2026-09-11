# CLI Commands

The installed command is `aegis`:

```bash
aegis init
aegis run "Summarize the supplied evidence"
aegis examples
aegis config show
aegis config set trust.level STAGING
aegis config set llm.api_key YOUR_API_KEY
```

Provider credentials and trust level are configuration inputs. A CLI command
does not override Lab policy, browser allowlists, resource bounds or evidence
status. `config set` validates the documented keys and writes the TOML file
atomically. `config show` redacts credential-like values before printing. Prefer
environment variables or a platform secret store for long-lived API keys. See
[`quickstart.md`](../quickstart.md) and the canonical
[`AEGIS_LAB_RUNTIME_MASTER_PLAN.md`](../architecture/AEGIS_LAB_RUNTIME_MASTER_PLAN.md)
for the current behavior. The command returns exit status `0` on success, `2`
for usage or unknown-command errors, and `1` when configuration or agent
execution fails, so scripts can distinguish a completed run from a rejected
command.
