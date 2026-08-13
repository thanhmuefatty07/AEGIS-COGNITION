pub struct VerificationResult {
    pub request_id: u128,
    pub accepted_prefix_len: usize,
    pub rejected: bool,
}

impl VerificationResult {
    pub fn accepts_anything(&self) -> bool {
        !self.rejected && self.accepted_prefix_len > 0
    }

    pub fn reject(request_id: u128) -> Self {
        Self {
            request_id,
            accepted_prefix_len: 0,
            rejected: true,
        }
    }

    pub fn accept(request_id: u128, accepted_prefix_len: usize) -> Self {
        Self {
            request_id,
            accepted_prefix_len,
            rejected: false,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.request_id > 0 && (self.rejected || self.accepted_prefix_len > 0)
    }
}

pub fn verify_target_prefix(
    draft: &crate::speculative::draft::DraftTokenBatch,
    target: &crate::speculative::draft::TargetTokenBatch,
) -> VerificationResult {
    if !draft.is_valid() || !target.is_valid() || draft.request_id != target.request_id {
        return VerificationResult::reject(draft.request_id);
    }

    let accepted_prefix_len = draft
        .tokens
        .iter()
        .zip(target.tokens.iter())
        .take_while(|(draft_token, target_token)| draft_token == target_token)
        .count();

    if accepted_prefix_len == 0 {
        VerificationResult::reject(draft.request_id)
    } else {
        VerificationResult::accept(draft.request_id, accepted_prefix_len)
    }
}
