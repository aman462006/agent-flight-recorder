"""
bench/real/evaluate.py — score the existing investigator against HUMAN labels.

    python -m bench.real.evaluate

Loads every trace in bench/real/traces/ that has a label in labels.json,
runs the UNCHANGED Phase-6 investigator on it, and compares:

    investigator "flags" a trace = any critical or warning finding
    human "failure"              = label != success

Reports overall + per-rule precision and recall, and lists every miss
(human failure, investigator silent) and false positive (human success,
investigator flagged) with trace IDs, so nothing hides in an average.

The rules are not tuned here and the numbers are whatever they are.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(HERE, "traces")
LABELS_PATH = os.path.join(HERE, "labels.json")

FLAG_SEVERITIES = {"critical", "warning"}  # info findings are notes, not flags


def investigator_findings(trace: dict):
    """Run the stock investigator over one trace, return findings."""
    from afrec.investigator import investigate_run
    inv = investigate_run(trace["events"], run_meta=trace.get("run_meta"),
                           run_id=trace.get("run_id"))
    return inv.findings


def main() -> None:
    if not os.path.exists(LABELS_PATH):
        print("no labels.json — run  python -m bench.real.label  first.")
        print("(labels are human-only in this pipeline; nothing auto-labels.)")
        return
    labels = json.load(open(LABELS_PATH))
    if not labels:
        print("labels.json is empty — run  python -m bench.real.label  first.")
        return

    tp, fp, fn, tn = 0, 0, 0, 0
    per_rule = defaultdict(lambda: Counter())       # rule -> fires on tp/fp
    per_humantype = defaultdict(lambda: Counter())  # human failure type -> detected?
    misses, false_positives = [], []

    for fname in sorted(os.listdir(TRACES_DIR)):
        if not fname.endswith(".json") or fname == "labels.json":
            continue  # labels are never traces
        trace = json.load(open(os.path.join(TRACES_DIR, fname)))
        if not isinstance(trace, dict) or "task_id" not in trace:
            continue  # not a trace file; never guess
        tid = trace["task_id"]
        if tid not in labels:
            continue

        findings = investigator_findings(trace)
        flag_findings = [f for f in findings if getattr(f, "severity", "warning") in FLAG_SEVERITIES]
        flagged = len(flag_findings) > 0
        human_failed = labels[tid]["label"] != "success"

        if human_failed and flagged:
            tp += 1
            for f in flag_findings:
                per_rule[f.rule]["tp"] += 1
            per_humantype[labels[tid].get("failure_type") or "other"]["detected"] += 1
        elif human_failed and not flagged:
            fn += 1
            misses.append((tid, labels[tid].get("failure_type"), trace["task"][:60]))
            per_humantype[labels[tid].get("failure_type") or "other"]["missed"] += 1
        elif not human_failed and flagged:
            fp += 1
            false_positives.append(
                (tid, [f.rule for f in flag_findings], trace["task"][:60]))
            for f in flag_findings:
                per_rule[f.rule]["fp"] += 1
        else:
            tn += 1

    n = tp + fp + fn + tn
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None

    def pct(x):
        return f"{100*x:.0f}%" if x is not None else "n/a"

    print(f"\nREAL-RUN EVALUATION — {n} labelled trace(s) vs the stock investigator")
    print("=" * 70)
    print(f"{'':22}flagged by investigator   clean")
    print(f"human said FAILURE     {tp:>10}             {fn:>6}   <- recall {pct(recall)}")
    print(f"human said SUCCESS     {fp:>10}             {tn:>6}")
    print(f"precision (of flags): {pct(precision)}   |   recall (of failures): {pct(recall)}")

    if per_rule:
        print("\nper-rule firing counts:")
        print(f"  {'rule':28}{'on human failures':>20}{'on human successes':>22}")
        for rule, c in sorted(per_rule.items()):
            print(f"  {rule:28}{c['tp']:>20}{c['fp']:>22}")

    if per_humantype:
        print("\nby human failure type:")
        for ft, c in sorted(per_humantype.items()):
            print(f"  {ft:24} detected {c['detected']:>2}  missed {c['missed']:>2}")

    if misses:
        print("\nMISSES (human failure, investigator clean):")
        for tid, ft, task in misses:
            print(f"  {tid} [{ft}] {task}")

    if false_positives:
        print("\nFALSE POSITIVES (human success, investigator flagged):")
        for tid, rules, task in false_positives:
            print(f"  {tid} rules={rules} {task}")

    if n:
        print(f"\nBottom line: precision {pct(precision)}, recall {pct(recall)} "
              f"on {n} real, human-labelled traces. Numbers as measured; "
              f"rules untouched.")
    else:
        print("\nno labelled traces found in traces/ — collect then label.")


if __name__ == "__main__":
    main()
