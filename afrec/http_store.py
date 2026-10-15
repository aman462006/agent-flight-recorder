"""Remote event store: stream runs and events to an afrec ingestion API.

Drop-in replacement for SqliteStore — same save_run/save_event interface.
Made for the real use case: instrumenting an agent that talks to a live
model provider (Gemini, OpenAI, …) and watching the run appear on the
deployed dashboard as it happens.

Design rules, same honesty as the rest of afrec:

  1. Instrumentation must never break the agent. Posts happen on a
     background worker thread; network failures are counted and warned
     about, never raised into the agent loop. (Run._emit already guards
     its own call, but save_run on __enter__ is not wrapped — so this
     class raises from neither.)
  2. An outage never loses a trace. An optional local SqliteStore mirror
     writes every event synchronously before it is queued for the
     network; if the API is unreachable you still hold a complete local
     flight recording.
  3. Nothing secret leaves the process. Event data is redacted with the
     same redact() the SQLite path uses, BEFORE it is put on the wire.
     The API redacts again on ingest (defence in depth), but the client
     does not rely on that.
  4. Retries are idempotent. event_ids are generated client-side, so a
     retried POST cannot duplicate an event server-side (the API
     de-duplicates on event_id; runs on run_id).
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from .models import Event, RunMeta, new_id
from .recorder import SqliteStore, redact


class HttpStore:
    """Streams runs/events to an afrec ingestion API over HTTP.

    Usage:
        store = HttpStore("https://afrec-api.onrender.com")
        rec = Recorder(store=store)
        ... run your agent ...
        store.flush()          # optional: block until everything is posted
        store.close()

    Or just set AFREC_API_URL in the environment — get_recorder() then
    defaults to an HttpStore (with a local mirror at $AFREC_DB).
    """

    def __init__(
        self,
        api_url: str,
        *,
        mirror: Optional[SqliteStore] = None,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.5,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.mirror = mirror
        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff
        self.failed_posts = 0
        self.posted = 0
        self._transport = transport
        self._client: Optional[httpx.Client] = None
        self._queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stopping = False

    # -- SqliteStore-compatible write interface --------------------------------

    def save_run(self, run: RunMeta) -> None:
        """Queue run metadata for posting. Re-posting the same run_id is
        safe: the API treats POST /runs as an upsert (agents re-send run
        meta with the final status on completion)."""
        if self.mirror is not None:
            self._mirror(lambda s: s.save_run(run))
        self._enqueue({
            "run_id": run.run_id,
            "task": redact(run.task),
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "status": run.status,
            "tags": redact(run.tags),
        })

    def save_event(self, event: Event) -> None:
        """Queue one event for posting. Assigns a client-side event_id so
        network retries stay idempotent."""
        if event.event_id is None:
            event.event_id = new_id("evt")
        if self.mirror is not None:
            self._mirror(lambda s: s.save_event(event))
        self._enqueue({
            "run_id": event.run_id,
            "event_id": event.event_id,
            "kind": event.kind,
            "span_id": event.span_id,
            "parent_span_id": event.parent_span_id,
            "timestamp": event.timestamp,
            "t_offset_ms": event.t_offset_ms,
            # Redact BEFORE anything goes on the wire (rule 3).
            "data": redact(event.data),
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "cached_tokens": event.cached_tokens,
            "model": event.model,
        })

    # -- read interface (proxied GETs; used by replay/diff from remote data) ---

    def list_runs(self) -> List[dict]:
        return self._get_json("/runs")

    def get_events(self, run_id: str) -> List[dict]:
        return self._get_json(f"/runs/{run_id}")

    # -- lifecycle ---------------------------------------------------------------

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Block until every queued item has been posted (or finally failed).
        Returns True if the queue fully drained, False on timeout. Polling
        is deliberate: unfinished_tasks is the honest counter, and a plain
        sleep keeps this dependency-free and test-simple."""
        self._ensure_worker()
        deadline = None if timeout is None else time.time() + timeout
        while self._queue.unfinished_tasks > 0:
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def close(self, drain_timeout: Optional[float] = 30.0) -> bool:
        """Stop the worker after draining the queue (bounded wait)."""
        drained = self.flush(timeout=drain_timeout)
        self._stopping = True
        self._enqueue(None)  # sentinel: worker exits after processing it
        if self._worker is not None and self._worker.is_alive() and self._worker is not threading.current_thread():
            self._worker.join(timeout=5)
        if self._client is not None:
            self._client.close()
            self._client = None
        return drained

    def __enter__(self) -> "HttpStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    # -- internals ---------------------------------------------------------------

    def _mirror(self, op) -> None:
        """Mirror writes must not break the agent either (rule 1)."""
        try:
            op(self.mirror)
        except Exception as err:  # pragma: no cover - defensive path
            print(f"[afrec] local mirror write failed: {err}")

    def _enqueue(self, item: Optional[Dict[str, Any]]) -> None:
        self._ensure_worker()
        self._queue.put(item)

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            if self._stopping and self._worker is not None:
                # close() already ran; a late save after close reopens a worker.
                self._stopping = False
            self._worker = threading.Thread(target=self._work, name="afrec-http", daemon=True)
            self._worker.start()

    def _client_or_die(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, transport=self._transport)
        return self._client

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                path = "/runs" if "kind" not in item else "/events"
                self._post_with_retry(path, item)
            finally:
                self._queue.task_done()

    def _post_with_retry(self, path: str, payload: Dict[str, Any]) -> None:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                r = self._client_or_die().post(f"{self.api_url}{path}", json=payload)
                if 200 <= r.status_code < 300:
                    self.posted += 1
                    return
                # 4xx means the payload itself is wrong — retrying cannot fix it.
                if 400 <= r.status_code < 500:
                    self.failed_posts += 1
                    print(f"[afrec] API rejected POST {path}: {r.status_code} {r.text[:200]}")
                    return
                last_err = RuntimeError(f"HTTP {r.status_code}")
            except httpx.HTTPError as err:  # network error: worth retrying
                last_err = err
            if attempt < self.retries:
                time.sleep(self.retry_backoff * attempt)
        self.failed_posts += 1
        print(f"[afrec] gave up posting to {path} after {self.retries} attempts: {last_err}")

    def _get_json(self, path: str):
        r = self._client_or_die().get(f"{self.api_url}{path}")
        r.raise_for_status()
        return r.json()
