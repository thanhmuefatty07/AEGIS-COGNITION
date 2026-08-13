use aegis_search_sdk::{Pipeline, SearchOperation, SearchQuery};

#[tokio::main]
async fn main() -> Result<(), aegis_search_sdk::SearchError> {
    let results = Pipeline::new()
        .then(SearchOperation::lexical(
            SearchQuery::new("evidence").with_source("local evidence"),
        ))
        .then(SearchOperation::Aggregate {
            group_by: "source".into(),
        })
        .execute()
        .await?;
    println!("{}", results.candidates.len());
    Ok(())
}
