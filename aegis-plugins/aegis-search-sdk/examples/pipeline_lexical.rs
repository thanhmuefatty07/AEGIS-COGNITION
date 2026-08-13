use aegis_search_sdk::{Pipeline, SearchOperation, SearchQuery};

#[tokio::main]
async fn main() -> Result<(), aegis_search_sdk::SearchError> {
    let results = Pipeline::new()
        .then(SearchOperation::lexical(
            SearchQuery::new("browser evidence").with_source("browser evidence packet"),
        ))
        .then(SearchOperation::rerank("evidence", "hybrid"))
        .execute()
        .await?;
    println!("{}", results.candidates.len());
    Ok(())
}
