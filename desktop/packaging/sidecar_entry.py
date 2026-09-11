from __future__ import annotations

import os

# Keep the frozen entry point on the small desktop dependency boundary.  The
# regular package API remains unchanged for all non-sidecar imports.
os.environ.setdefault("AEGIS_DESKTOP_SIDECAR", "1")

from aegis_cognition.desktop_service import main

if __name__ == "__main__":
    raise SystemExit(main())
