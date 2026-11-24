use std::collections::BTreeMap;

pub struct MemoryFrame {
    pub frame_id: u128,
    pub session_id: u128,
    pub payload: Vec<u8>,
    pub fidelity: f32,
}

#[derive(
    Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd, serde::Serialize, serde::Deserialize,
)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum EdgeProvenance {
    Extracted,
    Inferred,
    Ambiguous,
}

impl EdgeProvenance {
    pub fn default_confidence(self) -> f32 {
        match self {
            Self::Extracted => 1.0,
            Self::Inferred => 0.75,
            Self::Ambiguous => 0.35,
        }
    }

    pub fn transition_multiplier(self, confidence: f32) -> f32 {
        let clamped = if confidence.is_finite() {
            confidence.clamp(0.0, 1.0)
        } else {
            0.0
        };
        match self {
            Self::Extracted => clamped.max(0.85),
            Self::Inferred => 0.75 * clamped,
            Self::Ambiguous => (0.35 * clamped).min(0.35),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct SemanticNode {
    pub node_id: u128,
    pub session_id: u128,
    pub artifact_hash: [u8; 32],
    pub ast_fingerprint: u64,
    pub fidelity: f32,
}

impl SemanticNode {
    pub fn is_valid(&self) -> bool {
        self.node_id > 0
            && self.session_id > 0
            && self.artifact_hash.iter().any(|byte| *byte != 0)
            && self.fidelity.is_finite()
            && (0.0..=1.0).contains(&self.fidelity)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SemanticPointer {
    pub node_id: u128,
    pub session_id: u128,
    pub artifact_hash: [u8; 32],
}

impl SemanticPointer {
    pub fn from_node(node: &SemanticNode) -> Self {
        Self {
            node_id: node.node_id,
            session_id: node.session_id,
            artifact_hash: node.artifact_hash,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.node_id > 0 && self.session_id > 0 && self.artifact_hash.iter().any(|byte| *byte != 0)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ContextPrefix {
    pub session_id: u128,
    pub node_id: u128,
    pub prefix: String,
}

impl ContextPrefix {
    pub fn is_valid(&self) -> bool {
        self.session_id > 0
            && self.node_id > 0
            && self.prefix.starts_with("AEGIS_PREFIX:")
            && !self.prefix.contains("KV_CACHE")
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MemoryEdge {
    pub edge_id: u128,
    pub from_node: u128,
    pub to_node: u128,
    pub weight: f32,
    pub time_since_last_access: f32,
}

impl MemoryEdge {
    pub fn is_valid(&self) -> bool {
        self.edge_id > 0
            && self.from_node > 0
            && self.to_node > 0
            && self.from_node != self.to_node
            && self.weight.is_finite()
            && self.weight >= 0.0
            && self.time_since_last_access.is_finite()
            && self.time_since_last_access >= 0.0
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct TemporalMemoryEdge {
    pub edge_id: u128,
    pub from_node: u128,
    pub to_node: u128,
    pub relation_kind: String,
    pub provenance: EdgeProvenance,
    pub confidence: f32,
    pub weight: f32,
    pub valid_from_ms: u64,
    pub invalidated_at_ms: Option<u64>,
}

impl TemporalMemoryEdge {
    pub fn is_valid(&self) -> bool {
        self.edge_id > 0
            && self.from_node > 0
            && self.to_node > 0
            && self.from_node != self.to_node
            && !self.relation_kind.trim().is_empty()
            && self.relation_kind.len() <= 128
            && self.confidence.is_finite()
            && (0.0..=1.0).contains(&self.confidence)
            && self.weight.is_finite()
            && self.weight >= 0.0
            && self.valid_from_ms > 0
            && self
                .invalidated_at_ms
                .is_none_or(|invalidated| invalidated >= self.valid_from_ms)
    }

    pub fn is_active_at(&self, timestamp_ms: u64) -> bool {
        timestamp_ms >= self.valid_from_ms
            && self
                .invalidated_at_ms
                .is_none_or(|invalidated| timestamp_ms < invalidated)
    }

    pub fn effective_weight(&self) -> f32 {
        (self.weight * self.provenance.transition_multiplier(self.confidence)).clamp(0.0, 1.0)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct CsrGraphSnapshot {
    pub node_ids: Vec<u128>,
    pub row_offsets: Vec<usize>,
    pub col_indices: Vec<usize>,
    pub weights: Vec<f32>,
}

impl CsrGraphSnapshot {
    pub fn neighbors(&self, row: usize) -> (&[usize], &[f32]) {
        if row + 1 >= self.row_offsets.len() {
            return (&[], &[]);
        }
        let start = self.row_offsets[row];
        let end = self.row_offsets[row + 1];
        (&self.col_indices[start..end], &self.weights[start..end])
    }
}

#[derive(Clone, Debug)]
pub struct MemoryGraph {
    pub nodes: Vec<SemanticNode>,
    pub edges: Vec<MemoryEdge>,
    node_index: BTreeMap<u128, usize>,
    edge_index: BTreeMap<u128, usize>,
    temporal_edges: BTreeMap<u128, TemporalMemoryEdge>,
}

impl PartialEq for MemoryGraph {
    fn eq(&self, other: &Self) -> bool {
        self.nodes == other.nodes
            && self.edges == other.edges
            && self.temporal_edges == other.temporal_edges
    }
}

impl MemoryGraph {
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
            node_index: BTreeMap::new(),
            edge_index: BTreeMap::new(),
            temporal_edges: BTreeMap::new(),
        }
    }

    pub fn contains_node(&self, node_id: u128) -> bool {
        if self.node_index.len() == self.nodes.len() {
            self.node_index.contains_key(&node_id)
        } else {
            self.nodes.iter().any(|node| node.node_id == node_id)
        }
    }

    pub fn get_node(&self, node_id: u128) -> Option<&SemanticNode> {
        if self.node_index.len() == self.nodes.len() {
            self.node_index
                .get(&node_id)
                .and_then(|&idx| self.nodes.get(idx))
        } else {
            self.nodes.iter().find(|node| node.node_id == node_id)
        }
    }

    pub fn get_edge(&self, edge_id: u128) -> Option<&MemoryEdge> {
        if self.edge_index.len() == self.edges.len() {
            self.edge_index
                .get(&edge_id)
                .and_then(|&idx| self.edges.get(idx))
        } else {
            self.edges.iter().find(|edge| edge.edge_id == edge_id)
        }
    }

    pub fn rebuild_indices(&mut self) {
        self.node_index.clear();
        for (idx, node) in self.nodes.iter().enumerate() {
            self.node_index.insert(node.node_id, idx);
        }
        self.edge_index.clear();
        for (idx, edge) in self.edges.iter().enumerate() {
            self.edge_index.insert(edge.edge_id, idx);
        }
    }

    pub fn add_node(&mut self, node: SemanticNode) -> Result<(), &'static str> {
        if !node.is_valid() {
            return Err("invalid semantic node");
        }
        if self.node_index.len() != self.nodes.len() {
            self.rebuild_indices();
        }
        if let Some(&idx) = self.node_index.get(&node.node_id) {
            let existing = &mut self.nodes[idx];
            existing.session_id = node.session_id;
            existing.artifact_hash = node.artifact_hash;
            existing.ast_fingerprint = node.ast_fingerprint;
            existing.fidelity = existing.fidelity.max(node.fidelity).clamp(0.0, 1.0);
        } else {
            let idx = self.nodes.len();
            self.node_index.insert(node.node_id, idx);
            self.nodes.push(node);
        }
        Ok(())
    }

    pub fn add_edge(&mut self, edge: MemoryEdge) -> Result<(), &'static str> {
        if !edge.is_valid() {
            return Err("invalid memory edge");
        }
        if self.node_index.len() != self.nodes.len() || self.edge_index.len() != self.edges.len() {
            self.rebuild_indices();
        }
        if !self.node_index.contains_key(&edge.from_node)
            || !self.node_index.contains_key(&edge.to_node)
        {
            return Err("missing endpoint node in memory graph");
        }
        if let Some(&idx) = self.edge_index.get(&edge.edge_id) {
            let existing = &mut self.edges[idx];
            existing.from_node = edge.from_node;
            existing.to_node = edge.to_node;
            existing.weight = existing.weight.max(edge.weight).clamp(0.0, 1.0);
            existing.time_since_last_access = existing
                .time_since_last_access
                .min(edge.time_since_last_access);
        } else {
            let idx = self.edges.len();
            self.edge_index.insert(edge.edge_id, idx);
            self.edges.push(edge);
        }
        Ok(())
    }

    pub fn add_temporal_edge(&mut self, edge: TemporalMemoryEdge) -> Result<(), &'static str> {
        if !edge.is_valid() {
            return Err("invalid temporal memory edge");
        }
        if self.node_index.len() != self.nodes.len() {
            self.rebuild_indices();
        }
        if !self.node_index.contains_key(&edge.from_node)
            || !self.node_index.contains_key(&edge.to_node)
        {
            return Err("missing endpoint node in memory graph");
        }
        self.add_edge(MemoryEdge {
            edge_id: edge.edge_id,
            from_node: edge.from_node,
            to_node: edge.to_node,
            weight: edge.effective_weight(),
            time_since_last_access: 0.0,
        })?;
        self.temporal_edges.insert(edge.edge_id, edge);
        Ok(())
    }

    pub fn invalidate_temporal_edge(
        &mut self,
        edge_id: u128,
        invalidated_at_ms: u64,
    ) -> Result<bool, &'static str> {
        let Some(temporal) = self.temporal_edges.get_mut(&edge_id) else {
            return Ok(false);
        };
        if invalidated_at_ms < temporal.valid_from_ms {
            return Err("invalidation timestamp precedes valid_from_ms");
        }
        temporal.invalidated_at_ms = Some(invalidated_at_ms);
        Ok(true)
    }

    pub fn active_temporal_edges_at(&self, timestamp_ms: u64) -> Vec<&TemporalMemoryEdge> {
        self.temporal_edges
            .values()
            .filter(|edge| edge.is_active_at(timestamp_ms))
            .collect()
    }

    pub fn build_csr_snapshot(&self, at_timestamp_ms: Option<u64>) -> CsrGraphSnapshot {
        let mut node_ids: Vec<u128> = self.nodes.iter().map(|node| node.node_id).collect();
        node_ids.sort_unstable();
        node_ids.dedup();
        let id_to_idx: BTreeMap<u128, usize> = node_ids
            .iter()
            .copied()
            .enumerate()
            .map(|(idx, id)| (id, idx))
            .collect();

        let mut adj: Vec<Vec<(usize, f32)>> = vec![Vec::new(); node_ids.len()];
        for edge in &self.edges {
            if let Some(ts) = at_timestamp_ms {
                if let Some(temporal) = self.temporal_edges.get(&edge.edge_id) {
                    if !temporal.is_active_at(ts) {
                        continue;
                    }
                }
            }
            let Some(&u) = id_to_idx.get(&edge.from_node) else {
                continue;
            };
            let Some(&v) = id_to_idx.get(&edge.to_node) else {
                continue;
            };
            let weight = if let Some(temporal) = self.temporal_edges.get(&edge.edge_id) {
                temporal.effective_weight()
            } else {
                edge.weight.clamp(0.0, 1.0)
            };
            if weight > 0.0 {
                adj[u].push((v, weight));
            }
        }

        let mut row_offsets = Vec::with_capacity(node_ids.len() + 1);
        let mut col_indices = Vec::new();
        let mut weights = Vec::new();
        row_offsets.push(0);
        for neighbors in &mut adj {
            neighbors.sort_by(|a, b| a.0.cmp(&b.0));
            let mut merged: Vec<(usize, f32)> = Vec::with_capacity(neighbors.len());
            for &(col, w) in neighbors.iter() {
                if let Some(last) = merged.last_mut().filter(|last| last.0 == col) {
                    last.1 = last.1.max(w);
                } else {
                    merged.push((col, w));
                }
            }
            for (col, w) in merged {
                col_indices.push(col);
                weights.push(w);
            }
            row_offsets.push(col_indices.len());
        }
        CsrGraphSnapshot {
            node_ids,
            row_offsets,
            col_indices,
            weights,
        }
    }
}

impl Default for MemoryGraph {
    fn default() -> Self {
        Self::new()
    }
}

impl MemoryFrame {
    pub fn is_valid(&self) -> bool {
        self.frame_id > 0
            && self.session_id > 0
            && !self.payload.is_empty()
            && self.fidelity.is_finite()
            && (0.0..=1.0).contains(&self.fidelity)
    }

    pub fn payload_len(&self) -> usize {
        self.payload.len()
    }
}
