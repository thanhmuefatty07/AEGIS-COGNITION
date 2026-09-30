from __future__ import annotations

import pytest


def test_native_wasm_plugin_checks_python_bytes_limits_before_runtime_use() -> None:
    from aegis_cognition import aegis_nerve as native

    runtime_type = getattr(native, "WasmPlugin", None)
    assert callable(runtime_type), "the configured native WebAssembly runtime must be available"

    with pytest.raises(ValueError, match="module exceeds its input limit"):
        runtime_type(b"\x00asm" + b"\x00" * (16 * 1024 * 1024))

    runtime = runtime_type(b"\x00asm\x01\x00\x00\x00")
    with pytest.raises(ValueError, match="input exceeds its limit"):
        runtime.call("missing_export", b"x" * (1024 * 1024 + 1))
