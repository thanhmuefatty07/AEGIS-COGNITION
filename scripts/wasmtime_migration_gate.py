"""Static evidence gate for the Wasmtime migration.

This intentionally complements (rather than replaces) Rust tests and cargo-audit.
It fails closed when a security-sensitive control or the expected pin disappears.
"""

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
manifest_text = (ROOT / "core" / "rust" / "Cargo.toml").read_text(encoding="utf-8")
manifest_toml = tomllib.loads(manifest_text)
wasmtime_dep = manifest_toml.get("dependencies", {}).get("wasmtime")
wasmtime_version = wasmtime_dep.get("version") if isinstance(wasmtime_dep, dict) else wasmtime_dep
sandbox = (ROOT / "core" / "rust" / "src" / "sandbox.rs").read_text(encoding="utf-8")
sandbox_tree = "\n".join(
    path.read_text(encoding="utf-8") for path in (ROOT / "core" / "rust" / "src" / "eac").rglob("*.rs")
)
cli = (ROOT / "core" / "rust" / "src" / "cli" / "mod.rs").read_text(encoding="utf-8")
source = (sandbox + sandbox_tree + cli).lower()

required_manifest = ("50.0.3", "=50.0.3")
required_symbols = ("consume_fuel", "epoch_interruption", "memory", "wasi")

missing = []
if wasmtime_version not in required_manifest:
    missing.append("wasmtime pinned to 50.0.3")
missing.extend(symbol for symbol in required_symbols if symbol not in source)
if missing:
    raise SystemExit("Wasmtime migration gate failed: " + ", ".join(missing))
print("Wasmtime migration static gate: PASS (runtime tests and cargo-audit remain required)")
