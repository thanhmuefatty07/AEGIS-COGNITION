from __future__ import annotations

import argparse

from .operator_api import resolve_operator_root, serve_operator_evidence_api


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only AEGIS operator evidence API.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = serve_operator_evidence_api(resolve_operator_root(args.root), args.host, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
