pub struct MemoryFrame {
    pub frame_id: u128,
    pub session_id: u128,
    pub payload: Vec<u8>,
    pub fidelity: f32,
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
pub struct MemoryGraph {
    pub nodes: Vec<SemanticNode>,
    pub edges: Vec<MemoryEdge>,
}

impl MemoryGraph {
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            edges: Vec::new(),
        }
    }

    pub fn add_node(&mut self, node: SemanticNode) -> Result<(), &'static str> {
        if !node.is_valid() {
            return Err("invalid semantic node");
        }
        if !self
            .nodes
            .iter()
            .any(|existing| existing.node_id == node.node_id)
        {
            self.nodes.push(node);
        }
        Ok(())
    }

    pub fn add_edge(&mut self, edge: MemoryEdge) -> Result<(), &'static str> {
        if !edge.is_valid() {
            return Err("invalid memory edge");
        }
        self.edges.push(edge);
        Ok(())
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
