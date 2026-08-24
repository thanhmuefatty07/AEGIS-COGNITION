# Resource policy benchmark protocol

The current thresholds are `ASSUMED DEFAULTS`, not architecture truth. This
protocol makes H0/H1/H2 reproducible before any numbers are frozen:

| Profile | Meaning for this repository | Required recording |
|---|---|---|
| H0 | constrained host: <=4 usable CPUs or <8 GiB effective memory | OS, cgroup/job limits, CPU/memory profile |
| H1 | balanced laptop/workstation: 5–16 usable CPUs and known memory | same plus thermal/power mode |
| H2 | performance host: >16 usable CPUs and known memory | same plus NUMA/accelerator inventory |

Run the harness once per host:

```powershell
python scripts/resource_policy_benchmark.py --profile H0 --output artifacts/resource-policy-H0.json
python scripts/resource_policy_benchmark.py --profile H1 --output artifacts/resource-policy-H1.json
python scripts/resource_policy_benchmark.py --profile H2 --output artifacts/resource-policy-H2.json
```

The command records the actual host profile, admission throughput, bounded
lane throughput, p50/p95 latency, queue behavior, and policy source. A command
run on the wrong hardware must be labeled `mismatch` rather than reused. Until
all three profiles have retained raw output and review, `ResourcePolicy`
values remain assumptions and are not advertised as measured performance.

## Stable scheduler benchmark IDs

The Rust Criterion harnesses under `core/rust/benches/` own the architecture
IDs:

| ID | Measurement |
|---|---|
| `FFI-001` | empty Python-to-Rust boundary call |
| `FFI-002` | typed 1 KiB payload |
| `FFI-003` | typed 64 KiB payload |
| `FFI-004` | typed 1 MiB payload by copy |
| `FFI-005` | mmap/shared-memory handle open |
| `SCH-001` | task insert |
| `SCH-002` | DAG validation at 100/1,000/10,000 tasks |
| `SCH-003` | ready-queue selection |
| `SCH-004` | resource admission decision |
| `SCH-005` | lease acquire/release |
| `SCH-006` | deterministic capacity feedback |
| `SCH-007` | bounded queue behavior at limits 1/8/32 |
| `SCH-008` | TaskLedger-integrated runtime submit/finish |
| `CPU-001` | hashing batch |
| `CPU-002` | parser/AST batch |
| `CPU-003` | serialization batch |
| `SBX-001` | Wasmtime cold compile/instantiate |
| `SBX-002` | cached module execution |
| `SBX-003` | fuel trap |
| `SBX-004` | epoch interruption |

These are microbenchmarks, not end-to-end product SLOs. Run them with the
recorded compiler, OS, CPU quota, memory profile, sample count, and raw
Criterion output before comparing hosts or freezing policy values. The new
IDs are implemented in `architecture_performance.rs`; `resource_runtime.rs`
retains the resource-admission IDs.
