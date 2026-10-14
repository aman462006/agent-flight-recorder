import { describe, it, expect } from 'vitest';
import { buildSpanTree, summarizeCost, runDuration, Event } from './trace';

const ev = (o: Partial<Event>): Event => ({
  event_id: o.event_id ?? `e${Math.random()}`,
  run_id: 'r1' as string | undefined, kind: 'span.start', span_id: null, parent_span_id: null,
  timestamp: 0, t_offset_ms: 0, data: {}, input_tokens: null,
  output_tokens: null, cached_tokens: null, model: null, ...o,
});

describe('buildSpanTree', () => {
  it('nests children under parents and computes durations', () => {
    const events = [
      ev({ kind: 'span.start', span_id: 's1', data: { label: 'tool:search' }, t_offset_ms: 5 }),
      ev({ kind: 'span.start', span_id: 's2', parent_span_id: 's1', data: { label: 'llm:mock' }, t_offset_ms: 10 }),
      ev({ kind: 'span.end', span_id: 's2', t_offset_ms: 20 }),
      ev({ kind: 'span.end', span_id: 's1', t_offset_ms: 30 }),
    ];
    const tree = buildSpanTree(events);
    expect(tree).toHaveLength(1);
    expect(tree[0].kind).toBe('tool');
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children[0].kind).toBe('llm');
    expect(tree[0].duration_ms).toBe(25);
    expect(tree[0].children[0].duration_ms).toBe(10);
  });

  it('tolerates a crashed run: unclosed span is marked incomplete, not dropped', () => {
    const events = [
      ev({ kind: 'span.start', span_id: 's1', data: { label: 'tool:explode' }, t_offset_ms: 5 }),
      ev({ kind: 'error', span_id: 's1', data: { message: 'boom' }, t_offset_ms: 9 }),
      // no span.end — agent crashed mid-span
    ];
    const tree = buildSpanTree(events);
    expect(tree).toHaveLength(1);
    expect(tree[0].incomplete).toBe(true);
    expect(tree[0].duration_ms).toBeNull();
    expect(tree[0].events).toHaveLength(1);
  });

  it('tolerates span.end with no start (partial trace import)', () => {
    const events = [ev({ kind: 'span.end', span_id: 'ghost', t_offset_ms: 10 })];
    expect(() => buildSpanTree(events)).not.toThrow();
    expect(buildSpanTree(events)).toHaveLength(0);
  });

  it('attaches payload events to their spans', () => {
    const events = [
      ev({ kind: 'span.start', span_id: 's1', data: { label: 'llm:mock' }, t_offset_ms: 0 }),
      ev({ kind: 'llm.response', span_id: 's1', data: { payload: 'hi' }, t_offset_ms: 3, input_tokens: 10, output_tokens: 5 }),
    ];
    const tree = buildSpanTree(events);
    expect(tree[0].events).toHaveLength(1);
    expect(tree[0].events[0].input_tokens).toBe(10);
  });
});

describe('summarizeCost', () => {
  it('sums tokens across LLM calls', () => {
    const events = [
      ev({ kind: 'llm.response', input_tokens: 1200, output_tokens: 150, cached_tokens: 500 }),
      ev({ kind: 'llm.response', input_tokens: 800, output_tokens: 220 }),
    ];
    const c = summarizeCost(events);
    expect(c.llm_calls).toBe(2);
    expect(c.input_tokens).toBe(2000);
    expect(c.output_tokens).toBe(370);
    expect(c.cached_tokens).toBe(500);
    expect(c.unknown_usage).toBe(false);
  });

  it('flags missing usage as unknown instead of pretending it is zero', () => {
    const events = [ev({ kind: 'llm.response' })]; // no token data at all
    const c = summarizeCost(events);
    expect(c.llm_calls).toBe(1);
    expect(c.unknown_usage).toBe(true);
  });
});

describe('runDuration', () => {
  it('computes duration only when both markers exist', () => {
    expect(runDuration([ev({ kind: 'run.start', t_offset_ms: 0 }), ev({ kind: 'run.end', t_offset_ms: 4200 })])).toBe(4200);
    expect(runDuration([ev({ kind: 'run.start', t_offset_ms: 0 })])).toBeNull();
    expect(runDuration([])).toBeNull();
  });
});

describe('Phase 6 investigation logic', () => {
  const finding = (severity: string, rule: string) => ({
    rule, severity, observed: 'x', hypothesis: 'y',
    evidence: [{ event_id: null, kind: 'k', t_offset_ms: 0, excerpt: 'e' }],
  });

  it('sorts findings most severe first, keeping server order within a severity', async () => {
    const { sortFindings } = await import('./trace');
    const findings = [finding('info', 'a'), finding('critical', 'b'), finding('warning', 'c'), finding('info', 'd')];
    const sorted = sortFindings(findings);
    expect(sorted.map((f) => f.severity)).toEqual(['critical', 'warning', 'info', 'info']);
    expect(sorted[2].rule).toBe('a'); // stable within severity
    // does not mutate the input
    expect(findings[0].severity).toBe('info');
  });

  it('counts by severity and ignores unknown severities', async () => {
    const { countBySeverity } = await import('./trace');
    const counts = countBySeverity([finding('critical', 'a'), finding('critical', 'b'), finding('weird', 'c')]);
    expect(counts).toEqual({ critical: 2, warning: 0, info: 0 });
  });
});
