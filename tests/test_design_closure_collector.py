from __future__ import annotations

import json
import runpy
import tempfile
from pathlib import Path


COLLECTOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "architecture"
    / "design-closure"
    / "design_closure_collect.py"
)


def test_collector_redacts_machine_paths_and_labels_missing_temp_evidence() -> None:
    collector = runpy.run_path(str(COLLECTOR_PATH), run_name="collector_probe")
    root = collector["ROOT"]
    sanitize = collector["sanitize"]
    payload = sanitize(
        {
            "repo": str(root / "scripts" / "smoke_check.py"),
            "temp": str(Path(tempfile.gettempdir()) / "probe.json"),
            "home": str(Path.home() / "private.txt"),
        }
    )
    reconciliation = collector["v63_reconcile"]()
    serialized = json.dumps(reconciliation)

    assert payload["repo"].replace("\\", "/") == "<repo>/scripts/smoke_check.py"
    assert payload["temp"].replace("\\", "/") == "<system-temp>/probe.json"
    assert payload["home"].replace("\\", "/") == "<user-home>/private.txt"
    assert reconciliation["expected_root"] == "<system-temp>/aegis-lab-native-wheel-v63"
    assert str(root).casefold() not in serialized.casefold()
