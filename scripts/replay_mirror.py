"""Replay a local sqlite mirror into an AFREC ingestion API.

The HttpStore keeps a local SqliteStore mirror of everything it streams
(outage-proof). If the API was down - or posts were lost - this script
re-posts the mirror's runs and events. Idempotent end to end: run ids
upsert, event ids deduplicate, so replaying twice never duplicates.

Usage (from the repo root, with the mirror file at hand):

    py -3.12 scripts\replay_mirror.py --db C:\path\to\afrec.sqlite3
    py -3.12 scripts\replay_mirror.py --db afrec.sqlite3 --api https://afrec-api.onrender.com
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to the sqlite mirror file")
    parser.add_argument("--api", default="https://afrec-api.onrender.com")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row
    runs = db.execute("SELECT * FROM runs ORDER BY started_at ASC").fetchall()
    print(f"mirror: {len(runs)} runs")
    for run in runs:
        r = dict(run)
        r["tags"] = json.loads(r.get("tags") or "{}")
        _post(f"{api}/runs", r)
        events = db.execute("SELECT * FROM events WHERE run_id = ? ORDER BY timestamp ASC", (r["run_id"],)).fetchall()
        for ev in events:
            e = dict(ev)
            e["data"] = json.loads(e.get("data") or "{}")
            _post(f"{api}/events", e)
        print(f"  replayed {r['run_id']} ({len(events)} events) - {r.get('status')}")
    print(f"\nDone: {len(runs)} runs replayed to {api}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"API error {e.code}: {e.read().decode(errors='replace')[:300]}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as e:
        print(f"mirror read error: {e}", file=sys.stderr)
        sys.exit(1)
