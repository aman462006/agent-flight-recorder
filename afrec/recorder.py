"""
Recorder — the instrumentation surface agents call.

Usage (the whole API is three entry points):

    from afrec import get_recorder

    rec = get_recorder()                      # SQLite-backed default
    with rec.run("Compare three solar companies") as run:
        with run.llm_call("gpt-4o-mini", messages) as llm:
            ...
        with run.tool_call("search", {"query": "..."}) as tool:
            result = do_search(...)
            tool.result(result)
    # events are persisted as they happen; a crash still leaves a partial run

Design rules:
- Recording must never break the agent: persistence errors are captured
  as events, not raised into the caller.
- Events are emitted in order with both wall-clock and monotonic offsets.
- Spans are context managers; nested spans parent automatically.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

from .models import (
    KIND_ERROR,
    KIND_LLM_REQUEST,
    KIND_LLM_RESPONSE,
    KIND_RUN_END,
    KIND_RUN_START,
    KIND_SPAN_START,
    KIND_SPAN_END,
    KIND_TOOL_REQUEST,
    KIND_TOOL_RESULT,
    Event,
    RunMeta,
    new_id,
)

# ---------------------------------------------------------------------------
# Redaction — recorded prompts/tool results may contain credentials or PII.
# Rule-based and conservative by default; extend REDACTIONS per deployment.
# ---------------------------------------------------------------------------

REDACTIONS: List[re.Pattern] = [
    re.compile(r"(sk-[A-Za-z0-9]{8})[A-Za-z0-9\-_]+"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-_.~+/]+=*"),
    re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9\-_]{8})[A-Za-z0-9\-_]+"),
    re.compile(r"(?i)(password['\"]?\s*[:=]\s*['\"]?)([^\s'\"]{4})[^\s'\"]+"),
]

def redact(value: Any) -> Any:
    if isinstance(value, str):
        out = value
        for pat in REDACTIONS:
            out = pat.sub(lambda m: m.group(1) + "***REDACTED***" if len(m.groups()) == 1 else m.group(1) + "***REDACTED***", out)
        return out
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if k.lower() in {"api_key", "apikey", "authorization", "password", "secret"} else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class SqliteStore:
    """Append-only event store. SQLite now; the same SQL shape ports to Postgres."""

    DDL = """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        task TEXT,
        started_at REAL,
        ended_at REAL,
        status TEXT,
        tags TEXT
    );
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        span_id TEXT,
        parent_span_id TEXT,
        timestamp REAL NOT NULL,
        t_offset_ms REAL NOT NULL,
        data TEXT NOT NULL,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cached_tokens INTEGER,
        model TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, timestamp);
    """

    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.executescript(self.DDL)
        return self._conn

    def save_run(self, run: RunMeta) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                (run.run_id, run.task, run.started_at, run.ended_at, run.status, json.dumps(run.tags)),
            )

    def save_event(self, event: Event) -> None:
        if event.event_id is None:
            event.event_id = new_id("evt")
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event.event_id, event.run_id, event.kind, event.span_id,
                    event.parent_span_id, event.timestamp, event.t_offset_ms,
                    json.dumps(redact(event.data)), event.input_tokens,
                    event.output_tokens, event.cached_tokens, event.model,
                ),
            )

    def list_runs(self) -> List[dict]:
        cur = self.conn.execute("SELECT run_id, task, started_at, ended_at, status, tags FROM runs ORDER BY started_at DESC")
        return [
            {"run_id": r[0], "task": r[1], "started_at": r[2], "ended_at": r[3], "status": r[4], "tags": json.loads(r[5] or "{}")}
            for r in cur.fetchall()
        ]

    def get_events(self, run_id: str) -> List[dict]:
        cur = self.conn.execute(
            "SELECT event_id, kind, span_id, parent_span_id, timestamp, t_offset_ms, data, "
            "input_tokens, output_tokens, cached_tokens, model "
            "FROM events WHERE run_id = ? ORDER BY timestamp, rowid",
            (run_id,),
        )
        out = []
        for r in cur.fetchall():
            d = {
                "event_id": r[0], "kind": r[1], "span_id": r[2], "parent_span_id": r[3],
                "timestamp": r[4], "t_offset_ms": r[5], "data": json.loads(r[6]),
                "input_tokens": r[7], "output_tokens": r[8], "cached_tokens": r[9], "model": r[10],
            }
            out.append(d)
        return out


class _Span:
    """Context-managed operation inside a run (llm call, tool call, custom)."""

    def __init__(self, run: "Run", kind: str, label: str, parent: Optional["_Span"], data: Dict[str, Any]):
        self.run = run
        self.kind = kind
        self.label = label
        self.parent = parent
        self.data = data
        self.span_id = new_id("span")
        self._t0 = time.time()

    def __enter__(self) -> "_Span":
        self.run._stack.append(self)
        self.run._emit(Event(
            run_id=self.run.run_id, kind=KIND_SPAN_START,
            span_id=self.span_id, parent_span_id=self.parent.span_id if self.parent else None,
            data={"label": self.label, **self.data},
        ))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None:
            self.run._emit(Event(
                run_id=self.run.run_id, kind=KIND_ERROR, span_id=self.span_id,
                parent_span_id=self.parent.span_id if self.parent else None,
                data={"error_type": type(exc).__name__, "message": str(exc)[:2000]},
            ))
            self.run.recorder.crashed_runs.add(self.run.run_id)
        if self.run._stack and self.run._stack[-1] is self:
            self.run._stack.pop()
        self.run._emit(Event(
            run_id=self.run.run_id, kind=KIND_SPAN_END, span_id=self.span_id,
            parent_span_id=self.parent.span_id if self.parent else None,
            data={"label": self.label, "duration_ms": round((time.time() - self._t0) * 1000, 2)},
        ))
        return False  # never swallow agent exceptions

    def result(self, payload: Any, input_tokens: Optional[int] = None,
               output_tokens: Optional[int] = None, cached_tokens: Optional[int] = None,
               model: Optional[str] = None, kind: Optional[str] = None) -> None:
        """Record the output of this span (tool result or LLM response)."""
        emit_kind = kind or (KIND_LLM_RESPONSE if "llm" in self.kind else KIND_TOOL_RESULT)
        self.run._emit(Event(
            run_id=self.run.run_id, kind=emit_kind, span_id=self.span_id,
            parent_span_id=self.span_id,
            data={"payload": payload},
            input_tokens=input_tokens, output_tokens=output_tokens,
            cached_tokens=cached_tokens, model=model,
        ))


class Run:
    """One recorded agent execution. Created via recorder.run(...)."""

    def __init__(self, recorder: "Recorder", run_id: str, task: str, tags: Optional[Dict[str, Any]] = None):
        self.recorder = recorder
        self.run_id = run_id
        self.task = task
        self.tags = tags or {}
        self.meta = RunMeta(run_id=run_id, task=task, tags=self.tags)
        self._t0 = time.time()
        self._stack: List[_Span] = []

    def __enter__(self) -> "Run":
        self.recorder.store.save_run(self.meta)
        self._emit(Event(run_id=self.run_id, kind=KIND_RUN_START, data={"task": self.task, "tags": self.tags}))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.meta.ended_at = time.time()
        self.meta.status = "crashed" if self.run_id in self.recorder.crashed_runs else "completed"
        if exc is not None and self.meta.status != "crashed":
            self.meta.status = "crashed"
        self.recorder.store.save_run(self.meta)
        self._emit(Event(run_id=self.run_id, kind=KIND_RUN_END, data={"status": self.meta.status}))
        return False

    # -- span constructors -------------------------------------------------
    def span(self, label: str, **data) -> _Span:
        return _Span(self, "span", label, self._stack[-1] if self._stack else None, data)

    def llm_call(self, model: str, messages: List[Dict[str, str]]) -> _Span:
        return _Span(self, "llm", f"llm:{model}", self._stack[-1] if self._stack else None,
                     {"model": model, "messages": messages})

    def tool_call(self, name: str, arguments: Dict[str, Any]) -> _Span:
        return _Span(self, "tool", f"tool:{name}", self._stack[-1] if self._stack else None,
                     {"tool": name, "arguments": arguments})

    # -- internals ----------------------------------------------------------
    def _emit(self, event: Event) -> None:
        event.t_offset_ms = round((time.time() - self._t0) * 1000, 2)
        # Instrumentation must never break the agent: capture store failures.
        try:
            self.recorder.store.save_event(event)
        except Exception as store_err:  # pragma: no cover - defensive path
            self.recorder.failed_writes += 1
            print(f"[afrec] event persistence failed: {store_err}")


class Recorder:
    def __init__(self, store: Optional[SqliteStore] = None):
        env_path = os.environ.get("AFREC_DB", "afrec.sqlite3")
        self.store = store or SqliteStore(env_path)
        self.crashed_runs: set = set()
        self.failed_writes = 0

    def run(self, task: str, tags: Optional[Dict[str, Any]] = None) -> Run:
        return Run(self, new_id("run"), task, tags)


_default: Optional[Recorder] = None

def get_recorder() -> Recorder:
    global _default
    if _default is None:
        _default = Recorder()
    return _default
