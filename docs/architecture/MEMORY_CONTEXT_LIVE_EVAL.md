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
run. Treat them as compromised. The live runs below used fresh repository
Actions secrets; the old values were not used or written to the repository.

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
catalog-dependent, so pass a model that is currently enabled for the fresh key.
The workflow default is
`deepseek-ai/deepseek-v4-flash`; verify the account's Public API Endpoints access
and current model page before a live run.

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
  --delay-ms 2400 `
  --output artifacts/memory-context-live-eval-local.json

Remove-Item Env:OPENROUTER_API_KEY, Env:NVIDIA_NIM_API_KEY
Remove-Variable orKey, nimKey
```

Use `--provider openrouter` or `--provider nvidia` when only one key is
available. The planned request count is `providers × models × 6 tasks × 2
variants × repeats`; the harness defaults to 24 and rejects counts above 96
or the selected `--max-requests` value. The default 2,400 ms delay keeps a
sequential run below about 25 requests per minute; do not lower it for a free
NIM account unless its current account limit is verified. Keep free-tier probes
at 12 or 24 requests and raise the limit only when the account quota justifies it.

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
or exercise this evaluator. The earlier chat-exposed keys were revoked and replaced before the
live runs below; those runs used only the fresh repository Actions secrets.

The workflow plumbing was then exercised without credentials in
[dry-run 34929741934](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34929741934)
at commit `51eae346f913a46a43bd3b15da378d942efd2708`. It completed successfully
with 24 planned and observed records for both providers; the retained artifact
was `DRY_RUN` and contained neither raw model content nor key prefixes. This
run proves workflow execution and artifact handling, not provider availability
or model quality.

## Live-provider evidence log

These runs used fresh repository Actions secrets. They sent only the six
synthetic tasks described above; artifacts retain metadata and response hashes,
not raw model text or key values.

The broad matrix run
[34958748964](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34958748964)
at commit `f231b3181c5b5574b20938c6af587368d24ec055` planned and observed 96
requests and finished `PARTIAL`:

- OpenRouter `nex-agi/nex-n2.5-pro:free`: 12/12 transport successes, provider
  token usage complete, mean prompt reduction `9.413%`, but oracle quality
  passed 5/6 baseline and 4/6 bounded cases (one regression).
- OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`: 10/12 transport
  successes; two transport errors and incomplete oracle coverage; successful
  pairs averaged `9.067%` prompt reduction.
- OpenRouter `nvidia/nemotron-3.5-lightning:free`: 12/12 transport successes
  and `9.082%` mean reduction, but no response passed the JSON quality oracle.
- OpenRouter `google/gemma-4-31b-it:free`: 12/12 responses were HTTP `429`.
- NVIDIA returned HTTP `410` for the three `meta/llama` IDs tested and HTTP
  `404` for `moonshotai/kimi-k2.6`; no NVIDIA quality or token result exists.

The focused NVIDIA probe
[34959258795](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34959258795)
tested the catalog-listed `deepseek-ai/deepseek-v4-flash` model with 12
requests. All 12 returned HTTP `410`, so the endpoint/account availability
still needs confirmation in NVIDIA Build; this is not evidence of model
quality.

The additional OpenRouter probe
[34959406647](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34959406647)
planned and observed 48 requests and finished `PARTIAL`: the Cohere model
returned HTTP 200 without a usable message content field, Liquid returned 11
such responses plus one `429`, Nex Mini timed out 12 times, and Laguna XS had
two successes plus ten `429` responses. No complete quality pair was produced
by that run.

The three-repeat Nex Pro stability run
[34960662950](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34960662950)
planned and observed 36 requests; all were HTTP `429` after the earlier matrix,
so it is a provider-rate-limit observation rather than a quality measurement.

At the time of these runs, OpenRouter's
[FAQ](https://openrouter.ai/docs/faq) stated that free-model API access is
limited to 50 requests per day without at least 10 purchased credits (and
1000 per day after that threshold). The three OpenRouter runs above attempted
132 requests in total, so the later `429` responses are consistent with the
documented free-tier quota. The evaluator and workflow now default to 24
requests; this is a guardrail, not a claim that provider quotas are unlimited.

## Rate-25 NIM follow-up

The hosted NIM workflow was rerun with `delay_ms=2400`, which caps sequential
scheduling at about 25 requests per minute, `repeats=1`, and no automatic
retries. The runs used model IDs listed in the current NVIDIA catalog.

- [34972808716](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34972808716)
  at commit `16ec4b992c6a4844a02e3a9e1cb9a34435afe50e` planned and observed 48
  requests: `meta/llama-3.2-1b-instruct`, `microsoft/phi-4-mini-instruct`, and
  `nvidia/llama-3.1-nemotron-nano-8b-v1` each returned 12/12 HTTP `410`; the
  `openai/gpt-oss-20b` model returned seven usable HTTP `200` responses and
  five HTTP `200` responses without message content. It produced only three
  complete pairs, with `8.439%` mean prompt reduction and incomplete quality
  coverage. No response was HTTP `429`.
- [34973622950](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/34973622950)
  at commit `edaf1b60851304a287ccc2e8aa3f817543322132` planned and observed 24
  requests: `moonshotai/kimi-k2-instruct` and
  `qwen/qwen2.5-coder-32b-instruct` each returned 12/12 HTTP `410`. The artifact
  completed in 57.3 seconds and contained no HTTP `429` response.

Across this follow-up, 72 requests were attempted at the 25-RPM pacing ceiling
and none produced HTTP `429`. This does not prove the account has no rate
limit; it shows that these failures were not rate-limit evidence. The remaining
NVIDIA blocker is endpoint/model availability or account entitlement, and a
complete quality/token `PASS` has not been established.

These live results are exploratory, scoped to this commit, provider, model,
and timestamp. They do not establish a `PASS`, universal token savings, or
production readiness. A future run should wait for the provider quota window,
use currently available model IDs, and address NVIDIA account endpoint access
before collecting repeat evidence.
