# Optional ChatGPT Web provider

AEGIS can route a text provider call through the MIT-licensed
[`miuuyy/codex-chatgpt-web`](https://github.com/miuuyy/codex-chatgpt-web) bridge. The bridge uses
the ChatGPT Web session that the user signs into in its own browser profile and exposes a
loopback-only Responses endpoint. This avoids an OpenAI model API key, but it does **not** remove
ChatGPT account message limits, subscription requirements, privacy processing, or OpenAI policy
obligations. It is not an unlimited or quota-bypass path.

## Install the reviewed Windows release

From the repository root, run the pinned installer in PowerShell:

```powershell
& .\scripts\install_chatgpt_web_bridge.ps1
```

The installer verifies the v5.0.2 Windows x64 archive SHA-256, installs it under the current
user's local application data, and does not start the browser or alter Codex settings. Launch the
installed `bin\codex-chatgpt-web.cmd`, complete sign-in in that private browser, run its browser
check, and leave the bridge service running when AEGIS is used.

On Windows, the upstream setup currently requires its desktop browser host. Install it separately
with the pinned helper, and launch it only when you are ready for interactive sign-in:

```powershell
& .\scripts\install_chatgpt_web_launcher.ps1 -Launch
```

If that upstream installer exits non-zero, stop there: no authenticated session is available and
the AEGIS adapter will fail closed. Do not copy cookies or session files from another browser.

## Configure AEGIS

Run `aegis init`, choose `ChatGPT Web (local browser bridge)`, and keep the default loopback URL.
The resulting `~/.aegis/config.toml` contains only routing metadata:

```toml
[llm]
provider = "chatgpt-web"
base_url = "http://127.0.0.1:17841/v1"
model = "chatgpt-web/high"
```

The default model is account-gated. If the bridge reports that the selected effort is unavailable,
choose an account-eligible model such as `chatgpt-web/light` or `chatgpt-web/luna`.

The AEGIS adapter enforces `http://127.0.0.1/.../v1`; it rejects remote hosts, credentials,
queries, fragments, and non-Responses URLs. It never imports cookies, reads browser storage, or
enables the bridge's full MCP/tool tunnel. Tool execution remains owned by AEGIS's existing Lab
and gateway contracts.

## SDK use

```python
from core.python.chatgpt_web_client import ChatGPTWebClient
from aegis_cognition import Agent

client = ChatGPTWebClient.from_env()
result = Agent("summarize the current evidence", llm=client, trust_level="DEV").run()
```

`ChatGPTWebClient.health()` is a read-only local readiness check. A failed check means the bridge
is not running or sign-in is incomplete; it is not evidence that the ChatGPT account has a model
or quota available.

## Security boundary

Keep the bridge's browser profile private. The loopback listener is reachable by processes running
as the same Windows user. Do not share, sync, commit, or attach its profile, cookies, diagnostics,
or tunnel credentials. Do not use account-token extraction projects such as `chat2api`; AEGIS does
not support that authentication model.
