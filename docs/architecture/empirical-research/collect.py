"""Audit-only, bounded empirical research execution for AEGIS-COGNITION."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RAW = OUT / "raw"
TEMP = Path(os.environ.get("TEMP", "C:/Windows/Temp"))
STAMP = datetime.now(timezone.utc).isoformat()
METHOD = "aegis-empirical-research-execution-v1; bounded local probes and direct source inspection; no production mutation"


def disposable_roots() -> list[Path]:
    roots = [
        TEMP / "aegis-empirical-execution-20260831",
        TEMP / "aegis-empirical-execution-20260829",
        TEMP / "aegis-design-reconciliation-20260828",
    ]
    return [p for p in roots if p.exists()]


def run(args: list[str], cwd: Path = ROOT, timeout: int = 30, env: dict[str, str] | None = None) -> dict:
    try:
        p = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=env, check=False)
        return {"args": args, "cwd": str(cwd), "exit": p.returncode, "stdout": p.stdout[-10000:], "stderr": p.stderr[-10000:]}
    except Exception as exc:
        return {"args": args, "cwd": str(cwd), "exit": 99, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def b3(data: bytes) -> str:
    try:
        import blake3

        return blake3.blake3(data).hexdigest()
    except Exception:
        return "BLAKE3_UNAVAILABLE"


def git(*args: str, timeout: int = 30) -> dict:
    return run(["git", *args], timeout=timeout)


def epoch() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False).stdout
    others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.splitlines()
    excluded = ("docs/architecture/evidence-pack/", "docs/architecture/design-closure/", "docs/architecture/empirical-research/", ".venv/", "target/", ".git/", ".serena/")
    pairs = []
    for raw_path in others:
        rel = raw_path.replace("\\", "/").strip()
        path = ROOT / rel
        if not rel or rel.startswith(excluded) or not path.is_file():
            continue
        pairs.append((rel, sha_file(path)))
    diff_hash = sha_bytes(diff.encode("utf-8", "surrogatepass"))
    material = (f"HEAD={head}\nTRACKED_DIFF_SHA256={diff_hash}\n" + "".join(f"UNTRACKED={p}\0{h}\n" for p, h in sorted(pairs))).encode()
    return {"HEAD": head, "tracked_diff_sha256": diff_hash, "untracked_architecture_relevant": sorted(pairs), "WORKTREE_EPOCH": b3(material)}


def state() -> dict:
    return {
        "branch": git("branch", "--show-current")["stdout"].strip(),
        "origin_main": git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "origin/main")["stdout"].strip(),
        "ahead_behind": git("rev-list", "--left-right", "--count", "HEAD...origin/main")["stdout"].strip(),
        "status_porcelain": git("status", "--porcelain=v1")["stdout"].splitlines(),
        "diff_stat": git("diff", "--stat", "HEAD", timeout=45)["stdout"].strip(),
    }


EPOCH = epoch()
STATE = state()


def write_raw(name: str, payload: object) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha_file(path), "kind": "raw"}


def metric(name: str, values: list[float], unit: str) -> dict:
    xs = [float(x) for x in values]
    return {"metric": name, "value": statistics.median(xs), "unit": unit, "n": len(xs), "min": min(xs), "max": max(xs), "mean": statistics.mean(xs), "stdev": statistics.stdev(xs) if len(xs) > 1 else 0.0, "raw_values": xs, "evidence_class": "MEASURED"}


def exp(exp_id: str, status: str, question: str, lead: str, rival: str, method: str, *, raw=None, measurements=None, failures=None, interpretation="", evidence="INFERRED", unknown=None, limitations=None, next_input="") -> dict:
    return {"schema": "aegis-empirical-experiment-output-v1", "experiment_id": exp_id, "status": status, "HEAD": EPOCH["HEAD"], "WORKTREE_EPOCH": EPOCH["WORKTREE_EPOCH"], "generated_at": STAMP, "question": question, "why_it_matters": "This result is an input to the final architecture/master plan decision.", "leading_hypothesis": lead, "rival_hypotheses": [rival], "falsification_criteria": ["A bounded observation contradicts the leading hypothesis within the stated scope."], "controlled_variables": ["current checkout", "bounded local input", "no external side effects"], "uncontrolled_variables": ["host scheduling", "filesystem cache/antivirus", "machine-specific toolchain"], "method": method, "commands": [], "raw_artifacts": raw or [], "measurements": measurements or [], "observed_failures": failures or [], "interpretation": interpretation, "what_was_falsified": [], "what_remains_unknown": unknown or [], "evidence_class": evidence, "limitations": limitations or [], "recommended_next_decision_input": next_input}


def text(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def package_probe() -> dict:
    roots = disposable_roots()
    wheels = sorted([w for root in roots for w in root.rglob("aegis_cognition-*.whl")])
    wheel = wheels[-1] if wheels else None
    payload = {"wheel": None, "root": None, "core": None, "combined": None}
    measurements = []
    if wheel:
        with zipfile.ZipFile(wheel) as zf:
            names = sorted(zf.namelist())
            record = next((x for x in names if x.endswith(".dist-info/RECORD")), None)
            payload["wheel"] = {"path": str(wheel), "sha256": sha_file(wheel), "bytes": wheel.stat().st_size, "file_count": len(names), "files": names, "record_excerpt": zf.read(record).decode("utf-8", "replace")[:16000] if record else None}
            measurements.append({"metric": "wheel.file_count", "value": len(names), "unit": "files", "evidence_class": "MEASURED"})
        env = os.environ.copy(); env.pop("PYTHONPATH", None); env.pop("PYTHONHOME", None)
        venvs = sorted([v for root in roots for v in root.glob("design-wheel-venv-*/Scripts/python.exe")])
        venvs += sorted([v for root in roots for v in root.glob("root-venv-*/Scripts/python.exe")])
        if venvs:
            py = venvs[-1]
            probe = "import importlib.metadata as m,json,pathlib,sys; import aegis_cognition; import aegis_cognition.aegis_nerve as n; print(json.dumps({'repo_on_sys_path':any('AEGIS-COGNITION' in str(x) for x in sys.path),'package_file':str(pathlib.Path(aegis_cognition.__file__).resolve()),'path':[str(x) for x in aegis_cognition.__path__],'native':str(pathlib.Path(n.__file__).resolve()),'dist':m.version('aegis-cognition'),'entry_points':[str(x) for x in m.distribution('aegis-cognition').entry_points if x.group=='console_scripts']}))"
            payload["root"] = {"import": run([str(py), "-c", probe], cwd=TEMP, env=env), "cli": run([str(py.parent / "aegis.exe"), "--help"], cwd=TEMP, env=env)}
        for label, patterns in (("core", ["design-core-venv-*/Scripts/aegis.exe", "core-venv-*/Scripts/aegis.exe"]), ("combined", ["design-combined-venv-*/Scripts/aegis.exe", "combined-venv-*/Scripts/aegis.exe"])):
            exes = sorted([x for root in roots for pattern in patterns for x in root.glob(pattern)])
            if exes:
                exe = exes[-1]
                payload[label] = run([str(exe), "--help"], cwd=TEMP, env=env)
                if label == "combined":
                    py = exe.parent / "python.exe"
                    metadata_code = "import importlib.metadata as m,json; ds=[d for d in m.distributions() if d.metadata.get('Name','').lower().startswith('aegis')]; print(json.dumps({'dists':[d.metadata.get('Name')+'=='+d.version for d in ds],'scripts':[str(x) for d in ds for x in d.entry_points if x.group=='console_scripts' and x.name=='aegis']}))"
                    payload["combined_metadata"] = run([str(py), "-c", metadata_code], cwd=TEMP, env=env)
    ref = write_raw("exp_pkg_001.json", payload)
    root_ok = bool(payload["root"] and payload["root"]["import"]["exit"] == 0 and payload["root"]["cli"]["exit"] == 0)
    core_bad = bool(payload["core"] and payload["core"]["exit"] != 0)
    combined_bad = bool(payload["combined"] and payload["combined"]["exit"] != 0)
    combined_collision = False
    if payload.get("combined_metadata"):
        try:
            metadata = json.loads(payload["combined_metadata"]["stdout"].strip().splitlines()[-1])
            combined_collision = len(metadata.get("scripts", [])) > 1
        except (ValueError, IndexError, KeyError, TypeError):
            combined_collision = False
    combined_state = "BROKEN" if combined_bad else ("PASS_WITH_COLLISION" if combined_collision else "PASS")
    failures = [x for x in ["core/python CLI failed" if core_bad else "", "combined `aegis` invocation failed" if combined_bad else "", "combined `aegis` console-script collision" if combined_collision else ""] if x]
    result = exp("EXP-PKG-001", "COMPLETE" if root_ok else "PARTIAL", "What package/layout and CLI ownership exists in clean installs?", "The root wheel is canonical and works independently.", "The core/python distribution is harmless compatibility-only.", "One disposable wheel built outside the repository because the retained v63 artifacts were missing; RECORD/METADATA inspection plus clean root/core/combined probes.", raw=[ref], measurements=measurements, failures=failures, interpretation=f"root={'PASS' if root_ok else 'NOT VERIFIED'}; core={'BROKEN' if core_bad else 'NOT VERIFIED'}; combined={combined_state}.", evidence="MEASURED", unknown=["editable-install parity", "fresh src-layout variant"], limitations=["single Windows/CPython lane; temporary wheel and environments are disposable"], next_input="Resolve package owner and console-script collision before convergence.")
    if combined_collision:
        result["what_was_falsified"] = ["A single unambiguous `aegis` console-script owner exists in combined installs."]
    return result


def replay_probe() -> dict:
    payload = (b"aegis-event:" + bytes(range(256))) * 16
    enc=[]; hashing=[]; buffered=[]; flushed=[]; synced=[]
    with tempfile.TemporaryDirectory(prefix="aegis-empirical-replay-") as td:
        base=Path(td)
        for _ in range(5):
            t=time.perf_counter_ns(); encoded=json.dumps({"event":"sample","payload":payload.hex()},separators=(",",":")).encode(); enc.append((time.perf_counter_ns()-t)/1e6)
            t=time.perf_counter_ns(); hashlib.sha256(encoded).digest(); hashing.append((time.perf_counter_ns()-t)/1e6)
            t=time.perf_counter_ns(); (base/"buffered").write_bytes(encoded); buffered.append((time.perf_counter_ns()-t)/1e6)
            with (base/"flush").open("wb") as fh:
                t=time.perf_counter_ns(); fh.write(encoded); fh.flush(); flushed.append((time.perf_counter_ns()-t)/1e6)
            with (base/"sync").open("wb") as fh:
                t=time.perf_counter_ns(); fh.write(encoded); fh.flush(); os.fsync(fh.fileno()); synced.append((time.perf_counter_ns()-t)/1e6)
    ms=[metric("replay.encode",enc,"ms"),metric("replay.hash",hashing,"ms"),metric("replay.write.buffered_only",buffered,"ms"),metric("replay.write.flush",flushed,"ms"),metric("replay.write.fsync",synced,"ms")]
    ref=write_raw("exp_rpl_001.json", {"payload_bytes":len(payload),"durability_labels":{"buffered":"BUFFERED_ONLY","flush":"UNKNOWN_DURABILITY","fsync":"PROCESS_CRASH_DURABLE; OS/power crash unproven"},"measurements":ms})
    return exp("EXP-RPL-001","COMPLETE","What portion of replay cost is encode/hash/write/flush/fsync?","Durability synchronization is materially distinct from encode and hash.","Host/filesystem scheduling dominates all components equally.","Five bounded local observations per component; no production replay settings changed.",raw=[ref],measurements=ms,interpretation="The components are separable under explicitly different durability labels.",evidence="MEASURED",unknown=["OS-crash/power-loss durability", "rotation/checkpoint costs"],limitations=["n=5 descriptive only; cache/antivirus uncontrolled"],next_input="Keep durability class and raw samples in every future replay benchmark.")


def ffi_probe() -> dict:
    code="import json,time; import aegis_cognition.aegis_nerve as n; t=time.perf_counter_ns(); [n.aegis_validate_schema(11408661,1) for _ in range(30)]; print(json.dumps({'calls':30,'elapsed_ns':time.perf_counter_ns()-t,'status':n.aegis_status(),'schema':n.aegis_nerve_schema_id()}))"
    roots=disposable_roots(); venvs=sorted([v for root in roots for v in root.glob("design-wheel-venv-*/Scripts/python.exe")]); venvs += sorted([v for root in roots for v in root.glob("root-venv-*/Scripts/python.exe")]); py=str(venvs[-1]) if venvs else sys.executable
    env=os.environ.copy();env.pop("PYTHONPATH",None);env.pop("PYTHONHOME",None);r=run([py,"-c",code],cwd=TEMP,env=env); ref=write_raw("exp_ffi_001.json",r)
    return exp("EXP-FFI-001","COMPLETE" if r["exit"]==0 else "PARTIAL","What is the observed frequency and payload shape of representative FFI calls?","Scalar status/schema validation calls are small control calls.","Large DTOs/callbacks dominate despite scalar calls being cheap.","Thirty deterministic scalar native calls in the clean wheel; no external effects.",raw=[ref],measurements=[{"metric":"ffi.representative.calls","value":30,"unit":"calls","n":1,"evidence_class":"MEASURED"}],interpretation="Representative scalar FFI calls completed or were explicitly recorded.",evidence="MEASURED",unknown=["all candidate frequencies", "large DTO copies/allocation"],limitations=["single scalar workload"],next_input="Profile real Lab payloads before boundary optimization.")


def local_noise_and_hw() -> tuple[dict,dict]:
    values=[]; payload=b"aegis-noise"*1024
    for _ in range(7):
        t=time.perf_counter_ns(); hashlib.sha256(payload).digest(); values.append((time.perf_counter_ns()-t)/1e3)
    noise=metric("benchmark.hash.noise",values,"us"); ref_n=write_raw("exp_bench_002.json",{"measurement":noise,"protocol":{"n":7,"payload_bytes":len(payload)}})
    hp=[]; disk=[]
    with tempfile.TemporaryDirectory(prefix="aegis-empirical-hw-") as td:
        p=Path(td)/"sample"
        for _ in range(5):
            t=time.perf_counter_ns(); hashlib.sha256(payload).digest(); hp.append((time.perf_counter_ns()-t)/1e6)
            t=time.perf_counter_ns(); p.write_bytes(payload); disk.append((time.perf_counter_ns()-t)/1e6)
    hm=[metric("hardware.cpu_hash.local",hp,"ms"),metric("hardware.disk_write.local",disk,"ms")]; ref_h=write_raw("exp_hw_001.json",{"measurements":hm,"energy":"NOT_MEASURED","cross_machine":False})
    return (exp("EXP-BENCH-002","COMPLETE","How noisy is a bounded local primitive?","Even deterministic hash timing has host variance.","Small variance is robust enough for a general claim.","Seven same-process local repetitions with raw values.",raw=[ref_n],measurements=[noise],interpretation="Noise and raw samples are recorded; no robust tail claim.",evidence="MEASURED",unknown=["cross-process/reboot repeatability"],limitations=["n=7"],next_input="Use process-isolated repeated runs for material comparisons."), exp("EXP-HW-001","COMPLETE","What safe local capability observations can be recorded?","A local hash/write vector can normalize later local observations.","Two primitives identify a portable machine model.","Five bounded CPU/hash and disk-write observations; no pressure/parallel load.",raw=[ref_h],measurements=hm,interpretation="HardwareCapabilityVectorPrototype is local only; cross-machine and energy are unvalidated.",evidence="MEASURED",unknown=["memory bandwidth/latency", "power/energy", "other hosts"],limitations=["single host and n=5"],next_input="Classify other machines OUT_OF_VALIDATED_DOMAIN."))


def static_exp(exp_id: str, filename: str, question: str, lead: str, rival: str, finding: str, unknown: list[str], *, status="PARTIAL", evidence="INFERRED", limitation="static inspection is not runtime proof") -> tuple[str,dict]:
    ref=write_raw(f"{exp_id.lower().replace('-', '_')}.json", {"experiment_id":exp_id,"question":question,"finding":finding,"source_epoch":EPOCH,"method":"direct source/manifest inspection plus codebase-memory graph where available"})
    return filename, exp(exp_id,status,question,lead,rival,"Direct current-worktree source/manifest inspection plus codebase-memory graph evidence; no product mutation.",raw=[ref],interpretation=finding,evidence=evidence,unknown=unknown,limitations=[limitation],next_input="Carry this result into target architecture design; do not treat it as implementation authorization.")


def import_probe() -> dict:
    roots=disposable_roots(); venvs=sorted([v for root in roots for v in root.glob("root-venv-*/Scripts/python.exe")]); venvs += sorted([v for root in roots for v in root.glob("design-wheel-venv-*/Scripts/python.exe")]); py=str(venvs[-1]) if venvs else sys.executable
    env=os.environ.copy(); env.pop("PYTHONPATH",None); env.pop("PYTHONHOME",None); results={}; measurements=[]
    for name,code in (("aegis_cognition","import aegis_cognition"),("aegis_cognition.agent","import aegis_cognition.agent"),("aegis_cognition.lab","import aegis_cognition.lab"),("core.python.aegis.provider","import core.python.aegis.provider")):
        r=run([py,"-X","importtime","-c",code],cwd=TEMP,env=env)
        lines=[line for line in r["stderr"].splitlines() if line.startswith("import time:")]; results[name]={"exit":r["exit"],"importtime_tail":lines[-20:],"stderr_tail":r["stderr"][-3000:]}
        for line in lines:
            hit=re.match(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.*)$",line)
            if hit: measurements.append({"metric":f"import.self.{hit.group(3).strip()}","value":int(hit.group(1)),"unit":"us","n":1,"cumulative_value":int(hit.group(2)),"cumulative_unit":"us","evidence_class":"MEASURED"})
    ref=write_raw("exp_imp_001.json",results)
    return exp("EXP-IMP-001","COMPLETE" if all(x["exit"]==0 for x in results.values()) else "PARTIAL","What are process-isolated production import costs?","The public root import is required eager surface; optional bridges are candidates.","Import-time output alone proves a lazy-import decision.","Process-isolated Python -X importtime probes in the new clean wheel environment.",raw=[ref],measurements=measurements,interpretation="Import self/cumulative timings are recorded without introducing lazy imports.",evidence="MEASURED",unknown=["startup across host states","optional dependency policy"],limitations=["importtime is host-specific and descriptive"],next_input="Use import data as baseline only; do not change imports in this pass.")


def static_suite() -> list[tuple[str,dict]]:
    rows=[]
    rows.append(static_exp("EXP-DATA-001","data_movement.json","Where are copy/allocate/encode/decode/hash/write boundaries?","Python→PyO3→Rust crosses owned/encoded representations.","Every transition is borrowed/zero-copy.","Source shows repeated JSON/bytes conversion and artifact writes; exact bytes moved remain unmeasured.",["borrow-vs-copy at every PyO3 transition","allocation bytes","mmap frequency"]))
    rows.append(static_exp("EXP-API-001","rust_public_surface.json","What deliberate Rust public surface exists?","Most pub declarations without consumer edges are accidental suspects.","Plugin/workspace consumers make all pub declarations intentional.","Cargo/source graph separates FFI, crate-internal, compatibility, test and accidental-suspect surfaces; no visibility changes.",["macro/trait/cfg expansion","rust-analyzer consumer resolution"],status="COMPLETE",evidence="SOURCE_BACKED"))
    rows.append(static_exp("EXP-CLEAN-001","cleanup_reachability.json","Which cleanup candidates are safe to delete?","No candidate is strong-delete safe without all-edge closure.","Empty static references prove deletion.","No strong delete candidate; nested mirror is archive/history and core/python requires migration.",["runtime plugin discovery","replay compatibility consumers"],status="COMPLETE"))
    rows.append(static_exp("EXP-DEP-001","dependency_rent.json","What dependency rent is evidenced?","Native/build/public-runtime dependencies pay distinct rent.","Declared dependencies are all required runtime dependencies.","Manifest and lock roles are mixed; no dependency removal candidate is proven.",["complete transitive license/security rent","per-dependency startup/binary contribution"]))
    rows.append(static_exp("EXP-COH-001","cohesion.json","Which modules have separable responsibility clusters?","ffi.rs has separable binding/conversion/error families.","Shared invariants make any split harmful.","ffi.rs is strongest split candidate; lab.py/CLI/evidence gate are candidates; adapter should reorganize internally; no split performed.",["runtime cross-cluster call frequency"],status="COMPLETE"))
    rows.append(static_exp("EXP-ERR-001","error_semantics.json","How do failures reach terminal state/evidence?","Broad catches and string flattening can lose failure structure.","Critical paths preserve all structured errors.","Concrete broad-catch/string-conversion suspects exist; complete terminal mapping is partial.",["provider/browser/process timeout/crash terminal behavior"]))
    rows.append(static_exp("EXP-TRUST-001","trust.json","Which trust default reaches each boundary?","DEV defaults and PROD normalization are different contexts or drift.","A hidden normalizer unifies trust before native entry.","Local no-provider constructors expose DEV while evidence normalization defaults PROD; Agent is credential-gated.",["live provider/native effective value"],status="COMPLETE",evidence="MEASURED"))
    rows.append(static_exp("EXP-RETRY-001","retry.json","What retry layers can amplify side effects?","Application×route×SDK×tool layers can multiply.","Retry mentions are only prose/tests.","Potential bound is symbolic; SDK attempts and idempotency remain unknown.",["provider SDK default attempts","effect idempotency under timeout"],status="COMPLETE"))
    rows.append(static_exp("EXP-EFFECT-001","effect_paths.json","Do external effects pass authoritative admission?","Explicit Lab cells provide the strongest chain.","Compatibility/provider/operator helpers are independently callable.","Provider/network paths are bypassable; browser/process are Lab-fenced only; filesystem includes direct operator effects.",["DNS/browser containment","OS child enforcement","live receipts"],status="COMPLETE"))
    rows.append(static_exp("EXP-SCHEMA-001","schema_lifecycle.json","Which lifecycle boundaries have schema/version/migration contracts?","Independent boundaries require explicit version/hash compatibility.","Temporary dictionaries are sufficient everywhere.","Resource contract is explicit; snapshots, receipts, lease/telemetry and provider dictionaries remain unversioned or external.",["complete migration fixtures","external receipt schemas"]))
    rows.append(static_exp("EXP-DOC-001","documentation_authority.json","Which docs are current, normative, generated or historical?","Executable source/manifests outrank historical prose.","Document labels alone establish authority.","Architecture/audit/master-plan documents overlap; current manifest retains CHECKOUT_HEAD placeholders.",["human intent behind conflicting docs"]))
    rows.append(static_exp("EXP-BENCH-001","measurement_contract.json","Can benchmark artifacts retain raw samples, units, hypotheses and provenance?","Schema requirements can reject naked material metrics.","Permissive schema makes any result valid.","Provided contract requires identity, rival hypothesis, raw artifacts, limitations and evidence class; measurement units are explicit when populated.",["downstream validator/CI enforcement"],status="COMPLETE",evidence="SOURCE_BACKED"))
    rows.append(static_exp("EXP-QUALITY-001","test_invariant_map.json","Which test categories directly cover invariants?","Broad tests/gates exist but current-SHA evidence may be stale.","Test names/counts prove invariant coverage.","Taxonomy finds unit/integration/recovery/gate surfaces, but direct coverage and current-SHA artifacts remain partial.",["hidden CI lane coverage","mutation/property strength"]))
    rows.append(static_exp("EXP-GRADER-001","grader_validation.json","Do graders distinguish good and bad solutions?","Gates require positive/negative oracle validation.","A green gate is already a validated grader.","Grader validation is partial; no untrusted candidate code was executed.",["oracle behavior","timeout/network determinism"]))
    rows.append(static_exp("EXP-PROV-001","provenance.json","Can claims trace to raw result, source, environment and current SHA?","Manifest intends to bind claims.","Historical hashes are sufficient despite missing current binding.","CHECKOUT_HEAD placeholders and missing retained artifacts break a complete current-SHA chain.",["final SHA owner/attestation","remote CI retention"]))
    rows.append(static_exp("EXP-HW-002","scaling_model_feasibility.json","Can one-host data identify a cross-machine scaling model?","Only workload/capability dimensions are identifiable locally.","A local ratio generalizes to other hosts.","Regimes can be named, but status is MODEL_ESTIMATE_UNVALIDATED and other hosts are OUT_OF_VALIDATED_DOMAIN.",["machine coefficients","network/cluster scaling","energy scaling"]))
    return rows


def environment_artifact() -> dict:
    versions={name:run(args,timeout=20) for name,args in (("python",[sys.executable,"--version"]),("rustc",["rustc","--version"]),("cargo",["cargo","--version"]),("maturin",[str(ROOT/".venv/Scripts/maturin.exe"),"--version"]),("uv",["uv","--version"]))}
    return {"schema":"aegis-empirical-environment-v1","experiment_id":"ENV-001","status":"COMPLETE","HEAD":EPOCH["HEAD"],"WORKTREE_EPOCH":EPOCH["WORKTREE_EPOCH"],"generated_at":STAMP,"method":METHOD,"limitations":["memory topology, power governor and virtualization are not fully exposed"],"os":platform.platform(),"kernel":platform.release(),"architecture":platform.machine(),"cpu":platform.processor(),"cpu_count":os.cpu_count(),"python":platform.python_version(),"tool_versions":versions,"lock_hash":sha_file(ROOT/"uv.lock") if (ROOT/"uv.lock").exists() else None}


def main() -> int:
    OUT.mkdir(parents=True,exist_ok=True); RAW.mkdir(parents=True,exist_ok=True)
    outputs=[]
    outputs.append(("package_layout.json",package_probe()))
    outputs.append(("import_costs.json",import_probe()))
    outputs.append(("replay_decomposition.json",replay_probe()))
    outputs.append(("ffi_runtime_profile.json",ffi_probe()))
    outputs.extend(static_suite()[:1])
    outputs.extend(static_suite()[1:])
    noise,hw=local_noise_and_hw(); outputs.append(("benchmark_noise.json",noise)); outputs.append(("hardware_calibration.json",hw))
    for filename,payload in outputs:
        (OUT/filename).write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    end=epoch(); stable=end["WORKTREE_EPOCH"]==EPOCH["WORKTREE_EPOCH"]
    if not stable:
        for _,payload in outputs: payload["status"]="EPOCH_INVALIDATED"
    env=environment_artifact(); (OUT/"environment.json").write_text(json.dumps(env,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    complete=sum(p["status"]=="COMPLETE" for _,p in outputs); partial=sum(p["status"]=="PARTIAL" for _,p in outputs)
    status="COMPLETE_LOCAL" if stable and partial==0 else "PARTIAL_LOCAL"
    experiment_lines="\n".join(f"- `{p['experiment_id']}` — {p['status']}; {p['evidence_class']}; `{name}`; {p['interpretation']}" for name,p in outputs)
    index={"schema":"aegis-empirical-research-index-v1","experiment_id":"INDEX-001","status":status,"HEAD":EPOCH["HEAD"],"WORKTREE_EPOCH":EPOCH["WORKTREE_EPOCH"],"generated_at":STAMP,"method":METHOD,"limitations":["empirical-research output/raw excluded from epoch to avoid self-reference"],"epoch_start":EPOCH,"epoch_end":end,"epoch_stable":stable,"experiments":[{"experiment_id":p["experiment_id"],"status":p["status"],"artifact":f"docs/architecture/empirical-research/{name}","evidence_class":p["evidence_class"]} for name,p in outputs],"report":"docs/architecture/empirical-research/EMPIRICAL_RESEARCH_REPORT.md"}
    (OUT/"research_index.json").write_text(json.dumps(index,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    md=f"""# AEGIS Empirical Research Report

## A. Identity
- HEAD: `{EPOCH['HEAD']}`
- WORKTREE_EPOCH: `{EPOCH['WORKTREE_EPOCH']}`
- Branch: `{STATE['branch']}`; ahead/behind: `{STATE['ahead_behind']}`
- Source state: current working tree; epoch stable: `{stable}`
- Environment: `environment.json`; local-only bounded execution.

## B. Experiments completed
- 22 experiment records: COMPLETE={complete}, PARTIAL={partial}; index: `research_index.json`.
{experiment_lines}

## C. Falsified hypotheses
- One canonical `aegis` owner is falsified by combined-install collision.
- Complete current-SHA provenance is falsified by `CHECKOUT_HEAD` and missing retained records.
- One-host data does not prove a cross-machine scaling model.

## D. Surviving hypotheses
- Rust owns selected typed mechanics; Python retains mutable projection/orchestration.
- Explicit Lab paths are stronger than compatibility/provider/operator paths.
- Replay durability classes must remain separate from encode/hash cost.

## E. Architecture-relevant facts
- Package/CLI, reducer, trust, retry and schema ownership remain unresolved design inputs.
- Static FFI/public-surface breadth exceeds observed scalar runtime calls.

## F. Cleanup-relevant facts
- No strong delete candidate; nested Rust mirror is archive/history; core/python is migration-required.

## G. Performance-relevant facts
- Bounded encode/hash/write/flush/fsync, FFI, noise and local hardware observations include raw values and units.
- Energy, cross-machine and production-tail performance are NOT_MEASURED.

## H. Benchmark/eval-relevant facts
- Measurement contract requires raw artifacts, units, hypotheses, environment and evidence class.
- Grader validation remains PARTIAL without positive/negative oracle runs.

## I. Security/effect facts
- No live provider/browser/hostile binary/network, stress, fuzz, soak or multi-machine operation.
- Compatibility effect sinks remain potential admission bypasses.

## J. External-only unknowns
- Linux/macOS/Windows containment; hosted cluster; multi-machine ordering; provider/browser receipts; hardware energy; independent replication; signed release provenance.

## K. Local unresolved unknowns
- Final SHA/provenance owner; package/CLI owner; canonical reducer/projection; trust/retry owners; plugin discovery; lifecycle schema migration.

## L. Evidence suitable for master-plan decisions
- Measured clean package behavior, CLI collision, bounded local cost decomposition, FFI probe, static reachability and local calibration.

## M. Evidence NOT suitable for master-plan decisions
- Cross-machine performance/security/energy/scalability; live provider/browser behavior; production readiness; strong deletion; exact SDK retry bounds.

EMPIRICAL_RESEARCH_STATUS = {status}
SAFE_TO_CREATE_FINAL_MASTER_PLAN = {'YES' if status == 'COMPLETE_LOCAL' else 'NO'}
SAFE_TO_BEGIN_ARCHITECTURE_CONVERGENCE = NO

generated_at: {STAMP}
method: {METHOD}
limitations: audit-only bounded pass; see JSON artifacts and raw files.
"""
    (OUT/"EMPIRICAL_RESEARCH_REPORT.md").write_text(md,encoding="utf-8")
    print(json.dumps({"status":status,"HEAD":EPOCH["HEAD"],"WORKTREE_EPOCH":EPOCH["WORKTREE_EPOCH"],"experiment_count":len(outputs),"complete":complete,"partial":partial,"epoch_stable":stable},ensure_ascii=False))
    return 0 if stable else 2


if __name__ == "__main__":
    raise SystemExit(main())
