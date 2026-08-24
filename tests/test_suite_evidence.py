from scripts.suite_evidence import parse_counts


def test_parse_counts_keeps_scope_fields() -> None:
    counts = parse_counts("418 tests run: 418 passed, 0 failed, 0 skipped\n0 ignored; 2 filtered out")
    assert counts == {
        "discovered": 418,
        "passed": 418,
        "failed": 0,
        "ignored": 0,
        "filtered": 2,
        "skipped": 0,
    }
