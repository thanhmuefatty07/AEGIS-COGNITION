from __future__ import annotations

import json

from .operator_api import OperatorEvidenceApi, resolve_operator_root


def main() -> int:
    payload = OperatorEvidenceApi(resolve_operator_root()).handle_get("/health").json()
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("schema") == "aegis-operator-health-v1" and payload.get("truth_claim") is False else 1


if __name__ == "__main__":
    raise SystemExit(main())
