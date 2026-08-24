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

The Rust Criterion harness `core/rust/benches/resource_runtime.rs` owns the
initial architecture IDs:

| ID | Measurement |
|---|---|
| `SCH-004` | resource admission decision |
| `SCH-005` | lease acquire/release |
| `SCH-006` | deterministic capacity feedback |
| `SCH-007` | bounded queue behavior at limits 1/8/32 |
| `SCH-008` | TaskLedger-integrated runtime submit/finish |

These are microbenchmarks, not end-to-end product SLOs. Run them with the
recorded compiler, OS, CPU quota, memory profile, sample count, and raw
Criterion output before comparing hosts or freezing policy values.
