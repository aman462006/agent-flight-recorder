"""Seeded example runs for the dashboard demo.

Six canonical runs, each demonstrating one thing the recorder catches (or,
for the healthy pair, one thing it correctly stays quiet about):

    demo-1-healthy          clean run, all spans closed, real-looking tokens
    demo-2-crash            agent crashes mid-tool-call (no run.end)
    demo-3-tool-no-result   a tool call that ended with no result recorded
    demo-4-loop             the same LLM call 20 times, tokens burning
    demo-5-baseline-good    healthy baseline for the divergence pair
    demo-6-baseline-bad     same start, diverges at decision #3

Honesty rules for these seeds (they are also why they carry the
"demo": "seeded-example" tag everywhere):

- Every number in them (tokens, timestamps) is SYNTHETIC and says so in the
  run tags. They are illustrative examples, not real production captures.
- The failure modes are planted, but they fire the SAME deterministic rules
  a real failure would - nothing is hand-annotated.
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


def _t(offset: float) -> float:
    return _BASE + offset


def _ev(run_id, kind, ts, **fields) -> Event:
    return Event(
        run_id=run_id, kind=kind, timestamp=ts, t_offset_ms=0.0,
        event_id=fields.pop("event_id", None), **fields,
    )


def _llm_span(run_id, ts, model, messages, in_tok, out_tok, text, counter):
    """span.start + llm.response + span.end, properly closed with tokens."""
    sid = f"span_{run_id}_{next(counter)}"
    e1, e2, e3 = (f"evt_{run_id}_{next(counter)}" for _ in range(3))
    return [
        _ev(run_id, "span.start", ts, span_id=sid, data={
            "label": f"llm:{model}", "model": model, "messages": messages},
            event_id=e1),
        _ev(run_id, "llm.response", ts + 0.4, span_id=sid, data={"payload": {"text": text}},
            input_tokens=in_tok, output_tokens=out_tok, cached_tokens=0, model=model,
            event_id=e2),
        _ev(run_id, "span.end", ts + 0.5, span_id=sid, data={}, event_id=e3),
    ]


def _tool_span(run_id, ts, tool, args, result, counter):
    """span.start + tool.result + span.end, properly closed."""
    sid = f"span_{run_id}_{next(counter)}"
    e1, e2, e3 = (f"evt_{run_id}_{next(counter)}" for _ in range(3))
    return [
        _ev(run_id, "span.start", ts, span_id=sid,
            data={"label": f"tool:{tool}", "tool": tool, "arguments": args},
            event_id=e1),
        _ev(run_id, "tool.result", ts + 0.3, span_id=sid, data={"payload": result},
            event_id=e2),
        _ev(run_id, "span.end", ts + 0.4, span_id=sid, data={}, event_id=e3),
    ]


SEED_TAG = {"demo": "seeded-example", "note": "synthetic example run, illustrative numbers, not a real capture"}


def build_demo_runs() -> List[Tuple[RunMeta, List[Event]]]:
    runs: List[Tuple[RunMeta, List[Event]]] = []
    c: Iterator[int] = itertools.count(1)  # one global counter: ids never collide across runs

    # -- 1. healthy -----------------------------------------------------------
    rid = "demo-1-healthy"
    evs = [_ev(rid, "run.start", _t(0), data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, _t(1), "gemini-2.0-flash",
                     [{"role": "user", "content": "Check refund status for order ORD-9182"}],
                     640, 22, "I'll look up the payment and order records.", c)
    evs += _tool_span(rid, _t(2), "db.lookup", {"table": "orders", "id": "ORD-9182"},
                      {"status": "refund_initiated", "amount_inr": 4500}, c)
    evs += _tool_span(rid, _t(3), "db.lookup", {"table": "payments", "id": "pay_881"},
                      {"state": "REFUND_PENDING", "bank_ref": "UTR-556"}, c)
    evs += _llm_span(rid, _t(4), "gemini-2.0-flash",
                     [{"role": "user", "content": "Summarize the refund status for the customer"}],
                     910, 34, "Refund of INR 4,500 was initiated on Oct 12 and is pending bank settlement (UTR-556).", c)
    evs += [_ev(rid, "run.end", _t(5), data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: healthy support agent run",
                         started_at=_t(0), ended_at=_t(5), status="completed",
                         tags={**SEED_TAG, "shape": "healthy"}), evs))

    # -- 2. crash --------------------------------------------------------------
    rid = "demo-2-crash"
    evs = [_ev(rid, "run.start", _t(10), data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, _t(11), "gemini-2.0-flash",
                     [{"role": "user", "content": "Process the chargeback evidence pack"}],
                     580, 19, "Collecting the transaction and dispute records now.", c)
    # tool starts, then the agent records a crash inside it - no span.end, no run.end
    evs += [
        _ev(rid, "span.start", _t(12), span_id=f"span_{rid}_crash",
            data={"label": "tool:pdf.render", "tool": "pdf.render", "arguments": {"case": "CB-204"}},
            event_id=f"evt_{rid}_s0"),
        _ev(rid, "error", _t(13), span_id=f"span_{rid}_crash", data={
            "exception": "PanicException", "message": "worker thread killed while rendering 2.1 GB attachment"},
            event_id=f"evt_{rid}_err0"),
    ]
    runs.append((RunMeta(run_id=rid, task="demo: agent crashes mid-run (worker panic)",
                         started_at=_t(10), status="crashed",
                         tags={**SEED_TAG, "shape": "crash"}), evs))

    # -- 3. tool with no result ------------------------------------------------
    rid = "demo-3-tool-no-result"
    evs = [_ev(rid, "run.start", _t(20), data={}, event_id=f"evt_{rid}_runstart")]
    evs += _llm_span(rid, _t(21), "gemini-2.0-flash",
                     [{"role": "user", "content": "Verify the customer's email before sending the settlement link"}],
                     445, 17, "I'll verify the email address first.", c)
    # span ends but no tool.result was ever recorded
    evs += [
        _ev(rid, "span.start", _t(22), span_id=f"span_{rid}_gap",
            data={"label": "tool:email.verify", "tool": "email.verify", "arguments": {"email": "r@@example.com"}},
            event_id=f"evt_{rid}_s1"),
        _ev(rid, "span.end", _t(23), span_id=f"span_{rid}_gap", data={}, event_id=f"evt_{rid}_e1"),
    ]
    evs += _llm_span(rid, _t(24), "gemini-2.0-flash",
                     [{"role": "user", "content": "Send the settlement link"}],
                     520, 28, "Email verified, settlement link sent to the customer.", c)
    evs += [_ev(rid, "run.end", _t(25), data={"ok": True}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: tool call with no recorded result (agent acted anyway)",
                         started_at=_t(20), ended_at=_t(25), status="completed",
                         tags={**SEED_TAG, "shape": "tool-without-result"}), evs))

    # -- 4. token-burning loop --------------------------------------------------
    rid = "demo-4-loop"
    evs = [_ev(rid, "run.start", _t(40), data={}, event_id=f"evt_{rid}_runstart")]
    for i in range(20):
        evs += _llm_span(rid, _t(41 + i), "gemini-2.0-flash",
                        [{"role": "user", "content": "Find the failing transaction in batch BATCH-77"}],
                        470, 12, "Let me search the batch again.", c)
    evs += [_ev(rid, "run.end", _t(70), data={"ok": False}, event_id=f"evt_{rid}_runend")]
    runs.append((RunMeta(run_id=rid, task="demo: stuck loop, same call 20 times, 9,400 tokens burned",
                         started_at=_t(40), ended_at=_t(70), status="completed",
                         tags={**SEED_TAG, "shape": "loop"}), evs))

    # -- 5/6. baseline divergence pair ------------------------------------------
    plan_msgs = [{"role": "user", "content": "Draft the recovery plan for case RC-551"}]
    rid_good = "demo-5-baseline-good"
    evs = [_ev(rid_good, "run.start", _t(80), data={}, event_id=f"evt_{rid_good}_runstart")]
    evs += _llm_span(rid_good, _t(81), "gemini-2.0-flash", plan_msgs, 610, 21,
                     "I'll pull the current case file and the live payment status.", c)
    evs += _tool_span(rid_good, _t(82), "db.lookup", {"table": "cases", "id": "RC-551"},
                      {"as_of": "2025-10-10", "stage": "recovery_sent"}, c)
    evs += _tool_span(rid_good, _t(83), "payments.status", {"payment": "pay_77331"},
                      {"state": "PAID", "settled_at": "2025-10-09"}, c)
    evs += _llm_span(rid_good, _t(84), "gemini-2.0-flash",
                     [{"role": "user", "content": "Summarize and close the case"}],
                     700, 26, "Payment settled 2025-10-09; close RC-551 as recovered.", c)
    evs += [_ev(rid_good, "run.end", _t(85), data={"ok": True}, event_id=f"evt_{rid_good}_runend")]
    runs.append((RunMeta(run_id=rid_good, task="demo: baseline run (healthy recovery flow)",
                         started_at=_t(80), ended_at=_t(85), status="completed",
                         tags={**SEED_TAG, "shape": "baseline-good"}), evs))

    rid_bad = "demo-6-baseline-bad"
    evs = [_ev(rid_bad, "run.start", _t(90), data={}, event_id=f"evt_{rid_bad}_runstart")]
    evs += _llm_span(rid_bad, _t(91), "gemini-2.0-flash", plan_msgs, 610, 21,
                     "I'll pull the current case file and the live payment status.", c)
    evs += _tool_span(rid_bad, _t(92), "db.lookup", {"table": "cases", "id": "RC-551"},
                      {"as_of": "2025-10-10", "stage": "recovery_sent"}, c)
    # divergence: different tool with different args at decision #3
    evs += _tool_span(rid_bad, _t(93), "db.lookup", {"table": "payments_archive", "payment": "pay_77331"},
                      {"state": "UNPAID", "as_of": "2025-09-01"}, c)
    evs += _llm_span(rid_bad, _t(94), "gemini-2.0-flash",
                     [{"role": "user", "content": "Summarize and close the case"}],
                     640, 22, "Payment still unpaid per archive; escalate to collections.", c)
    evs += [_ev(rid_bad, "run.end", _t(95), data={"ok": True}, event_id=f"evt_{rid_bad}_runend")]
    runs.append((RunMeta(run_id=rid_bad, task="demo: diverging run (read stale archive instead of live status)",
                         started_at=_t(90), ended_at=_t(95), status="completed",
                         tags={**SEED_TAG, "shape": "baseline-bad", "baseline": rid_good}), evs))

    return runs


def events_as_store_dicts(events) -> List[Dict[str, Any]]:
    """Render seeded Events the way a store returns them (plain dicts),
    so the investigator and profiler read identical shapes from seeds
    and real captured runs."""
    out = []
    for e in events:
        out.append({
            "event_id": e.event_id, "kind": e.kind, "span_id": e.span_id,
            "parent_span_id": e.parent_span_id, "timestamp": e.timestamp,
            "t_offset_ms": e.t_offset_ms, "data": e.data,
            "input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
            "cached_tokens": e.cached_tokens, "model": e.model,
        })
    return out


DEMO_PAIRS: Dict[str, str] = {"demo-6-baseline-bad": "demo-5-baseline-good"}
