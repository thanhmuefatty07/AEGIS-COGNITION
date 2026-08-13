import json
from pathlib import Path

ROOT = r'c:\Users\ADMIN\AEGIS-COGNITION'


def main() -> int:
    import sys

    project_root = Path(ROOT)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from core.python import check_build_preflight

    result = check_build_preflight(ROOT)
    report = {
        'cargo_present': result.cargo_present,
        'rustc_present': result.rustc_present,
        'rust_toolchain_present': result.rust_toolchain_present,
        'python_bridge_present': result.python_bridge_present,
        'rust_core_present': result.rust_core_present,
        'ready_to_benchmark': result.ready_to_benchmark,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if result.ready_to_benchmark else 1


if __name__ == '__main__':
    raise SystemExit(main())
