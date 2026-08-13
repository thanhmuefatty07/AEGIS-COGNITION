use aegis_nerve::evidence_index::{CandidateEvidenceRef, HotEvidenceIndex, SortedEvidenceSet};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use thiserror::Error;
use url::Url;

#[derive(Debug, Error)]
pub enum SearchError {
    #[error("invalid query")]
    InvalidQuery,
    #[error("invalid url")]
    InvalidUrl,
    #[error("http request failed: {0}")]
    Http(String),
    #[error("pipeline step failed: {0}")]
    Pipeline(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SearchQuery {
    pub text: String,
    pub sources: Vec<String>,
    pub filters: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SearchCandidate {
    pub id: String,
    pub title: String,
    pub url: Option<String>,
    pub body: String,
    pub score_ppm: u32,
    pub metadata: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SearchResults {
    pub candidates: Vec<SearchCandidate>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ExtractMode {
    Text,
    Html,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum RerankStrategy {
    Lexical,
    Semantic,
    Hybrid,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum SearchOperation {
    Lexical {
        query: SearchQuery,
    },
    Semantic {
        query: String,
        embedding_model: String,
        top_k: usize,
    },
    Structured {
        query: String,
        schema: String,
    },
    FetchUrl {
        url: String,
        extract_mode: ExtractMode,
    },
    Rerank {
        query: String,
        strategy: RerankStrategy,
    },
    FilterContains {
        field: String,
        needle: String,
    },
    Dedupe {
        key: DedupeKey,
    },
    Aggregate {
        group_by: String,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum DedupeKey {
    Id,
    Url,
    Title,
}

#[derive(Clone, Debug, Default)]
pub struct Pipeline {
    operations: Vec<SearchOperation>,
}

#[derive(Clone)]
pub struct SearchClient {
    http: reqwest::Client,
}

impl SearchQuery {
    pub fn new(text: impl Into<String>) -> Self {
        Self {
            text: text.into(),
            sources: Vec::new(),
            filters: BTreeMap::new(),
        }
    }

    pub fn with_source(mut self, source: impl Into<String>) -> Self {
        self.sources.push(source.into());
        self
    }

    pub fn with_filter(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.filters.insert(key.into(), value.into());
        self
    }

    fn normalized_terms(&self) -> Vec<String> {
        normalized_terms(&self.text)
    }
}

impl SearchCandidate {
    pub fn new(
        id: impl Into<String>,
        title: impl Into<String>,
        body: impl Into<String>,
        score_ppm: u32,
    ) -> Self {
        Self {
            id: id.into(),
            title: title.into(),
            url: None,
            body: body.into(),
            score_ppm: score_ppm.min(1_000_000),
            metadata: BTreeMap::new(),
        }
    }

    pub fn with_url(mut self, url: impl Into<String>) -> Self {
        self.url = Some(url.into());
        self
    }

    pub fn with_metadata(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.metadata.insert(key.into(), value.into());
        self
    }

    fn searchable_text(&self) -> String {
        format!("{} {}", self.title, self.body).to_ascii_lowercase()
    }
}

impl SearchResults {
    pub fn empty() -> Self {
        Self {
            candidates: Vec::new(),
        }
    }

    pub fn from_candidates(candidates: Vec<SearchCandidate>) -> Self {
        Self { candidates }
    }
}

impl SearchOperation {
    pub fn lexical(query: SearchQuery) -> Self {
        Self::Lexical { query }
    }

    pub fn rerank(query: impl Into<String>, strategy: impl Into<RerankStrategy>) -> Self {
        Self::Rerank {
            query: query.into(),
            strategy: strategy.into(),
        }
    }
}

impl From<&str> for RerankStrategy {
    fn from(value: &str) -> Self {
        match value {
            "semantic" => Self::Semantic,
            "hybrid" => Self::Hybrid,
            _ => Self::Lexical,
        }
    }
}

impl Pipeline {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn then(mut self, operation: SearchOperation) -> Self {
        self.operations.push(operation);
        self
    }

    pub fn operations(&self) -> &[SearchOperation] {
        &self.operations
    }

    pub async fn execute(&self) -> Result<SearchResults, SearchError> {
        self.execute_with_client(&SearchClient::new()).await
    }

    pub async fn execute_with_client(
        &self,
        client: &SearchClient,
    ) -> Result<SearchResults, SearchError> {
        let mut results = SearchResults::empty();
        for operation in &self.operations {
            results = client.apply(operation, results).await?;
        }
        Ok(results)
    }
}

impl Default for SearchClient {
    fn default() -> Self {
        Self::new()
    }
}

impl SearchClient {
    pub fn new() -> Self {
        Self {
            http: reqwest::Client::builder()
                .pool_max_idle_per_host(32)
                .build()
                .expect("reqwest client config is static"),
        }
    }

    pub async fn search_lexical(&self, query: &SearchQuery) -> Result<SearchResults, SearchError> {
        if query.text.trim().is_empty() {
            return Err(SearchError::InvalidQuery);
        }
        let terms = query.normalized_terms();
        let mut candidates = Vec::new();
        for (index, source) in query.sources.iter().enumerate() {
            let text = format!("{source} {}", query.text);
            let score = lexical_score(&text, &terms);
            candidates.push(
                SearchCandidate::new(format!("lexical-{index}"), source, text, score.max(1))
                    .with_metadata("source", source),
            );
        }
        if candidates.is_empty() {
            candidates.push(SearchCandidate::new(
                "lexical-query",
                query.text.clone(),
                query.text.clone(),
                1_000_000,
            ));
        }
        Ok(SearchResults::from_candidates(candidates))
    }

    pub async fn fetch_url(
        &self,
        url: &str,
        extract_mode: ExtractMode,
    ) -> Result<SearchCandidate, SearchError> {
        let parsed = Url::parse(url).map_err(|_| SearchError::InvalidUrl)?;
        let response = self
            .http
            .get(parsed)
            .send()
            .await
            .map_err(|error| SearchError::Http(error.to_string()))?;
        let body = response
            .text()
            .await
            .map_err(|error| SearchError::Http(error.to_string()))?;
        let extracted = match extract_mode {
            ExtractMode::Text => strip_html_tags(&body),
            ExtractMode::Html => body,
        };
        Ok(SearchCandidate::new(url, url, extracted, 1_000_000).with_url(url))
    }

    pub fn rerank(
        &self,
        candidates: &[SearchCandidate],
        query: &str,
        strategy: RerankStrategy,
    ) -> SearchResults {
        let terms = normalized_terms(query);
        let mut reranked = candidates.to_vec();
        reranked.iter_mut().for_each(|candidate| {
            let lexical = lexical_score(&candidate.searchable_text(), &terms);
            candidate.score_ppm = match strategy {
                RerankStrategy::Lexical => lexical,
                RerankStrategy::Semantic => semantic_proxy_score(candidate, &terms),
                RerankStrategy::Hybrid => {
                    let semantic = semantic_proxy_score(candidate, &terms);
                    ((lexical as u64 + semantic as u64) / 2) as u32
                }
            };
        });
        reranked.sort_by(|left, right| {
            right
                .score_ppm
                .cmp(&left.score_ppm)
                .then_with(|| left.id.cmp(&right.id))
        });
        SearchResults::from_candidates(reranked)
    }

    pub fn dedupe(&self, candidates: &[SearchCandidate], key: DedupeKey) -> SearchResults {
        let mut seen = BTreeSet::new();
        let mut out = Vec::new();
        for candidate in candidates {
            let value = match key {
                DedupeKey::Id => candidate.id.clone(),
                DedupeKey::Url => candidate
                    .url
                    .clone()
                    .unwrap_or_else(|| candidate.id.clone()),
                DedupeKey::Title => candidate.title.clone(),
            };
            if seen.insert(value) {
                out.push(candidate.clone());
            }
        }
        SearchResults::from_candidates(out)
    }

    pub fn filter_contains(
        &self,
        candidates: &[SearchCandidate],
        field: &str,
        needle: &str,
    ) -> SearchResults {
        let needle = needle.to_ascii_lowercase();
        SearchResults::from_candidates(
            candidates
                .iter()
                .filter(|candidate| {
                    let haystack = match field {
                        "title" => candidate.title.as_str(),
                        "url" => candidate.url.as_deref().unwrap_or_default(),
                        _ => candidate.body.as_str(),
                    };
                    haystack.to_ascii_lowercase().contains(&needle)
                })
                .cloned()
                .collect(),
        )
    }

    pub fn aggregate_count(
        &self,
        candidates: &[SearchCandidate],
        group_by: &str,
    ) -> BTreeMap<String, usize> {
        let mut counts = BTreeMap::new();
        for candidate in candidates {
            let key = candidate
                .metadata
                .get(group_by)
                .cloned()
                .unwrap_or_else(|| "unknown".to_string());
            *counts.entry(key).or_insert(0) += 1;
        }
        counts
    }

    async fn apply(
        &self,
        operation: &SearchOperation,
        input: SearchResults,
    ) -> Result<SearchResults, SearchError> {
        match operation {
            SearchOperation::Lexical { query } => self.search_lexical(query).await,
            SearchOperation::Semantic {
                query,
                embedding_model,
                top_k,
            } => {
                let mut results = self.rerank(&input.candidates, query, RerankStrategy::Semantic);
                results.candidates.truncate(*top_k);
                for candidate in &mut results.candidates {
                    candidate
                        .metadata
                        .insert("embedding_model".to_string(), embedding_model.clone());
                }
                Ok(results)
            }
            SearchOperation::Structured { query, schema } => {
                let mut results = self.rerank(&input.candidates, query, RerankStrategy::Hybrid);
                for candidate in &mut results.candidates {
                    candidate
                        .metadata
                        .insert("schema".to_string(), schema.clone());
                }
                Ok(results)
            }
            SearchOperation::FetchUrl { url, extract_mode } => {
                let mut candidates = input.candidates;
                candidates.push(self.fetch_url(url, extract_mode.clone()).await?);
                Ok(SearchResults::from_candidates(candidates))
            }
            SearchOperation::Rerank { query, strategy } => {
                Ok(self.rerank(&input.candidates, query, strategy.clone()))
            }
            SearchOperation::FilterContains { field, needle } => {
                Ok(self.filter_contains(&input.candidates, field, needle))
            }
            SearchOperation::Dedupe { key } => Ok(self.dedupe(&input.candidates, *key)),
            SearchOperation::Aggregate { group_by } => {
                let counts = self.aggregate_count(&input.candidates, group_by);
                Ok(SearchResults::from_candidates(
                    counts
                        .into_iter()
                        .map(|(key, count)| {
                            SearchCandidate::new(key.clone(), key, count.to_string(), 1_000_000)
                        })
                        .collect(),
                ))
            }
        }
    }
}

pub struct EvidenceSearch<'a> {
    index: &'a HotEvidenceIndex,
}

impl<'a> EvidenceSearch<'a> {
    pub fn new(index: &'a HotEvidenceIndex) -> Self {
        Self { index }
    }

    pub fn search_evidence(
        &self,
        artifact_segments: Option<&SortedEvidenceSet>,
        patterns: &[&str],
        limit: usize,
    ) -> Vec<CandidateEvidenceRef> {
        self.index
            .query_exact_literals(patterns, artifact_segments, limit)
    }

    pub fn verify_binding(
        &self,
        candidate: &CandidateEvidenceRef,
        evidence_ref_hash: [u8; 32],
        artifact_hash: [u8; 32],
        ast_signature_hash: [u8; 32],
    ) -> bool {
        self.index.validates_artifact_binding(
            candidate,
            evidence_ref_hash,
            artifact_hash,
            ast_signature_hash,
        )
    }
}

#[cfg(feature = "python")]
mod python_bindings {
    use super::*;
    use pyo3::prelude::*;

    #[pyclass(name = "SearchQuery")]
    struct PySearchQuery {
        inner: SearchQuery,
    }

    #[pymethods]
    impl PySearchQuery {
        #[new]
        fn new(text: String) -> Self {
            Self {
                inner: SearchQuery::new(text),
            }
        }

        fn with_source(&mut self, source: String) {
            self.inner.sources.push(source);
        }
    }

    #[pyfunction]
    fn lexical_sync(query: &PySearchQuery) -> PyResult<String> {
        let client = SearchClient::new();
        let runtime = tokio::runtime::Runtime::new()
            .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
        let results = runtime
            .block_on(client.search_lexical(&query.inner))
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        serde_json::to_string(&results)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    }

    #[pymodule]
    fn aegis_search_sdk(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add_class::<PySearchQuery>()?;
        module.add_function(wrap_pyfunction!(lexical_sync, module)?)?;
        Ok(())
    }
}

fn normalized_terms(text: &str) -> Vec<String> {
    text.split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|term| !term.is_empty())
        .map(str::to_ascii_lowercase)
        .collect()
}

fn lexical_score(text: &str, terms: &[String]) -> u32 {
    if terms.is_empty() {
        return 0;
    }
    let text = text.to_ascii_lowercase();
    let matches = terms
        .iter()
        .filter(|term| text.contains(term.as_str()))
        .count() as u32;
    ((matches as u64 * 1_000_000) / terms.len() as u64) as u32
}

fn semantic_proxy_score(candidate: &SearchCandidate, terms: &[String]) -> u32 {
    let text = candidate.searchable_text();
    let unique = terms
        .iter()
        .filter(|term| {
            text.split_whitespace()
                .any(|word| word.starts_with(term.as_str()))
        })
        .count() as u32;
    ((unique as u64 * 900_000) / terms.len().max(1) as u64) as u32
}

fn strip_html_tags(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut in_tag = false;
    for ch in html.chars() {
        match ch {
            '<' => in_tag = true,
            '>' => in_tag = false,
            _ if !in_tag => out.push(ch),
            _ => {}
        }
    }
    out
}

pub fn candidate_hash(candidate: &SearchCandidate) -> [u8; 32] {
    let payload = serde_json::to_vec(candidate).unwrap_or_default();
    *blake3::hash(&payload).as_bytes()
}

pub type SharedSearchClient = Arc<SearchClient>;

#[cfg(test)]
mod tests {
    use super::*;
    use aegis_nerve::evidence_index::EvidenceCandidateTier;

    fn hash(label: &str) -> [u8; 32] {
        *blake3::hash(label.as_bytes()).as_bytes()
    }

    #[tokio::test]
    async fn lexical_pipeline_reranks_and_dedupes() {
        let pipeline = Pipeline::new()
            .then(SearchOperation::lexical(
                SearchQuery::new("evidence policy")
                    .with_source("policy evidence artifact")
                    .with_source("unrelated"),
            ))
            .then(SearchOperation::rerank("policy", "hybrid"))
            .then(SearchOperation::Dedupe {
                key: DedupeKey::Title,
            });

        let results = pipeline.execute().await.unwrap();
        assert_eq!(results.candidates.len(), 2);
        assert!(results.candidates[0].score_ppm >= results.candidates[1].score_ppm);
    }

    #[test]
    fn hot_evidence_index_is_exposed_as_search_primitive() {
        let mut index = HotEvidenceIndex::new(hash("epoch")).unwrap();
        let evidence_ref_hash = hash("evidence-ref");
        let artifact_hash = hash("artifact");
        let ast_signature_hash = hash("ast");
        index
            .insert_artifact_document(
                evidence_ref_hash,
                7,
                artifact_hash,
                ast_signature_hash,
                "policy gate evidence replay",
            )
            .unwrap();

        let evidence = EvidenceSearch::new(&index);
        let candidates = evidence.search_evidence(None, &["policy", "replay"], 4);
        assert_eq!(candidates.len(), 1);
        assert_eq!(
            candidates[0].origin_tier,
            EvidenceCandidateTier::ExactSimdRerank
        );
        assert!(evidence.verify_binding(
            &candidates[0],
            evidence_ref_hash,
            artifact_hash,
            ast_signature_hash
        ));
    }
}
