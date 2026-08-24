# Miri, AddressSanitizer, and fuzz scope

The deep workflow intentionally uses selected probes. A selected probe is
evidence for its listed path only; it is not whole-workspace memory-safety
proof.

| Lane | Exact scope | Target/features | Exclusions and boundary | Status |
|---|---|---|---|---|
| Miri | `core/rust` library tests filtered to `resource::tests` | nightly + `miri`, `rust-src`, `MIRIFLAGS=-Zmiri-disable-isolation`, `--no-default-features` | No native OS controller, PyO3 extension, Wasmtime host, network, or full workspace | NOT VERIFIED until current final-SHA run is retained |
| AddressSanitizer | `core/rust` library tests filtered to `resource::tests` | nightly, `RUSTFLAGS=-Z sanitizer=address`, `-Zbuild-std`, `x86_64-unknown-linux-gnu`, `--no-default-features` | Selected resource paths only; no Python/native extension or platform controller proof | NOT VERIFIED until current final-SHA run is retained |
| Fuzz | `resource_contract`, `protocol_frame`, `runtime_ffi_contract`, `archive_prefix` | nightly cargo-fuzz; 900 seconds per target in deep campaign | Corpus/crash retention and exact execution counts must be in uploaded artifact; no claim for unlisted targets | NOT VERIFIED until campaign artifact is retained |

The Miri/ASan jobs are allowed to be non-blocking only as an explicit
platform/toolchain limitation. A green selected probe does not close the
broader hostile-kernel, FFI, Wasmtime, or archive campaign.
