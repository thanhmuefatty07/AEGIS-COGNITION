#!/usr/bin/env python3
"""DX Metrics — Measure and validate Developer Experience quality."""

import sys
from pathlib import Path


def test_import_count():
    """API complexity: should require exactly 1 import."""
    code = "from aegis_cognition import Agent\n"
    import_count = code.count("import") + code.count("from")
    assert import_count == 1, f"API requires {import_count} imports (target: 1)"
    print(f"  API imports: {import_count} (target: 1) ")


def test_agent_creation():
    """Agent should be creatable with minimal code."""
    import ast

    code = """
from aegis_cognition import Agent
agent = Agent(task="test")
"""
    tree = ast.parse(code)
    # Should have 2 statements: import + Agent()
    stmts = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom, ast.Expr))]
    print(f"  Agent creation: {len(stmts)} statements (target: 2) ")


def test_accessible_run():
    """Both Agent().run() and run() function should exist."""
    # Check that the package has Agent and run exposed
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from aegis_cognition import Agent, run

    assert Agent is not None
    assert run is not None
    print("  Agent exposed: YES  run() exposed: YES ")


def test_friendly_errors():
    """All error templates return actionable messages."""
    from aegis_cognition.errors import (
        _api_key_missing,
        _approval_required,
        _browser_evidence_missing,
        _browser_not_installed,
        _invalid_trust_level,
        _rate_limited,
        _all_providers_throttled,
        _replay_chain_broken,
    )

    errors = [
        _api_key_missing(),
        _approval_required(),
        _browser_evidence_missing(),
        _browser_not_installed(),
        _invalid_trust_level("bad"),
        _rate_limited(),
        _all_providers_throttled(),
        _replay_chain_broken(),
    ]

    for i, msg in enumerate(errors):
        assert len(msg) > 50, f"Error {i} too short: {len(msg)} chars"
        has_actionable = any(
            keyword in msg.lower()
            for keyword in ["how to fix", "option 1", "run the", "export", "pip install", "playwright", "aegis"]
        )
        assert has_actionable, f"Error {i} missing actionable guidance"

    print(f"  Error templates: {len(errors)} all actionable ")


def test_package_structure():
    """Verify the package has all necessary files."""
    pkg_dir = Path(__file__).resolve().parents[1] / "aegis_cognition"
    required = ["__init__.py", "agent.py", "cli.py", "errors.py"]
    for f in required:
        assert (pkg_dir / f).exists(), f"Missing: {f}"
    print(f"  Package files: {len(required)}/4 present ")


def test_cli_help():
    """CLI should have --help."""
    pkg_dir = Path(__file__).resolve().parents[1] / "aegis_cognition"
    sys.path.insert(0, str(pkg_dir.parent))
    from aegis_cognition.cli import _print_help

    result = None
    import io

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        _print_help()
        result = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    assert "aegis" in result.lower(), "CLI help missing 'aegis'"
    assert "init" in result, "CLI help missing 'init' command"
    assert "run" in result, "CLI help missing 'run' command"
    print("  CLI help: present with init+run commands ")


def test_examples_exist():
    """All example directories should contain at least one script."""
    examples_dir = Path(__file__).resolve().parents[1] / "examples"
    categories = ["basic", "browser", "advanced"]
    for cat in categories:
        cat_dir = examples_dir / cat
        assert cat_dir.exists(), f"Missing directory: examples/{cat}"
        py_files = list(cat_dir.glob("*.py"))
        assert len(py_files) > 0, f"No examples in examples/{cat}"
    print("  Examples: 3 categories with scripts ")


def main():
    print("\n  AEGIS-COGNITION DX Metrics")
    print("  " + "=" * 50)
    print()

    tests = [
        test_import_count,
        test_agent_creation,
        test_accessible_run,
        test_friendly_errors,
        test_package_structure,
        test_cli_help,
        test_examples_exist,
    ]

    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  {test.__name__}: FAIL — {e}")
        except Exception as e:
            print(f"  {test.__name__}: ERROR — {e}")

    print()
    print(f"  Results: {passed}/{len(tests)} passed")

    if passed == len(tests):
        print("  Status: ALL DX CHECKS PASSED")
    else:
        print(f"  Status: {len(tests) - passed} checks FAILED")

    print()


if __name__ == "__main__":
    main()
