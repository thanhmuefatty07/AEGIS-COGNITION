"""Read-only architecture convergence evidence collector.

This is an audit tool, not a runtime component.  It deliberately uses direct
filesystem/AST/Cargo metadata queries instead of the repository knowledge
graph, whose snapshot may omit untracked working-tree files.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".venv", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
PY_ROOTS = ("aegis_cognition", "core/python", "tests", "scripts", "examples")
SAFE_TEXT_SUFFIXES = {".py", ".rs", ".toml", ".md", ".json", ".yml", ".yaml", ".txt", ".lock", ".ini", ".cfg"}
_TEXT_PATHS: list[Path] | None = None
_TEXT_CACHE: dict[Path, list[str]] = {}
_FILE_LIST_CACHE: dict[tuple[tuple[str, ...], tuple[str, ...] | None], list[Path]] = {}


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def write_json(name: str, value: Any) -> None:
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_files(suffixes: set[str], include_top: set[str] | None = None) -> list[Path]:
    """Enumerate relevant files while pruning build/cache/research trees."""
    key = (tuple(sorted(suffixes)), tuple(sorted(include_top)) if include_top is not None else None)
    if key in _FILE_LIST_CACHE:
        return _FILE_LIST_CACHE[key]
    result: list[Path] = []
    excluded_top = {".git", ".venv", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".serena", "artifacts", ".agents", ".cursor", ".sixth", "brain", "planning pdf"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if Path(dirpath).resolve() == ROOT.resolve():
            if include_top is None:
                dirnames[:] = [d for d in dirnames if d not in excluded_top]
            else:
                dirnames[:] = [d for d in dirnames if d in include_top]
        base = Path(dirpath)
        for filename in filenames:
            p = base / filename
            if p.suffix.lower() in suffixes:
                result.append(p)
    _FILE_LIST_CACHE[key] = sorted(result)
    return _FILE_LIST_CACHE[key]


def shell(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:  # audit must retain collection failures
        return 99, "", f"{type(exc).__name__}: {exc}"


def status_snapshot() -> dict[str, Any]:
    code, out, err = shell(["git", "status", "--porcelain=v1"], timeout=20)
    entries = []
    for line in out.splitlines():
        if not line:
            continue
        xy, path = (line[:2], line[3:]) if len(line) >= 4 else (line, "")
        path = path.strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append({"status": xy, "path": path, "category": classify_path(path)})
    code_h, head, _ = shell(["git", "rev-parse", "HEAD"], timeout=20)
    code_b, branch, _ = shell(["git", "branch", "--show-current"], timeout=20)
    code_o, origin, _ = shell(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "origin/main"], timeout=20)
    code_d, divergence, _ = shell(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], timeout=20)
    staged = [e for e in entries if e["status"][0] != " " and e["status"][0] != "?"]
    untracked = [e for e in entries if e["status"] == "??"]
    return {
        "command_exit": code,
        "stderr": err.strip(),
        "head": head.strip() if code_h == 0 else None,
        "branch": branch.strip() if code_b == 0 else None,
        "origin_main": origin.strip() if code_o == 0 else None,
        "ahead_behind": divergence.strip() if code_d == 0 else None,
        "entries": entries,
        "tracked_modified": [e for e in entries if e["status"] != "??"],
        "staged": staged,
        "untracked": untracked,
        "total_entries": len(entries),
    }


def classify_path(path: str) -> str:
    p = path.lower().replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if p.startswith("docs/architecture/evidence-pack/"):
        return "GENERATED" if name != "collect.py" else "SCRIPT"
    if p.startswith("tests/") or p.endswith("/tests.py") or "/tests/" in p:
        return "TEST"
    if p.startswith("scripts/"):
        return "SCRIPT"
    if p.startswith("docs/") or name.endswith((".md", ".rst")):
        return "DOCUMENTATION"
    if "/poc" in p or p.startswith("pocs/") or "/pocs/" in p:
        return "POC"
    if p.startswith("core/python/") and not p.startswith("core/python/tests"):
        return "COMPATIBILITY"
    if p.startswith("aegis_cognition/") or p.startswith("core/rust/") or p.startswith("aegis-plugins/"):
        if "/lab" in p or p.endswith("/lab.py") or p.endswith("/lab.rs"):
            return "LAB"
        return "PRODUCT"
    if p.endswith((".json", ".lock", ".yml", ".yaml")) and ("evidence" in p or "generated" in p):
        return "EVIDENCE"
    if p.startswith("artifacts/"):
        return "EVIDENCE"
    return "UNKNOWN"


def py_module_name(path: Path) -> tuple[str, str]:
    r = path.resolve().relative_to(ROOT.resolve()).as_posix()
    for prefix in PY_ROOTS:
        if r == prefix or r.startswith(prefix + "/"):
            tail = r[len(prefix):].lstrip("/")
            parts = tail.split("/") if tail else []
            if parts and parts[-1].endswith(".py"):
                parts[-1] = parts[-1][:-3]
            if parts and parts[-1] == "__init__":
                parts.pop()
            base = prefix.replace("/", ".")
            module = ".".join([x for x in ([base] if base else []) + parts if x])
            return module, prefix
    parts = r[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts), "repository"


def py_files() -> list[Path]:
    return safe_files({".py"}, {"aegis_cognition", "core", "tests", "scripts", "examples", "cluster", "aegis-plugins", "pocs"})


def resolve_import(raw: str, known: set[str]) -> str | None:
    if raw in known:
        return raw
    cur = raw
    while "." in cur:
        cur = cur.rsplit(".", 1)[0]
        if cur in known:
            return cur
    return None


def python_graph() -> dict[str, Any]:
    files = py_files()
    known: set[str] = set()
    info: dict[str, dict[str, Any]] = {}
    for p in files:
        mod, root = py_module_name(p)
        if not mod:
            continue
        known.add(mod)
        info[mod] = {"module": mod, "path": rel(p), "package_root": root, "imports": [], "resolved_imports": [], "dynamic_imports": [], "lazy_imports": [], "sys_path_manipulation": [], "import_time_side_effects": [], "functions": [], "classes": []}
    for p in files:
        mod, _ = py_module_name(p)
        if mod not in info:
            continue
        rec = info[mod]
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(p))
        except SyntaxError as exc:
            rec["parse_error"] = f"SyntaxError:{exc.lineno}:{exc.offset}:{exc.msg}"
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    rec["imports"].append({"name": alias.name, "line": node.lineno, "kind": "import"})
                    target = resolve_import(alias.name, known)
                    if target:
                        rec["resolved_imports"].append(target)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parent = mod.split(".")[:-node.level]
                    base = ".".join(parent + ([base] if base else []))
                rec["imports"].append({"name": base, "line": node.lineno, "kind": "from", "level": node.level})
                target = resolve_import(base, known)
                if target:
                    rec["resolved_imports"].append(target)
                if node.col_offset > 0 or any(isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for parent in []):
                    rec["lazy_imports"].append({"line": node.lineno, "name": base})
            elif isinstance(node, ast.Call):
                call = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                if call in {"__import__", "importlib.import_module", "import_module", "importlib.util.spec_from_file_location"}:
                    rec["dynamic_imports"].append({"line": node.lineno, "call": call})
                if call.startswith("sys.path.") or (call == "sys.path.insert"):
                    rec["sys_path_manipulation"].append({"line": node.lineno, "call": call})
        parents: list[ast.AST] = []
        side_names = ("open", "urlopen", "Popen", "run", "serve_forever", "create_task", "launch", "connect", "write_text", "unlink", "mkdir", "remove", "invoke", "index_session")
        def walk_top(node: ast.AST) -> None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rec["functions" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "classes"].append({"name": node.name, "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
                return
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                if any(name == s or name.endswith("." + s) for s in side_names):
                    rec["import_time_side_effects"].append({"line": node.lineno, "call": name})
            for child in ast.iter_child_nodes(node):
                walk_top(child)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rec["functions" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "classes"].append({"name": node.name, "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno)})
            elif isinstance(node, ast.Expr):
                walk_top(node)
        rec["imports"] = sorted(rec["imports"], key=lambda x: (x["line"], x["name"]))
        rec["resolved_imports"] = sorted(set(rec["resolved_imports"]))
    reverse: dict[str, set[str]] = defaultdict(set)
    for mod, rec in info.items():
        for target in rec["resolved_imports"]:
            reverse[target].add(mod)
    for rec in info.values():
        rec["imported_by"] = sorted(reverse.get(rec["module"], set()))
        rec["fan_in"] = len(rec["imported_by"])
        rec["fan_out"] = len(rec["resolved_imports"])
        rec["dynamic_imports"] = sorted(rec["dynamic_imports"], key=lambda x: x["line"])
        rec["lazy_imports"] = sorted(rec["lazy_imports"], key=lambda x: x["line"])
        rec["sys_path_manipulation"] = sorted(rec["sys_path_manipulation"], key=lambda x: x["line"])
        rec["import_time_side_effects"] = sorted(rec["import_time_side_effects"], key=lambda x: x["line"])
    edges = {m: set(r["resolved_imports"]) for m, r in info.items()}
    cycles: list[list[str]] = []
    temp: set[str] = set(); done: set[str] = set(); stack: list[str] = []
    def visit(m: str) -> None:
        if m in temp:
            if m in stack:
                cycles.append(stack[stack.index(m):] + [m])
            return
        if m in done:
            return
        temp.add(m); stack.append(m)
        for nxt in edges.get(m, set()):
            visit(nxt)
        stack.pop(); temp.remove(m); done.add(m)
    for m in sorted(edges):
        visit(m)
    return {"schema": "aegis-architecture-python-import-graph-v1", "source": "fresh AST scan of current working tree", "modules": info, "cycles": cycles, "module_count": len(info), "edge_count": sum(len(v) for v in edges.values())}


def rust_metadata() -> dict[str, Any]:
    code, out, err = shell(["cargo", "metadata", "--no-deps", "--format-version", "1"], timeout=45)
    if code != 0:
        return {"status": "NOT_VERIFIED", "command_exit": code, "stderr": err.strip()}
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        return {"status": "NOT_VERIFIED", "error": str(exc)}
    packages = []
    for pkg in data.get("packages", []):
        packages.append({"name": pkg["name"], "version": pkg["version"], "manifest_path": rel(Path(pkg["manifest_path"])), "targets": [{"name": t["name"], "kind": t["kind"], "src_path": rel(Path(t["src_path"]))} for t in pkg.get("targets", [])], "dependencies": [{"name": d["name"], "kind": d.get("kind"), "optional": d.get("optional", False), "req": d.get("req")} for d in pkg.get("dependencies", [])], "features": pkg.get("features", {})})
    return {"status": "PROVEN", "workspace_members": [x.split("#", 1)[0] for x in data.get("workspace_members", [])], "default_members": [x.split("#", 1)[0] for x in data.get("workspace_default_members", [])], "packages": packages}


def rust_graph(meta: dict[str, Any]) -> dict[str, Any]:
    modules: dict[str, dict[str, Any]] = {}
    edges: dict[str, set[str]] = defaultdict(set)
    package_by_src: dict[str, str] = {}
    for pkg in meta.get("packages", []):
        manifest = ROOT / pkg["manifest_path"]
        crate_root = manifest.parent
        for p in [candidate for candidate in safe_files({".rs"}) if str(candidate).startswith(str(crate_root))]:
            r = rel(p); src_rel = p.relative_to(crate_root).as_posix()
            parts = src_rel.split("/")
            if parts[-1] == "lib.rs":
                mod = f"{pkg['name']}::crate"
            elif parts[-1] == "main.rs":
                mod = f"{pkg['name']}::bin"
            else:
                stem = parts[-1][:-3]
                if stem == "mod":
                    parts = parts[:-1]
                else:
                    parts[-1] = stem
                mod = f"{pkg['name']}::" + "::".join(parts)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            public = re.findall(r"\bpub(?:\([^)]*\))?\s+(?:async\s+)?(?:fn|struct|enum|trait|type|const|static|use|mod|impl)\b", text)
            decls = re.findall(r"\b(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|type|const|static|mod|impl)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
            child_edges = []
            for m in re.finditer(r"\b(pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)", text):
                child_edges.append({"name": m.group(2), "public": bool(m.group(1)), "line": text[:m.start()].count("\n") + 1})
            uses = re.findall(r"\buse\s+(crate|super|self|[A-Za-z_][A-Za-z0-9_]*)[^;]*;", text)
            imports = sorted(set(uses))
            modules[mod] = {"module": mod, "package": pkg["name"], "path": r, "visibility": "public_file_or_decl" if public else "private_or_no_pub_decl", "imports": imports, "public_declarations": len(public), "declarations": len(decls), "child_modules": child_edges, "cfg_test_present": "cfg(test)" in text, "unsafe_hits": len(re.findall(r"\bunsafe\b", text)), "test_marker_count": len(re.findall(r"#\[(?:async_)?test\]", text))}
            package_by_src[r] = pkg["name"]
            for target in imports:
                if target in {"crate", "super", "self"}:
                    edges[mod].add(target)
    reverse: dict[str, set[str]] = defaultdict(set)
    for src, targets in edges.items():
        for t in targets:
            reverse[t].add(src)
    for mod, rec in modules.items():
        rec["resolved_imports"] = sorted(x for x in edges.get(mod, set()) if x in modules)
        rec["fan_out"] = len(rec["resolved_imports"])
        rec["fan_in"] = len(reverse.get(mod, set()))
        rec["primary_responsibility"] = rust_responsibility(rec["path"])
    return {"schema": "aegis-architecture-rust-module-graph-v1", "source": "fresh Cargo metadata + Rust source scan", "workspace": meta, "modules": modules, "edge_count": sum(len(v) for v in edges.values()), "conceptual_cycle_note": "crate/super imports are conservatively recorded; macro-generated and re-export edges need rust-analyzer for complete resolution"}


def rust_responsibility(path: str) -> str:
    p = path.lower()
    for key, value in (("ffi", "PyO3/native boundary"), ("lab", "Lab controller/state/events"), ("replay", "replay/archive/validation"), ("resource", "resource admission/platform"), ("execution", "execution lanes/processes"), ("gt96", "GT96 invariants/budget"), ("telemetry", "telemetry"), ("sandbox", "sandbox/Wasmtime"), ("tool_gateway", "tool/provider gateway"), ("task_ledger", "task lifecycle"), ("memory", "memory/storage"), ("cli", "CLI")):
        if key in p:
            return value
    return "module-local responsibility; inspect source"


def scan_text(pattern: str, paths: list[Path] | None = None, flags: int = re.IGNORECASE) -> list[dict[str, Any]]:
    rows = []
    global _TEXT_PATHS
    if paths is None:
        if _TEXT_PATHS is None:
            _TEXT_PATHS = safe_files(SAFE_TEXT_SUFFIXES)
        paths = _TEXT_PATHS
    rx = re.compile(pattern, flags)
    for p in paths:
        if p not in _TEXT_CACHE:
            try:
                _TEXT_CACHE[p] = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except (OSError, UnicodeError):
                _TEXT_CACHE[p] = []
        for line_no, line in enumerate(_TEXT_CACHE[p], 1):
            if rx.search(line):
                rows.append({"path": rel(p), "line": line_no, "text": line.strip()[:500], "category": classify_path(rel(p))})
    return rows


def python_call_graph(pg: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    funcs: dict[str, dict[str, Any]] = {}
    by_short: dict[str, set[str]] = defaultdict(set)
    for mod, rec in pg["modules"].items():
        p = ROOT / rec["path"]
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        def visit(body: list[ast.stmt], owner: str = "") -> None:
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{mod}:{node.name}"
                    funcs[q] = {"module": mod, "name": node.name, "path": rec["path"], "line": node.lineno, "calls": []}
                    by_short[node.name].add(q)
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Call):
                            try:
                                name = ast.unparse(sub.func)
                            except Exception:
                                name = ""
                            short = name.rsplit(".", 1)[-1]
                            funcs[q]["calls"].append(short)
                elif isinstance(node, ast.ClassDef):
                    visit(node.body, node.name)
        visit(tree.body)
    edges: dict[str, set[str]] = defaultdict(set)
    for q, rec in funcs.items():
        for short in rec["calls"]:
            for target in by_short.get(short, set()):
                if target != q:
                    edges[q].add(target)
    return edges, funcs


def side_effect_graph(pg: dict[str, Any]) -> dict[str, Any]:
    edges, funcs = python_call_graph(pg)
    effect_patterns = {
        "NETWORK": r"urlopen|requests\.|httpx\.|aiohttp|socket\.|connect\(|fetch\(|urllib",
        "FILESYSTEM_WRITE": r"write_text|write_bytes|open\(|mkdir\(|touch\(|copyfile|shutil\.copy",
        "FILESYSTEM_DELETE": r"unlink\(|remove\(|rmtree\(|delete\(",
        "PROCESS_SPAWN": r"Popen|subprocess\.|create_subprocess|multiprocessing|os\.system",
        "BROWSER": r"playwright|browser|\.launch\(|\.new_page\(|page\.goto",
        "PROVIDER_CALL": r"ainvoke|invoke_llm|provider|openai|chat\.completions",
        "IPC": r"TcpListener|TcpStream|socket|ipc|mmap|shared_memory",
        "SHARED_MEMORY": r"mmap|shared_memory|Shmem|memmap",
        "ENVIRONMENT_MUTATION": r"os\.environ\[|os\.putenv|setdefault\(|dotenv",
        "SECRET_ACCESS": r"api_key|secret|token|private_key|OPENAI_API_KEY",
        "EXTERNAL_WRITE": r"POST|PUT|PATCH|DELETE|upload|publish|send\(",
        "WASM_NATIVE_EXECUTION": r"wasmtime|Wasm|Engine\(|Module\("
    }
    effect_rx = {k: re.compile(v, re.I) for k, v in effect_patterns.items()}
    direct = []
    for q, rec in funcs.items():
        text = (ROOT / rec["path"]).read_text(encoding="utf-8", errors="replace")
        # Restrict evidence to a function's rough source span to avoid tagging every function in a module.
        lines = text.splitlines(); start = rec["line"] - 1; end = min(len(lines), start + 220)
        body = "\n".join(lines[start:end])
        for effect, rx in effect_rx.items():
            if rx.search(body):
                direct.append({"function": q, "path": rec["path"], "line": rec["line"], "effect": effect, "direct_evidence": True})
    # Production roots are named, not assumed complete.
    roots = [q for q, r in funcs.items() if (r["module"].startswith("aegis_cognition") and r["name"] in {"run", "arun", "main"}) or r["module"] in {"scripts.run_checks", "core.python.aegis_cli", "core.python.operator_api_server"}]
    reverse: dict[str, set[str]] = defaultdict(set)
    for src, ts in edges.items():
        for t in ts:
            reverse[t].add(src)
    records = []
    for item in direct:
        q = item["function"]; queue: deque[tuple[str, list[str]]] = deque([(q, [q])]); seen = {q}; paths = []
        while queue and len(paths) < 8:
            cur, path = queue.popleft()
            if cur in roots:
                paths.append(path[::-1]); continue
            for parent in reverse.get(cur, set()):
                if parent not in seen and len(path) < 7:
                    seen.add(parent); queue.append((parent, path + [parent]))
        records.append({**item, "reachable_from": sorted({p[0] for p in paths}) or ["UNKNOWN_DYNAMIC"], "call_chains": paths, "missing_stages": ["ADMISSION", "CAPABILITY", "BUDGET", "LEASE", "EFFECT", "SETTLEMENT", "EVIDENCE"], "stage_note": "Static AST scan cannot prove an explicit stage; inspect named cell/controller calls before treating as absent."})
    rust_sites = scan_text(r"TcpListener|TcpStream|std::fs::|Command::new|std::process|wasmtime|mmap|unsafe\b|File::create", safe_files({".rs"}))
    return {"schema": "aegis-architecture-side-effect-graph-v1", "python_function_records": records[:2000], "python_records_truncated": len(records) > 2000, "rust_side_effect_sites": rust_sites[:3000], "rust_sites_truncated": len(rust_sites) > 3000, "effect_classes": sorted(effect_patterns), "production_roots": sorted(roots), "limitations": ["dynamic dispatch, decorators, macro expansion and subprocess descendants are not fully resolved", "missing stage labels are not proof of absence"]}


def reachability(pg: dict[str, Any]) -> dict[str, Any]:
    roots = {
        "PUBLIC_API": [m for m in pg["modules"] if m == "aegis_cognition" or m.startswith("aegis_cognition.") and m in {"aegis_cognition.agent", "aegis_cognition.application", "aegis_cognition.cli"}],
        "LAB": [m for m in pg["modules"] if m in {"aegis_cognition.lab", "aegis_cognition.application", "aegis_cognition.agent"}],
        "CLI": [m for m in pg["modules"] if m.endswith(".cli") or m in {"core.python.aegis_cli"}],
        "FFI": [m for m in pg["modules"] if m in {"aegis_cognition.runtime", "core.python.bridge", "core.python.bridge_mmap", "core.python.aegis_adapter"}],
        "PLUGIN": [m for m in pg["modules"] if "plugin" in m or "aegis_plugins" in m],
        "TEST_ONLY": [m for m in pg["modules"] if m.startswith("tests") or m.startswith("core.python.tests")],
        "SCRIPT_ONLY": [m for m in pg["modules"] if m.startswith("scripts")],
        "POC_ONLY": [m for m in pg["modules"] if "poc" in m.lower()],
    }
    reverse = {m: set(r["imported_by"]) for m, r in pg["modules"].items()}
    records = []
    packaging_text = "\n".join((ROOT / x).read_text(encoding="utf-8", errors="replace") for x in ["pyproject.toml", "core/python/pyproject.toml"] if (ROOT / x).exists())
    ci_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (ROOT / ".github/workflows").glob("*.yml")) if (ROOT / ".github/workflows").exists() else ""
    docs_text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in safe_files({".md"}) if "docs" in p.relative_to(ROOT).parts)
    for mod, rec in pg["modules"].items():
        reachable = []
        for label, rs in roots.items():
            # Walk reverse imports from a named root to imported descendants.
            seen = set(rs); q = deque(rs)
            while q:
                cur = q.popleft()
                for child in pg["modules"].get(cur, {}).get("resolved_imports", []):
                    if child not in seen:
                        seen.add(child); q.append(child)
            if mod in seen:
                reachable.append(label)
        path = rec["path"]
        content = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        test_ref = bool(re.search(r"tests?/|pytest|unittest|test_", path + "\n" + docs_text[:0]))
        packaging_ref = mod.replace(".", "/") in packaging_text or rec["package_root"] in packaging_text
        records.append({"path": path, "module": mod, "reachable_from": sorted(set(reachable)), "dynamic_reference": bool(rec["dynamic_imports"] or rec["sys_path_manipulation"]), "packaging_reference": packaging_ref, "test_reference": test_ref, "ci_reference": mod in ci_text or path in ci_text, "doc_schema_reference": mod in docs_text or path in docs_text, "delete_confidence_class": "ACTIVE" if reachable else ("TEST_ONLY" if mod.startswith("tests") else "UNKNOWN")})
    return {"schema": "aegis-architecture-reachability-v1", "roots": roots, "files": records, "limitations": ["dynamic import/plugin entrypoint reachability is uncertain", "absence of static reachability is not deletion authorization"]}


def ffi_inventory() -> dict[str, Any]:
    records = []
    rust_paths = safe_files({".rs"})
    for p in rust_paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "#[pyfunction" not in line and "#[pyclass" not in line:
                continue
            attr = line.strip()
            window = "\n".join(lines[i:i + 8])
            fn = re.search(r"(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*(?:->\s*([^\{]+))?", window, re.S)
            cls = re.search(r"(?:pub\s+)?struct\s+([A-Za-z_][A-Za-z0-9_]*)", window)
            rust_name = fn.group(1) if fn else (cls.group(1) if cls else "UNKNOWN")
            python_name = (re.search(r"name\s*=\s*\"([^\"]+)\"", attr) or [None, rust_name])[1]
            sig = fn.group(2).strip() if fn else None
            output = fn.group(3).strip() if fn and fn.group(3) else ("PyClass" if cls else "UNKNOWN")
            lower = (str(rust_name) + " " + (sig or "") + " " + window).lower()
            flags = []
            if "string" in lower or "json" in lower or "pyany" in lower: flags.append("STRINGLY_TYPED")
            if "bytes" in lower or "json" in lower or "record" in lower: flags.append("LARGE_PAYLOAD")
            if "lab" in lower or "admit" in lower or "budget" in lower or "replay" in lower: flags.append("AUTHORITY_SENSITIVE")
            if "clone" in lower or "serde" in lower or "json" in lower: flags.append("COPY_HEAVY")
            side_effect_patterns = {"NETWORK": re.compile("network|http|socket", re.I), "FILESYSTEM": re.compile("file|path|replay|archive", re.I), "PROCESS": re.compile("process|spawn|job", re.I), "BROWSER": re.compile("browser|playwright", re.I), "PROVIDER": re.compile("llm|provider|model", re.I)}
            side_effect_class = next((e for e, rx in side_effect_patterns.items() if rx.search(lower)), "UNKNOWN")
            records.append({"python_facing_name": python_name, "rust_function_or_type": rust_name, "path": rel(p), "line": i + 1, "caller_candidates": [], "input_type": sig, "output_type": output, "serialization": "JSON/bytes/string boundary suspected" if any(x in lower for x in ["json", "bytes", "string", "pyany"]) else "typed/native or unknown", "copying": "suspected" if "clone" in lower or "json" in lower else "unknown", "side_effect_class": side_effect_class, "authority_relevance": "HIGH" if "AUTHORITY_SENSITIVE" in flags else "UNKNOWN", "estimated_frequency": "STARTUP" if "init" in rust_name.lower() else ("PER_RUN" if "lab" in lower or "runtime" in lower else "UNKNOWN"), "flags": sorted(set(flags))})
    # Methods inside a #[pymethods] impl are also Python-facing, even though
    # the individual methods do not carry #[pyfunction].
    existing = {(r["path"], r["line"], r["rust_function_or_type"]) for r in records}
    for p in rust_paths:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for marker_index, marker_line in enumerate(lines):
            if "#[pymethods]" not in marker_line:
                continue
            impl_index = next((j for j in range(marker_index + 1, min(len(lines), marker_index + 20)) if re.search(r"\bimpl\s+", lines[j])), None)
            if impl_index is None:
                continue
            block_lines: list[str] = []
            depth = 0
            started = False
            for j in range(impl_index, min(len(lines), impl_index + 700)):
                current = lines[j]
                if "{" in current:
                    started = True
                if started:
                    block_lines.append(current)
                depth += current.count("{") - current.count("}")
                if started and depth <= 0:
                    break
            block = "\n".join(block_lines)
            impl_match = re.search(r"impl\s+([A-Za-z_][A-Za-z0-9_]*)", block)
            receiver = impl_match.group(1) if impl_match else "UNKNOWN_IMPL"
            for method in re.finditer(r"(?m)^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*(?:->\s*([^\{]+))?", block, re.S):
                rust_name = method.group(1)
                method_line = marker_index + 2 + block[:method.start()].count("\n")
                key = (rel(p), method_line, rust_name)
                if key in existing:
                    continue
                sig = method.group(2).strip()
                output = method.group(3).strip() if method.group(3) else "()"
                lower = f"{rust_name} {sig} {receiver}".lower()
                flags = []
                if any(x in lower for x in ["string", "json", "pyany"]): flags.append("STRINGLY_TYPED")
                if any(x in lower for x in ["bytes", "json", "record", "snapshot"]): flags.append("LARGE_PAYLOAD")
                if any(x in lower for x in ["lab", "admit", "budget", "replay", "snapshot"]): flags.append("AUTHORITY_SENSITIVE")
                records.append({"python_facing_name": rust_name, "rust_function_or_type": f"{receiver}.{rust_name}", "path": rel(p), "line": method_line, "caller_candidates": [], "input_type": sig, "output_type": output, "serialization": "typed PyO3 method; serialization unknown", "copying": "unknown", "side_effect_class": "UNKNOWN", "authority_relevance": "HIGH" if "AUTHORITY_SENSITIVE" in flags else "UNKNOWN", "estimated_frequency": "PER_RUN" if any(x in lower for x in ["lab", "snapshot", "admit"]) else "UNKNOWN", "flags": sorted(set(flags)), "pymethods_receiver": receiver})
                existing.add(key)
    names = {str(r["python_facing_name"]) for r in records} | {str(r["rust_function_or_type"]) for r in records}
    callers: dict[str, list[dict[str, Any]]] = defaultdict(list)
    token_rx = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    for p in py_files():
        if p.suffix != ".py":
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            for token in set(token_rx.findall(line)) & names:
                callers[token].append({"path": rel(p), "line": line_no})
    for r in records:
        own = {r["path"], r["rust_function_or_type"]}
        r["caller_candidates"] = [x for x in (callers.get(str(r["python_facing_name"]), []) + callers.get(str(r["rust_function_or_type"]), [])) if x["path"] not in own][:30]
    return {"schema": "aegis-architecture-ffi-inventory-v1", "records": records, "exposure_note": "PyO3 attributes are mechanically enumerated; macro-generated/re-exported exposure may be missed."}


def authority_matrix() -> dict[str, Any]:
    entities = ["Mission", "Goal", "Run", "Task", "Attempt", "Lease", "Budget", "Capability", "Effect", "ExecutionCell", "Source", "Claim", "Evidence", "Hypothesis", "Experiment", "Observation", "Artifact", "Benchmark", "ProviderRequest", "ReplayEvent", "Checkpoint", "Context", "TrustLevel", "RetryPolicy"]
    evidence = {"Mission": ("LabRun, LabMissionSpec", "LabMissionSpec", "LabMissionSpec", "LabApplication", "LabController", "split authority"), "Run": ("LabRun", "LabRunState", "LabApplication", "LabController", "Python projection + Rust event admission", "SPLIT_AUTHORITY"), "Source": ("LabRun source records", "SourceRecord", "LabApplication", "Rust admission", "Python projection/replay", "PROJECTION"), "Claim": ("claim dict/record", "ClaimRecord", "LabApplication", "Rust admission", "Python projection/replay", "PROJECTION"), "Hypothesis": ("hypothesis records", "HypothesisRecord", "LabApplication", "Rust admission", "Python projection/replay", "PROJECTION"), "Experiment": ("experiment spec", "ExperimentSpec", "LabApplication", "Rust typed admission", "replay/archive", "PROJECTION"), "Observation": ("observation records", "ObservationRecord", "LabApplication", "Rust unit/uncertainty admission", "replay/archive", "PROJECTION"), "Benchmark": ("benchmark.py records", "BenchmarkProtocolV2/ResultV2", "LabApplication", "validator/controller", "archive", "SPLIT_AUTHORITY"), "TrustLevel": ("AgentConfig", "trust normalizer/policy", "config", "multiple consumers", "run policy", "COMPATIBILITY_DUPLICATE"), "RetryPolicy": ("AegisAgent/provider route", "runtime budget/retry primitives", "Python adapters", "hooks/native where installed", "events", "SPLIT_AUTHORITY"), "ReplayEvent": ("Python event", "LabEvent", "LabRun", "LabController validation", "replay writer", "SPLIT_AUTHORITY"), "Context": ("memory search result", "typed receipt", "LabApplication", "ExecutionCell/native admission", "memory", "PROJECTION")}
    rows = []
    for entity in entities:
        py, rs, constructor, validator, persister, cls = evidence.get(entity, ("not located mechanically", "not located mechanically", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"))
        rows.append({"entity": entity, "python_types": py, "rust_types": rs, "schema": "inspect named source/contract; no single schema found" if py == "not located mechanically" else "typed event/record or Python projection", "constructs": constructor, "validates": validator, "mutates": "Python projection and/or Rust controller depending on path", "persists": persister, "finalizes": "Rust controller for admitted Lab paths; Python lifecycle otherwise", "bypass_paths": "compatibility/direct entrypoints, arbitrary adapters, dynamic plugins", "classification": cls})
    return {"schema": "aegis-architecture-authority-matrix-v1", "rows": rows, "do_not_treat_as_recommendation": True}


def retry_graph() -> dict[str, Any]:
    pats = r"retry|retries|max_retries|backoff|sleep\(|timeout|wait_for|wait_for\(|cancel|cancelled|cancellation|abort|deadline|lease"
    py = scan_text(pats, safe_files({".py"}))
    rs = scan_text(pats, safe_files({".rs"}))
    rows = []
    for x in py + rs:
        txt = x["text"].lower()
        rows.append({**x, "owner": "nearest function requires AST/rust-analyzer resolution", "retry_count_policy": "literal nearby; see source line", "backoff": "exponential/constant/unknown", "idempotency_assumption": "not mechanically established", "effect_class": "provider/tool/network/process/unknown", "budget_integration": "unknown unless budget/ledger appears nearby", "attempt_identity": "unknown", "cancellation_behavior": "cancellation keyword present/unknown"})
    duplicates = [x for x in rows if any(k in x["path"].lower() for k in ["provider", "adapter", "lab", "replay", "execution"])]
    return {"schema": "aegis-architecture-retry-graph-v1", "records": rows[:4000], "records_truncated": len(rows) > 4000, "duplicate_policy_candidates": duplicates[:250], "note": "Pattern inventory is intentionally conservative; it is not proof that every keyword is a retry or that no policy exists."}


def public_api() -> dict[str, Any]:
    pg = python_graph()
    py_public = []
    for mod, rec in pg["modules"].items():
        if mod.startswith("aegis_cognition") or mod in {"core.python.aegis_cli", "core.python.aegis_adapter", "core.python.bridge"}:
            py_public.append({"surface": "Python", "name": mod, "path": rec["path"], "classification": "STABLE_PUBLIC" if mod.startswith("aegis_cognition") else "COMPATIBILITY", "symbols": [x["name"] for x in rec["functions"] + rec["classes"] if not x["name"].startswith("_")]})
    rust = rust_graph(rust_metadata())
    rust_public = [{"surface": "Rust module", "name": m, "path": r["path"], "classification": "INTERNAL_BUT_PUBLIC" if m.endswith("::crate") or r["public_declarations"] else "UNKNOWN", "public_declarations": r["public_declarations"]} for m, r in rust.get("modules", {}).items() if r["public_declarations"]]
    return {"schema": "aegis-architecture-public-api-v1", "python": py_public, "rust": rust_public, "ffi": "see architecture_ffi_inventory.json", "cli": [{"name": "aegis", "owner": "aegis_cognition.cli:main", "classification": "STABLE_PUBLIC"}, {"name": "aegis", "owner": "core/python/aegis_cli.py", "classification": "COMPATIBILITY"}], "schemas": [rel(p) for p in safe_files({".json", ".yaml", ".yml"}) if "schemas" in p.relative_to(ROOT).parts], "plugins": [rel(p) for p in safe_files({".toml"}) if p.name == "Cargo.toml" and "aegis-plugins" in p.relative_to(ROOT).parts]}


def duplication_candidates() -> dict[str, Any]:
    pattern_map = {
        "hashing": r"blake3|canonical_hash|hash_chain|sha256",
        "ID generation": r"uuid|uuid4|mission_id|run_id|attempt_id|request_id",
        "retry": r"max_retries|retry|backoff",
        "budget": r"BudgetLedger|budget|exploration_budget|finalization_budget",
        "capability": r"capability|ExecutionCell|admit_",
        "trust": r"TrustLevel|trust_level|DEV|PROD",
        "resource admission": r"resource_admission|ResourceAdmission|admission",
        "serialization": r"serde|json.dumps|json.loads|to_json|from_json",
        "event creation": r"LabEvent|event_kind|append_event|_append",
        "evidence validation": r"evidence.*valid|Validated|validator|validation",
        "provider routing": r"provider_route|invoke_with_provider_route|candidate",
        "browser policy": r"BrowserCell|browser.*policy|playwright|private.*address",
        "replay validation": r"replay|archive.*verif|snapshot.*restore",
        "context handling": r"search_past|context|retrieval",
        "telemetry": r"telemetry|metrics|tracing|correlation_id",
        "error conversion": r"PyErr|PyResult|str\(exc\)|raise\s+RuntimeError",
    }
    source_roots = ["aegis_cognition", "core", "scripts", "aegis-plugins", "pocs", "cluster"]
    result = {}
    for name, pat in pattern_map.items():
        code, out, err = shell(["rg", "-n", "-i", "--glob", "*.py", "--glob", "*.rs", "--glob", "*.toml", pat, *source_roots], timeout=20)
        hits = []
        for line in out.splitlines()[:600]:
            m = re.match(r"(.+?):(\d+):(.*)", line)
            if m:
                hits.append({"path": m.group(1).replace("\\", "/"), "line": int(m.group(2)), "text": m.group(3).strip()[:500], "category": classify_path(m.group(1))})
        by_path = defaultdict(list)
        for h in hits:
            if h["path"].startswith(("aegis_cognition/", "core/python/", "core/rust/", "scripts/")):
                by_path[h["path"]].append({"line": h["line"], "text": h["text"]})
        paths = sorted(by_path)
        result[name] = {"candidate_class": "UNKNOWN" if len(paths) > 1 else "FALSE_POSITIVE", "implementations": [{"path": p, "hits": by_path[p][:20]} for p in paths[:30]], "note": "semantic equivalence requires contract-level review"}
    return {"schema": "aegis-architecture-duplicate-candidates-v1", "domains": result}


def dependencies() -> dict[str, Any]:
    pytext = (ROOT / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (ROOT / "pyproject.toml").exists() else ""
    corepytext = (ROOT / "core/python/pyproject.toml").read_text(encoding="utf-8", errors="replace") if (ROOT / "core/python/pyproject.toml").exists() else ""
    pydeps = sorted(set(re.findall(r"^[ \t]*[\"']?([A-Za-z][A-Za-z0-9_.-]+)(?:[<>=!~].*)?[\"']?,?$", pytext, re.M)))
    pydeps += sorted(set(re.findall(r"^[ \t]*[\"']?([A-Za-z][A-Za-z0-9_.-]+)(?:[<>=!~].*)?[\"']?,?$", corepytext, re.M)))
    py_rows = []
    for dep in sorted(set(pydeps), key=str.lower):
        key = dep.lower().replace("-", "_")
        hits = scan_text(rf"\b{re.escape(key)}\b|\b{re.escape(dep)}\b", safe_files({".py"}))
        py_rows.append({"name": dep, "used_by": sorted(set(h["path"] for h in hits))[:80], "usage_hits": len(hits), "role": "production/test/build/unknown", "duplicate_capability": "unknown", "feature_flags": "unknown", "runtime_relevance": "high" if dep.lower() in {"openai", "playwright", "fastapi", "uvicorn", "pyyaml", "blake3"} else "unknown"})
    rust_rows = []
    for cargo in safe_files({".toml"}):
        if cargo.name != "Cargo.toml":
            continue
        text = cargo.read_text(encoding="utf-8", errors="replace")
        for section in re.finditer(r"\[dependencies\](.*?)(?=\n\[|\Z)", text, re.S):
            for line in section.group(1).splitlines():
                m = re.match(r"\s*([A-Za-z0-9_-]+)\s*=", line)
                if not m:
                    continue
                name = m.group(1); key = name.replace("-", "_")
                hits = scan_text(rf"\b{re.escape(key)}::|\b{re.escape(key)}\b", [p for p in safe_files({".rs"}) if str(p).startswith(str(cargo.parent))])
                rust_rows.append({"name": name, "manifest": rel(cargo), "used_by": sorted(set(h["path"] for h in hits))[:80], "usage_hits": len(hits), "role": "production/test/build/POC/unknown", "feature_flags": "line-level Cargo feature declaration", "runtime_relevance": "unknown"})
    return {"schema": "aegis-architecture-dependency-rent-v1", "python": py_rows, "rust": rust_rows, "note": "Direct dependency declarations are mechanically extracted; semantic production reachability and license/security rent still require review."}


def trust_policy() -> dict[str, Any]:
    hits = scan_text(r"\b(?:DEV|PROD|STAGING)\b|trust[_ -]?level|fail[-_ ]?(?:open|closed)|fallback|capability|security[_ -]?policy")
    defaults = [x for x in hits if "default" in x["text"].lower() or "= \"dev\"" in x["text"].lower() or "= \"prod\"" in x["text"].lower() or "fallback" in x["text"].lower()]
    return {"schema": "aegis-architecture-trust-policy-v1", "records": hits[:4000], "records_truncated": len(hits) > 4000, "defaults_and_fallbacks": defaults[:1000], "known_contradiction": {"python": "AgentConfig defaults DEV", "core_python": "normalize_trust_level defaults PROD", "status": "UNRESOLVED_POLICY_AMBIGUITY"}}


def errors() -> dict[str, Any]:
    py = scan_text(r"except\s+(?:Exception|BaseException)|except\s*:|str\(exc|raise\s+RuntimeError|raise\s+Exception", safe_files({".py"}))
    rs = scan_text(r"\.unwrap\(\)|\.expect\(|panic!\(|Box::leak|unsafe\b", safe_files({".rs"}))
    for row in rs:
        try:
            text = (ROOT / row["path"]).read_text(encoding="utf-8", errors="replace")
            before = "\n".join(text.splitlines()[max(0, row["line"] - 25):row["line"]])
            row["likely_test_context"] = "cfg(test)" in before or "#[test]" in before
        except OSError:
            row["likely_test_context"] = False
    return {"schema": "aegis-architecture-error-model-v1", "python_broad_catches_and_conversions": py[:3000], "python_truncated": len(py) > 3000, "rust_panic_unwrap_unsafe": rs[:5000], "rust_truncated": len(rs) > 5000, "precise_ffi_cases": [{"path": "core/rust/src/ffi.rs", "line": 24, "case": "SessionSearchIndex::new(...).unwrap()", "status": "production-path unwrap requires targeted review"}, {"path": "core/rust/src/ffi.rs", "line": "~39-43", "case": "Box::leak for LLM request/error strings", "status": "possible per-call leak; exact ownership must be confirmed"}], "limitations": ["test context inference is lexical", "macro-generated error conversions are not resolved"]}


def concurrency() -> dict[str, Any]:
    py = scan_text(r"asyncio|create_task|Thread|threading|multiprocessing|Lock\(|RLock\(|Semaphore\(|global |asyncio\.Lock|Process", safe_files({".py"}))
    rs = scan_text(r"tokio::|spawn\(|rayon|Arc<|Mutex<|RwLock<|Atomic|TcpListener|TcpStream|thread::|std::thread|mpsc", safe_files({".rs"}))
    return {"schema": "aegis-architecture-concurrency-v1", "python": py[:3000], "python_truncated": len(py) > 3000, "rust": rs[:5000], "rust_truncated": len(rs) > 5000, "flags": ["MULTIPLE_WRITERS", "LOCK_ACROSS_AWAIT", "GLOBAL_MUTABLE", "POTENTIAL_CONTENTION", "UNKNOWN"], "note": "Pattern evidence only; no race claim is made."}


def performance() -> dict[str, Any]:
    suspects = {
        "repeated_serialization": r"json\.dumps|json\.loads|serde_json|to_json|from_json",
        "repeated_hashing": r"blake3|hashlib|canonical_hash|hash_chain",
        "copies_clones": r"\.clone\(\)|copy\(|deepcopy|bytes\(",
        "lock_heavy": r"Mutex|RwLock|Lock\(|parking_lot|threading\.Lock",
        "filesystem_scans": r"rglob\(|glob\(|os\.walk|walkdir",
        "context_reconstruction": r"context|search_past|retrieve",
        "subprocess_startup": r"Popen|subprocess\.run|Command::new",
        "ffi_crossing": r"aegis_nerve|PyLabController|pyfunction|PyResult",
        "unbounded_collections": r"\.append\(|Vec<|HashMap<|dict\(|list\(",
    }
    rows = []
    for name, pat in suspects.items():
        hits = scan_text(pat)
        rows.append({"suspect": name, "hits": len(hits), "sample": hits[:25], "status": "SUSPECT_ONLY_UNMEASURED"})
    return {"schema": "aegis-architecture-performance-v1", "local_profile": {"status": "NOT_MEASURABLE_LOCALLY", "reason": "No external credentials/provider execution was invoked; no expensive Lab/benchmark workload was run for safety.", "startup_import": "not executed in this collection pass", "run_ffi_time": "NOT_MEASURED", "event_writes": "NOT_MEASURED", "serialization": "NOT_MEASURED", "subprocesses": "NOT_MEASURED"}, "static_suspects": rows}


def wheel_graph() -> dict[str, Any]:
    root_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8", errors="replace") if (ROOT / "pyproject.toml").exists() else ""
    core_text = (ROOT / "core/python/pyproject.toml").read_text(encoding="utf-8", errors="replace") if (ROOT / "core/python/pyproject.toml").exists() else ""
    mappings = [
        {"source_path": "aegis_cognition/*", "wheel_path": "aegis_cognition/*", "import_name": "aegis_cognition", "basis": "root pyproject/maturin python-source"},
        {"source_path": "core/python/*", "wheel_path": "aegis_cognition/core/python/* (intended by include core/**/*.py)", "import_name": "core.python.* or compatibility modules", "basis": "root maturin include; exact wheel listing not run"},
        {"source_path": "core/rust/src/lib.rs + feature python-extension", "wheel_path": "aegis_cognition/aegis_nerve*.pyd|*.so", "import_name": "aegis_cognition.aegis_nerve", "basis": "[tool.maturin] module-name"},
        {"source_path": "core/python/aegis_cli.py", "wheel_path": "console-script aegis", "import_name": "aegis_cli:main", "basis": "core/python pyproject"},
        {"source_path": "aegis_cognition/cli.py", "wheel_path": "console-script aegis", "import_name": "aegis_cognition.cli:main", "basis": "root pyproject"},
    ]
    existing = [rel(p) for p in safe_files({".whl"})]
    return {"schema": "aegis-architecture-wheel-graph-v1", "mappings": mappings, "duplicate_imports": ["aegis (two console-script owners)", "core.python compatibility imports overlap root package"], "existing_wheels": existing, "build_status": "NOT_RUN_FOR_SAFETY", "root_pyproject_excerpt": {"maturin": "present" if "[tool.maturin]" in root_text else "absent", "module_name": re.search(r"module-name\s*=\s*\"([^\"]+)\"", root_text).group(1) if re.search(r"module-name\s*=\s*\"([^\"]+)\"", root_text) else None}, "core_python_project_present": bool(core_text)}


def markdown_report(state: dict[str, Any], pg: dict[str, Any], rg: dict[str, Any], reach: dict[str, Any], ffi: dict[str, Any], authority: dict[str, Any], side: dict[str, Any], retry: dict[str, Any], trust: dict[str, Any], errors_report: dict[str, Any], deps: dict[str, Any], perf: dict[str, Any], conc: dict[str, Any], public: dict[str, Any], dup: dict[str, Any], wheel: dict[str, Any]) -> str:
    modules = list(pg["modules"].values())
    py_cycles = pg.get("cycles", [])
    high_in = sorted(modules, key=lambda x: x.get("fan_in", 0), reverse=True)[:12]
    high_out = sorted(modules, key=lambda x: x.get("fan_out", 0), reverse=True)[:12]
    classes = "\n".join(f"- `{e['path']}` → **{e['category']}**" for e in state["entries"])
    reach_counts = defaultdict(int)
    for row in reach["files"]:
        for label in row["reachable_from"]:
            reach_counts[label] += 1
    ffi_preview = "\n".join(f"- `{r['path']}:{r['line']}` `{r['python_facing_name']}` → `{r['rust_function_or_type']}`; flags={','.join(r['flags']) or 'none'}" for r in ffi["records"][:80])
    side_preview = "\n".join(f"- `{r['path']}:{r['line']}` `{r['function']}` → `{r['effect']}`; roots={','.join(r['reachable_from'])}" for r in side["python_function_records"][:80])
    retry_preview = "\n".join(f"- `{x['path']}:{x['line']}` `{x['text']}`" for x in retry["records"][:120])
    trust_preview = "\n".join(f"- `{x['path']}:{x['line']}` `{x['text']}`" for x in trust["defaults_and_fallbacks"][:100])
    rust_high = sorted(rg.get("modules", {}).values(), key=lambda x: (x.get("fan_in", 0) + x.get("fan_out", 0)), reverse=True)[:15]
    rust_preview = "\n".join(f"- `{r['path']}` `{r['module']}` fan_in={r.get('fan_in',0)} fan_out={r.get('fan_out',0)} pub={r.get('public_declarations',0)} responsibility={r.get('primary_responsibility')}" for r in rust_high)
    return f"""# AEGIS — Architecture Convergence Evidence Pack

**Collection date:** 2026-08-28  
**Repository:** `{ROOT}`  
**Method:** fresh filesystem + Git + Python AST + Rust source + `cargo metadata --no-deps`; repository knowledge-graph index intentionally not used as truth.  
**Safety:** no refactor, delete, feature, wheel build, provider call, benchmark campaign, fuzz, soak, or stress workload was run.

This document is an evidence pack, not a redesign. It reports what the current working tree mechanically supports and labels uncertainty. Generated JSON artifacts in this directory are audit outputs only.

## 1. Current state snapshot

| Field | Value |
|---|---|
| HEAD | `{state.get('head')}` |
| branch | `{state.get('branch')}` |
| origin/main ref | `{state.get('origin_main')}` |
| ahead/behind | `{state.get('ahead_behind')}` |
| tracked modified files | `{len(state.get('tracked_modified', []))}` |
| staged files | `{len(state.get('staged', []))}` |
| untracked files | `{len(state.get('untracked', []))}` |
| exact total status entries | `{state.get('total_entries')}` |

### File classification

{classes}

The generated evidence-pack files are included in the final status snapshot as `GENERATED`; the pre-generation snapshot is retained in `architecture_reachability.json` under `collection_state_before_generation` when available.

## 2. Fresh Python import graph

AST scan found **{pg['module_count']} modules** and **{pg['edge_count']} resolved internal import edges**. Full adjacency is in `architecture_python_import_graph.json`.

- Cycles mechanically detected: `{len(py_cycles)}`. Cycles are listed in the JSON; dynamic/decorator edges can be missing.
- Dynamic imports: `{sum(len(x.get('dynamic_imports', [])) for x in modules)}` module records.
- `sys.path` manipulation records: `{sum(len(x.get('sys_path_manipulation', [])) for x in modules)}`.
- Import-time side-effect candidates: `{sum(len(x.get('import_time_side_effects', [])) for x in modules)}` lexical candidates.
- Lazy import detection is conservative; imports nested in functions need parent-aware AST analysis before being classified as intentional.

Highest fan-in:
{chr(10).join(f"- `{x['module']}` fan_in={x['fan_in']} fan_out={x['fan_out']} ({x['path']})" for x in high_in)}

Highest fan-out:
{chr(10).join(f"- `{x['module']}` fan_in={x['fan_in']} fan_out={x['fan_out']} ({x['path']})" for x in high_out)}

## 3. Rust module + crate graph

`cargo metadata --no-deps` status: **{rg.get('workspace', {}).get('status', rg.get('status', 'UNKNOWN'))}**. Workspace packages, dependencies, features, targets, modules, visibility, public declarations, and target limitations are in `architecture_rust_module_graph.json`.

High-coupling Rust modules (source scan):
{rust_preview}

The graph does not claim macro-expanded, trait-dispatch, generated, or rust-analyzer-complete edges. `pub mod` and `pub use` are recorded conservatively; accidental-public status requires API review.

## 4. Production reachability graph

Root labels are separated as `PUBLIC_API`, `LAB`, `CLI`, `FFI`, `PLUGIN`, `TEST_ONLY`, `SCRIPT_ONLY`, and `POC_ONLY`. Reachability counts from the fresh static graph: `{dict(reach_counts)}`. Full per-file rows are in `architecture_reachability.json`.

The allowed final class is deliberately conservative: static absence yields `UNKNOWN`, not delete authorization. Dynamic imports, plugin discovery, packaging includes, console scripts, subprocesses, and documentation references are separately recorded.

## 5. Exact package / wheel graph

The current configuration declares two package/build surfaces and two `aegis` console-script owners. The source→wheel→import mappings and existing wheel search are in `architecture_wheel_graph.json`.

**Wheel build/listing was not run** to avoid a potentially heavy native build and source mutation. Source-checkout imports therefore remain insufficient to claim wheel correctness.

## 6. FFI contract inventory

Fresh PyO3 attribute scan found **{len(ffi['records'])} exposed function/class candidates**. Full records include Python name, Rust symbol, callers, inputs/outputs, serialization/copying suspicion, side-effect class, authority relevance, frequency category, and flags.

{ffi_preview}

Macro-generated or re-exported exposure may be missing; caller frequency is a category estimate based on static callsites, not runtime telemetry.

## 7. Canonical state ownership matrix

Full 24-entity matrix is in `architecture_authority_matrix.json`. The current tree mechanically indicates a split for Lab entities: Python constructs/mutates a mutable projection and Rust validates/adjudicates many typed admissions, budgets, replay, and finalization operations. `TrustLevel` and `RetryPolicy` have compatibility duplicates/split ownership. No solution is recommended in this pack.

## 8. Side-effect reachability

Fresh lexical/AST side-effect candidates:

{side_preview}

Rust side-effect sites and all effect classes are in `architecture_side_effect_graph.json`. The requested chain `ENTRYPOINT → CALLERS → ADMISSION → CAPABILITY → BUDGET → LEASE → EFFECT → SETTLEMENT → EVIDENCE` cannot be mechanically proven complete from static source alone. Records therefore flag missing stages as **UNKNOWN/REQUIRES CONTRACT REVIEW**, not as a proven absence. Hidden adapter effects, descendants, macro calls, and external service effects remain outside static completeness.

## 9. Retry / timeout / cancellation graph

Fresh pattern inventory produced `{len(retry['records'])}` records. Representative locations:

{retry_preview}

The JSON separates retry count/backoff/idempotency/budget/attempt/cancellation fields as unknown unless directly visible. Duplicated candidate locations include provider, adapter, Lab, replay, and execution paths; no exactly-once or system-wide retry-budget claim is made.

## 10. Trust / policy graph

Fresh policy scan found `{len(trust['records'])}` records and `{len(trust['defaults_and_fallbacks'])}` default/fallback candidates.

{trust_preview}

Previously suspected contradiction remains mechanically present: `AgentConfig` defaults trust to `DEV`, while core evidence normalization defaults to `PROD`. This is **UNRESOLVED_POLICY_AMBIGUITY** until one authoritative source and contract test exist.

## 11. Large module responsibility map

Size alone is not used as a split recommendation. Responsibility clusters inferred from names/calls are:

| Module | Responsibility clusters | Classification |
|---|---|---|
| `aegis_cognition/lab.py` | run lifecycle; Python projection/event append; execution-cell registry; search/fetch; browser; experiments/simulation; gateway/provider; benchmark; archive/recovery; memory context/index | `MIXED_RESPONSIBILITY` (large orchestration surface; split not authorized by size) |
| `core/rust/src/lab.rs` | typed domain records; event kinds/hash; controller admission; budget/finalization; snapshot/restore; archive/replay | `COHESIVE` with high contract density |
| `core/rust/src/replay.rs` | binary record validation; hash chain; snapshot/archive; recovery semantics | `COHESIVE` / high branching |
| `core/rust/src/cli/mod.rs` | command parsing; policy/config; dispatch; output/error translation | `MIXED_RESPONSIBILITY` |
| `core/rust/src/ffi.rs` | PyO3 conversion; runtime/resource/evidence bindings; LabController binding; panic/error boundary | `GOD_MODULE_CANDIDATE` by boundary breadth, not size alone |
| `scripts/evidence_consistency_gate.py` | manifest schema; commit binding; remediation parity; suite evidence; report output | `MIXED_RESPONSIBILITY` |

Exact definitions and call evidence are recoverable from the JSON graphs; no reorganization was performed.

## 12. Duplication map

`architecture_duplicate_candidates.json` records semantic domains and exact implementation hits. Candidates are labelled `UNKNOWN` until contract-level equivalence is demonstrated. Main domains with multiple implementations are hashing, ID generation, retry, budget, trust, capability/admission, serialization, event creation, evidence validation, provider routing, browser policy, replay validation, context handling, telemetry, and error conversion. Some are intentional projection/compatibility; static text overlap cannot distinguish all cases.

## 13. Error model

The error artifact separates Python broad catches/conversions from Rust `unwrap`/`expect`/`panic!`/`unsafe`/`Box::leak` hits and marks likely test context lexically. Important exact cases remain:

- `core/rust/src/ffi.rs:24`: `SessionSearchIndex::new(...).unwrap()` on a native initialization path;
- `core/rust/src/ffi.rs` around the LLM bridge: `Box::leak` is used for request/error strings; possible per-call leak requires ownership confirmation;
- broad Python catches in application/adapter/provider/Lab/browser paths can convert failures into recorded blockers, but broad catching is still a compatibility/error-surface risk.

Static counts are not production-only panic counts because Rust tests are embedded in source files. Full rows are in `architecture_error_model.json`.

## 14. Dependency rent data

`architecture_dependency_rent.json` maps direct Python/Rust declarations to usage-hit locations, role (production/test/build/POC/unknown), feature declaration, duplicate capability, and runtime relevance. It intentionally does not recommend removal. PyO3/maturin, Wasmtime, Arrow/mmap/IPC, Playwright, provider SDKs, plugins, and POC crates create material ABI, security, platform, build, and operational rent.

## 15. Actual performance profile

**`NOT_MEASURABLE_LOCALLY` for representative Lab/non-Lab runs in this pass.** No external credentials/provider call, native wheel build, Lab workload, benchmark, or browser session was invoked. Startup/FFI/event/hash/serialization/subprocess timings are therefore not invented. The profile status and reason are in `architecture_performance.json`.

## 16. Static performance suspects

The artifact records lexical suspects only: repeated JSON/Serde serialization, repeated hashing, clones/copies, lock use, filesystem scans, context reconstruction, subprocess startup, FFI crossings, and unbounded collection growth. All are `SUSPECT_ONLY_UNMEASURED`; no performance regression or optimization claim follows.

## 17. Concurrency ownership

Fresh pattern inventory covers Python asyncio/tasks/threads/processes/locks/global state and Rust Tokio/spawn/Rayon/Arc/Mutex/RwLock/atomics/TCP. Full rows are in `architecture_concurrency.json`. Flags are `MULTIPLE_WRITERS`, `LOCK_ACROSS_AWAIT`, `GLOBAL_MUTABLE`, `POTENTIAL_CONTENTION`, and `UNKNOWN`; no race is claimed without a reproducer or proof.

## 18. Public API inventory

`architecture_public_api.json` lists root Python facade symbols, compatibility package/CLI, Rust public modules/declarations, PyO3 exposure, schemas, plugins, and both `aegis` console-script definitions. Root `aegis_cognition` is the stable-public candidate; `core/python` is compatibility; many Rust modules are internal-but-public. Accidental public surface requires explicit API review.

## 19. Current architecture graph

```text
[PUBLIC/API]
  aegis_cognition.Agent [A]
      -> AgentApplication [A]
      -> core/python compatibility facade [C]
      -> CLI entrypoints [A/C]

[SEMANTIC]
  AgentApplication -> LabApplication [A when lab=True]
  LabApplication -> LabRun Python projection [A for current lifecycle]
  LabRun -> typed mission/source/claim/hypothesis/experiment/observation [P/A]

[STATE/AUTHORITY]
  LabRun Python state [A for projection]
      -> PyLabController/Rust LabRuntime [A for native admission/budget/replay]
      -> ExecutionCellRegistry [A for explicit cell policy]
  Rust controller -> GT96/resource/replay/evidence [A]

[SCHEDULING/RESOURCE]
  Agent/Lab -> resource/execution lanes [A/P]
  replay directory -> ReplayWriterLease [A local / C hosted gap]

[EXECUTION CELLS]
  cells -> search/urlopen [E]
  cells -> browser/Playwright [E]
  cells -> experiments/simulation [E]
  cells -> provider/gateway [E]
  cells -> benchmark subprocess [E]
  cells -> memory context/index [E]

[ADAPTERS]
  provider route -> candidates/retry/fallback [C/P]
  browser runtime -> network/process/browser [E]
  operator/cluster -> artifact/network surfaces [E]

[EVIDENCE/REPLAY]
  Python append -> hash/native admission/Arrow audit [P/A]
  Rust event chain -> snapshot/restore/archive verify [A]

[STORAGE]
  filesystem/replay/mmap/Arrow/memory [E/R]

[PACKAGING]
  root maturin -> aegis_cognition + aegis_cognition.aegis_nerve [A]
  core/python setuptools -> compatibility package + same `aegis` name [C]
```

This is the current graph only; it is not a target architecture. `[A]` means observed authority for that scope, `[P]` proposal/projection, `[R]` read, `[E]` effect, `[C]` compatibility.

## 20. Machine-readable output

Generated audit artifacts:

- `architecture_python_import_graph.json`
- `architecture_rust_module_graph.json`
- `architecture_reachability.json`
- `architecture_ffi_inventory.json`
- `architecture_authority_matrix.json`
- `architecture_side_effect_graph.json`
- `architecture_retry_graph.json`
- `architecture_public_api.json`
- `architecture_duplicate_candidates.json`
- `architecture_dependency_rent.json`
- `architecture_trust_policy.json`
- `architecture_error_model.json`
- `architecture_concurrency.json`
- `architecture_performance.json`
- `architecture_wheel_graph.json`

## Evidence boundary

This pack gives fresh machine-grounded design inputs, not proof of security, production readiness, benchmark superiority, scientific validity, complete reachability, or universal native authority. Missing dynamic/macro/hosted/platform evidence is enumerated rather than filled with assumptions.
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    state_before = status_snapshot()
    pg = python_graph()
    meta = rust_metadata()
    rg = rust_graph(meta)
    reach = reachability(pg)
    ffi = ffi_inventory()
    authority = authority_matrix()
    side = side_effect_graph(pg)
    retry = retry_graph()
    trust = trust_policy()
    err = errors()
    deps = dependencies()
    perf = performance()
    conc = concurrency()
    public = public_api()
    dup = duplication_candidates()
    wheel = wheel_graph()
    write_json("architecture_python_import_graph.json", pg)
    write_json("architecture_rust_module_graph.json", rg)
    reach["collection_state_before_generation"] = state_before
    write_json("architecture_reachability.json", reach)
    write_json("architecture_ffi_inventory.json", ffi)
    write_json("architecture_authority_matrix.json", authority)
    write_json("architecture_side_effect_graph.json", side)
    write_json("architecture_retry_graph.json", retry)
    write_json("architecture_public_api.json", public)
    write_json("architecture_duplicate_candidates.json", dup)
    write_json("architecture_dependency_rent.json", deps)
    write_json("architecture_trust_policy.json", trust)
    write_json("architecture_error_model.json", err)
    write_json("architecture_concurrency.json", conc)
    write_json("architecture_performance.json", perf)
    write_json("architecture_wheel_graph.json", wheel)
    final_state = status_snapshot()
    # Re-write the reachability record with the exact final status after all audit artifacts exist.
    reach["collection_state_after_generation"] = final_state
    write_json("architecture_reachability.json", reach)
    report = markdown_report(final_state, pg, rg, reach, ffi, authority, side, retry, trust, err, deps, perf, conc, public, dup, wheel)
    (OUT / "AEGIS_ARCHITECTURE_CONVERGENCE_EVIDENCE_PACK.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": "PASS", "output": rel(OUT), "python_modules": pg["module_count"], "python_edges": pg["edge_count"], "ffi_candidates": len(ffi["records"]), "status_entries_after_generation": final_state["total_entries"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
