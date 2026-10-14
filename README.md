# Agent Flight Recorder — AI Agent Observability & Debugging

A Python SDK and dashboard for recording, replaying and debugging multi-step
AI agent executions: every LLM call, tool invocation, result, error and token
count, captured as an ordered, parent-child trace you can inspect, cost and diff.

**Why it exists:** an agent that returns a polished answer containing a wrong
number is unfalsifiable from the outside. You need the complete execution
history — the model decisions, the tool results (including the irrelevant or
stale ones), and the moment a good run and a bad run diverged — to say *where*
it went wrong, not just *that* it did.

## Status — all 8 phases shipped (v1)

| Phase | Scope | State |
|---|---|---|
| 1 | Recorder SDK: run/span/event model, SQLite store, redaction | ✅ shipped |
| 2 | Ingestion API (FastAPI): validation, idempotency, redaction | ✅ shipped, 14/14 tests |
| 3 | Dashboard (React + TS): run list, span timeline, event inspector | ✅ shipped, 9 logic tests + tsc strict + prod build |
| 4 | Cost & latency profiler (pricing config, cached tokens) | ✅ shipped, 20/20 tests + /runs/{id}/profile |
| 5 | Replay & diff: deterministic replay + earliest-divergence diff | ✅ shipped, 28/28 tests |
| 6 | Failure investigator: evidence-linked diagnosis, hypothesis labelled | ✅ shipped, 12 tests, GET /runs/{id}/investigate |
| 7 | Benchmark: 103 scripted failures, measured precision/recall | ✅ shipped, 12 tests, `python -m bench.run_benchmark` |
| 8 | Deploy: render.yaml + vercel.json + DEPLOY.md + seed script | ✅ configs & docs shipped (see DEPLOY.md); live deploy pending accounts |

Full suite: 52/52 pytest, 9/9 vitest.

## Failure investigator (the headline)

`GET /runs/{id}/investigate` runs eight deterministic rules over a trace.
Every finding separates what the trace *observed* from the investigator's
*hypothesis*, and carries verbatim evidence excerpts:

```
critical  baseline_divergence — earliest divergence from baseline at
          decision #5: tool_call_changed — agent chose a different tool
          or arguments
          evidence: {"label": "tool:calculator", "arguments": {"expression": "2100 + 0"}}
warning   conflicting_records — tool results disagree for entity
          'Vikram Solar': year = 2022 (…), 2025 (…)
          Hypothesis: one of these records is stale and the agent may
          have consumed the older value without re-verifying
```

The honest limitation, tested and documented: the bad run *alone* looks
clean to single-run rules, because the fresh figure never entered its
trace. What exposes it is the baseline diff. Single-run heuristics are
not enough — that is why replay and diff exist.

## Benchmark — measured, not claimed

`python -m bench.run_benchmark` runs 103 seeded scenarios (40 clean,
63 scripted failures across 7 types) through the investigator:

| Metric | Measured (seed 42) |
|---|---|
| Failures fully detected | 63/63 |
| Clean runs reported clean | 40/40 (0 false-positive findings) |
| Per-rule precision | 1.00 (all 8 rules) |
| Latency per investigation | 0.019 ms mean, 0.066 ms max |

Stable across seeds 7 and 99. Full JSON report: `bench/results/`
(regenerate with the command above). Thresholds pinned in
`tests/test_benchmark.py`, so a regression fails CI, not the demo.

## The SDK (works today)

```python
from afrec import get_recorder

rec = get_recorder()  # AFREC_DB env var sets the SQLite path

with rec.run("Compare three solar companies") as run:
    with run.llm_call("gpt-4o-mini", messages) as llm:
        response = model(messages)
        llm.result(response.text, input_tokens=response.usage.input,
                   output_tokens=response.usage.output)

    with run.tool_call("search", {"query": "solar companies india"}) as tool:
        results = do_search(...)
        tool.result(results)   # stale/irrelevant results are captured too — that's the point
```

Design rules the code enforces:

- **Instrumentation must never break the agent.** Persistence failures are
  counted and logged, never raised into the caller's flow.
- **Crash-safe partial runs.** An agent exception still writes run.end with
  status `crashed` and an error event, so post-mortems have the flight up to
  impact.
- **Honest costs.** `PricingConfig.estimate_cost()` returns `None` when usage
  is unknown, never a fabricated 0.0.
- **Redaction is in the persistence path**, not an afterthought: known secret
  shapes and keys (`api_key`, `password`, `authorization`, ...) are masked
  before any event touches disk.

## Demo agent (deterministic, zero-cost)

`demo_agent/agent.py` is a scripted three-step research agent — mock LLM plus
`search` and `calculator` tools — with a deliberate failure mode: one search
result carries a stale 2022 revenue figure, and `use_stale_figure=True` makes
the agent trust it without the corrective follow-up query. The trace records
both the stale payload and the decision that trusted it, which is exactly the
evidence the failure investigator (Phase 6) will point at.

Run it:

```bash
python -m demo_agent.agent
```

## Tests

```bash
python3 -m pytest tests/ -q        # SDK + API (14 tests)
cd dashboard && npx vitest run  # trace-reconstruction logic (7 tests)
```

Phase 1 acceptance: complete ordered traces, parent-child reconstruction,
crash-time partial runs, secret redaction, honest cost accounting, and the
stale-figure failure leaving findable evidence. 6/6 passing.

## Stack

Python 3.11 SDK + SQLite (Postgres later) · FastAPI ingestion API (`api/main.py`, `uvicorn api.main:app`) · React + TypeScript dashboard (`dashboard/`, `npm run dev`) · pytest · Render + Vercel for deploy
