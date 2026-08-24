# Verification baseline

This is the current repository evidence ledger for the architecture slice. It
records what was actually run; it is not a production-readiness certificate.

## Local evidence on 2026-08-22

| Gate | Result | Evidence label | Boundary |
|---|---|---|---|
| `cargo metadata --locked --no-deps` | PASS | PROVEN | Workspace metadata only |
| `cargo fmt --all -- --check` | PASS | PROVEN | Current checkout |
| `cargo check --all-targets --no-default-features --locked` | PASS | PROVEN | Windows x86_64 host |
| `cargo clippy --workspace --all-targets --no-default-features -- -D warnings` | PASS | PROVEN | Windows x86_64 host |
| `cargo nextest run --no-default-features` | 409/409 passed; 0 skipped; 3 slow | PROVEN | Windows x86_64 host; production default-members |
| `cargo llvm-cov --workspace --no-default-features` | PASS; report written to `artifacts/rust-coverage.lcov`; core/plugin/POC suites passed | MEASURED | Windows x86_64 host; full workspace deep lane |
| `cargo deny check advisories bans licenses sources` | All four checks passed; duplicate-version notices remain warnings | PROVEN | Locked Rust workspace |
| Python pytest (`tests` + `core/python/tests.py`) | 89 passed, 1 deprecation warning | PROVEN | CPython 3.14.0 local; exact 3.14.7 is CI evidence |
| Pyright | 0 errors, 0 warnings | PROVEN | `aegis_cognition`, strict mode |
| Ruff lint/format | PASS with E4/E7/E9/F/UP/B/SIM/PERF/RUF/ASYNC | PROVEN | Canonical Python package and split gateway modules |
| Architecture fitness | PASS | PROVEN | Current checkout |
| Dependency audit | 8/8 checks passed | PROVEN | Locked Rust direct dependencies |
| `cargo audit` | PASS; 396 locked packages scanned | SOURCE-BACKED + PROVEN | Advisory database fetched during run |
| Secret scan | 376 tracked files inspected; no findings | PROVEN | Tracked text files; provider-side scanning remains separate |
| Supply-chain gate | 20/20 internal checks passed | PROVEN | External signed attestation absent; release remains blocked |
| Maturin wheel | Clean external import of `Agent`, `AegisAdapter`, native contract version, and runtime telemetry FFI | PROVEN | CPython 3.14.0 Windows wheel |
| Resource policy benchmark | H1 label, `MEASURED_LOCAL_ONLY`, 4 usable CPUs | MEASURED | Must not be reused as H1 hardware proof |
| Fuzz target | `cargo check --manifest-path fuzz/Cargo.toml --locked` passed | PROVEN | Build/target exists; campaign result remains NOT VERIFIED |

## Incremental architecture evidence on 2026-08-25

| Gate | Result | Evidence label | Boundary |
|---|---|---|---|
| Resource contract unit tests | 10/10 passed | PROVEN | Windows host; accelerator capability/kind/backend matching, failed-device filtering, memory-domain accounting, and pressure feedback |
| Runtime admission tests | 6/6 runtime cases passed | PROVEN | Windows host; FIFO queue drain and deadline reclamation included |
| Execution lane tests | 4/4 passed | PROVEN | Windows host; native process lane fails closed without controller attachment |
| Default-member Rust nextest | 418/418 passed; 0 skipped; 1 slow | PROVEN | Windows host; includes the new resource contract matrix |
| Resource runtime benchmarks | `SCH-004` 489 ns median; `SCH-005` 529 ns; `SCH-006` 244 ns; `SCH-007` 382/1,944/6,165 ns; `SCH-008` 1.061 µs | MEASURED | Windows host, Criterion sample-size 10; local comparison only, not H0/H1/H2 evidence |
| Architecture microbenchmarks | FFI-001 7.56 ns; FFI-002 4.46 µs; FFI-003 24.26 µs; FFI-004 786.90 µs; FFI-005 80.14 µs; SCH-001 148.84 ns; SCH-002 27.12/384.74/4.538 ms; SCH-003 24.54 µs; CPU-001 18.41 µs; CPU-002 16.45 µs; CPU-003 161.10 ns; SBX-001 890.90 µs; SBX-002 3.332 µs; SBX-003 4.381 µs; SBX-004 16.554 ms | MEASURED | Windows host, Criterion sample-size 10; smoke measurement only, not H0/H1/H2 evidence |
| Release evidence generator | Static implementation present | PROVEN | Generator/workflow path; no tag-triggered attestation run yet |

## Remote evidence on 2026-08-25

| Gate | Result | Evidence label | Boundary |
|---|---|---|---|
| GitHub CI for `d22a32ef7f0ee007971d26d23f55969fad3c398a` | 9/9 jobs passed | PROVEN | Rust MSRV 1.97.1, Python 3.14.7, 3.14.7t experimental, 3.15.0rc1, beta, and Ubuntu/Windows/macOS Tier-1 lanes; [run 32772520671](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/32772520671) |
| Windows plugin workflow for `d22a32ef7f0ee007971d26d23f55969fad3c398a` | Format, compile, tests, and clippy passed | PROVEN | Windows plugin workspace; [run 32772520755](https://github.com/thanhmuefatty07/AEGIS-COGNITION/actions/runs/32772520755) |

## Open evidence

- Exact CPython 3.14.7 and 3.15.0rc1 matrix and the current CI/plugin workflow
  runs are externally proven. The newly added 3.15t experimental lane still
  requires a post-change runner result. Tier-1 macOS arm64 package/import,
  privileged Linux cgroup and Windows Job Object enforcement remain open.
- H0/H1/H2 policy freeze, Wasmtime fuzz/adversarial corpus, replay parity across
  platforms, sanitizer/Miri campaign, OTel exporter semantics, release restore,
  signed tag/provenance, and external signed attestation remain `NOT VERIFIED`.
- GitHub branch-protection state could not be read because the private
  repository plan returned HTTP 403; this is recorded as `NOT VERIFIED`, not as
  a claim that protections are absent.
