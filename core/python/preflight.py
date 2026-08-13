from dataclasses import dataclass
from pathlib import Path
from shutil import which


@dataclass(frozen=True)
class BuildPreflightResult:
    cargo_present: bool
    rustc_present: bool
    rust_toolchain_present: bool
    python_bridge_present: bool
    rust_core_present: bool
    ready_to_benchmark: bool


def check_build_preflight(root: str) -> BuildPreflightResult:
    root_path = Path(root)
    cargo_present = which("cargo") is not None
    rustc_present = which("rustc") is not None
    rust_toolchain_present = cargo_present and rustc_present
    python_bridge_present = (root_path / "core" / "python" / "integration.py").exists()
    rust_core_present = (root_path / "core" / "rust" / "src" / "lib.rs").exists()
    ready_to_benchmark = rust_toolchain_present and python_bridge_present and rust_core_present and root_path.exists()
    return BuildPreflightResult(
        cargo_present=cargo_present,
        rustc_present=rustc_present,
        rust_toolchain_present=rust_toolchain_present,
        python_bridge_present=python_bridge_present,
        rust_core_present=rust_core_present,
        ready_to_benchmark=ready_to_benchmark,
    )
