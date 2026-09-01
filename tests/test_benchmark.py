from benchmarks.runner import run_benchmark


def test_controlled_benchmark_meets_quality_gates():
    results = run_benchmark()
    metrics = results["metrics"]

    assert metrics["file_recall_at_5"] >= 0.8
    assert metrics["file_recall_at_5"] > metrics["baseline_file_recall_at_5"]
    assert metrics["negative_rejection_rate"] == 1.0
    assert metrics["bounded_trace_rate"] == 1.0
    assert ".changeset/payment-release.md" in results["corpus"]["excluded_low_value_files"]
    assert {"Python", "JavaScript", "Java", "YAML"} <= set(results["corpus"]["languages"])
