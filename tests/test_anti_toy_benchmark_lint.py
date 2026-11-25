from __future__ import annotations

import textwrap

from scripts.anti_toy_benchmark_lint import (
    ROOT,
    lint_python_benchmark_source,
    lint_rust_bench_source,
    run_lint,
)


def test_rust_linter_rejects_toy_patterns_and_accepts_clean_iter_batched() -> None:
    toy_rust = """
    fn bench_toy(c: &mut Criterion) {
        let mut group = c.benchmark_group("toy");
        group.sample_size(10);
        let data = vec![1u8; 64];
        group.bench_function("bad", |b| {
            b.iter(|| {
                let copy = data.clone();
                let buf = vec![0u8; 16];
                let s = format!("k-{}", copy.len());
                let h = bench_hash(&buf);
                let _ = std::fs::remove_dir_all("tmp");
                (s.len(), h)
            })
        });
    }
    """
    findings = lint_rust_bench_source(toy_rust, path="toy.rs")
    rules = {f.rule_id for f in findings}
    assert {
        "RUST_SMALL_SAMPLE_SIZE",
        "RUST_CLONE_IN_ITER",
        "RUST_VEC_MACRO_IN_ITER",
        "RUST_FORMAT_MACRO_IN_ITER",
        "RUST_FS_DELETE_IN_ITER",
        "RUST_HASH_IN_ITER",
        "RUST_DISCARDED_RESULT",
        "RUST_MISSING_BLACK_BOX",
    }.issubset(rules)

    clean_rust = """
    fn bench_clean(c: &mut Criterion) {
        let mut group = c.benchmark_group("clean");
        group.sample_size(200);
        let data = vec![1u8; 64];
        group.bench_function("good_batched", |b| {
            b.iter_batched(
                || (data.clone(), format!("setup-{}", data.len()), vec![0u8; 32]),
                |(copy, label, mut buf)| {
                    buf[0] ^= copy[0];
                    black_box((label.len(), buf.len()))
                },
                BatchSize::SmallInput,
            )
        });
        let items = [1u8, 2, 3];
        group.bench_function("iterator_not_flagged", |b| {
            b.iter(|| black_box(items.iter().copied().sum::<u8>()))
        });
    }
    """
    assert lint_rust_bench_source(clean_rust, path="clean.rs") == []


def test_python_linter_rejects_toy_theater_tracemalloc_and_criterion_cache() -> None:
    toy_no_aegis = textwrap.dedent("""
    import json
    def run():
        return {"status": "PROVEN", "nv": "NV-005"}
    """)
    f1 = lint_python_benchmark_source(toy_no_aegis, path="scripts/fake.py")
    assert {f.rule_id for f in f1} == {"PYTHON_PROVEN_WITHOUT_AEGIS_IMPORT"}

    toy_tracemalloc = textwrap.dedent("""
    import time, tracemalloc
    from aegis_cognition import aegis_nerve
    def measure():
        tracemalloc.start()
        t0 = time.perf_counter_ns()
        res = aegis_nerve.aegis_status()
        elapsed = time.perf_counter_ns() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return elapsed, peak, res
    """)
    f2 = lint_python_benchmark_source(toy_tracemalloc, path="scripts/fake_mem.py")
    assert {f.rule_id for f in f2} == {"PYTHON_TRACEMALLOC_IN_TIMED_LOOP"}

    toy_criterion_cross = textwrap.dedent("""
    from pathlib import Path
    from aegis_cognition.benchmark import evaluate_paired_benchmark_v3
    def _criterion_estimate_ns(root: Path, name: str):
        return root / "target" / "criterion" / name / "new" / "estimates.json"
    """)
    f3 = lint_python_benchmark_source(
        toy_criterion_cross,
        path="scripts/sota_baseline_gate.py",
        is_cross_arm_comparator=True,
    )
    assert {f.rule_id for f in f3} == {"PYTHON_CRITERION_CACHE_CROSS_ARM"}

    clean_python = textwrap.dedent('''
    """Comparator avoiding estimates.json reads cleanly."""
    import time, tracemalloc
    from aegis_cognition import aegis_nerve
    from aegis_cognition.benchmark import evaluate_paired_benchmark_v3

    def profile_alloc_untimed():
        tracemalloc.start()
        _ = aegis_nerve.aegis_status()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    def measure_live():
        t0 = time.perf_counter_ns()
        _ = aegis_nerve.aegis_status()
        return time.perf_counter_ns() - t0
    ''')
    assert lint_python_benchmark_source(clean_python, path="scripts/clean.py", is_cross_arm_comparator=True) == []


def test_repo_benchmarks_pass_anti_toy_lint() -> None:
    report = run_lint(ROOT)
    assert report.ok, "\n".join(f"[{f.rule_id}] {f.path}:{f.line}: {f.message}" for f in report.findings)
