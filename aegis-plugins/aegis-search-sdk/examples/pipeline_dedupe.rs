use aegis_search_sdk::{DedupeKey, Pipeline, SearchOperation, SearchQuery};

#[tokio::main]
async fn main() -> Result<(), aegis_search_sdk::SearchError> {
    let results = Pipeline::new()
        .then(SearchOperation::lexical(
            SearchQuery::new("policy")
                .with_source("policy")
                .with_source("policy"),
        ))
        .then(SearchOperation::Dedupe {
            key: DedupeKey::Title,
        })
        .execute()
        .await?;
    println!("{}", results.candidates.len());
    Ok(())
}
