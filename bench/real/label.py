"""
bench/real/label.py — a HUMAN labelling CLI. No auto-labelling, ever.

Shows each collected trace (task, tool calls with arguments and results,
final answer, errors) and asks YOU to judge it:

    python -m bench.real.label                # label every unlabelled trace
    python -m bench.real.label --id r07       # (re)label one specific trace
    python -m bench.real.label --list         # summary of labels so far

For each trace you choose success or a failure type, and may add a free-text
note. Labels land in bench/real/labels.json. Nothing in this pipeline writes
a label unless a human typed it here.
"""

from __future__ import annotations

import argparse
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(HERE, "traces")
LABELS_PATH = os.path.join(HERE, "labels.json")

FAILURE_TYPES = [
    "stale_data",          # used an outdated record as current
    "wrong_tool_args",     # malformed or wrong arguments to a tool
    "ignored_error",       # tool errored/returned nothing; agent carried on as if fine
    "loop",                # repeated the same call(s) without progress
    "hallucinated_number", # stated a figure not supported by any tool result
    "wrong_answer",        # answered confidently but factually wrong vs the corpus
    "other",
]


def load_labels() -> dict:
    if os.path.exists(LABELS_PATH):
        return json.load(open(LABELS_PATH))
    return {}


def save_labels(labels: dict) -> None:
    with open(LABELS_PATH, "w") as f:
        json.dump(labels, f, indent=1)


def show_trace(trace: dict) -> None:
    print("=" * 70)
    print(f"TRACE {trace['task_id']}  |  model: {trace['model']}  "
          f"temp: {trace['temperature']}  |  collected: {trace['collected_at']}")
    print(f"TASK: {trace['task']}")
    print(f"FINAL ANSWER: {trace.get('final_answer')!r}")
    print(f"tool calls: {trace.get('tool_calls')}  |  parse errors: {trace.get('parse_errors')}")
    print("-" * 70)
    for ev in trace.get("events", []):
        kind = ev.get("kind")
        if kind == "tool.result" and ev.get("span_id"):
            print(f"  tool.result -> {json.dumps(ev.get('data', {}).get('payload'))[:300]}")
        elif kind == "span.start":
            label = ev.get("data", {}).get("label", "")
            if str(label).startswith("tool:"):
                print(f"  TOOL CALL {label} args={json.dumps(ev.get('data', {}).get('arguments'))}")
        elif kind == "error":
            print(f"  ERROR {json.dumps(ev.get('data'))[:200]}")
    print("=" * 70)


def label_one(trace: dict, labels: dict) -> None:
    show_trace(trace)
    tid = trace["task_id"]
    while True:
        choice = input("[s]uccess / [f]ailure / [k]skip? ").strip().lower()
        if choice in ("s", "k"):
            if choice == "s":
                note = input("optional note (enter to skip): ").strip()
                labels[tid] = {"label": "success", "failure_type": None,
                               "note": note or None,
                               "labeled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
                print(f"  -> {tid} labelled SUCCESS\n")
            return
        if choice == "f":
            print("failure type:")
            for i, ft in enumerate(FAILURE_TYPES, 1):
                print(f"  {i}. {ft}")
            while True:
                pick = input("type number or name: ").strip().lower()
                ft = None
                if pick.isdigit() and 1 <= int(pick) <= len(FAILURE_TYPES):
                    ft = FAILURE_TYPES[int(pick) - 1]
                elif pick in FAILURE_TYPES:
                    ft = pick
                if ft:
                    break
            note = input("optional note (enter to skip): ").strip()
            labels[tid] = {"label": "failure", "failure_type": ft,
                           "note": note or None,
                           "labeled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
            print(f"  -> {tid} labelled FAILURE ({ft})\n")
            return
        print("please answer s, f or k")


def main() -> None:
    ap = argparse.ArgumentParser(description="Human labelling of real traces")
    ap.add_argument("--id", help="label this specific trace id (e.g. r07)")
    ap.add_argument("--list", action="store_true", help="show label summary, label nothing")
    args = ap.parse_args()

    labels = load_labels()

    if args.list:
        if not labels:
            print("no labels yet — run this command without --list")
            return
        for tid, l in sorted(labels.items()):
            print(f"{tid}: {l['label']}" + (f" ({l['failure_type']})" if l['failure_type'] else "")
                  + (f" — {l['note']}" if l.get("note") else ""))
        return

    traces = sorted(os.listdir(TRACES_DIR)) if os.path.exists(TRACES_DIR) else []
    traces = [f for f in traces if f.endswith(".json")]
    if not traces:
        print(f"no traces in {TRACES_DIR} — run python -m bench.real.run_collect first")
        return

    if args.id:
        path = os.path.join(TRACES_DIR, f"{args.id}.json")
        if not os.path.exists(path):
            print(f"no trace {path}")
            return
        label_one(json.load(open(path)), labels)
        save_labels(labels)
        return

    for fname in traces:
        trace = json.load(open(os.path.join(TRACES_DIR, fname)))
        tid = trace["task_id"]
        if tid in labels:
            print(f"{tid}: already labelled ({labels[tid]['label']}) — "
                  f"use --id {tid} to relabel")
            continue
        label_one(trace, labels)
        save_labels(labels)  # save after every label so a ctrl-C loses nothing

    print(f"\n{len(labels)} trace(s) labelled. next: python -m bench.real.evaluate")


if __name__ == "__main__":
    main()
