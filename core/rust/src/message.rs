use crate::schema::{BinarySchemaId, NERVE_SCHEMA};

pub struct MessageHeader {
    pub message_id: u128,
    pub session_id: u128,
    pub schema_id: BinarySchemaId,
    pub version: u32,
    pub payload_len: usize,
    pub alignment: usize,
}

pub struct MessageFrame {
    pub header: MessageHeader,
    pub payload: Vec<u8>,
}

impl MessageHeader {
    pub fn is_valid(&self) -> bool {
        self.schema_id == NERVE_SCHEMA.schema_id
            && self.version == NERVE_SCHEMA.version
            && self.alignment == NERVE_SCHEMA.alignment
            && self.alignment.is_power_of_two()
            && self.payload_len > 0
            && self.message_id > 0
            && self.session_id > 0
    }
}

impl MessageFrame {
    pub fn new(payload: Vec<u8>) -> Self {
        Self::with_identity(1, 1, payload)
    }

    pub fn with_identity(message_id: u128, session_id: u128, payload: Vec<u8>) -> Self {
        let payload_len = payload.len();
        Self {
            header: MessageHeader {
                message_id,
                session_id,
                schema_id: NERVE_SCHEMA.schema_id,
                version: NERVE_SCHEMA.version,
                payload_len,
                alignment: NERVE_SCHEMA.alignment,
            },
            payload,
        }
    }

    pub fn with_capacity(message_id: u128, session_id: u128, capacity: usize) -> Self {
        let payload = Vec::with_capacity(capacity);
        Self {
            header: MessageHeader {
                message_id,
                session_id,
                schema_id: NERVE_SCHEMA.schema_id,
                version: NERVE_SCHEMA.version,
                payload_len: 0,
                alignment: NERVE_SCHEMA.alignment,
            },
            payload,
        }
    }

    pub fn is_valid(&self) -> bool {
        self.header.is_valid() && self.payload.len() == self.header.payload_len
    }
}
