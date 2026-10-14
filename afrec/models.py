"""
Agent Flight Recorder — core data model.

Every recorded operation is an Event attached to a Run. Events carry
parent-child relationships so a trace reconstructs into a tree:

    run (root span)
     ├── llm_call (span)
     │    └── ...
     └── tool_call (span, parent = run)
          └── tool_result (event, parent = tool_call)

Design rules:
- Plain dataclasses, JSON-serialisable, no framework lock-in.
- Every field the cost profiler and replay engine need later
  (tokens, cost inputs, tool payloads) is captured NOW, because
  retrofitting capture into old traces is impossible.
- `kind` is a closed enum-ish set of strings; unknown kinds are
  preserved, never rejected, so the SDK can record framework events
  we have not modelled yet.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


def new_id(prefix: str) -> str:
    """Short, sortable-enough unique id: prefix + time + random."""
    return f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


# Event kinds the SDK understands natively. Anything else is preserved verbatim.
KIND_RUN_START = "run.start"
KIND_RUN_END = "run.end"
KIND_SPAN_START = "span.start"
KIND_SPAN_END = "span.end"
KIND_LLM_REQUEST = "llm.request"
KIND_LLM_RESPONSE = "llm.response"
KIND_TOOL_REQUEST = "tool.request"
KIND_TOOL_RESULT = "tool.result"
KIND_ERROR = "error"


@dataclass
class Event:
    """One recorded operation. The unit of replay, diff and cost analysis."""

    run_id: str
    kind: str
    # Span this event belongs to / opens. None means run-level.
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    # Wall-clock (epoch seconds, monotonic-safe to ms) and monotonic offset
    # from run start, so ordering survives clock skew within a run.
    timestamp: float = field(default_factory=time.time)
    t_offset_ms: float = 0.0
    # Payload: model name, messages, tool name, arguments, results, errors.
    data: Dict[str, Any] = field(default_factory=dict)
    # Token accounting for llm.response events (cost profiler reads these).
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    model: Optional[str] = None
    # Server-assigned identity; set on ingest to de-duplicate submissions.
    event_id: Optional[str] = None

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunMeta:
    """Metadata for one agent execution (the 'flight')."""

    run_id: str
    task: str
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    status: str = "running"  # running | completed | crashed
    # Free-form tags: agent name, framework, git sha, environment.
    tags: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PricingConfig:
    """Per-model pricing in USD per million tokens, used by the cost profiler.

    Costs are ESTIMATES from provider-published rates; the recorder never
    fabricates a cost — if usage or pricing is unknown, cost is None.
    """

    input_per_m: float = 0.0
    output_per_m: float = 0.0
    cached_input_per_m: Optional[float] = None  # None → treated as input_per_m

    def estimate_cost(self, input_tokens: Optional[int],
                      output_tokens: Optional[int],
                      cached_tokens: Optional[int] = None) -> Optional[float]:
        if input_tokens is None and output_tokens is None:
            return None  # unknown usage → unknown cost, never a fake number
        inp = input_tokens or 0
        out = output_tokens or 0
        cached = 0
        if cached_tokens:
            cached = min(cached_tokens, inp)
        uncached = inp - cached
        cached_rate = self.cached_input_per_m
        if cached_rate is None:
            cached_rate = self.input_per_m
        cost = (uncached * self.input_per_m + cached * cached_rate
                + out * self.output_per_m) / 1_000_000
        return round(cost, 6)
