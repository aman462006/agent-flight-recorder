"""Seed a remote ingestion API with the demo agent's good and bad runs.

Used after deploying (DEPLOY.md step 3) so the dashboard and its
Investigation panel have the stale-figure scenario to show: a good run,
and a bad run that looks 'completed' to every single-run rule.

Usage:
    python -m demo_agent.seed_remote --api https://afrec-api.onrender.com

Runs the demo agent twice against a local SQLite store, then replays the
recorded runs through the public POST /runs + POST /events endpoints
(the same path a remote agent SDK would take).
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx

from afrec.recorder import Recorder, SqliteStore
from demo_agent.agent import run_agent


def seed(api: str) -> int:
    api = api.rstrip("/")
    local_path = "seed_afrec.sqlite3"
    if os.path.exists(local_path):
        os.remove(local_path)

    import afrec.recorder as rec_mod
    import demo_agent.agent as agent_mod
    rec = Recorder(SqliteStore(local_path))
    rec_mod.get_recorder = lambda: rec  # type: ignore[assignment]
    agent_mod.get_recorder = lambda: rec  # type: ignore[assignment]

    run_agent("Compare three solar companies", use_stale_figure=False)  # good
    run_agent("Compare three solar companies", use_stale_figure=True)   # bad

    posted = 0
    with httpx.Client(timeout=30) as client:
        for meta in rec.store.list_runs():
            r = client.post(f"{api}/runs", json={
                "run_id": meta["run_id"], "task": meta["task"],
                "started_at": meta["started_at"], "ended_at": meta["ended_at"],
                "status": meta["status"], "tags": meta["tags"],
            })
            r.raise_for_status()
            for ev in rec.store.get_events(meta["run_id"]):
                ev = {"run_id": meta["run_id"], **ev}  # store projection omits run_id
                e = client.post(f"{api}/events", json=ev)
                e.raise_for_status()
                posted += 1
            print(f"seeded {meta['status']:>9} run {meta['run_id']} "
                  f"({meta['task'][:40]}…)")
    print(f"done: {posted} events posted to {api}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True, help="e.g. https://afrec-api.onrender.com")
    sys.exit(seed(parser.parse_args().api))
