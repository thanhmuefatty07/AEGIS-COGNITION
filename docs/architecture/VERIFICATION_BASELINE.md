# Verification baseline

This is the current repository evidence ledger for the architecture slice. It
records what was actually run; it is not a production-readiness certificate.

## Local evidence on 2026-08-22

| Gate | Result | Evidence label | Boundary |
|---|---|---|---|
| `cargo metadata --locked --no-deps` | PASS | PROVEN | Workspace metadata only |
| `cargo fmt --all -- --check` | PASS | PROVEN | Current checkout |
| `cargo check --workspace --all-targets --no-default-features` | PASS | PROVEN | Windows x86_64 host |
| `cargo clippy --workspace --all-targets --no-default-features -- -D warnings` | PASS | PROVEN | Windows x86_64 host |
| `cargo test --workspace --no-default-features` | 397 core tests passed plus plugin/POC tests and doc-tests | PROVEN | Windows x86_64 host |
| Python pytest | 86 passed, 1 deprecation warning | PROVEN | CPython 3.14.0 local; exact 3.14.7 is CI evidence |
| Pyright | 0 errors, 0 warnings | PROVEN | `aegis_cognition`, strict mode |
| Ruff lint/format | PASS | PROVEN | `aegis_cognition` canonical package |
| Architecture fitness | PASS | PROVEN | Current checkout |
| Dependency audit | 8/8 checks passed | PROVEN | Locked Rust direct dependencies |
| `cargo audit` | PASS; 396 locked packages scanned | SOURCE-BACKED + PROVEN | Advisory database fetched during run |
| Secret scan | 376 tracked files inspected; no findings | PROVEN | Tracked text files; provider-side scanning remains separate |
| Supply-chain gate | 20/20 internal checks passed | PROVEN | External signed attestation absent; release remains blocked |
| Maturin wheel | Clean external import of `Agent`, `AegisAdapter`, and native contract version | PROVEN | CPython 3.14.0 Windows wheel |
| Resource policy benchmark | H1 label, `MEASURED_LOCAL_ONLY`, 4 usable CPUs | MEASURED | Must not be reused as H1 hardware proof |
| Fuzz target | `cargo check --manifest-path fuzz/Cargo.toml --locked` passed | PROVEN | Build/target exists; campaign result remains NOT VERIFIED |

## Open evidence

- Exact CPython 3.14.7 and 3.15.0rc1 matrix, Tier-1 macOS arm64 package/import,
  privileged Linux cgroup and Windows Job Object enforcement, and current
  GitHub workflow runs require external runner evidence.
- H0/H1/H2 policy freeze, Wasmtime fuzz/adversarial corpus, replay parity across
  platforms, sanitizer/Miri campaign, OTel exporter semantics, release restore,
  signed tag/provenance, and external signed attestation remain `NOT VERIFIED`.
- GitHub branch-protection state could not be read because the private
  repository plan returned HTTP 403; this is recorded as `NOT VERIFIED`, not as
  a claim that protections are absent.
