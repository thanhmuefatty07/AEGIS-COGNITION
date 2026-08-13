use crate::schema::{BinarySchemaId, NERVE_SCHEMA};

pub struct BinaryFrameDescriptor {
    pub schema_id: BinarySchemaId,
    pub header_bytes: usize,
    pub payload_alignment: usize,
    pub payload_len: usize,
}

impl BinaryFrameDescriptor {
    pub fn new(payload_len: usize) -> Self {
        Self {
            schema_id: NERVE_SCHEMA.schema_id,
            header_bytes: 64,
            payload_alignment: NERVE_SCHEMA.alignment,
            payload_len,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.schema_id == NERVE_SCHEMA.schema_id
            && self.header_bytes == 64
            && self.payload_alignment == NERVE_SCHEMA.alignment
            && self.payload_alignment.is_power_of_two()
            && self.payload_len > 0
    }

    pub fn message_size(&self) -> usize {
        self.header_bytes + self.payload_len
    }
}
