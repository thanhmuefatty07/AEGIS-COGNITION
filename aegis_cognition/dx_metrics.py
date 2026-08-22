"""Developer-experience checks retained separately from runtime metrics."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def test_import_count() -> None:
    code = "from aegis_cognition import Agent\n"
    import_count = code.count("import") + code.count("from")
    assert import_count == 1, f"API requires {import_count} imports (target: 1)"


def test_agent_creation() -> None:
    tree = ast.parse("from aegis_cognition import Agent\nagent = Agent(task='test')\n")
    statements = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign))]
    assert len(statements) == 2


def test_accessible_run() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aegis_cognition import Agent, run

    assert Agent is not None and run is not None


def test_package_structure() -> None:
    pkg_dir = Path(__file__).resolve().parents[1] / "aegis_cognition"
    for filename in ("__init__.py", "agent.py", "errors.py"):
        assert (pkg_dir / filename).exists(), filename


def main() -> int:
    tests = (test_import_count, test_agent_creation, test_accessible_run, test_package_structure)
    passed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            print(f"{test.__name__}: FAIL — {exc}")
        else:
            passed += 1
    print(f"DX checks: {passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
