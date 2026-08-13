use aegis_search_sdk::{Pipeline, SearchOperation, SearchQuery};

#[tokio::main]
async fn main() -> Result<(), aegis_search_sdk::SearchError> {
    let results = Pipeline::new()
        .then(SearchOperation::lexical(
            SearchQuery::new("sandbox state").with_source("sandbox state persistence"),
        ))
        .then(SearchOperation::FilterContains {
            field: "body".into(),
            needle: "state".into(),
        })
        .execute()
        .await?;
    println!("{}", results.candidates.len());
    Ok(())
}
