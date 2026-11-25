#![no_main]

use aegis_nerve::memory::frame::{
    EdgeProvenance, MemoryEdge, MemoryGraph, SemanticNode, TemporalMemoryEdge,
};
use aegis_nerve::memory::hypergraph::{
    DedupGuardConfig, HyperEdge, HyperNode, HyperNodeKind, MemoryHypergraph,
    RetrievalAblationConfig,
};
use libfuzzer_sys::fuzz_target;

fuzz_target!(|payload: &[u8]| {
    if let Ok(nodes) = serde_json::from_slice::<Vec<HyperNode>>(payload) {
        let mut hg = MemoryHypergraph::new();
        for node in nodes.into_iter().take(64) {
            let _ = hg.upsert_node(node);
        }
        let _ = hg.deduplicate_entities(DedupGuardConfig::default());
        let _ = hg.detect_communities_leiden(8, None);
        let _ = hg.hybrid_retrieve("fuzz query", 8, None, RetrievalAblationConfig::default());
    }

    let mut graph = MemoryGraph::new();
    let mut hg = MemoryHypergraph::new();
    for (idx, chunk) in payload.chunks(4).take(48).enumerate() {
        let id = (idx as u128) + 1;
        let kind = match chunk.first().copied().unwrap_or(0) % 8 {
            0 => HyperNodeKind::Symbol,
            1 => HyperNodeKind::File,
            2 => HyperNodeKind::Module,
            3 => HyperNodeKind::Concept,
            4 => HyperNodeKind::Memory,
            5 => HyperNodeKind::Session,
            6 => HyperNodeKind::Task,
            _ => HyperNodeKind::Evidence,
        };
        let label = format!("node_{}_{}", idx, chunk.get(1).copied().unwrap_or(0));
        let mut hnode = HyperNode::new(id, &label, kind);
        hnode.quantized_embedding = Some(vec![chunk.get(2).copied().unwrap_or(1) as i8; 8]);
        let _ = hg.upsert_node(hnode);

        let _ = graph.add_node(SemanticNode {
            node_id: id,
            session_id: 1,
            artifact_hash: [chunk.first().copied().unwrap_or(1).max(1); 32],
            ast_fingerprint: u64::from(idx as u64 + 1),
            fidelity: (f32::from(chunk.get(3).copied().unwrap_or(128)) / 255.0).clamp(0.0, 1.0),
        });
    }
    for (idx, chunk) in payload.chunks(3).take(48).enumerate() {
        let from = ((chunk.first().copied().unwrap_or(0) as u128) % 48) + 1;
        let to = ((chunk.get(1).copied().unwrap_or(1) as u128) % 48) + 1;
        let prov = match chunk.get(2).copied().unwrap_or(0) % 3 {
            0 => EdgeProvenance::Extracted,
            1 => EdgeProvenance::Inferred,
            _ => EdgeProvenance::Ambiguous,
        };
        let edge = HyperEdge::binary((idx as u128) + 1, from, to, "calls", prov, 0.8, 0.7, 100);
        let _ = hg.upsert_hyperedge(edge);
        let _ = graph.add_edge(MemoryEdge {
            edge_id: (idx as u128) + 1,
            from_node: from,
            to_node: to,
            weight: 0.7,
            time_since_last_access: 1.0,
        });
        let _ = graph.add_temporal_edge(TemporalMemoryEdge {
            edge_id: (idx as u128) + 1000,
            from_node: from,
            to_node: to,
            relation_kind: "relates".to_string(),
            provenance: prov,
            confidence: 0.8,
            weight: 0.5,
            valid_from_ms: 10,
            invalidated_at_ms: None,
        });
    }
    let _ = graph.build_csr_snapshot(None);
    let _ = hg.detect_communities_leiden(8, None);
    let _ = hg.hybrid_retrieve("node_0", 5, Some(100), RetrievalAblationConfig::default());
});
