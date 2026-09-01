# Contract and persistence inventory

This inventory covers the architecture slice in the master plan. The wider
repository has additional domain-specific evidence formats; those remain under
their owning module and must not be treated as interchangeable with the
runtime/resource contracts below.

## Versioned persisted or exchanged formats

| Contract | Location/owner | Authority | Compatibility rule |
|---|---|---|---|
| `aegis-resource-contract-v1` | `schemas/resource-contract-v1.json`, `resource.rs` | Rust resource authority | Reject an unknown schema; additive changes require a new version and fixture |
| `aegis-resource-lease-token-v1` | `schemas/lease-token-v1.json`, `resource.rs` | Rust lease ledger | Token carries identity/fencing only; it never carries a mutable grant |
| `aegis-runtime-admission-v1` | `runtime.rs`, `ffi.rs` | Rust runtime authority | Submit/retry/finish must pass attempt and lease fencing |
| `aegis-runtime-telemetry-v1` | `schemas/runtime-telemetry-v1.json`, `telemetry.rs` | Observation only | Bounded sink may drop oldest events; dropped telemetry cannot change authority |
| Python runtime telemetry bridge | `aegis_cognition/observability.py`, `ffi.rs` (`aegis_runtime_telemetry_emit`) | Observation only | Validate schema/correlation before Rust bounded sink; forwarding failure is degraded telemetry only |
| Replay/event archives | `replay.rs`, Arrow/binary archive modules | Rust replay ledger | Append-only, hash-bound, crash-prefix recovery; format-specific tests own migration proof |
| `aegis-friendly-trust-policy-v1` / Lab policy subject | `core/python/aegis/trust_policy.py` (canonical subject), `aegis_cognition/config.py`, `core/python/aegis/evidence.py`, `aegis_cognition/lab.py`, `core/python/aegis_adapter.py` | Canonical payload/hash primitive; validated `AgentConfig`/`LabPolicy` create the mission subject; adapters verify the propagated hash | Canonical fields and BLAKE2b hash must match across Agent/Lab/core for DEV, STAGING and PROD; `Agent(lab=True)` binds `lab_trust_policy_hash`; a mismatched explicit hash is rejected; direct compatibility defaults remain contextual legacy behavior and are not policy-bound |
| `aegis-lab-run-v1` trust binding | `aegis_cognition/lab.py` `LabRun.to_payload/from_payload` | Python snapshot compatibility projection, with native mission contract hash when bound | `trust_level` and optional `trust_policy_hash` are additive; bound event envelopes and execution-cell manifests carry the same subject; missing hash preserves legacy direct snapshots but cannot be treated as a policy-bound proof; present hash must equal the canonical trust subject |
| `AuthorityMode` | `aegis_cognition/lab.py`, `aegis_cognition/__init__.py` | Explicit Lab authority context | `projection_only`, `native_admitted`, and `native_required` are serialized in snapshots/manifests; legacy `require_native_authority` maps deterministically; conflicting legacy/native declarations fail closed |
| Python↔Rust PyO3 symbols | `ffi.rs`, `aegis_cognition/runtime.py` | Rust implementation | Coarse calls only; Python fallback is explicitly non-authoritative |
| `ExecutionCellBinding` / `ExecutionCellRegistry` | `aegis_cognition/lab.py` | Trusted configuration plus Lab admission fence | Controller may select only a registered cell identity; capability, effect class, trust level and (when Lab-bound) canonical trust-policy hash are checked before invocation; an explicitly supplied registry is a strict allowlist and cannot fall back to legacy callables; compatibility RAG `memory.search_past` is a dedicated `context_retrieval` read-only cell and prompt-injection markers fail closed; post-completion `memory.index_session` is a dedicated `post_completion_effect` cell and required policy fails closed when it is absent; the registry seals after manifest capture so late registration or mutation of the construction table cannot diverge from replay-bound bindings; manifest round-trips with the Lab snapshot; crash recovery scans and settles every open execution admission fail-closed |

## Runtime control seams

| Seam | Owner | Safety rule | Current evidence |
|---|---|---|---|
| `HardwareProfile` + `ResourcePolicy` | Rust `resource.rs` | Unknown capacity is finite and policy provenance is explicit | `PROVEN` unit tests; H0/H1/H2 freeze remains `NOT VERIFIED` |
| `CapacityFeedback` | Rust `AdmissionController` | Samples may reduce future admission capacity but cannot revoke an active lease | `PROVEN` pressure-feedback test |
| queued admission drain | Rust `AuthoritativeRuntime` | FIFO bounded queue is re-admitted only through `TaskLedger` transitions | `PROVEN` queue-release integration test |
| deadline reaper | Rust `AuthoritativeRuntime` | Expired leases become `TimedOut` and release lane/resource counters | `PROVEN` timeout-reclamation test |
| `AcceleratorExecutor` | Rust execution seam | Vendor adapters are optional; capability matching precedes execution | `PROVEN` CPU-only fail-closed test; vendor backend `NOT VERIFIED` |
| native process lane | Rust execution + `ResourceController` | Child process must be attached to an OS controller before arbitrary native work runs | `PROVEN` fail-closed portable test; privileged Linux/Windows live enforcement `NOT VERIFIED` |
| Lab edge execution cells | Python `ExecutionCellRegistry` + Rust-admitted Lab receipts | Search/browser/experiment/simulation/tool/skill/context-retrieval/benchmark-validation/post-completion runners are injected at the trusted boundary; model output never carries a callable; an explicitly supplied registry is a strict allowlist; an interrupted admission cannot be promoted to success; swallowed cancellation is fenced; opt-in `ProcessExecutionCell` can terminate its own picklable child on local timeout/cancellation; configured replay directories use an advisory cross-process `ReplayWriterLease`; operator-owned benchmark validator subprocesses are admitted/settled as typed compute cells; browser launch is admitted as a typed `launch` action before an untrusted launcher is invoked; browser admission rejects literal and obfuscated unsafe IP destinations; research fetch rejects literal/obfuscated private IPs before connection and performs a hostname-resolution preflight; browser observer projections reject bounded prompt-injection markers and retain hash-only security evidence, then stop controller continuation | `PROVEN` local identity/capability/effect/duplicate rejection, context retrieval admission/hash-only result/prompt-injection rejection and required-cell missing failure, required post-completion cell missing fails closed, strict legacy search/browser/skill/benchmark-validator fallback rejection, sealed-snapshot resistance to post-seal construction-table mutation, mixed-lane crash-prefix recovery, swallowed-cancellation rejection, process-cell timeout/cancellation termination, per-application active-run rejection, replay-writer process contention rejection, isolated-validator success/tampered-input/timeout rejection, literal IPv4/IPv6/decimal-IP unsafe-address rejection, research initial unsafe-literal/private-DNS/provider-candidate rejection, browser launch pre-admission ordering, browser prompt-injection rejection with hash-only evidence and controller-stop behavior, hostname-resolution private-address rejection, current replacement-wheel root/core/combined owner/import probes (v63 controller/recovery smoke is historical and not replayable); descendant cleanup, DNS rebinding race closure, OS-level process isolation, hosted writer service, hosted scorer secrecy and external-side-effect reversal remain `NOT VERIFIED` |
| Lab execution-cell deadline and idempotency metadata | `LabApplication._run_admitted_gateway`, `_run_tool_calls`, `_run_admitted_experiment`, `_run_admitted_simulation`, `LabRun.admit_tool_execution` / `record_tool_execution`, `LabRun.admit_experiment_execution` / `record_experiment_execution` | Each Lab-owned gateway, explicit generic-tool, experiment, or simulation attempt carries a finite positive local deadline and a deterministic 64-hex key bound to mission/execution/input/policy; mismatched or duplicate metadata fails closed; cooperative async timeout settles `TIMED_OUT` | `PROVEN` local retry/timeout regression (255-test gate) and packaged wheel probe for generic tool; synchronous/non-cooperative adapters, external effect idempotency and global attempt bound remain `NOT VERIFIED` |

The post-completion `memory.index_session` lane is now a dedicated
`post_completion_effect` execution cell; required policy records
`post_completion_effect_missing` instead of returning silently when no trusted
cell is configured.

Evidence reconciliation (2026-09-01): the `packaged v63 controller/recovery
smoke` wording in the execution-cell row is historical only; v63 artifacts are
absent and not replayable. Current package evidence is limited to the retained
replacement-wheel root/core/combined owner and import probes. Descendant
cleanup, DNS-rebinding race closure, OS-level process isolation, hosted writer
service, hosted scorer secrecy, and external-side-effect reversal remain
`NOT VERIFIED`.

## FFI surface for this architecture slice

The supported resource/runtime entry points are:

- `aegis_hardware_profile` — capability report only;
- `aegis_resource_contract_version` — schema identity;
- `aegis_resource_admission_preview` — preview/decision report;
- `aegis_execution_lanes` — bounded lane snapshot;
- `aegis_runtime_submit` — submit and return an opaque lease token;
- `aegis_runtime_retry` — start a fenced newer attempt;
- `aegis_runtime_finish` — complete by opaque token and outcome.
- `aegis_runtime_telemetry_emit` — validate and record one bounded observation event.
- `aegis_runtime_telemetry_snapshot` — inspect bounded observation events and drop count.

Other PyO3 symbols in `ffi.rs` belong to legacy frame/LLM/browser/evidence
surfaces and are not allowed to mint resource authority. New FFI calls must be
coarse, typed, versioned, bounded, and covered by a cross-language smoke test.

## Migration evidence

Current deterministic proof covers schema identity, forged/stale/duplicate
lease tokens, retry attempt fencing, JSON round-trips, and clean wheel import.
Cross-version persisted archive migration, Tier-1 wheel parity, and release
rollback/restore drills remain `NOT VERIFIED` until retained artifacts exist.
