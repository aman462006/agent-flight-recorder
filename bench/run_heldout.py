"""HELD-OUT ADVERSARIAL EVALUATION for the investigator rules.

Why this exists: the synthetic suite (bench/run_benchmark.py) is circular
by construction — the same author scripted the failures and wrote the
rules. A perfect score there only proves the rules fire as designed.

This set was written AFTER the rules were frozen, specifically to break
them: every scenario is a near-miss, either a *legitimate behaviour that
looks like a failure shape* (adversarial for precision) or a *genuine
failure in a shape the rules were not designed for* (adversarial for
recall). The event builders share no helpers with bench/scenarios.py and
the scenario shapes were picked against the published rule internals,
not derived from them.

Scoring is honest by construction: this module prints the numbers it
measured, including every miss and every false positive, by scenario id.
The rules are NOT re-tuned against this set; misses go to the README's
known-gaps list, not back into rule logic.

Run:  python -m bench.run_heldout
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afrec.investigator import investigate_run  # noqa: E402

_seq = 0


def _sid(prefix="s"):
    global _seq
    _seq += 1
    return f"{prefix}{_seq}"


def tool(tool, args, result, t, label=None):
    """A tool span with its arguments fingerprinted (data.arguments, the
    exact field _decision_key reads) and a .result payload."""
    sid = _sid()
    return sid, [
        {"kind": "span.start", "span_id": sid, "t_offset_ms": t,
         "data": {"label": f"tool:{label or tool}", "arguments": args}},
        {"kind": "tool.result", "span_id": sid, "t_offset_ms": t + 1,
         "data": {"payload": result}},
        {"kind": "span.end", "span_id": sid, "t_offset_ms": t + 2,
         "data": {"label": f"tool:{label or tool}"}},
    ]


def llm(answer, t, model="groq/llama-3.3-70b", in_tok=420, out_tok=60):
    sid = _sid()
    return sid, [
        {"kind": "span.start", "span_id": sid, "t_offset_ms": t,
         "data": {"label": f"llm:{model}"}},
        {"kind": "llm.response", "span_id": sid, "t_offset_ms": t + 1,
         "data": {"payload": answer}, "input_tokens": in_tok, "output_tokens": out_tok,
         "model": model},
        {"kind": "span.end", "span_id": sid, "t_offset_ms": t + 2,
         "data": {"label": f"llm:{model}"}},
    ]


def run_end(t):
    return {"kind": "run.end", "t_offset_ms": t}


def flat(pairs):
    out = []
    for _, evs in pairs:
        out.extend(evs)
    return out


# ---------------------------------------------------------------------------
# Scenario builders. Each returns (task, events, human_verdict, why).
# human_verdict: "clean" (the agent behaved correctly) or "failure".
# "why" is the adversarial intent, printed with the results.
# ---------------------------------------------------------------------------

def s01_retry_ladder_then_success():
    """CLEAN near-miss: 3 identical failed calls (below the 5-in-a-row
    threshold), then the SAME call with a corrected argument succeeds.
    A retry ladder with eventual progress is legitimate agent behaviour."""
    evs = []
    t = 100
    for _ in range(3):
        evs.extend(tool("payments.refund", {"txn": "TX-9001"}, {"error": "rate_limited"}, t)[1])
        t += 10
    evs.extend(tool("payments.refund", {"txn": "TX-9001", "retry_token": "r2"}, {"status": "refunded"}, t)[1])
    evs.extend(llm("Refund for TX-9001 issued after a retry.", t + 10)[1])
    evs.append(run_end(t + 20))
    return ("issue refund for TX-9001", flat([]) + evs, "clean",
            "3 identical retries below the loop threshold, then progress: must stay silent")


def s02_legit_polling_different_args():
    """CLEAN near-miss: 6 status polls, each with a DIFFERENT cursor arg.
    Longest consecutive identical decision = 1. Legitimate pagination."""
    evs = []
    t = 50
    for page in range(6):
        evs.extend(tool("jobs.status", {"job": "etl-77", "cursor": page},
                         {"state": "running", "pct": 10 * page + 5}, t)[1])
        t += 8
    evs.extend(llm("Job etl-77 has finished processing.", t + 5)[1])
    evs.append(run_end(t + 15))
    return ("check on job etl-77", flat([]) + evs, "clean",
            "6 same-tool calls with distinct args (pagination): must stay silent")


def s03_batch_lookup_distinct_ids():
    """CLEAN near-miss: 20 identical-shape db lookups with distinct ids.
    A batch pass over many records, not a loop."""
    evs = []
    t = 20
    for i in range(20):
        evs.extend(tool("ledger.entry", {"entry_id": f"E-{i:04d}"},
                         {"entry_id": f"E-{i:04d}", "amount": 100 + i}, t)[1])
        t += 5
    evs.extend(llm("All 20 ledger entries reconciled, no mismatches.", t + 5)[1])
    evs.append(run_end(t + 15))
    return ("reconcile batch E-0000..E-0019", flat([]) + evs, "clean",
            "20 same-tool calls with distinct ids (batch): must stay silent")


def s04_legit_empty_search_with_other_evidence():
    """CLEAN near-miss: a search returns empty, but a second, non-search
    source (a CRM lookup) returns the answer the user needed. The agent
    uses it and answers correctly. Empty search + real evidence elsewhere
    is not a failure."""
    evs = []
    evs.extend(tool("search", {"query": "refund customer C-552"}, [], 10)[1])
    evs.extend(tool("crm.customer", {"customer_id": "C-552"},
                    {"name": "Meera", "open_refund": "RF-102", "amount": 45.0}, 20)[1])
    evs.extend(llm("Customer Meera has one open refund, RF-102 for $45.", 30)[1])
    evs.append(run_end(40))
    return ("does customer C-552 have refunds open", flat([]) + evs, "clean",
            "empty search but the non-search CRM lookup had the answer: must stay silent")


def s05_old_and_new_agent_uses_new():
    """CLEAN near-miss: the trace holds a 2023 and a 2025 record for the
    same metric; the agent consumes the FRESH one. Old and new records
    coexisting is a state, not a failure — consuming the new one is
    correct behaviour."""
    evs = []
    evs.extend(tool("search", {"query": "usd_inr_rate"},
                    [{"metric": "usd_inr_rate", "value": 74.5, "as_of": "2023-12-31"},
                     {"metric": "usd_inr_rate", "value": 83.2, "as_of": "2025-10-01"}], 10)[1])
    evs.extend(llm("The current USD to INR rate is 83.2 (as of 2025-10-01).", 20)[1])
    evs.append(run_end(30))
    return ("what is the current usd_inr_rate", flat([]) + evs, "clean",
            "coexisting old/new records, agent consumes the fresh value: must stay silent")


def s06_deliberation_cites_both_records():
    """CLEAN near-miss: two records disagree; the agent explicitly cites
    BOTH, explains the discrepancy, and proceeds on the fresh value.
    Deliberation, not staleness."""
    evs = []
    evs.extend(tool("search", {"query": "Vikram Solar revenue"},
                    [{"entity": "Vikram Solar", "year": 2022, "revenue": "₹410 Cr"},
                     {"entity": "Vikram Solar", "year": 2025, "revenue": "₹1,120 Cr"}], 10)[1])
    evs.extend(llm("Sources disagree: a 2022 figure of ₹410 Cr and a 2025 figure of "
                   "₹1,120 Cr. The 2025 filing is the current one, so I will use ₹1,120 Cr.", 20)[1])
    evs.append(run_end(30))
    return ("what is Vikram Solar's revenue", flat([]) + evs, "clean",
            "agent cites both conflicting records and uses the fresh one: stale_consumption "
            "must stay silent (conflicting_records may note it as info by design)")


def s07_polling_same_args_waiting_for_completion():
    """CLEAN near-miss with teeth: the agent polls the SAME call, identical
    arguments, 6 times in a row — legitimately, because it is waiting for
    a batch job to finish, and each result does change (running -> done).
    The repeated_decision rule counts span starts, not result changes, so
    this is a designed false-positive risk. If it fires, that is a real
    precision miss on this set and gets reported as such."""
    evs = []
    t = 10
    for i in range(6):
        evs.extend(tool("jobs.status", {"job": "batch-31"},
                         {"state": "running" if i < 5 else "done", "pct": i * 20}, t)[1])
        t += 8
    evs.extend(llm("Batch job batch-31 has completed successfully.", t + 5)[1])
    evs.append(run_end(t + 15))
    return ("wait for batch-31 to finish", flat([]) + evs, "clean",
            "legitimate same-args polling where the RESULTS change (waiting for a job): "
            "adversarial for repeated_decision — a fire here is a false positive")


def f08_loop_interleaved_with_thinking():
    """FAILURE near-miss: the agent is genuinely stuck — the same tool call,
    identical arguments, 5 times — but it 'thinks' between every call, so
    no two identical decisions are consecutive. The consecutive-run rule
    cannot see interleaved loops. If missed, it goes to the known-gaps
    list, not back into the rule."""
    evs = []
    t = 10
    for i in range(5):
        evs.extend(tool("search", {"query": "PAY-404"}, [], t)[1])
        t += 6
        evs.extend(llm("Let me check the ledger again for PAY-404.", t, in_tok=500, out_tok=45)[1])
        t += 6
    evs.append(run_end(t + 5))
    return ("find payment PAY-404", flat([]) + evs, "failure",
            "a real stuck loop, interleaved with LLM thinking so identical calls are never "
            "consecutive: adversarial for repeated_decision recall")


def f09_stale_consumed_via_calculator():
    """FAILURE near-miss: classic staleness, but consumed through the
    calculator rather than the final answer — the answer never names the
    stale number. Rules must catch value consumption in tool arguments."""
    evs = []
    evs.extend(tool("search", {"query": "exchange rate"},
                    [{"metric": "usd_inr_rate", "value": 74.5, "as_of": "2023-12-31"},
                     {"metric": "usd_inr_rate", "value": 83.2, "as_of": "2025-10-01"}], 10)[1])
    evs.extend(tool("calculator", {"expression": "74.5 * 1200"}, {"result": 89400.0}, 20)[1])
    evs.extend(llm("Your 1200 USD amounts to 89,400 INR.", 30)[1])
    evs.append(run_end(40))
    return ("convert 1200 USD to INR", flat([]) + evs, "failure",
            "the stale 2023 rate is consumed through the calculator, never named in the answer")


def f10_wrong_record_consumed():
    """FAILURE near-miss: the search returns two customers' records; the
    agent answers customer A's question with customer B's balance. No
    rule was ever written for wrong-record consumption. Expected miss —
    documented honestly if it misses."""
    evs = []
    evs.extend(tool("search", {"query": "account balances for Rao household"},
                    [{"customer": "A. Rao", "balance": 12500},
                     {"customer": "B. Rao", "balance": 480}], 10)[1])
    evs.extend(llm("A. Rao, your account balance is ₹480.", 20)[1])
    evs.append(run_end(30))
    return ("what is A. Rao's account balance", flat([]) + evs, "failure",
            "the answer uses B. Rao's balance for A. Rao: a failure shape with no rule")


def f11_evidence_ignored_unavailability_claim():
    """FAILURE near-miss: the agent's search comes back empty, but a
    non-search lookup EARLIER in the same trace already returned the
    record. The agent answers 'not available' anyway. The
    empty_search_conclusion rule requires ALL evidence to be empty, so a
    record that was found-and-ignored defeats it. Expected miss —
    documented honestly if it misses."""
    evs = []
    evs.extend(tool("db.lookup", {"table": "payments", "ref": "PAY-404"},
                    {"ref": "PAY-404", "status": "PAID", "amount": 1200}, 10)[1])
    evs.extend(tool("search", {"query": "PAY-404"}, [], 20)[1])
    evs.extend(llm("I couldn't locate a transaction with the reference PAY-404.", 30)[1])
    evs.append(run_end(40))
    return ("check payment PAY-404", flat([]) + evs, "failure",
            "the record WAS found by db.lookup, the search came back empty, and the agent "
            "claims unavailability: adversarial for empty_search_conclusion recall")


SCENARIOS = [s01_retry_ladder_then_success, s02_legit_polling_different_args,
             s03_batch_lookup_distinct_ids, s04_legit_empty_search_with_other_evidence,
             s05_old_and_new_agent_uses_new, s06_deliberation_cites_both_records,
             s07_polling_same_args_waiting_for_completion,
             f08_loop_interleaved_with_thinking, f09_stale_consumed_via_calculator,
             f10_wrong_record_consumed, f11_evidence_ignored_unavailability_claim]


def run_heldout(verbose=True):
    rows = []
    for build in SCENARIOS:
        task, events, verdict, why = build()
        inv = investigate_run(events, run_meta={"task": task, "status": "completed"})
        warn = [f for f in inv.findings if f.severity in ("critical", "warning")]
        info = [f for f in inv.findings if f.severity == "info"]
        flagged = len(warn) > 0
        if verdict == "clean":
            outcome = "TN" if not flagged else "FP"
        else:
            outcome = "TP" if flagged else "MISS"
        rows.append({"id": build.__name__, "verdict": verdict, "outcome": outcome,
                     "warn_rules": [f.rule for f in warn], "info_rules": [f.rule for f in info],
                     "why": why})

    clean = [r for r in rows if r["verdict"] == "clean"]
    failures = [r for r in rows if r["verdict"] == "failure"]
    fp = [r for r in clean if r["outcome"] == "FP"]
    tp = [r for r in failures if r["outcome"] == "TP"]
    misses = [r for r in failures if r["outcome"] == "MISS"]
    info_on_clean = [r for r in clean if r["info_rules"]]

    summary = {
        "clean_near_misses": len(clean),
        "clean_flagged_false_positive": len(fp),
        "subtle_failures": len(failures),
        "failures_caught": len(tp),
        "failures_missed": len(misses),
        "warning_level_precision": (round(len(tp) / (len(tp) + len(fp)), 2)
                                    if (tp or fp) else None),
        "warning_level_recall": (round(len(tp) / len(failures), 2) if failures else None),
        "info_notes_on_clean_runs": len(info_on_clean),
    }
    if verbose:
        print("=" * 64)
        print("HELD-OUT ADVERSARIAL SET — rules frozen before this set was written")
        print("=" * 64)
        for r in rows:
            mark = {"TN": "  ", "FP": "!!", "TP": "ok", "MISS": "??"}[r["outcome"]]
            print(f"{mark} {r['id']:<44} verdict={r['verdict']:<7} -> {r['outcome']}")
            if r["warn_rules"]:
                print(f"     fired: {', '.join(r['warn_rules'])}")
            if r["info_rules"]:
                print(f"     info : {', '.join(r['info_rules'])}")
        print()
        print(f"clean near-misses: {len(clean)}  |  false positives (warning+): {len(fp)}")
        print(f"subtle failures  : {len(failures)}  |  caught: {len(tp)}  missed: {len(misses)}")
        if tp or fp:
            print(f"warning-level precision on this set: {summary['warning_level_precision']:.2f}")
        if failures:
            print(f"warning-level recall    on this set: {summary['warning_level_recall']:.2f}")
        print(f"info-level notes on clean runs (documented behaviour, not flags): "
              f"{len(info_on_clean)}")
        for r in misses:
            print(f"\nMISSED — {r['id']}:")
            print(f"  {r['why']}")
    return {"summary": summary, "rows": rows}


if __name__ == "__main__":
    result = run_heldout()
    out_path = os.path.join(os.path.dirname(__file__), "results", "heldout.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)
    print(f"\nwritten: {out_path}")
