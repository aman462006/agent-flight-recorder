"""Phase 2 acceptance tests: ingestion API behavior.

Covers the four design rules: idempotency, server-side redaction,
rejection without corruption, and full-trace reconstruction.
"""

import os
import tempfile

os.environ["AFREC_API_DB"] = os.path.join(tempfile.mkdtemp(), "api_test.sqlite3")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
import api.main as _m

client = TestClient(app)


def _mk_run(run_id="run_t1", task="test task", status="running"):
    return {
        "run_id": run_id,
        "task": task,
        "started_at": 1760000000.0,
        "status": status,
        "tags": {"agent": "tests"},
    }


def _mk_event(run_id="run_t1", event_id=None, kind="span.start", data=None):
    return {
        "run_id": run_id,
        "kind": kind,
        "span_id": "span_1",
        "timestamp": 1760000000.5,
        "t_offset_ms": 0.5,
        "data": data or {"label": "llm:mock"},
        "event_id": event_id,
    }


@pytest.fixture(autouse=True)
def clean():
    # Fresh DB per test: point the module-level store at a new file.
    import api.main as m
    from afrec.recorder import SqliteStore
    tmp = tempfile.mkdtemp()
    m.store = SqliteStore(os.path.join(tmp, "t.sqlite3"))
    yield


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_run_lifecycle_and_idempotency():
    r1 = client.post("/runs", json=_mk_run())
    assert r1.status_code == 200
    # same run_id re-posted with final status → update, not duplicate
    r2 = client.post("/runs", json=_mk_run(status="completed"))
    assert r2.status_code == 200
    runs = client.get("/runs").json()
    assert len(runs) == 1 and runs[0]["status"] == "completed"


def test_event_idempotency_no_double_count():
    client.post("/runs", json=_mk_run(run_id="run_dedup"))
    ev = _mk_event(run_id="run_dedup", event_id="evt_001", kind="llm.response",
                   data={"payload": "answer", "input_tokens": 10, "output_tokens": 5})
    a = client.post("/events", json=ev)
    b = client.post("/events", json=ev)  # retry after network blip
    assert a.status_code == 200 and b.status_code == 200
    events = client.get("/runs/run_dedup").json()["events"]
    assert len(events) == 1  # de-duplicated, not counted twice


def test_event_for_unknown_run_rejected():
    r = client.post("/events", json=_mk_event(run_id="run_ghost"))
    assert r.status_code == 404
    # and nothing was persisted
    assert client.get("/runs").json() == [] or all(
        x["run_id"] != "run_ghost" for x in client.get("/runs").json())


def test_server_side_redaction():
    client.post("/runs", json=_mk_run(run_id="run_redact"))
    r = client.post("/events", json=_mk_event(
        run_id="run_redact", kind="llm.response",
        data={"payload": "use api_key=supersecret123 please", "api_key": "sk-live-abcdefgh12345678"}))
    assert r.status_code == 200
    stored = client.get("/runs/run_redact").json()["events"][0]["data"]
    assert stored["api_key"] == "***REDACTED***"
    assert "supersecret123" not in stored["payload"]


def test_full_trace_reconstruction_over_http():
    client.post("/runs", json=_mk_run(run_id="run_trace"))
    for i, kind in enumerate(["run.start", "span.start", "llm.response", "span.end", "run.end"]):
        payload = _mk_event(run_id="run_trace", kind=kind, event_id=f"evt_{i}")
        payload["timestamp"] = 1760000000.0 + i
        client.post("/events", json=payload)
    got = client.get("/runs/run_trace").json()
    kinds = [e["kind"] for e in got["events"]]
    assert kinds == ["run.start", "span.start", "llm.response", "span.end", "run.end"]


def test_unknown_event_kind_preserved():
    client.post("/runs", json=_mk_run(run_id="run_fwd"))
    client.post("/events", json=_mk_event(run_id="run_fwd", kind="langgraph.node"))
    stored = client.get("/runs/run_fwd").json()["events"]
    assert any(e["kind"] == "langgraph.node" for e in stored)


def test_invalid_payload_rejected_before_store():
    r = client.post("/events", json={"run_id": "x"})  # missing required fields
    assert r.status_code == 422


def test_profile_endpoint_end_to_end():
    client.post("/runs", json=_mk_run(run_id="run_prof"))
    for i in range(2):
        client.post("/events", json={
            "run_id": "run_prof", "kind": "llm.response", "span_id": "s1",
            "timestamp": 1760000000.0 + i, "t_offset_ms": 10 + i,
            "data": {}, "model": "gpt-4o-mini",
            "input_tokens": 1000, "output_tokens": 200,
        })
    r = client.get("/runs/run_prof/profile")
    assert r.status_code == 200
    p = r.json()
    assert p["totals"]["llm_calls"] == 2
    assert p["totals"]["input_tokens"] == 2000
    assert p["totals"]["estimated_cost_usd"] > 0
