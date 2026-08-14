use aegis_search_sdk::{Pipeline, SearchOperation, SearchQuery};
use criterion::{Criterion, black_box, criterion_group, criterion_main};

fn single_lexical_operation(c: &mut Criterion) {
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let pipeline = Pipeline::new().then(SearchOperation::lexical(
        SearchQuery::new("policy evidence").with_source("policy evidence artifact"),
    ));

    c.bench_function("aegis_search_sdk_single_lexical", |b| {
        b.iter(|| {
            let results = runtime.block_on(pipeline.execute()).unwrap();
            black_box(results.candidates.len())
        })
    });
}

criterion_group!(benches, single_lexical_operation);
criterion_main!(benches);
