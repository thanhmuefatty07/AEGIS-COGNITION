use crate::evidence_index::{
    AgenticEvidenceExecutionRecord, BrowserPageSearchCandidateRecord, CandidateEvidenceRef,
    CandidateOnlyGate, CandidateOnlyGateError, CandidateStateCapsule,
    ColdVectorExpansionReplayRecord, EvidenceCandidateTier, agentic_evidence_execution_record_hash,
    candidate_list_hash,
};
use blake3::Hasher;
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

pub type ContextNodeId = u128;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContextNodeKind {
    Task,
    Evidence,
    File,
    Symbol,
    Memory,
    Policy,
    Tool,
}

/// Controls whether a context node may be removed by budget selection.
///
/// The default is deliberately `Condensable` for compatibility with the
/// existing utility-based selector. Callers must opt a node into
/// `Protected`; this avoids silently changing the retention semantics of
/// existing context producers.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ContextRetentionClass {
    Protected,
    Condensable,
    Ephemeral,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextNode {
    pub node_id: ContextNodeId,
    pub kind: ContextNodeKind,
    pub retention_class: ContextRetentionClass,
    pub token_cost: u32,
    pub utility_score: u32,
    pub dependency_coverage: u32,
    pub contradiction_risk: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ContextGovernorConfig {
    pub token_budget: u32,
    pub activation_hop_limit: u8,
    pub activation_node_cap: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ActivatedContextGraph {
    pub active_task_id: ContextNodeId,
    pub required_evidence_refs: Vec<ContextNodeId>,
    pub node_ids: Vec<ContextNodeId>,
    pub activation_node_count: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextPack {
    pub node_ids: Vec<ContextNodeId>,
    pub token_count: u32,
    pub utility_score: u64,
    pub digest: [u8; 32],
    pub activation_node_count: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextFoldRecord {
    pub active_task_id: ContextNodeId,
    pub retained_node_ids: Vec<ContextNodeId>,
    pub folded_node_ids: Vec<ContextNodeId>,
    pub retained_token_count: u32,
    pub folded_token_count: u32,
    pub retained_utility_score: u64,
    pub activation_node_count: usize,
    pub context_pack_digest: [u8; 32],
    pub retained_node_hash: [u8; 32],
    pub folded_node_hash: [u8; 32],
    pub record_hash: [u8; 32],
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextPackCandidateProof {
    pub active_task_id: ContextNodeId,
    pub context_pack_digest: [u8; 32],
    pub context_pack_node_hash: [u8; 32],
    pub candidate_count: u32,
    pub candidate_node_count: u32,
    pub candidate_list_hash: [u8; 32],
    pub candidate_node_hash: [u8; 32],
    pub cold_vector_replay_record_hash: [u8; 32],
    pub agentic_evidence_execution_record_hash: [u8; 32],
    pub agentic_evidence_sdk_run_handoff_hash: [u8; 32],
    pub browser_page_search_record_hash: [u8; 32],
    pub candidate_state_capsule_hash: [u8; 32],
    pub proof_hash: [u8; 32],
}

pub trait AgenticEvidenceSdkRunHandoffBinding {
    fn sdk_handoff_hash(&self) -> [u8; 32];
    fn sdk_execution_record_hash(&self) -> [u8; 32];
    fn sdk_candidate_list_hash(&self) -> [u8; 32];
    fn sdk_candidate_count(&self) -> u32;
    fn sdk_capsule_hash(&self) -> [u8; 32];
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContextGovernorError {
    InvalidConfig,
    InvalidNode(ContextNodeId),
    InvalidFoldRecord,
    InvalidCandidateEvidenceProof,
    CandidateEvidenceGate(CandidateOnlyGateError),
    MissingNode(ContextNodeId),
    ActivationCapExceeded,
    TokenBudgetExceeded,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextGovernor {
    config: ContextGovernorConfig,
    nodes: BTreeMap<ContextNodeId, ContextNode>,
    edges: BTreeMap<ContextNodeId, BTreeSet<ContextNodeId>>,
}

impl ContextNode {
    pub fn new(
        node_id: ContextNodeId,
        kind: ContextNodeKind,
        token_cost: u32,
        utility_score: u32,
        dependency_coverage: u32,
        contradiction_risk: u32,
    ) -> Self {
        Self {
            node_id,
            kind,
            retention_class: ContextRetentionClass::Condensable,
            token_cost,
            utility_score,
            dependency_coverage,
            contradiction_risk,
        }
    }

    pub fn with_retention_class(mut self, retention_class: ContextRetentionClass) -> Self {
        self.retention_class = retention_class;
        self
    }

    pub fn deterministic_utility(&self) -> u64 {
        (self.utility_score as u64)
            .saturating_add(self.dependency_coverage as u64)
            .saturating_sub(self.contradiction_risk as u64)
    }
}

impl ContextGovernorConfig {
    pub fn bounded(token_budget: u32, activation_node_cap: usize) -> Self {
        Self {
            token_budget,
            activation_hop_limit: 2,
            activation_node_cap,
        }
    }

    fn is_valid(self) -> bool {
        self.token_budget > 0 && self.activation_node_cap > 0 && self.activation_hop_limit > 0
    }

    fn effective_hop_limit(self) -> u8 {
        self.activation_hop_limit.min(2)
    }
}

impl ContextGovernor {
    pub fn new(config: ContextGovernorConfig) -> Result<Self, ContextGovernorError> {
        if !config.is_valid() {
            return Err(ContextGovernorError::InvalidConfig);
        }
        Ok(Self {
            config,
            nodes: BTreeMap::new(),
            edges: BTreeMap::new(),
        })
    }

    pub fn insert_node(&mut self, node: ContextNode) -> Result<(), ContextGovernorError> {
        if node.node_id == 0 || node.token_cost == 0 {
            return Err(ContextGovernorError::InvalidNode(node.node_id));
        }
        self.edges.entry(node.node_id).or_default();
        self.nodes.insert(node.node_id, node);
        Ok(())
    }

    pub fn add_edge(
        &mut self,
        left: ContextNodeId,
        right: ContextNodeId,
    ) -> Result<(), ContextGovernorError> {
        if !self.nodes.contains_key(&left) {
            return Err(ContextGovernorError::MissingNode(left));
        }
        if !self.nodes.contains_key(&right) {
            return Err(ContextGovernorError::MissingNode(right));
        }
        self.edges.entry(left).or_default().insert(right);
        self.edges.entry(right).or_default().insert(left);
        Ok(())
    }

    pub fn build_activated_graph(
        &self,
        active_task_id: ContextNodeId,
        required_evidence_refs: &[ContextNodeId],
    ) -> Result<ActivatedContextGraph, ContextGovernorError> {
        if !self.nodes.contains_key(&active_task_id) {
            return Err(ContextGovernorError::MissingNode(active_task_id));
        }
        for evidence_ref in required_evidence_refs {
            if !self.nodes.contains_key(evidence_ref) {
                return Err(ContextGovernorError::MissingNode(*evidence_ref));
            }
        }

        let mut activated = BTreeSet::new();
        self.insert_required(&mut activated, active_task_id)?;
        for evidence_ref in required_evidence_refs {
            self.insert_required(&mut activated, *evidence_ref)?;
        }

        let mut frontier: Vec<ContextNodeId> = activated.iter().copied().collect();
        for _ in 0..self.config.effective_hop_limit() {
            let mut next_frontier = Vec::new();
            for node_id in &frontier {
                for neighbor_id in self.neighbors(*node_id) {
                    if activated.len() >= self.config.activation_node_cap {
                        break;
                    }
                    if activated.insert(*neighbor_id) {
                        next_frontier.push(*neighbor_id);
                    }
                }
            }
            if next_frontier.is_empty() || activated.len() >= self.config.activation_node_cap {
                break;
            }
            next_frontier.sort_unstable();
            frontier = next_frontier;
        }

        let node_ids: Vec<ContextNodeId> = activated.into_iter().collect();
        Ok(ActivatedContextGraph {
            active_task_id,
            required_evidence_refs: canonical_ids(required_evidence_refs),
            activation_node_count: node_ids.len(),
            node_ids,
        })
    }

    pub fn build_context_pack(
        &self,
        active_task_id: ContextNodeId,
        required_evidence_refs: &[ContextNodeId],
    ) -> Result<ContextPack, ContextGovernorError> {
        let activated = self.build_activated_graph(active_task_id, required_evidence_refs)?;
        self.build_context_pack_from_activated(&activated)
    }

    /// Select already-hydrated nodes without inventing a task node. The
    /// ordinary graph path remains responsible for task activation; this
    /// bounded helper lets the provider-neutral hydration seam reuse the same
    /// deterministic utility, mandatory-node and token-budget rules.
    pub fn build_hydrated_context_pack(
        &self,
        required_node_ids: &[ContextNodeId],
    ) -> Result<ContextPack, ContextGovernorError> {
        let required = canonical_ids(required_node_ids);
        for node_id in &required {
            if !self.nodes.contains_key(node_id) {
                return Err(ContextGovernorError::MissingNode(*node_id));
            }
        }
        let activated_ids: Vec<ContextNodeId> = self.nodes.keys().copied().collect();
        let protected = self.retained_seed_ids(&activated_ids, &required);
        let mut selected = protected.clone();
        let mut token_count = 0u32;
        for node_id in &protected {
            token_count = token_count
                .checked_add(self.nodes[node_id].token_cost)
                .ok_or(ContextGovernorError::TokenBudgetExceeded)?;
        }
        if token_count > self.config.token_budget {
            return Err(ContextGovernorError::TokenBudgetExceeded);
        }
        let mut candidates: Vec<&ContextNode> = activated_ids
            .iter()
            .filter(|node_id| protected.binary_search(node_id).is_err())
            .filter_map(|node_id| self.nodes.get(node_id))
            .collect();
        candidates.sort_by(|left, right| candidate_order(left, right));
        for candidate in candidates {
            if token_count.saturating_add(candidate.token_cost) <= self.config.token_budget {
                token_count += candidate.token_cost;
                selected.push(candidate.node_id);
            }
        }
        self.local_swap_improve(&protected, &activated_ids, &mut selected, &mut token_count);
        selected.sort_unstable();
        let utility_score = selected
            .iter()
            .map(|node_id| self.nodes[node_id].deterministic_utility())
            .sum();
        let digest = context_pack_digest(&selected, token_count, utility_score);
        Ok(ContextPack {
            node_ids: selected,
            token_count,
            utility_score,
            digest,
            activation_node_count: activated_ids.len(),
        })
    }

    pub fn build_context_fold(
        &self,
        active_task_id: ContextNodeId,
        required_evidence_refs: &[ContextNodeId],
    ) -> Result<ContextFoldRecord, ContextGovernorError> {
        let activated = self.build_activated_graph(active_task_id, required_evidence_refs)?;
        let pack = self.build_context_pack_from_activated(&activated)?;
        let retained = pack.node_ids;
        let folded: Vec<ContextNodeId> = activated
            .node_ids
            .iter()
            .filter(|node_id| retained.binary_search(node_id).is_err())
            .copied()
            .collect();
        let folded_token_count = folded
            .iter()
            .filter_map(|node_id| self.nodes.get(node_id))
            .map(|node| node.token_cost)
            .try_fold(0u32, |total, token_cost| {
                total
                    .checked_add(token_cost)
                    .ok_or(ContextGovernorError::TokenBudgetExceeded)
            })?;

        ContextFoldRecord::new(
            active_task_id,
            &retained,
            &folded,
            pack.token_count,
            folded_token_count,
            pack.utility_score,
            activated.activation_node_count,
            pack.digest,
        )
    }

    pub fn build_candidate_bound_context_pack(
        &self,
        active_task_id: ContextNodeId,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
    ) -> Result<(ContextPack, ContextPackCandidateProof), ContextGovernorError> {
        self.build_replay_bound_candidate_context_pack(
            active_task_id,
            candidates,
            cold_vector_record,
            None,
        )
    }

    pub fn build_replay_bound_candidate_context_pack(
        &self,
        active_task_id: ContextNodeId,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
    ) -> Result<(ContextPack, ContextPackCandidateProof), ContextGovernorError> {
        self.build_replay_bound_candidate_context_pack_with_browser(
            active_task_id,
            candidates,
            cold_vector_record,
            agentic_record,
            None,
        )
    }

    pub fn build_replay_bound_candidate_context_pack_with_browser(
        &self,
        active_task_id: ContextNodeId,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> Result<(ContextPack, ContextPackCandidateProof), ContextGovernorError> {
        CandidateOnlyGate::validate_replay_bound_context_pack_candidates_with_browser(
            candidates,
            cold_vector_record,
            agentic_record,
            browser_page_search_record,
        )
        .map_err(ContextGovernorError::CandidateEvidenceGate)?;
        let candidate_node_ids = candidate_node_ids_from_refs(candidates)?;
        let pack = self.build_context_pack(active_task_id, &candidate_node_ids)?;
        let proof = ContextPackCandidateProof::new_with_replay_records(
            active_task_id,
            &pack,
            candidates,
            cold_vector_record,
            agentic_record,
            browser_page_search_record,
        )?;
        if !proof.is_valid_for_replay_records(
            &pack,
            candidates,
            cold_vector_record,
            agentic_record,
            browser_page_search_record,
        ) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        Ok((pack, proof))
    }

    pub fn build_capsule_bound_context_pack(
        &self,
        active_task_id: ContextNodeId,
        capsule: &CandidateStateCapsule,
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
    ) -> Result<(ContextPack, ContextPackCandidateProof), ContextGovernorError> {
        if !capsule.is_valid_for_replay_records(cold_vector_record, agentic_record) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        let candidate_node_ids = candidate_node_ids_from_refs(capsule.candidates())?;
        let pack = self.build_context_pack(active_task_id, &candidate_node_ids)?;
        let proof = ContextPackCandidateProof::new_with_capsule(
            active_task_id,
            &pack,
            capsule,
            cold_vector_record,
            agentic_record,
            None,
        )?;
        if !proof.is_valid_for_capsule(&pack, capsule, cold_vector_record, agentic_record, None) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        Ok((pack, proof))
    }

    pub fn build_sdk_run_handoff_bound_context_pack<H: AgenticEvidenceSdkRunHandoffBinding>(
        &self,
        active_task_id: ContextNodeId,
        capsule: &CandidateStateCapsule,
        handoff: &H,
    ) -> Result<(ContextPack, ContextPackCandidateProof), ContextGovernorError> {
        if !sdk_handoff_matches_capsule(capsule, handoff) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        let candidate_node_ids = candidate_node_ids_from_refs(capsule.candidates())?;
        let pack = self.build_context_pack(active_task_id, &candidate_node_ids)?;
        let proof = ContextPackCandidateProof::new_with_sdk_run_handoff(
            active_task_id,
            &pack,
            capsule,
            handoff,
        )?;
        if !proof.is_valid_for_sdk_run_handoff(&pack, capsule, handoff) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        Ok((pack, proof))
    }

    fn build_context_pack_from_activated(
        &self,
        activated: &ActivatedContextGraph,
    ) -> Result<ContextPack, ContextGovernorError> {
        let required =
            canonical_ids_with_active(activated.active_task_id, &activated.required_evidence_refs);
        let activated_ids = &activated.node_ids;
        let activation_node_count = activated.activation_node_count;
        let protected = self.retained_seed_ids(activated_ids, &required);
        let mut selected = Vec::with_capacity(activated_ids.len().min(32));
        let mut token_count = 0u32;
        let mut utility_score = 0u64;

        for required_id in &protected {
            if activated_ids.binary_search(required_id).is_err() {
                return Err(ContextGovernorError::MissingNode(*required_id));
            }
            let node = self
                .nodes
                .get(required_id)
                .ok_or(ContextGovernorError::MissingNode(*required_id))?;
            token_count = token_count
                .checked_add(node.token_cost)
                .ok_or(ContextGovernorError::TokenBudgetExceeded)?;
            if token_count > self.config.token_budget {
                return Err(ContextGovernorError::TokenBudgetExceeded);
            }
            utility_score = utility_score.saturating_add(node.deterministic_utility());
            selected.push(*required_id);
        }

        let mut candidates: Vec<&ContextNode> = activated_ids
            .iter()
            .filter(|node_id| protected.binary_search(node_id).is_err())
            .filter_map(|node_id| self.nodes.get(node_id))
            .collect();
        candidates.sort_by(|left, right| candidate_order(left, right));

        for candidate in candidates {
            if token_count.saturating_add(candidate.token_cost) <= self.config.token_budget {
                token_count += candidate.token_cost;
                utility_score = utility_score.saturating_add(candidate.deterministic_utility());
                selected.push(candidate.node_id);
            }
        }

        if selected.len() < activated_ids.len() {
            self.local_swap_improve(
                protected.as_slice(),
                activated_ids,
                &mut selected,
                &mut token_count,
            );
        }
        let mut node_ids = selected;
        node_ids.sort_unstable();
        utility_score = node_ids
            .iter()
            .filter_map(|node_id| self.nodes.get(node_id))
            .map(ContextNode::deterministic_utility)
            .sum();

        Ok(ContextPack {
            digest: context_pack_digest(&node_ids, token_count, utility_score),
            node_ids,
            token_count,
            utility_score,
            activation_node_count,
        })
    }

    fn retained_seed_ids(
        &self,
        activated_ids: &[ContextNodeId],
        required_ids: &[ContextNodeId],
    ) -> Vec<ContextNodeId> {
        let mut retained = required_ids.to_vec();
        retained.extend(activated_ids.iter().copied().filter(|node_id| {
            self.nodes[node_id].retention_class == ContextRetentionClass::Protected
        }));
        canonical_ids(&retained)
    }

    fn insert_required(
        &self,
        activated: &mut BTreeSet<ContextNodeId>,
        node_id: ContextNodeId,
    ) -> Result<(), ContextGovernorError> {
        if activated.len() >= self.config.activation_node_cap && !activated.contains(&node_id) {
            return Err(ContextGovernorError::ActivationCapExceeded);
        }
        activated.insert(node_id);
        Ok(())
    }

    fn neighbors(&self, node_id: ContextNodeId) -> impl Iterator<Item = &ContextNodeId> {
        self.edges.get(&node_id).into_iter().flatten()
    }

    fn local_swap_improve(
        &self,
        required: &[ContextNodeId],
        activated_ids: &[ContextNodeId],
        selected: &mut Vec<ContextNodeId>,
        token_count: &mut u32,
    ) {
        let mut selected_lookup = selected.clone();
        selected_lookup.sort_unstable();
        let mut candidates: Vec<&ContextNode> = activated_ids
            .iter()
            .filter(|node_id| selected_lookup.binary_search(node_id).is_err())
            .filter_map(|node_id| self.nodes.get(node_id))
            .collect();
        candidates.sort_by(|left, right| candidate_order(left, right));

        for candidate in candidates {
            let candidate_utility = candidate.deterministic_utility();
            let mut removable_index = 0usize;
            while removable_index < selected.len() {
                let removable_id = selected[removable_index];
                if required.binary_search(&removable_id).is_ok() {
                    removable_index += 1;
                    continue;
                }
                let removable = self
                    .nodes
                    .get(&removable_id)
                    .expect("selected node exists in graph");
                if retention_rank(candidate.retention_class)
                    > retention_rank(removable.retention_class)
                {
                    removable_index += 1;
                    continue;
                }
                if candidate_utility <= removable.deterministic_utility() {
                    removable_index += 1;
                    continue;
                }
                let swapped_tokens = token_count
                    .saturating_sub(removable.token_cost)
                    .saturating_add(candidate.token_cost);
                if swapped_tokens <= self.config.token_budget {
                    selected.swap_remove(removable_index);
                    selected.push(candidate.node_id);
                    if let Ok(index) = selected_lookup.binary_search(&removable_id) {
                        selected_lookup.remove(index);
                    }
                    match selected_lookup.binary_search(&candidate.node_id) {
                        Ok(_) => {}
                        Err(index) => selected_lookup.insert(index, candidate.node_id),
                    }
                    *token_count = swapped_tokens;
                    break;
                }
                removable_index += 1;
            }
        }
    }
}

impl ContextFoldRecord {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        active_task_id: ContextNodeId,
        retained_node_ids: &[ContextNodeId],
        folded_node_ids: &[ContextNodeId],
        retained_token_count: u32,
        folded_token_count: u32,
        retained_utility_score: u64,
        activation_node_count: usize,
        context_pack_digest: [u8; 32],
    ) -> Result<Self, ContextGovernorError> {
        let retained_node_ids = canonical_ids(retained_node_ids);
        let folded_node_ids = canonical_ids(folded_node_ids);
        if active_task_id == 0
            || retained_node_ids.is_empty()
            || retained_node_ids.binary_search(&active_task_id).is_err()
            || !disjoint_sorted_ids(&retained_node_ids, &folded_node_ids)
            || activation_node_count
                != retained_node_ids
                    .len()
                    .saturating_add(folded_node_ids.len())
            || retained_token_count == 0
            || (!folded_node_ids.is_empty() && folded_token_count == 0)
            || context_pack_digest == [0; 32]
        {
            return Err(ContextGovernorError::InvalidFoldRecord);
        }
        let retained_node_hash = context_node_set_hash("retained", &retained_node_ids);
        let folded_node_hash = context_node_set_hash("folded", &folded_node_ids);
        let record_hash = context_fold_record_hash(
            active_task_id,
            &retained_node_ids,
            &folded_node_ids,
            retained_token_count,
            folded_token_count,
            retained_utility_score,
            activation_node_count,
            context_pack_digest,
            retained_node_hash,
            folded_node_hash,
        );
        Ok(Self {
            active_task_id,
            retained_node_ids,
            folded_node_ids,
            retained_token_count,
            folded_token_count,
            retained_utility_score,
            activation_node_count,
            context_pack_digest,
            retained_node_hash,
            folded_node_hash,
            record_hash,
        })
    }

    pub fn is_valid(&self) -> bool {
        self.active_task_id > 0
            && !self.retained_node_ids.is_empty()
            && sorted_unique_ids(&self.retained_node_ids)
            && sorted_unique_ids(&self.folded_node_ids)
            && self
                .retained_node_ids
                .binary_search(&self.active_task_id)
                .is_ok()
            && disjoint_sorted_ids(&self.retained_node_ids, &self.folded_node_ids)
            && self.activation_node_count
                == self
                    .retained_node_ids
                    .len()
                    .saturating_add(self.folded_node_ids.len())
            && self.retained_token_count > 0
            && (self.folded_node_ids.is_empty() || self.folded_token_count > 0)
            && self.context_pack_digest != [0; 32]
            && self.retained_node_hash == context_node_set_hash("retained", &self.retained_node_ids)
            && self.folded_node_hash == context_node_set_hash("folded", &self.folded_node_ids)
            && self.record_hash
                == context_fold_record_hash(
                    self.active_task_id,
                    &self.retained_node_ids,
                    &self.folded_node_ids,
                    self.retained_token_count,
                    self.folded_token_count,
                    self.retained_utility_score,
                    self.activation_node_count,
                    self.context_pack_digest,
                    self.retained_node_hash,
                    self.folded_node_hash,
                )
            && self.record_hash != [0; 32]
    }

    pub fn is_lossy(&self) -> bool {
        !self.folded_node_ids.is_empty()
    }
}

impl ContextPackCandidateProof {
    pub fn new(
        active_task_id: ContextNodeId,
        context_pack: &ContextPack,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
    ) -> Result<Self, ContextGovernorError> {
        Self::new_with_replay_records(
            active_task_id,
            context_pack,
            candidates,
            cold_vector_record,
            None,
            None,
        )
    }

    pub fn new_with_replay_records(
        active_task_id: ContextNodeId,
        context_pack: &ContextPack,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> Result<Self, ContextGovernorError> {
        CandidateOnlyGate::validate_replay_bound_context_pack_candidates_with_browser(
            candidates,
            cold_vector_record,
            agentic_record,
            browser_page_search_record,
        )
        .map_err(ContextGovernorError::CandidateEvidenceGate)?;
        let candidate_node_ids = candidate_node_ids_from_refs(candidates)?;
        if active_task_id == 0
            || context_pack.digest == [0; 32]
            || context_pack
                .node_ids
                .binary_search(&active_task_id)
                .is_err()
            || candidate_node_ids
                .iter()
                .any(|node_id| context_pack.node_ids.binary_search(node_id).is_err())
        {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        let context_pack_node_hash =
            context_node_set_hash("candidate-proof-pack", &context_pack.node_ids);
        let candidate_list_hash = candidate_list_hash(candidates);
        let candidate_node_hash =
            context_node_set_hash("candidate-proof-required", &candidate_node_ids);
        let cold_vector_replay_record_hash =
            cold_vector_record_hash_for_candidates(candidates, cold_vector_record)?;
        let agentic_evidence_execution_record_hash =
            agentic_record_hash_for_candidates(candidates, agentic_record)?;
        let browser_page_search_record_hash =
            browser_page_search_record_hash_for_candidates(candidates, browser_page_search_record)?;
        let proof_hash = context_pack_candidate_proof_hash(
            active_task_id,
            context_pack.digest,
            context_pack_node_hash,
            candidates.len().min(u32::MAX as usize) as u32,
            candidate_node_ids.len().min(u32::MAX as usize) as u32,
            candidate_list_hash,
            candidate_node_hash,
            cold_vector_replay_record_hash,
            agentic_evidence_execution_record_hash,
            [0; 32],
            browser_page_search_record_hash,
            [0; 32],
        );
        Ok(Self {
            active_task_id,
            context_pack_digest: context_pack.digest,
            context_pack_node_hash,
            candidate_count: candidates.len().min(u32::MAX as usize) as u32,
            candidate_node_count: candidate_node_ids.len().min(u32::MAX as usize) as u32,
            candidate_list_hash,
            candidate_node_hash,
            cold_vector_replay_record_hash,
            agentic_evidence_execution_record_hash,
            agentic_evidence_sdk_run_handoff_hash: [0; 32],
            browser_page_search_record_hash,
            candidate_state_capsule_hash: [0; 32],
            proof_hash,
        })
    }

    pub fn new_with_capsule(
        active_task_id: ContextNodeId,
        context_pack: &ContextPack,
        capsule: &CandidateStateCapsule,
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> Result<Self, ContextGovernorError> {
        if !capsule.is_valid_for_replay_records(cold_vector_record, agentic_record) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        let mut proof = Self::new_with_replay_records(
            active_task_id,
            context_pack,
            capsule.candidates(),
            cold_vector_record,
            agentic_record,
            browser_page_search_record,
        )?;
        proof.candidate_state_capsule_hash = capsule.capsule_hash;
        proof.proof_hash = context_pack_candidate_proof_hash(
            proof.active_task_id,
            proof.context_pack_digest,
            proof.context_pack_node_hash,
            proof.candidate_count,
            proof.candidate_node_count,
            proof.candidate_list_hash,
            proof.candidate_node_hash,
            proof.cold_vector_replay_record_hash,
            proof.agentic_evidence_execution_record_hash,
            proof.agentic_evidence_sdk_run_handoff_hash,
            proof.browser_page_search_record_hash,
            proof.candidate_state_capsule_hash,
        );
        Ok(proof)
    }

    pub fn new_with_sdk_run_handoff<H: AgenticEvidenceSdkRunHandoffBinding>(
        active_task_id: ContextNodeId,
        context_pack: &ContextPack,
        capsule: &CandidateStateCapsule,
        handoff: &H,
    ) -> Result<Self, ContextGovernorError> {
        if !sdk_handoff_matches_capsule(capsule, handoff) {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        let candidate_node_ids = candidate_node_ids_from_refs(capsule.candidates())?;
        if active_task_id == 0
            || context_pack.digest == [0; 32]
            || context_pack
                .node_ids
                .binary_search(&active_task_id)
                .is_err()
            || candidate_node_ids
                .iter()
                .any(|node_id| context_pack.node_ids.binary_search(node_id).is_err())
        {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        let context_pack_node_hash =
            context_node_set_hash("candidate-proof-pack", &context_pack.node_ids);
        let candidate_list_hash = candidate_list_hash(capsule.candidates());
        let candidate_node_hash =
            context_node_set_hash("candidate-proof-required", &candidate_node_ids);
        let proof_hash = context_pack_candidate_proof_hash(
            active_task_id,
            context_pack.digest,
            context_pack_node_hash,
            capsule.candidate_count,
            candidate_node_ids.len().min(u32::MAX as usize) as u32,
            candidate_list_hash,
            candidate_node_hash,
            [0; 32],
            handoff.sdk_execution_record_hash(),
            handoff.sdk_handoff_hash(),
            [0; 32],
            capsule.capsule_hash,
        );
        Ok(Self {
            active_task_id,
            context_pack_digest: context_pack.digest,
            context_pack_node_hash,
            candidate_count: capsule.candidate_count,
            candidate_node_count: candidate_node_ids.len().min(u32::MAX as usize) as u32,
            candidate_list_hash,
            candidate_node_hash,
            cold_vector_replay_record_hash: [0; 32],
            agentic_evidence_execution_record_hash: handoff.sdk_execution_record_hash(),
            agentic_evidence_sdk_run_handoff_hash: handoff.sdk_handoff_hash(),
            browser_page_search_record_hash: [0; 32],
            candidate_state_capsule_hash: capsule.capsule_hash,
            proof_hash,
        })
    }

    pub fn has_valid_fields(&self) -> bool {
        self.active_task_id > 0
            && self.candidate_count > 0
            && self.candidate_node_count > 0
            && self.candidate_node_count <= self.candidate_count
            && self.context_pack_digest != [0; 32]
            && self.context_pack_node_hash != [0; 32]
            && self.candidate_list_hash != [0; 32]
            && self.candidate_node_hash != [0; 32]
            && self.proof_hash
                == context_pack_candidate_proof_hash(
                    self.active_task_id,
                    self.context_pack_digest,
                    self.context_pack_node_hash,
                    self.candidate_count,
                    self.candidate_node_count,
                    self.candidate_list_hash,
                    self.candidate_node_hash,
                    self.cold_vector_replay_record_hash,
                    self.agentic_evidence_execution_record_hash,
                    self.agentic_evidence_sdk_run_handoff_hash,
                    self.browser_page_search_record_hash,
                    self.candidate_state_capsule_hash,
                )
            && self.proof_hash != [0; 32]
    }

    pub fn is_valid_for(
        &self,
        context_pack: &ContextPack,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
    ) -> bool {
        self.is_valid_for_replay_records(context_pack, candidates, cold_vector_record, None, None)
    }

    pub fn is_valid_for_replay_records(
        &self,
        context_pack: &ContextPack,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> bool {
        self.candidate_state_capsule_hash == [0; 32]
            && self.agentic_evidence_sdk_run_handoff_hash == [0; 32]
            && self.matches_pack_and_candidates(
                context_pack,
                candidates,
                cold_vector_record,
                agentic_record,
                browser_page_search_record,
            )
    }

    fn matches_pack_and_candidates(
        &self,
        context_pack: &ContextPack,
        candidates: &[CandidateEvidenceRef],
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> bool {
        let Ok(candidate_node_ids) = candidate_node_ids_from_refs(candidates) else {
            return false;
        };
        let Ok(expected_cold_record_hash) =
            cold_vector_record_hash_for_candidates(candidates, cold_vector_record)
        else {
            return false;
        };
        let Ok(expected_agentic_record_hash) =
            agentic_record_hash_for_candidates(candidates, agentic_record)
        else {
            return false;
        };
        let Ok(expected_browser_page_search_record_hash) =
            browser_page_search_record_hash_for_candidates(candidates, browser_page_search_record)
        else {
            return false;
        };
        CandidateOnlyGate::validate_replay_bound_context_pack_candidates_with_browser(
            candidates,
            cold_vector_record,
            agentic_record,
            browser_page_search_record,
        )
        .is_ok()
            && self.matches_pack_and_candidates_with_hashes(
                context_pack,
                candidates,
                &candidate_node_ids,
                expected_cold_record_hash,
                expected_agentic_record_hash,
                expected_browser_page_search_record_hash,
            )
    }

    fn matches_pack_and_candidates_with_hashes(
        &self,
        context_pack: &ContextPack,
        candidates: &[CandidateEvidenceRef],
        candidate_node_ids: &[ContextNodeId],
        expected_cold_record_hash: [u8; 32],
        expected_agentic_record_hash: [u8; 32],
        expected_browser_page_search_record_hash: [u8; 32],
    ) -> bool {
        self.has_valid_fields()
            && self.context_pack_digest == context_pack.digest
            && self.context_pack_node_hash
                == context_node_set_hash("candidate-proof-pack", &context_pack.node_ids)
            && self.candidate_count == candidates.len().min(u32::MAX as usize) as u32
            && self.candidate_node_count == candidate_node_ids.len().min(u32::MAX as usize) as u32
            && self.candidate_list_hash == candidate_list_hash(candidates)
            && self.candidate_node_hash
                == context_node_set_hash("candidate-proof-required", candidate_node_ids)
            && self.cold_vector_replay_record_hash == expected_cold_record_hash
            && self.agentic_evidence_execution_record_hash == expected_agentic_record_hash
            && self.browser_page_search_record_hash == expected_browser_page_search_record_hash
            && context_pack
                .node_ids
                .binary_search(&self.active_task_id)
                .is_ok()
            && candidate_node_ids
                .iter()
                .all(|node_id| context_pack.node_ids.binary_search(node_id).is_ok())
    }

    pub fn is_valid_for_capsule(
        &self,
        context_pack: &ContextPack,
        capsule: &CandidateStateCapsule,
        cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
        agentic_record: Option<&AgenticEvidenceExecutionRecord>,
        browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
    ) -> bool {
        self.candidate_state_capsule_hash == capsule.capsule_hash
            && self.agentic_evidence_sdk_run_handoff_hash == [0; 32]
            && capsule.is_valid_for_replay_records(cold_vector_record, agentic_record)
            && self.matches_pack_and_candidates(
                context_pack,
                capsule.candidates(),
                cold_vector_record,
                agentic_record,
                browser_page_search_record,
            )
    }

    pub fn is_valid_for_sdk_run_handoff<H: AgenticEvidenceSdkRunHandoffBinding>(
        &self,
        context_pack: &ContextPack,
        capsule: &CandidateStateCapsule,
        handoff: &H,
    ) -> bool {
        sdk_handoff_matches_capsule(capsule, handoff)
            && self.agentic_evidence_execution_record_hash == handoff.sdk_execution_record_hash()
            && self.agentic_evidence_sdk_run_handoff_hash == handoff.sdk_handoff_hash()
            && self.candidate_state_capsule_hash == capsule.capsule_hash
            && candidate_node_ids_from_refs(capsule.candidates()).is_ok_and(|candidate_node_ids| {
                self.matches_pack_and_candidates_with_hashes(
                    context_pack,
                    capsule.candidates(),
                    &candidate_node_ids,
                    [0; 32],
                    handoff.sdk_execution_record_hash(),
                    [0; 32],
                )
            })
    }
}

fn candidate_order(left: &ContextNode, right: &ContextNode) -> Ordering {
    retention_rank(left.retention_class)
        .cmp(&retention_rank(right.retention_class))
        .then_with(|| {
            let left_utility = left.deterministic_utility();
            let right_utility = right.deterministic_utility();
            right_utility
                .saturating_mul(left.token_cost as u64)
                .cmp(&left_utility.saturating_mul(right.token_cost as u64))
                .then_with(|| right_utility.cmp(&left_utility))
                .then_with(|| left.token_cost.cmp(&right.token_cost))
                .then_with(|| left.node_id.cmp(&right.node_id))
        })
}

fn retention_rank(class: ContextRetentionClass) -> u8 {
    match class {
        ContextRetentionClass::Protected => 0,
        ContextRetentionClass::Condensable => 1,
        ContextRetentionClass::Ephemeral => 2,
    }
}

fn canonical_ids(ids: &[ContextNodeId]) -> Vec<ContextNodeId> {
    let mut canonical: Vec<ContextNodeId> = ids.to_vec();
    canonical.sort_unstable();
    canonical.dedup();
    canonical
}

fn canonical_ids_with_active(
    active_task_id: ContextNodeId,
    required_evidence_refs: &[ContextNodeId],
) -> Vec<ContextNodeId> {
    let mut required = Vec::with_capacity(required_evidence_refs.len().saturating_add(1));
    required.push(active_task_id);
    required.extend_from_slice(required_evidence_refs);
    canonical_ids(&required)
}

fn sorted_unique_ids(ids: &[ContextNodeId]) -> bool {
    ids.windows(2).all(|window| window[0] < window[1])
}

fn disjoint_sorted_ids(left: &[ContextNodeId], right: &[ContextNodeId]) -> bool {
    let mut left_index = 0usize;
    let mut right_index = 0usize;
    while left_index < left.len() && right_index < right.len() {
        match left[left_index].cmp(&right[right_index]) {
            Ordering::Less => left_index += 1,
            Ordering::Greater => right_index += 1,
            Ordering::Equal => return false,
        }
    }
    true
}

pub fn context_node_set_hash(label: &str, node_ids: &[ContextNodeId]) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-context-node-set-v1");
    hasher.update(label.as_bytes());
    hasher.update(&(node_ids.len() as u64).to_le_bytes());
    for node_id in node_ids {
        hasher.update(&node_id.to_le_bytes());
    }
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn context_fold_record_hash(
    active_task_id: ContextNodeId,
    retained_node_ids: &[ContextNodeId],
    folded_node_ids: &[ContextNodeId],
    retained_token_count: u32,
    folded_token_count: u32,
    retained_utility_score: u64,
    activation_node_count: usize,
    context_pack_digest: [u8; 32],
    retained_node_hash: [u8; 32],
    folded_node_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-context-fold-record-v1");
    hasher.update(&active_task_id.to_le_bytes());
    hasher.update(&(retained_node_ids.len() as u64).to_le_bytes());
    hasher.update(&(folded_node_ids.len() as u64).to_le_bytes());
    hasher.update(&retained_token_count.to_le_bytes());
    hasher.update(&folded_token_count.to_le_bytes());
    hasher.update(&retained_utility_score.to_le_bytes());
    hasher.update(&(activation_node_count as u64).to_le_bytes());
    hasher.update(&context_pack_digest);
    hasher.update(&retained_node_hash);
    hasher.update(&folded_node_hash);
    for node_id in retained_node_ids {
        hasher.update(&node_id.to_le_bytes());
    }
    for node_id in folded_node_ids {
        hasher.update(&node_id.to_le_bytes());
    }
    *hasher.finalize().as_bytes()
}

#[allow(clippy::too_many_arguments)]
pub fn context_pack_candidate_proof_hash(
    active_task_id: ContextNodeId,
    context_pack_digest: [u8; 32],
    context_pack_node_hash: [u8; 32],
    candidate_count: u32,
    candidate_node_count: u32,
    candidate_list_hash: [u8; 32],
    candidate_node_hash: [u8; 32],
    cold_vector_replay_record_hash: [u8; 32],
    agentic_evidence_execution_record_hash: [u8; 32],
    agentic_evidence_sdk_run_handoff_hash: [u8; 32],
    browser_page_search_record_hash: [u8; 32],
    candidate_state_capsule_hash: [u8; 32],
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    hasher.update(b"aegis-context-pack-candidate-proof-v5");
    hasher.update(&active_task_id.to_le_bytes());
    hasher.update(&context_pack_digest);
    hasher.update(&context_pack_node_hash);
    hasher.update(&candidate_count.to_le_bytes());
    hasher.update(&candidate_node_count.to_le_bytes());
    hasher.update(&candidate_list_hash);
    hasher.update(&candidate_node_hash);
    hasher.update(&cold_vector_replay_record_hash);
    hasher.update(&agentic_evidence_execution_record_hash);
    hasher.update(&agentic_evidence_sdk_run_handoff_hash);
    hasher.update(&browser_page_search_record_hash);
    hasher.update(&candidate_state_capsule_hash);
    *hasher.finalize().as_bytes()
}

fn sdk_handoff_matches_capsule<H: AgenticEvidenceSdkRunHandoffBinding>(
    capsule: &CandidateStateCapsule,
    handoff: &H,
) -> bool {
    capsule.capsule_hash != [0; 32]
        && handoff.sdk_handoff_hash() != [0; 32]
        && handoff.sdk_execution_record_hash() != [0; 32]
        && handoff.sdk_candidate_list_hash() != [0; 32]
        && handoff.sdk_candidate_count() > 0
        && handoff.sdk_capsule_hash() == capsule.capsule_hash
        && handoff.sdk_execution_record_hash() == capsule.agentic_evidence_execution_record_hash
        && handoff.sdk_candidate_list_hash() == capsule.candidate_list_hash
        && handoff.sdk_candidate_count() == capsule.candidate_count
        && capsule.cold_vector_replay_record_hash == [0; 32]
        && capsule.candidates().iter().all(|candidate| {
            candidate.origin_tier == EvidenceCandidateTier::AgenticProgramOutput
                && candidate.index_epoch_hash == capsule.index_epoch_hash
        })
}

fn candidate_node_ids_from_refs(
    candidates: &[CandidateEvidenceRef],
) -> Result<Vec<ContextNodeId>, ContextGovernorError> {
    if candidates.is_empty() {
        return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
    }
    let mut node_ids = Vec::with_capacity(candidates.len());
    for candidate in candidates {
        if !candidate.is_valid() || candidate.segment_id == 0 {
            return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
        }
        node_ids.push(candidate.segment_id as ContextNodeId);
    }
    node_ids.sort_unstable();
    node_ids.dedup();
    if node_ids.is_empty() {
        return Err(ContextGovernorError::InvalidCandidateEvidenceProof);
    }
    Ok(node_ids)
}

fn cold_vector_record_hash_for_candidates(
    candidates: &[CandidateEvidenceRef],
    cold_vector_record: Option<&ColdVectorExpansionReplayRecord>,
) -> Result<[u8; 32], ContextGovernorError> {
    let has_cold_candidate = candidates
        .iter()
        .any(|candidate| candidate.origin_tier == EvidenceCandidateTier::ColdVectorExpansion);
    if !has_cold_candidate {
        return Ok([0; 32]);
    }
    let record = cold_vector_record.ok_or(ContextGovernorError::CandidateEvidenceGate(
        CandidateOnlyGateError::MissingReplayEvent,
    ))?;
    if !record.is_valid() {
        return Err(ContextGovernorError::CandidateEvidenceGate(
            CandidateOnlyGateError::ReplayRecordMismatch,
        ));
    }
    Ok(record.record_hash)
}

fn agentic_record_hash_for_candidates(
    candidates: &[CandidateEvidenceRef],
    agentic_record: Option<&AgenticEvidenceExecutionRecord>,
) -> Result<[u8; 32], ContextGovernorError> {
    let agentic_candidates: Vec<CandidateEvidenceRef> = candidates
        .iter()
        .copied()
        .filter(|candidate| candidate.origin_tier == EvidenceCandidateTier::AgenticProgramOutput)
        .collect();
    if agentic_candidates.is_empty() {
        return Ok([0; 32]);
    }
    let record = agentic_record.ok_or(ContextGovernorError::CandidateEvidenceGate(
        CandidateOnlyGateError::MissingReplayEvent,
    ))?;
    if !record.is_valid()
        || record.record_hash != agentic_evidence_execution_record_hash(record)
        || record.candidate_count != agentic_candidates.len().min(u32::MAX as usize) as u32
        || record.candidate_list_hash != candidate_list_hash(&agentic_candidates)
        || agentic_candidates
            .iter()
            .any(|candidate| candidate.index_epoch_hash != record.index_epoch_hash)
    {
        return Err(ContextGovernorError::CandidateEvidenceGate(
            CandidateOnlyGateError::ReplayRecordMismatch,
        ));
    }
    Ok(record.record_hash)
}

fn browser_page_search_record_hash_for_candidates(
    candidates: &[CandidateEvidenceRef],
    browser_page_search_record: Option<&BrowserPageSearchCandidateRecord>,
) -> Result<[u8; 32], ContextGovernorError> {
    let browser_candidates: Vec<CandidateEvidenceRef> = candidates
        .iter()
        .copied()
        .filter(|candidate| candidate.origin_tier == EvidenceCandidateTier::BrowserPageSearch)
        .collect();
    if browser_candidates.is_empty() {
        return Ok([0; 32]);
    }
    let record = browser_page_search_record.ok_or(ContextGovernorError::CandidateEvidenceGate(
        CandidateOnlyGateError::MissingReplayEvent,
    ))?;
    if !record.matches_candidates(&browser_candidates) {
        return Err(ContextGovernorError::CandidateEvidenceGate(
            CandidateOnlyGateError::ReplayRecordMismatch,
        ));
    }
    Ok(record.record_hash)
}

fn context_pack_digest(
    node_ids: &[ContextNodeId],
    token_count: u32,
    utility_score: u64,
) -> [u8; 32] {
    let mut hasher = Hasher::new();
    for node_id in node_ids {
        hasher.update(&node_id.to_le_bytes());
    }
    hasher.update(&token_count.to_le_bytes());
    hasher.update(&utility_score.to_le_bytes());
    *hasher.finalize().as_bytes()
}
