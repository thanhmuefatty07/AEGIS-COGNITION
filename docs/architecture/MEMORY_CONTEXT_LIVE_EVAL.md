# Live memory-context model evaluation

This evaluator measures the same Memory Agent task with the full context and
the bounded context against an OpenAI-compatible hosted model. It is an
opt-in evidence job for the six synthetic tasks in
[`scripts/memory_context_live_eval.py`](../../scripts/memory_context_live_eval.py).
It does not make a production-readiness claim.

## What the result proves

For each selected provider and model, the harness runs every task twice (full
and bounded context), optionally repeats the pair, and records:

- provider-reported `usage.prompt_tokens` for both variants;
- deterministic answer checks: JSON shape, expected terms, evidence ids, and
  explicit unknown answers;
- returned model id, transport status, finish reason, latency, and a SHA-256
  response hash;
- paired prompt-token reduction and whether the bounded answer preserved the
  baseline quality;
- checkout commit, operating-system fingerprint, Python version, and run
  duration so the artifact can be tied to a precise environment.

`PASS` requires all calls to succeed, every pair to have positive provider
prompt-token counts, both variants to pass the oracle, and the provider to
return the same model id for each pair. A missing usage field, model-routing
change, malformed answer, or transport failure is reported as a failing
status instead of being silently averaged away.

The quality oracle is intentionally narrow and reproducible. It is evidence
that these six facts and scope boundaries survive the context reduction; it is
not a general measure of intelligence, factuality, safety, or production
quality.

## Data and secret boundary

Only eight synthetic memory records are sent. Repository source, local files,
conversation content, and the API keys are never included in a request or
artifact. Raw model text is removed after scoring; the JSON artifact keeps
only response hashes and metadata. Keys are read from process environment
variables and are never written to disk by the harness.

The two keys pasted into the chat must be revoked and replaced before any live
run. Treat them as compromised. Create fresh keys in the provider dashboards;
do not copy the old values into a file, command history, VM image, or GitHub
commit.

## Provider endpoints and model selection

The harness uses the documented OpenRouter chat-completions endpoint and the
documented NVIDIA hosted NIM endpoint:

- [OpenRouter chat completions API](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
  and [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting);
- [NVIDIA NIM LLM APIs](https://docs.api.nvidia.com/nim/re/reference/llm-apis) and
  [hosted NIM product guidance](https://docs.api.nvidia.com/nim/docs/product).

OpenRouter accepts `openrouter/free` or an explicit model id ending in
`:free`. The router can select different underlying models between requests;
that is why the evaluator records `model_returned` and fails with
`MODEL_MISMATCH` when a pair is not like-for-like. For the strongest comparison,
use one currently available explicit `:free` model. NIM model availability is
catalog-dependent, so pass a model that is currently enabled for the fresh
key.

Free hosted access is suitable for a bounded test or prototype. Availability,
latency, quotas, and provider-side logging policies can change; the live
artifact records what happened for this run and does not generalize beyond
that provider/model/time combination.

## Local run (Windows PowerShell)

First verify the harness without network access:

```text
uv sync --locked --extra all --extra dev --no-install-project
uv run --no-project --no-sync --locked --extra all --extra dev python scripts/memory_context_live_eval.py --dry-run --output artifacts/memory-context-live-eval-dry-run.json
```

For a live run, load fresh keys only into the current process. The following
keeps the values out of the command line and clears them afterwards:

```powershell
$orKey = Read-Host "OpenRouter key" -AsSecureString
$nimKey = Read-Host "NVIDIA NIM key" -AsSecureString
$env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new("", $orKey).Password
$env:NVIDIA_NIM_API_KEY = [System.Net.NetworkCredential]::new("", $nimKey).Password

uv run --no-project --no-sync --locked --extra all --extra dev python scripts/memory_context_live_eval.py `
  --provider auto `
  --openrouter-models "<explicit-free-model>" `
  --nvidia-models "<available-nim-model>" `
  --repeats 1 `
  --max-requests 24 `
  --output artifacts/memory-context-live-eval-local.json

Remove-Item Env:OPENROUTER_API_KEY, Env:NVIDIA_NIM_API_KEY
Remove-Variable orKey, nimKey
```

Use `--provider openrouter` or `--provider nvidia` when only one key is
available. The planned request count is `providers × models × 6 tasks × 2
variants × repeats`; the harness rejects counts above 96 or the selected
`--max-requests` value.

## GitHub Actions run

The manual workflow
`.github/workflows/memory-context-live-eval.yml` has no push or schedule
trigger. Add repository Actions secrets named exactly:

- `OPENROUTER_API_KEY`
- `NVIDIA_NIM_API_KEY`

Then open **Actions → Memory context live model evaluation → Run workflow**.
You can first set `dry_run=true` to validate the workflow and artifact upload
without making any provider call or using a secret.
Start with one explicit model and `repeats=1`; increase to `repeats=2` or `3`
only when the provider quota allows it. The workflow uploads one JSON artifact
and never prints a key. A hosted Ubuntu runner is sufficient for the provider
measurement; the existing Ubuntu/Windows/macOS matrix remains the evidence
for cross-platform context compilation.

## GCP VM role

The GCP Linux VM is useful as an independent Linux sample and for reproducing
the provider-neutral benchmark. It is not required for every hosted provider
call and does not add another operating system. If a live run is needed there,
inject the fresh key into an ephemeral shell variable, run the same script,
and unset it immediately. Do not save it in a VM file, shell profile, image,
startup script, or command history. Stop the VM after the bounded run when it
is not needed.

## Status interpretation

| Status | Meaning |
| --- | --- |
| `DRY_RUN` | Planned requests only; no network call or quality claim. |
| `PASS` | Complete, like-for-like provider evidence with no bounded quality regression. |
| `PARTIAL` | One or more provider calls failed or timed out. |
| `QUALITY_REGRESSION` | A baseline answer passed while its bounded pair failed. |
| `MODEL_MISMATCH` | The provider returned different model ids inside a pair. |
| `TOKEN_USAGE_INSUFFICIENT` | Provider prompt-token usage was missing or invalid. |
| `QUALITY_INSUFFICIENT` | Calls completed, but one or more oracle checks did not pass. |

Even `PASS` is scoped evidence: it supports the declared six-task synthetic
workload for the recorded commit, provider, model, and run. It does not prove
production readiness, universal token savings, privacy policy compliance of a
provider, or correctness for arbitrary user memories.

## Repository verification

The five evaluator tests passed on Ubuntu, macOS, and Windows inside the full
Python jobs of
[CI run 34927977822](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34927977822)
at commit `a2423aafce781aff7968190c01bfe62b44ef6d2c`. That umbrella run still
failed its separate 11 AESE registry-drift tests; those failures do not import
or exercise this evaluator. No hosted model call has been run yet because the
keys previously pasted into chat must be revoked and replaced first.

The workflow plumbing was then exercised without credentials in
[dry-run 34929741934](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34929741934)
at commit `51eae346f913a46a43bd3b15da378d942efd2708`. It completed successfully
with 24 planned and observed records for both providers; the retained artifact
was `DRY_RUN` and contained neither raw model content nor key prefixes. This
run proves workflow execution and artifact handling, not provider availability
or model quality.
