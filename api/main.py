"""
Phase 2 — Trace Ingestion API (FastAPI).

The SDK persists locally; this API is the network path for remote agents,
the dashboard's read side, and (later) cross-service tracing. Endpoints:

    POST /runs                 create/refresh run metadata (idempotent by run_id)
    POST /events               ingest one event (idempotent by event_id)
    GET  /runs                list runs, newest first
    GET  /runs/{run_id}       run metadata + full ordered event trace
    GET  /health              liveness + basic store stats

Design rules (same DNA as the SDK):

- Ingestion is idempotent: agents may retry submissions on flaky networks;
  duplicate event_ids are accepted and silently de-duplicated (INSERT OR
  IGNORE), never counted twice.
- Redaction runs server-side too: even a client that forgot to redact
  gets its events sanitized before persistence. Defense in depth.
- Ingestion failure must not corrupt traces: bad payloads are rejected
  with 422 before touching the store; store errors return 503, and the
  client's own events remain intact for retry.
- Unknown event kinds are preserved, not rejected (forward compatibility).
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from afrec.recorder import SqliteStore, redact

DB_PATH = os.environ.get("AFREC_API_DB", "afrec_api.sqlite3")

app = FastAPI(title="Agent Flight Recorder — Ingestion API", version="0.2.0")

# The dashboard (Phase 3) runs on a different origin in dev; allow all
# origins for now — traces contain no secrets (redacted at ingest), and
# a real deployment scopes this per environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = SqliteStore(DB_PATH)


# ---------------------------------------------------------------------------
# Request models — strict on structure, lenient on content.
# ---------------------------------------------------------------------------

class RunIn(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    task: str = Field(max_length=4096)
    started_at: float
    ended_at: Optional[float] = None
    status: str = "running"
    tags: Dict[str, Any] = Field(default_factory=dict)


class EventIn(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    timestamp: float
    t_offset_ms: float = 0.0
    data: Dict[str, Any] = Field(default_factory=dict)
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    model: Optional[str] = None
    event_id: Optional[str] = None  # client-supplied → idempotency key


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> Dict[str, Any]:
    try:
        runs = store.list_runs()
        return {"ok": True, "runs": len(runs), "db": DB_PATH}
    except sqlite3.Error as e:
        raise HTTPException(status_code=503, detail=f"store unavailable: {e}")


@app.post("/runs")
def create_run(run: RunIn) -> Dict[str, Any]:
    # Idempotent: re-posting the same run_id updates metadata (agent may
    # re-send run meta with the final status after completion).
    from afrec.models import RunMeta
    meta = RunMeta(
        run_id=run.run_id, task=redact(run.task), started_at=run.started_at,
        ended_at=run.ended_at, status=run.status, tags=redact(run.tags),
    )
    try:
        store.save_run(meta)
    except sqlite3.Error as e:
        raise HTTPException(status_code=503, detail=f"store unavailable: {e}")
    return {"ok": True, "run_id": run.run_id}


@app.post("/events")
def ingest_event(ev: EventIn) -> Dict[str, Any]:
    # The referenced run should exist; if not, reject — events floating
    # without a run are unplayable and hide bugs in the client.
    known = {r["run_id"] for r in store.list_runs()}
    if ev.run_id not in known:
        raise HTTPException(status_code=404, detail=f"unknown run_id {ev.run_id}")

    from afrec.models import Event
    event = Event(
        run_id=ev.run_id, kind=ev.kind, span_id=ev.span_id,
        parent_span_id=ev.parent_span_id, timestamp=ev.timestamp,
        t_offset_ms=ev.t_offset_ms, data=redact(ev.data),
        input_tokens=ev.input_tokens, output_tokens=ev.output_tokens,
        cached_tokens=ev.cached_tokens, model=ev.model, event_id=ev.event_id,
    )
    try:
        store.save_event(event)
    except sqlite3.Error as e:
        raise HTTPException(status_code=503, detail=f"store unavailable: {e}")
    return {"ok": True, "event_id": event.event_id}


@app.get("/runs")
def list_runs() -> List[Dict[str, Any]]:
    return store.list_runs()


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    runs = {r["run_id"]: r for r in store.list_runs()}
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": runs[run_id], "events": store.get_events(run_id)}
