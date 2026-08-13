import json
from pathlib import Path

ROOT = r'c:\Users\ADMIN\AEGIS-COGNITION'
ARTIFACTS_DIR = Path(ROOT) / 'artifacts'
REPORT_PATH = ARTIFACTS_DIR / 'benchmark_preflight_report.json'


def main() -> int:
    import sys

    project_root = Path(ROOT)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from core.python import check_build_preflight

    preflight = check_build_preflight(ROOT)
    report = {
        'cargo_present': preflight.cargo_present,
        'rustc_present': preflight.rustc_present,
        'rust_toolchain_present': preflight.rust_toolchain_present,
        'python_bridge_present': preflight.python_bridge_present,
        'rust_core_present': preflight.rust_core_present,
        'ready_to_benchmark': preflight.ready_to_benchmark,
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if preflight.ready_to_benchmark else 1


if __name__ == '__main__':
    raise SystemExit(main())
