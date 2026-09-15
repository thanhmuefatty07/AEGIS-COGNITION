//! Memory-mapped bridge bindings.
//!
//! The functions in this family are thin, typed adapters over the canonical
//! mmap bridge and Wasmtime cell.  They expose no mutable global state.

use super::py_safe;
use pyo3::buffer::PyBuffer;
use pyo3::prelude::*;

#[pyclass(name = "MmapBridgeWriter")]
pub struct PyMmapBridgeWriter {
    inner: Option<crate::bridge_mmap::MmapBridgeWriter>,
}

#[pymethods]
impl PyMmapBridgeWriter {
    #[new]
    pub fn new(
        path: String,
        message_id: u128,
        session_id: u128,
        payload_len: usize,
    ) -> PyResult<Self> {
        let inner =
            crate::bridge_mmap::MmapBridgeWriter::new(path, message_id, session_id, payload_len)
                .map_err(pyo3::exceptions::PyValueError::new_err)?;
        Ok(Self { inner: Some(inner) })
    }

    /// Write one bounded, C-contiguous byte buffer without materializing a
    /// second Rust-owned copy. The buffer is consumed synchronously while the
    /// Python buffer export is held; the pointer never escapes this call.
    pub fn write(&mut self, py: Python<'_>, payload: PyBuffer<u8>) -> PyResult<()> {
        let writer = self.inner.as_mut().ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("mmap bridge writer is already finished")
        })?;
        if payload.as_slice(py).is_none() {
            return Err(pyo3::exceptions::PyBufferError::new_err(
                "mmap bridge writer requires a C-contiguous byte buffer",
            ));
        }
        let payload_len = payload.len_bytes();
        if payload_len == 0 {
            return writer
                .write(&[])
                .map_err(pyo3::exceptions::PyValueError::new_err);
        }
        let payload_ptr = payload.buf_ptr();
        if payload_ptr.is_null() {
            return Err(pyo3::exceptions::PyBufferError::new_err(
                "mmap bridge writer received a null byte buffer",
            ));
        }
        let readonly = payload.readonly();
        // SAFETY: PyBuffer validated a one-byte C-contiguous export above;
        // the exporter is kept alive by `payload`, and the slice is consumed
        // synchronously before the buffer is released or the pointer drops.
        let payload = unsafe { std::slice::from_raw_parts(payload_ptr.cast::<u8>(), payload_len) };
        let result = if readonly {
            // A read-only export cannot be mutated through the Python buffer
            // protocol while this call is detached. Mutable buffers retain
            // the GIL below so callers can safely reuse them only after the
            // synchronous write returns.
            py.detach(|| writer.write(payload))
        } else {
            writer.write(payload)
        };
        result.map_err(pyo3::exceptions::PyValueError::new_err)
    }

    /// Stream an existing file through a bounded native buffer without
    /// re-entering Python for every chunk. The source is consumed while the
    /// GIL is detached; only the writer's native file and hash state mutate.
    pub fn write_file(
        &mut self,
        py: Python<'_>,
        source: String,
        chunk_bytes: usize,
    ) -> PyResult<()> {
        let writer = self.inner.as_mut().ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("mmap bridge writer is already finished")
        })?;
        py.detach(|| writer.write_file(source, chunk_bytes))
            .map_err(pyo3::exceptions::PyValueError::new_err)
    }

    /// Finalize and return the hash-bound frame metadata.
    pub fn finish(&mut self) -> PyResult<(u128, u128, u64, u64, Vec<u8>)> {
        let writer = self.inner.take().ok_or_else(|| {
            pyo3::exceptions::PyRuntimeError::new_err("mmap bridge writer is already finished")
        })?;
        let header = writer
            .finish()
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        Ok((
            header.message_id,
            header.session_id,
            header.payload_offset,
            header.payload_len,
            header.payload_blake3.to_vec(),
        ))
    }

    /// Drop an incomplete writer and close its file without publishing a
    /// valid header. This is used by the Python context manager on errors.
    pub fn abort(&mut self) -> bool {
        self.inner.take().is_some()
    }

    pub fn is_finished(&self) -> bool {
        self.inner.is_none()
    }
}

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
        let sandbox = crate::sandbox::WasmtimeSandbox::try_new().map_err(|error| {
            pyo3::exceptions::PyRuntimeError::new_err(format!(
                "Wasmtime sandbox initialization failed: {error:?}"
            ))
        })?;
        let result = sandbox
            .execute_mmap_wasm_bridge_frame(std::path::Path::new(&path), fuel_limit)
            .map_err(|err| pyo3::exceptions::PyRuntimeError::new_err(format!("{err:?}")))?;
        Ok((result.fuel_consumed, result.artifact.artifact_hash.to_vec()))
    })?
}
