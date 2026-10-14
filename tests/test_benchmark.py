"""Phase 7 acceptance tests: the benchmark measures what it claims.

Conservative floors only — these tests pin the numbers the README will
quote, so a regression in the investigator shows up as a red test, not a
quietly degraded benchmark.
"""

import pytest

from bench.run_benchmark import run_benchmark
from bench.scenarios import generate_scenarios, GROUND_TRUTH, CLEAN, MIX


class TestScenarioSuite:
    def test_suite_size_is_in_the_declared_range(self):
        scenarios = generate_scenarios()
        assert 50 <= len(scenarios) <= 120
        assert len(scenarios) == len(MIX)  # 103: 40 clean + 63 failures

    def test_deterministic_same_seed_same_scenarios(self):
        a = generate_scenarios(seed=123)
        b = generate_scenarios(seed=123)
        assert [s.scenario_id for s in a] == [s.scenario_id for s in b]
        assert [s.events for s in a] == [s.events for s in b]

    def test_every_failure_type_has_scenarios_and_ground_truth(self):
        scenarios = generate_scenarios()
        types = {s.failure_type for s in scenarios}
        assert types == set(GROUND_TRUTH)
        for s in scenarios:
            assert s.expected_rules == GROUND_TRUTH[s.failure_type]

    def test_clean_scenarios_have_no_expected_findings(self):
        scenarios = generate_scenarios()
        assert GROUND_TRUTH[CLEAN] == []
        assert sum(1 for s in scenarios if s.failure_type == CLEAN) >= 30


class TestBenchmarkMetrics:
    """Floors, not marketing numbers. If the investigator regresses, these fail."""

    def test_clean_runs_produce_zero_findings(self):
        out = run_benchmark(seed=42)
        r = out["report"]
        assert r["clean_scenarios"] >= 30
        assert r["clean_false_positive_findings"] == 0
        assert r["clean_correctly_reported_clean"] == r["clean_scenarios"]

    def test_all_scripted_failures_detected(self):
        r = run_benchmark(seed=42)["report"]
        assert r["failure_scenarios"] >= 50
        assert r["failures_fully_detected"] == r["failure_scenarios"]

    def test_per_type_recall_meets_floor(self):
        r = run_benchmark(seed=42)["report"]
        for ftype, s in r["per_failure_type"].items():
            if ftype == CLEAN:
                continue
            assert s["recall"] >= 0.95, f"{ftype} recall regressed: {s}"

    def test_per_rule_precision_meets_floor(self):
        r = run_benchmark(seed=42)["report"]
        for rule, s in r["per_rule"].items():
            assert s["precision"] >= 0.95, f"{rule} precision regressed: {s}"

    def test_no_expected_rule_was_ever_missed(self):
        r = run_benchmark(seed=42)["report"]
        for rule, s in r["per_rule"].items():
            assert s["missed_expected"] == 0, f"{rule} missed expected scenarios"

    def test_metrics_hold_across_seeds(self):
        for seed in (7, 99):
            r = run_benchmark(seed=seed)["report"]
            assert r["clean_false_positive_findings"] == 0
            assert r["failures_fully_detected"] == r["failure_scenarios"]

    def test_latency_is_measured_not_asserted(self):
        r = run_benchmark(seed=42)["report"]
        # measured on this machine; the report records it, the test only
        # requires it to exist and be positive
        assert r["latency"]["mean_ms"] > 0
        assert r["latency"]["max_ms"] >= r["latency"]["mean_ms"]

    def test_every_scenario_result_is_self_consistent(self):
        out = run_benchmark(seed=42)
        for res in out["results"]:
            fired, expected = set(res["fired_rules"]), set(res["expected_rules"])
            assert res["missed_rules"] == sorted(expected - fired)
            assert res["false_positive_rules"] == sorted(fired - expected)
