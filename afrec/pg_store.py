"""Postgres store for the ingestion API.

Same interface and SQL shape as SqliteStore (afrec/recorder.py): the API
picks this automatically when DATABASE_URL is set, so traces survive the
ephemeral filesystems of free-tier deploys (Render sleeps an instance and
wipes /tmp; Vercel serverless never shares a disk).

Idempotency is preserved:
- runs   INSERT ... ON CONFLICT (run_id) DO UPDATE   (matches INSERT OR REPLACE)
- events INSERT ... ON CONFLICT (event_id) DO NOTHING (matches INSERT OR IGNORE)

Connection notes:
- psycopg 3, thread-local connections (FastAPI serves sync endpoints from a
  threadpool; a single psycopg connection is not thread-safe).
- Serverless-friendly: connections are opened lazily on first use, never at
  import time.
- Errors are wrapped in StoreError so the API layer can map any store failure
  to a 503 without importing psycopg at the endpoint level.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

import psycopg

from .models import Event, RunMeta, new_id
from .recorder import redact


class StoreError(Exception):
    """Any failure talking to the Postgres store (connection, constraint, SQL)."""


DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    task       TEXT,
    started_at DOUBLE PRECISION,
    ended_at   DOUBLE PRECISION,
    status     TEXT,
    tags       TEXT
);
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    span_id         TEXT,
    parent_span_id  TEXT,
    timestamp       DOUBLE PRECISION NOT NULL,
    t_offset_ms     DOUBLE PRECISION NOT NULL,
    data            TEXT NOT NULL,
    input_tokens    BIGINT,
    output_tokens   BIGINT,
    cached_tokens   BIGINT,
    model           TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, timestamp);
"""


class PostgresStore:
    """Append-only event store backed by Postgres. Drop-in for SqliteStore."""

    def __init__(self, conninfo: str):
        if not conninfo:
            raise StoreError("PostgresStore requires a connection string (DATABASE_URL)")
        self.conninfo = conninfo
        self._local = threading.local()

    @property
    def conn(self) -> psycopg.Connection:
        # Thread-local: each FastAPI worker thread gets its own connection.
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed:
            try:
                # Row-factory off: we index positionally like the SQLite path.
                conn = psycopg.connect(self.conninfo, autocommit=True)
                conn.execute(DDL)
            except psycopg.Error as e:
                raise StoreError(f"postgres connect/init failed: {e}") from e
            self._local.conn = conn
        return conn

    def save_run(self, run: RunMeta) -> None:
        try:
            self.conn.execute(
                """
                INSERT INTO runs (run_id, task, started_at, ended_at, status, tags)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    task = EXCLUDED.task,
                    started_at = EXCLUDED.started_at,
                    ended_at = EXCLUDED.ended_at,
                    status = EXCLUDED.status,
                    tags = EXCLUDED.tags
                """,
                (run.run_id, run.task, run.started_at, run.ended_at,
                 run.status, json.dumps(run.tags)),
            )
        except psycopg.Error as e:
            raise StoreError(f"save_run failed: {e}") from e

    def save_event(self, event: Event) -> None:
        if event.event_id is None:
            event.event_id = new_id("evt")
        try:
            self.conn.execute(
                """
                INSERT INTO events (event_id, run_id, kind, span_id, parent_span_id,
                                    timestamp, t_offset_ms, data,
                                    input_tokens, output_tokens, cached_tokens, model)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event.event_id, event.run_id, event.kind, event.span_id,
                    event.parent_span_id, event.timestamp, event.t_offset_ms,
                    json.dumps(redact(event.data)),
                    event.input_tokens, event.output_tokens,
                    event.cached_tokens, event.model,
                ),
            )
        except psycopg.Error as e:
            raise StoreError(f"save_event failed: {e}") from e

    def list_runs(self) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.execute(
                "SELECT run_id, task, started_at, ended_at, status, tags "
                "FROM runs ORDER BY started_at DESC"
            )
            return [
                {
                    "run_id": r[0], "task": r[1], "started_at": r[2],
                    "ended_at": r[3], "status": r[4],
                    "tags": json.loads(r[5] or "{}"),
                }
                for r in cur.fetchall()
            ]
        except psycopg.Error as e:
            raise StoreError(f"list_runs failed: {e}") from e

    def get_events(self, run_id: str) -> List[Dict[str, Any]]:
        # Event ids embed a millisecond timestamp prefix, so (timestamp, event_id)
        # reproduces the SQLite (timestamp, rowid) insertion order deterministically.
        try:
            cur = self.conn.execute(
                "SELECT event_id, kind, span_id, parent_span_id, timestamp, t_offset_ms, data, "
                "input_tokens, output_tokens, cached_tokens, model "
                "FROM events WHERE run_id = %s ORDER BY timestamp, event_id",
                (run_id,),
            )
            out: List[Dict[str, Any]] = []
            for r in cur.fetchall():
                out.append({
                    "event_id": r[0], "kind": r[1], "span_id": r[2],
                    "parent_span_id": r[3], "timestamp": r[4], "t_offset_ms": r[5],
                    "data": json.loads(r[6]),
                    "input_tokens": r[7], "output_tokens": r[8],
                    "cached_tokens": r[9], "model": r[10],
                })
            return out
        except psycopg.Error as e:
            raise StoreError(f"get_events failed: {e}") from e

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            conn.close()
