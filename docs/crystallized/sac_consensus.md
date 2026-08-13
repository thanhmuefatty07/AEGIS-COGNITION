# SAC Physical Witness

## origin
SAC Physical Witness is the Byzantine-resilient trust filter used to guard AEGIS decisions against inconsistent or malicious inputs.

## proof
A local validation and filtering layer reduces the chance that a single poisoned input can dominate the system state.

## implementation
- `core/rust/src/sac.rs`
- anchor state
- vote acceptance rule
- physical witness votes keyed by canonical artifact hash and AST/physical fingerprint
- F+1 Byzantine quorum over matching physical evidence
- legacy score votes retained only as compatibility helpers

## risks
- false acceptance
- false rejection
- stale Criterion artifacts masking performance regressions
- adversarial input shaping

## tests
- accept on valid quorum score
- reject below threshold
- adversarial vote rejection
- physical hash quorum commit
- physical divergence rejection
