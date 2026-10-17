"""
Phase 7 — Benchmark scenario generator.

Scripted failures with KNOWN ground truth, so the investigator's
diagnosis quality is MEASURED, not claimed. Each scenario is a synthetic
event trace plus the set of rules that SHOULD fire on it (ground truth
accounts for legitimate secondary findings — a crashed run rightly
fires unclosed_span too; that is a true positive, not noise).

Determinism: scenarios are generated from a fixed seed. Same benchmark
run twice → identical scenarios → comparable metrics.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Scenario types and their ground-truth rule sets.
# Every list is the COMPLETE set of rules that must fire; anything else
# the investigator reports on that trace is a false positive.

CLEAN = "clean"
CRASHED = "crashed"
ERROR_EVENT = "error_event"
TOOL_NO_RESULT = "tool_without_result"
CONFLICTING_RECORDS = "conflicting_records"
TOKEN_GAP = "token_capture_gap"
BASELINE_DIVERGENCE = "baseline_divergence"
NEVER_ENDED = "run_never_ended"
STALE_CONSUMPTION = "stale_consumption"
EMPTY_SEARCH_CONCLUSION = "empty_search_conclusion"

GROUND_TRUTH: Dict[str, List[str]] = {
    CLEAN: [],
    # a recorded crash means: crash flag, and (by construction) a span that
    # never closed. run_never_ended deliberately does NOT fire — the crash
    # status already explains the missing run.end.
    CRASHED: ["run_status_crashed", "unclosed_span"],
    # the scripted error run also carries status 'crashed', so the crash
    # flag and the unclosed span are honest expected findings, not noise.
    ERROR_EVENT: ["run_status_crashed", "error_event", "unclosed_span"],
    TOOL_NO_RESULT: ["tool_without_result"],
    CONFLICTING_RECORDS: ["conflicting_records"],
    TOKEN_GAP: ["token_capture_gap"],
    BASELINE_DIVERGENCE: ["baseline_divergence"],
    NEVER_ENDED: ["run_never_ended"],
    STALE_CONSUMPTION: ["stale_consumption"],
    EMPTY_SEARCH_CONCLUSION: ["empty_search_conclusion"],
}

# A crashed/error run keeps an unclosed span by construction and has no
# run.end — the investigator must not double-penalise honest evidence.
# clean + benign scenarios use complete, closed traces.


def _span(sid: str, tool: str, arguments: Dict[str, Any], t: float) -> Dict[str, Any]:
    return {"kind": "span.start", "span_id": sid, "t_offset_ms": t,
            "data": {"label": f"tool:{tool}", "arguments": arguments}}


def _result(sid: str, payload: Any, t: float) -> Dict[str, Any]:
    return {"kind": "tool.result", "span_id": sid, "t_offset_ms": t, "data": {"payload": payload}}


def _end(sid: str, tool: str, t: float) -> Dict[str, Any]:
    return {"kind": "span.end", "span_id": sid, "t_offset_ms": t, "data": {"label": f"tool:{tool}"}}


def _llm(sid: str, t: float, tokens: bool = True) -> List[Dict[str, Any]]:
    return [
        {"kind": "span.start", "span_id": sid, "t_offset_ms": t, "data": {"label": "llm:mock-gpt"}},
        {"kind": "llm.response", "span_id": sid, "t_offset_ms": t + 1, "data": {"payload": "ok"},
         "input_tokens": 500 if tokens else None, "output_tokens": 120 if tokens else None, "model": "mock-gpt"},
        {"kind": "span.end", "span_id": sid, "t_offset_ms": t + 2, "data": {"label": "llm:mock-gpt"}},
    ]


@dataclass
class Scenario:
    scenario_id: str
    failure_type: str
    expected_rules: List[str]
    events: List[Dict[str, Any]] = field(default_factory=list)
    run_meta: Dict[str, Any] = field(default_factory=dict)
    baseline_events: List[Dict[str, Any]] | None = None
    baseline_meta: Dict[str, Any] | None = None


def _companies(rng: random.Random, years=(2025, 2025, 2025)) -> List[Dict[str, Any]]:
    return [
        {"company": "Adani Green", "revenue_inr_cr": 12345, "year": years[0]},
        {"company": "Tata Power Renewable", "revenue_inr_cr": 8912, "year": years[1]},
        {"company": "Waaree Energies", "revenue_inr_cr": 5678, "year": years[2]},
    ]


def _build(kind: str, i: int, rng: random.Random) -> Scenario:
    """One synthetic trace for a failure type. Structure varies with rng
    (tool order, ids, timings) but the failure itself is guaranteed."""
    sid = f"{kind}_{i}"
    q = f"query {i}"

    if kind == CLEAN:
        ev = ([_span("s1", "search", {"query": q}, 1),
               _result("s1", _companies(rng), 2), _end("s1", "search", 3)]
              + _llm("s2", 4)
              + [{"kind": "run.end", "t_offset_ms": 8}])
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    if kind == CRASHED:
        ev = [_span("s1", "search", {"query": q}, 1),
              _result("s1", _companies(rng), 2),
              _span("s2", "calculator", {"expression": f"{i} + 1"}, 4)]
        # no span.end, no run.end: died mid-tool
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "crashed"})

    if kind == ERROR_EVENT:
        ev = [_span("s1", "search", {"query": q}, 1),
              {"kind": "error", "span_id": "s1", "t_offset_ms": 2,
               "data": {"message": f"timeout calling search (scenario {i})"}}]
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "crashed"})

    if kind == TOOL_NO_RESULT:
        ev = [_span("s1", "search", {"query": q}, 1),
              _end("s1", "search", 2),
              {"kind": "run.end", "t_offset_ms": 3}]
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    if kind == CONFLICTING_RECORDS:
        stale_year = rng.choice([2021, 2022, 2023])
        ev = ([_span("s1", "search", {"query": q}, 1),
               _result("s1", _companies(rng) + [{"company": "Vikram Solar",
                                                 "revenue_inr_cr": 2100, "year": stale_year}], 2),
               _end("s1", "search", 3),
               _span("s2", "search", {"query": "vikram solar revenue"}, 4),
               _result("s2", [{"company": "Vikram Solar", "revenue_inr_cr": 3110, "year": 2025}], 5),
               _end("s2", "search", 6)]
              + _llm("s3", 7)
              + [{"kind": "run.end", "t_offset_ms": 11}])
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    if kind == TOKEN_GAP:
        ev = ([_span("s1", "search", {"query": q}, 1),
               _result("s1", _companies(rng), 2), _end("s1", "search", 3)]
              + _llm("s2", 4, tokens=False)
              + [{"kind": "run.end", "t_offset_ms": 8}])
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    if kind == NEVER_ENDED:
        ev = ([_span("s1", "search", {"query": q}, 1),
               _result("s1", _companies(rng), 2), _end("s1", "search", 3)]
              + _llm("s2", 4))
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    if kind == BASELINE_DIVERGENCE:
        good = ([_span("s1", "search", {"query": q}, 1),
                 _result("s1", _companies(rng), 2), _end("s1", "search", 3),
                 _span("s2", "search", {"query": "verification"}, 4),
                 _result("s2", [{"company": "Vikram Solar", "revenue_inr_cr": 3110, "year": 2025}], 5),
                 _end("s2", "search", 6)]
                + _llm("s3", 7)
                + [{"kind": "run.end", "t_offset_ms": 11}])
        bad = ([_span("s1", "search", {"query": q}, 1),
                _result("s1", _companies(rng), 2), _end("s1", "search", 3),
                _span("s2", "calculator", {"expression": f"{i} + 0"}, 4),
                _result("s2", {"value": 2100, "note": "trusted stale figure"}, 5),
                _end("s2", "calculator", 6)]
               + _llm("s3", 7)
               + [{"kind": "run.end", "t_offset_ms": 11}])
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), bad,
                        {"run_id": sid, "status": "completed"},
                        baseline_events=good, baseline_meta={"run_id": f"{sid}_baseline", "status": "completed"})

    if kind == STALE_CONSUMPTION:
        fresh_year = rng.choice([2024, 2025])
        ev = ([_span("s1", "search", {"query": q}, 1),
               _result("s1", [
                   {"metric": "usd_inr_rate", "value": 74.5, "as_of": "2023-12-31"},
                   {"metric": "usd_inr_rate", "value": 83.2, "as_of": f"{fresh_year}-10-01"},
               ], 2),
               _end("s1", "search", 3),
               _span("s2", "calculator", {"expression": "1000000 * 74.5"}, 4),
               _result("s2", {"value": 74500000}, 5),
               _end("s2", "calculator", 6)]
              + _llm("s3", 7)
              + [{"kind": "run.end", "t_offset_ms": 11}])
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    if kind == EMPTY_SEARCH_CONCLUSION:
        ev = ([_span("s1", "search", {"query": q}, 1),
               _result("s1", [], 2), _end("s1", "search", 3),
               _span("s2", "search", {"query": f"{q} retry"}, 4),
               _result("s2", [], 5), _end("s2", "search", 6),
               {"kind": "span.start", "span_id": "s3", "t_offset_ms": 7,
                "data": {"label": "llm:mock-gpt"}},
               {"kind": "llm.response", "span_id": "s3", "t_offset_ms": 8,
                "data": {"payload": "No matching records, so the answer is not available."},
                "input_tokens": 400, "output_tokens": 30, "model": "mock-gpt"},
               {"kind": "span.end", "span_id": "s3", "t_offset_ms": 9,
                "data": {"label": "llm:mock-gpt"}}]
              + [{"kind": "run.end", "t_offset_ms": 10}])
        return Scenario(sid, kind, list(GROUND_TRUTH[kind]), ev,
                        {"run_id": sid, "status": "completed"})

    raise ValueError(f"unknown scenario kind {kind}")


# The benchmark mix: clean-heavy (false-positive pressure), failures
# spread evenly across the nine failure types.
MIX: List[str] = (
    [CLEAN] * 40
    + [CRASHED, ERROR_EVENT, TOOL_NO_RESULT, CONFLICTING_RECORDS,
       TOKEN_GAP, BASELINE_DIVERGENCE, NEVER_ENDED,
       STALE_CONSUMPTION, EMPTY_SEARCH_CONCLUSION] * 9
)


def generate_scenarios(seed: int = 42) -> List[Scenario]:
    """Deterministic scenario suite. Length = len(MIX) (currently 121)."""
    rng = random.Random(seed)
    return [_build(kind, i, rng) for i, kind in enumerate(MIX)]
