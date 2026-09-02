//! Memory-mapped bridge bindings.
//!
//! The functions in this family are thin, typed adapters over the canonical
//! mmap bridge and Wasmtime cell.  They expose no mutable global state.

use super::py_safe;
use pyo3::prelude::*;

#[pyfunction]
pub fn aegis_mmap_bridge_header_bytes() -> PyResult<usize> {
    py_safe(|| crate::bridge_mmap::MMAP_BRIDGE_HEADER_BYTES)
}

#[pyfunction]
pub fn aegis_mmap_bridge_payload_alignment() -> PyResult<usize> {
    py_safe(|| crate::bridge_mmap::MMAP_BRIDGE_PAYLOAD_ALIGNMENT)
}

#[pyfunction]
pub fn aegis_write_mmap_bridge_pattern(
    path: String,
    message_id: u128,
    session_id: u128,
    payload_len: usize,
) -> PyResult<(u128, u128, u64, u64, Vec<u8>)> {
    py_safe(move || {
        let header = crate::bridge_mmap::write_pattern_mmap_bridge_frame(
            path,
            message_id,
            session_id,
            payload_len,
        )
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
        Ok((
            header.message_id,
            header.session_id,
            header.payload_offset,
            header.payload_len,
            header.payload_blake3.to_vec(),
        ))
    })?
}

#[pyfunction]
pub fn aegis_validate_mmap_bridge_frame(path: String) -> PyResult<bool> {
    py_safe(move || crate::bridge_mmap::validate_mmap_bridge_frame(path))
}

#[pyfunction]
pub fn aegis_execute_mmap_wasm_bridge_frame(
    path: String,
    fuel_limit: u64,
) -> PyResult<(u64, Vec<u8>)> {
    py_safe(move || {
        let sandbox = crate::sandbox::WasmtimeSandbox::new();
        let result = sandbox
            .execute_mmap_wasm_bridge_frame(std::path::Path::new(&path), fuel_limit)
            .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("{err:?}")))?;
        Ok((result.fuel_consumed, result.artifact.artifact_hash.to_vec()))
    })?
}
