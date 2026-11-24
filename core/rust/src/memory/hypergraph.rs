//! Research-grade Memory Hypergraph Engine.
//!
//! Absorbs and personalizes structural mechanisms from Graphify (entity
//! deduplication guards, Leiden/modularity clustering with low-cohesion re-split
//! and Jaccard community remapping, god-node / surprising-bridge / blast-radius
//! analytics) fused with AEGIS invariants: typed epistemic provenance
//! (`Extracted` / `Inferred` / `Ambiguous`), bi-temporal edge validity, AST/lifecycle
//! survivor tie-breaking, hybrid BM25 + `i8`-quantized vector + multi-seed
//! Personalized PageRank retrieval, and 30-day half-life `reflect` scoring.

use crate::memory::fidelity::{
    ExperientialReflectVerdict, HALF_LIFE_DEFAULT_DAYS, ReflectDisposition,
    compute_experiential_reflect_score,
};
use crate::memory::frame::EdgeProvenance;
use blake3::Hasher;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use unicode_normalization::UnicodeNormalization;
use unicode_normalization::char::is_combining_mark;
use xxhash_rust::xxh3::xxh3_64_with_seed;

pub const LSH_THRESHOLD: f64 = 0.7;
pub const MINHASH_PERMUTATIONS: usize = 128;
const LSH_BANDS: usize = 16;
const LSH_ROWS_PER_BAND: usize = MINHASH_PERMUTATIONS / LSH_BANDS;
pub const ENTROPY_THRESHOLD: f64 = 2.5;
pub const SHORT_LABEL_MIN_CHARS: usize = 6;
pub const MERGE_THRESHOLD: f64 = 92.0;
pub const COMMUNITY_BOOST: f64 = 5.0;
pub const MIN_COMMUNITY_COHESION: f64 = 0.05;
pub const DEFAULT_MAX_COMMUNITY_SIZE: usize = 64;
pub const QUANTIZED_VECTOR_DIM: usize = 64;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HyperNodeKind {
    Symbol,
    File,
    Module,
    Concept,
    Memory,
    Session,
    Task,
    Evidence,
}

impl HyperNodeKind {
    pub fn is_file_anchored(self) -> bool {
        matches!(self, Self::Symbol | Self::File)
    }

    pub fn family_id(self) -> u8 {
        match self {
            Self::Symbol => 1,
            Self::File => 2,
            Self::Module => 3,
            Self::Concept | Self::Memory => 4,
            Self::Session => 5,
            Self::Task => 6,
            Self::Evidence => 7,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HyperNode {
    pub node_id: u128,
    pub label: String,
    pub summary: String,
    pub kind: HyperNodeKind,
    pub source_file: Option<String>,
    pub ast_fingerprint: u64,
    pub lifecycle_rank: u8,
    pub is_synthetic: bool,
    pub community_id: Option<u32>,
    pub created_at_ms: u64,
    pub code_fingerprint: Option<u64>,
    pub quantized_embedding: Option<Vec<i8>>,
}

impl HyperNode {
    pub fn new(node_id: u128, label: impl Into<String>, kind: HyperNodeKind) -> Self {
        Self {
            node_id,
            label: label.into(),
            summary: String::new(),
            kind,
            source_file: None,
            ast_fingerprint: 0,
            lifecycle_rank: 1,
            is_synthetic: false,
            community_id: None,
            created_at_ms: 1,
            code_fingerprint: None,
            quantized_embedding: None,
        }
    }

    pub fn normalized_source_file(&self) -> Option<String> {
        self.source_file
            .as_deref()
            .map(normalize_repo_relative_path)
            .filter(|path| !path.is_empty())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HyperEdge {
    pub hyperedge_id: u128,
    pub relation: String,
    pub sources: Vec<u128>,
    pub targets: Vec<u128>,
    pub provenance: EdgeProvenance,
    pub confidence: f32,
    pub weight: f32,
    pub valid_from_ms: u64,
    pub invalidated_at_ms: Option<u64>,
    pub code_fingerprint: Option<u64>,
}

impl HyperEdge {
    pub fn binary(
        hyperedge_id: u128,
        from_node: u128,
        to_node: u128,
        relation: impl Into<String>,
        provenance: EdgeProvenance,
        confidence: f32,
        weight: f32,
        valid_from_ms: u64,
    ) -> Self {
        Self {
            hyperedge_id,
            relation: relation.into(),
            sources: vec![from_node],
            targets: vec![to_node],
            provenance,
            confidence: confidence.clamp(0.0, 1.0),
            weight: weight.clamp(0.0, 1.0),
            valid_from_ms: valid_from_ms.max(1),
            invalidated_at_ms: None,
            code_fingerprint: None,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.hyperedge_id > 0
            && !self.relation.trim().is_empty()
            && !self.sources.is_empty()
            && !self.targets.is_empty()
            && self.sources.iter().all(|&id| id > 0)
            && self.targets.iter().all(|&id| id > 0)
            && !(self.sources.len() == 1
                && self.targets.len() == 1
                && self.sources[0] == self.targets[0])
            && self.confidence.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.weight.is_finite()
            && self.weight >= 0.0
            && self.valid_from_ms > 0
            && self
                .invalidated_at_ms
                .is_none_or(|inv| inv >= self.valid_from_ms)
    }

    pub fn is_active_at(&self, timestamp_ms: Option<u64>) -> bool {
        match timestamp_ms {
            Some(ts) => {
                ts >= self.valid_from_ms && self.invalidated_at_ms.is_none_or(|inv| ts < inv)
            }
            None => self.invalidated_at_ms.is_none(),
        }
    }

    pub fn effective_weight(&self) -> f32 {
        (self.weight * self.provenance.transition_multiplier(self.confidence)).clamp(0.0, 1.0)
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NodeReflectRecord {
    pub supporting_outcomes: u32,
    pub contradicting_outcomes: u32,
    pub last_reflected_at_ms: u64,
    pub recorded_code_fingerprint: Option<u64>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DedupGuardConfig {
    pub enable_entropy_guard: bool,
    pub enable_short_label_guard: bool,
    pub enable_numeric_guard: bool,
    pub enable_variant_guard: bool,
    pub enable_token_swap_guard: bool,
    pub enable_crossfile_guard: bool,
    pub enable_community_boost: bool,
}

impl Default for DedupGuardConfig {
    fn default() -> Self {
        Self {
            enable_entropy_guard: true,
            enable_short_label_guard: true,
            enable_numeric_guard: true,
            enable_variant_guard: true,
            enable_token_swap_guard: true,
            enable_crossfile_guard: true,
            enable_community_boost: true,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct DedupMergeReport {
    pub initial_node_count: usize,
    pub final_node_count: usize,
    pub merged_node_count: usize,
    pub candidate_pairs_evaluated: usize,
    pub canonical_remap: BTreeMap<u128, u128>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CommunitySummary {
    pub community_id: u32,
    pub node_ids: Vec<u128>,
    pub internal_cohesion: f64,
    pub conductance: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct GodNodeEntry {
    pub node_id: u128,
    pub label: String,
    pub kind: HyperNodeKind,
    pub weighted_degree: f64,
    pub bridged_communities: usize,
    pub incident_hyperedges: usize,
    pub hub_score: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SurprisingConnectionEntry {
    pub hyperedge_id: u128,
    pub from_node: u128,
    pub to_node: u128,
    pub relation: String,
    pub provenance: EdgeProvenance,
    pub from_community: Option<u32>,
    pub to_community: Option<u32>,
    pub surprise_score: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AffectedNodeEntry {
    pub node_id: u128,
    pub label: String,
    pub kind: HyperNodeKind,
    pub source_file: Option<String>,
    pub hop_distance: u8,
    pub impact_score: f64,
    pub via_relation: String,
    pub via_provenance: EdgeProvenance,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HypergraphDiff {
    pub added_nodes: Vec<u128>,
    pub removed_nodes: Vec<u128>,
    pub modified_nodes: Vec<u128>,
    pub added_edges: Vec<u128>,
    pub removed_edges: Vec<u128>,
    pub invalidated_edges: Vec<u128>,
    pub community_stability_jaccard: f64,
    pub diff_digest: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub enum RetrievalTierOrigin {
    HotLexicalBm25,
    WarmGraphPpr,
    ColdVectorQuantized,
    HybridFused,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct RetrievalAblationConfig {
    pub enable_bm25: bool,
    pub enable_vector: bool,
    pub enable_ppr: bool,
    pub enable_reflect: bool,
}

impl Default for RetrievalAblationConfig {
    fn default() -> Self {
        Self {
            enable_bm25: true,
            enable_vector: true,
            enable_ppr: true,
            enable_reflect: true,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HybridRetrievalHit {
    pub node_id: u128,
    pub label: String,
    pub kind: HyperNodeKind,
    pub origin_tier: RetrievalTierOrigin,
    pub fused_score: f64,
    pub bm25_score: f64,
    pub vector_score: f64,
    pub ppr_score: f64,
    pub reflect_disposition: ReflectDisposition,
}

pub fn normalize_repo_relative_path(raw: &str) -> String {
    let unified = raw.trim().replace('\\', "/");
    let stripped = unified.strip_prefix("./").unwrap_or(&unified);
    let mut parts = Vec::new();
    for segment in stripped.split('/') {
        if segment.is_empty() || segment == "." {
            continue;
        }
        parts.push(segment);
    }
    parts.join("/")
}

/// Stage 1: Unicode `NFKC` + combining-mark-preserving (`M*`) + camelCase-aware normalization.
pub fn normalize_entity_label(raw: &str) -> String {
    let nfkc: String = raw.nfkc().collect();
    let chars: Vec<char> = nfkc.chars().collect();
    let mut expanded = String::with_capacity(nfkc.len() + 8);
    for (idx, &ch) in chars.iter().enumerate() {
        if idx > 0 && ch.is_uppercase() {
            let prev = chars[idx - 1];
            let next_is_lower = chars.get(idx + 1).is_some_and(|c| c.is_lowercase());
            if prev.is_lowercase()
                || prev.is_ascii_digit()
                || (prev.is_uppercase() && next_is_lower)
            {
                expanded.push(' ');
            }
        }
        if ch.is_alphanumeric() || is_combining_mark(ch) {
            for lower in ch.to_lowercase() {
                expanded.push(lower);
            }
        } else {
            expanded.push(' ');
        }
    }
    expanded.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Stage 4: Shannon character entropy in bits per character.
pub fn shannon_char_entropy(normalized: &str) -> f64 {
    let mut counts: BTreeMap<char, usize> = BTreeMap::new();
    let mut total = 0usize;
    for ch in normalized.chars().filter(|c| !c.is_whitespace()) {
        *counts.entry(ch).or_insert(0) += 1;
        total += 1;
    }
    if total <= 1 {
        return 0.0;
    }
    let inv_total = 1.0 / (total as f64);
    let mut entropy = 0.0f64;
    for count in counts.values() {
        let p = (*count as f64) * inv_total;
        entropy -= p * p.log2();
    }
    entropy
}

/// Stage 5: Short-label guard (`< 6` non-space characters cannot fuzzy-merge).
pub fn short_label_blocked(norm_a: &str, norm_b: &str) -> bool {
    if norm_a == norm_b {
        return false;
    }
    let len_a = norm_a.chars().filter(|c| !c.is_whitespace()).count();
    let len_b = norm_b.chars().filter(|c| !c.is_whitespace()).count();
    len_a < SHORT_LABEL_MIN_CHARS || len_b < SHORT_LABEL_MIN_CHARS
}

fn extract_numeric_tokens(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    for ch in text.chars() {
        if ch.is_ascii_digit() {
            current.push(ch);
        } else if !current.is_empty() {
            let trimmed = current.trim_start_matches('0');
            out.push(if trimmed.is_empty() {
                "0".to_owned()
            } else {
                trimmed.to_owned()
            });
            current.clear();
        }
    }
    if !current.is_empty() {
        let trimmed = current.trim_start_matches('0');
        out.push(if trimmed.is_empty() {
            "0".to_owned()
        } else {
            trimmed.to_owned()
        });
    }
    out
}

/// Stage 6: Numeric token divergence guard (`v1` vs `v2`, `sha256` vs `sha512`, `stage_1` vs `stage_2`).
pub fn numeric_tokens_differ(norm_a: &str, norm_b: &str) -> bool {
    let nums_a = extract_numeric_tokens(norm_a);
    let nums_b = extract_numeric_tokens(norm_b);
    if nums_a.is_empty() && nums_b.is_empty() {
        return false;
    }
    nums_a != nums_b
}

const VARIANT_ANTONYM_PAIRS: &[(&str, &str)] = &[
    ("read", "write"),
    ("reader", "writer"),
    ("encode", "decode"),
    ("encoder", "decoder"),
    ("serialize", "deserialize"),
    ("pack", "unpack"),
    ("push", "pop"),
    ("enqueue", "dequeue"),
    ("send", "recv"),
    ("sender", "receiver"),
    ("inbound", "outbound"),
    ("input", "output"),
    ("request", "response"),
    ("client", "server"),
    ("source", "target"),
    ("caller", "callee"),
    ("from", "to"),
    ("lock", "unlock"),
    ("acquire", "release"),
    ("allow", "deny"),
    ("accept", "reject"),
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("start", "stop"),
    ("open", "close"),
    ("min", "max"),
    ("pre", "post"),
    ("before", "after"),
    ("hot", "cold"),
    ("sync", "async"),
    ("local", "remote"),
    ("public", "private"),
    ("mutable", "immutable"),
    ("true", "false"),
    ("left", "right"),
    ("up", "down"),
];

/// Stage 7: Directional / antonym / variant pair guard.
pub fn is_variant_pair(norm_a: &str, norm_b: &str) -> bool {
    let tokens_a: BTreeSet<&str> = norm_a.split_whitespace().collect();
    let tokens_b: BTreeSet<&str> = norm_b.split_whitespace().collect();
    let only_a: BTreeSet<&str> = tokens_a.difference(&tokens_b).copied().collect();
    let only_b: BTreeSet<&str> = tokens_b.difference(&tokens_a).copied().collect();
    if only_a.is_empty() || only_b.is_empty() {
        return false;
    }
    for &(left, right) in VARIANT_ANTONYM_PAIRS {
        if (only_a.contains(left) && only_b.contains(right))
            || (only_a.contains(right) && only_b.contains(left))
        {
            return true;
        }
    }
    false
}

fn is_stop_connector(token: &str) -> bool {
    matches!(
        token,
        "a" | "an" | "the" | "of" | "for" | "in" | "on" | "by" | "with" | "and" | "or"
    )
}

/// Stage 8: Content-token swap guard (`memory_to_file` vs `file_to_memory`).
pub fn content_token_swap(norm_a: &str, norm_b: &str) -> bool {
    if norm_a == norm_b {
        return false;
    }
    let toks_a: Vec<&str> = norm_a
        .split_whitespace()
        .filter(|t| !is_stop_connector(t))
        .collect();
    let toks_b: Vec<&str> = norm_b
        .split_whitespace()
        .filter(|t| !is_stop_connector(t))
        .collect();
    if toks_a.len() < 2 || toks_b.len() < 2 {
        return false;
    }
    let set_a: BTreeSet<&str> = toks_a.iter().copied().collect();
    let set_b: BTreeSet<&str> = toks_b.iter().copied().collect();
    let shared: BTreeSet<&str> = set_a.intersection(&set_b).copied().collect();
    if shared.len() < 2 {
        return false;
    }
    let order_a: Vec<&str> = toks_a.into_iter().filter(|t| shared.contains(t)).collect();
    let order_b: Vec<&str> = toks_b.into_iter().filter(|t| shared.contains(t)).collect();
    order_a != order_b
}

/// Stage 9: Cross-file file-anchored symbol guard.
pub fn crossfile_fileanchored_blocked(node_a: &HyperNode, node_b: &HyperNode) -> bool {
    if !node_a.kind.is_file_anchored() || !node_b.kind.is_file_anchored() {
        return false;
    }
    match (
        node_a.normalized_source_file(),
        node_b.normalized_source_file(),
    ) {
        (Some(file_a), Some(file_b)) => file_a != file_b,
        _ => false,
    }
}

/// Stage 10: Jaro-Winkler similarity scaled to `[0.0, 100.0]`.
pub fn jaro_winkler_score_100(a: &str, b: &str) -> f64 {
    if a == b {
        return 100.0;
    }
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let len_a = a_chars.len();
    let len_b = b_chars.len();
    if len_a == 0 || len_b == 0 {
        return 0.0;
    }
    let match_distance = (len_a.max(len_b) / 2).saturating_sub(1);
    let mut a_matched = vec![false; len_a];
    let mut b_matched = vec![false; len_b];
    let mut matches = 0usize;

    for i in 0..len_a {
        let start = i.saturating_sub(match_distance);
        let end = (i + match_distance + 1).min(len_b);
        for j in start..end {
            if !b_matched[j] && a_chars[i] == b_chars[j] {
                a_matched[i] = true;
                b_matched[j] = true;
                matches += 1;
                break;
            }
        }
    }
    if matches == 0 {
        return 0.0;
    }
    let mut transpositions = 0usize;
    let mut k = 0usize;
    for i in 0..len_a {
        if !a_matched[i] {
            continue;
        }
        while !b_matched[k] {
            k += 1;
        }
        if a_chars[i] != b_chars[k] {
            transpositions += 1;
        }
        k += 1;
    }
    let m = matches as f64;
    let jaro =
        (m / (len_a as f64) + m / (len_b as f64) + (m - (transpositions as f64) / 2.0) / m) / 3.0;
    let prefix = a_chars
        .iter()
        .zip(b_chars.iter())
        .take(4)
        .take_while(|(ca, cb)| ca == cb)
        .count() as f64;
    (jaro + prefix * 0.1 * (1.0 - jaro)).clamp(0.0, 1.0) * 100.0
}

fn char_shingles_3(normalized: &str) -> Vec<String> {
    let padded = format!("^{normalized}$");
    let chars: Vec<char> = padded.chars().collect();
    if chars.len() <= 3 {
        return vec![padded];
    }
    let mut shingles = Vec::with_capacity(chars.len() - 2);
    for window in chars.windows(3) {
        let mut s = String::with_capacity(6);
        for &c in window {
            s.push(c);
        }
        shingles.push(s);
    }
    shingles.sort_unstable();
    shingles.dedup();
    shingles
}

fn compute_minhash_signature(normalized: &str) -> [u64; MINHASH_PERMUTATIONS] {
    let shingles = char_shingles_3(normalized);
    let mut sig = [u64::MAX; MINHASH_PERMUTATIONS];
    for shingle in &shingles {
        let bytes = shingle.as_bytes();
        for (perm, slot) in sig.iter_mut().enumerate() {
            let h = xxh3_64_with_seed(bytes, (perm as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15));
            if h < *slot {
                *slot = h;
            }
        }
    }
    sig
}

fn minhash_jaccard_estimate(
    sig_a: &[u64; MINHASH_PERMUTATIONS],
    sig_b: &[u64; MINHASH_PERMUTATIONS],
) -> f64 {
    let equal = sig_a
        .iter()
        .zip(sig_b.iter())
        .filter(|(a, b)| a == b)
        .count();
    (equal as f64) / (MINHASH_PERMUTATIONS as f64)
}

pub fn deterministic_quantized_embedding(text: &str) -> Vec<i8> {
    let normalized = normalize_entity_label(text);
    let mut accum = [0i32; QUANTIZED_VECTOR_DIM];
    for token in normalized.split_whitespace() {
        let h1 = xxh3_64_with_seed(token.as_bytes(), 0xA361_5EED_0001);
        let h2 = xxh3_64_with_seed(token.as_bytes(), 0xA361_5EED_0002);
        let dim1 = (h1 as usize) % QUANTIZED_VECTOR_DIM;
        let sign1 = if (h1 >> 63) == 0 { 16 } else { -16 };
        accum[dim1] += sign1;
        let dim2 = (h2 as usize) % QUANTIZED_VECTOR_DIM;
        let sign2 = if (h2 >> 63) == 0 { 8 } else { -8 };
        accum[dim2] += sign2;
    }
    for shingle in char_shingles_3(&normalized) {
        let h = xxh3_64_with_seed(shingle.as_bytes(), 0xA361_5EED_0003);
        let dim = (h as usize) % QUANTIZED_VECTOR_DIM;
        let sign = if (h >> 63) == 0 { 3 } else { -3 };
        accum[dim] += sign;
    }
    let max_abs = accum.iter().map(|v| v.abs()).max().unwrap_or(1).max(1);
    accum
        .iter()
        .map(|&v| ((v * 127) / max_abs).clamp(-127, 127) as i8)
        .collect()
}

fn quantized_cosine_similarity(a: &[i8], b: &[i8]) -> f64 {
    let len = a.len().min(b.len());
    if len == 0 {
        return 0.0;
    }
    let mut dot = 0i64;
    let mut norm_a = 0i64;
    let mut norm_b = 0i64;
    for i in 0..len {
        let va = a[i] as i64;
        let vb = b[i] as i64;
        dot += va * vb;
        norm_a += va * va;
        norm_b += vb * vb;
    }
    if norm_a == 0 || norm_b == 0 {
        return 0.0;
    }
    ((dot as f64) / ((norm_a as f64).sqrt() * (norm_b as f64).sqrt())).clamp(-1.0, 1.0)
}

struct DisjointSet {
    parent: Vec<usize>,
}

impl DisjointSet {
    fn new(n: usize) -> Self {
        Self {
            parent: (0..n).collect(),
        }
    }

    fn find(&mut self, mut x: usize) -> usize {
        while self.parent[x] != x {
            let grand = self.parent[self.parent[x]];
            self.parent[x] = grand;
            x = grand;
        }
        x
    }

    fn union(&mut self, a: usize, b: usize) {
        let ra = self.find(a);
        let rb = self.find(b);
        if ra != rb {
            if ra < rb {
                self.parent[rb] = ra;
            } else {
                self.parent[ra] = rb;
            }
        }
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct MemoryHypergraph {
    nodes: BTreeMap<u128, HyperNode>,
    hyperedges: BTreeMap<u128, HyperEdge>,
    reflect_records: BTreeMap<u128, NodeReflectRecord>,
}

impl MemoryHypergraph {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    pub fn hyperedge_count(&self) -> usize {
        self.hyperedges.len()
    }

    pub fn get_node(&self, node_id: u128) -> Option<&HyperNode> {
        self.nodes.get(&node_id)
    }

    pub fn get_hyperedge(&self, hyperedge_id: u128) -> Option<&HyperEdge> {
        self.hyperedges.get(&hyperedge_id)
    }

    pub fn nodes(&self) -> &BTreeMap<u128, HyperNode> {
        &self.nodes
    }

    pub fn hyperedges(&self) -> &BTreeMap<u128, HyperEdge> {
        &self.hyperedges
    }

    pub fn upsert_node(&mut self, mut node: HyperNode) -> Result<(), &'static str> {
        if node.node_id == 0 || node.label.trim().is_empty() {
            return Err("invalid hypernode identity or empty label");
        }
        if node.quantized_embedding.is_none() {
            let text = if node.summary.is_empty() {
                node.label.clone()
            } else {
                format!("{} {}", node.label, node.summary)
            };
            node.quantized_embedding = Some(deterministic_quantized_embedding(&text));
        }
        if let Some(existing) = self.nodes.get_mut(&node.node_id) {
            existing.lifecycle_rank = existing.lifecycle_rank.max(node.lifecycle_rank);
            if node.ast_fingerprint != 0 {
                existing.ast_fingerprint = node.ast_fingerprint;
            }
            if !node.summary.is_empty() {
                existing.summary = node.summary;
            }
            if node.source_file.is_some() {
                existing.source_file = node.source_file;
            }
            if node.code_fingerprint.is_some() {
                existing.code_fingerprint = node.code_fingerprint;
            }
            if node.community_id.is_some() {
                existing.community_id = node.community_id;
            }
            existing.label = node.label;
            existing.quantized_embedding = node.quantized_embedding;
        } else {
            self.nodes.insert(node.node_id, node);
        }
        Ok(())
    }

    pub fn upsert_hyperedge(&mut self, edge: HyperEdge) -> Result<(), &'static str> {
        if !edge.is_valid() {
            return Err("invalid hyperedge");
        }
        for endpoint in edge.sources.iter().chain(edge.targets.iter()) {
            if !self.nodes.contains_key(endpoint) {
                return Err("hyperedge references missing node");
            }
        }
        if let Some(existing) = self.hyperedges.get_mut(&edge.hyperedge_id) {
            existing.weight = existing.weight.max(edge.weight).clamp(0.0, 1.0);
            existing.confidence = existing.confidence.max(edge.confidence).clamp(0.0, 1.0);
            if edge.provenance < existing.provenance {
                existing.provenance = edge.provenance;
            }
            existing.invalidated_at_ms = edge.invalidated_at_ms;
            existing.code_fingerprint = edge.code_fingerprint.or(existing.code_fingerprint);
        } else {
            self.hyperedges.insert(edge.hyperedge_id, edge);
        }
        Ok(())
    }

    pub fn invalidate_hyperedge(
        &mut self,
        hyperedge_id: u128,
        invalidated_at_ms: u64,
    ) -> Result<bool, &'static str> {
        let Some(edge) = self.hyperedges.get_mut(&hyperedge_id) else {
            return Ok(false);
        };
        if invalidated_at_ms < edge.valid_from_ms {
            return Err("invalidation timestamp precedes valid_from_ms");
        }
        edge.invalidated_at_ms = Some(invalidated_at_ms);
        Ok(true)
    }

    pub fn record_reflect_outcome(
        &mut self,
        node_id: u128,
        supporting: bool,
        observed_at_ms: u64,
        code_fingerprint: Option<u64>,
    ) -> Result<ExperientialReflectVerdict, &'static str> {
        let Some(node) = self.nodes.get(&node_id) else {
            return Err("cannot reflect on missing hypernode");
        };
        let entry = self
            .reflect_records
            .entry(node_id)
            .or_insert(NodeReflectRecord {
                supporting_outcomes: 0,
                contradicting_outcomes: 0,
                last_reflected_at_ms: observed_at_ms.max(1),
                recorded_code_fingerprint: code_fingerprint.or(node.code_fingerprint),
            });
        if supporting {
            entry.supporting_outcomes = entry.supporting_outcomes.saturating_add(1);
        } else {
            entry.contradicting_outcomes = entry.contradicting_outcomes.saturating_add(1);
        }
        entry.last_reflected_at_ms = observed_at_ms.max(entry.last_reflected_at_ms);
        if code_fingerprint.is_some() {
            entry.recorded_code_fingerprint = code_fingerprint;
        }
        Ok(self.evaluate_node_reflect(node_id, observed_at_ms))
    }

    pub fn evaluate_node_reflect(&self, node_id: u128, now_ms: u64) -> ExperientialReflectVerdict {
        let Some(node) = self.nodes.get(&node_id) else {
            return ExperientialReflectVerdict {
                disposition: ReflectDisposition::NeutralUnverified,
                effective_score: 0.0,
                decayed_boost: 0.0,
                corroboration_count: 0,
                contradiction_count: 0,
            };
        };
        let base_fidelity = match node.lifecycle_rank {
            3.. => 0.80,
            2 => 0.70,
            1 => 0.55,
            _ => 0.40,
        };
        let Some(record) = self.reflect_records.get(&node_id) else {
            return ExperientialReflectVerdict {
                disposition: ReflectDisposition::NeutralUnverified,
                effective_score: base_fidelity,
                decayed_boost: 0.0,
                corroboration_count: 0,
                contradiction_count: 0,
            };
        };
        let fp_current = match (node.code_fingerprint, record.recorded_code_fingerprint) {
            (Some(current_fp), Some(recorded_fp)) => current_fp == recorded_fp,
            _ => true,
        };
        let elapsed_ms = now_ms.saturating_sub(record.last_reflected_at_ms);
        let elapsed_days = (elapsed_ms as f64) / 86_400_000.0;
        compute_experiential_reflect_score(
            base_fidelity,
            record.supporting_outcomes,
            record.contradicting_outcomes,
            elapsed_days,
            HALF_LIFE_DEFAULT_DAYS,
            fp_current,
        )
    }

    fn node_incident_degrees(&self) -> BTreeMap<u128, usize> {
        let mut degrees: BTreeMap<u128, usize> =
            self.nodes.keys().copied().map(|id| (id, 0usize)).collect();
        for edge in self.hyperedges.values() {
            if edge.invalidated_at_ms.is_some() {
                continue;
            }
            for &id in edge.sources.iter().chain(edge.targets.iter()) {
                if let Some(deg) = degrees.get_mut(&id) {
                    *deg += 1;
                }
            }
        }
        degrees
    }

    /// Full 10-stage entity deduplicator with configurable guard toggles for ablation.
    pub fn deduplicate_entities(&mut self, guards: DedupGuardConfig) -> DedupMergeReport {
        let initial_count = self.nodes.len();
        if initial_count <= 1 {
            return DedupMergeReport {
                initial_node_count: initial_count,
                final_node_count: initial_count,
                merged_node_count: 0,
                candidate_pairs_evaluated: 0,
                canonical_remap: self.nodes.keys().copied().map(|id| (id, id)).collect(),
            };
        }

        let ordered_ids: Vec<u128> = self.nodes.keys().copied().collect();
        let degrees = self.node_incident_degrees();

        let precomputed: Vec<(String, f64, [u64; MINHASH_PERMUTATIONS])> =
            if ordered_ids.len() >= 128 {
                ordered_ids
                    .par_iter()
                    .map(|id| {
                        let node = &self.nodes[id];
                        let norm = normalize_entity_label(&node.label);
                        let ent = shannon_char_entropy(&norm);
                        let sig = compute_minhash_signature(&norm);
                        (norm, ent, sig)
                    })
                    .collect()
            } else {
                ordered_ids
                    .iter()
                    .map(|id| {
                        let node = &self.nodes[id];
                        let norm = normalize_entity_label(&node.label);
                        let ent = shannon_char_entropy(&norm);
                        let sig = compute_minhash_signature(&norm);
                        (norm, ent, sig)
                    })
                    .collect()
            };

        // Stage 2 & 3: Exact normalized buckets + 16-band LSH + 4-char prefix blocks
        let mut exact_buckets: BTreeMap<(u8, String), Vec<usize>> = BTreeMap::new();
        let mut lsh_buckets: BTreeMap<(u8, usize, u64), Vec<usize>> = BTreeMap::new();
        let mut prefix_buckets: BTreeMap<(u8, String), Vec<usize>> = BTreeMap::new();

        for (idx, id) in ordered_ids.iter().enumerate() {
            let family = self.nodes[id].kind.family_id();
            let (ref norm, _, ref sig) = precomputed[idx];
            if norm.is_empty() {
                continue;
            }
            exact_buckets
                .entry((family, norm.clone()))
                .or_default()
                .push(idx);

            let prefix: String = norm
                .chars()
                .filter(|c| !c.is_whitespace())
                .take(4)
                .collect();
            if prefix.chars().count() >= 3 {
                prefix_buckets
                    .entry((family, prefix))
                    .or_default()
                    .push(idx);
            }

            for band in 0..LSH_BANDS {
                let start = band * LSH_ROWS_PER_BAND;
                let mut band_bytes = [0u8; LSH_ROWS_PER_BAND * 8];
                for row in 0..LSH_ROWS_PER_BAND {
                    let bytes = sig[start + row].to_le_bytes();
                    band_bytes[row * 8..(row + 1) * 8].copy_from_slice(&bytes);
                }
                let band_hash = xxh3_64_with_seed(&band_bytes, band as u64);
                lsh_buckets
                    .entry((family, band, band_hash))
                    .or_default()
                    .push(idx);
            }
        }

        let mut candidate_pairs: BTreeSet<(usize, usize)> = BTreeSet::new();
        const MAX_BUCKET_FANOUT: usize = 64;
        for bucket in exact_buckets
            .values()
            .chain(lsh_buckets.values())
            .chain(prefix_buckets.values())
        {
            if bucket.len() < 2 {
                continue;
            }
            let cap = bucket.len().min(MAX_BUCKET_FANOUT);
            for i in 0..cap {
                for j in (i + 1)..cap {
                    let a = bucket[i].min(bucket[j]);
                    let b = bucket[i].max(bucket[j]);
                    candidate_pairs.insert((a, b));
                }
            }
        }

        let mut dsu = DisjointSet::new(ordered_ids.len());
        let candidate_pairs_evaluated = candidate_pairs.len();

        for (i, j) in candidate_pairs {
            let node_a = &self.nodes[&ordered_ids[i]];
            let node_b = &self.nodes[&ordered_ids[j]];
            if node_a.is_synthetic || node_b.is_synthetic {
                continue;
            }
            let (ref norm_a, ent_a, ref sig_a) = precomputed[i];
            let (ref norm_b, ent_b, ref sig_b) = precomputed[j];

            // Stage 9: Cross-file file-anchored guard applies even to exact symbol names
            if guards.enable_crossfile_guard && crossfile_fileanchored_blocked(node_a, node_b) {
                continue;
            }

            let is_exact = norm_a == norm_b;
            if !is_exact {
                // Stage 3 threshold check: MinHash Jaccard >= 0.7 OR high prefix Jaro-Winkler candidate
                let minhash_sim = minhash_jaccard_estimate(sig_a, sig_b);
                let jw_raw = jaro_winkler_score_100(norm_a, norm_b);
                if minhash_sim < LSH_THRESHOLD && jw_raw < MERGE_THRESHOLD {
                    continue;
                }
                // Stage 4: Shannon entropy guard
                if guards.enable_entropy_guard
                    && (ent_a < ENTROPY_THRESHOLD || ent_b < ENTROPY_THRESHOLD)
                {
                    continue;
                }
                // Stage 5: Short-label guard
                if guards.enable_short_label_guard && short_label_blocked(norm_a, norm_b) {
                    continue;
                }
                // Stage 6: Numeric token divergence guard
                if guards.enable_numeric_guard && numeric_tokens_differ(norm_a, norm_b) {
                    continue;
                }
                // Stage 7: Variant / antonym guard
                if guards.enable_variant_guard && is_variant_pair(norm_a, norm_b) {
                    continue;
                }
                // Stage 8: Content-token swap guard
                if guards.enable_token_swap_guard && content_token_swap(norm_a, norm_b) {
                    continue;
                }
                // Stage 10: Jaro-Winkler + community boost
                let boost = if guards.enable_community_boost
                    && node_a.community_id.is_some()
                    && node_a.community_id == node_b.community_id
                {
                    COMMUNITY_BOOST
                } else {
                    0.0
                };
                if jw_raw + boost < MERGE_THRESHOLD {
                    continue;
                }
            }
            dsu.union(i, j);
        }

        let mut clusters: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for idx in 0..ordered_ids.len() {
            clusters.entry(dsu.find(idx)).or_default().push(idx);
        }

        let mut canonical_remap: BTreeMap<u128, u128> = BTreeMap::new();
        let mut surviving_nodes: BTreeMap<u128, HyperNode> = BTreeMap::new();

        for member_indices in clusters.values() {
            let best_idx = *member_indices
                .iter()
                .max_by(|&&ia, &&ib| {
                    let na = &self.nodes[&ordered_ids[ia]];
                    let nb = &self.nodes[&ordered_ids[ib]];
                    let deg_a = degrees.get(&na.node_id).copied().unwrap_or(0);
                    let deg_b = degrees.get(&nb.node_id).copied().unwrap_or(0);
                    (
                        na.lifecycle_rank,
                        u8::from(na.ast_fingerprint != 0),
                        deg_a,
                        na.label.len(),
                        std::cmp::Reverse(na.node_id),
                    )
                        .cmp(&(
                            nb.lifecycle_rank,
                            u8::from(nb.ast_fingerprint != 0),
                            deg_b,
                            nb.label.len(),
                            std::cmp::Reverse(nb.node_id),
                        ))
                })
                .expect("cluster non-empty");

            let survivor_id = ordered_ids[best_idx];
            let mut survivor = self.nodes[&survivor_id].clone();
            for &idx in member_indices {
                let member_id = ordered_ids[idx];
                canonical_remap.insert(member_id, survivor_id);
                if member_id != survivor_id {
                    let other = &self.nodes[&member_id];
                    if survivor.summary.is_empty() && !other.summary.is_empty() {
                        survivor.summary = other.summary.clone();
                    }
                    if survivor.ast_fingerprint == 0 && other.ast_fingerprint != 0 {
                        survivor.ast_fingerprint = other.ast_fingerprint;
                    }
                    if survivor.source_file.is_none() && other.source_file.is_some() {
                        survivor.source_file = other.source_file.clone();
                    }
                    if let Some(other_ref) = self.reflect_records.remove(&member_id) {
                        let surv_ref =
                            self.reflect_records
                                .entry(survivor_id)
                                .or_insert(NodeReflectRecord {
                                    supporting_outcomes: 0,
                                    contradicting_outcomes: 0,
                                    last_reflected_at_ms: other_ref.last_reflected_at_ms,
                                    recorded_code_fingerprint: other_ref.recorded_code_fingerprint,
                                });
                        surv_ref.supporting_outcomes = surv_ref
                            .supporting_outcomes
                            .saturating_add(other_ref.supporting_outcomes);
                        surv_ref.contradicting_outcomes = surv_ref
                            .contradicting_outcomes
                            .saturating_add(other_ref.contradicting_outcomes);
                        surv_ref.last_reflected_at_ms = surv_ref
                            .last_reflected_at_ms
                            .max(other_ref.last_reflected_at_ms);
                    }
                }
            }
            surviving_nodes.insert(survivor_id, survivor);
        }

        // Remap hyperedge endpoints and drop collapsed self-loops
        let mut remapped_edges: BTreeMap<u128, HyperEdge> = BTreeMap::new();
        let mut dedup_sig_to_edge_id: BTreeMap<(String, Vec<u128>, Vec<u128>), u128> =
            BTreeMap::new();
        for edge in self.hyperedges.values() {
            let mut sources: Vec<u128> = edge
                .sources
                .iter()
                .map(|id| canonical_remap.get(id).copied().unwrap_or(*id))
                .collect();
            sources.sort_unstable();
            sources.dedup();
            let mut targets: Vec<u128> = edge
                .targets
                .iter()
                .map(|id| canonical_remap.get(id).copied().unwrap_or(*id))
                .collect();
            targets.sort_unstable();
            targets.dedup();
            if sources.len() == 1 && targets.len() == 1 && sources[0] == targets[0] {
                continue;
            }
            let sig = (edge.relation.clone(), sources.clone(), targets.clone());
            if let Some(&existing_id) = dedup_sig_to_edge_id.get(&sig) {
                let existing = remapped_edges
                    .get_mut(&existing_id)
                    .expect("existing edge present");
                existing.weight = existing.weight.max(edge.weight);
                existing.confidence = existing.confidence.max(edge.confidence);
                if edge.provenance < existing.provenance {
                    existing.provenance = edge.provenance;
                }
            } else {
                let mut updated = edge.clone();
                updated.sources = sources;
                updated.targets = targets;
                dedup_sig_to_edge_id.insert(sig, updated.hyperedge_id);
                remapped_edges.insert(updated.hyperedge_id, updated);
            }
        }

        self.nodes = surviving_nodes;
        self.hyperedges = remapped_edges;

        let final_count = self.nodes.len();
        DedupMergeReport {
            initial_node_count: initial_count,
            final_node_count: final_count,
            merged_node_count: initial_count.saturating_sub(final_count),
            candidate_pairs_evaluated,
            canonical_remap,
        }
    }

    fn build_undirected_weighted_adj(&self) -> (Vec<u128>, Vec<Vec<(usize, f64)>>) {
        let node_ids: Vec<u128> = self.nodes.keys().copied().collect();
        let id_to_idx: BTreeMap<u128, usize> = node_ids
            .iter()
            .copied()
            .enumerate()
            .map(|(i, id)| (id, i))
            .collect();
        let mut adj_map: Vec<BTreeMap<usize, f64>> = vec![BTreeMap::new(); node_ids.len()];
        for edge in self.hyperedges.values() {
            if edge.invalidated_at_ms.is_some() {
                continue;
            }
            let w = edge.effective_weight() as f64;
            if w <= 0.0 {
                continue;
            }
            let pair_scale = 1.0 / ((edge.sources.len() * edge.targets.len()).max(1) as f64);
            for &s in &edge.sources {
                for &t in &edge.targets {
                    if s == t {
                        continue;
                    }
                    if let (Some(&u), Some(&v)) = (id_to_idx.get(&s), id_to_idx.get(&t)) {
                        *adj_map[u].entry(v).or_insert(0.0) += w * pair_scale;
                        *adj_map[v].entry(u).or_insert(0.0) += w * pair_scale;
                    }
                }
            }
        }
        let adj = adj_map
            .into_iter()
            .map(|m| m.into_iter().collect::<Vec<_>>())
            .collect();
        (node_ids, adj)
    }

    /// Deterministic Leiden-refined modularity clustering with oversized & low-cohesion
    /// (`< 0.05`) recursive re-split and optional Jaccard remapping to previous communities.
    pub fn detect_communities_leiden(
        &mut self,
        max_community_size: usize,
        previous_communities: Option<&BTreeMap<u128, u32>>,
    ) -> Vec<CommunitySummary> {
        let effective_max_size = max_community_size.max(2);
        let (node_ids, adj) = self.build_undirected_weighted_adj();
        let n = node_ids.len();
        if n == 0 {
            return Vec::new();
        }

        let degrees: Vec<f64> = adj
            .iter()
            .map(|nbrs| nbrs.iter().map(|(_, w)| *w).sum())
            .collect();
        let two_m: f64 = degrees.iter().sum::<f64>().max(1e-9);

        let mut comm: Vec<usize> = (0..n).collect();
        let mut sigma_tot: Vec<f64> = degrees.clone();

        // Phase A: Deterministic local modularity moving
        for _pass in 0..12 {
            let mut moved = false;
            for u in 0..n {
                let old_c = comm[u];
                let k_u = degrees[u];
                if k_u <= 0.0 {
                    continue;
                }
                sigma_tot[old_c] -= k_u;
                let mut comm_weights: BTreeMap<usize, f64> = BTreeMap::new();
                for &(v, w) in &adj[u] {
                    *comm_weights.entry(comm[v]).or_insert(0.0) += w;
                }
                let old_w = comm_weights.get(&old_c).copied().unwrap_or(0.0);
                let mut best_c = old_c;
                let mut best_gain = old_w - k_u * sigma_tot[old_c] / two_m;

                for (cand_c, k_u_in) in comm_weights {
                    let gain = k_u_in - k_u * sigma_tot[cand_c] / two_m;
                    if gain > best_gain + 1e-12
                        || ((gain - best_gain).abs() <= 1e-12 && cand_c < best_c)
                    {
                        best_gain = gain;
                        best_c = cand_c;
                    }
                }
                comm[u] = best_c;
                sigma_tot[best_c] += k_u;
                if best_c != old_c {
                    moved = true;
                }
            }
            if !moved {
                break;
            }
        }

        // Phase B: Leiden connected-subgraph refinement (guarantee every community is connected)
        let mut next_comm_id = n;
        let mut grouped: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
        for (u, &c) in comm.iter().enumerate() {
            grouped.entry(c).or_default().push(u);
        }
        for members in grouped.values() {
            if members.len() <= 1 {
                continue;
            }
            let member_set: BTreeSet<usize> = members.iter().copied().collect();
            let mut visited: BTreeSet<usize> = BTreeSet::new();
            let mut comp_idx = 0usize;
            for &seed in members {
                if !visited.insert(seed) {
                    continue;
                }
                let assigned = if comp_idx == 0 {
                    comm[seed]
                } else {
                    let fresh = next_comm_id;
                    next_comm_id += 1;
                    fresh
                };
                comp_idx += 1;
                let mut queue = VecDeque::new();
                queue.push_back(seed);
                while let Some(curr) = queue.pop_front() {
                    comm[curr] = assigned;
                    for &(nbr, _) in &adj[curr] {
                        if member_set.contains(&nbr) && visited.insert(nbr) {
                            queue.push_back(nbr);
                        }
                    }
                }
            }
        }

        // Phase C: Recursive re-split of oversized OR low-cohesion (< MIN_COMMUNITY_COHESION) communities
        let mut partition_queue: VecDeque<Vec<usize>> = {
            let mut m: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
            for (u, &c) in comm.iter().enumerate() {
                m.entry(c).or_default().push(u);
            }
            m.into_values().collect()
        };
        let mut final_partitions: Vec<Vec<usize>> = Vec::new();
        while let Some(mut part) = partition_queue.pop_front() {
            part.sort_unstable();
            let cohesion = subgraph_cohesion(&part, &adj);
            let needs_split = part.len() > effective_max_size
                || (part.len() >= 6 && cohesion < MIN_COMMUNITY_COHESION);
            if !needs_split {
                final_partitions.push(part);
                continue;
            }
            let (left, right) = deterministic_bisect(&part, &adj);
            if left.is_empty() || right.is_empty() {
                final_partitions.push(part);
            } else {
                partition_queue.push_back(left);
                partition_queue.push_back(right);
            }
        }

        // Canonicalize raw partition ordering deterministically by smallest node_id
        final_partitions.sort_by_key(|part| part.first().copied().unwrap_or(usize::MAX));
        let mut raw_communities: BTreeMap<u32, Vec<u128>> = BTreeMap::new();
        for (idx, part) in final_partitions.iter().enumerate() {
            let cid = (idx as u32) + 1;
            raw_communities.insert(cid, part.iter().map(|&u| node_ids[u]).collect());
        }

        let remapped = if let Some(prev) = previous_communities {
            remap_communities_to_previous(prev, &raw_communities)
        } else {
            raw_communities
        };

        for node in self.nodes.values_mut() {
            node.community_id = None;
        }
        let idx_by_id: BTreeMap<u128, usize> = node_ids
            .iter()
            .copied()
            .enumerate()
            .map(|(i, id)| (id, i))
            .collect();
        let mut summaries = Vec::with_capacity(remapped.len());
        for (cid, mut members) in remapped {
            members.sort_unstable();
            for &nid in &members {
                if let Some(node) = self.nodes.get_mut(&nid) {
                    node.community_id = Some(cid);
                }
            }
            let indices: Vec<usize> = members
                .iter()
                .filter_map(|nid| idx_by_id.get(nid).copied())
                .collect();
            let internal_cohesion = subgraph_cohesion(&indices, &adj);
            let conductance = subgraph_conductance(&indices, &adj);
            summaries.push(CommunitySummary {
                community_id: cid,
                node_ids: members,
                internal_cohesion,
                conductance,
            });
        }
        summaries.sort_by_key(|s| s.community_id);
        summaries
    }

    pub fn god_nodes(&self, top_k: usize) -> Vec<GodNodeEntry> {
        if top_k == 0 {
            return Vec::new();
        }
        let mut entries = Vec::new();
        let mut weighted_deg: BTreeMap<u128, f64> = BTreeMap::new();
        let mut incident_edges: BTreeMap<u128, usize> = BTreeMap::new();
        let mut neighbor_comms: BTreeMap<u128, BTreeSet<u32>> = BTreeMap::new();

        for edge in self.hyperedges.values() {
            if edge.invalidated_at_ms.is_some() {
                continue;
            }
            let w = edge.effective_weight() as f64;
            let all_endpoints: Vec<u128> = edge
                .sources
                .iter()
                .chain(edge.targets.iter())
                .copied()
                .collect();
            for &u in &all_endpoints {
                *weighted_deg.entry(u).or_insert(0.0) += w;
                *incident_edges.entry(u).or_insert(0) += 1;
                for &v in &all_endpoints {
                    if u != v {
                        if let Some(comm_v) = self.nodes.get(&v).and_then(|n| n.community_id) {
                            neighbor_comms.entry(u).or_default().insert(comm_v);
                        }
                    }
                }
            }
        }

        for node in self.nodes.values() {
            if node.is_synthetic || matches!(node.kind, HyperNodeKind::File | HyperNodeKind::Module)
            {
                continue;
            }
            let deg = weighted_deg.get(&node.node_id).copied().unwrap_or(0.0);
            let inc = incident_edges.get(&node.node_id).copied().unwrap_or(0);
            let bridged = neighbor_comms
                .get(&node.node_id)
                .map(|set| {
                    if let Some(own) = node.community_id {
                        set.iter().filter(|&&c| c != own).count()
                    } else {
                        set.len()
                    }
                })
                .unwrap_or(0);
            let hub_score = deg * (1.0 + 0.35 * (bridged as f64)) + 0.25 * (inc as f64);
            entries.push(GodNodeEntry {
                node_id: node.node_id,
                label: node.label.clone(),
                kind: node.kind,
                weighted_degree: deg,
                bridged_communities: bridged,
                incident_hyperedges: inc,
                hub_score,
            });
        }

        entries.sort_by(|a, b| {
            b.hub_score
                .total_cmp(&a.hub_score)
                .then_with(|| a.node_id.cmp(&b.node_id))
        });
        entries.truncate(top_k);
        entries
    }

    pub fn surprising_connections(&self, top_k: usize) -> Vec<SurprisingConnectionEntry> {
        if top_k == 0 {
            return Vec::new();
        }
        let degrees = self.node_incident_degrees();
        let mut neighbors_by_node: BTreeMap<u128, BTreeSet<u128>> = BTreeMap::new();
        for edge in self.hyperedges.values() {
            if edge.invalidated_at_ms.is_some() {
                continue;
            }
            for &s in &edge.sources {
                for &t in &edge.targets {
                    if s != t {
                        neighbors_by_node.entry(s).or_default().insert(t);
                        neighbors_by_node.entry(t).or_default().insert(s);
                    }
                }
            }
        }

        let mut results = Vec::new();
        for edge in self.hyperedges.values() {
            if edge.invalidated_at_ms.is_some()
                || matches!(
                    edge.relation.as_str(),
                    "contains" | "defines" | "has_method"
                )
            {
                continue;
            }
            for &s in &edge.sources {
                for &t in &edge.targets {
                    if s == t {
                        continue;
                    }
                    let (Some(ns), Some(nt)) = (self.nodes.get(&s), self.nodes.get(&t)) else {
                        continue;
                    };
                    if ns.is_synthetic
                        || nt.is_synthetic
                        || matches!(ns.kind, HyperNodeKind::File | HyperNodeKind::Module)
                        || matches!(nt.kind, HyperNodeKind::File | HyperNodeKind::Module)
                    {
                        continue;
                    }
                    let cross_community = match (ns.community_id, nt.community_id) {
                        (Some(ca), Some(cb)) => ca != cb,
                        _ => true,
                    };
                    if !cross_community {
                        continue;
                    }
                    let common = match (neighbors_by_node.get(&s), neighbors_by_node.get(&t)) {
                        (Some(na), Some(nb)) => na.intersection(nb).count(),
                        _ => 0,
                    };
                    let deg_s = degrees.get(&s).copied().unwrap_or(1) as f64;
                    let deg_t = degrees.get(&t).copied().unwrap_or(1) as f64;
                    let prov_novelty = match edge.provenance {
                        EdgeProvenance::Inferred => 1.35,
                        EdgeProvenance::Ambiguous => 1.15,
                        EdgeProvenance::Extracted => 1.0,
                    };
                    let surprise_score = prov_novelty
                        * (1.0 + deg_s).ln()
                        * (1.0 + deg_t).ln()
                        * (edge.confidence.max(0.2) as f64)
                        / (1.0 + (common as f64));
                    results.push(SurprisingConnectionEntry {
                        hyperedge_id: edge.hyperedge_id,
                        from_node: s,
                        to_node: t,
                        relation: edge.relation.clone(),
                        provenance: edge.provenance,
                        from_community: ns.community_id,
                        to_community: nt.community_id,
                        surprise_score,
                    });
                }
            }
        }

        results.sort_by(|a, b| {
            b.surprise_score
                .total_cmp(&a.surprise_score)
                .then_with(|| a.hyperedge_id.cmp(&b.hyperedge_id))
        });
        results.truncate(top_k);
        results
    }

    pub fn graph_diff(&self, newer: &MemoryHypergraph) -> HypergraphDiff {
        let mut added_nodes = Vec::new();
        let mut removed_nodes = Vec::new();
        let mut modified_nodes = Vec::new();

        for (&id, new_node) in &newer.nodes {
            match self.nodes.get(&id) {
                None => added_nodes.push(id),
                Some(old_node) => {
                    if old_node.label != new_node.label
                        || old_node.ast_fingerprint != new_node.ast_fingerprint
                        || old_node.code_fingerprint != new_node.code_fingerprint
                        || old_node.summary != new_node.summary
                    {
                        modified_nodes.push(id);
                    }
                }
            }
        }
        for &id in self.nodes.keys() {
            if !newer.nodes.contains_key(&id) {
                removed_nodes.push(id);
            }
        }

        let mut added_edges = Vec::new();
        let mut removed_edges = Vec::new();
        let mut invalidated_edges = Vec::new();
        for (&eid, new_edge) in &newer.hyperedges {
            match self.hyperedges.get(&eid) {
                None => added_edges.push(eid),
                Some(old_edge) => {
                    if old_edge.invalidated_at_ms.is_none() && new_edge.invalidated_at_ms.is_some()
                    {
                        invalidated_edges.push(eid);
                    }
                }
            }
        }
        for &eid in self.hyperedges.keys() {
            if !newer.hyperedges.contains_key(&eid) {
                removed_edges.push(eid);
            }
        }

        let shared_ids: Vec<u128> = self
            .nodes
            .keys()
            .filter(|id| newer.nodes.contains_key(id))
            .copied()
            .collect();
        let mut same_pair = 0usize;
        let mut union_pair = 0usize;
        let sample_cap = shared_ids.len().min(512);
        for i in 0..sample_cap {
            for j in (i + 1)..sample_cap {
                let old_same = self.nodes[&shared_ids[i]].community_id.is_some()
                    && self.nodes[&shared_ids[i]].community_id
                        == self.nodes[&shared_ids[j]].community_id;
                let new_same = newer.nodes[&shared_ids[i]].community_id.is_some()
                    && newer.nodes[&shared_ids[i]].community_id
                        == newer.nodes[&shared_ids[j]].community_id;
                if old_same || new_same {
                    union_pair += 1;
                    if old_same && new_same {
                        same_pair += 1;
                    }
                }
            }
        }
        let community_stability_jaccard = if union_pair == 0 {
            1.0
        } else {
            (same_pair as f64) / (union_pair as f64)
        };

        let mut hasher = Hasher::new();
        hasher.update(b"aegis-hypergraph-diff-v1");
        for list in [
            &added_nodes,
            &removed_nodes,
            &modified_nodes,
            &added_edges,
            &removed_edges,
            &invalidated_edges,
        ] {
            hasher.update(&(list.len() as u64).to_le_bytes());
            for &id in list {
                hasher.update(&id.to_le_bytes());
            }
        }
        let diff_digest = hasher.finalize().to_hex().to_string();

        HypergraphDiff {
            added_nodes,
            removed_nodes,
            modified_nodes,
            added_edges,
            removed_edges,
            invalidated_edges,
            community_stability_jaccard,
            diff_digest,
        }
    }

    /// Repo-relative blast-radius analysis (`affected_nodes`) propagating from changed files.
    pub fn affected_nodes(
        &self,
        changed_repo_paths: &[String],
        max_hops: u8,
        at_timestamp_ms: Option<u64>,
    ) -> Vec<AffectedNodeEntry> {
        let normalized_targets: BTreeSet<String> = changed_repo_paths
            .iter()
            .map(|p| normalize_repo_relative_path(p))
            .filter(|p| !p.is_empty())
            .collect();
        if normalized_targets.is_empty() {
            return Vec::new();
        }

        let mut best_impact: BTreeMap<u128, (u8, f64, String, EdgeProvenance)> = BTreeMap::new();
        let mut frontier: Vec<(u128, f64)> = Vec::new();

        for node in self.nodes.values() {
            let direct_hit = node
                .normalized_source_file()
                .is_some_and(|sf| normalized_targets.contains(&sf))
                || (node.kind == HyperNodeKind::File
                    && normalized_targets.contains(&normalize_repo_relative_path(&node.label)));
            if direct_hit {
                best_impact.insert(
                    node.node_id,
                    (
                        0,
                        1.0,
                        "direct_file_anchor".to_owned(),
                        EdgeProvenance::Extracted,
                    ),
                );
                frontier.push((node.node_id, 1.0));
            }
        }

        // Build bidirectional dependency/impact adjacency over active hyperedges
        let mut impact_adj: BTreeMap<u128, Vec<(u128, f64, String, EdgeProvenance)>> =
            BTreeMap::new();
        for edge in self.hyperedges.values() {
            if !edge.is_active_at(at_timestamp_ms) {
                continue;
            }
            let mult = edge.effective_weight() as f64;
            if mult <= 0.0 {
                continue;
            }
            for &s in &edge.sources {
                for &t in &edge.targets {
                    if s == t {
                        continue;
                    }
                    // If `t` is depended on by `s` (e.g. `s calls t`, `s imports t`, `memory relates_to file`),
                    // changing `t` impacts `s`, and changing `s` also propagates structural coupling to `t`.
                    impact_adj.entry(t).or_default().push((
                        s,
                        mult * 0.85,
                        edge.relation.clone(),
                        edge.provenance,
                    ));
                    impact_adj.entry(s).or_default().push((
                        t,
                        mult * 0.65,
                        edge.relation.clone(),
                        edge.provenance,
                    ));
                }
            }
        }

        let hop_cap = max_hops.clamp(1, 6);
        for hop in 1..=hop_cap {
            let mut next_frontier: BTreeMap<u128, f64> = BTreeMap::new();
            for &(curr_id, curr_score) in &frontier {
                let Some(neighbors) = impact_adj.get(&curr_id) else {
                    continue;
                };
                for (nbr_id, edge_mult, rel, prov) in neighbors {
                    let cand_score = curr_score * (*edge_mult);
                    if cand_score < 0.05 {
                        continue;
                    }
                    let update = match best_impact.get(nbr_id) {
                        None => true,
                        Some((prev_hop, prev_score, _, _)) => {
                            cand_score > *prev_score + 1e-9
                                || ((cand_score - *prev_score).abs() <= 1e-9 && hop < *prev_hop)
                        }
                    };
                    if update {
                        best_impact.insert(*nbr_id, (hop, cand_score, rel.clone(), *prov));
                        let entry = next_frontier.entry(*nbr_id).or_insert(0.0);
                        if cand_score > *entry {
                            *entry = cand_score;
                        }
                    }
                }
            }
            if next_frontier.is_empty() {
                break;
            }
            frontier = next_frontier.into_iter().collect();
        }

        let mut results: Vec<AffectedNodeEntry> = best_impact
            .into_iter()
            .filter_map(|(nid, (hop, score, rel, prov))| {
                let node = self.nodes.get(&nid)?;
                Some(AffectedNodeEntry {
                    node_id: nid,
                    label: node.label.clone(),
                    kind: node.kind,
                    source_file: node.normalized_source_file(),
                    hop_distance: hop,
                    impact_score: score,
                    via_relation: rel,
                    via_provenance: prov,
                })
            })
            .collect();
        results.sort_by(|a, b| {
            b.impact_score
                .total_cmp(&a.impact_score)
                .then_with(|| a.hop_distance.cmp(&b.hop_distance))
                .then_with(|| a.node_id.cmp(&b.node_id))
        });
        results
    }

    /// Hybrid 3-tier retrieval: Okapi BM25 + `i8`-quantized vector cosine + Multi-Seed
    /// Personalized PageRank + `reflect` experiential half-life weighting.
    pub fn hybrid_retrieve(
        &self,
        query: &str,
        top_k: usize,
        at_timestamp_ms: Option<u64>,
        ablation: RetrievalAblationConfig,
    ) -> Vec<HybridRetrievalHit> {
        if top_k == 0 || query.trim().is_empty() || self.nodes.is_empty() {
            return Vec::new();
        }

        let node_ids: Vec<u128> = self.nodes.keys().copied().collect();
        let n = node_ids.len();
        let id_to_idx: BTreeMap<u128, usize> = node_ids
            .iter()
            .copied()
            .enumerate()
            .map(|(i, id)| (id, i))
            .collect();

        // 1. Okapi BM25 lexical tier (`k1 = 1.2, b = 0.75`)
        let mut bm25_scores = vec![0.0f64; n];
        if ablation.enable_bm25 {
            let query_norm = normalize_entity_label(query);
            let query_terms: Vec<&str> = query_norm.split_whitespace().collect();
            if !query_terms.is_empty() {
                let mut doc_term_maps: Vec<BTreeMap<String, usize>> = Vec::with_capacity(n);
                let mut doc_lens: Vec<usize> = Vec::with_capacity(n);
                let mut df: BTreeMap<String, usize> = BTreeMap::new();

                for &nid in &node_ids {
                    let node = &self.nodes[&nid];
                    let doc_norm = normalize_entity_label(&format!(
                        "{} {} {}",
                        node.label, node.label, node.summary
                    ));
                    let mut tf: BTreeMap<String, usize> = BTreeMap::new();
                    let mut dlen = 0usize;
                    for tok in doc_norm.split_whitespace() {
                        *tf.entry(tok.to_owned()).or_insert(0) += 1;
                        dlen += 1;
                    }
                    for unique_tok in tf.keys() {
                        *df.entry(unique_tok.clone()).or_insert(0) += 1;
                    }
                    doc_term_maps.push(tf);
                    doc_lens.push(dlen.max(1));
                }

                let avgdl = (doc_lens.iter().sum::<usize>() as f64 / (n as f64)).max(1.0);
                let k1 = 1.2f64;
                let b = 0.75f64;
                for (i, tf_map) in doc_term_maps.iter().enumerate() {
                    let dl = doc_lens[i] as f64;
                    let mut raw = 0.0f64;
                    for &qterm in &query_terms {
                        let freq = tf_map.get(qterm).copied().unwrap_or(0) as f64;
                        if freq <= 0.0 {
                            continue;
                        }
                        let doc_freq = df.get(qterm).copied().unwrap_or(0) as f64;
                        let idf = (((n as f64) - doc_freq + 0.5) / (doc_freq + 0.5) + 1.0).ln();
                        let denom = freq + k1 * (1.0 - b + b * (dl / avgdl));
                        raw += idf * ((freq * (k1 + 1.0)) / denom);
                    }
                    bm25_scores[i] = raw;
                }
                let max_bm25 = bm25_scores.iter().copied().fold(0.0f64, f64::max);
                if max_bm25 > 0.0 {
                    for s in &mut bm25_scores {
                        *s /= max_bm25;
                    }
                }
            }
        }

        // 2. Cold `i8`-quantized dense vector tier
        let mut vector_scores = vec![0.0f64; n];
        if ablation.enable_vector {
            let query_vec = deterministic_quantized_embedding(query);
            for (i, &nid) in node_ids.iter().enumerate() {
                if let Some(ref emb) = self.nodes[&nid].quantized_embedding {
                    let sim = quantized_cosine_similarity(&query_vec, emb);
                    vector_scores[i] = sim.max(0.0);
                }
            }
            let max_vec = vector_scores.iter().copied().fold(0.0f64, f64::max);
            if max_vec > 0.0 {
                for s in &mut vector_scores {
                    *s /= max_vec;
                }
            }
        }

        // 3. Multi-seed Personalized PageRank over temporally-valid provenance-weighted hyperedges
        let mut ppr_scores = vec![0.0f64; n];
        if ablation.enable_ppr {
            let mut seed_mass = vec![0.0f64; n];
            for i in 0..n {
                let seed_signal = if ablation.enable_bm25 && ablation.enable_vector {
                    0.65 * bm25_scores[i] + 0.35 * vector_scores[i]
                } else if ablation.enable_bm25 {
                    bm25_scores[i]
                } else if ablation.enable_vector {
                    vector_scores[i]
                } else {
                    1.0 / (n as f64)
                };
                if seed_signal > 0.05 {
                    seed_mass[i] = seed_signal;
                }
            }
            let seed_sum: f64 = seed_mass.iter().sum();
            if seed_sum > 0.0 {
                for m in &mut seed_mass {
                    *m /= seed_sum;
                }
                // Build transition list
                let mut out_edges: Vec<Vec<(usize, f64)>> = vec![Vec::new(); n];
                let mut out_sum = vec![0.0f64; n];
                for edge in self.hyperedges.values() {
                    if !edge.is_active_at(at_timestamp_ms) {
                        continue;
                    }
                    let w = edge.effective_weight() as f64;
                    if w <= 0.0 {
                        continue;
                    }
                    for &s in &edge.sources {
                        for &t in &edge.targets {
                            if s == t {
                                continue;
                            }
                            if let (Some(&u), Some(&v)) = (id_to_idx.get(&s), id_to_idx.get(&t)) {
                                out_edges[u].push((v, w));
                                out_sum[u] += w;
                                out_edges[v].push((u, w * 0.85));
                                out_sum[v] += w * 0.85;
                            }
                        }
                    }
                }

                let alpha = 0.85f64;
                let mut rank = seed_mass.clone();
                let mut next_rank = vec![0.0f64; n];
                for _iter in 0..20 {
                    let mut dangling_mass = 0.0f64;
                    for u in 0..n {
                        if out_sum[u] <= 0.0 {
                            dangling_mass += rank[u];
                        }
                    }
                    for i in 0..n {
                        next_rank[i] = (1.0 - alpha + alpha * dangling_mass) * seed_mass[i];
                    }
                    for u in 0..n {
                        if out_sum[u] <= 0.0 || rank[u] <= 0.0 {
                            continue;
                        }
                        let scale = alpha * rank[u] / out_sum[u];
                        for &(v, w) in &out_edges[u] {
                            next_rank[v] += scale * w;
                        }
                    }
                    let diff: f64 = rank
                        .iter()
                        .zip(next_rank.iter())
                        .map(|(a, b)| (a - b).abs())
                        .sum();
                    std::mem::swap(&mut rank, &mut next_rank);
                    if diff < 1e-6 {
                        break;
                    }
                }
                let max_ppr = rank.iter().copied().fold(0.0f64, f64::max);
                if max_ppr > 0.0 {
                    for (i, r) in rank.into_iter().enumerate() {
                        ppr_scores[i] = r / max_ppr;
                    }
                }
            }
        }

        // 4. Fuse tiers + apply `reflect` experiential multiplier
        let now_ms = at_timestamp_ms.unwrap_or(1_700_000_000_000);
        let mut hits = Vec::new();
        for (i, &nid) in node_ids.iter().enumerate() {
            let node = &self.nodes[&nid];
            if node.is_synthetic {
                continue;
            }
            let b = bm25_scores[i];
            let v = vector_scores[i];
            let p = ppr_scores[i];
            let active_tier_count =
                usize::from(b > 0.01) + usize::from(v > 0.01) + usize::from(p > 0.01);
            if active_tier_count == 0 {
                continue;
            }

            let raw_fused = match (
                ablation.enable_bm25,
                ablation.enable_vector,
                ablation.enable_ppr,
            ) {
                (true, true, true) => 0.45 * b + 0.25 * v + 0.30 * p,
                (true, true, false) => 0.65 * b + 0.35 * v,
                (true, false, true) => 0.60 * b + 0.40 * p,
                (false, true, true) => 0.55 * v + 0.45 * p,
                (true, false, false) => b,
                (false, true, false) => v,
                (false, false, true) => p,
                (false, false, false) => 0.0,
            };

            let reflect_verdict = self.evaluate_node_reflect(nid, now_ms);
            let fused_score = if ablation.enable_reflect {
                let multiplier = match reflect_verdict.disposition {
                    ReflectDisposition::Corroborated => {
                        1.0 + 0.35 * (reflect_verdict.decayed_boost as f64)
                    }
                    ReflectDisposition::NeutralUnverified => 1.0,
                    ReflectDisposition::Contested => 0.72,
                    ReflectDisposition::Deprecated => {
                        (reflect_verdict.effective_score as f64).clamp(0.15, 0.65)
                    }
                    ReflectDisposition::StaleCodeFingerprint => 0.25,
                };
                raw_fused * multiplier
            } else {
                raw_fused
            };

            let origin_tier = if active_tier_count >= 2 {
                RetrievalTierOrigin::HybridFused
            } else if b >= v && b >= p {
                RetrievalTierOrigin::HotLexicalBm25
            } else if p >= v {
                RetrievalTierOrigin::WarmGraphPpr
            } else {
                RetrievalTierOrigin::ColdVectorQuantized
            };

            hits.push(HybridRetrievalHit {
                node_id: nid,
                label: node.label.clone(),
                kind: node.kind,
                origin_tier,
                fused_score,
                bm25_score: b,
                vector_score: v,
                ppr_score: p,
                reflect_disposition: reflect_verdict.disposition,
            });
        }

        hits.sort_by(|a, b| {
            b.fused_score
                .total_cmp(&a.fused_score)
                .then_with(|| a.node_id.cmp(&b.node_id))
        });
        hits.truncate(top_k);
        hits
    }
}

fn subgraph_cohesion(members: &[usize], adj: &[Vec<(usize, f64)>]) -> f64 {
    let k = members.len();
    if k <= 1 {
        return 1.0;
    }
    let set: BTreeSet<usize> = members.iter().copied().collect();
    let mut internal_edges = 0usize;
    for &u in members {
        for &(v, w) in &adj[u] {
            if v > u && w > 0.0 && set.contains(&v) {
                internal_edges += 1;
            }
        }
    }
    let max_possible = k * (k - 1) / 2;
    (internal_edges as f64) / (max_possible as f64)
}

fn subgraph_conductance(members: &[usize], adj: &[Vec<(usize, f64)>]) -> f64 {
    if members.is_empty() {
        return 0.0;
    }
    let set: BTreeSet<usize> = members.iter().copied().collect();
    let mut cut = 0.0f64;
    let mut vol = 0.0f64;
    for &u in members {
        for &(v, w) in &adj[u] {
            vol += w;
            if !set.contains(&v) {
                cut += w;
            }
        }
    }
    if vol <= 0.0 {
        0.0
    } else {
        (cut / vol).clamp(0.0, 1.0)
    }
}

fn deterministic_bisect(members: &[usize], adj: &[Vec<(usize, f64)>]) -> (Vec<usize>, Vec<usize>) {
    if members.len() < 2 {
        return (members.to_vec(), Vec::new());
    }
    let set: BTreeSet<usize> = members.iter().copied().collect();
    // Seed A = first member, Seed B = farthest member in induced subgraph
    let seed_a = members[0];
    let mut dist: BTreeMap<usize, usize> = BTreeMap::new();
    let mut queue = VecDeque::new();
    dist.insert(seed_a, 0);
    queue.push_back(seed_a);
    while let Some(u) = queue.pop_front() {
        let du = dist[&u];
        for &(v, _) in &adj[u] {
            if set.contains(&v) && !dist.contains_key(&v) {
                dist.insert(v, du + 1);
                queue.push_back(v);
            }
        }
    }
    let seed_b = *members
        .iter()
        .max_by_key(|&&u| (dist.get(&u).copied().unwrap_or(usize::MAX), u))
        .unwrap_or(&members[members.len() - 1]);
    if seed_a == seed_b {
        let mid = members.len() / 2;
        return (members[..mid].to_vec(), members[mid..].to_vec());
    }

    let mut side_a: BTreeSet<usize> = BTreeSet::from([seed_a]);
    let mut side_b: BTreeSet<usize> = BTreeSet::from([seed_b]);
    for &u in members {
        if u == seed_a || u == seed_b {
            continue;
        }
        let wa: f64 = adj[u]
            .iter()
            .filter(|(v, _)| side_a.contains(v))
            .map(|(_, w)| *w)
            .sum();
        let wb: f64 = adj[u]
            .iter()
            .filter(|(v, _)| side_b.contains(v))
            .map(|(_, w)| *w)
            .sum();
        if wa > wb || ((wa - wb).abs() <= 1e-12 && side_a.len() <= side_b.len()) {
            side_a.insert(u);
        } else {
            side_b.insert(u);
        }
    }
    (side_a.into_iter().collect(), side_b.into_iter().collect())
}

/// Greedy maximum-Jaccard community ID stabilization across incremental graph builds.
pub fn remap_communities_to_previous(
    previous_node_to_comm: &BTreeMap<u128, u32>,
    current_communities: &BTreeMap<u32, Vec<u128>>,
) -> BTreeMap<u32, Vec<u128>> {
    if previous_node_to_comm.is_empty() {
        return current_communities.clone();
    }
    let mut prev_sets: BTreeMap<u32, BTreeSet<u128>> = BTreeMap::new();
    for (&nid, &cid) in previous_node_to_comm {
        prev_sets.entry(cid).or_default().insert(nid);
    }
    let curr_sets: BTreeMap<u32, BTreeSet<u128>> = current_communities
        .iter()
        .map(|(&cid, nodes)| (cid, nodes.iter().copied().collect()))
        .collect();

    let mut candidates: Vec<(f64, usize, u32, u32)> = Vec::new();
    for (&prev_id, pset) in &prev_sets {
        for (&curr_id, cset) in &curr_sets {
            let inter = pset.intersection(cset).count();
            if inter == 0 {
                continue;
            }
            let union = pset.union(cset).count().max(1);
            let jaccard = (inter as f64) / (union as f64);
            candidates.push((jaccard, inter, prev_id, curr_id));
        }
    }

    candidates.sort_by(|a, b| {
        b.0.total_cmp(&a.0)
            .then_with(|| b.1.cmp(&a.1))
            .then_with(|| a.2.cmp(&b.2))
            .then_with(|| a.3.cmp(&b.3))
    });

    let mut used_prev: BTreeSet<u32> = BTreeSet::new();
    let mut assigned_curr: BTreeMap<u32, u32> = BTreeMap::new();
    for (_, _, prev_id, curr_id) in candidates {
        if used_prev.contains(&prev_id) || assigned_curr.contains_key(&curr_id) {
            continue;
        }
        used_prev.insert(prev_id);
        assigned_curr.insert(curr_id, prev_id);
    }

    let mut next_fresh_id = prev_sets.keys().copied().max().unwrap_or(0) + 1;
    let mut remapped: BTreeMap<u32, Vec<u128>> = BTreeMap::new();
    for (&curr_id, members) in current_communities {
        let target_id = if let Some(&pid) = assigned_curr.get(&curr_id) {
            pid
        } else {
            while used_prev.contains(&next_fresh_id) {
                next_fresh_id += 1;
            }
            let id = next_fresh_id;
            used_prev.insert(id);
            next_fresh_id += 1;
            id
        };
        remapped.insert(target_id, members.clone());
    }
    remapped
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_guards_block_false_merges_and_merge_true_aliases() {
        let mut hg = MemoryHypergraph::new();

        // True alias pair: ContextGovernor vs context_governor (same concept, high entropy)
        let mut n1 = HyperNode::new(1, "ContextGovernorSelector", HyperNodeKind::Concept);
        n1.lifecycle_rank = 3;
        n1.ast_fingerprint = 42;
        let n2 = HyperNode::new(2, "context_governor_selector", HyperNodeKind::Concept);
        hg.upsert_node(n1).unwrap();
        hg.upsert_node(n2).unwrap();

        // Numeric divergence: protocol_schema_v1 vs protocol_schema_v2 -> MUST NOT MERGE
        hg.upsert_node(HyperNode::new(
            3,
            "protocol_schema_version_1",
            HyperNodeKind::Concept,
        ))
        .unwrap();
        hg.upsert_node(HyperNode::new(
            4,
            "protocol_schema_version_2",
            HyperNodeKind::Concept,
        ))
        .unwrap();

        // Variant pair: serialize_frame_payload vs deserialize_frame_payload -> MUST NOT MERGE
        hg.upsert_node(HyperNode::new(
            5,
            "serialize_frame_payload",
            HyperNodeKind::Concept,
        ))
        .unwrap();
        hg.upsert_node(HyperNode::new(
            6,
            "deserialize_frame_payload",
            HyperNodeKind::Concept,
        ))
        .unwrap();

        // Token swap: file_to_memory_relation vs memory_to_file_relation -> MUST NOT MERGE
        hg.upsert_node(HyperNode::new(
            7,
            "file_to_memory_relation",
            HyperNodeKind::Concept,
        ))
        .unwrap();
        hg.upsert_node(HyperNode::new(
            8,
            "memory_to_file_relation",
            HyperNodeKind::Concept,
        ))
        .unwrap();

        // Cross-file file-anchored symbols with identical name -> MUST NOT MERGE
        let mut s1 = HyperNode::new(9, "validate_frame", HyperNodeKind::Symbol);
        s1.source_file = Some("core/rust/src/ipc.rs".to_owned());
        let mut s2 = HyperNode::new(10, "validate_frame", HyperNodeKind::Symbol);
        s2.source_file = Some("core/rust/src/shm.rs".to_owned());
        hg.upsert_node(s1).unwrap();
        hg.upsert_node(s2).unwrap();

        let report = hg.deduplicate_entities(DedupGuardConfig::default());
        assert_eq!(report.canonical_remap[&1], 1);
        assert_eq!(report.canonical_remap[&2], 1); // merged into higher-lifecycle AST node 1!
        assert_ne!(report.canonical_remap[&3], report.canonical_remap[&4]);
        assert_ne!(report.canonical_remap[&5], report.canonical_remap[&6]);
        assert_ne!(report.canonical_remap[&7], report.canonical_remap[&8]);
        assert_ne!(report.canonical_remap[&9], report.canonical_remap[&10]);
    }

    #[test]
    fn unicode_combining_marks_and_diacritics_are_preserved() {
        // Decomposed NFD Vietnamese combining marks must normalize cleanly without stripping diacritics
        let a = normalize_entity_label("ha\u{0300}m_ki\u{0309}em_tra");
        let b = normalize_entity_label("ha\u{0323}n_ki\u{0309}em_tra");
        assert_ne!(a, b);
    }

    #[test]
    fn leiden_clustering_jaccard_remap_and_hybrid_ppr_reflect_work() {
        let mut hg = MemoryHypergraph::new();
        for id in 1..=6 {
            let mut node = HyperNode::new(
                id,
                format!("hypergraph_module_component_{id}"),
                HyperNodeKind::Symbol,
            );
            node.source_file = Some(if id <= 3 {
                "core/rust/src/memory/frame.rs".to_owned()
            } else {
                "core/rust/src/ipc.rs".to_owned()
            });
            node.code_fingerprint = Some(0xABCD);
            hg.upsert_node(node).unwrap();
        }
        hg.nodes.get_mut(&2).unwrap().summary =
            "multi-hop Personalized PageRank memory retrieval bridge".to_owned();
        // Clique 1..=3 and Clique 4..=6 with one inferred cross-community bridge 3 -> 4
        let mut eid = 1u128;
        for (u, v) in [(1, 2), (2, 3), (1, 3), (4, 5), (5, 6), (4, 6)] {
            hg.upsert_hyperedge(HyperEdge::binary(
                eid,
                u,
                v,
                "calls",
                EdgeProvenance::Extracted,
                1.0,
                1.0,
                100,
            ))
            .unwrap();
            eid += 1;
        }
        hg.upsert_hyperedge(HyperEdge::binary(
            eid,
            3,
            4,
            "semantic_bridge",
            EdgeProvenance::Inferred,
            0.85,
            0.8,
            100,
        ))
        .unwrap();

        let summaries = hg.detect_communities_leiden(16, None);
        assert_eq!(summaries.len(), 2);
        let prev_map: BTreeMap<u128, u32> = hg
            .nodes()
            .iter()
            .filter_map(|(&id, n)| n.community_id.map(|c| (id, c + 40)))
            .collect();
        let remapped = hg.detect_communities_leiden(16, Some(&prev_map));
        assert_eq!(remapped.len(), 2);
        for (&nid, &expected_cid) in &prev_map {
            assert_eq!(hg.get_node(nid).unwrap().community_id, Some(expected_cid));
        }

        let surprises = hg.surprising_connections(3);
        assert!(!surprises.is_empty());
        assert_eq!(surprises[0].relation, "semantic_bridge");

        let blast = hg.affected_nodes(
            &["core\\rust\\src\\memory\\frame.rs".to_owned()],
            2,
            Some(200),
        );
        assert!(
            blast
                .iter()
                .any(|entry| entry.node_id == 4 && entry.hop_distance >= 1)
        );

        // Corroborate node 2 twice (>= MIN_CORROBORATION)
        hg.record_reflect_outcome(2, true, 1_000, Some(0xABCD))
            .unwrap();
        let verdict = hg
            .record_reflect_outcome(2, true, 2_000, Some(0xABCD))
            .unwrap();
        assert_eq!(verdict.disposition, ReflectDisposition::Corroborated);

        let hits = hg.hybrid_retrieve(
            "Personalized PageRank memory retrieval",
            5,
            Some(2_500),
            RetrievalAblationConfig::default(),
        );
        assert!(!hits.is_empty());
        assert_eq!(hits[0].node_id, 2);
        // Neighboring nodes 1 and 3 get WarmGraphPpr activation even without query terms!
        assert!(hits.iter().any(|h| h.node_id == 1 || h.node_id == 3));
    }
}
