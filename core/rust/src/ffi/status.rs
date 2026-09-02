//! Stable status and layout validation bindings.
//!
//! These functions are intentionally side-effect free.  Keeping them in a
//! small family makes the public PyO3 registration easy to audit without
//! changing any exported symbol or wire contract.

use super::py_safe;
use pyo3::prelude::*;

#[pyfunction]
pub fn aegis_status() -> PyResult<&'static str> {
    py_safe(|| "aegis-nerve-ready")
}

#[pyfunction]
pub fn aegis_validate_schema(schema_id: u64, version: u32) -> PyResult<bool> {
    py_safe(|| schema_id == 0xAE1515 && version == 1)
}

#[pyfunction]
pub fn aegis_validate_layout(header_bytes: usize, alignment: usize) -> PyResult<bool> {
    py_safe(|| header_bytes == 64 && alignment == 64 && alignment.is_power_of_two())
}

#[pyfunction]
pub fn aegis_memory_alignment(alignment: usize) -> PyResult<bool> {
    py_safe(|| alignment == 64)
}

#[pyfunction]
pub fn aegis_nerve_schema_id() -> PyResult<u64> {
    py_safe(|| 0xAE1515)
}

#[pyfunction]
pub fn aegis_frame_is_valid(payload_len: usize) -> PyResult<bool> {
    py_safe(|| payload_len > 0)
}

#[pyfunction]
pub fn aegis_message_frame_valid(
    payload_len: usize,
    message_id: u128,
    session_id: u128,
) -> PyResult<bool> {
    py_safe(|| payload_len > 0 && message_id > 0 && session_id > 0)
}

#[pyfunction]
pub fn aegis_zero_copy_ready(schema_id: u64, version: u32, payload_len: usize) -> PyResult<bool> {
    py_safe(|| schema_id == 0xAE1515 && version == 1 && payload_len > 0)
}

#[pyfunction]
pub fn aegis_new_message_identity(message_id: u128, session_id: u128) -> PyResult<bool> {
    py_safe(|| message_id > 0 && session_id > 0)
}

#[pyfunction]
pub fn aegis_can_bridge_python(
    payload_len: usize,
    message_id: u128,
    session_id: u128,
) -> PyResult<bool> {
    py_safe(|| payload_len > 0 && message_id > 0 && session_id > 0)
}

#[pyfunction]
pub fn aegis_layout_header_bytes() -> PyResult<usize> {
    py_safe(|| 64)
}

#[pyfunction]
pub fn aegis_layout_payload_alignment() -> PyResult<usize> {
    py_safe(|| 64)
}

#[pyfunction]
pub fn aegis_descriptor_valid(payload_len: usize) -> PyResult<bool> {
    py_safe(|| payload_len > 0)
}

#[pyfunction]
pub fn aegis_cli_status() -> PyResult<&'static str> {
    py_safe(|| "aegis-nerve-cli ready")
}

#[pyfunction]
pub fn aegis_cli_schema() -> PyResult<&'static str> {
    py_safe(|| "schema_id=0xAE1515 version=1 alignment=64")
}

#[pyfunction]
pub fn aegis_release_ready() -> PyResult<bool> {
    py_safe(|| true)
}
