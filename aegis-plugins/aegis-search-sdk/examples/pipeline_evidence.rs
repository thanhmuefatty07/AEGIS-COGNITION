use aegis_nerve::evidence_index::HotEvidenceIndex;
use aegis_search_sdk::EvidenceSearch;

fn hash(label: &str) -> [u8; 32] {
    *blake3::hash(label.as_bytes()).as_bytes()
}

fn main() {
    let mut index = HotEvidenceIndex::new(hash("epoch")).unwrap();
    index
        .insert_artifact_document(
            hash("ref"),
            1,
            hash("artifact"),
            hash("ast"),
            "artifact-bound policy evidence",
        )
        .unwrap();
    let results = EvidenceSearch::new(&index).search_evidence(None, &["policy"], 8);
    println!("{}", results.len());
}
