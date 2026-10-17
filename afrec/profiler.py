"""
Phase 4 — Cost & latency profiler.

Reads a recorded trace and answers the questions that make a run actionable:
- what did each step cost, in dollars, grounded in per-model pricing?
- which model call burned most of the budget?
- which spans were slow, and is that latency LLM-bound or tool-bound?

Honesty rules (same DNA as the rest of the system):
- cost is an ESTIMATE from published rates; the pricing config used is
  reported alongside the numbers so the estimate is auditable
- unknown usage or unknown model pricing → 'unknown', never a fake 0
- latency comes from span.start/span.end offsets; unclosed spans are
  reported as open, not assigned a made-up duration
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import PricingConfig

# Published list rates, USD per million tokens (estimates, overridable).
# Anything not here costs unknown by design — the profiler must say so,
# because a confidently-wrong number is worse than an honest gap.
DEFAULT_PRICING: Dict[str, PricingConfig] = {
    "mock-gpt": PricingConfig(input_per_m=0.15, output_per_m=0.60),
    "gpt-4o": PricingConfig(input_per_m=2.50, output_per_m=10.00, cached_input_per_m=1.25),
    "gpt-4o-mini": PricingConfig(input_per_m=0.15, output_per_m=0.60, cached_input_per_m=0.075),
    "claude-sonnet-4": PricingConfig(input_per_m=3.00, output_per_m=15.00),
    "llama-3.1-8b-instruct": PricingConfig(input_per_m=0.05, output_per_m=0.08),
    # Groq's published self-serve rate for Llama 3.3 70B Versatile as of its
    # 2026-08-26 enterprise transition (last public rate, groq.com pricing page).
    "llama-3.3-70b-versatile": PricingConfig(input_per_m=0.59, output_per_m=0.79),
}


def profile_run(events: List[Dict[str, Any]],
                pricing: Optional[Dict[str, PricingConfig]] = None) -> Dict[str, Any]:
    """Build a cost + latency profile from one run's ordered event list."""
    price_table = pricing if pricing is not None else DEFAULT_PRICING

    span_meta: Dict[str, Dict[str, Any]] = {}
    llm_calls: List[Dict[str, Any]] = []
    totals = {
        "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
        "estimated_cost_usd": 0.0, "unknown_cost_calls": 0, "llm_calls": 0,
    }
    unknown_models: set = set()

    # pass 1: span timings from start/end pairs
    for ev in events:
        sid = ev.get("span_id")
        if ev["kind"] == "span.start" and sid:
            span_meta.setdefault(sid, {"label": (ev.get("data") or {}).get("label", sid),
                                        "start_ms": ev["t_offset_ms"], "end_ms": None})
        elif ev["kind"] == "span.end" and sid in span_meta:
            span_meta[sid]["end_ms"] = ev["t_offset_ms"]

    # pass 2: LLM responses → per-call costs
    for ev in events:
        if ev["kind"] != "llm.response":
            continue
        totals["llm_calls"] += 1
        model = ev.get("model") or "unknown"
        pricing_cfg = price_table.get(model)
        in_tok, out_tok, cached = ev.get("input_tokens"), ev.get("output_tokens"), ev.get("cached_tokens")
        cost = pricing_cfg.estimate_cost(in_tok, out_tok, cached) if pricing_cfg else None
        if cost is None:
            totals["unknown_cost_calls"] += 1
            if model not in price_table:
                unknown_models.add(model)
        else:
            totals["estimated_cost_usd"] += cost
        totals["input_tokens"] += in_tok or 0
        totals["output_tokens"] += out_tok or 0
        totals["cached_tokens"] += cached or 0
        sid = ev.get("span_id")
        span = span_meta.get(sid, {}) if sid else {}
        llm_calls.append({
            "span_id": sid, "label": span.get("label", sid), "model": model,
            "input_tokens": in_tok, "output_tokens": out_tok, "cached_tokens": cached,
            "estimated_cost_usd": cost,  # None = unknown, by design
            "latency_ms": (span["end_ms"] - span["start_ms"]) if span.get("end_ms") is not None else None,
            "pricing_source": ("published rates (estimate)" if pricing_cfg else None),
        })

    # slowest spans (any kind)
    slowest = sorted(
        ({"span_id": sid, "label": meta["label"],
          "latency_ms": (meta["end_ms"] - meta["start_ms"]) if meta["end_ms"] is not None else None,
          "open": meta["end_ms"] is None}
         for sid, meta in span_meta.items()),
        key=lambda s: (-1 if s["open"] else 1, -(s["latency_ms"] or 0)),
    )[:5]

    return {
        "totals": totals,
        "llm_calls": llm_calls,
        "slowest_spans": slowest,
        "unknown_pricing_models": sorted(unknown_models),
        "pricing_note": "Costs are estimates from provider-published list rates, "
                         "not invoices. Calls with unknown usage or unknown model "
                         "pricing contribute nothing to estimated_cost_usd and are "
                         "counted separately.",
    }
