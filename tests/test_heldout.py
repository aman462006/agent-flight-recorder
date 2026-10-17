"""The held-out adversarial evaluation is part of the suite: CI runs it,
and the OUTCOMES are pinned. A rule change that alters held-out behaviour
flips a pinned outcome and fails this test — forcing a conscious decision
and an updated README, never a silent drift.

These outcomes are the measured values from 2026-10-17. They are not
targets: f08/f10/f11 are KNOWN MISSES documented in the README's
known-gaps list, and s07 is a KNOWN false positive of the
repeated_decision rule. They are pinned so that (a) improvements show up
as deliberate test updates, and (b) regressions (e.g. stale_consumption
losing f09) cannot land quietly.
"""

from bench.run_heldout import run_heldout, SCENARIOS

EXPECTED_OUTCOMES = {
    # clean near-misses: silence is correct
    "s01_retry_ladder_then_success": "TN",
    "s02_legit_polling_different_args": "TN",
    "s03_batch_lookup_distinct_ids": "TN",
    "s04_legit_empty_search_with_other_evidence": "TN",
    "s05_old_and_new_agent_uses_new": "TN",
    "s06_deliberation_cites_both_records": "TN",
    # known false positive: same-args polling where results change
    "s07_polling_same_args_waiting_for_completion": "FP",
    # subtle failures: f09 is caught; f08/f10/f11 are known misses
    "f08_loop_interleaved_with_thinking": "MISS",
    "f09_stale_consumed_via_calculator": "TP",
    "f10_wrong_record_consumed": "MISS",
    "f11_evidence_ignored_unavailability_claim": "MISS",
}


def test_heldout_outcomes_are_pinned():
    result = run_heldout(verbose=False)
    got = {r["id"]: r["outcome"] for r in result["rows"]}
    assert got == EXPECTED_OUTCOMES, (
        f"held-out outcomes drifted: {got}. Update the pin AND the README "
        "known-gaps list in the same commit, or fix your regression."
    )


def test_heldout_scenario_count():
    assert len(SCENARIOS) == 11
    assert sum(1 for s in SCENARIOS if s.__name__.startswith("s")) == 7
    assert sum(1 for s in SCENARIOS if s.__name__.startswith("f")) == 4


def test_heldout_summary_is_honest():
    """The README quotes these numbers; they must not flatter the rules."""
    result = run_heldout(verbose=False)
    s = result["summary"]
    assert s["clean_near_misses"] == 7
    assert s["subtle_failures"] == 4
    # if these improve, update the pin above and the README numbers together
    assert s["failures_missed"] >= 1, "held-out recall must not be claimed as perfect"
