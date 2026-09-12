# /aegis-plugin-create

Create or update an AEGIS NERVE-HARNESS plugin capsule under the zero-trust protocol.

## Steps

1. Read `docs/archive/reports/system-prompt.md` and the current Rust core contracts.
2. Classify the requested plugin as R0-R4.
3. Declare least-privilege host capabilities and side effects.
4. Define typed input/output structs and the `TypedToolIR` binding.
5. Implement sandbox-safe executor logic with fail-closed traps.
6. Define physical evidence, PAV verification, and sample witness proof.
7. Add unit, property, policy, replay, TOCTOU, and benchmark gates.
8. Run focused checks and report residual risks.

## Required Output

- risk and capability matrix
- plugin file tree
- implementation summary
- evidence contract
- test and benchmark report
- sample `manifest.toml`
- sample `WitnessProof`
