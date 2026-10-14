"""
Phase 7 — Benchmark runner.

Runs the full scenario suite through the Phase 6 investigator and
measures, per failure type and per rule:

- RECALL: of scenarios where a ground-truth rule should fire, the
  fraction where it did.
- PRECISION: of all findings of a rule, the fraction on scenarios where
  that rule was in ground truth.
- FALSE POSITIVES on clean scenarios: must be zero findings on clean.
- LATENCY: wall time per investigation.

Honesty rules:
- metrics are computed from the actual run, printed and written to
  bench/results/benchmark_results.json — never hand-edited
- anything unmeasured is reported as unmeasured
- thresholds in the tests are conservative floors, not marketing numbers

Usage: python -m bench.run_benchmark [--seed N]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from typing import Any, Dict, List

from afrec.investigator import investigate_run
from bench.scenarios import Scenario, generate_scenarios, GROUND_TRUTH, CLEAN, MIX


def run_benchmark(seed: int = 42) -> Dict[str, Any]:
    scenarios = generate_scenarios(seed)
    results: List[Dict[str, Any]] = []

    for sc in scenarios:
        t0 = time.perf_counter()
        inv = investigate_run(
            sc.events, run_meta=sc.run_meta, run_id=sc.run_meta.get("run_id"),
            baseline_events=sc.baseline_events,
            baseline_run_id=(sc.baseline_meta or {}).get("run_id"),
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        fired_rules = {f.rule for f in inv.findings}
        expected = set(sc.expected_rules)
        results.append({
            "scenario_id": sc.scenario_id,
            "failure_type": sc.failure_type,
            "fired_rules": sorted(fired_rules),
            "expected_rules": sorted(expected),
            "missed_rules": sorted(expected - fired_rules),
            "false_positive_rules": sorted(fired_rules - expected),
            "clean_reported_clean": inv.clean if sc.failure_type == CLEAN else None,
            "latency_ms": round(elapsed_ms, 3),
            "finding_count": len(inv.findings),
        })

    # ---- aggregate: per failure type ----------------------------------------
    by_type: Dict[str, Dict[str, Any]] = {}
    for ftype in GROUND_TRUTH:
        subset = [r for r in results if r["failure_type"] == ftype]
        detected = [r for r in subset if not r["missed_rules"]]
        by_type[ftype] = {
            "scenarios": len(subset),
            "fully_detected": len(detected),
            "recall": round(len(detected) / len(subset), 4) if subset else None,
        }

    # ---- aggregate: per rule -------------------------------------------------
    rule_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"true": 0, "false": 0})
    for r in results:
        for rule in r["fired_rules"]:
            key = "true" if rule in r["expected_rules"] else "false"
            rule_stats[rule][key] += 1
    # expected-but-missed count too (recall side)
    for r in results:
        for rule in r["missed_rules"]:
            rule_stats[rule]["missed"] = rule_stats[rule].get("missed", 0) + 1
    per_rule = {
        rule: {
            "true_positives": s["true"],
            "false_positives": s["false"],
            "missed_expected": s.get("missed", 0),
            "precision": round(s["true"] / (s["true"] + s["false"]), 4) if (s["true"] + s["false"]) else None,
        }
        for rule, s in sorted(rule_stats.items())
    }

    clean = [r for r in results if r["failure_type"] == CLEAN]
    latencies = [r["latency_ms"] for r in results]

    report = {
        "seed": seed,
        "scenarios": len(scenarios),
        "scenario_mix": {t: MIX.count(t) for t in sorted(set(MIX))},
        "clean_scenarios": len(clean),
        "clean_false_positive_findings": sum(r["finding_count"] for r in clean),
        "clean_correctly_reported_clean": sum(1 for r in clean if r["clean_reported_clean"]),
        "failure_scenarios": len(scenarios) - len(clean),
        "failures_fully_detected": sum(1 for r in results if r["failure_type"] != CLEAN and not r["missed_rules"]),
        "per_failure_type": by_type,
        "per_rule": per_rule,
        "latency": {
            "mean_ms": round(sum(latencies) / len(latencies), 3),
            "max_ms": round(max(latencies), 3),
        },
        "notes": [
            "recall = scenarios where every ground-truth rule fired; a crashed run "
            "must also fire unclosed_span and run_never_ended to count",
            "precision is per finding, not per scenario; secondary honest findings "
            "on failure scenarios are in ground truth, not counted as noise",
            "clean scenarios must produce zero findings — the clean false-positive "
            "count is the headline number",
        ],
    }
    return {"report": report, "results": results}


def print_report(report: Dict[str, Any]) -> None:
    r = report
    print("=" * 64)
    print("AGENT FLIGHT RECORDER — INVESTIGATOR BENCHMARK")
    print("=" * 64)
    print(f"scenarios: {r['scenarios']}  "
          f"(clean: {r['clean_scenarios']}, failures: {r['failure_scenarios']})")
    print()
    print(f"clean runs reported clean:   {r['clean_correctly_reported_clean']}/{r['clean_scenarios']}")
    print(f"clean false-positive findings: {r['clean_false_positive_findings']}")
    print(f"failures fully detected:      {r['failures_fully_detected']}/{r['failure_scenarios']}")
    print()
    print(f"{'failure type':<22}{'scenarios':>10}{'detected':>10}{'recall':>8}")
    for ftype, s in r["per_failure_type"].items():
        print(f"{ftype:<22}{s['scenarios']:>10}{s['fully_detected']:>10}{s['recall']:>8.2f}")
    print()
    print(f"{'rule':<24}{'true':>6}{'false':>7}{'missed':>8}{'precision':>11}")
    for rule, s in r["per_rule"].items():
        prec = "—" if s["precision"] is None else f"{s['precision']:.2f}"
        print(f"{rule:<24}{s['true_positives']:>6}{s['false_positives']:>7}"
              f"{s['missed_expected']:>8}{prec:>11}")
    print()
    print(f"latency per investigation: mean {r['latency']['mean_ms']} ms, "
          f"max {r['latency']['max_ms']} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the investigator benchmark")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", type=str, default=None,
                        help="also write the full report (results included) to this path")
    args = parser.parse_args()

    out = run_benchmark(seed=args.seed)
    print_report(out["report"])

    path = args.json or os.environ.get("AFREC_BENCH_OUT") or os.path.join(
        os.path.dirname(__file__), "results", f"benchmark_seed{args.seed}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2)
    print(f"\nfull results written to {path}")


if __name__ == "__main__":
    main()
