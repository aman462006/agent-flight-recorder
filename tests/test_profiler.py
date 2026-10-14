"""Phase 4 acceptance tests: cost/latency profiler honesty and math."""

import pytest

from afrec.profiler import profile_run, DEFAULT_PRICING


def _events(*llms, spans=()):
    """Build a minimal event list: span pairs + llm.response entries."""
    out = []
    for i, (sid, label, s, e) in enumerate(spans):
        out.append({"kind": "span.start", "span_id": sid, "t_offset_ms": s, "data": {"label": label}})
        out.append({"kind": "span.end", "span_id": sid, "t_offset_ms": e})
    for i, (sid, model, inp, outp, cached) in enumerate(llms):
        out.append({"kind": "llm.response", "span_id": sid, "model": model,
                    "t_offset_ms": 50 + i, "data": {},
                    "input_tokens": inp, "output_tokens": outp, "cached_tokens": cached})
    return out


def test_known_model_costs_sum_exactly():
    evs = _events(
        ("s1", "gpt-4o-mini", 1000, 500, None),
        ("s2", "gpt-4o", 1000, 0, None),
        spans=(("s1", "llm:gpt-4o-mini", 0, 100), ("s2", "llm:gpt-4o", 10, 30)),
    )
    p = profile_run(evs)
    mini = DEFAULT_PRICING["gpt-4o-mini"].estimate_cost(1000, 500, None)
    big = DEFAULT_PRICING["gpt-4o"].estimate_cost(1000, 0, None)
    assert p["totals"]["estimated_cost_usd"] == pytest.approx(mini + big)
    assert p["totals"]["llm_calls"] == 2
    assert p["unknown_pricing_models"] == []


def test_unknown_model_is_unknown_not_zero():
    evs = _events(("s1", "grok-9000", 1000, 500, None),
                  spans=(("s1", "llm:grok-9000", 0, 100),))
    p = profile_run(evs)
    assert p["totals"]["estimated_cost_usd"] == 0.0  # nothing KNOWN to count
    assert p["totals"]["unknown_cost_calls"] == 1
    assert p["unknown_pricing_models"] == ["grok-9000"]
    assert p["llm_calls"][0]["estimated_cost_usd"] is None


def test_missing_usage_on_known_model_is_unknown():
    evs = _events(("s1", "gpt-4o", None, None, None),
                  spans=(("s1", "llm:gpt-4o", 0, 100),))
    p = profile_run(evs)
    assert p["totals"]["unknown_cost_calls"] == 1
    assert p["totals"]["estimated_cost_usd"] == 0.0


def test_cached_tokens_use_cached_rate():
    evs = _events(("s1", "gpt-4o", 2000, 0, 1000),
                  spans=(("s1", "llm:gpt-4o", 0, 100),))
    p = profile_run(evs)
    cfg = DEFAULT_PRICING["gpt-4o"]
    expected = (1000 * cfg.input_per_m + 1000 * cfg.cached_input_per_m) / 1e6
    assert p["totals"]["estimated_cost_usd"] == pytest.approx(expected)


def test_latency_and_open_span_handling():
    evs = _events(
        ("s1", "gpt-4o-mini", 10, 10, None),
        spans=(("s1", "llm:gpt-4o-mini", 5, 45), ("s2", "tool:slow", 50, None)),
    )
    p = profile_run(evs)
    assert p["llm_calls"][0]["latency_ms"] == 40
    top = p["slowest_spans"][0]
    assert top["span_id"] == "s2" and top["open"] is True and top["latency_ms"] is None
