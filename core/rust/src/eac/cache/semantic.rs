use crate::eac::events::{self, CacheMatchKind, EventBus};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::Read;
use std::path::Path;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct CacheRecord {
    pub prompt: String,
    pub prompt_hash: String, // Hex-encoded BLAKE3 hash of trimmed prompt
    pub response: String,
    pub embedding: Vec<f32>,
    pub namespace: String,
    pub timestamp_ms: u64,
}

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
pub struct SemanticCache {
    pub records: Vec<CacheRecord>,
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

impl SemanticCache {
    pub fn new(threshold: f32, file_path: Option<String>) -> Self {
        let mut cache = Self {
            records: Vec::new(),
            threshold,
            file_path: file_path.clone(),
        };
        if let Some(ref path) = file_path {
            if Path::new(path).exists() {
                if let Err(e) = cache.load_from_file(path) {
                    tracing::error!("Failed to load semantic cache from file {}: {:?}", path, e);
                }
                // Ensure constructor values are preserved
                cache.threshold = threshold;
                cache.file_path = file_path;
            }
        }
        cache
    }

    pub fn lookup(&self, prompt: &str, embedding: Vec<f32>, namespace: &str) -> Option<String> {
        // 1. Exact Match via BLAKE3 Hash check first
        let query_hash = blake3::hash(prompt.trim().as_bytes()).to_hex().to_string();
        for record in &self.records {
            if record.namespace == namespace && record.prompt_hash == query_hash {
                // Production-grade fallback safety check against hash collision (highly unlikely but standard)
                if record.prompt.trim() == prompt.trim() {
                    return Some(record.response.clone());
                }
            }
        }

        // 2. Semantic Similarity match
        let mut best_score: f32 = -1.0;
        let mut best_response = None;

        for record in &self.records {
            if record.namespace == namespace {
                let sim = cosine_similarity(&record.embedding, &embedding);
                if sim >= self.threshold && sim > best_score {
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
        let prompt_trimmed = prompt.trim();
        let prompt_hash = blake3::hash(prompt_trimmed.as_bytes()).to_hex().to_string();

        // Remove existing exact prompt under the same namespace to prevent duplicates
        self.records
            .retain(|r| !(r.namespace == namespace && r.prompt_hash == prompt_hash));

        let timestamp_ms = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;

        self.records.push(CacheRecord {
            prompt,
            prompt_hash,
            response,
            embedding,
            namespace,
            timestamp_ms,
        });

        if let Some(ref path) = self.file_path {
            if let Err(e) = self.save_to_file(path) {
                tracing::error!("Failed to save semantic cache to file {}: {:?}", path, e);
            }
        }
    }

    pub fn invalidate(&mut self, namespace: &str) {
        self.records.retain(|r| r.namespace != namespace);
        if let Some(ref path) = self.file_path {
            if let Err(e) = self.save_to_file(path) {
                tracing::error!("Failed to save semantic cache to file {}: {:?}", path, e);
            }
        }
    }

    pub fn size(&self) -> usize {
        self.records.len()
    }

    // ─── EventBus-integrated methods ─────────────────────────────────────

    /// Lookup with EventBus emission. Emits `CacheLookup` events for both
    /// hits and misses.
    pub fn lookup_with_events(
        &self,
        prompt: &str,
        embedding: Vec<f32>,
        namespace: &str,
        event_bus: &EventBus,
    ) -> Option<String> {
        let query_hash = blake3::hash(prompt.trim().as_bytes()).to_hex().to_string();

        // 1. L1 Exact Match
        for record in &self.records {
            if record.namespace == namespace && record.prompt_hash == query_hash {
                if record.prompt.trim() == prompt.trim() {
                    events::emit_cache_lookup(
                        event_bus,
                        query_hash,
                        namespace.to_string(),
                        true,
                        Some(CacheMatchKind::ExactBlake3),
                        None,
                    );
                    return Some(record.response.clone());
                }
            }
        }

        // 2. L2 Semantic match
        let mut best_score: f32 = -1.0;
        let mut best_response = None;

        for record in &self.records {
            if record.namespace == namespace {
                let sim = cosine_similarity(&record.embedding, &embedding);
                if sim >= self.threshold && sim > best_score {
                    best_score = sim;
                    best_response = Some(record.response.clone());
                }
            }
        }

        let hit = best_response.is_some();
        let match_kind = if hit {
            Some(CacheMatchKind::SemanticCosine)
        } else {
            None
        };
        let sim_score = if hit { Some(best_score) } else { None };

        events::emit_cache_lookup(
            event_bus,
            query_hash,
            namespace.to_string(),
            hit,
            match_kind,
            sim_score,
        );

        best_response
    }

    /// Insert with EventBus emission.
    pub fn insert_with_events(
        &mut self,
        prompt: String,
        response: String,
        embedding: Vec<f32>,
        namespace: String,
        event_bus: &EventBus,
    ) {
        let prompt_hash = blake3::hash(prompt.trim().as_bytes()).to_hex().to_string();
        self.insert(prompt, response, embedding, namespace.clone());
        events::emit_cache_insert(event_bus, prompt_hash, namespace);
    }

    /// Invalidate with EventBus emission.
    pub fn invalidate_with_events(&mut self, namespace: &str, event_bus: &EventBus) {
        let count_before = self
            .records
            .iter()
            .filter(|r| r.namespace == namespace)
            .count();
        self.invalidate(namespace);
        events::emit_cache_invalidate(event_bus, namespace.to_string(), count_before);
    }

    pub fn save_to_file(&self, path: &str) -> std::io::Result<()> {
        let path_ref = Path::new(path);
        if let Some(parent) = path_ref.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let serialized = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        // Atomic write via temporary file to prevent corruption
        let temp_file_name = format!(
            "{}.tmp",
            path_ref
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("cache")
        );
        let temp_path = path_ref.with_file_name(temp_file_name);

        std::fs::write(&temp_path, &serialized)?;
        if let Err(_) = std::fs::rename(&temp_path, path_ref) {
            // Fallback for Windows/permission issues: remove destination if exists, and try renaming again
            let _ = std::fs::remove_file(path_ref);
            if let Err(_) = std::fs::rename(&temp_path, path_ref) {
                // Final fallback: write directly if rename fails
                std::fs::write(path_ref, &serialized)?;
            }
        }
        Ok(())
    }

    pub fn load_from_file(&mut self, path: &str) -> std::io::Result<()> {
        let mut file = File::open(path)?;
        let mut contents = String::new();
        file.read_to_string(&mut contents)?;
        let loaded: SemanticCache = serde_json::from_str(&contents)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        self.records = loaded.records;
        self.threshold = loaded.threshold;
        Ok(())
    }
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

        // Division by zero / empty cases
        assert_eq!(cosine_similarity(&[], &[]), 0.0);
        assert_eq!(cosine_similarity(&[0.0, 0.0], &[0.0, 0.0]), 0.0);
        assert_eq!(cosine_similarity(&[1.0, 2.0], &[1.0]), 0.0);
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

        // L1 Exact match
        assert_eq!(
            cache.lookup("search CVE-2026", vec_cve_search.clone(), &ns),
            Some("Found 3 vulnerabilities".to_string())
        );

        // L2 Semantic match
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
    fn test_cache_exact_match_l1_priority() {
        let mut cache = SemanticCache::new(0.9, None);
        let ns = "ns".to_string();

        // Exact match should trigger even if embedding is completely different
        cache.insert(
            "exact prompt".to_string(),
            "response exact".to_string(),
            vec![1.0, 0.0],
            ns.clone(),
        );

        // Query with a totally different embedding (e.g. orthogonal) but exact prompt text
        let result = cache.lookup("exact prompt", vec![0.0, 1.0], &ns);
        assert_eq!(result, Some("response exact".to_string()));
    }

    #[test]
    fn test_cache_l2_highest_similarity() {
        let mut cache = SemanticCache::new(0.5, None);
        let ns = "ns".to_string();

        cache.insert(
            "prompt1".to_string(),
            "res1".to_string(),
            vec![0.6, 0.8],
            ns.clone(),
        );

        cache.insert(
            "prompt2".to_string(),
            "res2".to_string(),
            vec![0.8, 0.6],
            ns.clone(),
        );

        // Query close to prompt2 (embedding [0.79, 0.61])
        let query_embedding = vec![0.79, 0.61];
        let result = cache.lookup("different prompt", query_embedding, &ns);
        assert_eq!(result, Some("res2".to_string()));
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

    #[test]
    fn test_file_load_save() {
        let temp_dir = std::env::temp_dir();
        let cache_file = temp_dir.join("test_semantic_cache.json");
        let cache_file_str = cache_file.to_str().unwrap().to_string();

        // Clean up any pre-existing test files
        let _ = std::fs::remove_file(&cache_file);

        let mut cache = SemanticCache::new(0.85, Some(cache_file_str.clone()));
        cache.insert(
            "hello".to_string(),
            "world".to_string(),
            vec![0.1, 0.2, 0.3],
            "ns".to_string(),
        );

        assert!(cache_file.exists());

        // Create new cache pointing to the same file
        let new_cache = SemanticCache::new(0.85, Some(cache_file_str.clone()));
        assert_eq!(new_cache.size(), 1);
        assert_eq!(
            new_cache.lookup("hello", vec![0.1, 0.2, 0.3], "ns"),
            Some("world".to_string())
        );

        // Clean up
        let _ = std::fs::remove_file(&cache_file);
    }

    #[test]
    fn test_lookup_with_events_l1_hit() {
        let mut cache = SemanticCache::new(0.9, None);
        let bus = EventBus::new();
        let ns = "ns".to_string();

        cache.insert_with_events(
            "test prompt".to_string(),
            "test response".to_string(),
            vec![1.0, 0.0],
            ns.clone(),
            &bus,
        );

        let result = cache.lookup_with_events("test prompt", vec![0.0, 1.0], &ns, &bus);
        assert_eq!(result, Some("test response".to_string()));

        let events = bus.drain();
        // 1 insert + 1 lookup = 2 events
        assert_eq!(events.len(), 2);

        match &events[1] {
            crate::eac::events::EacEvent::CacheLookup {
                hit, match_kind, ..
            } => {
                assert!(*hit);
                assert_eq!(*match_kind, Some(CacheMatchKind::ExactBlake3));
            }
            other => panic!("Expected CacheLookup, got {:?}", other),
        }
    }

    #[test]
    fn test_lookup_with_events_l2_hit() {
        let mut cache = SemanticCache::new(0.5, None);
        let bus = EventBus::new();
        let ns = "ns".to_string();

        cache.insert(
            "original".to_string(),
            "cached".to_string(),
            vec![0.9, 0.1],
            ns.clone(),
        );

        let result = cache.lookup_with_events("different", vec![0.88, 0.12], &ns, &bus);
        assert_eq!(result, Some("cached".to_string()));

        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            crate::eac::events::EacEvent::CacheLookup {
                hit,
                match_kind,
                similarity_score,
                ..
            } => {
                assert!(*hit);
                assert_eq!(*match_kind, Some(CacheMatchKind::SemanticCosine));
                assert!(similarity_score.unwrap() > 0.5);
            }
            other => panic!("Expected CacheLookup, got {:?}", other),
        }
    }

    #[test]
    fn test_lookup_with_events_miss() {
        let cache = SemanticCache::new(0.9, None);
        let bus = EventBus::new();

        let result = cache.lookup_with_events("query", vec![1.0, 0.0], "ns", &bus);
        assert!(result.is_none());

        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            crate::eac::events::EacEvent::CacheLookup {
                hit, match_kind, ..
            } => {
                assert!(!*hit);
                assert!(match_kind.is_none());
            }
            other => panic!("Expected CacheLookup, got {:?}", other),
        }
    }

    #[test]
    fn test_invalidate_with_events() {
        let mut cache = SemanticCache::new(0.9, None);
        let bus = EventBus::new();

        cache.insert(
            "q1".to_string(),
            "v1".to_string(),
            vec![1.0],
            "ns1".to_string(),
        );
        cache.insert(
            "q2".to_string(),
            "v2".to_string(),
            vec![1.0],
            "ns1".to_string(),
        );
        cache.insert(
            "q3".to_string(),
            "v3".to_string(),
            vec![1.0],
            "ns2".to_string(),
        );

        cache.invalidate_with_events("ns1", &bus);

        assert_eq!(cache.size(), 1);
        let events = bus.drain();
        assert_eq!(events.len(), 1);
        match &events[0] {
            crate::eac::events::EacEvent::CacheInvalidate {
                namespace,
                records_removed,
                ..
            } => {
                assert_eq!(namespace, "ns1");
                assert_eq!(*records_removed, 2);
            }
            other => panic!("Expected CacheInvalidate, got {:?}", other),
        }
    }
}
