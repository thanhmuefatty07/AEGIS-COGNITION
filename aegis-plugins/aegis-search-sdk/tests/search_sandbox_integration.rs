use aegis_sandbox::{
    ExecutionRequest, PolicyOnlyBackend, SandboxPolicy, SandboxRuntime, SandboxStateStore,
};
use aegis_search_sdk::{Pipeline, SearchOperation, SearchQuery};
use std::collections::BTreeMap;

#[tokio::test]
async fn search_results_can_be_persisted_for_sandbox_turns() {
    let results = Pipeline::new()
        .then(SearchOperation::lexical(
            SearchQuery::new("evidence replay").with_source("artifact evidence replay"),
        ))
        .execute()
        .await
        .unwrap();

    let dir = tempfile::tempdir().unwrap();
    let store = SandboxStateStore::new(dir.path()).unwrap();
    store.save_state("search_candidates", &results).unwrap();
    let loaded: aegis_search_sdk::SearchResults = store.load_state("search_candidates").unwrap();
    assert_eq!(loaded.candidates.len(), 1);

    let runtime = SandboxRuntime::new(SandboxPolicy::default(), store, PolicyOnlyBackend);
    let report = runtime
        .execute(ExecutionRequest {
            code: "import json\nresult = {'candidate_count': 1}".to_string(),
            entrypoint: None,
        })
        .unwrap();
    assert!(report.is_valid());

    let states: BTreeMap<&str, usize> =
        BTreeMap::from([("candidate_count", loaded.candidates.len())]);
    runtime.state().save_state("summary", &states).unwrap();
}
