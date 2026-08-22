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
| Python↔Rust PyO3 symbols | `ffi.rs`, `aegis_cognition/runtime.py` | Rust implementation | Coarse calls only; Python fallback is explicitly non-authoritative |

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
