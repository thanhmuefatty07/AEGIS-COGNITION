"""Static evidence gate for the Wasmtime migration.

This intentionally complements (rather than replaces) Rust tests and cargo-audit.
It fails closed when a security-sensitive control or the expected pin disappears.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
manifest = (ROOT / "core" / "rust" / "Cargo.toml").read_text(encoding="utf-8")
sandbox = (ROOT / "core" / "rust" / "src" / "sandbox.rs").read_text(encoding="utf-8")
sandbox_tree = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "core" / "rust" / "src" / "eac").rglob("*.rs"))
cli = (ROOT / "core" / "rust" / "src" / "cli" / "mod.rs").read_text(encoding="utf-8")
source = (sandbox + sandbox_tree + cli).lower()

required_manifest = ('wasmtime = "47.0.4"', 'wasmtime = "=47.0.4"')
required_symbols = ("consume_fuel", "epoch_interruption", "memory", "wasi")

missing = []
if not any(version in manifest for version in required_manifest):
    missing.append("wasmtime pinned to 47.0.4")
missing.extend(symbol for symbol in required_symbols if symbol not in source)
if missing:
    raise SystemExit("Wasmtime migration gate failed: " + ", ".join(missing))
print("Wasmtime migration static gate: PASS (runtime tests and cargo-audit remain required)")
