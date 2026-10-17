"""
Phase 6 — Failure investigator.

Reads a recorded trace and answers the two questions that matter after a
bad run: *what went wrong*, and *what is the evidence*. This is the
module a debugging engineer (or a hiring manager watching the demo)
actually opens.

Design rules, same DNA as the rest of the system:

- RULES, NOT AN LLM. Every finding is produced by a deterministic rule
  that looks only at recorded events. The investigator never invents a
  fact it cannot point at.
- OBSERVED vs HYPOTHESIS, always separated. `observed` is what the trace
  literally shows; `hypothesis` is the interpretation, explicitly
  labelled, never asserted. A confident guess is worse than an honest
  gap — the same rule that prices tokens.
- EVIDENCE IS A POINTER, not a paraphrase. Every finding carries the
  event_ids and a verbatim excerpt of the data that triggered it, so
  every claim is one click from its source.
- If a healthy baseline run is supplied, the Phase 5 diff locates the
  earliest divergence: the exact position where a good run and this run
  stopped agreeing. That position is the finding.
- Clean traces are reported clean. A run with no triggered rules gets
  `clean: true` and no manufactured findings.

The rules (each independently testable):

  run_never_ended        run has no run.end event / status still 'running'
  run_status_crashed     run meta says 'crashed'
  error_event            the agent recorded an error event
  unclosed_span          span started but never ended (crash mid-flight)
  tool_without_result    a tool call ended without a recorded result
  conflicting_records    two tool results for the same entity disagree
                         on a date-like field (the stale-data trap)
  token_capture_gap      llm.response events with no token usage
  baseline_divergence    earliest meaningful divergence vs a baseline run
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .replay import diff_runs

CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

# Fields treated as "date-like" for the conflicting-records rule. A
# disagreement between two tool results on one of these, for the same
# entity, is the classic stale-data trap the demo agent plants.
DATE_KEYS = {"year", "as_of", "updated_at", "as_of_date", "date"}
ENTITY_KEYS = ("company", "entity", "name", "id", "symbol")

MAX_EXCERPT = 240  # verbatim excerpts stay short; the full event is a click away


@dataclass
class Evidence:
    """A pointer to the recorded fact behind a finding. Verbatim, never paraphrased."""
    event_id: Optional[str]
    kind: str
    t_offset_ms: Optional[float]
    excerpt: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "kind": self.kind,
            "t_offset_ms": self.t_offset_ms,
            "excerpt": self.excerpt,
        }


@dataclass
class Finding:
    rule: str
    severity: str  # critical | warning | info
    observed: str      # what the trace literally shows
    hypothesis: str    # interpretation, labelled as hypothesis, may be ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "observed": self.observed,
            "hypothesis": self.hypothesis,
            "evidence": [e.to_json() for e in self.evidence],
        }


@dataclass
class Investigation:
    run_id: Optional[str]
    findings: List[Finding] = field(default_factory=list)
    clean: bool = True

    def to_json(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "clean": self.clean,
            "finding_count": len(self.findings),
            "findings": [f.to_json() for f in self.findings],
            "note": "observed = facts from the recorded trace; hypothesis = "
                    "labelled interpretation, not asserted. A clean result "
                    "means no rule fired, not that the run was correct.",
        }


def _excerpt(data: Any) -> str:
    """Verbatim slice of recorded data, truncated for display."""
    text = json.dumps(data, sort_keys=True, default=str)
    return text if len(text) <= MAX_EXCERPT else text[: MAX_EXCERPT - 1] + "…"


def _ev(events: List[Dict[str, Any]], idx: int) -> Evidence:
    ev = events[idx]
    return Evidence(
        event_id=ev.get("event_id"),
        kind=ev.get("kind", "?"),
        t_offset_ms=ev.get("t_offset_ms"),
        excerpt=_excerpt(ev.get("data") or {}),
    )


def investigate_run(events: List[Dict[str, Any]],
                    run_meta: Optional[Dict[str, Any]] = None,
                    run_id: Optional[str] = None,
                    baseline_events: Optional[List[Dict[str, Any]]] = None,
                    baseline_run_id: Optional[str] = None) -> Investigation:
    """Run every rule over one trace. Findings only; no verdicts invented."""
    inv = Investigation(run_id=run_id or (run_meta or {}).get("run_id"))

    # -- rule: run never ended -----------------------------------------------
    has_run_end = any(ev.get("kind") == "run.end" for ev in events)
    status = (run_meta or {}).get("status")
    if not has_run_end and status != "crashed":
        # A still-running live run is normal; a stored one with no end
        # event and no crash flag is a trace that stopped mid-flight.
        f = Finding(
            rule="run_never_ended", severity=WARNING,
            observed="no run.end event recorded" +
                     (f", run status is '{status}'" if status else ""),
            hypothesis="the agent was still running when captured, or the "
                      "process died before recording the end of the run",
            evidence=[],
        )
        if status == "running":
            f.severity = INFO  # could genuinely be in flight
        _add(inv, f)

    # -- rule: run marked crashed --------------------------------------------
    if status == "crashed":
        _add(inv, Finding(
            rule="run_status_crashed", severity=CRITICAL,
            observed="run metadata records status 'crashed'",
            hypothesis="the agent raised an unhandled exception mid-flight; "
                      "the partial trace below is everything that happened "
                      "before it died",
            evidence=[Evidence(None, "run.meta", None, _excerpt(run_meta or {}))],
        ))

    # -- indexes every other rule needs --------------------------------------
    error_idx = [i for i, ev in enumerate(events) if ev.get("kind") == "error"]
    span_starts = [i for i, ev in enumerate(events) if ev.get("kind") == "span.start"]
    span_ends = {ev.get("span_id") for ev in events if ev.get("kind") == "span.end"}
    tool_result_spans = {ev.get("span_id") for ev in events if ev.get("kind") == "tool.result"}

    # -- rule: error events ---------------------------------------------------
    for i in error_idx:
        ev = events[i]
        _add(inv, Finding(
            rule="error_event", severity=CRITICAL,
            observed=f"error event at t+{ev.get('t_offset_ms')}ms: {_excerpt(ev.get('data') or {})}",
            hypothesis="the agent recorded this error itself — check whether it "
                      "recovered or propagated the failure downstream",
            evidence=[_ev(events, i)],
        ))

    # -- rules: span integrity (needs both, cross-referenced) ---------------
    for i in span_starts:
        ev = events[i]
        sid = ev.get("span_id")
        label = (ev.get("data") or {}).get("label", "")
        is_tool = isinstance(label, str) and label.startswith("tool:")
        if sid not in span_ends:
            sev = CRITICAL if (status == "crashed" or error_idx) else WARNING
            _add(inv, Finding(
                rule="unclosed_span", severity=sev,
                observed=f"span '{label or sid}' started at t+{ev.get('t_offset_ms')}ms but has no span.end",
                hypothesis="execution died inside this span, or span recording "
                          "itself was interrupted",
                evidence=[_ev(events, i)],
            ))
        elif is_tool and sid not in tool_result_spans:
            _add(inv, Finding(
                rule="tool_without_result", severity=WARNING,
                observed=f"tool call '{label[5:]}' ended but no tool.result was recorded for it",
                hypothesis="the tool raised or returned nothing and the result "
                          "was never recorded — the agent may have acted on "
                          "an assumption instead of real output",
                evidence=[_ev(events, i)],
            ))

    # -- rule: repeated decisions (the token-burning loop) ---------------------
    _check_repeated_decisions(inv, events)

    # -- rule: conflicting records (the stale-data trap) ----------------------
    _check_conflicting_records(inv, events)

    # -- rule: token capture gaps --------------------------------------------
    for i, ev in enumerate(events):
        if ev.get("kind") == "llm.response" and not (ev.get("input_tokens") or ev.get("output_tokens")):
            _add(inv, Finding(
                rule="token_capture_gap", severity=INFO,
                observed=f"llm.response at t+{ev.get('t_offset_ms')}ms (model {ev.get('model') or 'unknown'}) "
                         "recorded no token usage",
                hypothesis="usage was never captured for this call, so its cost "
                           "is unknown, not zero — the profiler reports it as unknown",
                evidence=[_ev(events, i)],
            ))

    # -- rule: baseline divergence --------------------------------------------
    if baseline_events is not None:
        d = diff_runs(baseline_events, events, run_a=baseline_run_id or "baseline", run_b=run_id or "this_run")
        if d.identical:
            _add(inv, Finding(
                rule="baseline_identical", severity=INFO,
                observed="trace is identical in decisions to the baseline run "
                         "(same tools, arguments, models and payloads)",
                hypothesis="the failure, if any, is not in the decisions this "
                           "diff can see",
            ))
        elif d.first_divergence is not None:
            fd = d.first_divergence
            ev = _nearest_event_at(events, fd.position)
            _add(inv, Finding(
                rule="baseline_divergence", severity=CRITICAL,
                observed=f"earliest divergence from baseline at decision #{fd.position + 1}: "
                         f"{fd.kind} — {fd.detail.get('why', '')}",
                hypothesis="this is where the two executions parted ways; "
                           "everything after it is fallout, not cause",
                evidence=[ev] if ev else [],
            ))

    inv.findings.sort(key=_severity_order)
    inv.clean = len(inv.findings) == 0
    return inv


def _severity_order(f: Finding) -> Tuple[int, str]:
    return ({"critical": 0, "warning": 1, "info": 2}[f.severity], f.rule)


def _add(inv: Investigation, f: Finding) -> None:
    inv.findings.append(f)
    inv.clean = False


LOOP_THRESHOLD = 5  # identical consecutive decisions before this is called a loop


def _decision_key(ev: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """Fingerprint a span.start as a decision: what was called, with what.

    Tool calls fingerprint on arguments; LLM calls on the message list. The
    fingerprint is content-based, so a loop is only flagged when the agent
    repeats the SAME call unchanged - retries with different arguments, or a
    legitimate retry after an error result, are different decisions.
    """
    label = (ev.get("data") or {}).get("label", "")
    if not isinstance(label, str) or not (label.startswith("tool:") or label.startswith("llm:")):
        return None
    data = ev.get("data") or {}
    payload = data.get("arguments") if label.startswith("tool:") else data.get("messages")
    return (label, json.dumps(payload, sort_keys=True, default=str))


def _check_repeated_decisions(inv: Investigation, events: List[Dict[str, Any]]) -> None:
    """The same decision made LOOP_THRESHOLD+ times in a row with no progress
    between repetitions. The classic failure: a stuck agent burning tokens on
    an identical call because it never reads its own results."""
    decisions: List[Tuple[Any, ...]] = []
    idx_by_key: Dict[Any, List[int]] = {}
    for i, ev in enumerate(events):
        if ev.get("kind") != "span.start":
            continue
        key = _decision_key(ev)
        if key is None:
            continue
        decisions.append(key)
        idx_by_key.setdefault(key, []).append(i)

    # count the longest CONSECUTIVE run of each identical decision
    i = 0
    while i < len(decisions):
        j = i
        while j + 1 < len(decisions) and decisions[j + 1] == decisions[i]:
            j += 1
        count = j - i + 1
        if count >= LOOP_THRESHOLD:
            key = decisions[i]
            span_ids = [events[k].get("span_id") for k in idx_by_key[key][i:i + count] if k < len(events)]
            starts = idx_by_key[key][i:i + count]
            # honest token burn: sum usage of the responses belonging to these spans
            span_id_set = {events[k].get("span_id") for k in starts}
            in_tok = out_tok = 0
            for ev in events:
                if ev.get("kind") == "llm.response" and ev.get("span_id") in span_id_set:
                    in_tok += ev.get("input_tokens") or 0
                    out_tok += ev.get("output_tokens") or 0
            label = key[0]
            burn = ""
            if in_tok or out_tok:
                burn = f", {in_tok} input + {out_tok} output tokens recorded across them"
            _add(inv, Finding(
                rule="repeated_decision", severity=WARNING,
                observed=f"decision '{label}' repeated {count} times in a row with identical "
                         f"arguments{burn}",
                hypothesis="the agent is stuck in a loop: it keeps making the same call "
                           "without consuming the result, which burns tokens and never "
                           "progresses - check what the responses contained and why the "
                           "agent ignored them",
                evidence=[_ev(events, k) for k in starts[:3]] + ([_ev(events, starts[-1])] if count > 3 else []),
            ))
        i = j + 1


def _check_conflicting_records(inv: Investigation, events: List[Dict[str, Any]]) -> None:
    """Two tool results that describe the same entity but disagree on a
    date-like field. The classic failure: one source is stale and the
    agent never notices."""
    # entity -> {date_key -> [(value, event_index)]}
    seen: Dict[Any, Dict[str, List[Tuple[Any, int]]]] = {}
    for i, ev in enumerate(events):
        if ev.get("kind") != "tool.result":
            continue
        payload = (ev.get("data") or {}).get("payload")
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            entity = next((row[k] for k in ENTITY_KEYS if k in row), None)
            if entity is None:
                continue
            dates = seen.setdefault(entity, {})
            for key in row:
                if key.lower() in DATE_KEYS:
                    dates.setdefault(key, []).append((row[key], i))

    for entity, dates in seen.items():
        for key, pairs in dates.items():
            values = {v for v, _ in pairs}
            if len(values) > 1:
                parts = ", ".join(f"{v} (event {events[i].get('event_id') or i})" for v, i in pairs)
                _add(inv, Finding(
                    rule="conflicting_records", severity=WARNING,
                    observed=f"tool results disagree for entity '{entity}': "
                             f"{key} = {parts}",
                    hypothesis="one of these records is stale and the agent may "
                               "have consumed the older value without "
                               "re-verifying — check which value later spans "
                               "used",
                    evidence=[_ev(events, i) for _, i in pairs],
                ))


def _nearest_event_at(events: List[Dict[str, Any]], position: int) -> Optional[Evidence]:
    """Map a diff position (a decision fingerprint) back to a concrete event,
    so the divergence finding still points at recordable evidence."""
    fingerprints = []
    for i, ev in enumerate(events):
        data = ev.get("data") or {}
        label = data.get("label", "")
        if ev.get("kind") == "span.start" and isinstance(label, str) and (label.startswith("tool:") or label.startswith("llm:")):
            fingerprints.append(i)
        elif ev.get("kind") in ("llm.response", "tool.result"):
            fingerprints.append(i)
    if 0 <= position < len(fingerprints):
        return _ev(events, fingerprints[position])
    return None
