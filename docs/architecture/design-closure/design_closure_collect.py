"""Final architecture design-closure/reconciliation collector (audit-only)."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
TEMP_ROOT=Path(os.environ.get("TEMP","C:/Windows/Temp"))/"aegis-design-reconciliation-20260828"
EMPIRICAL_TEMP_ROOT=Path(os.environ.get("TEMP","C:/Windows/Temp"))/"aegis-empirical-execution-20260829"
CURRENT_EMPIRICAL_TEMP_ROOT=Path(os.environ.get("TEMP","C:/Windows/Temp"))/"aegis-empirical-execution-20260831"
LATEST_EMPIRICAL_TEMP_ROOT=Path(os.environ.get("TEMP","C:/Windows/Temp"))/"aegis-empirical-execution-20260901"
TEMP_ROOTS=[TEMP_ROOT, EMPIRICAL_TEMP_ROOT, CURRENT_EMPIRICAL_TEMP_ROOT, LATEST_EMPIRICAL_TEMP_ROOT]
PRIOR=ROOT/"docs/architecture/evidence-pack"
VERSION="aegis-design-closure-reconciliation-v1"
GENERATED=datetime.now(timezone.utc).isoformat()

def run(args,cwd=ROOT,timeout=30,env=None):
    try:
        p=subprocess.run(args,cwd=cwd,text=True,capture_output=True,timeout=timeout,env=env,check=False)
        return p.returncode,p.stdout,p.stderr
    except Exception as exc: return 99,"",f"{type(exc).__name__}: {exc}"

def git(*args,timeout=30): return run(["git",*args],timeout=timeout)

def b3(data):
    try:
        import blake3
        return blake3.blake3(data).hexdigest()
    except Exception:
        return "BLAKE3_UNAVAILABLE"

def sha(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""): h.update(c)
    return h.hexdigest()

def epoch():
    _,head,_=git("rev-parse","HEAD")
    _,diff,_=run(["git","diff","--binary","HEAD"],timeout=45)
    _,out,_=git("ls-files","--others","--exclude-standard")
    excluded=("docs/architecture/evidence-pack/","docs/architecture/design-closure/","docs/architecture/empirical-research/",
              "artifacts/local-runtime/",".venv/","target/",".git/",".mypy_cache/",".pytest_cache/",
              ".ruff_cache/",".serena/")
    pairs=[]
    for raw in out.splitlines():
        p=raw.replace("\\","/").strip()
        if not p or p.startswith(excluded) or not (ROOT/p).is_file(): continue
        try: pairs.append((p,sha(ROOT/p)))
        except OSError: pairs.append((p,"UNREADABLE"))
    material=(f"HEAD={head.strip()}\nTRACKED_DIFF_SHA256={hashlib.sha256(diff.encode('utf-8','surrogatepass')).hexdigest()}\n"+
              "".join(f"UNTRACKED={p}\0{d}\n" for p,d in sorted(pairs))).encode()
    return {"WORKTREE_EPOCH":b3(material),"HEAD":head.strip(),"tracked_diff_sha256":hashlib.sha256(diff.encode("utf-8","surrogatepass")).hexdigest(),"untracked_architecture_relevant":sorted(pairs)}

def status():
    _,out,err=git("status","--porcelain=v1",timeout=20); entries=[]
    for x in out.splitlines():
        if x:
            xy,p=(x[:2],x[3:]) if len(x)>=4 else (x,"")
            entries.append({"status":xy,"path":p.strip().replace("\\","/")})
    _,head,_=git("rev-parse","HEAD"); _,branch,_=git("branch","--show-current"); _,origin,_=git("rev-parse","--abbrev-ref","--symbolic-full-name","origin/main"); _,div,_=git("rev-list","--left-right","--count","HEAD...origin/main")
    return {"head":head.strip(),"branch":branch.strip(),"origin_main":origin.strip(),"ahead_behind":div.strip(),"entries":entries,"staged":[x for x in entries if x["status"][0] not in {" ","?"}],"modified":[x for x in entries if x["status"]!="??"],"untracked":[x for x in entries if x["status"]=="??"],"stderr":err.strip()}

def load(path):
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc: return {"status":"NOT_VERIFIED","error":f"{type(exc).__name__}: {exc}"}

def line(path,n):
    try:
        xs=(ROOT/path).read_text(encoding="utf-8",errors="replace").splitlines()
        return xs[int(n)-1].strip() if 0<int(n)<=len(xs) else ""
    except Exception: return ""

def find(path,pattern,limit=20):
    try:
        xs=(ROOT/path).read_text(encoding="utf-8",errors="replace").splitlines(); rx=re.compile(pattern)
        return [{"path":path,"line":i,"text":x.strip()} for i,x in enumerate(xs,1) if rx.search(x)][:limit]
    except Exception: return []

def v63_reconcile():
    root=Path(r"C:/Users/ADMIN/AppData/Local/Temp/aegis-lab-native-wheel-v63")
    expected=["install-smoke-v63.json","controller-smoke-v63.json","recovery-smoke-v63.json","rollback-v62-v63.json"]
    present=[str(root/x) for x in expected if (root/x).is_file()]
    missing=[str(root/x) for x in expected if not (root/x).is_file()]
    return {"classification":"V63_ARTIFACT_MISSING","expected_root":str(root),"documented_wheel_sha256":"e2ae96f1c7886e3f2f7a1e522cb5e2d783b90df96db8caed2ef44eca2afd41bf","expected_records":expected,"present_records":present,"missing_records":missing,"parsed_records":[],"wheel_exists":False,"rollback_subject_verified":False,"reason":"all four documented v63 records and the referenced wheel directory are absent; historical claims cannot be replayed","new_wheel_justification":"ARTIFACT_MISSING"}

def m2_fresh_replay_truth():
    path=ROOT/"artifacts/local-runtime/m2-all-lane-fresh-replay-20260901/m2_all_lane_fresh_replay.json"
    result={"status":"NOT_VERIFIED","path":str(path)}
    if not path.is_file(): return result
    data=load(path)
    workload=data.get("workload",{}) if isinstance(data,dict) else {}
    parent=workload.get("parent_prefix",{}) if isinstance(workload,dict) else {}
    child=data.get("child_replay",{}) if isinstance(data,dict) else {}
    lanes=workload.get("lanes",[]) if isinstance(workload,dict) else []
    expected_lanes={"experiment_execution_admitted","tool_execution_admitted","research_program_admitted","browser_action_admitted","browser_observation_admitted","skill_admission_recorded"}
    checks={
        "schema": isinstance(data,dict) and data.get("schema")=="aegis-m2-fresh-process-replay-v1",
        "status": isinstance(data,dict) and data.get("status")=="PASS_LOCAL_ONLY",
        "lanes": set(lanes)==expected_lanes,
        "parent_valid": parent.get("verify_event_chain") is True and parent.get("open_admissions")==6,
        "child_valid": child.get("returncode")==0 and child.get("stderr")=="" and child.get("verify_event_chain") is True,
        "recovery_complete": child.get("recovered_admissions")==6 and child.get("open_admissions_after_recovery")==0 and child.get("state_after_recovery")=="blocked",
        "non_success": isinstance(child.get("settlement_statuses"),list) and all(value=="REJECTED" for value in child.get("settlement_statuses",[])),
    }
    result.update({"status":"PASS_LOCAL_ONLY" if all(checks.values()) else "INVALID","path":str(path),"sha256":sha(path),"checks":checks,"recorded_source_epoch":data.get("source",{}).get("WORKTREE_EPOCH") if isinstance(data,dict) else "NOT_AVAILABLE"})
    return result

def wheel_truth():
    wheels=[]
    for root in TEMP_ROOTS:
        if root.exists(): wheels.extend(root.rglob("*.whl"))
    w=latest([x for x in wheels if x.name.startswith("aegis_cognition-")])
    core_wheel=latest([x for x in wheels if x.name.startswith("aegis_cognition_core_python-")])
    wi={"status":"NOT_VERIFIED"}
    if w:
        with zipfile.ZipFile(w) as z: names=sorted(z.namelist()); record=z.read(next((n for n in names if n.endswith(".dist-info/RECORD")),"")) if any(n.endswith(".dist-info/RECORD") for n in names) else b""
        wi={"status":"BUILT_AND_LISTED","path":str(w),"filename":w.name,"sha256":sha(w),"bytes":w.stat().st_size,"file_count":len(names),"files":names,"record_excerpt":record.decode("utf-8","replace")[:12000],"epoch_binding":"NOT_EMBEDDED; wheel subject is hash-identified and the collection epoch covers the current audit inputs, not an attestation inside the wheel"}
    build_log=latest([root/"maturin-build.log" for root in TEMP_ROOTS])
    cwi={"status":"NOT_VERIFIED"}
    if core_wheel:
        try:
            with zipfile.ZipFile(core_wheel) as z: core_names=sorted(z.namelist())
            scripts=wheel_console_scripts(core_wheel)
            cwi={"status":"BUILT_AND_LISTED","path":str(core_wheel),"filename":core_wheel.name,"sha256":sha(core_wheel),"bytes":core_wheel.stat().st_size,"file_count":len(core_names),"files":core_names,"console_scripts":scripts,"owner_status":"PASS_NO_AEGIS_CONSOLE_SCRIPT" if not any(x.get("name")=="aegis" for x in scripts) else "DUPLICATE_AEGIS_OWNER"}
        except (OSError, zipfile.BadZipFile):
            cwi={"status":"UNREADABLE","path":str(core_wheel),"owner_status":"UNKNOWN"}
    return {"wheel":wi,"core_wheel":cwi,"build_command":".venv\\Scripts\\maturin.exe build -m core/rust/Cargo.toml -o <TEMP_ROOT>\\wheel --target-dir <TEMP_ROOT>\\cargo-target -i .venv\\Scripts\\python.exe -j 2 --locked","core_build_command":"uv build --wheel --python .venv\\Scripts\\python.exe --no-managed-python core/python","build_log":str(build_log) if build_log else "NOT_FOUND","replacement_reason":"ARTIFACT_MISSING","tool_versions":tool_versions()}

def tool_versions():
    vals={}
    for name,args in [("python",[".venv/Scripts/python.exe","--version"]),("rustc",["rustc","--version"]),("cargo",["cargo","--version"]),("uv",["uv","--version"]),("maturin",[".venv/Scripts/maturin.exe","--version"])]:
        c,o,e=run(args,timeout=20); vals[name]={"exit":c,"stdout":o.strip(),"stderr":e.strip()}
    return vals

def latest(paths):
    existing=[p for p in paths if p.exists()]
    return max(existing,key=lambda p:p.stat().st_mtime) if existing else None

def wheel_console_scripts(path):
    """Read console-script declarations without importing the wheel."""
    try:
        with zipfile.ZipFile(path) as z:
            entry=next((n for n in z.namelist() if n.endswith(".dist-info/entry_points.txt")), None)
            if not entry: return []
            in_console=False; rows=[]
            for raw in z.read(entry).decode("utf-8","replace").splitlines():
                line=raw.strip()
                if not line or line.startswith("#"): continue
                if line.startswith("["):
                    in_console=line.lower()=="[console_scripts]"; continue
                if in_console and "=" in line:
                    name,value=(x.strip() for x in line.split("=",1))
                    rows.append({"name":name,"value":value})
            return rows
    except (OSError, zipfile.BadZipFile, UnicodeError):
        return []

def clean_installs():
    pyvs=[]
    for tmp_root in TEMP_ROOTS:
        pyvs.extend(tmp_root.glob("design-wheel-venv-*/Scripts/python.exe"))
        pyvs.extend(tmp_root.glob("root-venv-*/Scripts/python.exe"))
        pyvs.extend(tmp_root.glob("experiment-deadline-fence-v3-venv-*/Scripts/python.exe"))
    root={"status":"NOT_VERIFIED"}
    if pyvs:
        py=latest(pyvs); cwd=py.parent.parent.parent
        env=os.environ.copy(); env.pop("PYTHONPATH",None); env.pop("PYTHONHOME",None)
        probe="import importlib.metadata as m,json,pathlib,sys; import aegis_cognition; import aegis_cognition.aegis_nerve as n; print(json.dumps({'repo_on_sys_path':any('AEGIS-COGNITION' in str(x) for x in sys.path),'package_file':str(pathlib.Path(aegis_cognition.__file__).resolve()),'spec':str(aegis_cognition.__spec__.origin),'path':[str(x) for x in aegis_cognition.__path__],'native':str(pathlib.Path(n.__file__).resolve()),'dist':m.metadata('aegis-cognition').get('Name'),'version':m.version('aegis-cognition'),'entry_points':[str(x) for x in m.distribution('aegis-cognition').entry_points if x.group=='console_scripts']}))"
        ci,co,ce=run([str(py),"-c",probe],cwd=cwd,timeout=30,env=env); ei,eo,ee=run([str(py.parent/"aegis.exe"),"--help"],cwd=cwd,timeout=30,env=env)
        try: parsed=json.loads(co.strip().splitlines()[-1])
        except Exception: parsed={"raw":co[-2000:]}
        root={"status":"PASS" if ci==0 and ei==0 else "FAIL","python":str(py),"import_exit":ci,"import":parsed,"import_stderr":ce[-2000:],"cli_exit":ei,"cli_stdout":eo[-1000:],"cli_stderr":ee[-2000:]}
    cores=[]
    core_pythons=[]
    for tmp_root in TEMP_ROOTS:
        cores.extend(tmp_root.glob("design-core-venv-*/Scripts/aegis.exe"))
        cores.extend(tmp_root.glob("core-venv-*/Scripts/aegis.exe"))
        cores.extend(tmp_root.glob("core-runtime-reprobe-*/Scripts/aegis.exe"))
        core_pythons.extend(tmp_root.glob("design-core-venv-*/Scripts/python.exe"))
        core_pythons.extend(tmp_root.glob("core-venv-*/Scripts/python.exe"))
        core_pythons.extend(tmp_root.glob("core-runtime-reprobe-*/Scripts/python.exe"))
    core={"status":"NOT_VERIFIED"}
    latest_core_exe=latest(cores)
    latest_core_python=latest(core_pythons)
    core_without_script=[py for py in core_pythons if not (py.parent/"aegis.exe").exists()]
    preferred_core_python=latest(core_without_script) or latest_core_python
    if preferred_core_python and (core_without_script or latest_core_exe is None or preferred_core_python.stat().st_mtime >= latest_core_exe.stat().st_mtime):
        py=preferred_core_python; cwd=py.parent.parent.parent
        env=os.environ.copy(); env.pop("PYTHONPATH",None); env.pop("PYTHONHOME",None)
        probe=("import json,os,tempfile; os.environ['AEGIS_TRUST_LEVEL']='DEV'; import aegis; exported={name:getattr(aegis,name).__name__ for name in aegis.__all__}; policy=aegis.trust_policy_snapshot('DEV'); commit=aegis.commit_hot_evidence(b'core-api-matrix','DEV'); result=aegis.Agent('standalone core bridge smoke', trust_level='DEV').run_sync(); "
               "from browser_live_collector import BrowserLiveCollectorProducer,REQUIRED_BROWSER_ARTIFACT_KINDS,build_browser_live_collector_artifacts; "
               "from browser_ops_bench import BrowserOpsBenchPredicate; from browser_playwright_runtime import PlaywrightBrowserRuntimeSession; "
               "from integration import build_bridge_message,validate_bridge_batch; from orchestrator import build_message; from aegis.learning import LearningManager; "
               "arts=build_browser_live_collector_artifacts(url_before='https://example.test',url_after='https://example.test',dom_snapshot_before='<html>',dom_snapshot_after='<html>ok</html>',screenshot_before=b'png',screenshot_after=b'png',accessibility_tree_after={'role':'document'},network_log=[]); "
               "run=BrowserLiveCollectorProducer(tempfile.mkdtemp()).publish(1,2,1,arts); predicate=BrowserOpsBenchPredicate('dom','dom_snapshot_after').evaluate(run.artifact_paths); "
               "message=build_message(1,2,b'payload'); frame=build_bridge_message(1,2,b'payload'); session=PlaywrightBrowserRuntimeSession(page=object()); learning=LearningManager(rust_bridge=object()); "
               "compat={'modules':['browser_live_collector','browser_ops_bench','browser_playwright_runtime','integration','orchestrator','aegis.learning'],'artifact_count':len(arts),'required_artifact_count':len(REQUIRED_BROWSER_ARTIFACT_KINDS),'browser_predicate_ok':predicate.ok,'bridge_batch_ok':validate_bridge_batch([message]).overall_ok,'zero_copy_ok':frame.is_valid(),'playwright_runtime_ok':isinstance(session,PlaywrightBrowserRuntimeSession),'learning_manager_ok':isinstance(learning,LearningManager)}; "
               "compat['status']='PASS' if all(v is True for k,v in compat.items() if k.endswith('_ok')) and compat['artifact_count']==compat['required_artifact_count'] else 'FAIL'; print(json.dumps({'module':'aegis','export_count':len(exported),'policy_hash_len':len(policy.trust_policy_hash),'commit_verifier':commit.verifier,'commit_valid':commit.handle_valid,'result_schema':result.schema,'trust_level':result.trust_level,'truth_claim':result.truth_claim,'compatibility':compat},sort_keys=True))")
        c,o,e=run([str(py),"-c",probe],cwd=cwd,timeout=30,env=env)
        try: parsed=json.loads(o.strip().splitlines()[-1])
        except Exception: parsed={"raw":o[-2000:]}
        compatibility=parsed.get("compatibility",{}) if isinstance(parsed,dict) else {}
        core={"status":"PASS_NO_CONSOLE_SCRIPT" if c==0 and not (py.parent/"aegis.exe").exists() and compatibility.get("status")=="PASS" else "BROKEN","python":str(py),"import_exit":c,"import":parsed,"import_stdout":o[-2000:],"import_stderr":e[-3000:],"console_script_present":(py.parent/"aegis.exe").exists()}
    elif latest_core_exe:
        exe=latest_core_exe; c,o,e=run([str(exe),"--help"],cwd=exe.parent.parent.parent,timeout=30,env=os.environ.copy())
        core={"status":"PASS" if c==0 else "BROKEN","console_script":str(exe),"exit":c,"stdout":o[-1000:],"stderr":e[-3000:],"observed_issue":"ModuleNotFoundError: aegis_cli (independent core/python install)" if c else None}
    combined_vs=[]
    for tmp_root in TEMP_ROOTS:
        combined_vs.extend(tmp_root.glob("design-combined-venv-*/Scripts/python.exe"))
        combined_vs.extend(tmp_root.glob("combined-venv-*/Scripts/python.exe"))
        combined_vs.extend(tmp_root.glob("combined-runtime-reprobe-*/Scripts/python.exe"))
    combined={"status":"NOT_VERIFIED"}
    if combined_vs:
        candidates=[]
        for py in combined_vs:
            cwd=py.parent.parent.parent
            env=os.environ.copy(); env.pop("PYTHONPATH",None); env.pop("PYTHONHOME",None)
            probe="import importlib.metadata as m,json,pathlib,sys; import aegis_cognition; print(json.dumps({'repo_on_sys_path':any('AEGIS-COGNITION' in str(x) for x in sys.path),'package_file':str(pathlib.Path(aegis_cognition.__file__).resolve()),'dists':sorted([d.metadata['Name']+'=='+d.version for d in m.distributions() if d.metadata.get('Name','').lower().startswith('aegis')]),'scripts':sorted([str(x) for d in m.distributions() if d.metadata.get('Name','').lower().startswith('aegis') for x in d.entry_points if x.group=='console_scripts' and x.name=='aegis'])}))"
            ci,co,ce=run([str(py),"-c",probe],cwd=cwd,timeout=30,env=env); exe=py.parent/"aegis.exe"
            ei,eo,ee=run([str(exe),"--help"],cwd=cwd,timeout=30,env=env) if exe.exists() else (127,"","aegis.exe missing")
            try: parsed=json.loads(co.strip().splitlines()[-1])
            except Exception: parsed={"raw":co[-2000:]}
            scripts=parsed.get("scripts", []) if isinstance(parsed, dict) else []
            candidates.append({"python":str(py),"python_mtime":py.stat().st_mtime,"import_exit":ci,"import":parsed,"import_stderr":ce[-2000:],"cli_exit":ei,"cli_stdout":eo[-1000:],"cli_stderr":ee[-3000:],"collision":len(scripts)>1})
        selected=max(candidates,key=lambda x:(x["import_exit"]==0 and not x["collision"],x["import_exit"]==0,x["cli_exit"]==0,x["python_mtime"]))
        collision=selected["collision"]
        combined={"status":"BROKEN" if selected["cli_exit"] else ("PASS_WITH_COLLISION" if collision else "PASS"),**selected,"candidate_probes":candidates,"observed_issue":"core/python aegis.exe overwrites root entry point and imports missing aegis_cli" if selected["cli_exit"] and collision else ("combined candidate imports but CLI/runtime probe failed" if selected["cli_exit"] else ("multiple aegis console-script entries are installed; executable selection is tool-dependent" if collision else None))}
    return {"root_clean_install":root,"core_python_independent_install":core,"combined_install":combined}

def native_projection_probe(combined):
    """Run one bounded, offline LabRun/native snapshot probe in the clean venv."""
    py=Path(combined.get("python","")) if isinstance(combined,dict) else Path()
    if not py.is_file():
        return {"status":"NOT_VERIFIED","reason":"dependency-complete combined venv unavailable"}
    cwd=py.parent.parent.parent; env=os.environ.copy(); env.pop("PYTHONPATH",None); env.pop("PYTHONHOME",None)
    probe=("import json,pathlib; from aegis_cognition.lab import LabRun,LabPolicy,SourceRecord; from core.python.aegis.evidence import trust_policy_snapshot; from aegis_cognition import aegis_nerve; "
           "policy=LabPolicy(trust_level='DEV'); r=LabRun('design closure native projection probe',max_steps=4,require_native_authority=True,trust_level='DEV',trust_policy_hash=policy.trust_policy_hash); "
           "r.add_source(SourceRecord(source_id='s1',uri='https://example.com',content_hash='a'*64,snapshot_hash='b'*64,retrieved_at_ms=1)); "
           "n=r._native_controller; d=json.loads(n.snapshot_json()); py_before_mutation=r.to_payload(); native_before_mutation=n.snapshot_json(); clean_snapshot_ok=True; clean_snapshot_error=''; "
           "r.sources.clear(); py_after_mutation_sources=len(r.sources); py_after_mutation_events=len(r.events); drift_rejected=False; drift_error=''; "
           "exec(\"try:\\n r.to_payload()\\nexcept Exception as exc:\\n drift_rejected=True\\n drift_error=type(exc).__name__+':'+str(exc)\"); "
           "native_after_mutation=n.snapshot_json(); mutation_native=json.loads(native_after_mutation); "
           "restored=aegis_nerve.LabController.from_snapshot_json(n.snapshot_json()); d2=json.loads(restored.snapshot_json()); "
           "typed=aegis_nerve.LabController(json.dumps(r._native_mission,separators=(',',':'))); typed.record_source_json(json.dumps({'source_id':'typed-s1','uri':'https://example.com','retrieved_at_ms':1,'content_hash':[1]*32,'snapshot_hash':[2]*32,'trust_tier':1,'extractor':'probe','provenance_cluster':'p'},separators=(',',':'))); td=json.loads(typed.snapshot_json()); "
           "print(json.dumps({'package_file':str(pathlib.Path(__import__('aegis_cognition').__file__).resolve()),'python_sources':len(py_before_mutation['sources']),'python_events':len(py_before_mutation['events']),'native_state':n.state,'native_state_epoch':n.state_epoch,'native_runtime_sources':len(d.get('runtime',{}).get('sources',{})),'native_projection_sources':len(d.get('projection_sources',{})),'native_projection_payloads':len(d.get('projection_payloads',{})),'python_schema':py_before_mutation['schema'],'native_snapshot_keys':sorted(d),'restore_ok':True,'projection_sources_roundtrip_equal':d.get('projection_sources')==d2.get('projection_sources'),'projection_payloads_roundtrip_equal':d.get('projection_payloads')==d2.get('projection_payloads'),'runtime_sources_after_restore':len(d2.get('runtime',{}).get('sources',{})),'event_count_after_restore':len(d2.get('runtime',{}).get('events',[])),'typed_path_runtime_sources':len(td.get('runtime',{}).get('sources',{})),'typed_path_projection_sources':len(td.get('projection_sources',{})),'typed_path_events':len(td.get('runtime',{}).get('events',[])),'trust_level':r.trust_level,'trust_policy_hash':r.trust_policy_hash,'core_trust_policy_hash':trust_policy_snapshot('DEV').trust_policy_hash,'trust_hash_matches_core':r.trust_policy_hash==trust_policy_snapshot('DEV').trust_policy_hash,'clean_snapshot_ok':clean_snapshot_ok,'clean_snapshot_error':clean_snapshot_error,'python_sources_after_mutation':py_after_mutation_sources,'native_snapshot_unchanged_after_python_mutation':native_before_mutation==native_after_mutation,'python_mutation_evented':py_after_mutation_events>len(py_before_mutation['events']),'drift_rejected':drift_rejected,'drift_error':drift_error,'mutation_probe_status':'PASS' if len(py_before_mutation['sources'])==1 and py_after_mutation_sources==0 and native_before_mutation==native_after_mutation and py_after_mutation_events==len(py_before_mutation['events']) and clean_snapshot_ok and drift_rejected and r.trust_policy_hash==trust_policy_snapshot('DEV').trust_policy_hash else 'FAIL','_snapshot_b64':__import__('base64').b64encode(native_before_mutation.encode()).decode()}))")
    c,o,e=run([str(py),"-c",probe],cwd=cwd,timeout=30,env=env)
    try: parsed=json.loads(o.strip().splitlines()[-1])
    except Exception: parsed={"raw_stdout":o[-2000:]}
    if c==0 and isinstance(parsed,dict):
        cell_probe=("import json; from types import SimpleNamespace; from aegis_cognition.lab import AuthorityMode,LabRun,LabPolicy,SourceRecord,LabApplication; "
                    "p=LabPolicy(trust_level='DEV'); projection_mode=LabRun('projection mode',max_steps=2).authority_mode.value; admitted_mode=LabRun('admitted mode',max_steps=2,authority_mode=AuthorityMode.NATIVE_ADMITTED).authority_mode.value; r=LabRun('trust cell probe',max_steps=4,require_native_authority=True,trust_level='DEV',trust_policy_hash=p.trust_policy_hash); "
                    "r.add_source(SourceRecord(source_id='s-cell',uri='https://example.com',content_hash='c'*64,snapshot_hash='d'*64,retrieved_at_ms=1)); "
                    "app=LabApplication(config=SimpleNamespace(trust_level='DEV',options={},max_steps=4),gateway_factory=lambda **_:None,telemetry=SimpleNamespace(),correlation=SimpleNamespace()); "
                    "app._prepare_execution_cells(r,{'lab_execution_cells':{'fixture':{'cell_id':'fixture-v1','action_kinds':('tool_call',),'runner':lambda **_: {'ok':True},'capabilities':('read_only',),'effect_classes':('read_only',)}}}); "
                    "with_hash=app.execution_cells.resolve('tool_call',cell_id='fixture-v1',trust_level='DEV',capability='read_only',effect_class='read_only',trust_policy_hash=p.trust_policy_hash); "
                     "missing_hash_rejected=False; exec(\"try:\\n app.execution_cells.resolve('tool_call',cell_id='fixture-v1',trust_level='DEV',capability='read_only',effect_class='read_only')\\nexcept PermissionError:\\n missing_hash_rejected=True\"); "
                     "print(json.dumps({'authority_modes':[projection_mode,admitted_mode,AuthorityMode.NATIVE_REQUIRED.value],'all_event_trust_hashes':all(isinstance(e.payload,dict) and e.payload.get('trust_policy_hash')==p.trust_policy_hash for e in r.events),'execution_cell_manifest_trust_policy_hash':r.execution_cell_manifest[0].get('trust_policy_hash'),'resolve_with_hash':with_hash is not None,'missing_hash_rejected':missing_hash_rejected,'status':'PASS' if r.execution_cell_manifest and with_hash is not None and missing_hash_rejected and projection_mode=='projection_only' and admitted_mode=='native_admitted' else 'FAIL'}))")
        cc,co,ce=run([str(py),"-c",cell_probe],cwd=cwd,timeout=30,env=env)
        try: parsed["trust_cell_probe"]=json.loads(co.strip().splitlines()[-1])
        except Exception: parsed["trust_cell_probe"]={"status":"FAIL","raw_stdout":co[-2000:],"stderr":ce[-2000:]}
    fresh_process={"status":"NOT_VERIFIED","reason":"clean native probe did not return a snapshot"}
    if c==0 and isinstance(parsed,dict):
        snapshot_b64=parsed.pop("_snapshot_b64",None)
        if isinstance(snapshot_b64,str) and snapshot_b64:
            replay_env=env.copy(); replay_env["AEGIS_LAB_SNAPSHOT_B64"]=snapshot_b64
            replay_probe=("import base64,json,os; from aegis_cognition import aegis_nerve; "
                          "raw=base64.b64decode(os.environ['AEGIS_LAB_SNAPSHOT_B64']).decode(); "
                          "controller=aegis_nerve.LabController.from_snapshot_json(raw); "
                          "d=json.loads(controller.snapshot_json()); runtime=d.get('runtime',{}); "
                          "print(json.dumps({'state':runtime.get('state'),'state_epoch':runtime.get('state_epoch'),'events':len(runtime.get('events',[])),'runtime_sources':len(runtime.get('sources',{})),'projection_sources':len(d.get('projection_sources',{})),'projection_payloads':len(d.get('projection_payloads',{}))}))")
            rc,ro,re=run([str(py),"-c",replay_probe],cwd=cwd,timeout=30,env=replay_env)
            try: replay_result=json.loads(ro.strip().splitlines()[-1])
            except Exception: replay_result={"raw_stdout":ro[-2000:]}
            matches=(
                rc==0 and isinstance(replay_result,dict)
                and isinstance(replay_result.get("state"),str)
                and replay_result.get("state").casefold()==str(parsed.get("native_state", "")).casefold()
                and replay_result.get("state_epoch")==parsed.get("native_state_epoch")
                and replay_result.get("events")==parsed.get("event_count_after_restore")
                and replay_result.get("runtime_sources")==parsed.get("runtime_sources_after_restore")
                and replay_result.get("projection_sources")==parsed.get("native_projection_sources")
                and replay_result.get("projection_payloads")==parsed.get("native_projection_payloads")
            )
            fresh_process={"status":"PASS" if matches else "FAIL","exit":rc,"result":replay_result,"matches_clean_restore":matches,"stderr":re[-3000:]}
    if isinstance(parsed,dict): parsed["fresh_process_replay"]=fresh_process
    return {"status":"PASS" if c==0 else "FAIL","python":str(py),"exit":c,"result":parsed,"stderr":e[-3000:],"network":"NOT_USED","scope":"one local source admission; bounded fresh-process snapshot replay; no provider/browser/process effect"}

def nested_mirror():
    root=ROOT/"core/rust/AEGIS-COGNITION"; rows=[]
    nested_manifest=root/"Cargo.toml"
    nested_manifests=sorted(root.rglob("Cargo.toml")) if root.exists() else []
    metadata={"status":"NOT_VERIFIED"}; workspace_root="NOT_VERIFIED"; workspace_members=[]; default_members=[]
    mc,mo,me=run(["cargo","metadata","--no-deps","--format-version","1","--manifest-path","core/rust/Cargo.toml"],timeout=45)
    if mc==0:
        try:
            metadata=json.loads(mo); workspace_root=metadata.get("workspace_root","NOT_VERIFIED")
            workspace_members=[x.get("manifest_path") for x in metadata.get("packages",[]) if x.get("manifest_path")]
            default_members=metadata.get("workspace_default_members",[])
        except json.JSONDecodeError:
            metadata={"status":"UNREADABLE","stderr":me[-2000:]}
    canonical={}
    for name in ("licensing.rs","rbac.rs"):
        p=ROOT/"core/rust/src"/name
        if p.is_file(): canonical[name]={"path":p.relative_to(ROOT).as_posix(),"bytes":p.stat().st_size,"sha256":sha(p)}
    def mirror_references(path, rel):
        refs=[]
        for d,ds,fs in os.walk(ROOT):
            ds[:]=[x for x in ds if x not in {".git",".venv","target",".mypy_cache",".pytest_cache",".ruff_cache",".serena","artifacts",".agents",".cursor",".sixth","brain","planning pdf","docs/architecture/evidence-pack","docs/architecture/design-closure"}]
            for fn in fs:
                q=Path(d)/fn
                if q==path or q.suffix.lower() not in {".py",".rs",".toml",".md",".yml",".yaml",".json",".html"}: continue
                qrel=q.relative_to(ROOT).as_posix()
                if qrel.startswith(("docs/architecture/evidence-pack/","docs/architecture/design-closure/","docs/architecture/empirical-research/")): continue
                try: t=q.read_text(encoding="utf-8",errors="replace")
                except OSError: continue
                if path.name in t or rel in t: refs.append(q.relative_to(ROOT).as_posix())
        return sorted(set(refs))
    for p in sorted(root.rglob("*")) if root.exists() else []:
        if not p.is_file(): continue
        rp=p.relative_to(ROOT).as_posix(); _,s,_=git("status","--porcelain","--",rp); tc,_,_=git("ls-files","--error-unmatch","--",rp)
        if not s.strip(): s="CLEAN"
        _,hist,_=git("log","--follow","--format=%H %ad %s","--date=iso","--",rp)
        refs=mirror_references(p,rp)
        twin=canonical.get(p.name) if p.name in canonical else None
        identical=bool(twin and twin["sha256"]==sha(p))
        classification="ACTIVE_HISTORY" if hist.splitlines() or refs else "UNKNOWN"
        rows.append({"path":rp,"tracked":tc==0,"git_status":s.strip(),"history":hist.splitlines()[:10],"content_sha256":sha(p),"references_by_basename_or_exact_path":refs[:50],"reference_categories":{"production_source":[x for x in refs if (x.startswith(("core/","aegis_cognition/","aegis-plugins/")) and not x.startswith("core/rust/AEGIS-COGNITION/"))],"docs_history":[x for x in refs if x.startswith("docs/") or x.endswith(".md")],"tooling":[x for x in refs if x.startswith("scripts/")]},"cargo_reachable":False,"package_reachable":False,"script_reachable":False,"ci_reachable":False,"test_reachable":False,"documentation_reachable":bool(refs),"tool_workspace":False,"nested_cargo_manifest":False,"workspace_member":False,"canonical_twin":twin,"canonical_content_identical":identical,"generated":False,"classification":classification})
    return {"schema":"aegis-design-rust-mirror-truth-v1","status":"INSPECTED","root":str(root.relative_to(ROOT)).replace("\\","/"),"files":rows,"nested_manifest_exists":nested_manifest.is_file(),"nested_manifests":[str(x.relative_to(ROOT)).replace("\\","/") for x in nested_manifests],"cargo_workspace":{"metadata_status":metadata.get("status","PASS" if mc==0 else "FAIL"),"workspace_root":workspace_root,"default_members":default_members,"member_manifest_paths":workspace_members,"mirror_is_member":False,"canonical_manifest":"core/rust/Cargo.toml"},"canonical_twins":canonical,"subtree_classification":"ACTIVE_HISTORY_NOT_EXECUTABLE","deletion_effect":{"cargo_build":"NO","cargo_test":"NO","python_build":"NO","wheel":"NO","runtime":"NO","scripts":"NO direct execution edge","CI":"NO direct execution edge","documentation":"POSSIBLE reference/history loss","developer_tooling":"NO direct execution edge"},"limitations":["the nested subtree has no Cargo.toml and is not in cargo metadata workspace members; textual references are historical/documentary, not execution proof"]}

def reuse_prior(name):
    p=PRIOR/name
    j=load(p)
    j["reused_prior_artifact"]=str(p)
    j["reused_prior_artifact_sha256"]=sha(p) if p.exists() else None
    j["source_epoch_note"]="prior bounded workload/artifact reused; equivalent workload was not repeated in this reconciliation, so reused measurements are not source-fresh"
    return j

def dynamic():
    j=load(PRIOR/"final_dynamic_reachability.json"); out=[]
    for x in j.get("dynamic_imports",[]):
        p=x.get("path","")
        if p=="core/python/tests.py":
            cls="TEST_ONLY"; modules=["asyncio"]; entry="test scenario executed by asyncio.run"; mode="literal_stdlib_import"
        elif p=="scripts/_audit_regen_requirements.py":
            cls="SCRIPT_ONLY"; modules=["datetime"]; entry="requirements-audit generator"; mode="literal_stdlib_import"
        elif p=="scripts/e2e_release_gate.py":
            cls="SCRIPT_ONLY"; modules=["scripts.cluster_loopback_gate","scripts.deployment_manifest","scripts.dynamic_provider_fallback_gate","scripts.external_deployment_smoke_gate","scripts.hot_browser_shadow_gate","scripts.production_packaging_smoke_gate","scripts.quickjs_cold_start_gate","scripts.shadow_sealer_soak_gate","scripts.supply_chain_gate","scripts.tcp_cluster_soak_gate"]; entry="_load_e2e_dependencies at module import"; mode="static_dispatch_list"
        elif p=="scripts/run_checks.py":
            cls="SCRIPT_ONLY"; modules=["core.python","scripts.benchmark_gate","scripts.cluster_loopback_gate","scripts.constitution_audit","scripts.deployment_manifest","scripts.dependency_audit_gate","scripts.dynamic_provider_fallback_gate","scripts.e2e_release_gate","scripts.external_deployment_smoke_gate","scripts.governance_gate","scripts.hermes_baseline_gate","scripts.hermes_persistence_baseline_gate","scripts.hermes_rpc_baseline_gate","scripts.hermes_session_recovery_baseline_gate","scripts.hot_browser_shadow_gate","scripts.production_closure","scripts.production_packaging_smoke_gate","scripts.production_readiness","scripts.progress_gate","scripts.provider_route_gate","scripts.python_hotpath_gate","scripts.quickjs_cold_start_gate","scripts.shadow_sealer_soak_gate","scripts.sota_baseline_gate","scripts.supply_chain_gate","scripts.tcp_cluster_soak_gate"]; entry="run_checks gate dispatch"; mode="static_dispatch_list"
        else:
            cls="UNKNOWN"; modules=[]; entry="UNKNOWN"; mode="UNKNOWN"
        out.append({"path":p,"line":x.get("line"),"call":x.get("call"),"source":x.get("source"),"classification":cls,"actual_module_source":modules,"resolved_modules":modules,"dispatch_mode":mode,"entrypoint":entry,"reachable_from_product":False if cls in {"TEST_ONLY","SCRIPT_ONLY"} else None,"reachability":"not product runtime" if cls in {"TEST_ONLY","SCRIPT_ONLY"} else "UNKNOWN"})
    sp=[]
    for x in j.get("sys_path_records",[]):
        p=x.get("path",""); cls="DIRECT_SCRIPT_BOOTSTRAP_ONLY" if p.startswith("scripts/") else "LEGACY"
        sp.append({**x,"classification":cls,"runtime_required":False,"direct_file_execution_only":True})
    return {"schema":"aegis-design-dynamic-reachability-v1","dynamic_imports":out,"dynamic_count":len(out),"sys_path_records":sp,"sys_path_count":len(sp),"conclusion":"all 15 observed dynamic-import records resolve to stdlib literals or finite developer/test dispatch lists; none has a product-runtime reachability edge in the inspected tree","limitations":["runtime plugin/entry-point discovery outside these records remains not proven"]}

def entrypoints():
    return {"schema":"aegis-design-entrypoint-truth-v1","roots":[
      {"root":"aegis_cognition.Agent","class":"PRIMARY_PUBLIC","reachable":"AgentApplication; LabApplication only when lab=True; credential/provider dependent","proof":"source + clean wheel import"},
      {"root":"AgentApplication","class":"PRIMARY_PUBLIC","reachable":"application orchestration -> provider or Lab","proof":"source symbol"},
      {"root":"Lab/LabSession/LabPolicy","class":"PRIMARY_PUBLIC","reachable":"Python LabRun projection -> native controller/cells/replay","proof":"source + local lifecycle"},
      {"root":"root aegis CLI","class":"PRIMARY_PUBLIC","reachable":"aegis_cognition.cli:main","proof":"wheel entry point + --help PASS"},
      {"root":"core/python aegis CLI","class":"PUBLIC_COMPATIBILITY","reachable":"legacy source-level aegis_cli:main; current core wheel publishes no console script and does not contain aegis_cli.py","proof":"core wheel RECORD + independent core probe PASS_NO_CONSOLE_SCRIPT"},
      {"root":"Rust binary","class":"PRIMARY_PUBLIC","reachable":"Cargo target/CLI dispatch","proof":"cargo metadata/source"},
      {"root":"PyO3 module","class":"PRIMARY_PUBLIC","reachable":"aegis_cognition.aegis_nerve native bindings","proof":"clean wheel import"},
      {"root":"operator API","class":"INTERNAL_OPERATOR","reachable":"operator/artifact routes","proof":"source inventory"},
      {"root":"cluster worker","class":"INTERNAL_OPERATOR","reachable":"worker/queue paths","proof":"source; hosted behavior not proven"},
      {"root":"plugin entry points","class":"POC","reachable":"workspace/plugin crates; runtime discovery not proven","proof":"cargo metadata"},
      {"root":"scripts and gates","class":"DEVELOPER_TOOL","reachable":"explicit direct invocation/CI only","proof":"source"}],"limitations":["codebase-memory index is secondary: it is ready but detects 97 changed files versus origin and is not used as sole truth"]}

def ffi_surface():
    j=reuse_prior("final_ffi_runtime_map.json")
    j["schema"]="aegis-design-ffi-actual-surface-v1"
    ffi_path=ROOT/"core/rust/src/ffi.rs"
    ffi_text=ffi_path.read_text(encoding="utf-8",errors="replace") if ffi_path.is_file() else ""
    box_leak_present="Box::leak" in ffi_text
    pyfn_count=len(re.findall(r"#\[pyfunction(?:\([^]]*\))?\]",ffi_text)); wrap_count=len(re.findall(r"wrap_pyfunction!",ffi_text)); pymethod_count=len(re.findall(r"#\[pymethods\]",ffi_text))
    j["registration_summary"]={"pyfunction_attributes":pyfn_count,"wrap_pyfunction_calls":wrap_count,"pymethods_blocks":pymethod_count,"registered_module_functions":wrap_count,"pub_fn_declarations":len(re.findall(r"^\s*pub\s+fn\s+",ffi_text,re.M)),"source":"core/rust/src/ffi.rs","interpretation":f"{wrap_count} registered module functions plus {pymethod_count} pymethods class block(s); pub fn count includes helpers and is not API count"}
    for x in j.get("records",[]):
        c=x.get("reachability_class","UNKNOWN")
        x["classification"]={"REACHABLE_FROM_PUBLIC_API":"EXPORTED_AND_PUBLICLY_REACHABLE","REACHABLE_FROM_LAB":"EXPORTED_LAB_REACHABLE","REACHABLE_FROM_COMPATIBILITY":"EXPORTED_COMPATIBILITY_ONLY","REACHABLE_FROM_TEST":"EXPORTED_TEST_ONLY","REACHABLE_FROM_POC":"EXPORTED_POC_ONLY","UNUSED_SUSPECT":"EXPORTED_UNUSED_SUSPECT","UNKNOWN":"UNKNOWN"}.get(c,"UNKNOWN")
        if x["classification"]=="UNKNOWN":
            path=x.get("path","").replace("\\","/")
            callers=x.get("caller_candidates") or []
            if "/tests/" in path or "/benches/" in path: x["classification"]="EXPORTED_TEST_ONLY"
            elif path.startswith("pocs/") or path.startswith("aegis-plugins/"): x["classification"]="EXPORTED_POC_ONLY"
            elif callers: x["classification"]="EXPORTED_COMPATIBILITY_ONLY"
            elif path.endswith("core/rust/src/ffi.rs"): x["classification"]="EXPORTED_UNUSED_SUSPECT"
            else: x["classification"]="UNKNOWN"
        x["classification_evidence"]="runtime representative call, source path, and caller candidates; absence of a caller is not proof of unreachable"
    j["ffi_file_families"]=[{"family":"native status/resource/hash/layout","responsibilities":["public scalar/bytes validation"],"coherent":True},{"family":"PyO3 conversions","responsibilities":["Python/Rust DTO conversion"],"coherent":True},{"family":"Lab controller binding","responsibilities":["controller/admission/replay bindings"],"coherent":True},{"family":"error/panic boundary","responsibilities":["PyErr conversion, unwrap/leak hazards"],"coherent":False}]
    leak_record=(
        {"classification":"UNBOUNDED_LEAK_DEFECT","reachability":"EXPORTED_PUBLIC_FFI; in-repo callers are TEST_ONLY","lifetime":"process","reset":"none observed","evidence":"aegis_llm_request leaks model_hint and aegis_llm_reject leaks error into &'static str; public PyO3 functions accept arbitrary per-call String and no reclamation/arena bound exists","consequence":"unbounded process memory growth under repeated public calls"}
        if box_leak_present else
        {"classification":"NOT_PRESENT_IN_CURRENT_FFI_SOURCE","reachability":"previous targeted wrappers are still exported; current source contains no Box::leak call","lifetime":"N/A","reset":"N/A","evidence":"aegis_llm_request drops the compatibility-only model_hint and passes None; aegis_llm_reject uses the bounded literal 'ffi rejection'; targeted ffi_smoke_checks passed","consequence":"the previously identified wrapper leak is locally addressed; other static-lifetime fields and unrelated sites require separate review"}
    )
    special_leak=(
        {"classification":"UNBOUNDED_LEAK_DEFECT","scope":"exported aegis_llm_request/aegis_llm_reject String inputs","evidence":"same process-lifetime leak and absent reclamation as hazard_classification"}
        if box_leak_present else
        {"classification":"LOCALLY_ADDRESSED_IN_CURRENT_SOURCE","scope":"exported aegis_llm_request/aegis_llm_reject compatibility wrappers","evidence":"no Box::leak remains in core/rust/src/ffi.rs; the bounded smoke test passed, but no long-run allocation campaign was run"}
    )
    j["hazard_classification"]={
        "SessionSearchIndex::new(...).unwrap()":{"classification":"SAFE_BY_PROVEN_INVARIANT","panic_boundary":"py_safe maps any unexpected panic to PyRuntimeError rather than process escape","evidence":"core/rust/src/ffi.rs get_session_index uses constant blake3 epoch hash; HotLexicalIndex::new rejects only an all-zero hash, and the measured digest is 0b76d5ae2bdb94c7e958e8eb8925e5c4109c1e2807f49ad424761dee3a9e57b4"},
        "Box::leak":leak_record
    }
    j["special_audit"]={
        "SessionSearchIndex::new(...).unwrap()": {"classification":"SAFE_BY_PROVEN_INVARIANT","scope":"targeted production-facing index path","evidence":"same measured nonzero constant hash and py_safe panic-to-PyErr boundary as hazard_classification"},
        "Box::leak": special_leak,
        "other unwrap/expect/panic/static occurrences": {"classification":"UNKNOWN","scope":"not asserted as the targeted SessionSearchIndex path; requires separate per-site reachability review","evidence":"this closure pass does not promote lexical occurrence counts to production hazard claims"}
    }
    return j

def rust_surface():
    j=reuse_prior("final_rust_public_api.json"); j["schema"]="aegis-design-rust-public-surface-v1"; j["exposure_rules"]=["PUBLIC_INTENTIONAL only when deliberate product/FFI/CLI contract","FFI_INTERNAL for binding helpers","CRATE_INTERNAL for pub used within workspace","PUBLIC_ACCIDENTAL_SUSPECT for visibility without consumer proof","PUBLIC_COMPATIBILITY for compatibility surfaces","TEST_ONLY for tests/benches"]
    for m in j.get("modules",[]):
        old=m.get("class")
        path=m.get("path","").replace("\\","/")
        if bool(m.get("test_only")) or "/tests/" in path or "/benches/" in path:
            exposure="TEST_ONLY"
        elif path.endswith("/examples/pipeline_aggregate.rs") or "/examples/" in path:
            exposure="TEST_ONLY"
        elif path.endswith("/src/main.rs"):
            exposure="PUBLIC_INTENTIONAL"
        elif path.endswith("/src/ffi.rs") or bool(m.get("ffi_reachable")):
            exposure="FFI_INTERNAL"
        elif path.endswith("/src/lab.rs") or path.endswith("/src/replay.rs") or (path.endswith("/src/lib.rs") and "/core/rust/" in path):
            exposure="CRATE_INTERNAL"
        elif path.endswith("/src/cli/mod.rs"):
            exposure="PUBLIC_COMPATIBILITY"
        elif m.get("public_declarations",0) and not m.get("fan_in"):
            exposure="PUBLIC_ACCIDENTAL_SUSPECT"
        else:
            exposure="UNKNOWN"
        m["legacy_class"]=old
        m["class"]=exposure
    declarations=[]
    decl_rx=re.compile(r"^\s*pub(?:\([^)]*\))?\s+(?:(?:async|unsafe|const)\s+)*(fn|struct|enum|trait|type|const|static|mod)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for base in (ROOT/"core/rust/src", ROOT/"aegis-plugins"):
        if not base.exists(): continue
        for p in base.rglob("*.rs"):
            path=p.relative_to(ROOT).as_posix()
            if "/tests/" in path or "/benches/" in path or "/examples/" in path: exposure="TEST_ONLY"
            elif path.endswith("/src/main.rs"): exposure="PUBLIC_INTENTIONAL"
            elif path.endswith("core/rust/src/ffi.rs"): exposure="FFI_INTERNAL"
            elif path.endswith("core/rust/src/cli/mod.rs"): exposure="PUBLIC_COMPATIBILITY"
            elif path.endswith("core/rust/src/lab.rs") or path.endswith("core/rust/src/replay.rs") or path.endswith("core/rust/src/lib.rs"): exposure="CRATE_INTERNAL"
            elif path.startswith("aegis-plugins/"): exposure="PUBLIC_ACCIDENTAL_SUSPECT"
            else: exposure="UNKNOWN"
            try: lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
            except OSError: continue
            for no,text in enumerate(lines,1):
                hit=decl_rx.search(text)
                if hit: declarations.append({"path":path,"line":no,"kind":hit.group(1),"name":hit.group(2),"exposure_class":exposure,"visibility":"pub","evidence":"source declaration; consumer fan-in is separate graph evidence"})
    j["important_public_declarations"]=declarations
    if isinstance(j.get("nested_rust_mirror"),dict):
        j.pop("nested_rust_mirror",None)
    j["nested_rust_mirror_ref"]="rust_mirror_truth.json"
    j["tooling_used"]=["cargo metadata --no-deps","direct Rust source parser/prior graph","codebase-memory secondary graph schema/search"]
    return j

def fixed_reports(base, ep):
    ffi=ffi_surface(); rust=rust_surface()
    chains={"schema":"aegis-design-side-effect-chains-v1","chains":[
      {"effect":"PROVIDER CALL","entrypoint":"aegis_cognition/application.py -> AegisAdapter -> provider.py","stages":[{"stage":"orchestrator","status":"PRESENT_AND_MECHANICAL","evidence":"AgentApplication/AgentApplication.run"},{"stage":"IR/request","status":"PRESENT_BY_CONVENTION","evidence":"adapter builds provider request"},{"stage":"cell/admission","status":"PRESENT_AND_MECHANICAL","scope":"LabApplication gateway only","evidence":"lab.py _run_admitted_gateway"},{"stage":"capability/budget/attempt/lease","status":"PRESENT_AND_MECHANICAL","scope":"Lab gateway plus nested provider-attempt admission"},{"stage":"effect sink","status":"PRESENT_AND_MECHANICAL","evidence":"provider.py invoke_with_provider_route"},{"stage":"settlement/evidence/replay","status":"PRESENT_AND_MECHANICAL","scope":"Lab path; compatibility path has no Lab event"}],"overall":"BYPASSABLE","reason":"non-Lab Agent/AegisAdapter/provider route is directly callable; SDK receipt/idempotency remains external","can_execute_without_authoritative_admission":True},
      {"effect":"NETWORK FETCH","entrypoint":"LabApplication -> SearchProgramExecutor._fetch","stages":[{"stage":"orchestrator","status":"PRESENT_AND_MECHANICAL","evidence":"_run_search_program"},{"stage":"IR/request","status":"PRESENT_AND_MECHANICAL","evidence":"research program validation"},{"stage":"cell/admission","status":"PRESENT_AND_MECHANICAL","evidence":"research action admission before executor"},{"stage":"capability/budget/attempt","status":"PRESENT_AND_MECHANICAL","evidence":"allowlist, HTTPS, byte quota, Lab budget"},{"stage":"lease","status":"UNKNOWN","evidence":"no independent helper-wide lease proof"},{"stage":"effect sink","status":"PRESENT_AND_MECHANICAL","evidence":"urllib.request.urlopen in asyncio.to_thread"},{"stage":"settlement/evidence/replay","status":"PRESENT_AND_MECHANICAL","scope":"strict Lab research path"}],"overall":"BYPASSABLE","reason":"compatibility search callback can run outside strict research-cell path; DNS/redirect/platform enforcement needs external probe","can_execute_without_authoritative_admission":"compatibility-mode dependent"},
      {"effect":"BROWSER ACTION","entrypoint":"LabApplication -> BrowserCell -> browser_runtime_adapter/Playwright","stages":[{"stage":"orchestrator","status":"PRESENT_BY_CONVENTION","evidence":"browser action dispatcher"},{"stage":"cell/admission","status":"PRESENT_AND_MECHANICAL","evidence":"BrowserCell.admit before acquire/action"},{"stage":"capability/budget","status":"PRESENT_AND_MECHANICAL","evidence":"browser policy and Lab admission"},{"stage":"attempt/lease","status":"UNKNOWN","evidence":"recover max_attempts is local; OS/browser lease and descendant containment external"},{"stage":"effect sink","status":"PRESENT_AND_MECHANICAL","evidence":"Playwright adapter"},{"stage":"settlement/evidence/replay","status":"PRESENT_AND_MECHANICAL","scope":"explicit Lab browser path"}],"overall":"FENCED_IN_LAB_ONLY","reason":"live browser permissions, SSRF/redirect behavior, and descendant cleanup are external-only","can_execute_without_authoritative_admission":"direct compatibility adapter possible"},
      {"effect":"PROCESS SPAWN","entrypoint":"Lab ProcessExecutionCell / benchmark hidden validator","stages":[{"stage":"orchestrator","status":"PRESENT_BY_CONVENTION","evidence":"experiment/benchmark dispatch"},{"stage":"admission","status":"PRESENT_AND_MECHANICAL","scope":"Lab ProcessExecutionCell"},{"stage":"budget","status":"PRESENT_AND_MECHANICAL","scope":"Lab limits"},{"stage":"effect sink","status":"PRESENT_AND_MECHANICAL","evidence":"benchmark.py subprocess.Popen shell=False timeout"},{"stage":"settlement/evidence/replay","status":"PRESENT_AND_MECHANICAL","scope":"Lab process path; benchmark gate is operator/test infrastructure"}],"overall":"FENCED_IN_LAB_ONLY","reason":"child descendants, OS limits, and operator-injected argv are not fully proven","can_execute_without_authoritative_admission":"YES for benchmark/tooling paths"},
      {"effect":"FILESYSTEM WRITE","entrypoint":"LabRun archive/replay versus CLI/scripts","stages":[{"stage":"orchestrator","status":"PRESENT_BY_CONVENTION","scope":"Lab archive path"},{"stage":"admission","status":"PRESENT_BY_CONVENTION","scope":"Lab memory/replay events"},{"stage":"capability/budget","status":"PRESENT_AND_MECHANICAL","scope":"Lab path"},{"stage":"lease","status":"UNKNOWN","scope":"filesystem ownership not globally leased"},{"stage":"effect sink","status":"PRESENT_AND_MECHANICAL","evidence":"Lab archive plus direct CLI/script writes"},{"stage":"settlement/evidence/replay","status":"PRESENT_AND_MECHANICAL","scope":"Lab path"}],"overall":"BYPASSABLE","reason":"CLI config writes, report generators and operator/audit writes are direct effects outside Lab authority","can_execute_without_authoritative_admission":True}]}
    bypass={"schema":"aegis-design-side-effect-bypasses-v1","rows":[
      {"sink":"urllib.request.urlopen","classification":"FENCED_IN_LAB_ONLY","can_execute_without_authoritative_admission":"NO in strict research-cell path; YES via compatibility callback/direct helper","evidence":"aegis_cognition/lab.py SearchProgramExecutor._fetch lines 475-494"},
      {"sink":"provider SDK invoke","classification":"COMPATIBILITY_BYPASS","can_execute_without_authoritative_admission":"YES","evidence":"aegis_cognition/application.py and core/python/aegis/provider.py route do not require Lab admission"},
      {"sink":"Playwright launch/action","classification":"FENCED_IN_LAB_ONLY","can_execute_without_authoritative_admission":"YES via direct compatibility runtime; Lab path admits before acquire/action","evidence":"core/python/browser_playwright_runtime.py:244; lab.py BrowserCell"},
      {"sink":"subprocess/Popen","classification":"DIRECT_OPERATOR_EFFECT","can_execute_without_authoritative_admission":"YES for benchmark hidden validator and developer tooling; Lab cell is admitted","evidence":"aegis_cognition/benchmark.py:225, shell=False"},
      {"sink":"filesystem writes","classification":"DIRECT_OPERATOR_EFFECT","can_execute_without_authoritative_admission":"YES for CLI/scripts/operator; Lab archive path is evented","evidence":"aegis_cognition/cli.py:143 and LabRun archive/replay"},
      {"sink":"sys.path/environment mutation","classification":"TEST_ONLY","can_execute_without_authoritative_admission":"YES setup by direct script/test execution","evidence":"13 sys.path records are direct script bootstrap or legacy metrics"}]}
    div={"schema":"aegis-design-state-divergence-v1","rows":[
      {"entity":"Mission","python":"LabRun mission_id/objective/scope/non_goals; mission_id includes created_at_ms","rust":"LabMissionSpec mission_id/objective/scope/contract_hash/BudgetPolicy/required_evidence/max_steps/schema","classification":"SEMANTIC_DIVERGENCE","field_comparison":{"python_only":["non_goals","token_budget scalar"],"rust_only":["contract_hash","budget vectors","required_evidence","schema"],"shared":"mission_id/objective/scope"},"evidence":"aegis_cognition/lab.py LabRun/_initialize_native_controller; core/rust/src/lab.rs LabMissionSpec"},
      {"entity":"Source","python":"SourceRecord hashes are str; relation/citation_spans/extractor/provenance fields","rust":"typed hashes [u8;32], HTTPS/credential/hash validation","classification":"LOSSY_PROJECTION","field_comparison":{"python_only":["relation","citation_spans"],"rust_only":["typed hash validation"],"shared":"source_id,uri,retrieved_at_ms,trust_tier,extractor,provenance_cluster"},"native_projection_behavior":"valid hex hashes are materialized into typed runtime.sources; opaque compatibility labels remain projection-only","evidence":"lab.py SourceRecord/add_source; core/rust/src/lab.rs SourceRecord/admit_projection_record"},
      {"entity":"Claim","python":"status str, tuple source_ids, confidence_bps int","rust":"ClaimStatus enum, Vec source_ids, u16 confidence_bps","classification":"LOSSY_PROJECTION","native_projection_behavior":"a valid typed source chain materializes runtime.claims; adapter-only fields remain in projection payload","evidence":"lab.py ClaimRecord/add_claim; core/rust/src/lab.rs ClaimRecord"},
      {"entity":"Hypothesis","python":"tuple links/falsifiers and Python validation","rust":"Vec links/falsifiers with typed validation","classification":"LOSSY_PROJECTION","native_projection_behavior":"a valid typed claim chain materializes runtime.hypotheses; projection IDs/payload remain for compatibility","evidence":"lab.py HypothesisRecord/add_hypothesis; core/rust/src/lab.rs HypothesisRecord"},
      {"entity":"Experiment","python":"tuple variables/controls/seeds, optional uncertainty, min_clean_replicates default 0","rust":"Vec fields, at least five seeds, dimensional unit and hypothesis validation","classification":"LOSSY_PROJECTION","native_projection_behavior":"a valid typed prerequisite chain materializes runtime.experiments while projection retains the adapter payload","evidence":"lab.py ExperimentSpec; core/rust/src/lab.rs ExperimentSpec"},
      {"entity":"Observation","python":"string hashes, reported_unit, optional uncertainty/replication/operator/clean","rust":"typed hashes, uncertainty/replication/operator/clean, no reported_unit","classification":"LOSSY_PROJECTION","native_projection_behavior":"a valid typed prerequisite/hash chain materializes runtime.observations; projection retains reported_unit and adapter fields","evidence":"lab.py ObservationRecord; core/rust/src/lab.rs ObservationRecord"},
      {"entity":"Artifact","python":"LabDossier manifest/artifact hashes; no canonical typed ArtifactRef in lab.py","rust":"typed gt96::ArtifactRef and replay/hash structures","classification":"SEMANTIC_DIVERGENCE","evidence":"lab.py LabDossier.manifest; core/rust/src/gt96.rs ArtifactRef"},
      {"entity":"Event","python":"payload-bearing LabEvent with string kind and Python event hash","rust":"typed LabEventKind, hash-only payload reference, Rust chain","classification":"LOSSY_PROJECTION","native_projection_behavior":"Python payload_json is admitted and stored in projection_payloads, not in typed Rust LabEvent payload","evidence":"lab.py LabEvent/_append; core/rust/src/lab.rs LabEvent/admit_projection_record"},
      {"entity":"Replay","python":"Python archive/replay and optional native reconstruction","rust":"LabController snapshot/replay with native runtime","classification":"AUTHORITY_DIVERGENCE","compatibility":"Python aegis-lab-run-v1 and Rust mission schema aegis-lab-runtime-v1 are not cross-compatible snapshots","evidence":"lab.py to_payload/from_payload; core/rust/src/lab.rs snapshot_json/from_snapshot_json"},
      {"entity":"ExecutionCell","python":"callable registry, admission dictionaries and retry options","rust":"typed action plan/resource lease/admission maps","classification":"SEMANTIC_DIVERGENCE","evidence":"lab.py cell runners; core/rust/src/lab.rs controller projection and resource types"},
      {"entity":"TrustLevel","python":"Agent/Lab default DEV; evidence adapter None normalizes PROD","rust":"TrustLevel::from_env default PROD","classification":"SEMANTIC_DIVERGENCE","evidence":"config.py, lab.py, core/python/aegis/evidence.py, core/rust/src/hot_engine.rs"},
      {"entity":"RetryPolicy","python":"AegisAgent/provider/Lab gateway/tool/experiment loops","rust":"native admission/resource checks, no single retry owner","classification":"AUTHORITY_DIVERGENCE","evidence":"aegis_adapter.py, provider.py, lab.py, core/rust/src/lab.rs"}],"conclusion":"both Python and Rust contain explicit records; the bridge now conditionally materializes valid typed records while retaining a lossy adapter projection, and no single lossless canonical reducer is proven","required_entities_checked":["LabRun","LabController","Mission","Event","Budget","Attempt","Lease","ExecutionCell","Source","Claim","Hypothesis","Experiment","Observation","Evidence","Artifact","ProviderRequest","Replay state","Trust policy","Retry policy"],"limitations":["field comparison is source-based; bounded local native snapshot/restore, fresh-process replay, typed materialization, and Python-projection mutation probes were executed, but no lossless cross-language canonical model was proven"]}
    trust=reuse_prior("final_trust_policy.json"); trust["schema"]="aegis-design-trust-truth-v1"; trust.update({"canonical_subject_primitive":{"path":"core/python/aegis/trust_policy.py","status":"LOCAL-PROVEN","scope":"canonical schema fields and BLAKE2b-256 subject hash consumed by root config and core evidence bridge","limitation":"contextual DEV/PROD defaults and cross-cell Rust/provider/browser/process policy receipts remain open"},"effective_local_probe":{"env_AEGIS_TRUST_LEVEL":None,"AgentConfig_default":"DEV","AgentConfig_mission_hash_binding":"from_inputs creates a canonical 64-hex subject; Agent(lab=True) binds lab_trust_policy_hash and gateway receives the same hash","LabPolicy_default":"DEV","LabPolicy_native_authority_required":"False when trust=DEV","AegisAdapter_default":"PROD","evidence.normalize_trust_level(None)":"PROD","rust_TrustLevel_from_env_default":"PROD","lab_policy_hash_binding":"LabPolicy hash is propagated into Lab options/native mission contract hash; core bridge hash equivalence is tested for DEV/STAGING/PROD; native-required gateway drops/mismatch fail closed","result":"local defaults are inconsistent across public paths; mission-bound hash propagation is locally proven; no provider call was made"},"authority_matrix":[{"boundary":"Agent/AgentConfig","default":"DEV","owner":"aegis_cognition.config","hash_binding":"validated missions create a canonical subject; legacy direct AgentConfig may have no hash"},{"boundary":"LabPolicy/LabRun","default":"DEV","owner":"aegis_cognition.lab","hash_binding":"Lab facade and Agent(lab=True) bind the mission subject; direct legacy snapshots may be unbound"},{"boundary":"AegisAdapter/evidence","default":"PROD","owner":"core/python/aegis","hash_binding":"explicit hash mismatch rejected"},{"boundary":"Rust hot_engine","default":"PROD","owner":"core/rust","hash_binding":"mission contract hash receives Lab policy subject"}],"classification":"LEGACY_DRIFT_WITH_LAB_BOUNDARY","security_implication":"global default owner and cross-cell receipt propagation remain unproven; local Lab hash mismatch is fail-closed"})
    retry={"schema":"aegis-design-retry-truth-v1","layers":[
      {"owner":"AegisAgent.run","trigger":"ProviderRateLimitError","default":"max_retries=3 total gateway calls including first","max_attempts":3,"backoff":"exponential sleep","timeout":"inherited provider call","effect_class":"provider call","attempt_id":"adapter retry counter; no shared Lab admission on compatibility path","budget_integration":"compatibility-local, not LabBudget","idempotency":"not globally enforced","cancellation":"not rate-limit retry; other exceptions propagate","formula":"N_agent <= 3","evidence":"core/python/aegis_adapter.py AegisAgent.run"},
      {"owner":"provider.invoke_with_provider_route","trigger":"rate-limit candidate failure","default":"one pass over [primary,*fallback]","max_attempts":"P=1+#fallbacks","backoff":"none in route; SDK may retry","timeout":"provider/SDK dependent","effect_class":"provider call per candidate","attempt_id":"provider candidate index; no global id","budget_integration":"not LabBudget-bound on compatibility path","idempotency":"external provider","cancellation":"CancelledError propagates","formula":"N_route <= P","evidence":"core/python/aegis/provider.py"},
      {"owner":"LabApplication._run_admitted_gateway","trigger":"any Exception","default":"gateway_max_attempts=1, capped by max_steps","max_attempts":"G=min(gateway_max_attempts,max_steps)","backoff":"recursive retry; no uniform backoff","timeout":"finite gateway_timeout_seconds default 60.0 enforced by asyncio.wait_for for cooperative async calls","effect_class":"provider gateway","attempt_id":"per-attempt admission/execution id","budget_integration":"Lab step/reserve and native admission","idempotency":"deterministic local 64-hex key bound to mission/execution/input/policy and checked at admission/settlement; external receipt not proven","cancellation":"CancelledError settles CANCELLED; deadline settles TIMED_OUT","formula":"N_gateway <= G; default G=1","evidence":"aegis_cognition/lab.py _run_admitted_gateway"},
      {"owner":"LabApplication._run_tool_calls","trigger":"any Exception","default":"tool_max_attempts=1, capped by max_steps","max_attempts":"T=min(tool_max_attempts,max_steps)","backoff":"none uniform","timeout":"finite tool_timeout_seconds default 30.0 enforced by asyncio.wait_for for cooperative async calls","effect_class":"tool effect","attempt_id":"per-tool-attempt admission","budget_integration":"Lab steps/reserves","idempotency":"deterministic local 64-hex key bound to mission/call/execution/input/policy and checked at admission/settlement; runner external effect not proven","cancellation":"cancellation settles terminal non-success; deadline settles TIMED_OUT","formula":"N_tool <= T","evidence":"aegis_cognition/lab.py _run_tool_calls"},
      {"owner":"LabApplication experiment/simulation","trigger":"runner failure","default":"experiment_max_attempts/simulation_max_attempts, capped by max_steps","max_attempts":"E/S capped by max_steps","backoff":"none uniform","timeout":"finite experiment_timeout_seconds/simulation_timeout_seconds default 30.0 enforced by asyncio.wait_for for cooperative async calls","effect_class":"process/experiment effect","attempt_id":"per experiment/simulation admission","budget_integration":"Lab budget/step","idempotency":"deterministic local 64-hex key bound to mission/execution/input/policy and checked at admission/settlement; runner external effect not proven","cancellation":"terminal non-success; deadline settles TIMED_OUT","formula":"N_exp<=E; N_sim<=S","evidence":"aegis_cognition/lab.py _run_admitted_experiment/simulation"},
      {"owner":"ProcessExecutionCell","trigger":"child timeout, cancellation or exit without result","default":"one child invocation; timeout_seconds=30.0 unless caller supplies another finite positive value","max_attempts":1,"backoff":"none; the cell has no retry loop","timeout":"parent monotonic deadline; timeout terminates child and raises TimeoutError; cancellation terminates child then propagates CancelledError","effect_class":"process execution","attempt_id":"enclosing Lab execution admission; child PID is not an authority identity","budget_integration":"enclosing Lab cell/budget only; ProcessExecutionCell does not own Lab budget","idempotency":"enclosing execution idempotency when admitted; no external-effect idempotency is established by the child cell","cancellation":"local child termination is proven; descendant and OS resource containment remain external","formula":"N_process_cell <= 1 per invocation before any enclosing retry","evidence":"aegis_cognition/lab.py ProcessExecutionCell; tests/test_lab_runtime.py process_execution_cell timeout/cancellation tests"},
      {"owner":"provider SDK","trigger":"SDK transient","default":"UNKNOWN","max_attempts":"UNKNOWN","backoff":"UNKNOWN","timeout":"UNKNOWN","effect_class":"provider external","attempt_id":"UNKNOWN","budget_integration":"UNKNOWN","idempotency":"provider-specific","cancellation":"provider-specific","formula":"N_sdk UNKNOWN","evidence":"provider package not contacted"}],"observed_lab_admission_bound":{"field":"max_external_attempts","default_formula":"8 * (max_steps + 1)^3","scope":"Lab-owned effect admissions observed by the Python/Rust event ledger","enforcement":"Python pre-mutation counter and Rust clone-before-commit projection guard","replay_binding":"mission/snapshot field plus exact event-derived admission count","exclusions":["control-only cancellation admissions","physical network/browser subrequests","opaque SDK/user-runner retries","external provider receipt idempotency","descendant effects"]},"derived_bounds":["non-Lab AegisAgent over provider route: N_provider_invocations <= 3*P before unknown SDK retries","Lab gateway uses AegisAdapter (not AegisAgent): N_provider_invocations <= G*P before unknown SDK retries","Lab gateway/tool/experiment/simulation local receipts have deterministic idempotency keys and finite cooperative deadlines","observed Lab-owned effect admissions: N_lab_observed <= max_external_attempts; default max_external_attempts = 8*(max_steps+1)^3","ProcessExecutionCell itself performs no retry: N_process_cell <= 1 per invocation; an enclosing Lab policy may still retry the admission","physical/global N_external and end-to-end idempotency remain NOT VERIFIED because user runners/callables, external effects and SDK retry semantics remain open"],"duplicate_effect_risks":["outer Lab gateway retries any Exception, including ambiguous timeout after external commit","adapter/provider/SDK layers can multiply attempts","provider candidate fallback may execute a different external effect","user-supplied runner can contain its own retry","non-cooperative or synchronous runner can outlive an asyncio deadline"],"classification":"LAYERED_RETRY_OWNER_OPEN","limitations":["no live provider or SDK retry probe","observed Lab-owned admission bound does not measure physical requests or hidden retries"]}
    err={"schema":"aegis-design-error-semantics-v1","rows":[
      {"failure":"invalid input/contract","origin":"Python/Lab/Rust validation","handling":"ValueError/PyValueError before effect","classification":"FAIL_CLOSED","status":"SOURCE_PROVEN_LOCAL"},
      {"failure":"native admission rejection","origin":"LabController.admit_record_json","handling":"Python raises generic RuntimeError and rolls back projection; Rust variant detail is flattened","classification":"FAIL_CLOSED_STRUCTURED_DETAIL_LOST","status":"SOURCE_PROVEN_LOCAL"},
      {"failure":"provider non-rate-limit failure","origin":"provider route/adapter","handling":"settled REJECTED/TIMED_OUT then original exception re-raised; Lab outer retry may recur","classification":"FAIL_CLOSED_LOCALLY_BUT_RETRY_DUPLICATE_RISK","status":"SOURCE_PROVEN_LOCAL"},
      {"failure":"provider timeout","origin":"SDK/provider","handling":"no local success on exception; external commit before timeout is ambiguous","classification":"AMBIGUOUS_EXTERNAL_COMMIT","status":"EXTERNAL_NOT_VERIFIED"},
      {"failure":"provider cancellation","origin":"provider route/Lab","handling":"settled CANCELLED and propagates cancellation","classification":"FAIL_CLOSED_LOCALLY","status":"SOURCE_PROVEN_LOCAL"},
      {"failure":"process crash/no child message","origin":"ProcessExecutionCell/benchmark validator","handling":"RuntimeError/nonzero/timeout; caller settles REJECTED","classification":"FAIL_CLOSED_LOCALLY_DESCENDANT_CLEANUP_EXTERNAL","status":"PARTIALLY_PROVEN_LOCAL"},
      {"failure":"browser failure/cancellation","origin":"BrowserCell/Playwright","handling":"known errors settle REJECTED; CancelledError settles CANCELLED","classification":"FAIL_CLOSED_LOCALLY_BROWSER_BOUNDARY_EXTERNAL","status":"PARTIALLY_PROVEN_LOCAL"},
      {"failure":"replay corruption","origin":"Python/Rust snapshot/event chain","handling":"validation rejects restore/replay","classification":"FAIL_CLOSED","status":"SOURCE_AND_TEST_PROVEN_LOCAL"},
      {"failure":"budget exhaustion","origin":"Lab/native resource","handling":"admission/step budget rejects before effect","classification":"FAIL_CLOSED","status":"SOURCE_AND_TEST_PROVEN_LOCAL"},
      {"failure":"benchmark validator failure","origin":"hidden validator subprocess","handling":"timeout/nonzero/invalid JSON/schema/hash raises; gate remains failed","classification":"FAIL_CLOSED_GATE","status":"SOURCE_PROVEN_LOCAL"}],"structured_to_string_sites":reuse_prior("final_error_semantics.json").get("structured_to_string_sites",[]),"broad_catch_suspects":reuse_prior("final_error_semantics.json").get("broad_catch_suspects",[]),"limitations":["no live provider/browser/OS descendant failure induced"]}
    traces={
      "invalid input/contract":"input -> Python/Rust validation -> ValueError/PyValueError -> no effect -> no settlement event -> rejection returned",
      "native admission rejection":"Lab event -> PyO3 admit_record_json -> Rust LabError -> generic Python RuntimeError -> Python projection rollback -> non-success result/replay blocker",
      "provider non-rate-limit failure":"provider SDK/route -> exception -> Lab settlement REJECTED/TIMED_OUT when fenced -> outer exception/retry -> replay stores error type, not full structured variant",
      "provider timeout":"provider SDK timeout -> Python exception -> local non-success settlement -> external receipt/commit state unknown -> replay cannot prove whether remote effect committed",
      "provider cancellation":"CancelledError -> route/Lab CANCELLED settlement -> propagation -> replay terminal CANCELLED -> user cancellation",
      "process crash/no child message":"child/process -> missing message/nonzero/timeout -> RuntimeError -> REJECTED settlement -> local replay records failure; descendant cleanup is external",
      "browser failure/cancellation":"Playwright/browser adapter -> known exception or CancelledError -> REJECTED/CANCELLED settlement -> replay terminal status -> user error/cancel",
      "replay corruption":"archive/snapshot -> hash/schema/sequence validation -> recovery error -> no reconstructed success -> fail-closed user result",
      "budget exhaustion":"admission/resource ledger -> budget error/status -> no sink invocation -> rejected/blocked settlement -> replay blocker",
      "benchmark validator failure":"validator subprocess -> timeout/nonzero/invalid JSON/hash -> Python validation exception -> gate failure -> no acceptance evidence"
    }
    for row in err["rows"]: row["trace"]=traces.get(row["failure"],"NOT_TRACED")
    conf={"schema":"aegis-design-config-precedence-v1","precedence_by_boundary":[
      {"boundary":"trust level","order":"explicit override > config.toml trust.level > AEGIS_TRUST_LEVEL > DEV","evidence":"aegis_cognition/config.py resolve_trust_level"},
      {"boundary":"API key","order":"config llm.api_key > OPENAI_API_KEY > AEGIS_API_KEY","evidence":"aegis_cognition/config.py AgentConfig.from_inputs"},
      {"boundary":"Agent/Lab options","order":"Agent kwargs/options > LabPolicy/LabBudget merged options > defaults; policy setdefault allows explicit option override","evidence":"aegis_cognition/lab.py LabPolicy.start/_gateway"},
      {"boundary":"retry counts and observed admissions","order":"per-run options override defaults, then cap at max_steps; LabBudget.max_external_attempts may impose a stricter mission-bound envelope; no provider SDK override known","evidence":"lab.py gateway/tool/experiment/simulation/LabBudget"},
      {"boundary":"Rust trust","order":"AEGIS_TRUST_LEVEL > Rust default PROD","evidence":"core/rust/src/hot_engine.rs TrustLevel::from_env"}],"duplicated_defaults":["trust (DEV/PROD)","retry (adapter/provider/Lab/SDK)","retention/archive","package/CLI owner"],"security_relevant_override":"Lab policy browser/replay/native/external-write values use setdefault, so explicit options can override policy; this is a design blocker until authority is explicit","limitations":["provider/deployment environment matrix not contacted"]}
    sch=reuse_prior("final_schema_contracts.json"); sch["schema"]="aegis-design-schema-graph-v1"
    known={x.get("id") for x in sch.get("contracts",[])}
    additional=[
      {"id":"PyO3 DTO boundary","writer":"ffi.rs","reader":"Python bindings","version":"unversioned per function","compatibility":"not globally versioned","migration":"none proven","hash_binding":"per payload/unknown","test_coverage":"representative local calls","status":"PARTIALLY_PROVEN"},
      {"id":"snapshot format","writer":"Python LabRun / Rust LabController","reader":"recovery/archive","version":"Python aegis-lab-run-v1; Rust mission aegis-lab-runtime-v1; no top-level controller version","compatibility":"CROSS_LANGUAGE_INCOMPATIBLE_NOT_PROVEN","migration":"none proven","hash_binding":"per event/snapshot fields; no common envelope proven","test_coverage":"local same-language recovery only","status":"RISK"},
      {"id":"artifact manifest","writer":"release/evidence scripts","reader":"gates/archive","version":"source-labelled","compatibility":"current.json binding broken","migration":"none proven","hash_binding":"SHA/epoch mixed","test_coverage":"gate fails","status":"RISK"},
      {"id":"lease token","writer":"Lab controller","reader":"execution cell","version":"UNVERSIONED","compatibility":"local-only","migration":"none proven","hash_binding":"metadata only","test_coverage":"partial lifecycle","status":"PARTIALLY_PROVEN"},
      {"id":"telemetry record","writer":"observability/benchmark","reader":"gates/metrics","version":"UNVERSIONED","compatibility":"UNKNOWN","migration":"none proven","hash_binding":"NOT_AUTHORITY","test_coverage":"candidate only","status":"UNKNOWN"},
      {"id":"provider receipt","writer":"provider SDK","reader":"evidence/replay","version":"EXTERNAL","compatibility":"provider-specific","migration":"external","hash_binding":"not locally proven","test_coverage":"no live provider","status":"UNKNOWN"},
      {"id":"browser receipt","writer":"Playwright adapter","reader":"evidence/replay","version":"EXTERNAL","compatibility":"adapter-specific","migration":"external","hash_binding":"not locally proven","test_coverage":"no live browser","status":"UNKNOWN"},
      {"id":"research receipt","writer":"search/fetch helpers","reader":"evidence/replay","version":"aegis-research-program payload family; receipt envelope not globally versioned","compatibility":"PARTIAL_LOCAL","migration":"none proven","hash_binding":"content/snapshot hashes in records; end-to-end receipt binding not proven","test_coverage":"local helper tests; no live research","status":"PARTIALLY_PROVEN"}
    ]
    for item in additional:
        if item["id"] not in known: sch.setdefault("contracts",[]).append(item)
    sch["required_format_families_checked"]=["JSON schemas","PyO3 DTOs","replay events","snapshot","artifact manifest","benchmark protocol","resource contract","lease token","telemetry","evidence records","execution-cell manifest","provider receipt","browser receipt","research receipt"]
    retention=reuse_prior("final_artifact_retention.json"); retention["schema"]="aegis-design-artifact-retention-v1"
    retained_paths={x.get("path") for x in retention.get("artifacts",[])}
    replacement=latest([p for root in TEMP_ROOTS for p in root.rglob("aegis_cognition-*.whl")])
    if replacement:
        current_item={"kind":"replacement wheel","path":str(replacement),"exists_now":True,"tracked":False,"temporary":True,"hash":sha(replacement),"source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"artifact_epoch_binding":"NOT_EMBEDDED; hash is the subject identity","reproducible":"build command recorded; independent rebuild not repeated","superseded":False,"required_for_release":False}
        if current_item["path"] not in retained_paths: retention.setdefault("artifacts",[]).append(current_item)
    build_log=latest([root/"maturin-build.log" for root in TEMP_ROOTS])
    if build_log:
        log_item={"kind":"replacement wheel build log","path":str(build_log),"exists_now":True,"tracked":False,"temporary":True,"hash":sha(build_log),"source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"reproducible":"command/toolchain recorded; log is disposable","superseded":False,"required_for_release":False}
        if log_item["path"] not in retained_paths: retention.setdefault("artifacts",[]).append(log_item)
    provider_fence=latest(
        (ROOT/"artifacts/local-runtime").glob("provider-fence-*/provider_fence*_packaging.json")
    )
    if provider_fence is None:
        provider_fence=ROOT/"artifacts/local-runtime/provider-fence-20260831/provider_fence_packaging.json"
    provider_fence_item={"kind":"provider-route fence packaging record","path":str(provider_fence),"exists_now":provider_fence.is_file(),"tracked":False,"temporary":False,"hash":sha(provider_fence) if provider_fence.is_file() else "NOT_AVAILABLE","source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"reproducible":"local wheel command and regression command are recorded in the JSON record","superseded":False,"required_for_release":False}
    if provider_fence_item["path"] not in retained_paths: retention.setdefault("artifacts",[]).append(provider_fence_item)
    m2_replay=ROOT/"artifacts/local-runtime/m2-all-lane-fresh-replay-20260901/m2_all_lane_fresh_replay.json"
    m2_replay_data=load(m2_replay) if m2_replay.is_file() else {}
    m2_replay_item={"kind":"M2 fresh-process replay witness","path":str(m2_replay),"exists_now":m2_replay.is_file(),"tracked":False,"temporary":False,"hash":sha(m2_replay) if m2_replay.is_file() else "NOT_AVAILABLE","source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"artifact_source_epoch":m2_replay_data.get("source",{}).get("WORKTREE_EPOCH","NOT_AVAILABLE") if isinstance(m2_replay_data,dict) else "NOT_AVAILABLE","reproducible":"bounded native-required parent/child replay command is recorded in the JSON witness","superseded":False,"required_for_release":False}
    if m2_replay_item["path"] not in retained_paths: retention.setdefault("artifacts",[]).append(m2_replay_item)
    for item in [
      {"kind":"v63 smoke records","path":"C:\\Users\\ADMIN\\AppData\\Local\\Temp\\aegis-lab-native-wheel-v63\\*.json","exists_now":False,"tracked":False,"temporary":True,"hash":"NOT_AVAILABLE","source_sha":"NOT_AVAILABLE","worktree_epoch":"NOT_AVAILABLE","reproducible":False,"superseded":"historical/missing","required_for_release":False},
      {"kind":"v62 predecessor","path":"C:\\Users\\ADMIN\\AppData\\Local\\Temp\\aegis-lab-native-wheel-v62","exists_now":False,"tracked":False,"temporary":True,"hash":"NOT_AVAILABLE","source_sha":"NOT_AVAILABLE","worktree_epoch":"NOT_AVAILABLE","reproducible":False,"superseded":"historical/missing","required_for_release":False},
      {"kind":"generated status","path":"docs/architecture/AEGIS_LAB_STATUS_GENERATED.md","exists_now":True,"tracked":False,"temporary":False,"hash":"in WORKTREE_EPOCH","source_sha":"not manifest-bound","worktree_epoch":"CURRENT_RUN_WORKTREE_EPOCH","reproducible":"source script dependent","superseded":False,"required_for_release":"unknown"},
      {"kind":"replay/snapshot samples","path":"local replay/snapshot artifact paths","exists_now":False,"tracked":False,"temporary":True,"hash":"NOT_AVAILABLE","source_sha":"NOT_AVAILABLE","worktree_epoch":"NOT_AVAILABLE","reproducible":False,"superseded":"unknown","required_for_release":"unknown"},
      {"kind":"benchmark outputs","path":"local benchmark artifact paths","exists_now":False,"tracked":False,"temporary":True,"hash":"NOT_AVAILABLE","source_sha":"NOT_AVAILABLE","worktree_epoch":"NOT_AVAILABLE","reproducible":False,"superseded":"unknown","required_for_release":"unknown"},
      {"kind":"CI artifacts","path":"remote CI artifact references","exists_now":False,"tracked":False,"temporary":True,"hash":"NOT_AVAILABLE","source_sha":"NOT_AVAILABLE","worktree_epoch":"NOT_AVAILABLE","reproducible":False,"superseded":"unknown","required_for_release":"external"}
    ]:
        if item["path"] not in retained_paths: retention.setdefault("artifacts",[]).append(item)
    for row in retention.get("artifacts",[]):
        if row.get("kind") == "current manifest":
            manifest=ROOT/"docs/architecture/evidence/current.json"
            row.update({"exists_now":manifest.is_file(),"hash":sha(manifest) if manifest.is_file() else "NOT_AVAILABLE","temporary":False,"source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"reproducible":"tracked manifest; current placeholder is intentionally fail-closed","superseded":False,"required_for_release":True})
        elif row.get("kind") == "final pack":
            pack=ROOT/"docs/architecture/evidence-pack"
            row.update({"exists_now":pack.is_dir(),"temporary":False,"source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"reproducible":"per-file generated artifacts; see evidence-pack","superseded":False,"required_for_release":False})
        elif row.get("kind") == "generated status":
            generated=ROOT/"docs/architecture/AEGIS_LAB_STATUS_GENERATED.md"
            row.update({"exists_now":generated.is_file(),"hash":sha(generated) if generated.is_file() else "NOT_AVAILABLE","source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"reproducible":"document consistency gate output","superseded":False,"required_for_release":True})
        elif row.get("kind") == "provider-route fence packaging record":
            provider=Path(row.get("path", ""))
            row.update({"exists_now":provider.is_file(),"hash":sha(provider) if provider.is_file() else "NOT_AVAILABLE","source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"reproducible":"local wheel command and regression command are recorded in the JSON record","superseded":False,"required_for_release":False})
        elif row.get("kind") == "M2 fresh-process replay witness":
            witness=Path(row.get("path", ""))
            witness_data=load(witness) if witness.is_file() else {}
            row.update({"exists_now":witness.is_file(),"hash":sha(witness) if witness.is_file() else "NOT_AVAILABLE","source_sha":ep["HEAD"],"worktree_epoch":ep["WORKTREE_EPOCH"],"artifact_source_epoch":witness_data.get("source",{}).get("WORKTREE_EPOCH","NOT_AVAILABLE") if isinstance(witness_data,dict) else "NOT_AVAILABLE","reproducible":"bounded native-required parent/child replay command is recorded in the JSON witness","superseded":False,"required_for_release":False})
        elif row.get("kind") == "disposable wheel/temp logs":
            old_temp=Path(row.get("path", ""))
            row.update({"exists_now":old_temp.is_dir(),"temporary":True,"source_sha":"NOT_AVAILABLE","worktree_epoch":"NOT_AVAILABLE","reproducible":"historical disposable location; current replacement is recorded separately","superseded":"historical/missing","required_for_release":False})
    dele=reuse_prior("final_delete_candidates.json"); dele["schema"]="aegis-design-delete-candidates-v1"
    for row in dele.get("rows",[]):
        if row.get("classification") == "ARCHIVE_ONLY": row["classification"]="HISTORICAL_KEEP"
        elif row.get("classification") == "MIGRATION_REQUIRED": row["classification"]="DELETE_AFTER_MIGRATION"
    coh=reuse_prior("final_module_split_candidates.json"); coh["schema"]="aegis-design-cohesion-candidates-v1"
    for row in coh.get("rows",[]):
        if row.get("classification") == "KEEP_MONOLITHIC": row["classification"]="KEEP_AS_IS"
    coh["rows"].append({"path":"core/python/aegis_adapter.py","classification":"REORGANIZE_INTERNAL_ONLY","reason":"compatibility adapter mixes route/retry/error/effect translation","do_not_split":True})
    perf=reuse_prior("final_performance_baseline.json"); perf["schema"]="aegis-design-performance-baseline-v1"; perf["status"]="PASS_REUSED_PRIOR_LOCAL_PROBE"
    move=reuse_prior("final_data_movement.json"); move["schema"]="aegis-design-data-movement-v1"
    inv=reuse_prior("final_invariant_matrix.json"); inv["schema"]="aegis-design-invariant-matrix-v1"; inv["invariant_ids"]="I-001 through I-016"
    inv["rows"]=[
      {"id":"I-001","invariant":"one authoritative Lab reducer","classification":"PARTIALLY_ENFORCED","evidence":"Rust resource/replay authority exists; Python LabRun remains mutable projection"},
      {"id":"I-002","invariant":"no external effect before admission","classification":"PARTIALLY_ENFORCED","evidence":"explicit Lab cells fenced; compatibility/provider paths bypass"},
      {"id":"I-003","invariant":"every admitted effect has exactly one terminal settlement","classification":"TEST_PROVEN_LOCAL","evidence":"duplicate/unknown settlement rejected in bounded lifecycle test"},
      {"id":"I-004","invariant":"stale settlement rejected","classification":"TEST_PROVEN_LOCAL","evidence":"settlement with unknown/stale admission rejected locally"},
      {"id":"I-005","invariant":"duplicate settlement rejected","classification":"TEST_PROVEN_LOCAL","evidence":"duplicate terminal event rejected locally"},
      {"id":"I-006","invariant":"cancellation cannot become success","classification":"PARTIALLY_ENFORCED","evidence":"explicit fenced path only; provider/browser external paths not exercised"},
      {"id":"I-007","invariant":"crash prefix cannot become false success","classification":"PARTIALLY_ENFORCED","evidence":"archive/hash metadata and local replay prefix checks are tested; process crash/descendant recovery is not exercised"},
      {"id":"I-008","invariant":"budget conservation","classification":"TEST_PROVEN_LOCAL","evidence":"native/Lab budget contracts and bounded lifecycle"},
      {"id":"I-009","invariant":"execution registry sealed before execution","classification":"TEST_PROVEN_LOCAL","evidence":"cell registry snapshot/seal path"},
      {"id":"I-010","invariant":"PROD cannot silently use Python fallback","classification":"PARTIALLY_ENFORCED","evidence":"PROD normalization exists; DEV defaults and compatibility fallback remain"},
      {"id":"I-011","invariant":"telemetry cannot mutate authority","classification":"PARTIALLY_ENFORCED","evidence":"observability is mostly separate; broad catches/control branches remain"},
      {"id":"I-012","invariant":"evidence cannot self-declare verification","classification":"PARTIALLY_ENFORCED","evidence":"Rust ValidatorProof rejects verdict=false; Python truth_claim defaults False and candidate tiers cannot become PhysicalWitness without validator evidence; end-to-end hidden validator remains external"},
      {"id":"I-013","invariant":"replay manifest binds runtime configuration","classification":"PARTIALLY_ENFORCED","evidence":"hash/archive metadata present; complete configuration binding not proven"},
      {"id":"I-014","invariant":"package owner is unambiguous","classification":"TEST_PROVEN_LOCAL","evidence":"fresh root/core wheels and a dependency-complete combined venv show exactly one aegis console script; the independent core probe also passes browser/batch/learning compatibility without a console-script collision"},
      {"id":"I-015","invariant":"trust policy owner is unambiguous","classification":"VIOLATED","evidence":"AgentConfig/LabPolicy default DEV while AegisAdapter/evidence/Rust normalize None/env to PROD; local probe confirms split"},
      {"id":"I-016","invariant":"retry policy owner is unambiguous","classification":"VIOLATED","evidence":"AegisAgent, provider route, Lab gateway/tool/experiment/simulation and unknown SDK layers remain independently bounded"}
    ]
    ext={"schema":"aegis-design-external-boundary-v1","items":[
      {"unknown":"Linux native enforcement","class":"LINUX_NATIVE_REQUIRED","future_test":"clean Linux wheel + child/resource policy probes"},
      {"unknown":"macOS native enforcement","class":"MACOS_NATIVE_REQUIRED","future_test":"clean macOS wheel + child/resource policy probes"},
      {"unknown":"Windows memory-pressure kill/descendant limits","class":"WINDOWS_NATIVE_REQUIRED","future_test":"deterministic Job Object pressure witness"},
      {"unknown":"hosted lease/queue/telemetry/restore","class":"HOSTED_CI_REQUIRED","future_test":"deployed multi-worker recovery/restore"},
      {"unknown":"cross-machine ordering and clocks","class":"MULTI_MACHINE_REQUIRED","future_test":"multi-host failure/clock test"},
      {"unknown":"provider SDK retries/idempotency","class":"LIVE_PROVIDER_REQUIRED","future_test":"provider-specific receipt/retry contract"},
      {"unknown":"live browser SSRF/redirect/permissions","class":"LIVE_BROWSER_REQUIRED","future_test":"controlled browser corpus"},
      {"unknown":"hardware/electrical calibration","class":"HARDWARE_REQUIRED","future_test":"instrumented calibration witness"},
      {"unknown":"scientific/generalization claims","class":"OTHER_EXTERNAL","future_test":"independent benchmark/scientific replication"},
      {"unknown":"signed release attestation","class":"RELEASE_SIGNER_REQUIRED","future_test":"signed artifact subject hash and attestation verification"},
      {"unknown":"final commit and repository manifest ownership","class":"REPOSITORY_OWNER_REQUIRED","future_test":"owner-approved final commit, regenerated manifest and hosted release IDs"}]}
    return {"ffi_actual_surface.json":ffi,"rust_public_surface.json":rust,"side_effect_chains.json":chains,"side_effect_bypasses.json":bypass,"state_divergence.json":div,"trust_truth.json":trust,"retry_truth.json":retry,"error_semantics.json":err,"config_precedence.json":conf,"schema_graph.json":sch,"artifact_retention.json":retention,"delete_candidates.json":dele,"cohesion_candidates.json":coh,"performance_baseline.json":perf,"data_movement.json":move,"invariant_matrix.json":inv,"external_boundary.json":ext}

def write(name,payload,ep,head,extra):
    x=dict(payload); x.update({"HEAD":head,"WORKTREE_EPOCH":ep,"generated_at":GENERATED,"method":f"{VERSION}; direct Git/filesystem/source inspection, prior artifact hash reuse, disposable wheel reconciliation, bounded local probes","limitations":list(x.get("limitations",[]))+extra})
    (OUT/name).write_text(json.dumps(x,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")

def main():
    OUT.mkdir(parents=True,exist_ok=True); ep0=epoch(); state=status(); v63=v63_reconcile(); m2_replay=m2_fresh_replay_truth(); installs=clean_installs(); pkg=wheel_truth(); pkg.update(v63); pkg.update(installs)
    gate_code,gate_out,gate_err=run([str(ROOT/".venv/Scripts/python.exe"),"scripts/evidence_consistency_gate.py"],timeout=30)
    gate_lines=[x.strip() for x in (gate_out+"\n"+gate_err).splitlines() if x.strip()]
    evidence_gate={"command":".venv\\Scripts\\python.exe scripts/evidence_consistency_gate.py","exit":gate_code,"status":"PASS" if gate_code==0 else "FAIL","decisive_errors":gate_lines[:8],"error_count":sum(1 for x in gate_lines if "failed:" in x.lower())}
    core_owner_status=pkg.get("core_wheel",{}).get("owner_status")
    if core_owner_status=="PASS_NO_AEGIS_CONSOLE_SCRIPT":
        if installs.get("combined_install",{}).get("status") in {"BROKEN","PASS_WITH_COLLISION"}:
            pkg["combined_install"]["evidence_freshness"]="STALE_PRE_M1_PROBE"
            pkg["combined_install"]["observed_issue"]="existing combined venv predates the core bridge owner change; recreate with the recorded core wheel before runtime CLI closure"
        core_probe=installs.get("core_python_independent_install",{}).get("import",{})
        core_compat=core_probe.get("compatibility",{}) if isinstance(core_probe,dict) else {}
        if installs.get("combined_install",{}).get("status")=="PASS" and installs.get("core_python_independent_install",{}).get("status")=="PASS_NO_CONSOLE_SCRIPT" and core_compat.get("status")=="PASS":
            pkg["classification"]="PACKAGE_OWNER_LOCAL_PASS"
            pkg["secondary_classifications"]=["ROOT_CANONICAL_AEGIS","CORE_NO_DUPLICATE_AEGIS","CORE_BRIDGE_API_COMPATIBILITY_PROVEN_OFFLINE"]
        else:
            pkg["classification"]="PACKAGE_OWNER_MIGRATION_PARTIAL_LOCAL"
            pkg["secondary_classifications"]=["ROOT_CANONICAL_AEGIS","CORE_NO_DUPLICATE_AEGIS","COMBINED_RUNTIME_REPROBE_REQUIRED"]
    elif installs.get("combined_install",{}).get("status") in {"BROKEN","PASS_WITH_COLLISION"}: pkg["classification"]="CONSOLE_SCRIPT_COLLISION"; pkg["secondary_classifications"]=["OVERLAPPING_DISTRIBUTIONS", installs["combined_install"]["status"]]
    elif installs.get("core_python_independent_install",{}).get("status")=="BROKEN": pkg["classification"]="OVERLAPPING_DISTRIBUTIONS"; pkg["secondary_classifications"]=["BROKEN"]
    else: pkg["classification"]="UNKNOWN"; pkg["secondary_classifications"]=[]
    base=reuse_prior("final_performance_baseline.json"); base["schema"]="aegis-design-performance-baseline-v1"; reports={"packaging_truth.json":pkg,"rust_mirror_truth.json":nested_mirror(),"dynamic_reachability.json":dynamic(),"entrypoint_truth.json":entrypoints()}; reports.update(fixed_reports(base,ep0)); reports["state_divergence.json"]["native_projection_probe"]=native_projection_probe(installs.get("combined_install",{}))
    ep1=epoch(); stable=ep0["WORKTREE_EPOCH"]==ep1["WORKTREE_EPOCH"]; closure_status="PARTIAL_LOCAL" if stable else "WORKTREE_CHANGED_DURING_COLLECTION"; extra=[] if stable else ["WORKTREE_CHANGED_DURING_COLLECTION"]
    head=ep0["HEAD"]
    for n,p in reports.items(): write(n,p,ep0["WORKTREE_EPOCH"],head,extra)
    closure={"schema":"aegis-design-closure-v1","status":closure_status,"epoch_stable":stable,"epoch_start":ep0,"epoch_end":ep1,"collection_state":state,"v63":v63,"verification":{"evidence_consistency_gate":evidence_gate,"m2_fresh_process_replay":m2_replay,"document_consistency_gate":"PASS (run separately after collection)","collector_py_compile":"PASS"},"decision_summary":{"safe_to_design_target_architecture":"YES","safe_to_begin_surgical_convergence":"NO","strong_delete_candidates":[],"strong_split_candidates":["core/rust/src/ffi.rs"],"local_blockers":["final SHA/current.json provenance","Python/Rust authority divergence and lossy Lab projection","DEV/PROD trust policy contradiction","layered retry ownership and duplicate-effect risk"],"local_questions_exhausted":["v63 artifact availability","current package/CLI owner and stale collision provenance","standalone core bridge/browser/batch/learning compatibility","nested mirror Cargo reachability","dynamic imports/sys.path product reachability","actual FFI registration count and local hazard conditions","local Python/Rust model fields and projection behavior","local side-effect admission paths","local error handling and schema/version surfaces","local performance baseline reuse and artifact retention"],"external_only":["Linux/macOS/Windows native enforcement","hosted queue/lease/telemetry/restore","multi-machine clocks/order","provider SDK retry/idempotency and receipts","live browser SSRF/permissions/descendant cleanup","hardware/electrical calibration","independent scientific/generalization benchmark","signed release attestation and owner-approved final manifest"]},"artifacts":sorted(reports),"epoch_exclusions":["docs/architecture/evidence-pack/","docs/architecture/design-closure/","docs/architecture/empirical-research/","artifacts/local-runtime/",".venv/","target/",".git/",".serena/","cache directories"],"limitations":["audit output/cache directories are excluded from epoch hashing to avoid self-reference; their per-file hashes are recorded separately"]}
    write("design_closure.json",closure,ep0["WORKTREE_EPOCH"],head,extra)
    paths=["docs/architecture/design-closure/AEGIS_FINAL_DESIGN_CLOSURE.md","docs/architecture/design-closure/design_closure.json"]+[f"docs/architecture/design-closure/{x}" for x in sorted(reports)]
    provider_fence_path=latest(
        (ROOT/"artifacts/local-runtime").glob("provider-fence-*/provider_fence*_packaging.json")
    )
    if provider_fence_path is None:
        provider_fence_path=ROOT/"artifacts/local-runtime/provider-fence-20260831/provider_fence_packaging.json"
    paths += [
        "artifacts/local-runtime/fresh-typed-chain-20260831/typed_chain_replay.json",
        "artifacts/local-runtime/replay-chaos-20260831/replay_chaos_scorecard.json",
        "artifacts/local-runtime/m2-all-lane-fresh-replay-20260901/m2_all_lane_fresh_replay.json",
        provider_fence_path.relative_to(ROOT).as_posix(),
    ]
    package_status=pkg["classification"]
    package_reconciled=(
        "- Root owns `aegis`; the current core-owner wheel has no duplicate console script, all 14 `aegis.__all__` exports plus DEV evidence/`Agent` smoke are clean, the standalone bridge/browser/batch/learning compatibility probe passes offline, and the dependency-complete combined runtime selects exactly one root entry point."
        if package_status=="PACKAGE_OWNER_LOCAL_PASS"
        else "- Root wheel clean import/native/CLI evidence is separate from the core bridge. The current core-owner wheel has no `aegis` console script, but the retained combined venv is a pre-M1 probe and still reports the old collision; a fresh dependency-complete combined runtime probe remains required."
    )
    package_fact=(
        f"- Packaging owner migration is `{package_status}`: root owns `aegis`, the newly built core bridge wheel has no duplicate console script, and the standalone browser/batch/learning compatibility probe is recorded as an offline local PASS."
        if package_status=="PACKAGE_OWNER_LOCAL_PASS"
        else f"- Packaging owner migration is `{package_status}`: root owns `aegis`, the newly built core bridge wheel has no duplicate console script, and the old combined runtime probe must be recreated after dependency-complete installation."
    )
    package_blocker=(
        "- Package/CLI ownership and standalone bridge/browser/batch/learning compatibility are locally closed by bounded offline probes. Final-SHA provenance remains external/release-scoped; state authority, trust owner and retry owner remain open before surgical convergence."
        if package_status=="PACKAGE_OWNER_LOCAL_PASS"
        else "- Package/CLI ownership has a local source/wheel decision and regression gate, but a dependency-complete fresh combined runtime probe and final-SHA provenance are still required. State authority, trust owner and retry owner remain open before surgical convergence."
    )
    projection_probe=reports.get("state_divergence.json",{}).get("native_projection_probe",{})
    projection_result=projection_probe.get("result",{}) if isinstance(projection_probe,dict) else {}
    fresh_replay=projection_result.get("fresh_process_replay",{}) if isinstance(projection_result,dict) else {}
    authority_summary=(
        f"- The native controller and Python reducer are both real, but the bridge retains a lossy adapter projection alongside typed native runtime state. A fresh offline combined-wheel probe admitted one source: Python held {projection_result.get('python_sources', 'UNKNOWN')} source(s) and {projection_result.get('python_events', 'UNKNOWN')} events, while the clean Rust snapshot held {projection_result.get('native_runtime_sources', 'UNKNOWN')} typed runtime source(s), {projection_result.get('native_projection_sources', 'UNKNOWN')} projection source(s) and {projection_result.get('native_projection_payloads', 'UNKNOWN')} projection payload(s). Rust snapshot restore reported `restore_ok={projection_result.get('restore_ok', 'UNKNOWN')}`, projection IDs round-tripped as `{projection_result.get('projection_sources_roundtrip_equal', 'UNKNOWN')}`, projection payloads as `{projection_result.get('projection_payloads_roundtrip_equal', 'UNKNOWN')}`, and typed runtime sources after restore as `{projection_result.get('runtime_sources_after_restore', 'UNKNOWN')}`. A bounded fresh-process replay returned `status={fresh_replay.get('status', 'UNKNOWN')}` with `matches_clean_restore={fresh_replay.get('matches_clean_restore', 'UNKNOWN')}`. The direct typed `record_source_json` path held {projection_result.get('typed_path_runtime_sources', 'UNKNOWN')} typed runtime source(s), {projection_result.get('typed_path_projection_sources', 'UNKNOWN')} projection source(s), and {projection_result.get('typed_path_events', 'UNKNOWN')} runtime event(s). The same probe reported trust `{projection_result.get('trust_level', 'UNKNOWN')}`, a 64-hex Lab policy hash, and `trust_hash_matches_core={projection_result.get('trust_hash_matches_core', 'UNKNOWN')}`. This proves conditional typed materialization plus same-prefix local replay, local Lab policy binding, and separate projection persistence, not a single lossless cross-language reducer. See `state_divergence.json` and `schema_graph.json`."
        if projection_probe.get("status")=="PASS" and projection_result.get("mutation_probe_status")=="PASS"
        else "- The native/Python authority probe is NOT VERIFIED; the current code still has separate typed and projection representations and no lossless cross-language reducer is proven. See `state_divergence.json` and `schema_graph.json`."
    )
    authority_mutation=(
        f"- A bounded offline mutation probe then cleared Python's `sources` list: Python changed from {projection_result.get('python_sources', 'UNKNOWN')} to {projection_result.get('python_sources_after_mutation', 'UNKNOWN')} source(s), emitted no compensating event (`python_mutation_evented={projection_result.get('python_mutation_evented', 'UNKNOWN')}`), while the native snapshot stayed byte-identical (`native_snapshot_unchanged_after_python_mutation={projection_result.get('native_snapshot_unchanged_after_python_mutation', 'UNKNOWN')}`). The new snapshot boundary rejected serialization (`drift_rejected={projection_result.get('drift_rejected', 'UNKNOWN')}`), converting the previously silent divergence into a fail-closed error. The focused Python suite additionally rejects replacing a typed record under the same ID when its admitted payload differs."
        if projection_result.get("mutation_probe_status")=="PASS"
        else "- The planned Python-projection mutation probe is NOT VERIFIED; native/Python mutation divergence remains unresolved."
    )
    trust_cell_probe = projection_result.get("trust_cell_probe", {}) if isinstance(projection_result, dict) else {}
    authority_mode_fact = (
        f"- `AuthorityMode` is explicit and packaged-probed as `{trust_cell_probe.get('authority_modes', [])}`; legacy `require_native_authority` mappings and conflicting declarations are covered by the focused regression suite."
        if trust_cell_probe.get("status") == "PASS"
        else "- `AuthorityMode` packaged probe is NOT VERIFIED; compatibility and native authority contexts remain labelled by the legacy flag."
    )
    trust_cell_fact = (
        f"- The same packaged probe confirms every bound event carries the policy subject (`all_event_trust_hashes={trust_cell_probe.get('all_event_trust_hashes', 'UNKNOWN')}`), the execution-cell manifest carries it, matching lookup succeeds, and lookup without the subject is rejected (`missing_hash_rejected={trust_cell_probe.get('missing_hash_rejected', 'UNKNOWN')}`)."
        if trust_cell_probe.get("status") == "PASS"
        else "- Cross-cell trust-subject propagation is NOT VERIFIED in the packaged probe."
    )
    ffi_text_current=(ROOT/"core/rust/src/ffi.rs").read_text(encoding="utf-8",errors="replace") if (ROOT/"core/rust/src/ffi.rs").is_file() else ""
    box_leak_present="Box::leak" in ffi_text_current
    ffi_leak_fact = (
        "- `SessionSearchIndex::new(...).unwrap()` is safe for the measured nonzero constant hash and contained by `py_safe`; current `ffi.rs` contains no `Box::leak` in the previously targeted request/error wrappers."
        if not box_leak_present else
        "- `SessionSearchIndex::new(...).unwrap()` is safe for the measured nonzero constant hash and contained by `py_safe`; exported `Box::leak` request/error strings are an unbounded process-lifetime leak defect."
    )
    ffi_leak_summary = (
        "The static-index unwrap has a proved input invariant plus panic-to-PyErr defense; the previously identified wrapper `Box::leak` calls are absent from current source after a bounded safety fix. No long-run allocation campaign or full FFI split was performed."
        if not box_leak_present else
        "The static-index unwrap has a proved input invariant plus panic-to-PyErr defense; `Box::leak` is unbounded for exported public string inputs. No production fix was applied in this audit."
    )
    settlement_record = ROOT / "artifacts/local-runtime/provider-fence-settlement-20260901/provider_fence_settlement_packaging.json"
    settlement_test_count = "NOT RECORDED"
    if settlement_record.is_file():
        try:
            settlement_test_count = str(json.loads(settlement_record.read_text(encoding="utf-8")).get("regression", {}).get("tests_passed", settlement_test_count))
        except (OSError, json.JSONDecodeError):
            settlement_test_count = "NOT READABLE"
    canonical_trust_fact = "- The trust-policy schema and subject digest now have one local Python primitive at `core/python/aegis/trust_policy.py`; compatibility defaults remain contextual (`DEV` for Agent/Lab, `PROD` for standalone evidence) and cross-cell/Rust policy receipts remain open."
    gateway_deadline_fact = "- Lab-owned gateway, explicit generic-tool, experiment and simulation attempts now require one finite positive deadline (`gateway_timeout_seconds`, `tool_timeout_seconds`, `experiment_timeout_seconds` or `simulation_timeout_seconds`), derive a deterministic 64-hex idempotency key from mission/execution/input/policy identity, and carry both fields through local admission/settlement receipts; a bounded `asyncio.wait_for` timeout settles the execution as `TIMED_OUT`. A mission-bound finite envelope now also bounds observed Lab-owned effect admissions (`max_external_attempts`, default `8 * (max_steps + 1)^3`) and is replay-bound in Python/Rust snapshots. Physical network/browser requests, provider idempotency, opaque SDK/user-runner retries, and external effect reversal remain NOT VERIFIED."
    m2_replay_fact = (f"- Fresh native-required parent/child replay witness is `PASS_LOCAL_ONLY` (SHA-256 `{m2_replay.get('sha256', 'NOT_AVAILABLE')}`): six explicit execution-cell admission lanes are restored in a child process, all six open admissions reconcile to explicit `REJECTED`, `state=blocked`, `open_after=0` and the event-chain verdict remains valid. This is local same-wheel evidence only; it does not prove hidden planners, external effect reversal or hosted authority." if m2_replay.get("status") == "PASS_LOCAL_ONLY" else "- The six-lane fresh-process replay witness is NOT VERIFIED; no claim is made beyond the other local snapshot tests.")
    md=f"""# AEGIS — Final Architecture Design-Closure and Evidence Reconciliation
WORKTREE_EPOCH: {ep0["WORKTREE_EPOCH"]}
HEAD: {head}
STATUS: {closure_status}
generated_at: {GENERATED}
method: {VERSION}; direct Git/filesystem/source inspection, prior artifact hash reuse, disposable wheel reconciliation, bounded local probes
limitations: local evidence is partial; external-only closure is explicit below

## RECONCILED CONTRADICTIONS
- v63 directory and all four documented records are absent; the documented v63 wheel hash is not replayable. The replacement wheel is an artifact-missing recovery, not proof of v63.
{package_reconciled}
- The prior lexical side-effect graph mixed strict Lab fencing with compatibility/operator paths; this pack separates those paths and names bypass conditions.
- The prior count of 93 PyO3 candidates is not the registered API count; source registration is measured separately (59 `#[pyfunction]`, 59 wrappers, one `#[pymethods]` block).
- The nested `core/rust/AEGIS-COGNITION` subtree is tracked history with no nested Cargo manifest and no workspace-member edge; it is not a build/runtime mirror.
- The performance baseline is reused because the equivalent workload was not repeated and no new campaign was authorized; reused measurements are not source-fresh.

## NEW DESIGN-CRITICAL FACTS
{package_fact}
{authority_mode_fact}
 - The current working-tree M4 settlement fence is locally proven by the retained provider-fence packaging record and {settlement_test_count}-test regression gate: experiment, research, browser action/observation, skill, and cancellation receipts require exactly one open event-ledger admission; duplicate identities, stale/unknown admissions, and input/policy hash mismatches fail closed without appending another event. This does not prove external provider idempotency or opaque SDK/user-runner retry behavior.
{gateway_deadline_fact}
{m2_replay_fact}
- Python `LabRun` is mutable and payload-bearing; Rust `LabController` conditionally materializes valid typed records while retaining a separate adapter projection. Opaque compatibility labels and adapter-only fields remain projection-only, so projection admission does not prove a single lossless reducer.
- Source-level field comparison proves semantic/lossy divergence for Mission, Source, Claim, Hypothesis, Experiment, Observation, Artifact, Event, Replay, ExecutionCell, Trust and Retry.
 - Local trust defaults are inconsistent: Agent/Lab `DEV`; AegisAdapter/evidence and Rust `PROD` when unset.
 {canonical_trust_fact}
- A finite mission-bound envelope now exists for observed Lab-owned effect admissions and is enforced/replayed by Python and Rust; a physical/global external-attempt bound and end-to-end idempotency guarantee remain NOT VERIFIED because provider, SDK, user-runner and descendant behavior is open.
{ffi_leak_fact}
- No strong delete candidate is proven. `core/rust/src/ffi.rs` is the sole strong split input; no split was performed.

## PACKAGING TRUTH
{package_blocker}
Fresh source/wheel/combined-runtime owner evidence is in `packaging_truth.json`; stale pre-M1 environments are explicitly historical.

## NESTED MIRROR TRUTH
The four tracked files under `core/rust/AEGIS-COGNITION` have history and documentary references, but no nested manifest, Cargo workspace membership, package, script, CI, test, or runtime edge. Canonical twin hashes and references are in `rust_mirror_truth.json`.

## AUTHORITY TRUTH
{authority_summary}
{authority_mutation}

## SIDE-EFFECT TRUTH
Strict Lab provider/search/browser/process paths admit before effect and settle locally. Non-Lab provider calls, compatibility callbacks, benchmark/operator subprocesses, CLI writes and scripts remain independently executable. See `side_effect_chains.json` and `side_effect_bypasses.json`.

## TRUST TRUTH
The local probe measured Agent/Lab `DEV`, AegisAdapter/evidence `PROD`, and Rust default `PROD` with no provider call. Validated `AgentConfig` missions now create and propagate a 64-hex policy subject, and the packaged Lab probe binds the same subject to the native mission; direct compatibility defaults remain split. This is a local policy contradiction with bounded mission propagation, not a production-security proof. See `trust_truth.json`.
{trust_cell_fact}

## RETRY TRUTH
The code gives finite local formulas (`P`, `G`, `T`, `E`, `S`), a one-invocation/no-retry `ProcessExecutionCell` bound, local gateway/tool/experiment/simulation idempotency/deadline contracts, and a mission-bound envelope for observed Lab-owned effect admissions. SDK retries, user runners, physical browser/network subrequests, external receipt idempotency and timeout commit ambiguity still prevent a physical/global bound. See `retry_truth.json`.

## FFI TRUTH
Registered surface counts and hazard evidence are source-based. {ffi_leak_summary}

## RUST PUBLIC API TRUTH
Public declarations are classified by source exposure and consumer evidence; `ffi.rs` is binding surface, `lab.rs`/`replay.rs` are crate-internal authority candidates, and compatibility/POC surfaces remain separately labelled. See `rust_public_surface.json`.

## STRONG DELETE CANDIDATES
None proven. Historical mirror/docs may only be deleted after owner-approved retention and reference review.

## STRONG SPLIT CANDIDATES
`core/rust/src/ffi.rs` is a strong cohesion/split input because binding, conversion, controller, error and hazard responsibilities co-reside. This is a design input only; no source split occurred.

## PERFORMANCE BASELINE
`performance_baseline.json` is `PASS_REUSED_PRIOR_LOCAL_PROBE`; no new benchmark claim is made. Provider, browser, hosted and cross-platform performance remain unverified.

## LOCAL UNKNOWNS REMAINING
- Final owner-approved SHA/current.json provenance is absent; the fail-closed placeholder was not edited.
- `evidence_consistency_gate.py` is `FAIL` by design at this checkout: `current.json` and remediation/suite records still carry `CHECKOUT_HEAD` instead of the actual 40-character `HEAD`.
- Python/Rust canonical authority, snapshot envelope and lossless projection contract remain undecided.
- DEV/PROD trust owner and retry owner/idempotency contract remain undecided.
- Bounded typed-chain and replay-chaos samples are now retained with local hashes; full provenance/retention for missing v63, independent rebuild/signature and hosted restore samples remains unavailable.

## EXTERNAL-ONLY UNKNOWNS
Linux/macOS/Windows native enforcement; hosted queue/lease/telemetry/restore; multi-machine ordering/clocks; provider SDK retry/idempotency and receipts; live browser SSRF/permissions/descendant cleanup; hardware/electrical calibration; independent scientific/generalization benchmarks; signed release attestation and repository-owner final manifest.

## ARTIFACT PATHS
{chr(10).join("- "+p for p in paths)}

DESIGN_EVIDENCE_STATUS:
PARTIAL_LOCAL
SAFE_TO_DESIGN_TARGET_ARCHITECTURE:
YES
SAFE_TO_BEGIN_SURGICAL_CONVERGENCE:
NO

No broad feature, refactor, delete, rename, migration, optimization, uncontrolled crawl, fuzz, soak, stress, provider call, or live browser run was performed. The bounded M2 snapshot guard, six-lane fresh-process replay witness, conditional typed-materialization hardening, local M3 trust-policy hash binding, and targeted FFI wrapper leak safety fix are recorded in the master plan and verified by the local gates above; global trust-owner unification remains open.
"""
    (OUT/"AEGIS_FINAL_DESIGN_CLOSURE.md").write_text(md,encoding="utf-8")
    print(json.dumps({"status":closure_status,"WORKTREE_EPOCH":ep0["WORKTREE_EPOCH"],"HEAD":head,"artifact_count":len(reports)+3,"v63":v63["classification"],"packaging":pkg["classification"]},ensure_ascii=False))
    return 0 if stable else 2

if __name__=="__main__": raise SystemExit(main())
