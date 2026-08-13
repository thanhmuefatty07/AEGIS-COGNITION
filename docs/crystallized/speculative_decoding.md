# Speculative Decoding

## origin
Speculative decoding accelerates inference by drafting candidate tokens and verifying them against a target path.

## proof
If verification accepts only valid prefixes, throughput can improve while preserving correctness of accepted output.

## implementation
- `core/rust/src/speculative/draft.rs`
- `core/rust/src/speculative/verifier.rs`
- `core/rust/src/speculative/orchestrator.rs`
- deterministic longest-common-prefix verifier keyed by request id
- accepted output is clipped to the verified target prefix

## risks
- overly permissive acceptance
- verifier drift
- fallback inefficiency
- incorrect prefix acceptance

## tests
- draft generation shape
- verifier rejection path
- fallback path
- prefix acceptance behavior
