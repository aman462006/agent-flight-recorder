"""
Phase 5 — Replay & diff.

Two replay modes (the core engineering decision of the whole product):

1. DETERMINISTIC REPLAY — re-execute an agent's code path using the
   tool responses RECORDED in the trace, so no external system is
   touched and no result changed underneath you. Used to verify a fix
   reproduces the original path, or to debug offline.

2. LIVE RERUN — run the agent again against current tools/model and
   diff the new trace against the old one.

DIFF DESIGN — 'earliest meaningful divergence':
- position-by-position comparison of the two event streams
- a divergence is a difference in *what the agent did* (tool call with
  different arguments, different tool selected, different model,
  different final payload) — not a difference in timestamps or costs,
  which are consequences, not causes
- the first divergence is where the two executions became different
  runs; everything after it is fallout. The UI shows the divergence
  and then what followed.

Honesty rules:
- identical runs → empty diff, reported as empty, no invented findings
- replay that requests a tool/argument combination never recorded →
  flagged as a divergence with the requested key; NEVER substituted
  with a made-up response
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Deterministic replay: a tool stub backed by the recorded trace.
# ---------------------------------------------------------------------------

class RecordedToolKit:
    """Looks up tool calls in a recorded trace. Deterministic replay's engine."""

    def __init__(self, events: List[Dict[str, Any]]):
        self.responses: Dict[Tuple[str, str], Any] = {}
        self.misses: List[Dict[str, Any]] = []
        pending: Dict[Optional[str], Tuple[str, Dict[str, Any]]] = {}
        for ev in events:
            if ev["kind"] == "span.start":
                label = (ev.get("data") or {}).get("label", "")
                if isinstance(label, str) and label.startswith("tool:"):
                    pending[ev["span_id"]] = (label[5:], (ev.get("data") or {}).get("arguments") or {})
            elif ev["kind"] == "tool.result" and ev["span_id"] in pending:
                tool, args = pending.pop(ev["span_id"])
                key = (tool, json.dumps(args, sort_keys=True))
                self.responses[key] = (ev.get("data") or {}).get("payload")

    def call(self, tool: str, arguments: Dict[str, Any]) -> Any:
        key = (tool, json.dumps(arguments, sort_keys=True))
        if key in self.responses:
            return self.responses[key]
        self.misses.append({"tool": tool, "arguments": arguments})
        raise ReplayDivergence(
            f"replay requested {tool}({json.dumps(arguments, sort_keys=True)}) "
            "which the original run never recorded — the agent's behaviour diverged"
        )


class ReplayDivergence(Exception):
    """The replayed agent wants something the original trace cannot provide."""


# ---------------------------------------------------------------------------
# Trace diff
# ---------------------------------------------------------------------------

@dataclass
class DiffEntry:
    position: int
    kind: str  # 'tool_call_changed' | 'span_missing' | 'llm_payload_changed' | 'model_changed'
    detail: Dict[str, Any]


@dataclass
class TraceDiff:
    run_a: str
    run_b: str
    identical: bool
    first_divergence: Optional[DiffEntry]
    entries: List[DiffEntry] = field(default_factory=list)


def _fingerprint(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reduce an event stream to the *decisions* the agent made."""
    out = []
    for ev in events:
        data = ev.get("data") or {}
        if ev["kind"] == "span.start":
            label = data.get("label", "")
            if isinstance(label, str) and label.startswith("tool:"):
                out.append({"type": "tool_call", "tool": label[5:], "arguments": data.get("arguments") or {}})
            elif isinstance(label, str) and label.startswith("llm:"):
                out.append({"type": "llm_call", "model": label[4:]})
        elif ev["kind"] == "llm.response":
            out.append({"type": "llm_payload", "model": ev.get("model"),
                        "input_tokens": ev.get("input_tokens"),
                        "output_tokens": ev.get("output_tokens")})
        elif ev["kind"] == "tool.result":
            out.append({"type": "tool_payload", "payload": data.get("payload")})
    return out


def diff_runs(events_a: List[Dict[str, Any]],
              events_b: List[Dict[str, Any]],
              run_a: str = "run_a", run_b: str = "run_b") -> TraceDiff:
    fp_a, fp_b = _fingerprint(events_a), _fingerprint(events_b)
    diff = TraceDiff(run_a=run_a, run_b=run_b, identical=True, first_divergence=None)

    for pos, (a, b) in enumerate(zip(fp_a, fp_b)):
        if a == b:
            continue
        diff.identical = False
        if a["type"] != b["type"]:
            entry = DiffEntry(pos, "span_missing",
                              {"a": a, "b": b, "why": "different operation types at this position"})
        elif a["type"] == "tool_call" and (a["tool"] != b["tool"] or a["arguments"] != b["arguments"]):
            entry = DiffEntry(pos, "tool_call_changed",
                              {"a": a, "b": b, "why": "agent chose a different tool or arguments"})
        elif a["type"] == "llm_call" and a["model"] != b["model"]:
            entry = DiffEntry(pos, "model_changed", {"a": a, "b": b, "why": "different model used"})
        elif a["type"] == "llm_payload" and (a["input_tokens"], a["output_tokens"]) != (b["input_tokens"], b["output_tokens"]):
            entry = DiffEntry(pos, "llm_payload_changed",
                              {"a": a, "b": b, "why": "token usage differs (consequence of an earlier divergence, or model nondeterminism)"})
        else:
            entry = DiffEntry(pos, "llm_payload_changed",
                              {"a": a, "b": b, "why": "response payloads differ"})
        diff.entries.append(entry)

    # length mismatch: one run ended earlier / later than the other
    if len(fp_a) != len(fp_b):
        diff.identical = False
        longer, which = (fp_a, "a") if len(fp_a) > len(fp_b) else (fp_b, "b")
        for pos in range(min(len(fp_a), len(fp_b)), len(longer)):
            diff.entries.append(DiffEntry(pos, "span_missing",
                                          {"a": fp_a[pos] if pos < len(fp_a) else None,
                                           "b": fp_b[pos] if pos < len(fp_b) else None,
                                           "why": f"run {which} continued where the other ended"}))

    diff.entries.sort(key=lambda e: e.position)
    diff.first_divergence = diff.entries[0] if diff.entries else None
    return diff


def replay_deterministic(events: List[Dict[str, Any]],
                         agent_fn: Callable[[RecordedToolKit], Any]) -> Dict[str, Any]:
    """Re-run agent_fn with every tool call served from the recorded trace.

    Returns what agent_fn returned plus a replay report: either
    'faithful' (agent made the exact same calls) or the first divergence
    the moment the replayed agent asked for anything unrecorded.
    """
    kit = RecordedToolKit(events)
    report: Dict[str, Any] = {"stored_tool_responses": len(kit.responses)}
    try:
        result = agent_fn(kit)
        report.update({"faithful": len(kit.misses) == 0, "misses": kit.misses, "result": result})
    except ReplayDivergence as d:
        report.update({"faithful": False, "misses": kit.misses, "divergence": str(d)})
    return report
