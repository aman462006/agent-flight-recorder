"""Unit tests for the repeated_decision (token-burning loop) rule."""

from afrec.investigator import investigate_run, LOOP_THRESHOLD


def _llm_loop_events(n: int):
    """n identical LLM calls with responses - the classic burning loop."""
    events = []
    t = 1000.0
    for i in range(n):
        sid = f"span_{i}"
        events.append({
            "kind": "span.start", "span_id": sid, "timestamp": t + i, "t_offset_ms": i * 10.0,
            "data": {"label": "llm:gpt-4o-mini", "messages": [{"role": "user", "content": "same"}]},
        })
        events.append({
            "kind": "llm.response", "span_id": sid, "timestamp": t + i + 0.5, "t_offset_ms": i * 10.0 + 5,
            "data": {"payload": {"text": "nope"}},
            "input_tokens": 470, "output_tokens": 12, "model": "gpt-4o-mini",
        })
        events.append({
            "kind": "span.end", "span_id": sid, "timestamp": t + i + 0.6, "t_offset_ms": i * 10.0 + 6,
            "data": {},
        })
    return events


def test_loop_below_threshold_is_not_flagged():
    """4 identical calls stay quiet: the rule must not cry wolf early."""
    events = _llm_loop_events(LOOP_THRESHOLD - 1)
    inv = investigate_run(events, run_meta={"status": "completed"}, run_id="r")
    assert not any(f.rule == "repeated_decision" for f in inv.findings)


def test_loop_at_threshold_is_flagged_with_token_burn():
    """A 20-call identical loop is flagged, with the REAL summed tokens."""
    n = 20
    events = _llm_loop_events(n)
    inv = investigate_run(events, run_meta={"status": "completed"}, run_id="r")
    hits = [f for f in inv.findings if f.rule == "repeated_decision"]
    assert len(hits) == 1
    f = hits[0]
    assert f"repeated {n} times" in f.observed
    # honest burn: n * (470 + 12)
    assert f"{n * 470} input + {n * 12} output tokens" in f.observed
    assert f.evidence  # findings carry evidence, not just claims


def test_loop_with_changing_arguments_is_not_flagged():
    """Retries with different arguments are progress, not a loop."""
    events = _llm_loop_events(LOOP_THRESHOLD + 2)
    for i, ev in enumerate(events):
        if ev["kind"] == "span.start":
            ev["data"]["messages"] = [{"role": "user", "content": f"attempt {i}"}]
    inv = investigate_run(events, run_meta={"status": "completed"}, run_id="r")
    assert not any(f.rule == "repeated_decision" for f in inv.findings)


def test_tool_loop_flagged():
    """The same tool call with identical args 10 times also fires."""
    events = []
    for i in range(10):
        sid = f"ts_{i}"
        events.append({
            "kind": "span.start", "span_id": sid, "timestamp": 1000 + i, "t_offset_ms": i * 5.0,
            "data": {"label": "tool:search", "arguments": {"query": "fix the bug"}},
        })
        events.append({
            "kind": "tool.result", "span_id": sid, "timestamp": 1000 + i + 0.1, "t_offset_ms": i * 5.0 + 1,
            "data": {"payload": {"rows": 0}},
        })
        events.append({
            "kind": "span.end", "span_id": sid, "timestamp": 1000 + i + 0.2, "t_offset_ms": i * 5.0 + 2,
            "data": {},
        })
    inv = investigate_run(events, run_meta={"status": "completed"}, run_id="r")
    hits = [f for f in inv.findings if f.rule == "repeated_decision"]
    assert len(hits) == 1
    assert "repeated 10 times" in hits[0].observed
