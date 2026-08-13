import json
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from core.python import build_message, build_operator_evidence_snapshot, build_service_manifest, check_build_preflight, validate_bridge_batch, validate_bridge_smoke
from scripts.benchmark_gate import evaluate_benchmarks, report_to_dict as benchmark_report_to_dict
from scripts.benchmark_gate import BENCHMARK_PROFILE_INNOVATION, BENCHMARK_PROFILE_RELEASE
from scripts.cluster_loopback_gate import evaluate_cluster_loopback_gate
from scripts.constitution_audit import evaluate_constitution
from scripts.deployment_manifest import build_deployment_manifest
from scripts.e2e_release_gate import evaluate_e2e_release_gate
from scripts.dependency_audit_gate import evaluate_dependency_audit_gate
from scripts.dynamic_provider_fallback_gate import evaluate_dynamic_provider_fallback_gate
from scripts.external_deployment_smoke_gate import evaluate_external_deployment_smoke_gate
from scripts.provider_route_gate import evaluate_provider_route_gate
from scripts.progress_gate import evaluate_progress_gate
from scripts.production_closure import build_production_closure_workflow
from scripts.production_readiness import (
    build_production_readiness_report,
    production_blocker_closure_packet as _production_blocker_closure_packet,
    production_blocker_closure_packets as _production_blocker_closure_packets,
)
from scripts.production_packaging_smoke_gate import evaluate_production_packaging_smoke_gate
from scripts.quickjs_cold_start_gate import evaluate_quickjs_cold_start_gate
from scripts.shadow_sealer_soak_gate import evaluate_shadow_sealer_soak_gate
from scripts.supply_chain_gate import evaluate_supply_chain_gate
from scripts.tcp_cluster_soak_gate import evaluate_tcp_cluster_soak_gate
from scripts.hermes_baseline_gate import evaluate_hermes_baseline, report_to_dict as hermes_baseline_report_to_dict
from scripts.hermes_persistence_baseline_gate import (
    evaluate_hermes_persistence_baseline,
    report_to_dict as hermes_persistence_baseline_report_to_dict,
)
from scripts.hermes_rpc_baseline_gate import (
    evaluate_hermes_rpc_baseline,
    report_to_dict as hermes_rpc_baseline_report_to_dict,
)
from scripts.hermes_session_recovery_baseline_gate import (
    evaluate_hermes_session_recovery_baseline,
    report_to_dict as hermes_session_recovery_baseline_report_to_dict,
)
from scripts.python_hotpath_gate import evaluate_python_hotpaths, report_to_dict as python_hotpath_report_to_dict
from scripts.governance_gate import evaluate_governance
from scripts.hot_browser_shadow_gate import evaluate_hot_browser_shadow_gate
from scripts.sota_baseline_gate import evaluate_sota_baseline, report_to_dict as sota_baseline_report_to_dict


ROOT = str(ROOT_PATH)
ARTIFACTS_DIR = Path(ROOT) / 'artifacts'
REPORT_PATH = ARTIFACTS_DIR / 'run_checks_report.json'
FORCE_FRESH_REPORT_PATH = ARTIFACTS_DIR / 'last_run_checks_force_fresh.json'
BENCHMARK_MANIFEST_PATH = ARTIFACTS_DIR / '.benchmark_manifest.json'
CLI_REPORT_MANIFEST_PATH = ARTIFACTS_DIR / '.cli_report_manifest.json'
BASELINE_GATE_CACHE_PATH = ARTIFACTS_DIR / '.baseline_gate_cache.json'
REPLAY_CHAOS_SCORECARD_PATH = ARTIFACTS_DIR / 'replay_chaos_scorecard.json'
REPLAY_ENDURANCE_REPORT_PATH = ARTIFACTS_DIR / 'replay_endurance_report.json'
BROWSER_OPS_BENCH_VERIFICATION_REPORT_PATH = ARTIFACTS_DIR / 'browser_ops_bench_verification_report.json'
AGENTIC_SDK_CONTEXT_REPORT_PATH = ARTIFACTS_DIR / 'agentic_sdk_context_report.json'
CLUSTER_LOOPBACK_REPORT_PATH = ARTIFACTS_DIR / 'cluster_loopback_report.json'
QUICKJS_COLD_START_REPORT_PATH = ARTIFACTS_DIR / 'quickjs_cold_start_report.json'
TCP_CLUSTER_SOAK_REPORT_PATH = ARTIFACTS_DIR / 'tcp_cluster_soak_report.json'
HOT_BROWSER_SHADOW_REPORT_PATH = ARTIFACTS_DIR / 'hot_browser_shadow_report.json'
SHADOW_SEALER_SOAK_REPORT_PATH = ARTIFACTS_DIR / 'shadow_sealer_soak_report.json'
DYNAMIC_PROVIDER_FALLBACK_REPORT_PATH = ARTIFACTS_DIR / 'dynamic_provider_fallback_report.json'
E2E_RELEASE_GATE_REPORT_PATH = ARTIFACTS_DIR / 'e2e_release_gate_report.json'
PROVIDER_ROUTE_GATE_REPORT_PATH = ARTIFACTS_DIR / 'provider_route_gate_report.json'
DEPENDENCY_AUDIT_GATE_REPORT_PATH = ARTIFACTS_DIR / 'dependency_audit_gate_report.json'
SUPPLY_CHAIN_GATE_REPORT_PATH = ARTIFACTS_DIR / 'supply_chain_gate_report.json'
CLUSTER_LOOPBACK_GATE_REPORT_PATH = ARTIFACTS_DIR / 'cluster_loopback_gate_report.json'
QUICKJS_COLD_START_GATE_REPORT_PATH = ARTIFACTS_DIR / 'quickjs_cold_start_gate_report.json'
TCP_CLUSTER_SOAK_GATE_REPORT_PATH = ARTIFACTS_DIR / 'tcp_cluster_soak_gate_report.json'
HOT_BROWSER_SHADOW_GATE_REPORT_PATH = ARTIFACTS_DIR / 'hot_browser_shadow_gate_report.json'
SHADOW_SEALER_SOAK_GATE_REPORT_PATH = ARTIFACTS_DIR / 'shadow_sealer_soak_gate_report.json'
DYNAMIC_PROVIDER_FALLBACK_GATE_REPORT_PATH = ARTIFACTS_DIR / 'dynamic_provider_fallback_gate_report.json'
PRODUCTION_PACKAGING_SMOKE_GATE_REPORT_PATH = ARTIFACTS_DIR / 'production_packaging_smoke_gate_report.json'
EXTERNAL_DEPLOYMENT_SMOKE_GATE_REPORT_PATH = ARTIFACTS_DIR / 'external_deployment_smoke_gate_report.json'
DEPLOYMENT_MANIFEST_REPORT_PATH = ARTIFACTS_DIR / 'deployment_manifest_report.json'
PRODUCTION_READINESS_REPORT_PATH = ARTIFACTS_DIR / 'production_readiness_report.json'
PRODUCTION_CLOSURE_WORKFLOW_REPORT_PATH = ARTIFACTS_DIR / 'production_closure_workflow_report.json'
PROGRESS_GATE_REPORT_PATH = ARTIFACTS_DIR / 'progress_gate_report.json'
BENCHMARK_COMMAND = [
    'cargo',
    'bench',
    '--bench',
    'nerve_bench',
    '--',
    '--sample-size',
    '10',
    '--measurement-time',
    '1',
    '--warm-up-time',
    '1',
]
REPLAY_CHAOS_SCORECARD_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'replay-chaos-scorecard',
]
REPLAY_ENDURANCE_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'replay-endurance-report',
]
BROWSER_OPS_BENCH_VERIFY_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'browser-ops-bench-verify',
]
AGENTIC_SDK_CONTEXT_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'agentic-sdk-context-report',
]
CLUSTER_LOOPBACK_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'cluster-loopback-report',
]
QUICKJS_COLD_START_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'quickjs-cold-start-report',
]
TCP_CLUSTER_SOAK_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'tcp-cluster-soak-report',
]
HOT_BROWSER_SHADOW_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'hot-browser-shadow-report',
]
SHADOW_SEALER_SOAK_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'shadow-sealer-soak-report',
]
DYNAMIC_PROVIDER_FALLBACK_REPORT_COMMAND = [
    'cargo',
    'run',
    '--quiet',
    '--bin',
    'aegis-nerve-cli',
    '--',
    'dynamic-provider-fallback-report',
]


def criterion_artifacts_present(root: str) -> bool:
    return (Path(root) / 'target' / 'criterion').exists()


def _benchmark_inputs(root: Path) -> list[Path]:
    rust_root = root / 'core' / 'rust'
    inputs = list((rust_root / 'src').rglob('*.rs'))
    inputs.extend((rust_root / 'benches').rglob('*.rs'))
    inputs.extend([
        rust_root / 'Cargo.toml',
        root / 'Cargo.lock',
    ])
    return sorted(path for path in inputs if path.exists())


def _cli_report_inputs(root: Path) -> list[Path]:
    rust_root = root / 'core' / 'rust'
    inputs = list((rust_root / 'src').rglob('*.rs'))
    inputs.extend([
        rust_root / 'Cargo.toml',
        root / 'Cargo.toml',
        root / 'Cargo.lock',
        root / 'scripts' / 'cluster_loopback_gate.py',
        root / 'scripts' / 'quickjs_cold_start_gate.py',
        root / 'scripts' / 'tcp_cluster_soak_gate.py',
        root / 'scripts' / 'hot_browser_shadow_gate.py',
        root / 'scripts' / 'shadow_sealer_soak_gate.py',
        root / 'scripts' / 'dynamic_provider_fallback_gate.py',
        root / 'scripts' / 'run_checks.py',
    ])
    return sorted(path for path in inputs if path.exists())


def _baseline_gate_inputs(root: Path) -> list[Path]:
    inputs = [
        root / 'scripts' / 'sota_baseline_gate.py',
        root / 'scripts' / 'hermes_baseline_gate.py',
        root / 'scripts' / 'hermes_rpc_baseline_gate.py',
        root / 'scripts' / 'hermes_session_recovery_baseline_gate.py',
        root / 'scripts' / 'hermes_persistence_baseline_gate.py',
        root / 'scripts' / 'run_checks.py',
    ]
    criterion_root = root / 'target' / 'criterion'
    if criterion_root.exists():
        inputs.extend(criterion_root.rglob('estimates.json'))
    return sorted(path for path in inputs if path.exists())


def _benchmark_manifest_payload(root: Path) -> bytes:
    chunks: list[bytes] = [b'AEGIS-BENCHMARK-MANIFEST-v1\0']
    for path in _benchmark_inputs(root):
        relative = path.relative_to(root).as_posix().encode('utf-8')
        chunks.extend([relative, b'\0', path.read_bytes(), b'\0'])
    return b''.join(chunks)


def _hashed_file_payload(root: Path, paths: list[Path], label: bytes) -> bytes:
    chunks: list[bytes] = [label, b'\0']
    for path in paths:
        relative = path.relative_to(root).as_posix().encode('utf-8')
        chunks.extend([relative, b'\0', path.read_bytes(), b'\0'])
    return b''.join(chunks)


def _blake3_hex_with_rust(root: Path, payload: bytes) -> str:
    result = subprocess.run(
        ['cargo', 'run', '--quiet', '--bin', 'aegis-nerve-cli', '--', 'blake3-stdin'],
        cwd=root / 'core' / 'rust',
        input=payload,
        capture_output=True,
        env=_cargo_env(),
        check=True,
    )
    return result.stdout.decode('utf-8').strip()


def _cargo_env() -> dict:
    env = os.environ.copy()
    py_root = Path(sys.executable).parent
    env['PYO3_PYTHON'] = sys.executable
    path_parts = [
        str(py_root),
        str(py_root / 'DLLs'),
        str(py_root / 'libs'),
        env.get('PATH', ''),
    ]
    env['PATH'] = os.pathsep.join(path_parts)
    env.setdefault('RUSTFLAGS', '-C debuginfo=0')
    return env


def benchmark_content_hash(root: str | Path) -> str:
    root_path = Path(root)
    payload = _benchmark_manifest_payload(root_path)
    try:
        import blake3  # type: ignore
    except ImportError:
        return _blake3_hex_with_rust(root_path, payload)
    return blake3.blake3(payload).hexdigest()


def cli_report_content_hash(root: str | Path) -> str:
    root_path = Path(root)
    payload = _hashed_file_payload(root_path, _cli_report_inputs(root_path), b'AEGIS-CLI-REPORT-MANIFEST-v1')
    return hashlib.sha256(payload).hexdigest()


def baseline_gate_content_hash(root: str | Path, benchmark_profile: str) -> str:
    root_path = Path(root)
    label = f'AEGIS-BASELINE-GATE-CACHE-v1:{benchmark_profile}'.encode('utf-8')
    payload = _hashed_file_payload(root_path, _baseline_gate_inputs(root_path), label)
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ''
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _file_blake3_hex(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        import blake3  # type: ignore
    except ImportError:
        return _blake3_hex_with_rust(ROOT_PATH, path.read_bytes())
    hasher = blake3.blake3()
    with path.open('rb') as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_benchmark_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _read_cli_report_manifest(path: Path = CLI_REPORT_MANIFEST_PATH) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_baseline_gate_cache(path: Path = BASELINE_GATE_CACHE_PATH) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cli_report_manifest(payload: dict, path: Path = CLI_REPORT_MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')


def _write_baseline_gate_cache(payload: dict, path: Path = BASELINE_GATE_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')


def _stable_payload_sha256(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _production_readiness_summary(deployment: object, e2e_release_gate: dict) -> dict:
    return build_production_readiness_report(deployment, e2e_release_gate)


def _baseline_gate_payload_valid(payload: dict) -> bool:
    checks = payload.get('checks')
    physical_evidence = payload.get('physical_evidence')
    return (
        payload.get('overall_ok') is True
        and payload.get('failed') == 0
        and isinstance(checks, list)
        and len(checks) > 0
        and all(isinstance(check, dict) and check.get('ok') is True for check in checks)
        and isinstance(physical_evidence, dict)
        and len(physical_evidence) > 0
    )


def _with_baseline_cache_fields(
    payload: dict,
    gate_name: str,
    content_hash: str,
    cache_hit: bool,
    validated_existing_gate: bool,
) -> dict:
    result = dict(payload)
    result.update(
        {
            'baseline_cache_gate_name': gate_name,
            'baseline_cache_hit': cache_hit,
            'validated_existing_gate': validated_existing_gate,
            'baseline_cache_content_hash_sha256': content_hash,
            'baseline_payload_sha256': _stable_payload_sha256(payload),
        }
    )
    return result


def _update_baseline_gate_cache(
    root_path: Path,
    gate_name: str,
    payload: dict,
    content_hash: str,
    benchmark_content_hash_value: str,
    benchmark_profile: str,
) -> None:
    cache = _read_baseline_gate_cache()
    if (
        cache.get('content_hash_sha256') == content_hash
        and cache.get('benchmark_content_hash_blake3') == benchmark_content_hash_value
        and cache.get('benchmark_profile') == benchmark_profile
        and isinstance(cache.get('gates'), dict)
    ):
        gates = cache['gates']
    else:
        gates = {}
    gates[gate_name] = {
        'payload': payload,
        'payload_sha256': _stable_payload_sha256(payload),
    }
    _write_baseline_gate_cache(
        {
            'schema': 'aegis-baseline-gate-cache-v1',
            'content_hash_sha256': content_hash,
            'benchmark_content_hash_blake3': benchmark_content_hash_value,
            'benchmark_profile': benchmark_profile,
            'hash_scope': [path.relative_to(root_path).as_posix() for path in _baseline_gate_inputs(root_path)],
            'gates': gates,
        }
    )


def _cached_or_evaluate_baseline_gate(
    root: str | Path,
    gate_name: str,
    evaluator,
    serializer,
    content_hash: str,
    benchmark_content_hash_value: str,
    benchmark_profile: str,
    force_fresh: bool,
) -> tuple[dict, bool]:
    root_path = Path(root)
    if not force_fresh:
        cache = _read_baseline_gate_cache()
        gates = cache.get('gates') if isinstance(cache.get('gates'), dict) else {}
        entry = gates.get(gate_name) if isinstance(gates, dict) else None
        payload = entry.get('payload') if isinstance(entry, dict) else None
        payload_sha256 = entry.get('payload_sha256') if isinstance(entry, dict) else ''
        cache_matches = (
            cache.get('schema') == 'aegis-baseline-gate-cache-v1'
            and cache.get('content_hash_sha256') == content_hash
            and cache.get('benchmark_content_hash_blake3') == benchmark_content_hash_value
            and cache.get('benchmark_profile') == benchmark_profile
            and isinstance(payload, dict)
            and payload_sha256 == _stable_payload_sha256(payload)
            and _baseline_gate_payload_valid(payload)
        )
        if cache_matches:
            return _with_baseline_cache_fields(payload, gate_name, content_hash, True, True), True

    report = evaluator(root_path)
    payload = serializer(report)
    if _baseline_gate_payload_valid(payload):
        _update_baseline_gate_cache(
            root_path,
            gate_name,
            payload,
            content_hash,
            benchmark_content_hash_value,
            benchmark_profile,
        )
    return (
        _with_baseline_cache_fields(payload, gate_name, content_hash, False, False),
        bool(getattr(report, 'overall_ok', payload.get('overall_ok') is True)),
    )


def _update_cli_report_manifest(
    root_path: Path,
    artifact_path: Path,
    content_hash: str,
    command: list[str],
    artifact_hash_blake3: str | None,
) -> None:
    manifest = _read_cli_report_manifest()
    if manifest.get('content_hash_sha256') == content_hash and isinstance(manifest.get('artifacts'), dict):
        artifacts = manifest['artifacts']
    else:
        artifacts = {}
    artifacts[artifact_path.name] = {
        'artifact_hash_blake3': artifact_hash_blake3 or '',
        'artifact_sha256': _file_sha256(artifact_path),
        'artifact_path': str(artifact_path),
        'command': command,
        'content_hash_sha256': content_hash,
        'validated_existing_artifact_required': True,
    }
    manifest = {
        'schema': 'aegis-cli-report-cache-manifest-v1',
        'content_hash_sha256': content_hash,
        'hash_scope': [path.relative_to(root_path).as_posix() for path in _cli_report_inputs(root_path)],
        'artifacts': artifacts,
    }
    _write_cli_report_manifest(manifest)


def _write_benchmark_manifest(path: Path, content_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'content_hash_blake3': content_hash,
        'hash_scope': ['core/rust/src', 'core/rust/benches', 'core/rust/Cargo.toml', 'Cargo.lock'],
        'benchmark_command': BENCHMARK_COMMAND,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')


def _remove_benchmark_manifest(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _parse_benchmark_profile(args: list[str]) -> str:
    for index, arg in enumerate(args):
        if arg == '--benchmark-profile' and index + 1 < len(args):
            profile = args[index + 1]
            break
        if arg.startswith('--benchmark-profile='):
            profile = arg.split('=', 1)[1]
            break
    else:
        profile = BENCHMARK_PROFILE_RELEASE
    if profile not in {BENCHMARK_PROFILE_RELEASE, BENCHMARK_PROFILE_INNOVATION}:
        raise ValueError(f"unsupported benchmark profile: {profile}")
    return profile


def ensure_fresh_benchmarks(root: str | Path, force_fresh: bool, benchmark_profile: str) -> dict:
    root_path = Path(root)
    content_hash = benchmark_content_hash(root_path)
    previous_manifest = _read_benchmark_manifest(BENCHMARK_MANIFEST_PATH)
    artifacts_present = criterion_artifacts_present(str(root_path))
    manifest_hash_matches = (
        previous_manifest is not None
        and previous_manifest.get('content_hash_blake3') == content_hash
    )
    pre_benchmark_gate_ok = False
    if artifacts_present:
        pre_benchmark_gate_ok = evaluate_benchmarks(root_path, profile=benchmark_profile).overall_ok
    cache_hit = (
        not force_fresh
        and artifacts_present
        and manifest_hash_matches
        and pre_benchmark_gate_ok
    )

    state = {
        'content_hash_blake3': content_hash,
        'manifest_hash_matches': manifest_hash_matches,
        'pre_benchmark_gate_ok': pre_benchmark_gate_ok,
        'cache_hit': cache_hit,
        'validated_existing_artifacts': artifacts_present and pre_benchmark_gate_ok,
        'stale_content_hash': artifacts_present and previous_manifest is not None and not manifest_hash_matches,
        'force_fresh': force_fresh,
        'benchmark_profile': benchmark_profile,
        'refresh_required_for_manifest_update': (
            artifacts_present
            and previous_manifest is not None
            and not manifest_hash_matches
            and not force_fresh
        ),
        'manifest_path': str(BENCHMARK_MANIFEST_PATH),
        'ran_benchmark': False,
        'benchmark_command': BENCHMARK_COMMAND,
    }
    if cache_hit:
        return state
    if not force_fresh:
        if artifacts_present and manifest_hash_matches and not pre_benchmark_gate_ok:
            state['invalid_benchmark_manifest_removed'] = _remove_benchmark_manifest(
                BENCHMARK_MANIFEST_PATH
            )
        return state
    if artifacts_present and manifest_hash_matches and not pre_benchmark_gate_ok:
        state['invalid_benchmark_manifest_removed'] = _remove_benchmark_manifest(
            BENCHMARK_MANIFEST_PATH
        )

    result = subprocess.run(
        BENCHMARK_COMMAND,
        cwd=root_path / 'core' / 'rust',
        capture_output=True,
        text=True,
        env=_cargo_env(),
    )
    state['ran_benchmark'] = True
    state['returncode'] = result.returncode
    state['stdout_tail'] = result.stdout[-4000:]
    state['stderr_tail'] = result.stderr[-4000:]
    if result.returncode == 0:
        post_benchmark_gate_ok = evaluate_benchmarks(root_path, profile=benchmark_profile).overall_ok
        state['post_benchmark_gate_ok'] = post_benchmark_gate_ok
        if post_benchmark_gate_ok:
            _write_benchmark_manifest(BENCHMARK_MANIFEST_PATH, content_hash)
        else:
            state['invalid_benchmark_manifest_removed_after_run'] = _remove_benchmark_manifest(
                BENCHMARK_MANIFEST_PATH
            )
    return state


def _parse_scorecard_hash(stdout: str) -> str | None:
    marker = 'replay_chaos_scorecard_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_endurance_report_hash(stdout: str) -> str | None:
    marker = 'replay_endurance_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_browser_ops_bench_verification_report_hash(stdout: str) -> str | None:
    marker = 'browser_ops_bench_verification_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_agentic_sdk_context_report_hash(stdout: str) -> str | None:
    marker = 'agentic_sdk_context_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_cluster_loopback_report_hash(stdout: str) -> str | None:
    marker = 'cluster_loopback_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_quickjs_cold_start_report_hash(stdout: str) -> str | None:
    marker = 'quickjs_cold_start_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_tcp_cluster_soak_report_hash(stdout: str) -> str | None:
    marker = 'tcp_cluster_soak_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_hot_browser_shadow_report_hash(stdout: str) -> str | None:
    marker = 'hot_browser_shadow_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_shadow_sealer_soak_report_hash(stdout: str) -> str | None:
    marker = 'shadow_sealer_soak_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _parse_dynamic_provider_fallback_report_hash(stdout: str) -> str | None:
    marker = 'dynamic_provider_fallback_report_hash='
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):].strip()
    return None


def _validation_passed(validation: dict) -> bool:
    return validation.get('overall_ok') is True or validation.get('ok') is True


def _cached_or_generate_cli_report(
    root: str | Path,
    artifact_path: Path,
    command_prefix: list[str],
    parse_artifact_hash,
    validator,
    force_fresh: bool = False,
    content_hash: str | None = None,
    command: list[str] | None = None,
    extra_fields: dict | None = None,
) -> dict:
    root_path = Path(root)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    command = command or [*command_prefix, str(artifact_path)]
    content_hash = content_hash or cli_report_content_hash(root_path)
    extra_fields = extra_fields or {}

    if not force_fresh:
        manifest = _read_cli_report_manifest()
        artifacts = manifest.get('artifacts') if isinstance(manifest.get('artifacts'), dict) else {}
        entry = artifacts.get(artifact_path.name) if isinstance(artifacts, dict) else None
        artifact_sha256 = _file_sha256(artifact_path)
        manifest_matches = (
            isinstance(entry, dict)
            and manifest.get('schema') == 'aegis-cli-report-cache-manifest-v1'
            and manifest.get('content_hash_sha256') == content_hash
            and entry.get('content_hash_sha256') == content_hash
            and entry.get('artifact_sha256') == artifact_sha256
            and bool(artifact_sha256)
        )
        if manifest_matches:
            validation = validator(root_path)
            if _validation_passed(validation):
                result_payload = {
                    'overall_ok': True,
                    'artifact_path': str(artifact_path),
                    'artifact_hash_blake3': entry.get('artifact_hash_blake3') or _file_blake3_hex(artifact_path),
                    'artifact_sha256': artifact_sha256,
                    'cache_hit': True,
                    'validated_existing_artifact': True,
                    'content_hash_sha256': content_hash,
                    'command': command,
                    'returncode': 0,
                    'stdout_tail': '',
                    'stderr_tail': '',
                    'validation': validation,
                    'artifact': validation.get('artifact'),
                }
                result_payload.update(extra_fields)
                return result_payload

    result = subprocess.run(
        command,
        cwd=root_path,
        capture_output=True,
        text=True,
        env=_cargo_env(),
    )
    validation = validator(root_path)
    overall_ok = result.returncode == 0 and _validation_passed(validation)
    artifact_hash_blake3 = parse_artifact_hash(result.stdout)
    if overall_ok:
        _update_cli_report_manifest(root_path, artifact_path, content_hash, command, artifact_hash_blake3)
    result_payload = {
        'overall_ok': overall_ok,
        'artifact_path': str(artifact_path),
        'artifact_hash_blake3': artifact_hash_blake3,
        'artifact_sha256': _file_sha256(artifact_path),
        'cache_hit': False,
        'validated_existing_artifact': False,
        'content_hash_sha256': content_hash,
        'command': command,
        'returncode': result.returncode,
        'stdout_tail': result.stdout[-2000:],
        'stderr_tail': result.stderr[-2000:],
        'validation': validation,
        'artifact': validation.get('artifact'),
    }
    result_payload.update(extra_fields)
    return result_payload


def _fixture_hash_hex(label: str) -> str:
    try:
        import blake3

        return blake3.blake3(label.encode('utf-8')).hexdigest()
    except ImportError:
        return _blake3_hex_with_rust(Path(ROOT), label.encode('utf-8'))


def _is_nonzero_hash_array(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 32
        and all(isinstance(byte, int) and 0 <= byte <= 255 for byte in value)
        and any(byte != 0 for byte in value)
    )


def _validate_replay_write_evidence(payload: dict) -> dict:
    evidence = payload.get('write_evidence') or {}
    logical_payload_hash = evidence.get('logical_payload_hash')
    evidence_hash = evidence.get('evidence_hash')
    checks = {
        'staged_temp_file_used': evidence.get('staged_temp_file_used') is True,
        'temp_file_synced_before_publish': evidence.get('temp_file_synced_before_publish') is True,
        'publish_completed': evidence.get('publish_completed') is True,
        'parent_directory_sync_attempted': evidence.get('parent_directory_sync_attempted') is True,
        'replace_existing_supported': evidence.get('replace_existing_supported') is True,
        'publish_write_through_requested': evidence.get('publish_write_through_requested') is (os.name == 'nt'),
        'logical_payload_bytes': int(evidence.get('logical_payload_bytes') or 0) > 0,
        'logical_payload_hash': _is_nonzero_hash_array(logical_payload_hash),
        'evidence_hash': _is_nonzero_hash_array(evidence_hash) and evidence_hash != logical_payload_hash,
    }
    return {
        'ok': all(checks.values()),
        'checks': checks,
    }


def _write_browser_ops_bench_verification_fixture(root: Path) -> Path:
    fixture_root = root / 'artifacts' / 'browser_ops_bench_fixture'
    if fixture_root.exists():
        shutil.rmtree(fixture_root)
    run_dir = fixture_root / (
        'collector-runs/'
        'run-00000000000000000000000000000a11-'
        'action-00000000000000000000000000000b22-'
        'seq-0000000000000001'
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    artifact_bytes = {
        'url_before': b'https://aegis.local/before',
        'url_after': b'https://aegis.local/after',
        'dom_snapshot_before': b'<html><body>before</body></html>',
        'dom_snapshot_after': b'<html><body>after</body></html>',
        'screenshot_before': b'aegis-before-screenshot-bytes',
        'screenshot_after': b'aegis-after-screenshot-bytes',
        'accessibility_tree_after': b'{"role":"document","name":"after"}',
        'network_log': b'{"status":200,"url":"/after"}',
    }
    filenames = {
        'url_before': '01-url-before.txt',
        'url_after': '02-url-after.txt',
        'dom_snapshot_before': '03-dom-before.html',
        'dom_snapshot_after': '04-dom-after.html',
        'screenshot_before': '05-screenshot-before.bin',
        'screenshot_after': '06-screenshot-after.bin',
        'accessibility_tree_after': '07-accessibility-after.json',
        'network_log': '08-network-log.jsonl',
    }
    artifact_paths = {}
    for kind, payload in artifact_bytes.items():
        path = run_dir / filenames[kind]
        path.write_bytes(payload)
        artifact_paths[kind] = str(path.resolve())

    metadata_path = run_dir / 'producer-metadata.json'
    metadata = {
        'schema': 'aegis-browser-live-collector-producer-v1',
        'run_id': 2577,
        'action_id': 2850,
        'sequence_number': 1,
        'run_directory': str(run_dir.resolve()),
        'artifact_paths': artifact_paths,
        'verifier': 'rust-browser-live-collector-run',
        'truth_claim': False,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding='utf-8')

    scorecard_path = fixture_root / 'browser-ops-scorecard.json'
    scorecard = {
        'schema': 'aegis-browser-ops-bench-scorecard-v1',
        'suite_name': 'run-checks-browser-ops',
        'total': 1,
        'passed': 1,
        'failed': 0,
        'overall_ok': True,
        'truth_claim': False,
        'verifier': 'rust-browser-live-collector-run',
        'records': [
            {
                'task_id': 'run-checks-browser-ops-physical-fixture',
                'ok': True,
                'run_directory': str(run_dir.resolve()),
                'producer_metadata_path': str(metadata_path.resolve()),
                'action_result_type': 'str',
                'error': '',
                'predicate_results': [
                    {
                        'name': 'url-after',
                        'artifact_kind': 'url_after',
                        'artifact_path': artifact_paths['url_after'],
                        'byte_len': len(artifact_bytes['url_after']),
                        'ok': True,
                        'detail': 'ok',
                    },
                    {
                        'name': 'network-log',
                        'artifact_kind': 'network_log',
                        'artifact_path': artifact_paths['network_log'],
                        'byte_len': len(artifact_bytes['network_log']),
                        'ok': True,
                        'detail': 'ok',
                    },
                ],
            }
        ],
    }
    scorecard_path.write_text(json.dumps(scorecard, indent=2, sort_keys=True), encoding='utf-8')
    return scorecard_path


def _validate_browser_ops_bench_verification_report(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return {'ok': False, 'detail': str(exc)}

    report = payload.get('report') or {}
    write_evidence = _validate_replay_write_evidence(payload)
    proof_hash = report.get('proof_hash')
    report_hash = report.get('report_hash')
    verification_event_hash = report.get('verification_event_hash')
    replay_binding_hash = report.get('replay_binding_hash')
    checks = {
        'schema_version': payload.get('schema_version') == 1,
        'report_schema': report.get('schema') == 'aegis-browser-ops-bench-verification-report-v1',
        'verified_task_count': int(report.get('verified_task_count') or 0) > 0,
        'scorecard_file_hash': _is_nonzero_hash_array(report.get('scorecard_file_hash')),
        'suite_hash': _is_nonzero_hash_array(report.get('suite_hash')),
        'proof_hash': _is_nonzero_hash_array(proof_hash),
        'verified_records_hash': _is_nonzero_hash_array(report.get('verified_records_hash')),
        'report_hash': _is_nonzero_hash_array(report_hash),
        'replay_recorded': report.get('replay_recorded') is True,
        'run_id': int(report.get('run_id') or 0) > 0,
        'verification_subject_id': int(report.get('verification_subject_id') or 0) > 0,
        'verification_event_id': int(report.get('verification_event_id') or 0) > 0,
        'verification_event_hash': _is_nonzero_hash_array(verification_event_hash),
        'replay_binding_hash': _is_nonzero_hash_array(replay_binding_hash),
        'hashes_are_distinct': (
            proof_hash != report_hash
            and proof_hash != verification_event_hash
            and replay_binding_hash != verification_event_hash
        ),
        'write_evidence': write_evidence['ok'],
    }
    return {
        'ok': all(checks.values()),
        'checks': checks,
        'write_evidence': write_evidence,
        'detail': 'ok' if all(checks.values()) else 'invalid BrowserOpsBench verification report artifact',
        'artifact': payload,
    }


def _validate_agentic_sdk_context_report(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return {'ok': False, 'detail': str(exc)}

    report = payload.get('report') or {}
    write_evidence = _validate_replay_write_evidence(payload)
    sdk_run_hash = report.get('sdk_run_hash')
    manifest_hash = report.get('manifest_hash')
    execution_record_hash = report.get('execution_record_hash')
    capsule_hash = report.get('capsule_hash')
    candidate_list_hash = report.get('candidate_list_hash')
    sdk_run_event_hash = report.get('sdk_run_event_hash')
    sdk_run_replay_binding_hash = report.get('sdk_run_replay_binding_hash')
    handoff_hash = report.get('handoff_hash')
    checkpoint_hash = report.get('checkpoint_hash')
    next_action_packet_hash = report.get('next_action_packet_hash')
    context_pack_digest = report.get('context_pack_digest')
    context_pack_node_hash = report.get('context_pack_node_hash')
    context_pack_candidate_proof_hash = report.get('context_pack_candidate_proof_hash')
    context_pack_event_hash = report.get('context_pack_event_hash')
    context_archive_manifest_hash = report.get('context_archive_manifest_hash')
    context_ledger_hash = report.get('context_ledger_hash')
    report_hash = report.get('report_hash')
    checks = {
        'schema_version': payload.get('schema_version') == 1,
        'report_schema': report.get('schema') == 'aegis-agentic-sdk-context-report-v1',
        'fixed_fixture_ids': (
            int(report.get('run_id') or 0) == 9901
            and int(report.get('execution_id') or 0) == 1991
            and int(report.get('active_task_id') or 0) == 1
        ),
        'candidate_count': int(report.get('candidate_count') or 0) > 0,
        'event_ids': (
            int(report.get('sdk_run_event_id') or 0) > 0
            and int(report.get('context_pack_event_id') or 0) > int(report.get('sdk_run_event_id') or 0)
        ),
        'replay_recorded': report.get('replay_recorded') is True,
        'context_recorded': report.get('context_recorded') is True,
        'sdk_hashes': (
            _is_nonzero_hash_array(sdk_run_hash)
            and _is_nonzero_hash_array(manifest_hash)
            and _is_nonzero_hash_array(execution_record_hash)
            and _is_nonzero_hash_array(capsule_hash)
            and _is_nonzero_hash_array(candidate_list_hash)
        ),
        'replay_handoff_hashes': (
            _is_nonzero_hash_array(sdk_run_event_hash)
            and _is_nonzero_hash_array(sdk_run_replay_binding_hash)
            and _is_nonzero_hash_array(handoff_hash)
            and _is_nonzero_hash_array(checkpoint_hash)
            and _is_nonzero_hash_array(next_action_packet_hash)
        ),
        'context_hashes': (
            _is_nonzero_hash_array(context_pack_digest)
            and _is_nonzero_hash_array(context_pack_node_hash)
            and _is_nonzero_hash_array(context_pack_candidate_proof_hash)
            and _is_nonzero_hash_array(context_pack_event_hash)
            and _is_nonzero_hash_array(context_archive_manifest_hash)
            and _is_nonzero_hash_array(context_ledger_hash)
            and int(report.get('context_event_count') or 0) >= int(report.get('context_pack_event_id') or 0)
            and context_ledger_hash == context_pack_event_hash
        ),
        'report_hash': _is_nonzero_hash_array(report_hash),
        'hashes_are_distinct': len({
            tuple(sdk_run_hash or []),
            tuple(sdk_run_event_hash or []),
            tuple(sdk_run_replay_binding_hash or []),
            tuple(handoff_hash or []),
            tuple(context_pack_candidate_proof_hash or []),
            tuple(context_pack_event_hash or []),
            tuple(context_archive_manifest_hash or []),
            tuple(report_hash or []),
        }) == 8,
        'write_evidence': write_evidence['ok'],
    }
    return {
        'ok': all(checks.values()),
        'checks': checks,
        'write_evidence': write_evidence,
        'detail': 'ok' if all(checks.values()) else 'invalid agentic SDK context report artifact',
        'artifact': payload,
    }


def _validate_replay_chaos_scorecard(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return {'ok': False, 'detail': str(exc)}

    scorecard = payload.get('scorecard') or {}
    report = payload.get('report') or {}
    io_evidence = payload.get('io_evidence') or {}
    scorecard_replay_hash = scorecard.get('replay_hash')
    scorecard_witness_hash = scorecard.get('physical_witness_hash')
    report_hash = report.get('report_hash')
    crash_points_exercised = int(report.get('crash_points_exercised') or 0)
    segmented_arrow_audit_mmap_buffer_count = int(report.get('segmented_arrow_audit_mmap_buffer_count') or 0)
    segmented_arrow_audit_segment_witness_hash = report.get('segmented_arrow_audit_segment_witness_hash')
    column_scan_full_acceptance_count = int(report.get('column_scan_full_acceptance_count') or 0)
    column_scan_missing_tail_rejection_count = int(report.get('column_scan_missing_tail_rejection_count') or 0)
    write_evidence = _validate_replay_write_evidence(payload)
    checks = {
        'schema_version': payload.get('schema_version') == 1,
        'bench_name': payload.get('bench_name') == 'ReplayChaosBench',
        'scorecard_success': scorecard.get('success') is True,
        'scorecard_crash_recovery': scorecard.get('crash_recovery_passed') is True,
        'report_recoveries_valid': report.get('all_recoveries_valid') is True,
        'hash_binding': (
            isinstance(scorecard_replay_hash, list)
            and scorecard_replay_hash == scorecard_witness_hash
            and scorecard_replay_hash == report_hash
        ),
        'io_mmap_backing': io_evidence.get('mmap_backing_used') is True,
        'io_materialization_recorded': (
            io_evidence.get('stream_reader_materializes_events') is True
            and int(io_evidence.get('materialized_event_count') or 0) > 0
            and int(io_evidence.get('materialized_run_event_bytes') or 0) > 0
            and int(io_evidence.get('materialized_hash_bytes') or 0) > 0
        ),
        'segmented_arrow_column_scan_proof': (
            _is_nonzero_hash_array(report.get('segmented_arrow_audit_proof_hash'))
            and _is_nonzero_hash_array(report.get('segmented_arrow_audit_logical_replay_hash'))
            and _is_nonzero_hash_array(report.get('segmented_arrow_audit_mmap_evidence_hash'))
            and _is_nonzero_hash_array(segmented_arrow_audit_segment_witness_hash)
            and segmented_arrow_audit_mmap_buffer_count > 0
            and column_scan_full_acceptance_count > 0
            and column_scan_missing_tail_rejection_count > 0
            and column_scan_full_acceptance_count + column_scan_missing_tail_rejection_count
                == crash_points_exercised
        ),
        'write_evidence': write_evidence['ok'],
    }
    return {
        'ok': all(checks.values()),
        'checks': checks,
        'write_evidence': write_evidence,
        'detail': 'ok' if all(checks.values()) else 'invalid ReplayChaosBench scorecard artifact',
        'artifact': payload,
    }


def _validate_replay_endurance_report(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return {'ok': False, 'detail': str(exc)}

    report = payload.get('report') or {}
    simulated_hours = int(report.get('simulated_hours') or 0)
    cycles_per_hour = int(report.get('cycles_per_hour') or 0)
    synthetic_cycle_count = int(report.get('synthetic_cycle_count') or 0)
    checkpoint_cadence_cycles = int(report.get('checkpoint_cadence_cycles') or 0)
    checkpoint_count = int(report.get('checkpoint_count') or 0)
    context_fold_count = int(report.get('context_fold_count') or 0)
    tail_recovered_context_fold_count = int(report.get('tail_recovered_context_fold_count') or 0)
    context_fold_checkpoint_pair_count = int(report.get('context_fold_checkpoint_pair_count') or 0)
    event_count = int(report.get('event_count') or 0)
    segment_count = int(report.get('segment_count') or 0)
    segmented_arrow_audit_segment_count = int(report.get('segmented_arrow_audit_segment_count') or 0)
    segmented_arrow_audit_event_count = int(report.get('segmented_arrow_audit_event_count') or 0)
    segmented_arrow_audit_total_file_bytes = int(report.get('segmented_arrow_audit_total_file_bytes') or 0)
    segmented_arrow_audit_mmap_buffer_count = int(report.get('segmented_arrow_audit_mmap_buffer_count') or 0)
    segmented_arrow_audit_segment_witness_hash = report.get('segmented_arrow_audit_segment_witness_hash')
    mmap_recovered_event_count = int(report.get('mmap_recovered_event_count') or 0)
    tail_drop_segment_count = int(report.get('tail_drop_segment_count') or 0)
    tail_recovered_segment_count = int(report.get('tail_recovered_segment_count') or 0)
    tail_recovered_event_count = int(report.get('tail_recovered_event_count') or 0)
    tail_recovered_checkpoint_count = int(report.get('tail_recovered_checkpoint_count') or 0)
    tail_events_since_last_checkpoint = int(report.get('tail_events_since_last_checkpoint') or 0)
    checkpoint_cadence_event_bound = int(report.get('checkpoint_cadence_event_bound') or 0)
    materialized_event_count = int(report.get('materialized_event_count') or 0)
    materialized_replay_bytes = int(report.get('materialized_replay_bytes') or 0)
    bounded_materialized_bytes = int(report.get('bounded_materialized_bytes') or 0)
    report_hash = report.get('report_hash')
    manifest_hash = report.get('manifest_hash')
    expected_ledger_hash = report.get('expected_ledger_hash')
    mmap_recovered_ledger_hash = report.get('mmap_recovered_ledger_hash')
    tail_recovered_ledger_hash = report.get('tail_recovered_ledger_hash')
    context_fold_evidence_hash = report.get('context_fold_evidence_hash')
    segmented_arrow_audit_proof_hash = report.get('segmented_arrow_audit_proof_hash')
    replay_determinism_proof_hash = report.get('replay_determinism_proof_hash')
    run_checkpoint_hash = report.get('run_checkpoint_hash')
    next_action_packet_hash = report.get('next_action_packet_hash')
    mmap_evidence_hash = report.get('mmap_evidence_hash')
    tail_recovery_hash = report.get('tail_recovery_hash')
    write_evidence = _validate_replay_write_evidence(payload)
    checks = {
        'schema_version': payload.get('schema_version') == 1,
        'bench_name': payload.get('bench_name') == 'ReplayEnduranceBench',
        'simulated_100h': simulated_hours == 100,
        'cycle_binding': cycles_per_hour > 0 and synthetic_cycle_count == simulated_hours * cycles_per_hour,
        'default_cycle_count': synthetic_cycle_count == 2_400,
        'checkpoint_cadence': checkpoint_cadence_cycles > 0 and checkpoint_count == 400,
        'context_fold_proof': (
            context_fold_count == checkpoint_count
            and context_fold_count == 400
            and tail_recovered_context_fold_count >= tail_recovered_checkpoint_count
            and tail_recovered_context_fold_count <= tail_recovered_checkpoint_count + 1
            and tail_recovered_context_fold_count > 0
            and context_fold_checkpoint_pair_count == checkpoint_count
        ),
        'mmap_full_recovery': mmap_recovered_event_count == event_count and event_count > 0,
        'segmented_arrow_audit_proof': (
            segmented_arrow_audit_segment_count == segment_count
            and segmented_arrow_audit_event_count == event_count
            and segmented_arrow_audit_total_file_bytes > 0
            and segmented_arrow_audit_mmap_buffer_count > 0
            and _is_nonzero_hash_array(segmented_arrow_audit_proof_hash)
            and _is_nonzero_hash_array(segmented_arrow_audit_segment_witness_hash)
        ),
        'replay_determinism_proof': _is_nonzero_hash_array(replay_determinism_proof_hash),
        'shift_manager_next_action': (
            _is_nonzero_hash_array(run_checkpoint_hash)
            and _is_nonzero_hash_array(next_action_packet_hash)
            and run_checkpoint_hash != next_action_packet_hash
        ),
        'tail_recovery_prefix': (
            tail_drop_segment_count > 0
            and tail_recovered_segment_count + tail_drop_segment_count == segment_count
            and tail_recovered_event_count < event_count
            and tail_recovered_checkpoint_count > 0
        ),
        'checkpoint_bound': tail_events_since_last_checkpoint <= checkpoint_cadence_event_bound,
        'materialization_bound': (
            materialized_event_count == event_count
            and 0 < materialized_replay_bytes <= bounded_materialized_bytes
        ),
        'physical_flags': (
            report.get('all_hash_chains_valid') is True
            and report.get('full_replay_matches') is True
            and report.get('tail_recovery_matches_prefix') is True
            and report.get('mmap_materialized_replay_proven') is True
            and report.get('materialization_within_bound') is True
        ),
        'hash_binding': (
            _is_nonzero_hash_array(report_hash)
            and _is_nonzero_hash_array(manifest_hash)
            and _is_nonzero_hash_array(expected_ledger_hash)
            and _is_nonzero_hash_array(mmap_recovered_ledger_hash)
            and _is_nonzero_hash_array(tail_recovered_ledger_hash)
            and _is_nonzero_hash_array(context_fold_evidence_hash)
            and _is_nonzero_hash_array(segmented_arrow_audit_proof_hash)
            and _is_nonzero_hash_array(segmented_arrow_audit_segment_witness_hash)
            and _is_nonzero_hash_array(replay_determinism_proof_hash)
            and _is_nonzero_hash_array(run_checkpoint_hash)
            and _is_nonzero_hash_array(next_action_packet_hash)
            and _is_nonzero_hash_array(mmap_evidence_hash)
            and _is_nonzero_hash_array(tail_recovery_hash)
            and expected_ledger_hash == mmap_recovered_ledger_hash
            and tail_recovered_ledger_hash != expected_ledger_hash
        ),
        'write_evidence': write_evidence['ok'],
    }
    return {
        'ok': all(checks.values()),
        'checks': checks,
        'write_evidence': write_evidence,
        'detail': 'ok' if all(checks.values()) else 'invalid ReplayEnduranceBench artifact',
        'artifact': payload,
    }


def generate_replay_chaos_scorecard(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'replay_chaos_scorecard.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        REPLAY_CHAOS_SCORECARD_COMMAND,
        _parse_scorecard_hash,
        lambda _root: _validate_replay_chaos_scorecard(artifact_path),
        force_fresh,
        content_hash,
    )


def generate_replay_endurance_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'replay_endurance_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        REPLAY_ENDURANCE_REPORT_COMMAND,
        _parse_endurance_report_hash,
        lambda _root: _validate_replay_endurance_report(artifact_path),
        force_fresh,
        content_hash,
    )


def generate_browser_ops_bench_verification_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'browser_ops_bench_verification_report.json'
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    scorecard_path = _write_browser_ops_bench_verification_fixture(root_path)
    command = [
        *BROWSER_OPS_BENCH_VERIFY_COMMAND,
        str(scorecard_path),
        str(artifact_path),
        'playwright-cdp',
        '1786560000000',
        _fixture_hash_hex('run-checks-browser-ops-config'),
        _fixture_hash_hex('run-checks-browser-ops-capability'),
        _fixture_hash_hex('run-checks-browser-ops-session'),
        _fixture_hash_hex('run-checks-browser-ops-redaction'),
        _fixture_hash_hex('run-checks-browser-ops-policy-window'),
        '8899',
    ]
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        BROWSER_OPS_BENCH_VERIFY_COMMAND,
        _parse_browser_ops_bench_verification_report_hash,
        lambda _root: _validate_browser_ops_bench_verification_report(artifact_path),
        force_fresh,
        content_hash,
        command=command,
        extra_fields={'scorecard_path': str(scorecard_path)},
    )


def generate_agentic_sdk_context_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'agentic_sdk_context_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        AGENTIC_SDK_CONTEXT_REPORT_COMMAND,
        _parse_agentic_sdk_context_report_hash,
        lambda _root: _validate_agentic_sdk_context_report(artifact_path),
        force_fresh,
        content_hash,
    )


def generate_cluster_loopback_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'cluster_loopback_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        CLUSTER_LOOPBACK_REPORT_COMMAND,
        _parse_cluster_loopback_report_hash,
        evaluate_cluster_loopback_gate,
        force_fresh,
        content_hash,
    )


def generate_quickjs_cold_start_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'quickjs_cold_start_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        QUICKJS_COLD_START_REPORT_COMMAND,
        _parse_quickjs_cold_start_report_hash,
        evaluate_quickjs_cold_start_gate,
        force_fresh,
        content_hash,
    )


def generate_tcp_cluster_soak_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'tcp_cluster_soak_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        TCP_CLUSTER_SOAK_REPORT_COMMAND,
        _parse_tcp_cluster_soak_report_hash,
        evaluate_tcp_cluster_soak_gate,
        force_fresh,
        content_hash,
    )


def generate_hot_browser_shadow_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'hot_browser_shadow_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        HOT_BROWSER_SHADOW_REPORT_COMMAND,
        _parse_hot_browser_shadow_report_hash,
        evaluate_hot_browser_shadow_gate,
        force_fresh,
        content_hash,
    )


def generate_shadow_sealer_soak_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'shadow_sealer_soak_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        SHADOW_SEALER_SOAK_REPORT_COMMAND,
        _parse_shadow_sealer_soak_report_hash,
        evaluate_shadow_sealer_soak_gate,
        force_fresh,
        content_hash,
    )


def generate_dynamic_provider_fallback_report(root: str | Path, force_fresh: bool = False, content_hash: str | None = None) -> dict:
    root_path = Path(root)
    artifact_path = root_path / 'artifacts' / 'dynamic_provider_fallback_report.json'
    return _cached_or_generate_cli_report(
        root_path,
        artifact_path,
        DYNAMIC_PROVIDER_FALLBACK_REPORT_COMMAND,
        _parse_dynamic_provider_fallback_report_hash,
        evaluate_dynamic_provider_fallback_gate,
        force_fresh,
        content_hash,
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    force_fresh = '--force-fresh' in args or '--refresh-benchmarks' in args
    benchmark_profile = _parse_benchmark_profile(args)
    baseline_gates_hard_required = benchmark_profile == BENCHMARK_PROFILE_RELEASE
    preflight = check_build_preflight(ROOT)
    messages = [
        build_message(1, 2, b'abc'),
        build_message(2, 3, b'def'),
        build_message(3, 4, b'ghi'),
    ]
    smoke = validate_bridge_smoke(messages[0])
    batch = validate_bridge_batch(messages)
    manifest = build_service_manifest(messages[0])
    constitution_audit = evaluate_constitution(ROOT)
    constitution_audit_ok = constitution_audit['overall_ok']
    governance_gate = evaluate_governance(ROOT)
    governance_gate_ok = governance_gate['overall_ok']
    python_hotpath_gate_report = evaluate_python_hotpaths(ROOT)
    python_hotpath_gate = python_hotpath_report_to_dict(python_hotpath_gate_report)
    python_hotpath_gate_ok = python_hotpath_gate_report.overall_ok
    replay_chaos_scorecard = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; replay chaos scorecard not generated.',
    }
    replay_endurance_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; replay endurance report not generated.',
    }
    browser_ops_bench_verification_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; BrowserOpsBench verification report not generated.',
    }
    agentic_sdk_context_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; agentic SDK context report not generated.',
    }
    cluster_loopback_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; cluster loopback report not generated.',
    }
    quickjs_cold_start_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; QuickJS cold-start report not generated.',
    }
    tcp_cluster_soak_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; TCP cluster soak report not generated.',
    }
    hot_browser_shadow_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; Hot Browser Shadow report not generated.',
    }
    shadow_sealer_soak_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; Shadow Sealer soak report not generated.',
    }
    dynamic_provider_fallback_report = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Rust toolchain not ready; dynamic provider fallback report not generated.',
    }
    cli_report_cache = {
        'schema': 'aegis-cli-report-cache-summary-v1',
        'enabled': False,
        'content_hash_sha256': '',
        'cacheable_artifact_count': 10,
        'cache_hit_count': 0,
        'validated_existing_artifact_count': 0,
        'force_fresh': force_fresh,
    }
    baseline_gate_cache = {
        'schema': 'aegis-baseline-gate-cache-summary-v1',
        'enabled': False,
        'content_hash_sha256': '',
        'benchmark_content_hash_blake3': '',
        'benchmark_profile': benchmark_profile,
        'cacheable_gate_count': 5,
        'cache_hit_count': 0,
        'validated_existing_gate_count': 0,
        'force_fresh': force_fresh,
    }
    replay_chaos_scorecard_ok = True
    replay_endurance_report_ok = True
    browser_ops_bench_verification_report_ok = True
    agentic_sdk_context_report_ok = True
    cluster_loopback_report_ok = True
    quickjs_cold_start_report_ok = True
    tcp_cluster_soak_report_ok = True
    hot_browser_shadow_report_ok = True
    shadow_sealer_soak_report_ok = True
    dynamic_provider_fallback_report_ok = True
    benchmark_cache = None
    benchmark_gate_ok = True
    sota_baseline_gate = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Criterion artifacts not present; run cargo bench before enforcing SOTA baseline.',
    }
    sota_baseline_gate_ok = True
    hermes_baseline_gate = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Criterion artifacts not present; run cargo bench before enforcing Hermes baseline.',
    }
    hermes_baseline_gate_ok = True
    hermes_rpc_baseline_gate = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Criterion artifacts not present; run cargo bench before enforcing Hermes JSON-RPC baseline.',
    }
    hermes_rpc_baseline_gate_ok = True
    hermes_session_recovery_baseline_gate = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Criterion artifacts not present; run cargo bench before enforcing Hermes session recovery baseline.',
    }
    hermes_session_recovery_baseline_gate_ok = True
    hermes_persistence_baseline_gate = {
        'overall_ok': True,
        'skipped': True,
        'reason': 'Criterion artifacts not present; run cargo bench before enforcing Hermes persistence baseline.',
    }
    hermes_persistence_baseline_gate_ok = True
    if preflight.ready_to_benchmark:
        cli_report_cache_content_hash = cli_report_content_hash(ROOT)
        cli_report_cache['enabled'] = True
        cli_report_cache['content_hash_sha256'] = cli_report_cache_content_hash
        replay_chaos_scorecard = generate_replay_chaos_scorecard(ROOT, force_fresh, cli_report_cache_content_hash)
        replay_chaos_scorecard_ok = replay_chaos_scorecard['overall_ok']
        replay_endurance_report = generate_replay_endurance_report(ROOT, force_fresh, cli_report_cache_content_hash)
        replay_endurance_report_ok = replay_endurance_report['overall_ok']
        browser_ops_bench_verification_report = generate_browser_ops_bench_verification_report(
            ROOT, force_fresh, cli_report_cache_content_hash
        )
        browser_ops_bench_verification_report_ok = browser_ops_bench_verification_report['overall_ok']
        agentic_sdk_context_report = generate_agentic_sdk_context_report(ROOT, force_fresh, cli_report_cache_content_hash)
        agentic_sdk_context_report_ok = agentic_sdk_context_report['overall_ok']
        cluster_loopback_report = generate_cluster_loopback_report(ROOT, force_fresh, cli_report_cache_content_hash)
        cluster_loopback_report_ok = cluster_loopback_report['overall_ok']
        quickjs_cold_start_report = generate_quickjs_cold_start_report(ROOT, force_fresh, cli_report_cache_content_hash)
        quickjs_cold_start_report_ok = quickjs_cold_start_report['overall_ok']
        tcp_cluster_soak_report = generate_tcp_cluster_soak_report(ROOT, force_fresh, cli_report_cache_content_hash)
        tcp_cluster_soak_report_ok = tcp_cluster_soak_report['overall_ok']
        hot_browser_shadow_report = generate_hot_browser_shadow_report(ROOT, force_fresh, cli_report_cache_content_hash)
        hot_browser_shadow_report_ok = hot_browser_shadow_report['overall_ok']
        shadow_sealer_soak_report = generate_shadow_sealer_soak_report(ROOT, force_fresh, cli_report_cache_content_hash)
        shadow_sealer_soak_report_ok = shadow_sealer_soak_report['overall_ok']
        dynamic_provider_fallback_report = generate_dynamic_provider_fallback_report(ROOT, force_fresh, cli_report_cache_content_hash)
        dynamic_provider_fallback_report_ok = dynamic_provider_fallback_report['overall_ok']
        cacheable_cli_reports = (
            replay_chaos_scorecard,
            replay_endurance_report,
            browser_ops_bench_verification_report,
            agentic_sdk_context_report,
            cluster_loopback_report,
            quickjs_cold_start_report,
            tcp_cluster_soak_report,
            hot_browser_shadow_report,
            shadow_sealer_soak_report,
            dynamic_provider_fallback_report,
        )
        cli_report_cache['cache_hit_count'] = sum(1 for item in cacheable_cli_reports if item.get('cache_hit') is True)
        cli_report_cache['validated_existing_artifact_count'] = sum(
            1 for item in cacheable_cli_reports if item.get('validated_existing_artifact') is True
        )
        try:
            benchmark_cache = ensure_fresh_benchmarks(ROOT, force_fresh, benchmark_profile)
        except (OSError, subprocess.CalledProcessError) as exc:
            benchmark_cache = {
                'overall_ok': False,
                'error': str(exc),
                'force_fresh': force_fresh,
                'benchmark_profile': benchmark_profile,
                'baseline_gates_hard_required': baseline_gates_hard_required,
            }
            benchmark_gate_ok = False

    if benchmark_cache is not None and benchmark_cache.get('returncode', 0) != 0:
        benchmark_gate = {
            'overall_ok': False,
            'reason': 'fresh cargo bench failed',
            'benchmark_cache': benchmark_cache,
        }
        benchmark_gate_ok = False
    elif criterion_artifacts_present(ROOT):
        benchmark_gate_report = evaluate_benchmarks(ROOT, profile=benchmark_profile)
        benchmark_gate = benchmark_report_to_dict(benchmark_gate_report)
        benchmark_gate['profile'] = benchmark_profile
        benchmark_gate_ok = benchmark_gate_report.overall_ok
    else:
        benchmark_gate = {
            'overall_ok': True,
            'skipped': True,
            'profile': benchmark_profile,
            'reason': 'Criterion artifacts not present; run cargo bench before enforcing thresholds.',
        }

    if criterion_artifacts_present(ROOT) and benchmark_gate_ok:
        baseline_gate_cache_content_hash = baseline_gate_content_hash(ROOT, benchmark_profile)
        benchmark_content_hash_value = (
            benchmark_cache.get('content_hash_blake3')
            if isinstance(benchmark_cache, dict) and benchmark_cache.get('content_hash_blake3')
            else benchmark_content_hash(ROOT)
        )
        baseline_gate_cache['enabled'] = True
        baseline_gate_cache['content_hash_sha256'] = baseline_gate_cache_content_hash
        baseline_gate_cache['benchmark_content_hash_blake3'] = benchmark_content_hash_value
        baseline_gate_specs = (
            ('sota_baseline_gate', evaluate_sota_baseline, sota_baseline_report_to_dict),
            ('hermes_baseline_gate', evaluate_hermes_baseline, hermes_baseline_report_to_dict),
            ('hermes_rpc_baseline_gate', evaluate_hermes_rpc_baseline, hermes_rpc_baseline_report_to_dict),
            (
                'hermes_session_recovery_baseline_gate',
                evaluate_hermes_session_recovery_baseline,
                hermes_session_recovery_baseline_report_to_dict,
            ),
            (
                'hermes_persistence_baseline_gate',
                evaluate_hermes_persistence_baseline,
                hermes_persistence_baseline_report_to_dict,
            ),
        )
        evaluated_baseline_gates: dict[str, dict] = {}
        evaluated_baseline_gate_ok: dict[str, bool] = {}
        for gate_name, evaluator, serializer in baseline_gate_specs:
            try:
                gate_payload, gate_ok = _cached_or_evaluate_baseline_gate(
                    ROOT,
                    gate_name,
                    evaluator,
                    serializer,
                    baseline_gate_cache_content_hash,
                    benchmark_content_hash_value,
                    benchmark_profile,
                    force_fresh,
                )
            except (OSError, subprocess.CalledProcessError, ValueError) as exc:
                gate_payload = {
                    'overall_ok': False,
                    'error': str(exc),
                    'baseline_cache_gate_name': gate_name,
                    'baseline_cache_hit': False,
                    'validated_existing_gate': False,
                    'baseline_cache_content_hash_sha256': baseline_gate_cache_content_hash,
                }
                gate_ok = False
            evaluated_baseline_gates[gate_name] = gate_payload
            evaluated_baseline_gate_ok[gate_name] = gate_ok

        sota_baseline_gate = evaluated_baseline_gates['sota_baseline_gate']
        sota_baseline_gate_ok = evaluated_baseline_gate_ok['sota_baseline_gate']
        hermes_baseline_gate = evaluated_baseline_gates['hermes_baseline_gate']
        hermes_baseline_gate_ok = evaluated_baseline_gate_ok['hermes_baseline_gate']
        hermes_rpc_baseline_gate = evaluated_baseline_gates['hermes_rpc_baseline_gate']
        hermes_rpc_baseline_gate_ok = evaluated_baseline_gate_ok['hermes_rpc_baseline_gate']
        hermes_session_recovery_baseline_gate = evaluated_baseline_gates['hermes_session_recovery_baseline_gate']
        hermes_session_recovery_baseline_gate_ok = evaluated_baseline_gate_ok['hermes_session_recovery_baseline_gate']
        hermes_persistence_baseline_gate = evaluated_baseline_gates['hermes_persistence_baseline_gate']
        hermes_persistence_baseline_gate_ok = evaluated_baseline_gate_ok['hermes_persistence_baseline_gate']
        baseline_gate_cache['cache_hit_count'] = sum(
            1 for item in evaluated_baseline_gates.values() if item.get('baseline_cache_hit') is True
        )
        baseline_gate_cache['validated_existing_gate_count'] = sum(
            1 for item in evaluated_baseline_gates.values() if item.get('validated_existing_gate') is True
        )

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACTS_DIR / 'benchmark_gate_report.json').write_text(
        json.dumps(benchmark_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    (ARTIFACTS_DIR / 'constitution_audit_report.json').write_text(
        json.dumps(constitution_audit, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    (ARTIFACTS_DIR / 'governance_gate_report.json').write_text(
        json.dumps(governance_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    dependency_audit_gate = evaluate_dependency_audit_gate(ROOT)
    DEPENDENCY_AUDIT_GATE_REPORT_PATH.write_text(
        json.dumps(dependency_audit_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    dependency_audit_gate_ok = dependency_audit_gate['overall_ok']
    provider_route_gate = evaluate_provider_route_gate(ROOT)
    PROVIDER_ROUTE_GATE_REPORT_PATH.write_text(
        json.dumps(provider_route_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    provider_route_gate_ok = provider_route_gate['overall_ok']
    supply_chain_gate = evaluate_supply_chain_gate(ROOT)
    SUPPLY_CHAIN_GATE_REPORT_PATH.write_text(
        json.dumps(supply_chain_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    supply_chain_gate_ok = supply_chain_gate['overall_ok']
    cluster_loopback_gate = evaluate_cluster_loopback_gate(ROOT)
    CLUSTER_LOOPBACK_GATE_REPORT_PATH.write_text(
        json.dumps(cluster_loopback_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    cluster_loopback_gate_ok = cluster_loopback_gate['overall_ok']
    quickjs_cold_start_gate = evaluate_quickjs_cold_start_gate(ROOT)
    QUICKJS_COLD_START_GATE_REPORT_PATH.write_text(
        json.dumps(quickjs_cold_start_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    quickjs_cold_start_gate_ok = quickjs_cold_start_gate['overall_ok']
    tcp_cluster_soak_gate = evaluate_tcp_cluster_soak_gate(ROOT)
    TCP_CLUSTER_SOAK_GATE_REPORT_PATH.write_text(
        json.dumps(tcp_cluster_soak_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    tcp_cluster_soak_gate_ok = tcp_cluster_soak_gate['overall_ok']
    hot_browser_shadow_gate = evaluate_hot_browser_shadow_gate(ROOT)
    HOT_BROWSER_SHADOW_GATE_REPORT_PATH.write_text(
        json.dumps(hot_browser_shadow_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    hot_browser_shadow_gate_ok = hot_browser_shadow_gate['overall_ok']
    shadow_sealer_soak_gate = evaluate_shadow_sealer_soak_gate(ROOT)
    SHADOW_SEALER_SOAK_GATE_REPORT_PATH.write_text(
        json.dumps(shadow_sealer_soak_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    shadow_sealer_soak_gate_ok = shadow_sealer_soak_gate['overall_ok']
    dynamic_provider_fallback_gate = evaluate_dynamic_provider_fallback_gate(ROOT)
    DYNAMIC_PROVIDER_FALLBACK_GATE_REPORT_PATH.write_text(
        json.dumps(dynamic_provider_fallback_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    dynamic_provider_fallback_gate_ok = dynamic_provider_fallback_gate['overall_ok']
    production_packaging_smoke_gate = evaluate_production_packaging_smoke_gate(ROOT)
    PRODUCTION_PACKAGING_SMOKE_GATE_REPORT_PATH.write_text(
        json.dumps(production_packaging_smoke_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    production_packaging_smoke_gate_ok = production_packaging_smoke_gate['overall_ok']
    external_deployment_smoke_gate = evaluate_external_deployment_smoke_gate(ROOT)
    EXTERNAL_DEPLOYMENT_SMOKE_GATE_REPORT_PATH.write_text(
        json.dumps(external_deployment_smoke_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    external_deployment_smoke_gate_ok = external_deployment_smoke_gate['overall_ok']
    deployment = build_deployment_manifest(ROOT)
    DEPLOYMENT_MANIFEST_REPORT_PATH.write_text(
        json.dumps(deployment.to_report(), indent=2, sort_keys=True),
        encoding='utf-8',
    )
    deployment_manifest_ok = deployment.overall_ok
    e2e_release_gate = evaluate_e2e_release_gate(ROOT)
    E2E_RELEASE_GATE_REPORT_PATH.write_text(
        json.dumps(e2e_release_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    e2e_release_gate_ok = e2e_release_gate['overall_ok']
    production_readiness = _production_readiness_summary(deployment, e2e_release_gate)
    PRODUCTION_READINESS_REPORT_PATH.write_text(
        json.dumps(production_readiness, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    production_closure_workflow = build_production_closure_workflow(
        ROOT,
        production_readiness,
        execute=False,
    )
    PRODUCTION_CLOSURE_WORKFLOW_REPORT_PATH.write_text(
        json.dumps(production_closure_workflow, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    production_closure_workflow_ok = production_closure_workflow['overall_ok']
    progress_payload = {
        'benchmark_gate': benchmark_gate,
        'governance_gate': governance_gate,
        'e2e_release_gate': e2e_release_gate,
        'provider_route_gate': provider_route_gate,
        'dependency_audit_gate': dependency_audit_gate,
        'supply_chain_gate': supply_chain_gate,
        'cluster_loopback_gate': cluster_loopback_gate,
        'quickjs_cold_start_gate': quickjs_cold_start_gate,
        'tcp_cluster_soak_gate': tcp_cluster_soak_gate,
        'hot_browser_shadow_gate': hot_browser_shadow_gate,
        'shadow_sealer_soak_gate': shadow_sealer_soak_gate,
        'dynamic_provider_fallback_gate': dynamic_provider_fallback_gate,
        'production_packaging_smoke_gate': production_packaging_smoke_gate,
        'external_deployment_smoke_gate': external_deployment_smoke_gate,
        'deployment_manifest': deployment.to_report(),
        'constitution_audit': constitution_audit,
        'sota_baseline_gate': sota_baseline_gate,
        'hermes_baseline_gate': hermes_baseline_gate,
        'hermes_rpc_baseline_gate': hermes_rpc_baseline_gate,
        'hermes_session_recovery_baseline_gate': hermes_session_recovery_baseline_gate,
        'hermes_persistence_baseline_gate': hermes_persistence_baseline_gate,
        'replay_chaos_scorecard': replay_chaos_scorecard,
        'replay_endurance_report': replay_endurance_report,
        'browser_ops_bench_verification_report': browser_ops_bench_verification_report,
        'agentic_sdk_context_report': agentic_sdk_context_report,
        'cluster_loopback_report': cluster_loopback_report,
        'quickjs_cold_start_report': quickjs_cold_start_report,
        'tcp_cluster_soak_report': tcp_cluster_soak_report,
        'hot_browser_shadow_report': hot_browser_shadow_report,
        'shadow_sealer_soak_report': shadow_sealer_soak_report,
        'dynamic_provider_fallback_report': dynamic_provider_fallback_report,
        'production_readiness': production_readiness,
        'production_closure_workflow': production_closure_workflow,
    }
    progress_gate = evaluate_progress_gate(ROOT, progress_payload)
    PROGRESS_GATE_REPORT_PATH.write_text(
        json.dumps(progress_gate, indent=2, sort_keys=True),
        encoding='utf-8',
    )
    progress_gate_ok = progress_gate['overall_ok']
    operator_snapshot_artifacts = (
        'benchmark_gate_report.json',
        'constitution_audit_report.json',
        'governance_gate_report.json',
        'e2e_release_gate_report.json',
        'provider_route_gate_report.json',
        'dependency_audit_gate_report.json',
        'supply_chain_gate_report.json',
        'cluster_loopback_gate_report.json',
        'quickjs_cold_start_gate_report.json',
        'tcp_cluster_soak_gate_report.json',
        'hot_browser_shadow_gate_report.json',
        'shadow_sealer_soak_gate_report.json',
        'dynamic_provider_fallback_gate_report.json',
        'production_packaging_smoke_gate_report.json',
        'external_deployment_smoke_gate_report.json',
        'deployment_manifest_report.json',
        'production_readiness_report.json',
        'production_closure_workflow_report.json',
        'progress_gate_report.json',
    )
    operator_snapshot = build_operator_evidence_snapshot(Path(ROOT), operator_snapshot_artifacts)
    operator_evidence_api = {
        'schema': 'aegis-operator-evidence-api-run-checks-v1',
        'truth_claim': operator_snapshot.truth_claim,
        'verifier': operator_snapshot.verifier,
        'python_role': operator_snapshot.python_role,
        'release_gate_observed_ok': operator_snapshot.release_gate_observed_ok,
        'missing_artifact_count': len(operator_snapshot.missing_artifacts),
        'artifact_count': len(operator_snapshot.artifact_summaries),
        'artifact_names': [summary.logical_name for summary in operator_snapshot.artifact_summaries],
        'snapshot_digest': operator_snapshot.snapshot_digest.to_dict(),
    }
    operator_evidence_api_ok = (
        operator_snapshot.truth_claim is False
        and operator_snapshot.release_gate_observed_ok
        and len(operator_snapshot.missing_artifacts) == 0
    )

    report = {
        'production_readiness': production_readiness,
        'production_deployable': production_readiness['production_deployable'],
        'active_production_blocker_count': production_readiness['active_production_blocker_count'],
        'active_production_blocker_hash': production_readiness['active_production_blocker_hash'],
        'active_production_blocker_ids': production_readiness['active_production_blocker_ids'],
        'preflight': {
            'ready_to_benchmark': preflight.ready_to_benchmark,
            'cargo_present': preflight.cargo_present,
            'rustc_present': preflight.rustc_present,
            'rust_toolchain_present': preflight.rust_toolchain_present,
            'python_bridge_present': preflight.python_bridge_present,
            'rust_core_present': preflight.rust_core_present,
        },
        'smoke': {
            'overall_ok': smoke.overall_ok,
            'bridge_ok': smoke.contract.bridge_ok,
            'payload_size_ok': smoke.payload_size_ok,
            'identity_ok': smoke.identity_ok,
        },
        'batch': {
            'overall_ok': batch.overall_ok,
            'total_messages': batch.total_messages,
            'passed_messages': batch.passed_messages,
        },
        'memory': {
            'surface_ready': True,
        },
        'speculative': {
            'surface_ready': True,
        },
        'service_manifest': {
            'service_name': manifest.service_name,
            'version': manifest.version,
            'bridge_ready': manifest.bridge_ready,
            'contract_ok': manifest.contract_ok,
            'runtime_surface_ok': manifest.runtime_surface_ok,
            'packaging_ready': manifest.packaging_ready,
        },
        'deployment_manifest': {
            'root': deployment.root,
            'python_bridge_ready': deployment.python_bridge_ready,
            'rust_core_ready': deployment.rust_core_ready,
            'packaging_ready': deployment.packaging_ready,
            'service_surface_ready': deployment.service_surface_ready,
            'cargo_manifest_ready': deployment.cargo_manifest_ready,
            'docs_ready': deployment.docs_ready,
            'artifacts_dir_ready': deployment.artifacts_dir_ready,
            'operator_api_ready': deployment.operator_api_ready,
            'release_profile': deployment.release_profile,
            'topology_id': deployment.topology_id,
            'topology_hash': deployment.topology_hash,
            'topology_contract_hash': deployment.topology_contract_hash,
            'service_boundary_hash': deployment.service_boundary_hash,
            'service_placement_hash': deployment.service_placement_hash,
            'network_edge_hash': deployment.network_edge_hash,
            'storage_volume_hash': deployment.storage_volume_hash,
            'process_model_hash': deployment.process_model_hash,
            'health_check_hash': deployment.health_check_hash,
            'rollback_plan_hash': deployment.rollback_plan_hash,
            'operator_runbook_hash': deployment.operator_runbook_hash,
            'production_blocker_hash': deployment.production_blocker_hash,
            'active_production_blocker_hash': deployment.active_production_blocker_hash,
            'config_layer_hash': deployment.config_layer_hash,
            'artifact_index_hash': deployment.artifact_index_hash,
            'release_manifest_hash': deployment.release_manifest_hash,
            'release_attestation_hash': deployment.release_attestation_hash,
            'deployment_manifest_hash': deployment.deployment_manifest_hash,
            'non_core_commit_authority_count': deployment.non_core_commit_authority_count,
            'topology_cannot_weaken_policy': deployment.topology_cannot_weaken_policy,
            'remote_workers_candidate_only': deployment.remote_workers_candidate_only,
            'service_placements_cover_boundaries': deployment.service_placements_cover_boundaries,
            'single_writer_placement_enforced': deployment.single_writer_placement_enforced,
            'network_edges_candidate_only': deployment.network_edges_candidate_only,
            'durable_storage_defined': deployment.durable_storage_defined,
            'health_checks_defined': deployment.health_checks_defined,
            'operator_rollback_defined': deployment.operator_rollback_defined,
            'operator_runbook_defined': deployment.operator_runbook_defined,
            'production_blockers_declared': deployment.production_blockers_declared,
            'active_production_blockers': list(deployment.active_production_blockers),
            'production_packaging_smoke_present': deployment.production_packaging_smoke_present,
            'production_packaging_smoke_hash': deployment.production_packaging_smoke_hash,
            'external_signed_attestation_present': deployment.external_signed_attestation_present,
            'external_deployment_smoke_hash': deployment.external_deployment_smoke_hash,
            'container_image_attestation_present': deployment.container_image_attestation_present,
            'external_deployment_smoke_present': deployment.external_deployment_smoke_present,
            'production_deployable': deployment.production_deployable,
            'overall_ok': deployment.overall_ok,
        },
        'benchmark_gate': benchmark_gate,
        'benchmark_profile': benchmark_profile,
        'baseline_gates_hard_required': baseline_gates_hard_required,
        'governance_gate': governance_gate,
        'e2e_release_gate': e2e_release_gate,
        'provider_route_gate': provider_route_gate,
        'dependency_audit_gate': dependency_audit_gate,
        'supply_chain_gate': supply_chain_gate,
        'cluster_loopback_gate': cluster_loopback_gate,
        'quickjs_cold_start_gate': quickjs_cold_start_gate,
        'tcp_cluster_soak_gate': tcp_cluster_soak_gate,
        'hot_browser_shadow_gate': hot_browser_shadow_gate,
        'shadow_sealer_soak_gate': shadow_sealer_soak_gate,
        'dynamic_provider_fallback_gate': dynamic_provider_fallback_gate,
        'production_packaging_smoke_gate': production_packaging_smoke_gate,
        'external_deployment_smoke_gate': external_deployment_smoke_gate,
        'production_closure_workflow': production_closure_workflow,
        'python_hotpath_gate': python_hotpath_gate,
        'sota_baseline_gate': sota_baseline_gate,
        'hermes_baseline_gate': hermes_baseline_gate,
        'hermes_rpc_baseline_gate': hermes_rpc_baseline_gate,
        'hermes_session_recovery_baseline_gate': hermes_session_recovery_baseline_gate,
        'hermes_persistence_baseline_gate': hermes_persistence_baseline_gate,
        'benchmark_cache': benchmark_cache,
        'cli_report_cache': cli_report_cache,
        'baseline_gate_cache': baseline_gate_cache,
        'replay_chaos_scorecard': replay_chaos_scorecard,
        'replay_endurance_report': replay_endurance_report,
        'browser_ops_bench_verification_report': browser_ops_bench_verification_report,
        'agentic_sdk_context_report': agentic_sdk_context_report,
        'cluster_loopback_report': cluster_loopback_report,
        'quickjs_cold_start_report': quickjs_cold_start_report,
        'tcp_cluster_soak_report': tcp_cluster_soak_report,
        'hot_browser_shadow_report': hot_browser_shadow_report,
        'shadow_sealer_soak_report': shadow_sealer_soak_report,
        'dynamic_provider_fallback_report': dynamic_provider_fallback_report,
        'operator_evidence_api': operator_evidence_api,
        'constitution_audit': constitution_audit,
        'progress_gate': progress_gate,
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    report_json = json.dumps(report, indent=2, sort_keys=True)
    REPORT_PATH.write_text(report_json, encoding='utf-8')
    if force_fresh:
        FORCE_FRESH_REPORT_PATH.write_text(report_json, encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if (
        smoke.overall_ok 
        and batch.overall_ok 
        and manifest.packaging_ready 
        and deployment.packaging_ready 
        and deployment.cargo_manifest_ready 
        and deployment.docs_ready 
        and deployment.artifacts_dir_ready
        and deployment.operator_api_ready
        and deployment_manifest_ok
        and benchmark_gate_ok
        and python_hotpath_gate_ok
        and (sota_baseline_gate_ok or not baseline_gates_hard_required)
        and (hermes_baseline_gate_ok or not baseline_gates_hard_required)
        and (hermes_rpc_baseline_gate_ok or not baseline_gates_hard_required)
        and (hermes_session_recovery_baseline_gate_ok or not baseline_gates_hard_required)
        and (hermes_persistence_baseline_gate_ok or not baseline_gates_hard_required)
        and replay_chaos_scorecard_ok
        and replay_endurance_report_ok
        and browser_ops_bench_verification_report_ok
        and agentic_sdk_context_report_ok
        and cluster_loopback_report_ok
        and quickjs_cold_start_report_ok
        and tcp_cluster_soak_report_ok
        and hot_browser_shadow_report_ok
        and shadow_sealer_soak_report_ok
        and dynamic_provider_fallback_report_ok
        and operator_evidence_api_ok
        and governance_gate_ok
        and e2e_release_gate_ok
        and provider_route_gate_ok
        and dependency_audit_gate_ok
        and supply_chain_gate_ok
        and cluster_loopback_gate_ok
        and quickjs_cold_start_gate_ok
        and tcp_cluster_soak_gate_ok
        and hot_browser_shadow_gate_ok
        and shadow_sealer_soak_gate_ok
        and dynamic_provider_fallback_gate_ok
        and production_packaging_smoke_gate_ok
        and external_deployment_smoke_gate_ok
        and production_closure_workflow_ok
        and progress_gate_ok
        and constitution_audit_ok
    ) else 1


if __name__ == '__main__':
    raise SystemExit(main())
