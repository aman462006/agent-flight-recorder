"""Phase 5 acceptance tests: replay fidelity and diff precision.

The stale-figure scenario is the recurring test subject: a good run
(re-queries Vikram Solar's revenue) vs a bad run (trusts the 2022 figure).
The diff must locate the exact tool call where they part ways — that is
the product's headline capability.
"""

import pytest

from afrec.replay import RecordedToolKit, replay_deterministic, diff_runs, ReplayDivergence


def _recorded_run(events: list) -> list:
    return events


def _good_run_events():
    """Two tool calls: search, then the corrective re-query."""
    return [
        {"kind": "span.start", "span_id": "s1", "t_offset_ms": 1, "data": {"label": "tool:search", "arguments": {"query": "solar companies india"}}},
        {"kind": "tool.result", "span_id": "s1", "t_offset_ms": 2, "data": {"payload": [{"company": "Adani Green"}]}},
        {"kind": "span.end", "span_id": "s1", "t_offset_ms": 3, "data": {"label": "tool:search"}},
        {"kind": "span.start", "span_id": "s2", "t_offset_ms": 4, "data": {"label": "tool:search", "arguments": {"query": "vikram solar revenue"}}},
        {"kind": "tool.result", "span_id": "s2", "t_offset_ms": 5, "data": {"payload": [{"revenue": 3110, "year": 2025}]}},
        {"kind": "span.end", "span_id": "s2", "t_offset_ms": 6, "data": {"label": "tool:search"}},
        {"kind": "span.start", "span_id": "s3", "t_offset_ms": 7, "data": {"label": "llm:mock-gpt"}},
        {"kind": "llm.response", "span_id": "s3", "t_offset_ms": 8, "data": {"payload": "comparison"},
         "input_tokens": 500, "output_tokens": 180, "model": "mock-gpt"},
    ]


def _bad_run_events():
    """Same first call, then TRUSTS the stale figure instead of re-querying."""
    return [
        {"kind": "span.start", "span_id": "s1", "t_offset_ms": 1, "data": {"label": "tool:search", "arguments": {"query": "solar companies india"}}},
        {"kind": "tool.result", "span_id": "s1", "t_offset_ms": 2, "data": {"payload": [{"company": "Adani Green"}]}},
        {"kind": "span.end", "span_id": "s1", "t_offset_ms": 3, "data": {"label": "tool:search"}},
        {"kind": "span.start", "span_id": "s2", "t_offset_ms": 4, "data": {"label": "tool:calculator", "arguments": {"expression": "2100 + 0"}}},
        {"kind": "tool.result", "span_id": "s2", "t_offset_ms": 5, "data": {"payload": {"value": 2100, "note": "trusted 2022 figure"}}},
        {"kind": "span.end", "span_id": "s2", "t_offset_ms": 6, "data": {"label": "tool:calculator"}},
        {"kind": "span.start", "span_id": "s3", "t_offset_ms": 7, "data": {"label": "llm:mock-gpt"}},
        {"kind": "llm.response", "span_id": "s3", "t_offset_ms": 8, "data": {"payload": "comparison"},
         "input_tokens": 500, "output_tokens": 180, "model": "mock-gpt"},
    ]


class TestRecordedToolKit:
    def test_returns_stored_response_for_identical_call(self):
        kit = RecordedToolKit(_good_run_events())
        out = kit.call("search", {"query": "solar companies india"})
        assert out == [{"company": "Adani Green"}]
        out2 = kit.call("search", {"query": "vikram solar revenue"})
        assert out2 == [{"revenue": 3110, "year": 2025}]

    def test_raises_divergence_for_unrecorded_call(self):
        kit = RecordedToolKit(_good_run_events())
        with pytest.raises(ReplayDivergence):
            kit.call("search", {"query": "totally new query"})
        assert kit.misses == [{"tool": "search", "arguments": {"query": "totally new query"}}]


class TestReplayDeterministic:
    def test_faithful_replay_of_original_path(self):
        def agent(kit):
            first = kit.call("search", {"query": "solar companies india"})
            second = kit.call("search", {"query": "vikram solar revenue"})
            return {"used_fresh": second[0]["year"] == 2025}

        report = replay_deterministic(_good_run_events(), agent)
        assert report["faithful"] is True
        assert report["stored_tool_responses"] == 2
        assert report["result"]["used_fresh"] is True

    def test_divergent_replay_is_flagged_not_faked(self):
        def changed_agent(kit):
            kit.call("search", {"query": "solar companies india"})
            kit.call("search", {"query": "a different query"})  # behaviour changed
            return None

        report = replay_deterministic(_good_run_events(), changed_agent)
        assert report["faithful"] is False
        assert "divergence" in report
        assert report["misses"][0]["arguments"]["query"] == "a different query"


class TestDiff:
    def test_identical_runs_produce_empty_diff(self):
        d = diff_runs(_good_run_events(), _good_run_events())
        assert d.identical is True
        assert d.first_divergence is None
        assert d.entries == []

    def test_stale_figure_failure_is_located_at_the_second_tool_call(self):
        d = diff_runs(_good_run_events(), _bad_run_events(), run_a="good", run_b="bad")
        assert d.identical is False
        assert d.first_divergence is not None
        assert d.first_divergence.kind == "tool_call_changed"
        # positions include payloads: 0 = tool call, 1 = its result,
        # 2 = the SECOND tool call — where good and bad runs part ways
        assert d.first_divergence.position == 2
        assert d.first_divergence.detail["a"]["tool"] == "search"
        assert d.first_divergence.detail["b"]["tool"] == "calculator"

    def test_earlier_run_end_is_reported_not_silenced(self):
        short = _good_run_events()[:3]  # crashed after first search
        d = diff_runs(_good_run_events(), short, run_a="full", run_b="short")
        assert d.identical is False
        assert d.entries[-1].kind == "span_missing"
        assert d.entries[-1].detail["b"] is None

    def test_timestamp_differences_are_not_divergences(self):
        a = _good_run_events()
        b = [dict(ev, t_offset_ms=ev["t_offset_ms"] + 100) for ev in a]
        d = diff_runs(a, b)
        assert d.identical is True  # same decisions, different timing
