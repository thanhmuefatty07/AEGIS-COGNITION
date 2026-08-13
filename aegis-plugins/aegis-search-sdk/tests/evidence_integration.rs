use aegis_evidence::{EvidenceBinding, EvidenceStore};
use aegis_nerve::evidence_index::HotEvidenceIndex;
use aegis_search_sdk::EvidenceSearch;

fn hash(label: &str) -> [u8; 32] {
    *blake3::hash(label.as_bytes()).as_bytes()
}

#[test]
fn retrieved_candidate_can_be_bound_into_evidence_audit() {
    let evidence_ref_hash = hash("ref");
    let artifact_hash = hash("artifact");
    let ast_signature_hash = hash("ast");
    let mut index = HotEvidenceIndex::new(hash("epoch")).unwrap();
    index
        .insert_artifact_document(
            evidence_ref_hash,
            3,
            artifact_hash,
            ast_signature_hash,
            "browser research evidence",
        )
        .unwrap();

    let search = EvidenceSearch::new(&index);
    let candidates = search.search_evidence(None, &["research"], 8);
    assert!(search.verify_binding(
        &candidates[0],
        evidence_ref_hash,
        artifact_hash,
        ast_signature_hash
    ));

    let mut store = EvidenceStore::new();
    store
        .bind(
            EvidenceBinding::new(evidence_ref_hash, artifact_hash, ast_signature_hash, 1).unwrap(),
        )
        .unwrap();
    assert!(store.verify_audit_chain().is_ok());
}
