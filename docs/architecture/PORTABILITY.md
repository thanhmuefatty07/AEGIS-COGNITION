# Portability tiers

This document is the product-facing portability policy. A tier is a support
target, not blanket proof that every feature has identical semantics on every
operating system. Runtime capability detection and retained, revision-bound
evidence decide which controls may be advertised for a particular run.

## Tier 1 target

- Linux x86_64 — Ubuntu LTS, glibc, systemd, and cgroup v2 are the primary
  reference environment for AESE.
- Windows x86_64.
- macOS arm64.

The CPU-only path is mandatory on every tier. Accelerator backends are optional
capability adapters and must not prevent provider-backed or CPU-only workflows from
starting.

## Tier 2 target

- Linux arm64
- Windows arm64
- macOS x86_64 while user demand justifies the maintenance cost

The current resource probe reports conservative portable signals. It does not yet
promise exact physical-core, NUMA, battery, thermal, GPU, or OS quota detection on
every platform.

## Current evidence boundary

| Platform | Product meaning | Current evidence | What must not be claimed |
|---|---|---|---|
| Ubuntu LTS x86_64, glibc, cgroup v2 | Primary Linux deployment target | `IMPLEMENTED / PARTIALLY LIVE VERIFIED` on one GCP Ubuntu 24.04 host with kernel `7.0.0-1011-gcp`; both the cgroup primitive and the Rust/Lab native-process seam were observed | Compatibility with every Linux distribution, kernel, container, or the current dirty workspace |
| Windows x86_64 | Tier-1 CPU/runtime target; Job Object is an opt-in hard-control adapter when the capability probe passes | `IMPLEMENTED / PARTIALLY LIVE VERIFIED`: assignment, containment, active-process limit, termination, and deadline cancellation were observed; allocation-pressure kill is tracked separately | Complete equivalence with Linux or universal memory-pressure enforcement |
| macOS arm64 | Tier-1 CPU/runtime target; resource controls remain cooperative/measurement-only | `NOT VERIFIED` for kernel-equivalent resource enforcement; the adapter explicitly does not make that claim | Hard memory/CPU/process isolation or Linux/Windows-equivalent enforcement |

The raw Linux host evidence is retained in the local-only evidence archive and
is intentionally not tracked. The tracked `evidence/current.json` file is the
schema/template; it is not a host-specific proof artifact.
The authoritative unresolved scope is maintained in
[`NOT_VERIFIED_REGISTRY.md`](NOT_VERIFIED_REGISTRY.md), especially NV-001 through
NV-003.

## Product and runtime rules

1. At startup, probe capabilities (architecture, libc, cgroup mode, permissions,
   toolchain, and platform controls); do not infer enforcement from the OS name
   alone.
2. Report each control as `HARD_ENFORCED`, `COOPERATIVE`,
   `MEASUREMENT_ONLY`, or `UNAVAILABLE`.
3. If a task requires hard isolation and the platform cannot prove it, block or
   route it to an explicitly consented supported lane. Never silently downgrade
   it and return `PASS`.
4. Portable build/test success proves portability of that tested path only. It
   does not prove equivalent kernel resource enforcement.
5. Every promoted platform claim must be bound to the exact source revision,
   toolchain, runner/host, policy, and retained artifact. A later code or
   configuration change invalidates the affected evidence.

For the first product release, use Ubuntu LTS x86_64 with cgroup v2 as the
reference Linux environment, keep Windows as a separately evidenced Tier-1
lane, and expose macOS resource control as cooperative/measurement-only until a
macOS host produces the required evidence.
