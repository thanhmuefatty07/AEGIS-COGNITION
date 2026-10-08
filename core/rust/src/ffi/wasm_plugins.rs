use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString, PyStringMethods};
use std::sync::Arc;

fn copy_bounded_bytes(bytes: &[u8], maximum: usize, label: &'static str) -> PyResult<Vec<u8>> {
    if bytes.len() > maximum {
        return Err(pyo3::exceptions::PyValueError::new_err(label));
    }
    Ok(bytes.to_vec())
}

#[pyclass(name = "WasmPlugin")]
pub struct PyWasmPlugin {
    runtime: Arc<crate::wasm_plugins::WasmPluginRuntime>,
}

#[pymethods]
impl PyWasmPlugin {
    #[new]
    #[pyo3(signature = (module, *, fuel_limit=2_000_000, max_memory_pages=64, timeout_ms=5_000, max_output_bytes=262_144))]
    pub fn new(
        py: Python<'_>,
        module: Bound<'_, PyBytes>,
        fuel_limit: u64,
        max_memory_pages: u32,
        timeout_ms: u64,
        max_output_bytes: usize,
    ) -> PyResult<Self> {
        let module = copy_bounded_bytes(
            module.as_bytes(),
            crate::wasm_plugins::MAX_PLUGIN_BYTES,
            "WebAssembly plugin module exceeds its input limit",
        )?;
        let runtime = py
            .detach(move || {
                crate::wasm_plugins::WasmPluginRuntime::compile(
                    &module,
                    fuel_limit,
                    max_memory_pages,
                    timeout_ms,
                    max_output_bytes,
                )
            })
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        Ok(Self {
            runtime: Arc::new(runtime),
        })
    }

    pub fn call<'py>(
        &self,
        py: Python<'py>,
        function: Bound<'py, PyString>,
        input: Bound<'py, PyBytes>,
    ) -> PyResult<(Bound<'py, PyBytes>, u64)> {
        if function.len()? > crate::wasm_plugins::MAX_FUNCTION_NAME_BYTES {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "WebAssembly plugin function name exceeds its limit",
            ));
        }
        let function = function.to_str()?.to_owned();
        let input = copy_bounded_bytes(
            input.as_bytes(),
            crate::wasm_plugins::MAX_INPUT_BYTES,
            "WebAssembly plugin input exceeds its limit",
        )?;
        let runtime = Arc::clone(&self.runtime);
        let (output, fuel_consumed) = py
            .detach(move || runtime.call(&function, &input))
            .map_err(|error| pyo3::exceptions::PyRuntimeError::new_err(error.to_string()))?;
        Ok((PyBytes::new(py, &output), fuel_consumed))
    }

    #[getter]
    pub fn module_hash(&self) -> String {
        self.runtime
            .module_hash()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::copy_bounded_bytes;

    #[test]
    fn byte_bridge_rejects_oversized_values_before_copying() {
        let bytes = [b'x'; 5];
        assert!(copy_bounded_bytes(&bytes, 4, "too large").is_err());
        assert_eq!(
            copy_bounded_bytes(&bytes, 5, "too large")
                .unwrap()
                .as_slice(),
            &bytes[..]
        );
    }
}
