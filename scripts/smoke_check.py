import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(r'c:\Users\ADMIN\AEGIS-COGNITION')
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.python import build_message, validate_bridge_batch, validate_bridge_smoke


def main() -> int:
    message = build_message(1, 2, b'abc')
    batch = validate_bridge_batch([message])
    result = validate_bridge_smoke(message)
    report = {
        'overall_ok': result.overall_ok,
        'bridge_ok': result.contract.bridge_ok,
        'payload_size_ok': result.payload_size_ok,
        'identity_ok': result.identity_ok,
        'batch_overall_ok': batch.overall_ok,
        'batch_passed_messages': batch.passed_messages,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if result.overall_ok and batch.overall_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
