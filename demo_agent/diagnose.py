"""In-process diagnosis: run the demo agent's failure mode and ask the
investigator what it thinks, with zero setup.

The demo agent's planted failure (a stale 2022 revenue figure trusted
without the corrective query) is deliberately invisible to single-run
rules — the fresh figure never enters the failing trace. That is the
project's own documented limitation, and it is exactly what the baseline
diff is for: this script runs BOTH variants and investigates the failing
one against the healthy one as its baseline.

Usage:
    python -m demo_agent.diagnose
"""

from __future__ import annotations

import sys

from afrec import get_recorder
from afrec.investigator import investigate_run
from demo_agent.agent import run_agent

TASK = "Compare three solar companies"


def _print(inv, header):
    print(f"{header}: {'CLEAN' if inv.clean else 'FINDINGS'} "
          f"({len(inv.findings)} finding(s))\n")
    for f in inv.findings:
        print(f"  {f.severity:<8} {f.rule}")
        print(f"          observed : {f.observed}")
        print(f"          hypothesis: {f.hypothesis}")
        if f.evidence:
            first = f.evidence[0]
            exc = (first.excerpt or "")[:140]
            print(f"          evidence : {first.kind} {exc}...")
        print()


def main(argv: list[str]) -> int:
    print(f"running demo agent, task: {TASK!r}")
    print("  variant 1: healthy (queries the corrective source)")
    run_agent(TASK, use_stale_figure=False)
    print("  variant 2: failing (trusts the stale 2022 figure)")
    run_agent(TASK, use_stale_figure=True)

    rec = get_recorder()
    store = rec.store
    runs = store.list_runs()          # newest first
    if len(runs) < 2:
        print("expected two recorded runs, found", len(runs))
        return 1
    failing, healthy = runs[0], runs[1]
    fail_events = store.get_events(failing["run_id"])
    healthy_events = store.get_events(healthy["run_id"])

    print("\n-- single-run rules on the failing trace alone --")
    solo = investigate_run(fail_events, run_meta=failing)
    _print(solo, "verdict")
    if solo.clean:
        print("  (clean is the honest single-run verdict: the fresh figure "
              "never entered this trace)\n")

    print("-- the same trace, investigated against the healthy baseline --")
    with_base = investigate_run(fail_events, run_meta=failing,
                                baseline_events=healthy_events,
                                baseline_run_id=healthy["run_id"])
    _print(with_base, "verdict")

    if with_base.findings:
        print("That last finding is the product: the baseline diff catches "
              "what single-run heuristics cannot.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
