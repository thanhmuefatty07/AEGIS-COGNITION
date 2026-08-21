# Standards applicability and verification gates

This matrix records how the architecture plan applies the referenced
standards and engineering frameworks. It is an implementation aid, not a
certification or a claim of compliance with the full text of any standard.

| Standard/framework | Application in AEGIS | Repository evidence | Verification state |
|---|---|---|---|
| ISO/IEC 25010:2023 | Map quality attributes to the release scorecard and measurable gates. | `docs/architecture/TRACEABILITY.md`, benchmark protocol, CI gates | PROVEN mapping; release score values remain evidence-dependent |
| ISO/IEC/IEEE 42010:2022 | Record architecture concerns, decisions, trust boundaries, interfaces, and failure boundaries. | `docs/architecture/README.md`, `docs/adr/` | PROVEN |
| ISO/IEC/IEEE 12207:2026 | Keep build, test, release, operate, maintain, rollback, and evidence workflows explicit. | `.github/workflows/`, `docs/architecture/BASELINE.md`, ADR evidence sections | PROVEN workflow definition; production operation evidence is NOT VERIFIED |
| ISO/IEC/IEEE 29148:2018 | Express requirements as traceable acceptance criteria with assumptions and invariants. | `docs/architecture/TRACEABILITY.md`, versioned JSON schemas, Rust tests | PROVEN |
| ISO/IEC/IEEE 29119-1/2/3 | Separate fast, compatibility, deep, and release test processes and retain reports. | `.github/workflows/`, `docs/architecture/TESTING_AND_EVIDENCE.md` | PROVEN process; deep/release runs are NOT VERIFIED until executed |
| NIST SP 800-218 SSDF 1.1 | Apply secure design, dependency, secret, review, provenance, and vulnerability gates. | `scripts/dependency_audit_gate.py`, `scripts/supply_chain_gate.py`, `scripts/secret_scan.py` | PROVEN internal gates; external signed release attestation is NOT VERIFIED |
| OWASP ASVS 5.0.0 | Use as the future API/control-plane security checklist; current scope is local runtime and operator evidence. | `docs/architecture/SECURITY_AND_OPERATIONS.md` | APPLICABLE; web/API coverage is NOT VERIFIED |
| OWASP Top 10:2025 | Use for threat awareness when adding networked control-plane surfaces. | `docs/architecture/SECURITY_AND_OPERATIONS.md` | APPLICABLE; not a coverage claim |
| OWASP WSTG 4.2 | Define future release/pentest cases for web-facing surfaces. | `docs/architecture/SECURITY_AND_OPERATIONS.md` | APPLICABLE; NOT VERIFIED |
| SLSA 1.2 | Bind source, lockfiles, SBOM indices, build provenance, and external attestation at release. | `scripts/supply_chain_gate.py`, release workflow policy | Internal hashes PROVEN; external signed attestation NOT VERIFIED |
| OpenSSF OSPS Baseline 2026-02-19 | Track repository protections, review, dependency pinning, and workflow hardening. | `docs/architecture/SECURITY_AND_OPERATIONS.md`, workflow action pins | Partially PROVEN; GitHub branch/ruleset state NOT VERIFIED |
| OpenTelemetry specification | Preserve correlation IDs and expose bounded runtime/admission/lane/sandbox signals without making telemetry authoritative. | `core/rust/src/telemetry.rs`, `docs/adr/ADR-011-telemetry.md` | Facade PROVEN; OTel exporter and full semantic convention coverage NOT VERIFIED |
| SRE practices | Define SLI/SLO/error-budget work only after representative H0–H2 workload baselines exist. | `docs/architecture/SECURITY_AND_OPERATIONS.md`, benchmark protocol | Policy documented; SLO baseline NOT VERIFIED |

## Evidence labels

- `PROVEN`: repository code, deterministic test, or static gate demonstrates the
  stated property.
- `MEASURED`: raw benchmark output is retained and tied to the stated host and
  workload.
- `SOURCE-BACKED`: the plan cites an authoritative upstream source; it is not
  runtime evidence.
- `ASSUMED`: an explicit default is in use but has not been frozen by data.
- `NOT VERIFIED`: the required external, privileged, cross-platform, or release
  evidence has not been executed and retained.
