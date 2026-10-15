# Agent Flight Recorder — AI Agent Observability & Debugging

Agent Flight Recorder records everything an AI agent does while it runs: every
model call, tool use, result and error, with token counts and cost. Developers
can replay a recorded run, diff it against another, and see the exact step
where it went wrong.

Under the hood it is a Python SDK and dashboard for recording, replaying and
debugging multi-step AI agent executions: every LLM call, tool invocation, result, error and token
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
| 2 | Ingestion API (FastAPI): validation, idempotency, redaction | ✅ shipped |
| 3 | Dashboard (React + TS): run list, span timeline, event inspector | ✅ shipped |
| 4 | Cost & latency profiler (pricing config, cached tokens) | ✅ shipped, GET /runs/{id}/profile |
| 5 | Replay & diff: deterministic replay + earliest-divergence diff | ✅ shipped |
| 6 | Failure investigator: evidence-linked diagnosis, hypothesis labelled | ✅ shipped, GET /runs/{id}/investigate |
| 7 | Synthetic benchmark: 103 scripted failures, precision/recall | ✅ shipped, `python -m bench.run_benchmark` |
| 8 | Deploy: single Vercel project (dashboard at root + ingestion API, see vercel.json) | ✅ live; optional standalone API on Render (see DEPLOY.md) |

Full suite, run just now on this repo: 57/57 pytest, 10/10 vitest.

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

## Benchmark

Two different things live here, and conflating them is how evals lie.

### Synthetic regression suite (`bench/run_benchmark.py`)

`python -m bench.run_benchmark` runs 103 seeded scenarios (40 clean,
63 scripted failures across 7 types) through the investigator:

| Metric | Measured (seed 42) |
|---|---|
| Failures fully detected | 63/63 |
| Clean runs reported clean | 40/40 (0 false-positive findings) |
| Per-rule precision | 1.00 (all 8 rules) |
| Rule latency | microbenchmark noise (~0.01 ms in-process); not a claim about usefulness |

Read this honestly: the failures are scripted by the same author who wrote
the rules, so a perfect score only verifies that the rules fire as
designed. It is a regression suite. It says nothing about real-world
accuracy, and this README will not pretend otherwise.

Stable across seeds 7 and 99. Full JSON report: `bench/results/`
(regenerate with the command above). Thresholds pinned in
`tests/test_benchmark.py`, so a regression fails CI, not the demo.

### Real-agent evaluation (`bench/real/`)

The circularity above is exactly why `bench/real/` exists. A tool-using
agent (search + calculator) runs 30 varied tasks against a REAL LLM
(Gemini, key from the environment, never hardcoded), every trace is
saved, a HUMAN labels each one success/failure via `python -m
bench.real.label` (nothing auto-labels), and `python -m
bench.real.evaluate` scores the UNCHANGED investigator against those
labels — precision, recall, every miss and false positive, by trace ID.
Rules are not tuned to the labelled set.

Current state, verbatim as of 2026-10-15: no traces have been collected
(collection needs a valid `GEMINI_API_KEY`; the keys in the build
environment are placeholders that the API rejects) and no labels exist.
The real output of `python -m bench.real.evaluate` right now:

```
no labels.json — run  python -m bench.real.label  first.
(labels are human-only in this pipeline; nothing auto-labels.)
```

Traces: 0 collected. Model: `gemini-2.0-flash` (planned, configurable
via `AFREC_REAL_MODEL`). Known weaknesses: none measured yet — this list
will be filled from actual misses with trace IDs once labelled traces
exist, and if recall is low, the number stays low here. The README will
quote the real table, not a flattering one.

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
python3 -m pytest tests/ -q        # full backend suite (57 tests at last run)
cd dashboard && npx vitest run  # trace-reconstruction logic (10 tests at last run)
```

Phase 1 acceptance: complete ordered traces, parent-child reconstruction,
crash-time partial runs, secret redaction, honest cost accounting, and the
stale-figure failure leaving findable evidence. All enforced by the suite above.

## Stack

Python 3.11 SDK + SQLite (Postgres later) · FastAPI ingestion API (`api/main.py`, `uvicorn api.main:app`) · React + TypeScript dashboard (`dashboard/`, `npm run dev`) · pytest · Render + Vercel for deploy
## Streaming real agents to the live API (Phase 8.1)

`afrec.HttpStore` streams runs to an ingestion API as they happen — the
drop-in way to record a real provider call (Gemini, OpenAI, ...) and watch
it on the dashboard:

```python
from afrec import Recorder, HttpStore

rec = Recorder(store=HttpStore("https://afrec-api.onrender.com"))
# or just: export AFREC_API_URL=https://afrec-api.onrender.com
#           then get_recorder() picks it up automatically

with rec.run("my task") as run:
    with run.llm_call("gemini-2.0-flash", messages=[...]) as span:
        resp = client.models.generate_content(model="gemini-2.0-flash", contents="hi")
        span.result(resp.text,
                    input_tokens=resp.usage_metadata.prompt_token_count,
                    output_tokens=resp.usage_metadata.candidates_token_count)

rec.store.flush(timeout=30)
```

Design guarantees (tested in `tests/test_http_store.py`):

1. **Instrumentation never breaks the agent.** Posts run on a background
   thread; a dead API is counted and warned, never raised into your loop.
2. **An outage never loses a trace.** Every event also writes to a local
   SQLite mirror (`$AFREC_DB`), so the recording survives network failure.
3. **Secrets stay in-process.** Data is redacted before it goes on the wire.
4. **Retries are idempotent.** Client-side event IDs de-duplicate retries.

Full runnable example: `python -m examples.gemini_agent` (needs
`google-genai` and a `GEMINI_API_KEY`).

