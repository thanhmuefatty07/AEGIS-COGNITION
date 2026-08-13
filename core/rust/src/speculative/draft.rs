pub struct DraftTokenBatch {
    pub request_id: u128,
    pub tokens: Vec<u32>,
}

pub struct TargetTokenBatch {
    pub request_id: u128,
    pub tokens: Vec<u32>,
}

impl DraftTokenBatch {
    pub fn is_valid(&self) -> bool {
        !self.tokens.is_empty() && self.request_id > 0
    }

    pub fn len(&self) -> usize {
        self.tokens.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tokens.is_empty()
    }

    pub fn prefix_capacity(&self) -> usize {
        self.tokens.len()
    }
}

impl TargetTokenBatch {
    pub fn is_valid(&self) -> bool {
        !self.tokens.is_empty() && self.request_id > 0
    }
}
