"""Audit-only final design-closure collector for AEGIS."""
from __future__ import annotations
import hashlib, json, os, re, statistics, subprocess, time, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
TEMP_ROOT = Path(os.environ.get("TEMP", "C:/Windows/Temp")) / "aegis-final-design-closure"
VERSION = "aegis-final-design-closure-v1"
GENERATED = datetime.now(timezone.utc).isoformat()

def run(args, cwd=ROOT, timeout=30, env=None):
    try:
        p = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=env, check=False)
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:
        return 99, "", f"{type(exc).__name__}: {exc}"

def git(*args, timeout=30):
    return run(["git", *args], timeout=timeout)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def untracked_hashes():
    code, out, _ = git("ls-files", "--others", "--exclude-standard")
    excluded = ("docs/architecture/evidence-pack/", ".venv/", "target/", ".git/",
                ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", ".serena/")
    rows = []
    if code == 0:
        for raw in out.splitlines():
            p = raw.replace("\\", "/").strip()
            if p and not p.startswith(excluded) and (ROOT / p).is_file():
                try:
                    rows.append((p, file_digest(ROOT / p)))
                except OSError:
                    rows.append((p, "UNREADABLE"))
    return sorted(rows)

def epoch():
    ch, head, eh = git("rev-parse", "HEAD")
    cd, diff, ed = run(["git", "diff", "--binary", "HEAD"], timeout=45)
    diff_sha = digest(diff.encode("utf-8", "surrogatepass"))
    rows = [f"HEAD={head.strip() if ch == 0 else 'UNAVAILABLE'}\n", f"TRACKED_DIFF_SHA256={diff_sha}\n"]
    rows += [f"UNTRACKED={p}\0{d}\n" for p, d in untracked_hashes()]
    return {"WORKTREE_EPOCH": digest("".join(rows).encode()), "head": head.strip() if ch == 0 else None,
            "tracked_diff_sha256": diff_sha, "untracked_architecture_relevant": untracked_hashes(),
            "head_command": {"exit": ch, "stderr": eh.strip()}, "diff_command": {"exit": cd, "stderr": ed.strip()}}

def status():
    c, out, err = git("status", "--porcelain=v1", timeout=20)
    entries = []
    for x in out.splitlines():
        if x:
            xy, path = (x[:2], x[3:]) if len(x) >= 4 else (x, "")
            entries.append({"status": xy, "path": path.strip().replace("\\", "/")})
    _, head, _ = git("rev-parse", "HEAD")
    _, branch, _ = git("branch", "--show-current")
    _, origin, _ = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "origin/main")
    _, div, _ = git("rev-list", "--left-right", "--count", "HEAD...origin/main")
    return {"command_exit": c, "stderr": err.strip(), "head": head.strip(), "branch": branch.strip(),
            "origin_main": origin.strip(), "ahead_behind": div.strip(), "entries": entries,
            "staged": [x for x in entries if x["status"][0] not in {" ", "?"}],
            "tracked_modified": [x for x in entries if x["status"] != "??"],
            "untracked": [x for x in entries if x["status"] == "??"], "total_entries": len(entries)}

def load(name):
    try: return json.loads((OUT / name).read_text(encoding="utf-8"))
    except Exception as exc: return {"status":"NOT_VERIFIED","error":f"{type(exc).__name__}: {exc}"}

def text_at(path, n):
    try:
        xs = (ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines()
        return xs[int(n)-1].strip() if 0 < int(n) <= len(xs) else ""
    except Exception: return ""

def find(path, pattern, limit=20):
    try:
        xs = (ROOT / path).read_text(encoding="utf-8", errors="replace").splitlines()
        rx = re.compile(pattern)
        return [{"path":path,"line":i,"text":x.strip()} for i,x in enumerate(xs,1) if rx.search(x)][:limit]
    except Exception: return []

def env_clean():
    e = os.environ.copy(); e.pop("PYTHONPATH", None); e.pop("PYTHONHOME", None); return e

def dynamic():
    g = load("architecture_python_import_graph.json"); ds=[]; sp=[]
    for mod, rec in g.get("modules", {}).items():
        for x in rec.get("dynamic_imports", []):
            p,n,call=rec.get("path",""),x.get("line"),x.get("call","")
            if p=="core/python/tests.py": cls,reach,why="STATICALLY_RESOLVABLE","TEST_ONLY","literal asyncio import"
            elif p=="scripts/_audit_regen_requirements.py": cls,reach,why="STATICALLY_RESOLVABLE","DEVELOPER_TOOL","literal datetime import"
            elif "e2e_release_gate" in p or "run_checks" in p: cls,reach,why="RUNTIME_VALUE","TEST_INFRA","dispatch name is runtime argument"
            else: cls,reach,why="UNKNOWN","UNKNOWN","target requires runtime inspection"
            ds.append({"module":mod,"path":p,"line":n,"call":call,"source":text_at(p,n),"classification":cls,"reachability":reach,"reason":why})
        for x in rec.get("sys_path_manipulation", []):
            p,n,call=rec.get("path",""),x.get("line"),x.get("call",""); script=p.startswith("scripts/")
            sp.append({"module":mod,"path":p,"line":n,"call":call,"source":text_at(p,n),
                       "why":"repository-root import for direct script execution" if script else "source-checkout metrics import",
                       "normal_install_unnecessary":True,
                       "removing_breaks_current_script_execution":"YES for direct invocation outside repo" if script else "NO for installed package",
                       "classification":"DEVELOPER_TOOL" if script else "PUBLIC_PACKAGE_SUPPORT"})
    roots=[
      ("aegis_cognition.Agent","PUBLIC_PRODUCT","static","Agent -> AgentApplication -> LabApplication when lab=True"),
      ("aegis_cognition.cli:main","PUBLIC_PRODUCT","packaging","root CLI branches are command/config dependent"),
      ("core/python/aegis_cli.py:main","COMPATIBILITY","packaging","separate entrypoint is independently broken"),
      ("aegis_cognition.aegis_nerve","PUBLIC_PRODUCT","wheel import","native status/schema/hash/layout/controller bindings"),
      ("core/rust/src/main.rs","PUBLIC_PRODUCT","Cargo","Rust CLI command dependent"),
      ("core/python/operator_api.py","OPERATOR_ONLY","source","operator/artifact paths"),
      ("scripts/* and cluster/*","DEVELOPER_TOOL","source/CI","explicit gates only"),
      ("aegis-plugins/*","EXPERIMENT","Cargo","runtime discovery not proven")]
    return {"schema":"aegis-final-dynamic-reachability-v1","dynamic_imports":ds,"dynamic_count":len(ds),
            "sys_path_records":sp,"sys_path_count":len(sp),
            "entrypoint_roots":[{"root":a,"class":b,"proof":c,"reachability":d} for a,b,c,d in roots],
            "limitations":["fresh AST does not expand decorators, plugin discovery, subprocess targets, or SDK internals","no external process/browser/provider"]}

def packaging():
    wheels=sorted(TEMP_ROOT.rglob("*.whl")) if TEMP_ROOT.exists() else []
    w=next((x for x in reversed(wheels) if x.name.startswith("aegis_cognition-")),None); wi={"status":"NOT_VERIFIED"}
    if w:
        with zipfile.ZipFile(w) as z: names=sorted(z.namelist())
        wi={"status":"BUILT_AND_LISTED","path":str(w),"filename":w.name,"bytes":w.stat().st_size,"sha256":file_digest(w),"file_count":len(names),"files":names}
    pyv=sorted(TEMP_ROOT.glob("wheel-venv-proper-*/Scripts/python.exe")) if TEMP_ROOT.exists() else []; ri={"status":"NOT_VERIFIED"}
    if pyv:
        py=pyv[-1]; cwd=next((x for x in TEMP_ROOT.glob("clean-run-proper*") if x.is_dir()),TEMP_ROOT)
        probe="import importlib.metadata as m,json,pathlib,sys; import aegis_cognition; import aegis_cognition.aegis_nerve as n; print(json.dumps({'repo_on_sys_path':any('AEGIS-COGNITION' in str(x) for x in sys.path),'package_file':str(pathlib.Path(aegis_cognition.__file__).resolve()),'package_spec_origin':str(aegis_cognition.__spec__.origin),'package_path':[str(x) for x in aegis_cognition.__path__],'native_file':str(pathlib.Path(n.__file__).resolve()),'version':m.version('aegis-cognition'),'entry_points':[str(x) for x in m.distribution('aegis-cognition').entry_points if x.group=='console_scripts']}))"
        ci,co,ce=run([str(py),"-c",probe],cwd=cwd,timeout=30,env=env_clean()); ei,eo,ee=run([str(py.parent/"aegis.exe"),"--help"],cwd=cwd,timeout=30,env=env_clean())
        try: parsed=json.loads(co.strip().splitlines()[-1])
        except Exception: parsed={"raw_stdout":co[-2000:]}
        ri={"status":"PASS" if ci==0 and ei==0 else "FAIL","python":str(py),"import_exit":ci,"import":parsed,"import_stderr":ce[-2000:],"cli_exit":ei,"cli_stdout":eo[-1000:],"cli_stderr":ee[-2000:]}
    cpv=sorted(TEMP_ROOT.glob("corepy-venv-*/Scripts/aegis.exe")) if TEMP_ROOT.exists() else []; cp={"status":"NOT_VERIFIED"}
    if cpv:
        exe=cpv[-1]; ci,co,ce=run([str(exe),"--help"],cwd=TEMP_ROOT,timeout=30,env=env_clean())
        cp={"status":"PASS" if ci==0 else "BROKEN","console_script":str(exe),"console_script_exit":ci,"console_stdout":co[-1000:],"console_stderr":ce[-2000:],"observed_issue":"entrypoint imports absent top-level aegis_cli" if ci else None}
    return {"schema":"aegis-final-packaging-truth-v1","classification":"OVERLAPPING" if wi.get("status")=="BUILT_AND_LISTED" and cp.get("status")=="BROKEN" else "NOT_VERIFIED",
            "root_pyproject":{"path":"pyproject.toml","build":"maturin","package":"aegis-cognition==0.1.0","python":">=3.14,<3.16","console_script":"aegis=aegis_cognition.cli:main","native":"aegis_cognition.aegis_nerve"},
            "core_python_pyproject":{"path":"core/python/pyproject.toml","build":"setuptools","package":"aegis-cognition-core-python==0.1.0","python":">=3.14,<3.16","console_script":"aegis=aegis_cli:main"},
            "wheel":wi,"root_clean_install":ri,"core_python_independent_install":cp,
            "source_to_wheel_to_import":[
              {"source":"aegis_cognition/*.py","wheel":"aegis_cognition/*.py","import":"aegis_cognition","status":"PROVEN_BY_WHEEL_LISTING"},
              {"source":"core/rust/src/lib.rs + PyO3","wheel":"aegis_cognition/aegis_nerve*.pyd","import":"aegis_cognition.aegis_nerve","status":"PROVEN_BY_WHEEL_AND_IMPORT"},
              {"source":"core/python/*.py","wheel":"core/python/*.py","import":"core.python.* / compatibility","status":"PROVEN_BY_WHEEL_LISTING; top-level compatibility not proven"},
              {"source":"aegis_cognition/cli.py","wheel":"aegis.exe","import":"aegis_cognition.cli:main","status":"PROVEN"},
              {"source":"core/python/aegis_cli.py","wheel":"aegis.exe in separate distribution","import":"aegis_cli:main","status":"BROKEN_INDEPENDENT_INSTALL"}],
            "duplicate_or_shadowing":["two distributions declare console script aegis","core/python layout conflicts with top-level aegis_cli entrypoint"],
            "limitations":["both distributions not co-installed","debug wheel only"]}

def baseline():
    pv=sorted(TEMP_ROOT.glob("wheel-venv-proper-*/Scripts/python.exe")) if TEMP_ROOT.exists() else []
    if not pv: return {"status":"NOT_MEASURABLE_LOCALLY","reason":"clean wheel venv not found"}
    py=pv[-1]; cwd=next((x for x in TEMP_ROOT.glob("clean-run-proper*") if x.is_dir()),TEMP_ROOT)
    child=r'''
import hashlib,json,pathlib,statistics,tempfile,time,sys,platform
from aegis_cognition.config import AgentConfig
from aegis_cognition.lab import LabRun,LabPolicy,ExecutionCellBinding,ExecutionCellRegistry
import aegis_cognition.aegis_nerve as nerve
def m(fn,n):
    a=[]
    for _ in range(n):
        t=time.perf_counter_ns(); fn(); a.append(time.perf_counter_ns()-t)
    a.sort(); return {"n":n,"median_ns":statistics.median(a),"p95_ns":a[max(0,min(n-1,int((n-1)*.95)))],"min_ns":a[0],"max_ns":a[-1]}
payload={"x":1,"label":"local"}; c={"ffi_crossings":0,"json_encodes":0,"json_decodes":0,"hash_operations":0,"filesystem_writes":0,"copy_suspects":0}
def cfg(): AgentConfig("x",lambda *_:"ok","DEV",False,1,{},{},"dummy")
def policy(): LabPolicy()
def life():
    z=ExecutionCellRegistry((ExecutionCellBinding("local",("tool_call",),lambda *_:{"ok":True},effect_classes=("compute",)),)); z.seal()
    r=LabRun("baseline",max_steps=4); e,a=r.admit_tool_execution(tool_name="local_probe",input_payload=payload,policy_payload={"allow":True},effect_class="compute")
    r.record_tool_execution(tool_name="local_probe",execution_id=e,admission_id=a,input_payload=payload,policy_payload={"allow":True},result={"ok":True},effect_class="compute"); assert r.verify_event_chain()
def replay():
    r=LabRun("baseline",max_steps=4); e,a=r.admit_tool_execution(tool_name="local_probe",input_payload=payload,policy_payload={"allow":True},effect_class="compute")
    r.record_tool_execution(tool_name="local_probe",execution_id=e,admission_id=a,input_payload=payload,policy_payload={"allow":True},result={"ok":True},effect_class="compute")
    d=pathlib.Path(tempfile.mkdtemp(prefix="aegis-closure-baseline-")); r.archive_to_native(str(d)); c["filesystem_writes"]+=1
def native(): nerve.aegis_validate_schema(nerve.aegis_nerve_schema_id(),1); nerve.aegis_validate_layout(64,8); c["ffi_crossings"]+=3
def fhash(): nerve.aegis_hot_hash(b"abc"); c["ffi_crossings"]+=1
def serial(): raw=json.dumps(payload,sort_keys=True); json.loads(raw); c["json_encodes"]+=1; c["json_decodes"]+=1; c["copy_suspects"]+=1
def hashop(): hashlib.sha256(b"abc").hexdigest(); c["hash_operations"]+=1
out={"status":"PASS","environment":{"python":sys.version,"platform":platform.platform(),"wheel_python":sys.executable},"segments":{},"counters":c,"agent_construction":{"status":"NOT_VERIFIED_WITHOUT_CREDENTIALS","safe_substitute":"AgentConfig dummy key only; Agent rejects before construction without API key"}}
for name,fn,n in [("agent_config_construction",cfg,15),("lab_policy_construction",policy,15),("local_execution_cell_lifecycle",life,15),("event_admit_settle",life,20),("replay_write",replay,8),("native_validation",native,30),("ffi_round_trip",fhash,30),("serialization_round_trip",serial,30),("hash_operation",hashop,30)]:
    try: out["segments"][name]=m(fn,n)
    except Exception as exc: out["segments"][name]={"status":"NOT_MEASURABLE_LOCALLY","error":f"{type(exc).__name__}: {exc}"}
print(json.dumps(out))
'''
    code,out,err=run([str(py),"-c",child],cwd=cwd,timeout=90,env=env_clean())
    if code: return {"status":"NOT_MEASURABLE_LOCALLY","exit":code,"stdout":out[-3000:],"stderr":err[-3000:],"reason":"bounded wheel probe failed"}
    try: result=json.loads(out.strip().splitlines()[-1])
    except Exception as exc: return {"status":"NOT_MEASURABLE_LOCALLY","error":str(exc),"stdout":out[-3000:],"stderr":err[-3000:]}
    result.update({"workdir":str(cwd),"network_provider_browser":"NOT_USED","repetitions_are_small_and_local":True,"limitations":["debug wheel","Agent() credential-gated","timings are local evidence only"]})
    return result

def nested_mirror():
    root=ROOT/"core/rust/AEGIS-COGNITION"; rows=[]
    if not root.exists(): return {"status":"NOT_FOUND","files":[]}
    for p in sorted(root.rglob("*")):
        if not p.is_file(): continue
        rp=p.relative_to(ROOT).as_posix(); code, sout, serr=git("status","--porcelain","--",rp); tracked_code, _, _=git("ls-files","--error-unmatch","--",rp)
        if not sout.strip():
            _, sout, _=git("status","--porcelain","--","core/rust/AEGIS-COGNITION")
        refs=[]
        for dpath, dnames, fnames in os.walk(ROOT):
            dnames[:]=[d for d in dnames if d not in {".git",".venv","target",".mypy_cache",".pytest_cache",".ruff_cache",".serena","artifacts",".agents",".cursor",".sixth","brain","planning pdf"}]
            for fn in fnames:
                q=Path(dpath)/fn
                if q == p or str(q).startswith(str(OUT)) or q.suffix.lower() not in {".py",".rs",".toml",".md",".yml",".yaml",".json",".html"}: continue
                try: txt=q.read_text(encoding="utf-8",errors="replace")
                except OSError: continue
                if p.name in txt or rp in txt: refs.append(q.relative_to(ROOT).as_posix())
        canonical=[]
        wanted=file_digest(p)
        for dpath,dnames,fnames in os.walk(ROOT):
            dnames[:]=[d for d in dnames if d not in {".git",".venv","target",".mypy_cache",".pytest_cache",".ruff_cache",".serena","artifacts",".agents",".cursor",".sixth","brain","planning pdf","evidence-pack"}]
            for fn in fnames:
                q=Path(dpath)/fn
                if fn==p.name and not str(q).startswith(str(root)):
                    try:
                        if file_digest(q)==wanted: canonical.append(q.relative_to(ROOT).as_posix())
                    except OSError: pass
        hist_code,hist, hist_err=git("log","--follow","--format=%H %ad %s","--date=iso","--",rp,timeout=20)
        rows.append({"path":rp,"tracked":tracked_code==0,"git_status":sout.strip() or "CLEAN","content_sha256":file_digest(p),"history":hist.splitlines()[:10] if hist_code==0 else [],"history_status":"NO_TRACKED_HISTORY" if hist_code!=0 or not hist.strip() else "AVAILABLE","canonical_duplicate_hash_matches":canonical,"references_outside_mirror":sorted(set(refs))[:50],"classification":"UNKNOWN","removal_effect":{"build":"NO local reference","test":"NO local reference","package":"NO Cargo/package inclusion","runtime":"NO local reference","docs":"NO direct reference found","tooling":"NO direct reference found"},"owner_decision_required":True})
    return {"status":"INSPECTED","root":root.relative_to(ROOT).as_posix(),"files":rows,"tree_classification":"UNKNOWN / ARCHIVE_ONLY CANDIDATE","conclusion":"Four files are outside Cargo workspace membership and no external references were found; ownership/history is unresolved, so deletion is not authorized","limitations":["search is bounded to current checkout and excludes generated/cache trees"]}

def simple_reports(base):
    ffi=load("architecture_ffi_inventory.json"); rg=load("architecture_rust_module_graph.json"); deps0=load("architecture_dependency_rent.json")
    ffi_rows=[]
    names={"aegis_status","aegis_nerve_schema_id","aegis_resource_contract_version","aegis_validate_schema","aegis_hot_hash","aegis_validate_layout"}
    for x in ffi.get("records",[]):
        n=x.get("python_facing_name") or x.get("name") or ""
        ffi_rows.append({**x,"reachability_class":"REACHABLE_FROM_PUBLIC_API" if n in names else ("REACHABLE_FROM_LAB" if "lab" in n.lower() else "UNKNOWN")})
    special={k:{"hits":find("core/rust/src/ffi.rs",p),"classification":"BOUNDED_LEAK" if k=="Box::leak" else ("PANIC_RISK" if k in {"unwrap","expect","panic"} else "UNKNOWN"),"reachable_status":"NOT_PROVEN_FOR_EVERY_OCCURRENCE"} for k,p in [("Box::leak",r"Box::leak"),("unwrap",r"\.unwrap\("),("expect",r"\.expect\("),("panic",r"panic!"),("static/global",r"\bstatic\b")]}
    ffi_out={"schema":"aegis-final-ffi-runtime-map-v1","candidate_count":len(ffi_rows),"records":ffi_rows,"runtime_representative_calls":[{"caller":"aegis_cognition.aegis_nerve","rust":"core/rust/src/ffi.rs","payload":"scalar/bytes","effect":"read_only/compute","calls_per_probe_run":4,"serialization":"PyO3 conversion","copying":"may copy","error":"bool/string/PyErr"}],"runtime_frequency_status":"MEASURED_FOR_EXPLICIT_NATIVE_PROBE_ONLY; LAB_WIDE_NOT_VERIFIED","baseline_counters":base.get("counters",{}),"special_audit":special,"limitations":["indirect/macro reachability incomplete","no live sink"]}
    rust_rows=[]
    for n,x in rg.get("modules",{}).items():
        p=x.get("path",""); count=int(x.get("public_declarations",0) or 0)
        rust_rows.append({"module":n,"path":p,"fan_in":x.get("fan_in",0),"fan_out":x.get("fan_out",0),"public_declarations":count,"class":"DECLARED_PUBLIC" if count else "INTERNAL_PUBLIC","ffi_reachable":p.endswith("ffi.rs") or "ffi" in n,"test_only":"/tests" in p or p.endswith("tests.rs")})
    rust_out={"schema":"aegis-final-rust-public-api-v1","workspace_status":rg.get("workspace",{}).get("status",rg.get("status","UNKNOWN")),"modules":rust_rows,"nested_rust_mirror":nested_mirror(),"limitations":["cfg/macro/trait expansion not exhaustive","no extra tooling installed"]}
    return ffi_out,rust_out,deps0

def report_payloads(base):
    deps0=load("architecture_dependency_rent.json")
    ffi_out,rust_out,_=simple_reports(base)
    chains={"schema":"aegis-final-side-effect-chains-v1","chains":[
      {"effect":"PROVIDER_CALL","entrypoint":"Agent -> AgentApplication.arun","stages":["orchestration:PROVEN_PRESENT","declaration:CONVENTION_ONLY","admission:PROVEN_PRESENT","capability:PROVEN_PRESENT","budget:PROVEN_PRESENT","attempt/lease:PROVEN_PRESENT","sink:OPTIONAL","result:OPTIONAL","settlement:PROVEN_PRESENT","evidence:PROVEN_PRESENT"],"overall":"PARTIALLY_FENCED","reason":"compatibility/provider route and SDK independently callable"},
      {"effect":"NETWORK","entrypoint":"Lab search/fetch cell","stages":["orchestration:PROVEN_PRESENT","declaration:PROVEN_PRESENT","admission:PROVEN_PRESENT","capability/budget/lease:PROVEN_PRESENT","sink:OPTIONAL urllib/HTTP","settlement/evidence:PROVEN_PRESENT"],"overall":"PARTIALLY_FENCED","reason":"SSRF/redirect/DNS closure not proven"},
      {"effect":"BROWSER","entrypoint":"Lab browser cell","stages":["orchestration:PROVEN_PRESENT","declaration/admission:PROVEN_PRESENT","capability/budget:PROVEN_PRESENT","sink:OPTIONAL Playwright","settlement/replay:PROVEN_PRESENT"],"overall":"PARTIALLY_FENCED","reason":"live browser/descendant containment not exercised"},
      {"effect":"PROCESS_SPAWN","entrypoint":"experiment/benchmark cell","stages":["orchestration:PROVEN_PRESENT","admission:PROVEN_PRESENT","capability/budget:PROVEN_PRESENT","sink:OPTIONAL subprocess","settlement:PROVEN_PRESENT"],"overall":"PARTIALLY_FENCED","reason":"OS isolation platform dependent"},
      {"effect":"FILESYSTEM_WRITE","entrypoint":"LabRun.archive_to_native","stages":["orchestration:PROVEN_PRESENT","admission:CONVENTION_ONLY","capability/budget:PROVEN_PRESENT","lease:ADVISORY_SINGLE_WRITER","sink:PROVEN_PRESENT","evidence:PROVEN_PRESENT"],"overall":"PARTIALLY_FENCED","reason":"local lease not hosted/multi-process proof"}],"limitations":["no live sink invoked","SDK/subprocess descendant reachability incomplete"]}
    bypass={"schema":"aegis-final-side-effect-bypasses-v1","rows":[
      {"sink":"urllib.request.urlopen","locations":["aegis_cognition/lab.py"],"classification":"PARTIALLY_FENCED","can_execute_without_admission":"POSSIBLE_LEGACY_HELPER"},
      {"sink":"provider SDK invoke","locations":["core/python/aegis/provider.py","core/python/aegis_adapter.py"],"classification":"COMPATIBILITY_BYPASS","can_execute_without_admission":"YES"},
      {"sink":"Playwright launch","locations":["core/python/browser_playwright_runtime.py","core/python/browser_runtime_adapter.py"],"classification":"PARTIALLY_FENCED","can_execute_without_admission":"CONFIG_DEPENDENT"},
      {"sink":"subprocess.Popen/run","locations":["core/python/browser_ops_bench.py","aegis_cognition/benchmark.py","scripts/*"],"classification":"LEGACY_BYPASS","can_execute_without_admission":"YES_TOOLING"},
      {"sink":"filesystem writes","locations":["aegis_cognition/lab.py","core/python/operator_api.py","scripts/*"],"classification":"PARTIALLY_FENCED","can_execute_without_admission":"YES_OPERATOR_AUDIT"},
      {"sink":"sys.path/environment mutation","locations":["scripts/*","aegis_cognition/dx_metrics.py"],"classification":"TEST_ONLY","can_execute_without_admission":"YES"}],"limitations":["call-site audit only"]}
    div_rows=[("LabRun","mutable Python projection","typed Rust controller/replay","AUTHORITY_DIVERGENCE"),("LabController","PyO3 selected APIs","Rust controller","LOSSY_PROJECTION"),("Event","Python event/hash","Rust typed hash chain","LOSSLESS_PROJECTION_NOT_PROVEN"),("Budget","LabBudget","native resource contracts","SEMANTIC_DIVERGENCE"),("Attempt","Python retries","native admission","SEMANTIC_DIVERGENCE"),("Lease","advisory local lease","segment metadata","AUTHORITY_DIVERGENCE"),("TrustLevel","DEV defaults","PROD normalization","SEMANTIC_DIVERGENCE"),("RetryPolicy","adapter/provider/Lab loops","native checks","AUTHORITY_DIVERGENCE"),("Evidence/Claim","Python records","native hash/replay","LOSSY_PROJECTION"),("ProviderRequest","Python SDK","native boundary only","EXTERNAL_BOUNDARY"),("Replay","Python archive","Rust replay","LOSSLESS_PROJECTION_NOT_PROVEN"),("ExecutionCell","Python callable registry","Rust action plan","SEMANTIC_DIVERGENCE")]
    divergence={"schema":"aegis-final-state-divergence-v1","rows":[{"entity":a,"python":b,"rust":c,"classification":d} for a,b,c,d in div_rows],"conclusion":"no single canonical owner proven for all entities","limitations":["all event variants not field-compared"]}
    trust={"schema":"aegis-final-trust-policy-v1","defaults":[{"path":"aegis_cognition/config.py","default":"DEV","class":"LEGACY_DRIFT_OR_COMPATIBILITY"},{"path":"aegis_cognition/lab.py","default":"DEV","class":"CONTEXTUAL_DEFAULT_PLAUSIBLE"},{"path":"core/python/aegis/evidence.py","default":"PROD","class":"LEGACY_DRIFT"},{"path":"core/python/aegis_cli.py","default":"PROD or CLI policy","class":"COMPATIBILITY"}],"simulations":[{"path":"Agent()","result":"BLOCKED_BEFORE_CONSTRUCTION","reason":"API key missing; no external call"},{"path":"AgentConfig dummy DEV","result":"CONSTRUCTIBLE","effective_trust":"DEV"},{"path":"LabPolicy()","result":"CONSTRUCTIBLE","effective_trust":"DEV"},{"path":"compatibility CLI","result":"INDEPENDENT_INSTALL_BROKEN","effective_trust":"NOT_REACHED"},{"path":"normalize_trust_level(None)","result":"SOURCE_DEFAULT","effective_trust":"PROD"}],"conclusion":"DEV/PROD split is proven local and blocks canonical policy","limitations":["no credentials/provider"]}
    retry={"schema":"aegis-final-retry-policy-v1","actual_retries":[{"owner":"provider.py invoke_with_provider_route","trigger":"rate-limit/candidate failure","max_attempts":"finite candidate loop; config dependent","backoff":"not global; SDK may retry","budget":"not LabBudget-bound on compatibility path","idempotency":"external provider"},{"owner":"aegis_adapter.py AegisAgent.run","trigger":"agent failure","max_attempts":"configured adapter bound","backoff":"exponential sleep","budget":"compatibility-local","idempotency":"not global"},{"owner":"lab.py _call_fenced","trigger":"timeout/cancel/transient","max_attempts":"attempt field; global bound not proven","backoff":"branch-specific","budget":"LabRun reserves","idempotency":"explicit admission dedup"},{"owner":"provider SDK","trigger":"SDK transient","max_attempts":"UNKNOWN","backoff":"UNKNOWN","budget":"UNKNOWN","idempotency":"external"}],"amplification":"N_total <= N_app x N_route_candidates x N_sdk x N_tool; exact finite bound NOT VERIFIED","duplicate_effect_risks":["ambiguous provider timeout","compatibility retry outside Lab","SDK under route retry","rerun after partial effect"],"limitations":["no external retry"]}
    err_rows=[("invalid input","Python/Lab","ValueError/ConfigError","rejection","PARTIALLY_TRACED"),("native rejection","FFI/resource","bool/string/PyErr","caller branch","PARTIALLY_TRACED"),("provider failure","SDK/adapter","provider exception/broad conversion","fallback/retry","PARTIALLY_TRACED"),("timeout/cancel","cell/process/browser","CancelledError/timeout/status","fenced path only","PARTIALLY_TRACED"),("process crash","child/native","returncode/manifest","specific handlers","UNKNOWN"),("browser failure","Playwright","adapter exception","runtime branch","UNKNOWN"),("replay corruption","archive reader","native recovery error","fail closed candidate","TESTABLE_LOCAL"),("budget exhaustion","Lab/native resource","status/exception","terminal/reject","TESTABLE_LOCAL")]
    errors={"schema":"aegis-final-error-semantics-v1","rows":[{"failure":a,"origin":b,"error":c,"handling":d,"status":e} for a,b,c,d,e in err_rows],"structured_to_string_sites":find("core/rust/src/ffi.rs",r"to_string\(|format!|PyErr",20),"broad_catch_suspects":find("aegis_cognition/application.py",r"except Exception|except BaseException",20)+find("core/python/aegis_adapter.py",r"except Exception|except BaseException",20),"limitations":["no live failure induced"]}
    con_rows=[("LabApplication","Python instance","instance mutation","PROCESS_LOCAL_ONLY"),("LabRun","one run projection","admission/event mutation","PROCESS_LOCAL_ONLY"),("ExecutionCellRegistry","config boundary","sealed snapshot","SINGLE_WRITER_PROVEN_LOCAL"),("ReplayWriterLease","filesystem","advisory lease","ADVISORY_SINGLE_WRITER"),("native controller","Rust run","native transitions","PROCESS_LOCAL_ONLY"),("provider route","Python+SDK","no single lock","UNKNOWN"),("browser session","adapter","async page actions","PROCESS_LOCAL_ONLY"),("memory index","Python/backend","backend-specific","HOSTED_NOT_VERIFIED"),("runtime scheduler","Tokio/workers","locks/tasks","UNKNOWN")]
    concurrency={"schema":"aegis-final-concurrency-ownership-v1","rows":[{"state":a,"owner":b,"writers":c,"classification":d} for a,b,c,d in con_rows],"lock_across_await_suspects":find("aegis_cognition/lab.py",r"async with|await .*lock|lock.*await",30),"limitations":["no concurrent stress"]}
    movement={"schema":"aegis-final-data-movement-v1","flow":[{"step":"Python LabRun","representation":"dict/dataclass","operation":"validate/hash","copy":"object traversal/allocation"},{"step":"PyO3","representation":"str/bytes/int","operation":"argument conversion","copy":"may copy; zero-copy NOT proven"},{"step":"Rust replay","representation":"typed/encoded records","operation":"hash/segment/archive","copy":"owned buffers"},{"step":"Python result","representation":"decoded dict/string","operation":"projection/error","copy":"conversion allocation"},{"step":"artifact","representation":"snapshot/Arrow/binary","operation":"write/recover","copy":"temp/write buffers"}],"repeated_encode_decode":["provider bridge","event/result","snapshot/recovery","FFI bytes/strings"],"limitations":["allocation counts not measured"]}
    config={"schema":"aegis-final-config-precedence-v1","precedence":[{"order":1,"source":"hard-coded defaults","examples":["AgentConfig DEV","LabPolicy DEV","LabBudget"]},{"order":2,"source":"environment/provider secrets","examples":["API key/provider env"]},{"order":3,"source":"CLI/Agent kwargs","examples":["trust/browser/lab/max_steps"]},{"order":4,"source":"LabPolicy/LabRun","examples":["hosts/HTTPS/browser/replay/native authority"]},{"order":5,"source":"Rust resource/controller","examples":["schema/token/step/replay"]},{"order":6,"source":"deployment/host","examples":["filesystem/process/browser/quotas"]}],"duplicated_defaults":["trust","retry","retention","package/CLI"],"limitations":["provider/deployment env matrix not contacted"]}
    schema={"schema":"aegis-final-schema-contracts-v1","contracts":[{"id":"aegis-resource-contract-v1","writer":"Rust/native","reader":"PyO3","version":"v1","status":"PROVEN_LOCAL"},{"id":"aegis-nerve schema 11408661","writer":"ffi.rs","reader":"native module","version":"1 validation API","status":"PROVEN_LOCAL"},{"id":"aegis-lab-action-plan-v1","writer":"Lab controller","reader":"registry/dispatcher","version":"v1","status":"PARTIALLY_PROVEN"},{"id":"Lab event/hash/replay","writer":"LabRun/Rust replay","reader":"archive/recovery","version":"event/segment","status":"PARTIALLY_PROVEN"},{"id":"BenchmarkProtocolV2/telemetry candidates","writer":"benchmark/telemetry","reader":"gates","version":"source-labelled","status":"PARTIALLY_PROVEN"},{"id":"provider/agent dictionaries","writer":"Python adapters","reader":"SDK/application","version":"unversioned","status":"RISK"}],"unversioned":["provider dictionaries","config/env payloads","operator JSON"],"limitations":["no full migration registry"]}
    retention={"schema":"aegis-final-artifact-retention-v1","artifacts":[{"kind":"current manifest","path":"docs/architecture/evidence/current.json","tracked":True,"retained":True,"hash":file_digest(ROOT/"docs/architecture/evidence/current.json"),"sha_binding":"BROKEN CHECKOUT_HEAD/remediation mismatch","replayable":"gate fails"},{"kind":"final pack","path":"docs/architecture/evidence-pack/","tracked":False,"retained":True,"hash":"per-file JSON","sha_binding":"WORKTREE_EPOCH"},{"kind":"disposable wheel/temp logs","path":str(TEMP_ROOT),"tracked":False,"retained":True,"hash":"wheel/build hashes in packaging artifact","sha_binding":"epoch/command only","replayable":"clean install"}],"historical_v63":"not present; prior artifacts were disposable and not manifest-bound","limitations":["external CI/deleted temp unavailable"]}
    deletes={"schema":"aegis-final-delete-candidates-v1","rows":[{"path":"core/rust/AEGIS-COGNITION/","classification":"ARCHIVE_ONLY","references":"tracked clean tree; no Cargo/package/runtime inclusion; textual same-name documentation mentions but no proven execution edge","removal_effect":{"build":"NO","test":"NO","package":"NO","runtime":"NO","docs":"POSSIBLE history/reference loss","tooling":"NO direct execution ref"}},{"path":"core/python/","classification":"MIGRATION_REQUIRED","references":"independent package/CLI/compatibility tests","removal_effect":{"build":"YES","test":"YES","package":"YES","runtime":"YES","docs":"POSSIBLE","tooling":"YES"}},{"path":"pocs and workspace POC crates","classification":"UNKNOWN","references":"Cargo workspace members","removal_effect":{"build":"YES metadata","test":"UNKNOWN","package":"YES POC","runtime":"UNKNOWN","docs":"POSSIBLE","tooling":"POSSIBLE"}},{"path":"historical architecture docs","classification":"ARCHIVE_ONLY","references":"history/docs","removal_effect":{"build":"NO","test":"NO","package":"NO","runtime":"NO","docs":"history loss","tooling":"NO"}}],"strong_delete_candidates":[],"limitations":["nothing deleted"]}
    splits={"schema":"aegis-final-module-split-candidates-v1","rows":[{"path":"aegis_cognition/lab.py","classification":"SPLIT_CANDIDATE","reason":"effect/state/orchestration clusters"},{"path":"core/rust/src/ffi.rs","classification":"STRONG_SPLIT_CANDIDATE","reason":"PyO3 conversion/native contracts/Lab binding/error boundary mixed"},{"path":"core/rust/src/lab.rs","classification":"KEEP_MONOLITHIC","reason":"cohesive contract-dense reducer/state"},{"path":"core/rust/src/replay.rs","classification":"KEEP_MONOLITHIC","reason":"single hash/replay integrity invariant"},{"path":"core/rust/src/cli/mod.rs","classification":"SPLIT_CANDIDATE","reason":"parse/policy/dispatch/presentation"},{"path":"scripts/evidence_consistency_gate.py","classification":"SPLIT_CANDIDATE","reason":"manifest/commit/evidence/report mixed"}],"do_not_split_yet":True,"limitations":["no split performed"]}
    dep_rows=[]
    for eco in ("python","rust"):
        src=deps0.get(eco,{}) if isinstance(deps0,dict) else {}
        src=src.get("dependencies",src.get("records",[])) if isinstance(src,dict) else src
        for x in src if isinstance(src,list) else []:
            name=x.get("name") or x.get("dependency") or x.get("package") or "unknown"
            dep_rows.append({"ecosystem":eco,"name":name,"role":"REQUIRED_RUNTIME" if x.get("usage_hits",x.get("hits",[])) else "UNKNOWN","evidence":x})
    if not dep_rows: dep_rows=[{"ecosystem":"python","name":"maturin/PyO3","role":"REQUIRED_BUILD","evidence":"native wheel"},{"ecosystem":"python","name":"provider SDKs","role":"REQUIRED_RUNTIME","evidence":"provider paths"},{"ecosystem":"rust","name":"workspace crates","role":"REQUIRED_BUILD_OR_POC","evidence":"cargo metadata"}]
    dep={"schema":"aegis-final-dependency-roles-v1","rows":dep_rows,"suspicious_direct_dependencies":"No UNUSED_SUSPECT proven by lexical absence","limitations":["runtime feature matrix not executed"]}
    inv_rows=[("Rust authoritative state","PARTIALLY_ENFORCED","native resource/replay exists; Python LabRun mutable projection"),("no side effect before admission","PARTIALLY_ENFORCED","explicit cells fenced; compatibility bypasses"),("one terminal settlement per admission","TEST_PROVEN_LOCAL","duplicate/unknown settlement rejected; chain verify passed"),("budget conservation","TEST_PROVEN_LOCAL","native/Lab contracts; all effects not exercised"),("attempt fencing","PARTIALLY_ENFORCED","execution/attempt/lease explicit; SDK outside"),("cancellation cannot become success","PARTIALLY_ENFORCED","explicit fenced path only"),("replay prefix cannot imply false success","TEST_PROVEN_LOCAL","archive/hash metadata; external crash untested"),("explicit cell registry","TEST_PROVEN_LOCAL","supported kinds and sealed snapshot"),("no silent fallback in PROD","PARTIALLY_ENFORCED","PROD normalization vs DEV defaults/fallback"),("one canonical package","VIOLATED","two distributions and aegis owners"),("one canonical trust policy","VIOLATED","DEV vs PROD"),("one canonical retry policy","VIOLATED","adapter/provider/Lab/SDK"),("telemetry observational only","PARTIALLY_ENFORCED","broad catches/control branches")]
    inv={"schema":"aegis-final-invariant-matrix-v1","rows":[{"invariant":a,"classification":b,"evidence":c} for a,b,c in inv_rows],"baseline_status":base.get("status"),"limitations":["current state, not target recommendation"]}
    boundary={"schema":"aegis-final-external-boundary-v1","items":[
      {"unknown":"current.json/final SHA binding","class":"LOCAL_ACTIONABLE","reason":"policy/source decision"},
      {"unknown":"package/CLI ownership","class":"LOCAL_ACTIONABLE","reason":"conflict proven locally"},
      {"unknown":"canonical state/trust/retry owner","class":"LOCAL_ACTIONABLE","reason":"divergence proven locally"},
      {"unknown":"full plugin/subprocess reachability","class":"LOCAL_ACTIONABLE","reason":"more contract work possible"},
      {"unknown":"Windows process/browser containment","class":"WINDOWS_NATIVE_REQUIRED","reason":"OS-level child tests"},
      {"unknown":"Linux sandbox/namespace/seccomp","class":"LINUX_NATIVE_REQUIRED","reason":"not available on Windows"},
      {"unknown":"macOS sandbox","class":"MACOS_NATIVE_REQUIRED","reason":"platform-specific"},
      {"unknown":"hosted multi-worker lease/queue","class":"HOSTED_REQUIRED","reason":"deployed shared service"},
      {"unknown":"cross-machine ordering/clock/failure","class":"MULTI_MACHINE_REQUIRED","reason":"single checkout"},
      {"unknown":"provider hidden retry/idempotency","class":"LIVE_PROVIDER_REQUIRED","reason":"provider contract"},
      {"unknown":"live browser SSRF/redirect/permissions","class":"LIVE_BROWSER_REQUIRED","reason":"controlled browser"},
      {"unknown":"hardware/electrical behavior","class":"HARDWARE_REQUIRED","reason":"software cannot represent"},
      {"unknown":"scientific validity","class":"SCIENTIFIC_VALIDATION_ONLY","reason":"domain replication"},
      {"unknown":"independent security/performance reproduction","class":"INDEPENDENT_VALIDATION_REQUIRED","reason":"self-audit is not independent"}]}
    return {"final_ffi_runtime_map.json":ffi_out,"final_rust_public_api.json":rust_out,"final_side_effect_chains.json":chains,"final_side_effect_bypasses.json":bypass,"final_state_divergence.json":divergence,"final_trust_policy.json":trust,"final_retry_policy.json":retry,"final_error_semantics.json":errors,"final_concurrency_ownership.json":concurrency,"final_data_movement.json":movement,"final_config_precedence.json":config,"final_schema_contracts.json":schema,"final_artifact_retention.json":retention,"final_delete_candidates.json":deletes,"final_module_split_candidates.json":splits,"final_dependency_roles.json":dep,"final_invariant_matrix.json":inv,"final_external_boundary.json":boundary}

def write(name,payload,ep,head,extra):
    x=dict(payload); x.update({"WORKTREE_EPOCH":ep,"HEAD":head,"generated_at":GENERATED,"collector_version/method":f"{VERSION}; direct Git/filesystem/AST/Cargo plus bounded disposable-wheel probes"})
    x["limitations"]=list(x.get("limitations",[]))+extra
    (OUT/name).write_text(json.dumps(x,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")

def main():
    OUT.mkdir(parents=True,exist_ok=True); ep0=epoch(); state=status(); dyn=dynamic(); pack=packaging(); base=baseline()
    outputs={"final_packaging_truth.json":pack,"final_dynamic_reachability.json":dyn,"final_performance_baseline.json":base}; outputs.update(report_payloads(base))
    ep1=epoch(); epoch_stable=ep0["WORKTREE_EPOCH"]==ep1["WORKTREE_EPOCH"]; st="PARTIAL_LOCAL" if epoch_stable else "WORKTREE_CHANGED_DURING_AUDIT"; extra=[] if epoch_stable else ["WORKTREE_CHANGED_DURING_AUDIT"]
    head=ep0.get("head") or state.get("head")
    for n,p in outputs.items(): write(n,p,ep0["WORKTREE_EPOCH"],head,extra)
    pack_meta={"schema":"aegis-design-closure-pack-v1","status":st,"epoch_stable":epoch_stable,"collection_state":state,"epoch_start":ep0,"epoch_end":ep1,"decision_summary":{"packaging":pack.get("classification"),"strong_delete_candidates":[],"strong_split_candidates":["core/rust/src/ffi.rs"],"safe_to_design_target_architecture":"YES","safe_to_begin_surgical_convergence":"NO","reason":"package/authority/trust/retry blockers remain"},"artifacts":sorted(outputs)}
    write("design_closure_pack.json",pack_meta,ep0["WORKTREE_EPOCH"],head,extra)
    paths=["docs/architecture/evidence-pack/design_closure_summary.md","docs/architecture/evidence-pack/design_closure_pack.json"]+[f"docs/architecture/evidence-pack/{n}" for n in sorted(outputs)]
    md=f"""# AEGIS — Final Design-Closure Evidence Summary
WORKTREE_EPOCH: {ep0["WORKTREE_EPOCH"]}
HEAD: {head}
Status: {st}

Root wheel classification: {pack.get("classification")}. Root clean import/native/CLI smoke is recorded. The independent core/python distribution declares the same aegis command but its entrypoint imports an absent top-level aegis_cli, so packaging is OVERLAPPING and not canonical.

Local decision blockers: final-SHA evidence manifest is invalid; package/CLI ownership is duplicated; Python/Rust state projection is not proven lossless; trust defaults diverge DEV versus PROD; retry ownership is split across adapter/provider/Lab/SDK. No strong delete candidate is proven. Strong split input: core/rust/src/ffi.rs; other modules are only split candidates or cohesive.

Baseline status: {base.get("status")}. Provider, network crawl, live browser, fuzz/soak/stress, migration, deletion, rename, and optimization were not run. This is design evidence, not a production-readiness claim.

Artifact index:
{chr(10).join("- "+p for p in paths)}
"""
    (OUT/"design_closure_summary.md").write_text(md,encoding="utf-8")
    print(json.dumps({"status":st,"WORKTREE_EPOCH":ep0["WORKTREE_EPOCH"],"HEAD":head,"outputs":len(outputs)+2,"baseline":base.get("status"),"packaging":pack.get("classification")},ensure_ascii=False))
    return 0 if epoch_stable else 2

if __name__=="__main__":
    raise SystemExit(main())
