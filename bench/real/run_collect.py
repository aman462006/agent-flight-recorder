"""
bench/real/run_collect.py — run every task in tasks.json against the REAL
model and save each trace to bench/real/traces/<task_id>.json.

Usage:
    python -m bench.real.run_collect                 # all tasks, skips existing
    python -m bench.real.run_collect --start 1 --end 5
    python -m bench.real.run_collect --force          # re-run and overwrite
    python -m bench.real.run_collect --temperature 0.2 --model gemini-2.0-flash

Every trace records model name, temperature, and collection date, so the
labelled evaluation is reproducible and citable.
"""

from __future__ import annotations

import argparse
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.join(HERE, "traces")


def main() -> None:
    ap = argparse.ArgumentParser(description="Collect real agent traces")
    ap.add_argument("--start", type=int, default=1, help="first task number (1-based)")
    ap.add_argument("--end", type=int, default=0, help="last task number (inclusive)")
    ap.add_argument("--force", action="store_true", help="overwrite existing traces")
    ap.add_argument("--model", default=None, help="override model (default from env/Gemini default)")
    ap.add_argument("--temperature", type=float, default=None)
    args = ap.parse_args()

    from bench.real.agent import GeminiLLM, run_task

    os.makedirs(TRACES_DIR, exist_ok=True)
    tasks = json.load(open(os.path.join(HERE, "tasks.json")))["tasks"]
    if args.end:
        tasks = [t for t in tasks if args.start <= int(t["id"][1:]) <= args.end]
    else:
        tasks = [t for t in tasks if int(t["id"][1:]) >= args.start]

    llm = GeminiLLM(model=args.model,
                    temperature=args.temperature if args.temperature is not None else 0.7)
    print(f"collecting {len(tasks)} task(s) | model={llm.model} temp={llm.temperature}")

    done, skipped, failed = 0, 0, 0
    for t in tasks:
        path = os.path.join(TRACES_DIR, f"{t['id']}.json")
        if os.path.exists(path) and not args.force:
            print(f"  {t['id']}: skipped (trace exists, use --force to redo)")
            skipped += 1
            continue
        try:
            trace = run_task(t["task"], llm, task_id=t["id"])
            # db_path is a local temp artifact, not part of the eval data
            trace.pop("db_path", None)
            with open(path, "w") as f:
                json.dump(trace, f, indent=1)
            done += 1
            ans = (trace["final_answer"] or "<no answer>").replace("\n", " ")[:80]
            print(f"  {t['id']}: ok | {trace['tool_calls']} tool calls, "
                  f"{trace['parse_errors']} parse errors | answer: {ans}")
        except Exception as e:
            failed += 1
            print(f"  {t['id']}: FAILED to collect ({type(e).__name__}: {str(e)[:120]})")
        time.sleep(1.0)  # be gentle with rate limits

    print(f"\ndone: {done} collected, {skipped} skipped, {failed} failed")
    print(f"traces in {TRACES_DIR}")
    print("next: python -m bench.real.label   (a human labels each trace)")


if __name__ == "__main__":
    main()
