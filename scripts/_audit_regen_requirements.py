#!/usr/bin/env -S python3
"""
Regenerate AEGIS-COGNITION's top-level requirements.txt from an AST-aware
import scan, merged with the declared deps in pyproject.toml files.

Usage (from project root):
    python scripts/_audit_regen_requirements.py

This is the canonical tool that produced requirements.txt on 2026-06-14.
Re-run after adding any new Python dependency.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# AEGIS-owned source roots (artifacts/research/* is excluded on purpose -
# those are 3rd-party snapshots, not AEGIS source).
AEGIS_DIRS = [
    "core/python",
    "aegis_cognition",
    "examples",
    "tests",
    "scripts",
    "pocs",
    "brain",
    "cluster",
]

STDLIB = set("""
__future__ abc argparse ast asyncio base64 binascii bisect builtins cmath
collections concurrent configparser contextlib copy csv dataclasses datetime
decimal difflib enum errno fcntl fileinput fnmatch functools gc getopt getpass
glob gzip hashlib heapq hmac html http importlib inspect io ipaddress itertools
json logging math mimetypes msvcrt mmap numbers operator os pathlib pickle
platform posixpath pprint queue random re reprlib secrets selectors shutil
signal site socket sqlite3 ssl stat statistics string struct subprocess sys
tempfile textwrap threading time tokenize token tomllib traceback types typing
unicodedata unittest urllib uuid venv warnings weakref xml xmlrpc zipfile zlib
tomli termios tty socketserver
""".split())

LOCAL_AEGIS = {
    "aegis_nerve", "aegis", "aegis_cognition", "bridge_mmap", "bridge",
    "browser_runtime_adapter", "browser_live_collector", "browser_ops_bench",
    "browser_playwright_runtime", "integration", "nim_client",
    "operator_api", "operator_api_healthcheck", "operator_api_server",
    "orchestrator", "preflight", "service", "aegis_test_helpers",
    "aegis_test_artifacts", "scripts", "aegis_adapter",
    "core", "cli",
    # Local script-gate helpers in scripts/ directory; not on PyPI.
    "agent",
    "tcp_cluster_soak_gate",
    "dynamic_provider_fallback_gate",
    "dependency_audit_gate",
    "e2e_release_gate",
    "external_deployment_smoke_gate",
    "provider_route_gate",
    "quickjs_cold_start_gate",
    "supply_chain_gate",
    "deployment_manifest",
    "benchmark_gate",
    "run_checks",
    "production_readiness",
    "production_closure",
    "python_hotpath_gate",
    "hermes_session_recovery_baseline_gate",
    "hermes_persistence_baseline_gate",
    "hermes_rpc_baseline_gate",
    "hermes_baseline_gate",
}

EXTRAS_FROM_PYPROJECT: dict[str, list[str]] = {
    "@runtime-discovered": [],
}


def load_pyproject_extras(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as f:
        data = tomllib.load(f)
    out: list[str] = []
    if isinstance(data.get("project"), dict):
        deps = data["project"].get("dependencies", [])
        out.extend(deps)
        opt = data["project"].get("optional-dependencies", {})
        for group, entries in opt.items():
            out.extend(entries)
    return out


def scan_imports(root: Path) -> Counter:
    counts: Counter = Counter()
    for src_dir in AEGIS_DIRS:
        for path in (root / src_dir).rglob("*.py"):
            if path.name == "__init__.py" and path.stat().st_size == 0:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if not top or top in STDLIB or top in LOCAL_AEGIS:
                            continue
                        if top.startswith("_") or not re.match(
                            r"^[A-Za-z][A-Za-z0-9_.\-]*$", top
                        ):
                            continue
                        counts[top] += 1
                elif isinstance(node, ast.ImportFrom):
                    top = (node.module or "").split(".")[0]
                    if not top or top in STDLIB or top in LOCAL_AEGIS:
                        continue
                    if top.startswith("_") or not re.match(
                        r"^[A-Za-z][A-Za-z0-9_.\-]*$", top
                    ):
                        continue
                    counts[top] += 1
    return counts


# External package name -> pip requirement string. Pinned to lower-bounds
# that match the version currently used in production traces.
KNOWN_EXTERNAL: dict[str, str] = {
    "blake3":     "blake3>=0.4",
    "fastapi":    "fastapi>=0.110",
    "openai":     "openai>=1.40",
    "uvicorn":    "uvicorn[standard]>=0.27",
    "yaml":       "PyYAML>=6.0",
    "dotenv":     "python-dotenv>=1.0.0",
    "playwright": "playwright>=1.40",
    "pytest":     "pytest>=8",
    "pytest_asyncio": "pytest-asyncio>=0.23",
    "ruff":       "ruff>=0.3",
}


def main() -> int:
    counts = scan_imports(ROOT)
    extras_top = load_pyproject_extras(ROOT / "pyproject.toml")
    extras_bridge = load_pyproject_extras(ROOT / "core/python/pyproject.toml")

    lines: list[str] = [
        "# AEGIS-COGNITION Python dependencies",
        f"# Auto-generated by scripts/_audit_regen_requirements.py on {__import__('datetime').date.today()}",
        "# Do not edit by hand - re-run the generator after dependency changes.",
        "",
        "# === Core Python bridge (core/python/pyproject.toml) ===",
        *extras_bridge,
        "",
        "# === Top-level project (pyproject.toml) ===",
        *(extras_top if extras_top else ["# (no top-level direct dependencies)"]),
        "",
        "# === Discovered via import scan ===",
    ]
    for pkg, n in sorted(counts.items()):
        if pkg in KNOWN_EXTERNAL:
            lines.append(f"{KNOWN_EXTERNAL[pkg]}    # used in {n} import(s)")
        else:
            lines.append(f"# {pkg:24} # external? verify: used in {n} import(s)")

    target = ROOT / "requirements.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {target} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
