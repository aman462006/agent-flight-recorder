"""HttpStore: streaming a real agent run to the ingestion API.

Covers the four design rules the class promises:
  1. never breaks the agent (API down -> counted, not raised)
  2. local mirror survives an outage with a full trace
  3. secrets are redacted before they go on the wire
  4. posts are idempotent (client-side event_id, retries safe)

Plus an end-to-end test: run an agent against HttpStore pointed at the
real FastAPI app, then read the run back through the API.
"""

import json
import os
import tempfile

import httpx
import pytest
from fastapi.testclient import TestClient

from afrec import Recorder
from afrec.http_store import HttpStore
from afrec.models import Event, RunMeta
from afrec.recorder import SqliteStore

from api.main import app
import api.main as _m

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean():
    # Fresh DB per test, same pattern as test_api.py.
    from afrec.recorder import SqliteStore as S
    tmp = tempfile.mkdtemp()
    _m.store = S(os.path.join(tmp, "t.sqlite3"))
    yield


class CaptureTransport(httpx.BaseTransport):
    """Records every request the store makes; returns canned responses."""

    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

    def handle_request(self, request):
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("simulated outage")
        return httpx.Response(200, json={"ok": True, "id": "x"})


def make_store(transport):
    return HttpStore("https://api.example", transport=transport, retries=2, retry_backoff=0.01)


def test_posts_run_then_events_in_order_and_redacts():
    t = CaptureTransport()
    s = make_store(t)

    meta = RunMeta(run_id="run_x", task="do thing")
    s.save_run(meta)
    s.save_event(Event(run_id="run_x", kind="llm.response",
                       data={"content": "hi", "api_key": "sk-supersecret"}))
    assert s.flush(timeout=5) is True
    s.close()

    assert [r.url.path for r in t.requests] == ["/runs", "/events"]
    run_payload = json.loads(t.requests[0].content)
    assert run_payload["run_id"] == "run_x" and run_payload["task"] == "do thing"

    ev_payload = json.loads(t.requests[1].content)
    assert ev_payload["kind"] == "llm.response"
    # Rule 3: the secret never leaves the process.
    assert ev_payload["data"]["api_key"] == "***REDACTED***"
    assert ev_payload["data"]["content"] == "hi"
    assert ev_payload["event_id"]  # client-side id -> idempotent retries


def test_api_down_never_raises_and_counts_failures():
    t = CaptureTransport(fail=True)
    s = make_store(t)

    s.save_run(RunMeta(run_id="run_y", task="t"))
    s.save_event(Event(run_id="run_y", kind="span.start"))
    assert s.flush(timeout=10) is True  # drained = posted or finally failed
    s.close()
    assert s.failed_posts == 2 and s.posted == 0


def test_mirror_keeps_a_full_local_trace_during_outage():
    tmp = tempfile.mkdtemp()
    mirror = SqliteStore(os.path.join(tmp, "mirror.sqlite3"))
    t = CaptureTransport(fail=True)
    s = HttpStore("https://api.example", mirror=mirror, transport=t,
                  retries=1, retry_backoff=0.01)

    s.save_run(RunMeta(run_id="run_z", task="survive outage"))
    s.save_event(Event(run_id="run_z", kind="llm.response",
                       data={"content": "hello"}, input_tokens=10, output_tokens=2))
    s.flush(timeout=5)
    s.close()

    runs = mirror.list_runs()
    assert len(runs) == 1 and runs[0]["run_id"] == "run_z"
    events = mirror.get_events("run_z")
    assert len(events) == 1 and events[0]["input_tokens"] == 10


def test_recorder_env_var_defaults_to_http_store(monkeypatch):
    monkeypatch.setenv("AFREC_API_URL", "https://afrec-api.onrender.com")
    rec = Recorder()
    assert isinstance(rec.store, HttpStore)
    assert rec.store.api_url == "https://afrec-api.onrender.com"
    assert isinstance(rec.store.mirror, SqliteStore)
    rec.store.close()


def test_end_to_end_real_agent_run_reaches_api():
    """A real agent loop (LLM + tool), recorded into HttpStore pointing at
    the real FastAPI app. The run must be readable through the API after."""
    def asgi_bridge(request: httpx.Request) -> httpx.Response:
        r = client.post(request.url.path, json=json.loads(request.content))
        return httpx.Response(r.status_code, json=r.json())

    s = HttpStore("https://api.example", transport=httpx.MockTransport(asgi_bridge))
    rec = Recorder(store=s)

    with rec.run("compare two energy sources") as run:
        with run.llm_call("gemini-2.0-flash", messages=[{"role": "user", "content": "compare"}]) as span:
            # Real provider call would go here; result + real usage below.
            span.result("solar is cheaper in 2025",
                        input_tokens=1500, output_tokens=40,
                        cached_tokens=0)
        with run.tool_call("calculator", {"expression": "120 - 80"}) as tool:
            tool.result({"value": 40})

    assert s.flush(timeout=10) is True
    s.close(drain_timeout=0)
    assert s.failed_posts == 0

    runs = client.get("/runs").json()
    assert any(r["task"] == "compare two energy sources" for r in runs)

    run_id = run.run_id
    detail = client.get(f"/runs/{run_id}").json()
    kinds = [e["kind"] for e in detail["events"]]
    assert "run.start" in kinds and "llm.response" in kinds and "tool.result" in kinds

    # Tokens must survive the round trip for the dashboard to display them.
    llm_resp = next(e for e in detail["events"] if e["kind"] == "llm.response")
    assert llm_resp["input_tokens"] == 1500 and llm_resp["output_tokens"] == 40


def test_run_exit_flushes_http_store():
    """A short-lived script (no explicit flush) must not lose its tail: the
    run context manager drains the queue on close."""
    import httpx
    from afrec import Recorder
    from afrec.http_store import HttpStore

    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    import tempfile, os
    mirror_path = os.path.join(tempfile.mkdtemp(), "m.sqlite3")
    from afrec.recorder import SqliteStore
    store = HttpStore("https://api.test", mirror=SqliteStore(mirror_path),
                      transport=httpx.MockTransport(handler))
    rec = Recorder(store=store)
    with rec.run("short-lived-script", tags={"t": 1}) as run:
        with run.tool_call("p", {"n": 1}) as t:
            t.result({"ok": True})
    # queue drained by the run exit itself, before process exit
    assert store._queue.unfinished_tasks == 0
    assert any(p.endswith("/runs") for p in posts), posts
    assert any(p.endswith("/events") for p in posts), posts
