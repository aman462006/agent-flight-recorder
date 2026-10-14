"""Phase 1 acceptance tests: does the recorder capture a complete, ordered,
parent-child-correct trace of an agent run — including the failure path?"""

import json
import os
import sqlite3

os.environ["AFREC_DB"] = "test_afrec.sqlite3"

import pytest  # noqa: E402

from afrec import Recorder, redact, PricingConfig  # noqa: E402
from afrec.models import (  # noqa: E402
    KIND_ERROR, KIND_LLM_RESPONSE, KIND_RUN_END, KIND_RUN_START,
    KIND_SPAN_START, KIND_TOOL_RESULT,
)
from demo_agent.agent import run_agent  # noqa: E402


@pytest.fixture()
def recorder(tmp_path):
    from afrec.recorder import SqliteStore
    return Recorder(SqliteStore(str(tmp_path / "t.sqlite3")))


def test_records_complete_ordered_trace(recorder):
    with recorder.run("test task") as run:
        with run.llm_call("m", [{"role": "user", "content": "hi"}]) as s:
            s.result("ok", input_tokens=10, output_tokens=5)
        with run.tool_call("calc", {"expression": "1+1"}) as t:
            t.result({"value": 2})

    events = recorder.store.get_events(run.run_id)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == KIND_RUN_START
    assert kinds[-1] == KIND_RUN_END
    assert KIND_SPAN_START in kinds
    assert KIND_LLM_RESPONSE in kinds
    assert KIND_TOOL_RESULT in kinds
    # ordering: monotonic offsets never decrease
    offsets = [e["t_offset_ms"] for e in events]
    assert offsets == sorted(offsets)


def test_parent_child_relationships(recorder):
    with recorder.run("nested") as run:
        with run.tool_call("outer", {}) as outer:
            with run.tool_call("inner", {}) as inner:
                assert inner.parent is not None
            inner_evt = [e for e in recorder.store.get_events(run.run_id)
                         if e["kind"] == KIND_SPAN_START and e["data"].get("label") == "tool:inner"]
            assert inner_evt and inner_evt[0]["parent_span_id"] == outer.span_id


def test_crash_leaves_partial_run_with_error(recorder):
    with pytest.raises(RuntimeError):
        with recorder.run("boom") as run:
            with run.tool_call("explode", {}) as t:
                t.result("never happens")
                raise RuntimeError("tool exploded")

    events = recorder.store.get_events(run.run_id)
    kinds = [e["kind"] for e in events]
    assert KIND_ERROR in kinds
    assert kinds[-1] == KIND_RUN_END
    runs = {r["run_id"]: r for r in recorder.store.list_runs()}
    assert runs[run.run_id]["status"] == "crashed"


def test_redaction_strips_secrets():
    text = "my key sk-abcdef1234567890xyz and password=hunter2secret"
    out = redact({"note": text, "api_key": "verysecretvalue123"})
    assert "abcdef1234567890xyz" not in json.dumps(out)
    assert out["api_key"] == "***REDACTED***"


def test_cost_estimation_honest_when_unknown():
    p = PricingConfig(input_per_m=0.15, output_per_m=0.60)
    assert p.estimate_cost(None, None) is None  # no fake zero-cost
    c = p.estimate_cost(1000, 500)
    assert abs(c - (1000 * 0.15 + 500 * 0.60) / 1e6) < 1e-9


def test_demo_agent_failure_scenario_leaves_evidence(recorder, monkeypatch):
    """The reason the product exists: the stale-figure failure must be visible
    in the trace as a decision the investigator can point at."""
    import afrec.recorder as rec_mod
    monkeypatch.setattr(rec_mod, "get_recorder", lambda: recorder)
    import demo_agent.agent as agent_mod
    monkeypatch.setattr(agent_mod, "get_recorder", lambda: recorder)

    run_agent("Find three solar companies and compare revenue", use_stale_figure=True)

    # latest run captured tool results including the stale 2022 payload
    runs = recorder.store.list_runs()
    assert runs and runs[0]["status"] == "completed"
    events = recorder.store.get_events(runs[0]["run_id"])
    results = [e for e in events if e["kind"] == KIND_TOOL_RESULT]
    payloads = json.dumps([e["data"] for e in results])
    assert "2022" in payloads  # the stale figure is on record, findable
    assert "note" in payloads and "trusted 2022 figure" in payloads
