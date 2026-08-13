# Search SDK Skill

## Overview
Use atomic search primitives as composable steps. Search returns candidates, not
facts. Verify important claims with evidence bindings or source artifacts.

## When to Use
Use for task-specific retrieval, reranking, filtering, dedupe, and aggregation.

## Core Concepts
Build small pipelines: lexical fetch first, rerank second, filter and dedupe
last. Keep candidate IDs stable.

## Examples
```rust
Pipeline::new()
    .then(SearchOperation::lexical(SearchQuery::new("policy evidence")))
    .then(SearchOperation::rerank("policy", "hybrid"));
```

## Best Practices
Prefer bounded `top_k`, explicit filters, and artifact-bound evidence search.

## Common Pitfalls
Do not treat retrieved text as final truth without verification.
