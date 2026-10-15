/**
 * Trace reconstruction — pure logic, no React, fully unit-testable.
 *
 * Turns a flat, ordered event list into:
 *  - a span tree (for the timeline)
 *  - a per-run cost summary (for the profiler)
 *  - a flat event list with span context attached (for the inspector)
 *
 * Rules, mirroring the SDK:
 *  - orphans tolerated: span.end without span.start (partial trace after a
 *    crash) still renders, marked incomplete.
 *  - honest costs: missing token data shows as unknown, never 0.
 *  - ordering comes from t_offset_ms (monotonic offsets), not wall-clock.
 */

export interface Event {
  event_id: string | null;
  run_id?: string; // absent in GET /runs/{id} responses; unused by tree building
  kind: string;
  span_id: string | null;
  parent_span_id: string | null;
  timestamp: number;
  t_offset_ms: number;
  data: Record<string, unknown>;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  model: string | null;
}

export interface SpanNode {
  span_id: string;
  label: string;
  kind: string; // llm | tool | span
  parent_span_id: string | null;
  start_ms: number;
  end_ms: number | null; // null = never closed (crashed run / open span)
  duration_ms: number | null;
  events: Event[];
  children: SpanNode[];
  incomplete: boolean;
}

/** Per-span token totals for LLM spans (from llm.response events).
 *  Tool spans return null — they don't consume tokens. */
export function spanTokens(events: Event[]): { input: number; output: number; cached: number } | null {
  const resp = events.filter((e) => e.kind === 'llm.response');
  if (resp.length === 0) return null;
  const sum = (k: 'input_tokens' | 'output_tokens' | 'cached_tokens') =>
    resp.reduce((acc, e) => acc + (e[k] ?? 0), 0);
  return { input: sum('input_tokens'), output: sum('output_tokens'), cached: sum('cached_tokens') };
}

export interface CostSummary {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  llm_calls: number;
  tool_calls: number;
  unknown_usage: boolean;
}

/** Rebuild the span tree from flat events. Order-independent but expects
 *  events sorted by t_offset_ms. Unknown kinds attach to their span. */
export function buildSpanTree(events: Event[]): SpanNode[] {
  const roots: SpanNode[] = [];
  const byId = new Map<string, SpanNode>();

  const ensureSpan = (id: string, parent: string | null, kind: string): SpanNode => {
    let node = byId.get(id);
    if (!node) {
      node = {
        span_id: id, label: id, kind, parent_span_id: parent,
        start_ms: 0, end_ms: null, duration_ms: null,
        events: [], children: [], incomplete: true,
      };
      byId.set(id, node);
    }
    return node;
  };

  for (const ev of events) {
    switch (ev.kind) {
      case 'span.start': {
        const parent = ev.parent_span_id ? byId.get(ev.parent_span_id) ?? null : null;
        const node = ensureSpan(ev.span_id ?? 'unknown', ev.parent_span_id, 'span');
        node.label = String(ev.data['label'] ?? ev.span_id ?? 'span');
        node.kind = node.label.startsWith('llm:') ? 'llm'
          : node.label.startsWith('tool:') ? 'tool' : 'span';
        node.start_ms = ev.t_offset_ms;
        node.incomplete = true;
        if (parent) parent.children.push(node);
        else roots.push(node);
        break;
      }
      case 'span.end': {
        const node = ev.span_id ? byId.get(ev.span_id) : undefined;
        if (node) {
          node.end_ms = ev.t_offset_ms;
          node.duration_ms = node.end_ms - node.start_ms;
          node.incomplete = false;
        }
        // span.end with no matching start: tolerated silently (partial trace)
        break;
      }
      case 'run.start':
      case 'run.end':
        break; // run-level, not rendered as spans
      default: {
        // llm.response / tool.result / error / unknown kinds → attach to span
        if (ev.span_id) {
          const node = ensureSpan(ev.span_id, ev.parent_span_id, 'span');
          if (node.incomplete && node.label === node.span_id) {
            node.label = ev.span_id; // keep id until a start event labels it
          }
          node.events.push(ev);
        }
        break;
      }
    }
  }
  return roots;
}

/** Honest cost summary: sums what exists, flags what doesn't. */
export function summarizeCost(events: Event[]): CostSummary {
  const summary: CostSummary = {
    input_tokens: 0, output_tokens: 0, cached_tokens: 0,
    llm_calls: 0, tool_calls: 0, unknown_usage: false,
  };
  for (const ev of events) {
    if (ev.kind === 'llm.response') {
      summary.llm_calls += 1;
      if (ev.input_tokens == null && ev.output_tokens == null) summary.unknown_usage = true;
      summary.input_tokens += ev.input_tokens ?? 0;
      summary.output_tokens += ev.output_tokens ?? 0;
      summary.cached_tokens += ev.cached_tokens ?? 0;
    } else if (ev.kind === 'tool.request' || (ev.kind === 'span.start' && String(ev.data['label'] ?? '').startsWith('tool:'))) {
      if (ev.kind === 'span.start') summary.tool_calls += 1;
    }
  }
  // tool.request events count too (SDK emits span.start for tools; API clients may send tool.request)
  for (const ev of events) {
    if (ev.kind === 'tool.request') summary.tool_calls += 1;
  }
  return summary;
}

/** Wall-clock duration of a run, honest when the run never ended. */
export function runDuration(events: Event[]): number | null {
  const start = events.find((e) => e.kind === 'run.start');
  const end = events.find((e) => e.kind === 'run.end');
  if (!start) return null;
  if (!end) return null; // crashed / partial: no duration, not a fake one
  return end.t_offset_ms - start.t_offset_ms;
}

// -- Phase 6: investigation display logic -----------------------------------

export interface InvestigationFinding {
  rule: string;
  severity: string; // 'critical' | 'warning' | 'info' | unknown
  observed: string;
  hypothesis: string;
  evidence: { event_id: string | null; kind: string; t_offset_ms: number | null; excerpt: string }[];
}

export interface Investigation {
  run_id: string;
  clean: boolean;
  finding_count: number;
  findings: InvestigationFinding[];
  note: string;
}

const SEVERITY_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };

/** Most severe first; within a severity, keeps the server's original order. */
export function sortFindings(findings: InvestigationFinding[]): InvestigationFinding[] {
  return [...findings].sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 3) - (SEVERITY_RANK[b.severity] ?? 3),
  );
}

/** Counts per severity, for the summary line above the findings list. */
export function countBySeverity(findings: InvestigationFinding[]): { critical: number; warning: number; info: number } {
  const counts = { critical: 0, warning: 0, info: 0 };
  for (const f of findings) {
    if (f.severity === 'critical' || f.severity === 'warning' || f.severity === 'info') {
      counts[f.severity] += 1;
    }
  }
  return counts;
}
