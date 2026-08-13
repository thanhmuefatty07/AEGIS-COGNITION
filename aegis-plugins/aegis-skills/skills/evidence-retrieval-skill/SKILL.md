# Evidence Retrieval Skill

## Overview
Use `HotEvidenceIndex` for artifact-bound exact evidence candidates.

## When to Use
Use when replay, memory, or context needs fast local evidence retrieval.

## Core Concepts
Candidates include binding hashes and index epochs. They require later
verification before fact or memory commitment.

## Examples
```rust
EvidenceSearch::new(&index).search_evidence(None, &["policy"], 8);
```

## Best Practices
Verify `evidence_ref_hash`, `artifact_hash`, and `ast_signature_hash`.

## Common Pitfalls
Do not commit candidate refs as facts.
