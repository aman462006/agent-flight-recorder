"""Phase 6 acceptance tests: the failure investigator.

Every test asserts three things at once, because they are the product:
- the rule FIRES on the failing trace it exists to catch
- the finding CARRIES EVIDENCE (event pointers, verbatim excerpts)
- the HYPOTHESIS stays labelled as a hypothesis, never asserted as fact

And the negative space: a clean trace must be reported clean, with no
manufactured findings.
"""

import json

import afrec.recorder
import demo_agent.agent
from afrec import Recorder
from afrec.investigator import investigate_run, CRITICAL, WARNING, INFO
from afrec.recorder import SqliteStore
from demo_agent.agent import run_agent


# -- helpers -------------------------------------------------------------------

def _tool_span(sid, tool, arguments, t):
    return {"kind": "span.start", "span_id": sid, "t_offset_ms": t,
            "data": {"label": f"tool:{tool}", "arguments": arguments}}


def _tool_result(sid, payload, t):
    return {"kind": "tool.result", "span_id": sid, "t_offset_ms": t, "data": {"payload": payload}}


def _span_end(sid, tool, t):
    return {"kind": "span.end", "span_id": sid, "t_offset_ms": t, "data": {"label": f"tool:{tool}"}}


def _rules(inv):
    return [f.rule for f in inv.findings]


# -- the stale-figure demo: end to end through the real recorder ----------------
#
# The honest design point this suite documents: the BAD run (trusts the
# 2022 figure) looks clean to every single-run rule, because the fresh
# 2025 figure never enters its trace. What exposes it is (a) the entity
# conflict visible in the GOOD run, and (b) the earliest divergence of
# the bad run against the good baseline. Single-run heuristics are not
# enough — that is why Phase 5 diffing exists.

class TestDemoAgentStaleFigure:
    def _recorded(self, tmp_path, monkeypatch):
        rec = Recorder(SqliteStore(str(tmp_path / "t.sqlite3")))
        monkeypatch.setattr(afrec.recorder, "get_recorder", lambda: rec)
        monkeypatch.setattr(demo_agent.agent, "get_recorder", lambda: rec)
        return rec

    def test_good_run_shows_the_entity_conflict(self, tmp_path, monkeypatch):
        rec = self._recorded(tmp_path, monkeypatch)
        run_agent("Compare three solar companies", use_stale_figure=False)
        meta = rec.store.list_runs()[0]
        events = rec.store.get_events(meta["run_id"])
        inv = investigate_run(events, run_meta=meta, run_id=meta["run_id"])
        assert not inv.clean
        stale = [f for f in inv.findings if f.rule == "conflicting_records"][0]
        # Vikram Solar: 2022 vs 2025 — the trap the demo plants
        assert "Vikram Solar" in stale.observed and "2022" in stale.observed and "2025" in stale.observed
        # evidence points at the two tool results that disagree
        ids = [e.event_id for e in stale.evidence if e.event_id]
        assert len(set(ids)) >= 2
        assert stale.hypothesis  # labelled interpretation exists, is not asserted

    def test_bad_run_alone_is_invisible_to_single_run_rules(self, tmp_path, monkeypatch):
        """The core honest limitation: a confidently-wrong run that never
        queried fresh data leaves no INTERNAL contradiction to find."""
        rec = self._recorded(tmp_path, monkeypatch)
        run_agent("Compare three solar companies", use_stale_figure=True)
        meta = rec.store.list_runs()[0]
        events = rec.store.get_events(meta["run_id"])
        inv = investigate_run(events, run_meta=meta, run_id=meta["run_id"])
        assert inv.clean, "single-run rules must not manufacture findings here"

    def test_bad_run_diverges_from_good_baseline_at_the_exact_call(self, tmp_path, monkeypatch):
        rec = self._recorded(tmp_path, monkeypatch)
        run_agent("Compare three solar companies", use_stale_figure=False)
        good_meta = rec.store.list_runs()[0]
        good_events = rec.store.get_events(good_meta["run_id"])
        run_agent("Compare three solar companies", use_stale_figure=True)
        bad_meta = rec.store.list_runs()[0]
        bad_events = rec.store.get_events(bad_meta["run_id"])

        inv = investigate_run(bad_events, run_meta=bad_meta, run_id=bad_meta["run_id"],
                              baseline_events=good_events, baseline_run_id=good_meta["run_id"])
        div = [f for f in inv.findings if f.rule == "baseline_divergence"]
        assert div, "bad run must diverge from the good baseline"
        f = div[0]
        assert f.severity == CRITICAL
        # the divergence is the exact position where search became calculator
        assert "tool_call_changed" in f.observed
        # and it carries concrete evidence from the bad trace
        assert f.evidence and f.evidence[0].excerpt


# -- individual rules ----------------------------------------------------------

class TestRunNeverEnded:
    def test_no_run_end_fires_warning(self):
        inv = investigate_run([_tool_span("s1", "search", {}, 1)], run_meta={"status": "completed"})
        assert "run_never_ended" in _rules(inv)

    def test_live_running_run_is_info_not_warning(self):
        inv = investigate_run([_tool_span("s1", "search", {}, 1)], run_meta={"status": "running"})
        f = [x for x in inv.findings if x.rule == "run_never_ended"][0]
        assert f.severity == INFO


class TestCrashedRun:
    def test_crash_is_critical_with_partial_trace_evidence(self):
        inv = investigate_run(
            [_tool_span("s1", "search", {"q": "x"}, 1)],  # never ends
            run_meta={"status": "crashed", "run_id": "r1"},
        )
        rules = _rules(inv)
        assert "run_status_crashed" in rules
        crash = [f for f in inv.findings if f.rule == "run_status_crashed"][0]
        assert crash.severity == CRITICAL
        # the unclosed span inside a crashed run escalates to critical
        unclosed = [f for f in inv.findings if f.rule == "unclosed_span"][0]
        assert unclosed.severity == CRITICAL


class TestErrorEvents:
    def test_error_event_is_critical_with_verbatim_excerpt(self):
        events = [
            _tool_span("s1", "search", {"q": "x"}, 1),
            {"kind": "error", "span_id": "s1", "t_offset_ms": 2,
             "data": {"message": "connection reset by peer"}},
            _span_end("s1", "search", 3),
        ]
        inv = investigate_run(events, run_meta={"status": "completed"})
        err = [f for f in inv.findings if f.rule == "error_event"][0]
        assert err.severity == CRITICAL
        assert "connection reset by peer" in err.observed
        assert err.evidence[0].excerpt


class TestToolWithoutResult:
    def test_tool_ended_without_result(self):
        events = [
            _tool_span("s1", "search", {"q": "x"}, 1),
            _span_end("s1", "search", 2),
            {"kind": "run.end", "t_offset_ms": 3},
        ]
        inv = investigate_run(events, run_meta={"status": "completed"})
        assert "tool_without_result" in _rules(inv)
        f = [x for x in inv.findings if x.rule == "tool_without_result"][0]
        assert f.severity == WARNING


class TestTokenCaptureGap:
    def test_missing_usage_is_flagged_as_unknown(self):
        events = [
            {"kind": "span.start", "span_id": "s1", "t_offset_ms": 1, "data": {"label": "llm:mock-gpt"}},
            {"kind": "llm.response", "span_id": "s1", "t_offset_ms": 2, "data": {},
             "input_tokens": None, "output_tokens": None, "model": "mock-gpt"},
            {"kind": "span.end", "span_id": "s1", "t_offset_ms": 3, "data": {"label": "llm:mock-gpt"}},
            {"kind": "run.end", "t_offset_ms": 4},
        ]
        inv = investigate_run(events, run_meta={"status": "completed"})
        assert "token_capture_gap" in _rules(inv)


class TestCleanRunReportedClean:
    def test_no_rules_no_findings(self):
        events = [
            _tool_span("s1", "search", {"query": "solar companies india"}, 1),
            _tool_result("s1", [{"company": "Adani Green", "year": 2025}], 2),
            _span_end("s1", "search", 3),
            {"kind": "span.start", "span_id": "s2", "t_offset_ms": 4, "data": {"label": "llm:mock-gpt"}},
            {"kind": "llm.response", "span_id": "s2", "t_offset_ms": 5, "data": {"payload": "ok"},
             "input_tokens": 100, "output_tokens": 20, "model": "mock-gpt"},
            {"kind": "span.end", "span_id": "s2", "t_offset_ms": 6, "data": {"label": "llm:mock-gpt"}},
            {"kind": "run.end", "t_offset_ms": 7},
        ]
        inv = investigate_run(events, run_meta={"status": "completed"})
        assert inv.clean, f"expected clean, got {[f.to_json() for f in inv.findings]}"
        assert inv.to_json()["clean"] is True

    def test_identical_baseline_is_info_not_critical(self):
        events = [
            _tool_span("s1", "search", {"q": "x"}, 1),
            _tool_result("s1", [{"company": "A", "year": 2025}], 2),
            _span_end("s1", "search", 3),
            {"kind": "run.end", "t_offset_ms": 4},
        ]
        inv = investigate_run(events, run_meta={"status": "completed"},
                              baseline_events=list(events))
        assert "baseline_identical" in _rules(inv)
        f = [x for x in inv.findings if x.rule == "baseline_identical"][0]
        assert f.severity == INFO


class TestSerialization:
    def test_findings_serialize_with_note_and_labels(self):
        events = [
            _tool_span("s1", "search", {"q": "x"}, 1),
            _span_end("s1", "search", 2),
            {"kind": "run.end", "t_offset_ms": 3},
        ]
        inv = investigate_run(events, run_meta={"status": "completed"})
        doc = inv.to_json()
        assert doc["clean"] is False
        assert doc["finding_count"] == len(doc["findings"])
        assert "hypothesis" in json.dumps(doc)
        assert "not asserted" in doc["note"]


# -- stale_consumption + empty_search_conclusion (the 30-label benchmark rules) ---

def _llm_answer(sid, t, answer, model="mock-gpt"):
    return [
        {"kind": "span.start", "span_id": sid, "t_offset_ms": t, "data": {"label": f"llm:{model}"}},
        {"kind": "llm.response", "span_id": sid, "t_offset_ms": t + 1,
         "data": {"payload": answer}, "input_tokens": 300, "output_tokens": 40, "model": model},
        {"kind": "span.end", "span_id": sid, "t_offset_ms": t + 2, "data": {"label": f"llm:{model}"}},
    ]


def _run_end(t):
    return {"kind": "run.end", "t_offset_ms": t}


class TestStaleConsumption:
    """The r09 shape: a trace holds an old and a fresh value; the agent
    consumes the old one. Fires ONLY when a fresher value exists."""

    RATES = [{"metric": "usd_inr_rate", "value": 74.5, "as_of": "2023-12-31"},
             {"metric": "usd_inr_rate", "value": 83.2, "as_of": "2025-10-01"}]

    def test_fires_when_answer_cites_older_value(self):
        ev = ([_tool_span("s1", "search", {"query": "exchange rate"}, 1),
               _tool_result("s1", self.RATES, 2), _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "Using the exchange rate of 74.5 INR/USD (2023), "
                                     "the answer is 216.35 million USD.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Convert Ather revenue to USD", "status": "completed"})
        assert "stale_consumption" in _rules(inv)
        f = [x for x in inv.findings if x.rule == "stale_consumption"][0]
        assert f.severity == WARNING
        assert len(f.evidence) == 3  # older record, fresher record, consumer
        assert "74.5" in f.observed and "83.2" in f.observed

    def test_fires_when_calculator_uses_older_value(self):
        ev = ([_tool_span("s1", "search", {"query": "exchange rate"}, 1),
               _tool_result("s1", self.RATES, 2), _span_end("s1", "search", 3),
               _tool_span("s2", "calculator", {"expression": "1000000 * 74.5"}, 4),
               _tool_result("s2", {"value": 74500000}, 5), _span_end("s2", "calculator", 6)]
              + _llm_answer("s3", 7, "The answer is 74,500,000 INR.")
              + [_run_end(10)])
        inv = investigate_run(ev, run_meta={"task": "Convert 1 million USD", "status": "completed"})
        assert "stale_consumption" in _rules(inv)

    def test_silent_when_freshest_value_used(self):
        ev = ([_tool_span("s1", "search", {"query": "exchange rate"}, 1),
               _tool_result("s1", self.RATES, 2), _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "The most recent rate is 83.2 (as of 2025-10-01); "
                                     "1 million USD is 83,200,000 INR.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Convert 1 million USD", "status": "completed"})
        assert "stale_consumption" not in _rules(inv)

    def test_silent_when_answer_cites_both_values(self):
        ev = ([_tool_span("s1", "search", {"query": "exchange rate"}, 1),
               _tool_result("s1", self.RATES, 2), _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "The corpus has 74.5 (2023-12-31) and 83.2 (2025-10-01); "
                                     "I trust the fresher one, 83.2.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Convert 1 million USD", "status": "completed"})
        assert "stale_consumption" not in _rules(inv)

    def test_silent_with_only_one_value_on_record(self):
        ev = ([_tool_span("s1", "search", {"query": "rate"}, 1),
               _tool_result("s1", [{"metric": "usd_inr_rate", "value": 74.5, "as_of": "2023-12-31"}], 2),
               _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "The rate is 74.5.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Get the rate", "status": "completed"})
        assert "stale_consumption" not in _rules(inv)

    def test_silent_when_task_explicitly_asks_for_the_older_date(self):
        ev = ([_tool_span("s1", "search", {"query": "exchange rate"}, 1),
               _tool_result("s1", self.RATES, 2), _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "In 2023-12-31 the rate was 74.5 INR/USD.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "What was the rate on 2023-12-31?", "status": "completed"})
        assert "stale_consumption" not in _rules(inv)

    def test_comma_formatted_numbers_match(self):
        ev = ([_tool_span("s1", "search", {"query": "revenue"}, 1),
               _tool_result("s1", [{"company": "Vikram Solar", "metric": "revenue_inr_cr",
                                    "value": 2100, "year": 2022},
                                   {"company": "Vikram Solar", "metric": "revenue_inr_cr",
                                    "value": 3110, "year": 2025}], 2),
               _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "Vikram Solar's revenue is 2,100 crore INR.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Vikram Solar revenue", "status": "completed"})
        assert "stale_consumption" in _rules(inv)

    def test_no_false_match_inside_larger_numbers(self):
        ev = ([_tool_span("s1", "search", {"query": "rate"}, 1),
               _tool_result("s1", self.RATES, 2), _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "The total is 83,200,000 INR, converted at the current rate.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Convert 1 million USD", "status": "completed"})
        assert "stale_consumption" not in _rules(inv)  # 83.2 must not match 83,200,000


class TestEmptySearchConclusion:
    """The r29 shape: every search empty, the answer claims unavailability.
    A run with any real evidence (r21) must stay silent."""

    def _empty_searches(self, sid_a="s1", sid_b="s2"):
        return ([_tool_span(sid_a, "search", {"query": "oldest data year company"}, 1),
                 _tool_result(sid_a, [], 2), _span_end(sid_a, "search", 3),
                 _tool_span(sid_b, "search", {"query": "oldest data year in corpus"}, 4),
                 _tool_result(sid_b, [], 5), _span_end(sid_b, "search", 6)])

    def test_fires_when_all_searches_empty_and_answer_claims_unavailability(self):
        ev = self._empty_searches() + _llm_answer("s3", 7, "The search corpus returned no records, "
                                                           "so the requested information is not available.") + [_run_end(11)]
        inv = investigate_run(ev, run_meta={"task": "Which company's data is oldest?", "status": "completed"})
        assert "empty_search_conclusion" in _rules(inv)
        f = [x for x in inv.findings if x.rule == "empty_search_conclusion"][0]
        assert f.severity == WARNING
        assert "2 search calls" in f.observed
        assert len(f.evidence) >= 3

    def test_fires_on_the_real_live_capture_phrasing(self):
        """Regression: the first real captured failure (2026-10-17, Groq
        gpt-oss-20b) answered "couldn’t locate a transaction" — a curly
        apostrophe the regex originally missed. The rule must meet the
        language real agents use, not just our test fixtures."""
        ev = ([_tool_span("s1", "search_transactions", {"query": "PAY-404"}, 1),
               _tool_result("s1", [], 2), _span_end("s1", "search_transactions", 3)]
              + _llm_answer("s2", 4, "I\u2019m sorry, but I couldn\u2019t locate a transaction "
                                   "with the reference PAY-404 in our ledger.") + [_run_end(6)])
        inv = investigate_run(ev, run_meta={"task": "check payment PAY-404", "status": "completed"})
        assert "empty_search_conclusion" in _rules(inv)

    def test_silent_when_any_tool_returned_real_evidence(self):
        ev = ([_tool_span("s1", "search", {"query": "4724.096"}, 1),
               _tool_result("s1", [], 2), _span_end("s1", "search", 3),
               _tool_span("s2", "calculator", {"expression": "5678*83.2/100"}, 4),
               _tool_result("s2", {"value": 4724.096}, 5), _span_end("s2", "calculator", 6)]
              + _llm_answer("s3", 7, "The calculator result is 4724.096. However, a search of "
                                     "the corpus returned no records.")
              + [_run_end(11)])
        inv = investigate_run(ev, run_meta={"task": "Compute and interpret", "status": "completed"})
        assert "empty_search_conclusion" not in _rules(inv)

    def test_silent_when_a_search_found_something(self):
        ev = ([_tool_span("s1", "search", {"query": "ather revenue"}, 1),
               _tool_result("s1", [{"company": "Ather Energy", "metric": "revenue_inr_cr",
                                    "value": 1800, "year": 2024}], 2),
               _span_end("s1", "search", 3)]
              + _llm_answer("s2", 4, "No matching records were found for the other question.")
              + [_run_end(7)])
        inv = investigate_run(ev, run_meta={"task": "Ather revenue", "status": "completed"})
        assert "empty_search_conclusion" not in _rules(inv)

    def test_silent_when_answer_reports_something_rather_than_unavailability(self):
        ev = self._empty_searches() + _llm_answer("s3", 7, "The oldest company is Vikram Solar (2022).") + [_run_end(11)]
        inv = investigate_run(ev, run_meta={"task": "Which company's data is oldest?", "status": "completed"})
        assert "empty_search_conclusion" not in _rules(inv)
