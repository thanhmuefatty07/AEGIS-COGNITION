<!-- GENERATED FILE: scripts/document_consistency_gate.py; do not edit. -->
# AEGIS Lab blocker status (generated)

This view is generated from `not_verified_registry.json` and
`deployment_policy.json`. It is a human-readable view only; the
machine-readable registry remains authoritative.

| ID | Title | Registry status | Blocks production | Policy classification |
|---|---|---|---|---|
| NV-001 | Linux cgroup v2 enforcement | IMPLEMENTED / PARTIALLY LIVE VERIFIED | NO | non_blocking_registry_ids |
| NV-002 | Windows Job Object live process enforcement | IMPLEMENTED / PARTIALLY LIVE VERIFIED | NO | non_blocking_registry_ids |
| NV-003 | macOS resource enforcement | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-004 | External signed release attestation | NOT VERIFIED | YES | external_signed_attestation_missing |
| NV-005 | Hardware policy freeze for H0-H2 | MEASURED LOCAL ONLY / NOT VERIFIED FOR H0-H2 | NO | non_blocking_registry_ids |
| NV-006 | OpenTelemetry exporter | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-007 | Fuzz campaign evidence | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-008 | Tier-1 wheel parity | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-009 | Restore from external release backup | PROVEN LOCAL PACKAGE DRILL / RESTORE NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-010 | Branch protection and hosted CI observation | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-011 | GT96 requirement closure | IN PROGRESS | NO | non_blocking_registry_ids |
| NV-012 | Production migration rehearsal | IMPLEMENTED / NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-013 | Miri and AddressSanitizer evidence | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-014 | Communication exporter | IMPLEMENTED / MEASURED LOCAL ONLY | NO | non_blocking_registry_ids |
| NV-015 | GitHub hosted-runner evidence for current head | NOT VERIFIED | NO | non_blocking_registry_ids |
| NV-016 | Real multi-machine TCP cluster soak | NOT VERIFIED | YES | real_multi_machine_cluster_soak_missing |
| NV-017 | Full QuickJS interpreter cold-start | NOT VERIFIED | YES | full_quickjs_interpreter_cold_start_missing |
| NV-018 | Live provider HTTP 429 soak | NOT VERIFIED | YES | live_provider_429_soak_missing |
| NV-019 | External deployment smoke | NOT VERIFIED | YES | external_deployment_smoke_missing |
| NV-020 | Trust-policy owner and cross-cell binding | LOCAL LAB BINDING PROVEN / GLOBAL NOT VERIFIED | NO | non_blocking_registry_ids |
