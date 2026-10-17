"""Acceptance tests for POST /demo/seed: the seeded example runs.

The seeds are what a hiring reviewer sees first, so they are tested as hard
as anything real: seeding works, re-seeding never duplicates, every run
reconstructs through the API's own read paths, and every failing example
actually fires its intended rule through /investigate.
"""

import os
import tempfile

os.environ["AFREC_API_DB"] = os.path.join(tempfile.mkdtemp(), "demo_seed_test.sqlite3")

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

# findings from SINGLE-RUN rules only (no baseline passed)
EXPECTED_SINGLE_RUN_FINDINGS = {
    "demo-1-healthy": set(),
    "demo-2-crash": {"run_status_crashed", "error_event", "unclosed_span"},
    "demo-3-tool-no-result": {"tool_without_result"},
    "demo-4-loop": {"repeated_decision"},
    "demo-5-baseline-good": set(),
    "demo-6-baseline-bad": set(),  # its failure only shows against its baseline
    "demo-7-token-gap": {"token_capture_gap"},
    "demo-8-conflicting": {"conflicting_records"},
    "demo-9-stale-value": {"stale_consumption", "conflicting_records"},
    "demo-10-empty-search": {"empty_search_conclusion"},
}

DEMO_RUN_COUNT = len(EXPECTED_SINGLE_RUN_FINDINGS)


def _seed():
    r = client.post("/demo/seed")
    assert r.status_code == 200, r.text
    return r.json()


def test_seed_creates_all_demo_runs():
    body = _seed()
    assert body["ok"] is True
    assert len(body["seeded"]) == DEMO_RUN_COUNT
    runs = client.get("/runs").json()
    ids = {r["run_id"] for r in runs}
    assert set(body["seeded"]) <= ids


def test_reseed_is_idempotent():
    _seed()
    _seed()  # again
    runs = {r["run_id"]: r for r in client.get("/runs").json()}
    seeded_ids = [rid for rid in runs if rid.startswith("demo-")]
    assert len(seeded_ids) == DEMO_RUN_COUNT, "re-seeding duplicated runs"
    # events must not duplicate either
    for rid in EXPECTED_SINGLE_RUN_FINDINGS:
        detail = client.get(f"/runs/{rid}").json()
        event_ids = [e["event_id"] for e in detail["events"]]
        assert len(event_ids) == len(set(event_ids)), f"duplicate events in {rid}"


def test_every_example_fires_exactly_its_rule():
    _seed()
    for rid, want in EXPECTED_SINGLE_RUN_FINDINGS.items():
        r = client.get(f"/runs/{rid}/investigate").json()
        got = {f["rule"] for f in r["findings"]}
        assert got == want, f"{rid}: got {got}, want {want}"
        for f in r["findings"]:
            if want:  # failing runs must back every finding with evidence
                assert f.get("evidence"), f"{rid}: finding {f['rule']} has no evidence"
                assert f.get("hypothesis"), f"{rid}: finding {f['rule']} has no hypothesis"
    # and the divergence pair works through the API's baseline parameter
    r = client.get(
        "/runs/demo-6-baseline-bad/investigate",
        params={"baseline_run_id": "demo-5-baseline-good"},
    ).json()
    assert "baseline_divergence" in {f["rule"] for f in r["findings"]}


def test_seeded_events_survive_the_full_read_path():
    """The dashboard's read path (get run -> profile) must reconstruct seeds
    exactly like real captured runs."""
    _seed()
    detail = client.get("/runs/demo-4-loop").json()
    events = detail["events"]
    assert any(e["kind"] == "llm.response" for e in events)
    profile = client.get("/runs/demo-4-loop/profile").json()
    assert profile["totals"]["llm_calls"] == 20
    assert profile["totals"]["input_tokens"] == 20 * 470


def test_demo_durations_are_realistic_not_flat():
    """The timeline renders duration bars: every seeded span must have a
    non-trivial t_offset spread (the old seeds all sat at 0.0 ms)."""
    _seed()
    for rid in EXPECTED_SINGLE_RUN_FINDINGS:
        events = client.get(f"/runs/{rid}").json()["events"]
        offsets = [e["t_offset_ms"] for e in events]
        assert max(offsets) > 100, f"{rid}: flat timeline, max offset {max(offsets)} ms"
        if rid != "demo-2-crash":  # the crash run never closes by design
            starts = [e for e in events if e["kind"] == "span.start"]
            ends = {e["span_id"]: e for e in events if e["kind"] == "span.end"}
            for s in starts:
                end = ends.get(s["span_id"])
                if end is not None:
                    assert end["t_offset_ms"] > s["t_offset_ms"], (
                        f"{rid}: span {s['span_id']} ends before it starts")


def test_no_placeholder_email_typo_in_seeds():
    """Reviewer bait: the email.verify demo must not carry a double-@ typo."""
    _seed()
    events = client.get("/runs/demo-3-tool-no-result").json()["events"]
    for e in events:
        blob = str(e["data"])
        assert "@@" not in blob, "double-@ typo is back in the seeds"


def test_seeded_runs_can_be_profiled_in_dollars():
    """gemini-2.0-flash is priced, so every demo's cost estimate is a real
    dollar figure — except demo-7, whose whole point is the unknown-usage gap."""
    _seed()
    for rid in EXPECTED_SINGLE_RUN_FINDINGS:
        totals = client.get(f"/runs/{rid}/profile").json()["totals"]
        if rid == "demo-7-token-gap":
            assert totals["unknown_cost_calls"] == 1
            assert totals["estimated_cost_usd"] > 0  # the priced call still counts
        else:
            assert totals["estimated_cost_usd"] > 0, f"{rid}: cost estimate came back $0"
            assert totals["unknown_cost_calls"] == 0
