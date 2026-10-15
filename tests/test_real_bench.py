"""Tests for bench/real — the real-run evaluation pipeline.

All LLM calls are faked: these tests prove the harness (instrumentation,
trace format, collect/label/evaluate plumbing) without network, keys, or
auto-generated labels. The labelled-fixture tests use explicitly synthetic
traces written by the TEST, not by the pipeline, so no label is ever
machine-produced for a real trace.
"""

import json
import os
import tempfile

import pytest

from bench.real.agent import (
    CORPUS, _parse_action, calculator, search, run_task,
)


class FakeLLM:
    """Scripted stand-in for GeminiLLM: same duck-typed interface."""

    def __init__(self, replies):
        self.model = "fake-model"
        self.temperature = 0.0
        self.replies = list(replies)

    def complete(self, prompt):
        text = self.replies.pop(0)
        return {"text": text, "input_tokens": 10, "output_tokens": 5,
                "model": self.model}


# -- tools -----------------------------------------------------------------

def test_search_returns_stale_and_fresh_for_named_entity():
    hits = search("vikram solar revenue")
    assert {h["year"] for h in hits} == {2022, 2025}


def test_search_empty_for_unknown_entity():
    assert search("infosys") == []


def test_search_specific_query_beats_generic():
    assert len(search("adani green tata power")) == 2


def test_calculator_and_rejects_garbage():
    assert calculator("3500*1.2") == 4200.0
    with pytest.raises(ValueError):
        calculator("import os")


def test_parse_action_tolerates_fences_rejects_garbage():
    assert _parse_action('```json\n{"answer": "42"}\n```')["answer"] == "42"
    assert _parse_action("no json here") is None
    assert _parse_action('{"irrelevant": 1}') is None


# -- the instrumented run ---------------------------------------------------

def test_run_task_records_llm_and_tool_spans():
    llm = FakeLLM([
        '{"tool": "search", "args": {"query": "vikram solar revenue"}}',
        '{"answer": "3110 crore (2025 record)"}',
    ])
    trace = run_task("Vikram Solar revenue?", llm, task_id="t-test-1")
    kinds = [e["kind"] for e in trace["events"]]
    assert trace["final_answer"] == "3110 crore (2025 record)"
    assert trace["tool_calls"] == 1 and trace["parse_errors"] == 0
    assert "llm.response" in kinds and "tool.result" in kinds
    assert trace["run_meta"]["status"] == "completed"
    assert trace["model"] == "fake-model" and trace["temperature"] == 0.0
    # real token accounting flowed into the trace
    llm_resp = [e for e in trace["events"] if e["kind"] == "llm.response"][0]
    assert llm_resp["input_tokens"] == 10 and llm_resp["output_tokens"] == 5


def test_run_task_records_parse_errors_and_recovery():
    llm = FakeLLM([
        "I think the answer is 3110",             # garbage -> parse error
        "maybe {\"answer\": \"3110\"} or not",    # json embedded in prose -> ok
    ])
    trace = run_task("Vikram Solar revenue?", llm, task_id="t-test-2")
    assert trace["parse_errors"] == 1
    assert trace["final_answer"] == "3110"


def test_run_task_unknown_tool_result_is_recorded_not_raised():
    llm = FakeLLM([
        '{"tool": "web_scrape", "args": {"url": "http://x"}}',
        '{"answer": "could not scrape"}',
    ])
    trace = run_task("scrape something", llm, task_id="t-test-3")
    results = [e for e in trace["events"] if e["kind"] == "tool.result"]
    assert any("unknown tool" in json.dumps(r["data"]) for r in results)
    assert trace["final_answer"] == "could not scrape"


def test_run_task_step_budget_exhaustion_is_visible():
    llm = FakeLLM(['{"tool": "search", "args": {"query": "x"}}'] * 8)
    trace = run_task("loop forever", llm, task_id="t-test-4")
    assert trace["final_answer"] is None          # no answer produced
    assert trace["tool_calls"] == 8              # it really looped


# -- evaluate plumbing (synthetic fixtures, human-style labels) -----------

def _mk_trace(tmpdir, tid, status="completed", events=None):
    events = events or [
        {"kind": "run.start", "span_id": None, "parent_span_id": None,
         "t_offset_ms": 0, "data": {}, "event_id": f"{tid}-1"},
        {"kind": "span.start", "span_id": "s1", "parent_span_id": None,
         "t_offset_ms": 1, "data": {"label": "tool:search", "arguments": {"query": "x"}}},
        {"kind": "tool.result", "span_id": "s1", "parent_span_id": "s1",
         "t_offset_ms": 2, "data": {"payload": [{"company": "A", "year": 2022}]}},
        {"kind": "span.end", "span_id": "s1", "parent_span_id": None,
         "t_offset_ms": 3, "data": {"label": "tool:search"}},
        {"kind": "run.end", "span_id": None, "parent_span_id": None,
         "t_offset_ms": 4, "data": {"status": status}},
    ]
    trace = {"task_id": tid, "task": "synthetic", "model": "fake",
             "temperature": 0.0, "collected_at": "now", "run_id": tid,
             "run_meta": {"run_id": tid, "task": "synthetic", "status": status},
             "events": events, "final_answer": None,
             "tool_calls": 1, "parse_errors": 0}
    with open(os.path.join(tmpdir, f"{tid}.json"), "w") as f:
        json.dump(trace, f)
    return trace


def test_evaluate_scoring_math_on_synthetic_fixtures(monkeypatch):
    import bench.real.evaluate as ev
    tmp = tempfile.mkdtemp()
    # one clean trace the investigator should call clean (TN)
    _mk_trace(tmp, "clean1")
    # one trace the investigator will flag (run never ended -> run_never_ended)
    _mk_trace(tmp, "hang1", events=[
        {"kind": "run.start", "span_id": None, "parent_span_id": None,
         "t_offset_ms": 0, "data": {}, "event_id": "h-1"},
    ])
    labels = {
        "clean1": {"label": "success", "failure_type": None, "note": None},
        "hang1": {"label": "failure", "failure_type": "loop", "note": None},
    }
    monkeypatch.setattr(ev, "TRACES_DIR", tmp)
    monkeypatch.setattr(ev, "LABELS_PATH", os.path.join(tmp, "labels.json"))
    json.dump(labels, open(ev.LABELS_PATH, "w"))

    # capture the report
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ev.main()
    out = buf.getvalue()
    assert "REAL-RUN EVALUATION" in out
    assert "recall" in out.lower()
    # clean1 clean (TN), hang1 flagged (TP): precision 100%, recall 100%
    assert "precision (of flags): 100%" in out.lower()
    assert "recall (of failures): 100%" in out.lower()


def test_evaluate_reports_miss_and_false_positive(monkeypatch):
    import bench.real.evaluate as ev
    tmp = tempfile.mkdtemp()
    # investigator flags this (never ended) but human says success -> FP
    _mk_trace(tmp, "fp1", events=[
        {"kind": "run.start", "span_id": None, "parent_span_id": None,
         "t_offset_ms": 0, "data": {}, "event_id": "f-1"},
    ])
    # investigator sees a normal clean trace but human says failure -> MISS
    _mk_trace(tmp, "miss1")
    labels = {
        "fp1": {"label": "success", "failure_type": None, "note": None},
        "miss1": {"label": "failure", "failure_type": "wrong_answer", "note": None},
    }
    monkeypatch.setattr(ev, "TRACES_DIR", tmp)
    monkeypatch.setattr(ev, "LABELS_PATH", os.path.join(tmp, "labels.json"))
    json.dump(labels, open(ev.LABELS_PATH, "w"))
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ev.main()
    out = buf.getvalue()
    assert "MISSES" in out and "miss1" in out
    assert "FALSE POSITIVES" in out and "fp1" in out
    assert "recall (of failures): 0%" in out.lower()


def test_evaluate_with_no_labels_is_not_fake(monkeypatch, capsys):
    import bench.real.evaluate as ev
    monkeypatch.setattr(ev, "LABELS_PATH", "/nonexistent/labels.json")
    ev.main()
    out = capsys.readouterr().out
    assert "no labels" in out.lower()  # says so plainly instead of inventing numbers


def test_make_llm_prefers_groq_then_gemini(monkeypatch):
    from bench.real import agent as A
    # no keys at all -> clear error, never a silent fake
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No LLM provider key"):
        A.make_llm()
    # groq wins when both are present (RevGuard's production provider)
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    llm = A.make_llm()
    assert isinstance(llm, A.GroqLLM)
    assert llm.model == "llama-3.3-70b-versatile"
    # gemini fallback when only gemini is present
    monkeypatch.delenv("GROQ_API_KEY")
    assert isinstance(A.make_llm(), A.GeminiLLM)


def test_groqllm_parses_response_shape():
    from bench.real import agent as A
    import urllib.request, urllib.error, io
    fake_body = {"choices": [{"message": {"content": '{"answer": "ok"}'}}],
                 "usage": {"prompt_tokens": 12, "completion_tokens": 7},
                 "model": "llama-3.3-70b-versatile"}
    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    captured = {}
    def fake_urlopen(req, timeout=None):
        captured["auth"] = req.headers.get("Authorization", "")
        return FakeResp(json.dumps(fake_body).encode())
    monkey = pytest.MonkeyPatch()
    monkey.setattr(urllib.request, "urlopen", fake_urlopen)
    llm = A.GroqLLM(api_key="k-test")
    out = llm.complete("hi")
    monkey.undo()
    assert out["text"] == '{"answer": "ok"}'
    assert out["input_tokens"] == 12 and out["output_tokens"] == 7
    assert captured["auth"] == "Bearer k-test"
