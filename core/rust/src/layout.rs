use crate::schema::NERVE_SCHEMA;

pub fn expected_header_bytes() -> usize {
    64
}

pub fn expected_payload_alignment() -> usize {
    NERVE_SCHEMA.alignment
}

pub fn is_expected_alignment(alignment: usize) -> bool {
    alignment == expected_payload_alignment() && alignment.is_power_of_two()
}

pub fn is_expected_header_size(header_size: usize) -> bool {
    header_size == expected_header_bytes()
}

pub fn expected_message_metadata_size() -> usize {
    expected_header_bytes()
}

pub fn validate_layout(header_size: usize, alignment: usize) -> bool {
    is_expected_header_size(header_size) && is_expected_alignment(alignment)
}

pub fn validate_message_shape(header_size: usize, alignment: usize, payload_len: usize) -> bool {
    validate_layout(header_size, alignment) && payload_len > 0
}
