"""Seeded example runs for the dashboard demo.

Ten canonical runs, each demonstrating one thing the recorder catches (or,
for the healthy pair, one thing it correctly stays quiet about):

    demo-1-healthy          clean run, all spans closed, real-looking tokens
    demo-2-crash            agent crashes mid-tool-call (no run.end)
    demo-3-tool-no-result   a tool call that ended with no result recorded
    demo-4-loop             the same LLM call 20 times, tokens burning
    demo-5-baseline-good    healthy baseline for the divergence pair
    demo-6-baseline-bad     same start, diverges at decision #3
    demo-7-token-gap        one model call reports no token usage
    demo-8-conflicting      two records disagree for the same entity
    demo-9-stale-value      the agent consumes the older of two values
    demo-10-empty-search    all searches empty, agent answers "not available"

Honesty rules for these seeds (they are also why they carry the
"demo": "seeded-example" tag everywhere):

- Every number in them (tokens, timestamps, durations) is SYNTHETIC and
  says so in the run tags. They are illustrative examples, not real
  production captures.
- The failure modes are planted, but they fire the SAME deterministic rules
  a real failure would - nothing is hand-annotated.
- Durations are realistic-looking (hundreds of ms per LLM call) but
  synthetic, so the timeline reads like a real trace.
- All ids are fixed, so seeding twice never duplicates anything: the store
  deduplicates by event_id / upserts by run_id.

Used by POST /demo/seed (api/main.py) so the dashboard's "Load examples"
button works against any deployment of this API.
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, Iterator, List, Tuple

from .models import Event, RunMeta

_BASE = 1_792_150_000.0  # fixed epoch (mid-Oct 2026): sorts deterministically, near recent runs


def _ev(run_id, kind, off_ms, **fields) -> Event:
    """Event at t_offset_ms=off_ms; the timestamp mirrors it on the fixed epoch."""
    return Event(
        run_id=run_id, kind=kind, timestamp=_BASE + off_ms / 1000.0,
        t_offset_ms=round(off_ms, 2),
        event_id=fields.pop("event_id", None), **fields,
    )


def _llm_span(run_id, start_ms, model, messages, in_tok, out_tok, text, counter, duration_ms=1050):
    """span.start + llm.response + span.end, closed, with realistic ms timing."""
    sid = f"span_{run_id}_{next(counter)}"
    e1, e2, e3 = (f"evt_{run_id}_{next(counter)}" for _ in range(3))
    return [
        _ev(run_id, "span.start", start_ms, span_id=sid, data={
            "label": f"llm:{model}", "model": model, "messages": messages},
            event_id=e1),
        _ev(run_id, "llm.response", start_ms + duration_ms * 0.85, span_id=sid,
            data={"payload": {"text": text}},
            input_tokens=in_tok, output_tokens=out_tok, cached_tokens=0, model=model,
            event_id=e2),
        _ev(run_id, "span.end", start_ms + duration_ms, span_id=sid, data={}, event_id=e3),
    ]


def _llm_span_no_usage(run_id, start_ms, model, messages, text, counter, duration_ms=1050):
    """Same shape but the usage never got captured - the token_capture_gap demo."""
    sid = f"span_{run_id}_{next(counter)}"
    e1, e2, e3 = (f"evt_{run_id}_{next(counter)}" for _ in range(3))
    return [
        _ev(run_id, "span.start", start_ms, span_id=sid, data={
            "label": f"llm:{model}", "model": model, "messages": messages},
            event_id=e1),
        _ev(run_id, "llm.response", start_ms + duration_ms * 0.85, span_id=sid,
            data={"payload": {"text": text}}, event_id=e2),
        _ev(run_id, "span.end", start_ms + duration_ms, span_id=sid, data={}, event_id=e3),
    ]


def _tool_span(run_id, start_ms, tool, args, result, counter, duration_ms=280):
    """span.start + tool.result + span.end, properly closed."""
    sid = f"span_{run_id}_{next(counter)}"
    e1, e2, e3 = (f"evt_{run_id}_{next(counter)}" for _ in range(3))
    return [
        _ev(run_id, "span.start", start_ms, span_id=sid,
            data={"label": f"tool:{tool}", "tool": tool, "arguments": args},
            event_id=e1),
        _ev(run_id, "tool.result", start_ms + duration_ms * 0.7, span_id=sid,
            data={"payload": result}, event_id=e2),
        _ev(run_id, "span.end", start_ms + duration_ms, span_id=sid, data={}, event_id=e3),
    ]


SEED_TAG = {"demo": "seeded-example", "note": "synthetic example run, illustrative numbers, not a real capture"}


def build_demo_runs() -> List[Tuple[RunMeta, List[Event]]]:
    runs: List[Tuple[RunMeta, List[Event]]] = []
    c: Iterator[int] = itertools.count(1)  # one global counter: ids never collide across runs

    # -- 1. healthy -----------------------------------------------------------
    rid = "demo-1-healthy"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Check refund status for order ORD-9182"}],
                     640, 22, "I'll look up the payment and order records.", c, 920)
    evs += _tool_span(rid, 990, "db.lookup", {"table": "orders", "id": "ORD-9182"},
                      {"status": "refund_initiated", "amount_inr": 4500}, c, 250)
    evs += _tool_span(rid, 1310, "db.lookup", {"table": "payments", "id": "pay_881"},
                      {"state": "REFUND_PENDING", "bank_ref": "UTR-556"}, c, 280)
    evs += _llm_span(rid, 1660, "gemini-2.0-flash",
                     [{"role": "user", "content": "Summarize the refund status for the customer"}],
                     910, 34, "Refund of INR 4,500 was initiated on Oct 12 and is pending bank settlement (UTR-556).", c, 1050)
    evs += [_ev(rid, "run.end", 2760, data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: healthy support agent run",
                         started_at=_BASE, ended_at=_BASE + 2.76, status="completed",
                         tags={**SEED_TAG, "shape": "healthy"}), evs))

    # -- 2. crash --------------------------------------------------------------
    rid = "demo-2-crash"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Process the chargeback evidence pack"}],
                     580, 19, "Collecting the transaction and dispute records now.", c, 870)
    # tool starts, then the agent records a crash inside it - no span.end, no run.end
    evs += [
        _ev(rid, "span.start", 940, span_id=f"span_{rid}_crash",
            data={"label": "tool:pdf.render", "tool": "pdf.render", "arguments": {"case": "CB-204"}},
            event_id=f"evt_{rid}_s0"),
        _ev(rid, "error", 1420, span_id=f"span_{rid}_crash", data={
            "exception": "PanicException", "message": "worker thread killed while rendering 2.1 GB attachment"},
            event_id=f"evt_{rid}_err0"),
    ]
    runs.append((RunMeta(run_id=rid, task="demo: agent crashes mid-run (worker panic)",
                         started_at=_BASE, status="crashed",
                         tags={**SEED_TAG, "shape": "crash"}), evs))

    # -- 3. tool with no result ------------------------------------------------
    rid = "demo-3-tool-no-result"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Verify the customer's email before sending the settlement link"}],
                     445, 17, "I'll verify the email address first.", c, 830)
    # span ends but no tool.result was ever recorded
    evs += [
        _ev(rid, "span.start", 910, span_id=f"span_{rid}_gap",
            data={"label": "tool:email.verify", "tool": "email.verify", "arguments": {"email": "r@example.com"}},
            event_id=f"evt_{rid}_s1"),
        _ev(rid, "span.end", 1180, span_id=f"span_{rid}_gap", data={}, event_id=f"evt_{rid}_e1"),
    ]
    evs += _llm_span(rid, 1250, "gemini-2.0-flash",
                     [{"role": "user", "content": "Send the settlement link"}],
                     520, 28, "Email verified, settlement link sent to the customer.", c, 910)
    evs += [_ev(rid, "run.end", 2200, data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: tool call with no recorded result (agent acted anyway)",
                         started_at=_BASE, ended_at=_BASE + 2.2, status="completed",
                         tags={**SEED_TAG, "shape": "tool-without-result"}), evs))

    # -- 4. token-burning loop --------------------------------------------------
    rid = "demo-4-loop"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    for i in range(20):
        evs += _llm_span(rid, 60 + i * 660, "gemini-2.0-flash",
                        [{"role": "user", "content": "Find the failing transaction in batch BATCH-77"}],
                        470, 12, "Let me search the batch again.", c, 620)
    evs += [_ev(rid, "run.end", 13220, data={"ok": False}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: stuck loop, same call 20 times, 9,400 tokens burned",
                         started_at=_BASE, ended_at=_BASE + 13.22, status="completed",
                         tags={**SEED_TAG, "shape": "loop"}), evs))

    # -- 5/6. baseline divergence pair ------------------------------------------
    plan_msgs = [{"role": "user", "content": "Draft the recovery plan for case RC-551"}]
    rid_good = "demo-5-baseline-good"
    evs = [_ev(rid_good, "run.start", 0, data={}, event_id=f"evt_{rid_good}_runstart")]
    evs += _llm_span(rid_good, 60, "gemini-2.0-flash", plan_msgs, 610, 21,
                     "I'll pull the current case file and the live payment status.", c, 980)
    evs += _tool_span(rid_good, 1070, "db.lookup", {"table": "cases", "id": "RC-551"},
                      {"as_of": "2025-10-10", "stage": "recovery_sent"}, c, 280)
    evs += _tool_span(rid_good, 1400, "payments.status", {"payment": "pay_77331"},
                      {"state": "PAID", "settled_at": "2025-10-09"}, c, 260)
    evs += _llm_span(rid_good, 1710, "gemini-2.0-flash",
                     [{"role": "user", "content": "Summarize and close the case"}],
                     700, 26, "Payment settled 2025-10-09; close RC-551 as recovered.", c, 1170)
    evs += [_ev(rid_good, "run.end", 2910, data={"ok": True}, event_id=f"evt_{rid_good}_runend")]
    runs.append((RunMeta(run_id=rid_good, task="demo: baseline run (healthy recovery flow)",
                         started_at=_BASE, ended_at=_BASE + 2.91, status="completed",
                         tags={**SEED_TAG, "shape": "baseline-good"}), evs))

    rid_bad = "demo-6-baseline-bad"
    evs = [_ev(rid_bad, "run.start", 0, data={}, event_id=f"evt_{rid_bad}_runstart")]
    evs += _llm_span(rid_bad, 60, "gemini-2.0-flash", plan_msgs, 610, 21,
                     "I'll pull the current case file and the live payment status.", c, 980)
    evs += _tool_span(rid_bad, 1070, "db.lookup", {"table": "cases", "id": "RC-551"},
                      {"as_of": "2025-10-10", "stage": "recovery_sent"}, c, 280)
    # divergence: the archive instead of the live status
    evs += _tool_span(rid_bad, 1400, "payments.archive", {"payment": "pay_77331"},
                      {"state": "UNPAID", "as_of": "2025-09-01"}, c, 300)
    evs += _llm_span(rid_bad, 1740, "gemini-2.0-flash",
                     [{"role": "user", "content": "Summarize and close the case"}],
                     680, 24, "Payment record shows UNPAID as of 2025-09-01; escalate RC-551 for manual recovery.", c, 1240)
    evs += [_ev(rid_bad, "run.end", 3020, data={"ok": True}, event_id=f"evt_{rid_bad}_runend")]
    runs.append((RunMeta(run_id=rid_bad, task="demo: diverging run (read stale archive instead of live status)",
                         started_at=_BASE, ended_at=_BASE + 3.02, status="completed",
                         tags={**SEED_TAG, "shape": "baseline-bad"}), evs))

    # -- 7. token capture gap ----------------------------------------------------
    rid = "demo-7-token-gap"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Draft the chargeback summary for case CB-77"}],
                     655, 23, "Let me pull the transaction history first.", c, 910)
    evs += _tool_span(rid, 1000, "db.lookup", {"table": "transactions", "id": "TX-87120"},
                      {"status": "charged_back", "amount": 12900}, c, 260)
    # second LLM call came back through a wrapper that dropped the usage field
    evs += _llm_span_no_usage(rid, 1310, "gemini-2.0-flash",
                     [{"role": "user", "content": "Write the two-paragraph summary for the bank"}],
                     "Chargeback summary drafted for the bank.", c, 1080)
    evs += [_ev(rid, "run.end", 2440, data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: one model call reports no token usage (capture gap)",
                         started_at=_BASE, ended_at=_BASE + 2.44, status="completed",
                         tags={**SEED_TAG, "shape": "token-gap"}), evs))

    # -- 8. conflicting records --------------------------------------------------
    rid = "demo-8-conflicting"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Confirm whether payment pay_881 has settled"}],
                     590, 20, "I'll check the archive and the live payment record.", c, 900)
    evs += _tool_span(rid, 990, "payments.archive", {"payment": "pay_881"},
                      {"id": "pay_881", "as_of": "2025-09-01", "state": "UNPAID"}, c, 280)
    evs += _tool_span(rid, 1320, "payments.status", {"payment": "pay_881"},
                      {"id": "pay_881", "as_of": "2025-10-10", "state": "PAID"}, c, 260)
    evs += _llm_span(rid, 1620, "gemini-2.0-flash",
                     [{"role": "user", "content": "Give the final settlement verdict"}],
                     705, 27, "The archive shows UNPAID as of 2025-09-01 but the live record shows PAID as of 2025-10-10. The live record wins: pay_881 has settled, no action needed.", c, 1190)
    evs += [_ev(rid, "run.end", 2860, data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: two records disagree for the same entity (agent resolves it correctly)",
                         started_at=_BASE, ended_at=_BASE + 2.86, status="completed",
                         tags={**SEED_TAG, "shape": "conflicting-records"}), evs))

    # -- 9. stale value consumed -------------------------------------------------
    rid = "demo-9-stale-value"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Compute the TDS due on the 32,000 settlement"}],
                     615, 21, "I'll look up the applicable surcharge rate and calculate.", c, 890)
    evs += _tool_span(rid, 980, "taxrate.lookup", {"fiscal_year": "2024-25", "jurisdiction": "IN"},
                      {"entity": "surcharge-rate", "metric": "rate", "value": 0.10, "as_of": "2025-04-01"}, c, 270)
    evs += _tool_span(rid, 1300, "taxrate.lookup", {"fiscal_year": "2025-26", "jurisdiction": "IN"},
                      {"entity": "surcharge-rate", "metric": "rate", "value": 0.15, "as_of": "2025-10-01"}, c, 260)
    evs += _tool_span(rid, 1610, "calc.evaluate", {"expression": "32000 * 0.1"},
                      3200, c, 190)
    evs += _llm_span(rid, 1860, "gemini-2.0-flash",
                     [{"role": "user", "content": "State the final TDS amount"}],
                     540, 15, "TDS payable on the settlement is INR 3,200.", c, 930)
    evs += [_ev(rid, "run.end", 2840, data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: agent consumes the stale rate while the fresh one is on record",
                         started_at=_BASE, ended_at=_BASE + 2.84, status="completed",
                         tags={**SEED_TAG, "shape": "stale-value"}), evs))

    # -- 10. empty search conclusion ----------------------------------------------
    rid = "demo-10-empty-search"
    evs = [_ev(rid, "run.start", 0, data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, 60, "gemini-2.0-flash",
                     [{"role": "user", "content": "Check whether customer CUST-2213 is due a refund"}],
                     660, 24, "I'll search the transaction history for this customer.", c, 940)
    evs += _tool_span(rid, 1030, "search.transactions", {"customer": "CUST-2213", "window": "30d"},
                      [], c, 420)
    evs += _tool_span(rid, 1500, "search.transactions", {"customer": "CUST-2213", "window": "90d"},
                      [], c, 460)
    evs += _llm_span(rid, 2010, "gemini-2.0-flash",
                     [{"role": "user", "content": "Give the customer a final answer"}],
                     575, 26, "No matching transactions were found for this customer, so no refund is due.", c, 1060)
    evs += [_ev(rid, "run.end", 3110, data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: empty searches, agent answers 'not available' anyway",
                         started_at=_BASE, ended_at=_BASE + 3.11, status="completed",
                         tags={**SEED_TAG, "shape": "empty-search"}), evs))

    return runs
