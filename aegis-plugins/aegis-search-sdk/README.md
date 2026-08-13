# aegis-search-sdk

Atomic search primitives for AEGIS agents.

## Examples

```rust
use aegis_search_sdk::{Pipeline, SearchOperation, SearchQuery};

# async fn run() -> Result<(), aegis_search_sdk::SearchError> {
let pipeline = Pipeline::new()
    .then(SearchOperation::lexical(SearchQuery::new("policy evidence")))
    .then(SearchOperation::rerank("hybrid"));
let results = pipeline.execute().await?;
# Ok(())
# }
```

See `examples/` for five task-oriented pipelines.
