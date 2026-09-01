use parking_lot::RwLock;
use proc_macro2::{Delimiter, TokenStream, TokenTree};
use serde::{Deserialize, Serialize};
use std::borrow::Cow;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};
use tracing::{error, info, instrument, warn};

static ACCEPTED_ARTIFACTS: AtomicU64 = AtomicU64::new(0);
static REJECTED_ARTIFACTS: AtomicU64 = AtomicU64::new(0);
static HARD_BACKTRACKS: AtomicU64 = AtomicU64::new(0);
static CONSENSUS_COMMITS: AtomicU64 = AtomicU64::new(0);
static CONSENSUS_FAILURES: AtomicU64 = AtomicU64::new(0);
static TOTAL_FUEL_CONSUMED: AtomicU64 = AtomicU64::new(0);
static LAST_PAV_BITS: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PhysicalMetricsSnapshot {
    pub accepted_artifacts: u64,
    pub rejected_artifacts: u64,
    pub hard_backtracks: u64,
    pub consensus_commits: u64,
    pub consensus_failures: u64,
    pub total_fuel_consumed: u64,
    pub last_pav: f64,
}

pub fn physical_metrics_snapshot() -> PhysicalMetricsSnapshot {
    PhysicalMetricsSnapshot {
        accepted_artifacts: ACCEPTED_ARTIFACTS.load(Ordering::Relaxed),
        rejected_artifacts: REJECTED_ARTIFACTS.load(Ordering::Relaxed),
        hard_backtracks: HARD_BACKTRACKS.load(Ordering::Relaxed),
        consensus_commits: CONSENSUS_COMMITS.load(Ordering::Relaxed),
        consensus_failures: CONSENSUS_FAILURES.load(Ordering::Relaxed),
        total_fuel_consumed: TOTAL_FUEL_CONSUMED.load(Ordering::Relaxed),
        last_pav: f64::from_bits(LAST_PAV_BITS.load(Ordering::Relaxed)),
    }
}

pub fn physical_metrics_prometheus() -> String {
    let snapshot = physical_metrics_snapshot();
    format!(
        concat!(
            "# HELP aegis_physical_accepted_artifacts_total Physical artifacts accepted by the PAV watchdog.\n",
            "# TYPE aegis_physical_accepted_artifacts_total counter\n",
            "aegis_physical_accepted_artifacts_total {}\n",
            "# HELP aegis_physical_rejected_artifacts_total Physical artifacts rejected by the PAV watchdog.\n",
            "# TYPE aegis_physical_rejected_artifacts_total counter\n",
            "aegis_physical_rejected_artifacts_total {}\n",
            "# HELP aegis_physical_hard_backtracks_total Hard backtrack signals emitted by deterministic control.\n",
            "# TYPE aegis_physical_hard_backtracks_total counter\n",
            "aegis_physical_hard_backtracks_total {}\n",
            "# HELP aegis_physical_consensus_commits_total Physical consensus commits.\n",
            "# TYPE aegis_physical_consensus_commits_total counter\n",
            "aegis_physical_consensus_commits_total {}\n",
            "# HELP aegis_physical_consensus_failures_total Physical consensus failures.\n",
            "# TYPE aegis_physical_consensus_failures_total counter\n",
            "aegis_physical_consensus_failures_total {}\n",
            "# HELP aegis_physical_fuel_consumed_total Fuel consumed by accepted physical artifacts.\n",
            "# TYPE aegis_physical_fuel_consumed_total counter\n",
            "aegis_physical_fuel_consumed_total {}\n",
            "# HELP aegis_physical_last_pav Last observed Physical Artifact Velocity value.\n",
            "# TYPE aegis_physical_last_pav gauge\n",
            "aegis_physical_last_pav {:.12}\n"
        ),
        snapshot.accepted_artifacts,
        snapshot.rejected_artifacts,
        snapshot.hard_backtracks,
        snapshot.consensus_commits,
        snapshot.consensus_failures,
        snapshot.total_fuel_consumed,
        snapshot.last_pav,
    )
}

#[cfg(test)]
pub fn reset_physical_metrics_for_tests() {
    ACCEPTED_ARTIFACTS.store(0, Ordering::Relaxed);
    REJECTED_ARTIFACTS.store(0, Ordering::Relaxed);
    HARD_BACKTRACKS.store(0, Ordering::Relaxed);
    CONSENSUS_COMMITS.store(0, Ordering::Relaxed);
    CONSENSUS_FAILURES.store(0, Ordering::Relaxed);
    TOTAL_FUEL_CONSUMED.store(0, Ordering::Relaxed);
    LAST_PAV_BITS.store(0.0f64.to_bits(), Ordering::Relaxed);
}

#[derive(Deserialize, Serialize)]
#[serde(untagged)]
enum ZeroCopyValue<'a> {
    Null,
    Bool(bool),
    Number(serde_json::Number),
    String(Cow<'a, str>),
    Array(Vec<ZeroCopyValue<'a>>),
    Object(BTreeMap<Cow<'a, str>, ZeroCopyValue<'a>>),
}

pub fn canonicalize_payload(payload: &[u8]) -> Vec<u8> {
    if let Ok(text) = std::str::from_utf8(payload) {
        // Try parsing as JSON (Zero-Copy)
        if let Ok(value) = serde_json::from_str::<ZeroCopyValue<'_>>(text) {
            if let Ok(canonical_json) = serde_json::to_string(&value) {
                return canonical_json.into_bytes();
            }
        }
        // Try parsing as Rust code
        if let Ok(ast) = syn::parse_file(text) {
            let canonical_rust = quote::quote!(#ast).to_string();
            return canonical_rust.into_bytes();
        }
        // Fallback: trim whitespace
        return text.trim().as_bytes().to_vec();
    }
    // Fallback: original payload
    payload.to_vec()
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PhysicalArtifact {
    pub artifact_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub fuel_consumed: u64,
    pub bytes_changed: usize,
}

/// Objective-level validation is separate from PAV. A non-zero AST/fuel
/// delta proves only that execution changed something; this receipt binds an
/// independent checker, objective and artifact before a caller can treat the
/// change as correct.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ObjectiveValidationReceipt {
    pub artifact_hash: [u8; 32],
    pub objective_hash: [u8; 32],
    pub test_suite_hash: [u8; 32],
    pub result_hash: [u8; 32],
    pub validator_id: String,
    pub checked_at_ms: u64,
    pub receipt_hash: [u8; 32],
}

impl ObjectiveValidationReceipt {
    pub fn new(
        artifact: &PhysicalArtifact,
        objective_hash: [u8; 32],
        test_suite_hash: [u8; 32],
        result_hash: [u8; 32],
        validator_id: impl Into<String>,
        checked_at_ms: u64,
    ) -> Result<Self, TrapReason> {
        let validator_id = validator_id.into();
        if objective_hash == [0; 32]
            || test_suite_hash == [0; 32]
            || result_hash == [0; 32]
            || validator_id.trim().is_empty()
            || checked_at_ms == 0
        {
            return Err(TrapReason::InvariantViolation);
        }
        let mut receipt = Self {
            artifact_hash: artifact.artifact_hash,
            objective_hash,
            test_suite_hash,
            result_hash,
            validator_id,
            checked_at_ms,
            receipt_hash: [0; 32],
        };
        receipt.receipt_hash = receipt.compute_hash();
        Ok(receipt)
    }

    pub fn compute_hash(&self) -> [u8; 32] {
        let mut hasher = blake3::Hasher::new();
        hasher.update(b"aegis-objective-validation-receipt-v1");
        hasher.update(&self.artifact_hash);
        hasher.update(&self.objective_hash);
        hasher.update(&self.test_suite_hash);
        hasher.update(&self.result_hash);
        hasher.update(&(self.validator_id.len() as u64).to_le_bytes());
        hasher.update(self.validator_id.as_bytes());
        hasher.update(&self.checked_at_ms.to_le_bytes());
        *hasher.finalize().as_bytes()
    }

    pub fn is_valid_for(&self, artifact: &PhysicalArtifact) -> bool {
        self.artifact_hash == artifact.artifact_hash
            && self.objective_hash != [0; 32]
            && self.test_suite_hash != [0; 32]
            && self.result_hash != [0; 32]
            && !self.validator_id.trim().is_empty()
            && self.checked_at_ms > 0
            && self.receipt_hash != [0; 32]
            && self.receipt_hash == self.compute_hash()
    }
}

impl PhysicalArtifact {
    pub fn new(
        payload: &[u8],
        ast_fingerprint: u64,
        fuel_consumed: u64,
        bytes_changed: usize,
    ) -> Result<Self, TrapReason> {
        if payload.is_empty() {
            return Err(TrapReason::EmptyArtifact);
        }
        if fuel_consumed == 0 {
            return Err(TrapReason::FuelExhausted);
        }

        let canonical_payload = canonicalize_payload(payload);

        Ok(Self {
            artifact_hash: blake3_digest(&canonical_payload),
            ast_fingerprint,
            fuel_consumed,
            bytes_changed,
        })
    }

    pub fn is_physical_change(&self) -> bool {
        self.bytes_changed > 0 && self.fuel_consumed > 0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TrapReason {
    EmptyArtifact,
    FuelExhausted,
    InvariantViolation,
    PavBelowThreshold,
    NoConsensus,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BacktrackSignal {
    HardBacktrack(TrapReason),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConsensusResult {
    pub artifact_hash: [u8; 32],
    pub quorum_size: usize,
    pub fuel_consumed: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DAGNode {
    pub node_id: u128,
    pub parent_id: Option<u128>,
    pub state_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub depth: u16,
}

impl DAGNode {
    pub fn root(state_payload: &[u8]) -> Self {
        Self {
            node_id: 1,
            parent_id: None,
            state_hash: blake3_digest(state_payload),
            ast_fingerprint: fingerprint_payload(state_payload),
            depth: 0,
        }
    }

    pub fn child_from(&self, artifact: &PhysicalArtifact) -> Self {
        let mut state_material = Vec::with_capacity(72);
        state_material.extend_from_slice(&self.state_hash);
        state_material.extend_from_slice(&artifact.artifact_hash);
        state_material.extend_from_slice(&artifact.ast_fingerprint.to_le_bytes());
        Self {
            node_id: self.node_id.saturating_add(1),
            parent_id: Some(self.node_id),
            state_hash: blake3_digest(&state_material),
            ast_fingerprint: artifact.ast_fingerprint,
            depth: self.depth.saturating_add(1),
        }
    }
}

pub trait PAVWatchdog {
    fn measure_pav(
        &self,
        old_ast_fingerprint: u64,
        new_ast_fingerprint: u64,
        fuel_consumed: u64,
    ) -> f64;
    fn trigger_circuit_breaker(&self, reason: TrapReason) -> BacktrackSignal;
}

pub trait DeterministicOrchestrator {
    fn advance_state(
        &self,
        current_node: DAGNode,
        execution_result: Result<PhysicalArtifact, TrapReason>,
    ) -> Result<DAGNode, BacktrackSignal>;
}

pub trait PhysicalWitnessVerification {
    fn verify_physical_witnesses(
        &self,
        artifacts: &[PhysicalArtifact],
    ) -> Result<ConsensusResult, TrapReason>;
}

pub struct PhysicalWatchdog {
    pub epsilon: f64,
}

impl PhysicalWatchdog {
    /// PAV is an admissibility/novelty signal only. It is not a correctness
    /// proof; use `accepts_with_objective` at an authoritative commit boundary.
    pub fn accepts(&self, artifact: &PhysicalArtifact) -> Result<(), BacktrackSignal> {
        self.accepts_transition(0, artifact)
    }

    pub fn accepts_with_objective(
        &self,
        old_ast_fingerprint: u64,
        artifact: &PhysicalArtifact,
        receipt: &ObjectiveValidationReceipt,
    ) -> Result<(), BacktrackSignal> {
        if !receipt.is_valid_for(artifact) {
            REJECTED_ARTIFACTS.fetch_add(1, Ordering::Relaxed);
            return Err(self.trigger_circuit_breaker(TrapReason::InvariantViolation));
        }
        self.accepts_transition(old_ast_fingerprint, artifact)
    }

    pub fn accepts_transition(
        &self,
        old_ast_fingerprint: u64,
        artifact: &PhysicalArtifact,
    ) -> Result<(), BacktrackSignal> {
        if !artifact.is_physical_change() {
            REJECTED_ARTIFACTS.fetch_add(1, Ordering::Relaxed);
            return Err(self.trigger_circuit_breaker(TrapReason::PavBelowThreshold));
        }
        let pav = self.measure_pav(
            old_ast_fingerprint,
            artifact.ast_fingerprint,
            artifact.fuel_consumed,
        );
        LAST_PAV_BITS.store(pav.to_bits(), Ordering::Relaxed);
        TOTAL_FUEL_CONSUMED.fetch_add(artifact.fuel_consumed, Ordering::Relaxed);
        if pav < self.epsilon {
            REJECTED_ARTIFACTS.fetch_add(1, Ordering::Relaxed);
            return Err(self.trigger_circuit_breaker(TrapReason::PavBelowThreshold));
        }
        ACCEPTED_ARTIFACTS.fetch_add(1, Ordering::Relaxed);
        Ok(())
    }
}

impl PAVWatchdog for PhysicalWatchdog {
    fn measure_pav(
        &self,
        old_ast_fingerprint: u64,
        new_ast_fingerprint: u64,
        fuel_consumed: u64,
    ) -> f64 {
        if fuel_consumed == 0 {
            return 0.0;
        }
        let ast_distance = ast_distance_from_fingerprints(old_ast_fingerprint, new_ast_fingerprint);
        ast_distance as f64 / fuel_consumed as f64
    }

    fn trigger_circuit_breaker(&self, reason: TrapReason) -> BacktrackSignal {
        HARD_BACKTRACKS.fetch_add(1, Ordering::Relaxed);
        BacktrackSignal::HardBacktrack(reason)
    }
}

#[derive(Clone, Debug)]
struct AstEditNode {
    label: u64,
    children: Vec<usize>,
}

#[derive(Clone, Debug)]
struct AstSignature {
    root: usize,
    nodes: Vec<AstEditNode>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AstDistanceCacheEntry {
    key: (u64, u64),
    distance: u64,
}

#[derive(Clone, Debug)]
struct AstDistanceCache {
    slots: Vec<Option<AstDistanceCacheEntry>>,
}

static AST_SIGNATURES: OnceLock<RwLock<BTreeMap<u64, Arc<AstSignature>>>> = OnceLock::new();
static AST_DISTANCE_CACHE: OnceLock<RwLock<AstDistanceCache>> = OnceLock::new();
const AST_DISTANCE_CACHE_SLOTS: usize = 16_384;

fn ast_signature_registry() -> &'static RwLock<BTreeMap<u64, Arc<AstSignature>>> {
    AST_SIGNATURES.get_or_init(|| RwLock::new(BTreeMap::new()))
}

fn ast_distance_cache() -> &'static RwLock<AstDistanceCache> {
    AST_DISTANCE_CACHE.get_or_init(|| RwLock::new(AstDistanceCache::new(AST_DISTANCE_CACHE_SLOTS)))
}

impl AstDistanceCache {
    fn new(slot_count: usize) -> Self {
        let slot_count = slot_count.max(1);
        Self {
            slots: vec![None; slot_count],
        }
    }

    fn get(&self, key: (u64, u64)) -> Option<u64> {
        let slot = ast_distance_cache_slot(key, self.slots.len());
        self.slots[slot]
            .filter(|entry| entry.key == key)
            .map(|entry| entry.distance)
    }

    fn insert(&mut self, key: (u64, u64), distance: u64) {
        let slot = ast_distance_cache_slot(key, self.slots.len());
        self.slots[slot] = Some(AstDistanceCacheEntry { key, distance });
    }
}

impl AstSignature {
    fn from_rust_code(code: &str) -> Option<Self> {
        let file = syn::parse_file(code).ok()?;
        let mut signature = Self {
            root: 0,
            nodes: Vec::new(),
        };
        let root = signature.push_node(ast_label(0, "file"));
        signature.root = root;
        for item in &file.items {
            signature.append_token_stream(root, quote::quote!(#item));
        }
        Some(signature)
    }

    fn push_node(&mut self, label: u64) -> usize {
        let index = self.nodes.len();
        self.nodes.push(AstEditNode {
            label,
            children: Vec::new(),
        });
        index
    }

    fn append_token_stream(&mut self, parent: usize, tokens: TokenStream) {
        for token in tokens {
            match token {
                TokenTree::Group(group) => {
                    let child = self.push_node(ast_label(1, delimiter_name(group.delimiter())));
                    self.nodes[parent].children.push(child);
                    self.append_token_stream(child, group.stream());
                }
                TokenTree::Ident(ident) => {
                    let child = self.push_node(ast_label(2, &ident.to_string()));
                    self.nodes[parent].children.push(child);
                }
                TokenTree::Punct(punct) => {
                    let label = punct.as_char() as u32 as u64;
                    let child = self.push_node(ast_label(3, "") ^ label);
                    self.nodes[parent].children.push(child);
                }
                TokenTree::Literal(literal) => {
                    let child = self.push_node(ast_label(4, &literal.to_string()));
                    self.nodes[parent].children.push(child);
                }
            }
        }
    }

    fn fingerprint(&self) -> u64 {
        let mut encoded = Vec::with_capacity(self.nodes.len() * 16);
        self.encode_node(self.root, &mut encoded);
        xxhash_rust::xxh3::xxh3_64(&encoded)
    }

    fn encode_node(&self, index: usize, encoded: &mut Vec<u8>) {
        let node = &self.nodes[index];
        encoded.extend_from_slice(&node.label.to_le_bytes());
        encoded.extend_from_slice(&(node.children.len() as u64).to_le_bytes());
        for child in &node.children {
            self.encode_node(*child, encoded);
        }
    }
}

fn delimiter_name(delimiter: Delimiter) -> &'static str {
    match delimiter {
        Delimiter::Parenthesis => "paren",
        Delimiter::Brace => "brace",
        Delimiter::Bracket => "bracket",
        Delimiter::None => "none",
    }
}

fn ast_label(kind: u64, text: &str) -> u64 {
    const MIX: u64 = 0x9E37_79B9_7F4A_7C15;
    xxhash_rust::xxh3::xxh3_64(text.as_bytes()) ^ kind.wrapping_mul(MIX)
}

fn register_ast_signature(signature: AstSignature) -> u64 {
    let fingerprint = signature.fingerprint();
    ast_signature_registry()
        .write()
        .insert(fingerprint, Arc::new(signature));
    fingerprint
}

pub fn compute_ast_structural_fingerprint(code: &str) -> u64 {
    if let Some(signature) = AstSignature::from_rust_code(code) {
        register_ast_signature(signature)
    } else {
        xxhash_rust::xxh3::xxh3_64(code.trim().as_bytes())
    }
}

pub fn compute_ast_tree_edit_distance(old_code: &str, new_code: &str) -> Option<u64> {
    let old_signature = AstSignature::from_rust_code(old_code)?;
    let new_signature = AstSignature::from_rust_code(new_code)?;
    Some(zhang_shasha_distance(&old_signature, &new_signature) as u64)
}

fn ast_distance_from_fingerprints(old_ast_fingerprint: u64, new_ast_fingerprint: u64) -> u64 {
    if old_ast_fingerprint == new_ast_fingerprint {
        return 0;
    }
    let cache_key = ordered_ast_distance_key(old_ast_fingerprint, new_ast_fingerprint);
    if let Some(distance) = ast_distance_cache().read().get(cache_key) {
        return distance;
    }

    // A fingerprint is intentionally opaque. Reconstructing a distance from
    // an in-process signature registry made PAV depend on call history: the
    // same pair could produce different values after a restart. Use a stable
    // symmetric distance for the watchdog; semantic tree-edit distance remains
    // available through `compute_ast_tree_edit_distance` when source is
    // present.
    cache_ast_distance(
        cache_key,
        stable_fingerprint_distance(old_ast_fingerprint, new_ast_fingerprint),
    )
}

fn ordered_ast_distance_key(left: u64, right: u64) -> (u64, u64) {
    if left <= right {
        (left, right)
    } else {
        (right, left)
    }
}

fn ast_distance_cache_slot(key: (u64, u64), slot_count: usize) -> usize {
    let mut bytes = [0u8; 16];
    bytes[..8].copy_from_slice(&key.0.to_le_bytes());
    bytes[8..].copy_from_slice(&key.1.to_le_bytes());
    (xxhash_rust::xxh3::xxh3_64(&bytes) as usize) % slot_count
}

fn cache_ast_distance(cache_key: (u64, u64), distance: u64) -> u64 {
    ast_distance_cache().write().insert(cache_key, distance);
    distance
}

#[cfg(test)]
pub(crate) fn ast_distance_cache_contains_for_tests(left: u64, right: u64) -> bool {
    ast_distance_cache()
        .read()
        .get(ordered_ast_distance_key(left, right))
        .is_some()
}

#[cfg(test)]
pub(crate) fn ast_distance_cache_slot_count_for_tests() -> usize {
    ast_distance_cache().read().slots.len()
}

fn stable_fingerprint_distance(old_ast_fingerprint: u64, new_ast_fingerprint: u64) -> u64 {
    if old_ast_fingerprint == new_ast_fingerprint {
        return 0;
    }
    // Cap the opaque-fingerprint distance. This keeps high-fuel literal edits
    // below a strict novelty threshold while remaining deterministic across
    // process restarts.
    u64::from((old_ast_fingerprint ^ new_ast_fingerprint).count_ones()).min(8)
}

struct PostorderTree {
    labels: Vec<u64>,
    leftmost_descendant: Vec<usize>,
    keyroots: Vec<usize>,
}

impl PostorderTree {
    fn from_signature(signature: &AstSignature) -> Self {
        let mut labels = vec![0];
        let mut leftmost_descendant = vec![0];
        visit_postorder(
            signature,
            signature.root,
            &mut labels,
            &mut leftmost_descendant,
        );

        let mut last_root_for_leftmost = vec![0usize; labels.len()];
        for postorder_index in 1..labels.len() {
            last_root_for_leftmost[leftmost_descendant[postorder_index]] = postorder_index;
        }
        let keyroots = last_root_for_leftmost
            .into_iter()
            .filter(|postorder_index| *postorder_index != 0)
            .collect::<Vec<_>>();
        let mut keyroots = keyroots;
        keyroots.sort_unstable();

        Self {
            labels,
            leftmost_descendant,
            keyroots,
        }
    }

    fn len(&self) -> usize {
        self.labels.len().saturating_sub(1)
    }
}

fn visit_postorder(
    signature: &AstSignature,
    node_index: usize,
    labels: &mut Vec<u64>,
    leftmost_descendant: &mut Vec<usize>,
) -> usize {
    let node = &signature.nodes[node_index];
    let leftmost = if node.children.is_empty() {
        labels.len()
    } else {
        let mut child_leftmost = 0;
        for (position, child) in node.children.iter().enumerate() {
            let left = visit_postorder(signature, *child, labels, leftmost_descendant);
            if position == 0 {
                child_leftmost = left;
            }
        }
        child_leftmost
    };

    labels.push(node.label);
    leftmost_descendant.push(leftmost);
    leftmost
}

fn zhang_shasha_distance(old_signature: &AstSignature, new_signature: &AstSignature) -> usize {
    let old_tree = PostorderTree::from_signature(old_signature);
    let new_tree = PostorderTree::from_signature(new_signature);
    let tree_cols = new_tree.len() + 1;
    let mut tree_distance = vec![0usize; (old_tree.len() + 1) * tree_cols];
    let mut forest_distance = Vec::new();

    for old_root in &old_tree.keyroots {
        for new_root in &new_tree.keyroots {
            compute_tree_distance(
                *old_root,
                *new_root,
                &old_tree,
                &new_tree,
                tree_cols,
                &mut tree_distance,
                &mut forest_distance,
            );
        }
    }

    tree_distance[old_tree.len() * tree_cols + new_tree.len()]
}

fn compute_tree_distance(
    old_root: usize,
    new_root: usize,
    old_tree: &PostorderTree,
    new_tree: &PostorderTree,
    tree_cols: usize,
    tree_distance: &mut [usize],
    forest_distance: &mut Vec<usize>,
) {
    let old_left = old_tree.leftmost_descendant[old_root];
    let new_left = new_tree.leftmost_descendant[new_root];
    let row_count = old_root - old_left + 2;
    let col_count = new_root - new_left + 2;
    forest_distance.resize(row_count * col_count, 0);
    forest_distance[0] = 0;

    for old_index in old_left..=old_root {
        let row = old_index - old_left + 1;
        forest_distance[row * col_count] = forest_distance[(row - 1) * col_count] + 1;
    }
    for new_index in new_left..=new_root {
        let col = new_index - new_left + 1;
        forest_distance[col] = forest_distance[col - 1] + 1;
    }

    for old_index in old_left..=old_root {
        for new_index in new_left..=new_root {
            let row = old_index - old_left + 1;
            let col = new_index - new_left + 1;
            let cell = row * col_count + col;
            let delete_cost = forest_distance[(row - 1) * col_count + col] + 1;
            let insert_cost = forest_distance[row * col_count + col - 1] + 1;

            if old_tree.leftmost_descendant[old_index] == old_left
                && new_tree.leftmost_descendant[new_index] == new_left
            {
                let update_cost =
                    usize::from(old_tree.labels[old_index] != new_tree.labels[new_index]);
                let update_tree_cost =
                    forest_distance[(row - 1) * col_count + col - 1] + update_cost;
                let cost = delete_cost.min(insert_cost).min(update_tree_cost);
                forest_distance[cell] = cost;
                tree_distance[old_index * tree_cols + new_index] = cost;
            } else {
                let old_subtree_start = old_tree.leftmost_descendant[old_index] - old_left;
                let new_subtree_start = new_tree.leftmost_descendant[new_index] - new_left;
                let update_subtree_cost = forest_distance
                    [old_subtree_start * col_count + new_subtree_start]
                    + tree_distance[old_index * tree_cols + new_index];
                forest_distance[cell] = delete_cost.min(insert_cost).min(update_subtree_cost);
            }
        }
    }
}

pub struct PhysicalDagOrchestrator {
    pub watchdog: PhysicalWatchdog,
}

impl PhysicalDagOrchestrator {
    /// Advance an authoritative state only after objective validation. The
    /// trait implementation below is retained as a candidate-generation path.
    pub fn advance_state_with_objective(
        &self,
        current_node: DAGNode,
        artifact: PhysicalArtifact,
        receipt: &ObjectiveValidationReceipt,
    ) -> Result<DAGNode, BacktrackSignal> {
        self.watchdog
            .accepts_with_objective(current_node.ast_fingerprint, &artifact, receipt)?;
        Ok(current_node.child_from(&artifact))
    }
}

impl DeterministicOrchestrator for PhysicalDagOrchestrator {
    #[instrument(skip(self, execution_result))]
    fn advance_state(
        &self,
        current_node: DAGNode,
        execution_result: Result<PhysicalArtifact, TrapReason>,
    ) -> Result<DAGNode, BacktrackSignal> {
        let artifact = execution_result.map_err(|reason| {
            warn!(?reason, "Execution trapped, triggering circuit breaker");
            self.watchdog.trigger_circuit_breaker(reason)
        })?;

        if let Err(signal) = self
            .watchdog
            .accepts_transition(current_node.ast_fingerprint, &artifact)
        {
            warn!(?signal, "Watchdog rejected artifact, triggering backtrack");
            return Err(signal);
        }

        let new_node = current_node.child_from(&artifact);
        info!(
            new_node_id = new_node.node_id,
            depth = new_node.depth,
            "State advanced successfully"
        );
        Ok(new_node)
    }
}

pub struct PhysicalWitnessThreshold {
    pub total_witnesses: usize,
}

impl PhysicalWitnessThreshold {
    fn required_matches(&self) -> usize {
        let f = self.total_witnesses.saturating_sub(1) / 3;
        f + 1
    }
}

impl PhysicalWitnessVerification for PhysicalWitnessThreshold {
    #[instrument(skip(self, artifacts))]
    fn verify_physical_witnesses(
        &self,
        artifacts: &[PhysicalArtifact],
    ) -> Result<ConsensusResult, TrapReason> {
        if artifacts.is_empty() || self.total_witnesses == 0 {
            CONSENSUS_FAILURES.fetch_add(1, Ordering::Relaxed);
            warn!("No physical witness threshold reached: empty artifacts or zero witnesses");
            return Err(TrapReason::NoConsensus);
        }

        let mut groups: BTreeMap<[u8; 32], Vec<&PhysicalArtifact>> = BTreeMap::new();
        for artifact in artifacts {
            if !artifact.is_physical_change() {
                continue;
            }
            groups
                .entry(artifact.artifact_hash)
                .or_default()
                .push(artifact);
        }

        let required_matches = self.required_matches();
        let result = groups
            .into_iter()
            .find(|(_, group)| group.len() >= required_matches)
            .map(|(artifact_hash, group)| ConsensusResult {
                artifact_hash,
                quorum_size: group.len(),
                fuel_consumed: group[0].fuel_consumed,
            });

        match result {
            Some(res) => {
                CONSENSUS_COMMITS.fetch_add(1, Ordering::Relaxed);
                info!(
                    quorum_size = res.quorum_size,
                    "Physical witness threshold reached"
                );
                Ok(res)
            }
            None => {
                CONSENSUS_FAILURES.fetch_add(1, Ordering::Relaxed);
                error!("Physical witness divergence detected, failing threshold");
                Err(TrapReason::NoConsensus)
            }
        }
    }
}

pub fn blake3_digest(bytes: &[u8]) -> [u8; 32] {
    *blake3::hash(bytes).as_bytes()
}

fn fingerprint_payload(payload: &[u8]) -> u64 {
    if let Ok(text) = std::str::from_utf8(payload) {
        compute_ast_structural_fingerprint(text)
    } else {
        let hash = blake3_digest(payload);
        u64::from_le_bytes(hash[..8].try_into().unwrap_or([0u8; 8]))
    }
}
