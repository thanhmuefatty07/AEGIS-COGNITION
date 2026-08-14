use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct CacheRecord {
    pub prompt: String,
    pub response: String,
    pub embedding: Vec<f32>,
    pub namespace: String, // String representation of evidence_binding_hash (hex)
    pub timestamp_ms: u64,
}

#[pyclass(skip_from_py_object)]
#[derive(Serialize, Deserialize, Default, Clone, Debug)]
pub struct SemanticCache {
    records: Vec<CacheRecord>,
    #[pyo3(get, set)]
    pub threshold: f32,
    pub file_path: Option<String>,
}

fn cosine_similarity(v1: &[f32], v2: &[f32]) -> f32 {
    if v1.len() != v2.len() || v1.is_empty() {
        return 0.0;
    }
    let mut dot_product = 0.0;
    let mut norm_a = 0.0;
    let mut norm_b = 0.0;
    for i in 0..v1.len() {
        dot_product += v1[i] * v2[i];
        norm_a += v1[i] * v1[i];
        norm_b += v2[i] * v2[i];
    }
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    dot_product / (norm_a.sqrt() * norm_b.sqrt())
}

#[pymethods]
impl SemanticCache {
    #[new]
    #[pyo3(signature = (threshold = 0.95, file_path = None))]
    pub fn new(threshold: f32, file_path: Option<String>) -> Self {
        let mut cache = Self {
            records: Vec::new(),
            threshold,
            file_path: file_path.clone(),
        };
        if let Some(ref path) = file_path
            && Path::new(path).exists()
        {
            let _ = cache.load_from_file(path);
        }
        cache
    }

    pub fn lookup(&self, prompt: &str, embedding: Vec<f32>, namespace: &str) -> Option<String> {
        // 1. Exact Match check first
        for record in &self.records {
            if record.namespace == namespace && record.prompt.trim() == prompt.trim() {
                return Some(record.response.clone());
            }
        }

        // 2. Semantic Similarity match
        let mut best_score = -1.0;
        let mut best_response = None;

        for record in &self.records {
            if record.namespace == namespace {
                let sim = cosine_similarity(&record.embedding, &embedding);
                if sim > best_score && sim >= self.threshold {
                    best_score = sim;
                    best_response = Some(record.response.clone());
                }
            }
        }

        best_response
    }

    pub fn insert(
        &mut self,
        prompt: String,
        response: String,
        embedding: Vec<f32>,
        namespace: String,
    ) {
        // Remove existing exact prompt under the same namespace to prevent duplicates
        self.records
            .retain(|r| !(r.namespace == namespace && r.prompt == prompt));

        let timestamp_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        self.records.push(CacheRecord {
            prompt,
            response,
            embedding,
            namespace,
            timestamp_ms,
        });

        if let Some(ref path) = self.file_path {
            let _ = self.save_to_file(path);
        }
    }

    pub fn invalidate(&mut self, namespace: &str) {
        self.records.retain(|r| r.namespace != namespace);
        if let Some(ref path) = self.file_path {
            let _ = self.save_to_file(path);
        }
    }

    pub fn size(&self) -> usize {
        self.records.len()
    }

    pub fn save_to_file(&self, path: &str) -> PyResult<()> {
        let serialized = serde_json::to_string_pretty(self).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Serialization failed: {}", e))
        })?;
        let mut file = File::create(path).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to create file: {}", e))
        })?;
        file.write_all(serialized.as_bytes()).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to write file: {}", e))
        })?;
        Ok(())
    }

    pub fn load_from_file(&mut self, path: &str) -> PyResult<()> {
        let mut file = File::open(path).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to open file: {}", e))
        })?;
        let mut contents = String::new();
        file.read_to_string(&mut contents).map_err(|e| {
            pyo3::exceptions::PyIOError::new_err(format!("Failed to read file: {}", e))
        })?;
        let loaded: SemanticCache = serde_json::from_str(&contents).map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("Deserialization failed: {}", e))
        })?;
        self.records = loaded.records;
        self.threshold = loaded.threshold;
        Ok(())
    }
}

#[pymodule]
fn semantic_cache_poc(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SemanticCache>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cosine_similarity() {
        let v1 = vec![1.0, 0.0, 0.0];
        let v2 = vec![1.0, 0.0, 0.0];
        assert!((cosine_similarity(&v1, &v2) - 1.0).abs() < 1e-5);

        let v3 = vec![0.0, 1.0, 0.0];
        assert!(cosine_similarity(&v1, &v3).abs() < 1e-5);

        let v4 = vec![0.5, 0.5, 0.0];
        assert!(cosine_similarity(&v1, &v4) > 0.7);
    }

    #[test]
    fn test_cache_lookup_insert() {
        let mut cache = SemanticCache::new(0.9, None);
        let ns = "test-namespace".to_string();

        let vec_cve_search = vec![0.9, 0.1, 0.0];
        let vec_cve_sim = vec![0.88, 0.12, 0.0];
        let vec_diff = vec![0.0, 0.9, 0.1];

        cache.insert(
            "search CVE-2026".to_string(),
            "Found 3 vulnerabilities".to_string(),
            vec_cve_search.clone(),
            ns.clone(),
        );

        // Exact match
        assert_eq!(
            cache.lookup("search CVE-2026", vec_cve_search.clone(), &ns),
            Some("Found 3 vulnerabilities".to_string())
        );

        // Semantic match
        assert_eq!(
            cache.lookup("find CVE-2026", vec_cve_sim.clone(), &ns),
            Some("Found 3 vulnerabilities".to_string())
        );

        // Mismatch below threshold
        assert_eq!(
            cache.lookup("something unrelated", vec_diff.clone(), &ns),
            None
        );
    }

    #[test]
    fn test_cache_invalidation() {
        let mut cache = SemanticCache::new(0.9, None);
        let ns1 = "ns1".to_string();
        let ns2 = "ns2".to_string();

        cache.insert(
            "query".to_string(),
            "val1".to_string(),
            vec![1.0],
            ns1.clone(),
        );
        cache.insert(
            "query".to_string(),
            "val2".to_string(),
            vec![1.0],
            ns2.clone(),
        );

        assert_eq!(cache.size(), 2);

        cache.invalidate(&ns1);
        assert_eq!(cache.size(), 1);
        assert_eq!(cache.lookup("query", vec![1.0], &ns1), None);
        assert_eq!(
            cache.lookup("query", vec![1.0], &ns2),
            Some("val2".to_string())
        );
    }
}
