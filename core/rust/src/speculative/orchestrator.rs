use super::draft::{DraftTokenBatch, TargetTokenBatch};
use super::verifier::{verify_target_prefix, VerificationResult};

pub fn speculative_decode(prompt: &str) -> (DraftTokenBatch, VerificationResult) {
    let seed = prompt.len() as u128 + 1;
    let draft = DraftTokenBatch {
        request_id: seed,
        tokens: draft_tokens(prompt),
    };
    let target = TargetTokenBatch {
        request_id: seed,
        tokens: target_tokens(&draft),
    };
    let verification = verify_target_prefix(&draft, &target);
    (draft, verification)
}

pub fn fallback_decode(request_id: u128) -> VerificationResult {
    VerificationResult::reject(request_id)
}

pub fn accept_prefix(draft: &DraftTokenBatch, verification: &VerificationResult) -> Vec<u32> {
    if !draft.is_valid()
        || !verification.is_valid()
        || verification.rejected
        || draft.request_id != verification.request_id
    {
        return Vec::new();
    }
    let prefix_len = verification.accepted_prefix_len.min(draft.tokens.len());
    draft.tokens[..prefix_len].to_vec()
}

pub fn speculative_round(prompt: &str) -> usize {
    let (draft, verification) = speculative_decode(prompt);
    accept_prefix(&draft, &verification).len()
}

fn draft_tokens(prompt: &str) -> Vec<u32> {
    if prompt.trim().is_empty() {
        return vec![0];
    }
    let mut state = 0x811C_9DC5u32;
    for byte in prompt.as_bytes() {
        state ^= *byte as u32;
        state = state.wrapping_mul(0x0100_0193);
    }
    vec![
        state,
        state.rotate_left(7) ^ 0xA5A5_5A5A,
        state.rotate_left(13) ^ prompt.len() as u32,
    ]
}

fn target_tokens(draft: &DraftTokenBatch) -> Vec<u32> {
    if draft.tokens.len() < 3 {
        return draft.tokens.clone();
    }
    vec![
        draft.tokens[0],
        draft.tokens[1],
        draft.tokens[2] ^ 0xFFFF_0000,
    ]
}
