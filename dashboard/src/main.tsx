import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { listRuns, getRun, investigateRun, seedExamples, ApiRun, ApiEvent, ApiInvestigation } from './api';
import { sortFindings, countBySeverity } from './trace';
import { buildSpanTree, summarizeCost, runDuration, spanTokens, SpanNode } from './trace';

/* ---------- design tokens (light theme) ---------- */

const T = {
  bg: '#f6f7fb',
  card: '#ffffff',
  border: '#e4e8f2',
  text: '#1b2333',
  soft: '#5b6472',
  faint: '#8b93a3',
  accent: '#4f6ef7',
  accentSoft: '#eef1fe',
  green: '#0d8a5f',
  greenSoft: '#e6f6ef',
  red: '#d92d20',
  redSoft: '#fdeceb',
  amber: '#b25e09',
  amberSoft: '#fdf3e4',
  blue: '#0369a1',
  blueSoft: '#e5f3fb',
  radius: 12,
};

const KIND_COLORS: Record<string, string> = {
  llm: T.amber,
  tool: T.blue,
  span: '#8b93a3',
  error: T.red,
};

const KIND_SOFT: Record<string, string> = {
  llm: T.amberSoft,
  tool: T.blueSoft,
  error: T.redSoft,
};

const card: React.CSSProperties = {
  background: T.card, borderRadius: T.radius, border: `1px solid ${T.border}`,
  boxShadow: '0 1px 2px rgba(27,35,51,0.04)',
};

const btn: React.CSSProperties = {
  background: T.accent, color: '#fff', border: 'none', borderRadius: 8,
  padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};

const btnGhost: React.CSSProperties = {
  background: 'transparent', color: T.accent, border: `1px solid ${T.accent}`,
  borderRadius: 8, padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};

const sectionLabel: React.CSSProperties = {
  fontSize: 11, color: T.faint, fontWeight: 700, textTransform: 'uppercase',
  letterSpacing: 0.8, margin: '20px 0 8px',
};

const mono: React.CSSProperties = { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' };

function statusPill(status: string): React.CSSProperties {
  const tone = status === 'crashed' ? { bg: T.redSoft, fg: T.red } :
    status === 'completed' ? { bg: T.greenSoft, fg: T.green } :
    { bg: '#eef0f5', fg: T.soft };
  return {
    display: 'inline-block', background: tone.bg, color: tone.fg, fontSize: 11,
    fontWeight: 600, borderRadius: 999, padding: '2px 10px',
  };
}

/* ---------- routing (hash-based, no router dependency) ---------- */

function useHashRoute(): [string, (h: string) => void] {
  const [hash, setHash] = useState(() => window.location.hash || '#/');
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/');
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);
  const nav = (h: string) => { window.location.hash = h; };
  return [hash, nav];
}

/* ---------- shared header ---------- */

function Header({ route }: { route: string }) {
  return (
    <header style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '14px 24px', background: '#fff', borderBottom: `1px solid ${T.border}`,
      position: 'sticky', top: 0, zIndex: 10,
    }}>
      <a href="#/" style={{ textDecoration: 'none', color: T.text, display: 'flex', alignItems: 'center', gap: 9 }}>
        <span style={{
          width: 30, height: 30, borderRadius: 9, background: T.accent, color: '#fff',
          display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15,
        }}>✈</span>
        <span style={{ fontWeight: 800, fontSize: 15, letterSpacing: -0.2 }}>Agent Flight Recorder</span>
      </a>
      <nav style={{ display: 'flex', gap: 4 }}>
        <a href="#/" style={{
          textDecoration: 'none', padding: '7px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
          color: route === '' || route === '/' ? T.accent : T.soft,
          background: route === '' || route === '/' ? T.accentSoft : 'transparent',
        }}>About</a>
        <a href="#/runs" style={{
          textDecoration: 'none', padding: '7px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600,
          color: route.startsWith('/runs') ? T.accent : T.soft,
          background: route.startsWith('/runs') ? T.accentSoft : 'transparent',
        }}>Dashboard</a>
      </nav>
    </header>
  );
}

/* ---------- About page ---------- */

function AboutPage() {
  const step = (n: number, title: string, body: string): React.ReactNode => (
    <div style={{ ...card, padding: 20, flex: '1 1 240px' }}>
      <div style={{
        width: 28, height: 28, borderRadius: 999, background: T.accentSoft, color: T.accent,
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700,
        fontSize: 13, marginBottom: 12,
      }}>{n}</div>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 6 }}>{title}</div>
      <div style={{ fontSize: 13, color: T.soft, lineHeight: 1.6 }}>{body}</div>
    </div>
  );

  return (
    <div style={{ maxWidth: 880, margin: '0 auto', padding: '48px 24px 80px' }}>
      <div style={{ ...card, padding: '48px 40px', textAlign: 'center', marginBottom: 24 }}>
        <div style={{ color: T.accent, fontWeight: 700, fontSize: 12, letterSpacing: 1.2, textTransform: 'uppercase', marginBottom: 14 }}>
          AI Agent Observability
        </div>
        <h1 style={{ fontSize: 34, fontWeight: 800, letterSpacing: -0.8, margin: '0 0 14px', lineHeight: 1.2 }}>
          A flight recorder for AI agents.
        </h1>
        <p style={{ fontSize: 15, color: T.soft, lineHeight: 1.7, margin: '0 auto 24px', maxWidth: 560 }}>
          When an AI agent crashes, silently escalates, or burns tokens on a broken loop,
          you need the same thing aviation built decades ago: a black box. The Agent Flight
          Recorder captures every run, every model call, and every decision, so failures are
          visible and explainable instead of mysterious.
        </p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
          <a href="#/runs" style={{ ...btn, textDecoration: 'none' }}>Open the dashboard</a>
          <a href="https://github.com/aman462006/agent-flight-recorder" target="_blank" rel="noreferrer" style={{ ...btnGhost, textDecoration: 'none' }}>
            View on GitHub
          </a>
        </div>
      </div>

      <h2 style={{ fontSize: 20, fontWeight: 800, letterSpacing: -0.4, margin: '36px 0 6px' }}>How it works</h2>
      <p style={{ fontSize: 14, color: T.soft, margin: '0 0 16px' }}>
        Three moving parts, one data path. Your agent never changes what it does,
        the recorder just watches it work.
      </p>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {step(1, 'Instrument',
          'Drop the recorder into any agent that calls an LLM. Each run gets spans for every model and tool call, with real token counts taken from the provider response, never estimated when real numbers exist.')}
        {step(2, 'Record and stream',
          'Runs are recorded locally and streamed to the ingestion API in the background. Recording can never crash your agent: a broken store degrades to a no-op, and an outage falls back to a local mirror that catches up later.')}
        {step(3, 'Investigate',
          'The investigator runs ten deterministic rules over each run: crashes, errors, unclosed spans, tool calls without results, conflicting records, stale-value consumption, empty-search conclusions, token gaps, repeated decisions, and baseline divergence against a healthy run.')}
      </div>

      <h2 style={{ fontSize: 20, fontWeight: 800, letterSpacing: -0.4, margin: '40px 0 6px' }}>What you can see</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
        {[
          ['Span timeline', 'Every model and tool call in order, with duration and in/out tokens per call.'],
          ['Honest cost data', 'Token counts come from the provider response. If a call reports no usage, the dashboard says unknown, not zero.'],
          ['Failure findings', 'Rules fire with evidence and a labelled hypothesis. Observed facts and hypotheses are never mixed.'],
          ['Baseline diffing', 'Some failures look clean in isolation. Comparing a bad run against a good one catches what single-run rules cannot.'],
        ].map(([title, body]) => (
          <div key={title} style={{ ...card, padding: 16 }}>
            <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 5 }}>{title}</div>
            <div style={{ fontSize: 12.5, color: T.soft, lineHeight: 1.6 }}>{body}</div>
          </div>
        ))}
      </div>


      <h2 style={{ fontSize: 20, fontWeight: 800, letterSpacing: -0.4, margin: '40px 0 6px' }}>Use it with your own agent</h2>
      <p style={{ fontSize: 14, color: T.soft, margin: '0 0 16px' }}>
        The SDK is two imports. Point it at this hosted API, or clone the repo and run the
        ingestion API yourself with a DATABASE_URL for persistent storage. Your agent's
        logic does not change, the recorder just watches it work.
      </p>
      <div style={{ ...card, padding: 0, overflow: 'hidden', marginBottom: 12 }}>
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid #eef1f7', fontSize: 12,
          fontWeight: 700, color: T.soft, background: '#fafbfe',
        }}>Python, with any LLM provider</div>
        <pre style={{
          margin: 0, padding: '16px 18px', overflowX: 'auto', fontSize: 12.2, lineHeight: 1.7,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', color: T.text,
        }}>{`from afrec import Recorder, HttpStore

# hosted API, or your own deployment's URL
rec = Recorder(store=HttpStore("https://afrec-api.onrender.com"))

with rec.run("my-agent-task") as run:
    with run.llm_call("gpt-4o-mini", [{"role": "user", "content": prompt}]) as span:
        reply = my_llm(prompt)          # your model call, unchanged
        span.result(reply,
                    input_tokens=usage.prompt_tokens,
                    output_tokens=usage.completion_tokens)
    with run.tool_call("search", {"query": q}) as tool:
        tool.result(my_search(q))       # tool spans work the same way`}</pre>
      </div>
      <div style={{ ...card, padding: 0, overflow: 'hidden' }}>
        <div style={{
          padding: '10px 16px', borderBottom: '1px solid #eef1f7', fontSize: 12,
          fontWeight: 700, color: T.soft, background: '#fafbfe',
        }}>Any language: plain HTTP</div>
        <pre style={{
          margin: 0, padding: '16px 18px', overflowX: 'auto', fontSize: 12.2, lineHeight: 1.7,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', color: T.text,
        }}>{`POST /runs    {"run_id": "run_1", "task": "...", "started_at": 1760000000}
POST /events  {"run_id": "run_1", "kind": "llm.request",
               "timestamp": 1760000001, "data": {"model": "gpt-4o-mini"}}

# both endpoints are idempotent — retries never duplicate events.
# GET /runs/{id} returns the full ordered trace back.`}</pre>
      </div>

      <div style={{ ...card, padding: 20, marginTop: 24, borderLeft: `3px solid ${T.accent}` }}>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>A note on honesty</div>
        <div style={{ fontSize: 13, color: T.soft, lineHeight: 1.65 }}>
          An investigation with no findings means no recorded contradictions, crashes or gaps.
          It is not a verdict that the run was correct. The benchmark suite measures this
          directly: 121 scenarios, zero false positives across all rules.
        </div>
      </div>

      <div style={{ textAlign: 'center', marginTop: 36 }}>
        <a href="#/runs" style={{ ...btn, textDecoration: 'none' }}>Explore recorded runs →</a>
      </div>
    </div>
  );
}

/* ---------- span timeline ---------- */

function SpanRow({ node, depth, onSelect, selectedId }: {
  node: SpanNode; depth: number; onSelect: (n: SpanNode) => void; selectedId: string | null;
}) {
  const color = KIND_COLORS[node.kind] ?? KIND_COLORS.span;
  return (
    <div>
      <div
        onClick={() => onSelect(node)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '7px 10px', marginLeft: depth * 18, cursor: 'pointer',
          borderRadius: 8,
          background: selectedId === node.span_id ? T.accentSoft : 'transparent',
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: 4, background: color, flexShrink: 0 }} />
        <span style={{ flex: 1, ...mono, fontSize: 12.5, color: T.text }}>{node.label}</span>
        {spanTokens(node.events) && (
          <span style={{ color: T.blue, fontSize: 11.5, fontWeight: 500, background: T.blueSoft, borderRadius: 999, padding: '2px 8px' }}>
            {spanTokens(node.events)!.input.toLocaleString()} in / {spanTokens(node.events)!.output.toLocaleString()} out
          </span>
        )}
        {node.duration_ms != null
          ? <span style={{ color: T.faint, fontSize: 11.5 }}>{node.duration_ms.toFixed(1)} ms</span>
          : <span style={{ color: T.red, fontSize: 11.5, fontWeight: 600 }}>open / crashed</span>}
      </div>
      {node.children.map((c) => <SpanRow key={c.span_id} node={c} depth={depth + 1} onSelect={onSelect} selectedId={selectedId} />)}
    </div>
  );
}

function EventInspector({ events }: { events: ApiEvent[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <div>
      <div style={{ color: T.faint, fontSize: 12, marginBottom: 4 }}>
        {events.length} event{events.length === 1 ? '' : 's'}
      </div>
      {events.map((ev) => {
        const id = ev.event_id ?? `${ev.kind}-${ev.t_offset_ms}`;
        const root = ev.kind.split('.')[0];
        return (
          <div key={id} style={{ marginBottom: 6 }}>
            <button
              onClick={() => setOpenId(openId === id ? null : id)}
              style={{
                all: 'unset', cursor: 'pointer',
                color: KIND_COLORS[root] ?? T.text, ...mono, fontSize: 12,
                background: KIND_SOFT[root] ?? '#f1f3f8',
                borderRadius: 6, padding: '3px 8px',
              }}
            >
              {openId === id ? '▾' : '▸'} {ev.kind}
            </button>
            {openId === id && (
              <pre style={{ background: '#f4f6fa', border: `1px solid ${T.border}`, padding: 10, borderRadius: 8, overflowX: 'auto', fontSize: 12, margin: '6px 0' }}>
                {JSON.stringify(ev.data, null, 2)}
              </pre>
            )}
          </div>
        );
      })}
      {events.length === 0 && <div style={{ color: T.faint, fontSize: 13 }}>No payload events on this span.</div>}
    </div>
  );
}

/* ---------- investigation ---------- */

const SEVERITY_COLORS: Record<string, string> = {
  critical: T.red,
  warning: T.amber,
  info: T.blue,
};

const SEVERITY_SOFT: Record<string, string> = {
  critical: T.redSoft,
  warning: T.amberSoft,
  info: T.blueSoft,
};

function InvestigationPanel({ runId, otherRuns }: { runId: string; otherRuns: ApiRun[] }) {
  const [inv, setInv] = useState<ApiInvestigation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<string>('');

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await investigateRun(runId, baseline || undefined);
      setInv(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div style={sectionLabel}>Investigation</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={run} disabled={busy} style={{ ...btn, opacity: busy ? 0.6 : 1 }}>
          {busy ? 'Investigating…' : 'Investigate this run'}
        </button>
        <select
          value={baseline}
          onChange={(e) => setBaseline(e.target.value)}
          style={{ background: '#fff', color: T.text, border: `1px solid ${T.border}`, borderRadius: 8, padding: '7px 10px', fontSize: 12, maxWidth: 220 }}
        >
          <option value="">no baseline (single-run rules)</option>
          {otherRuns.map((r) => (
            <option key={r.run_id} value={r.run_id}>baseline: {r.task.slice(0, 40)}</option>
          ))}
        </select>
      </div>
      {error && <div style={{ color: T.red, fontSize: 12, marginBottom: 8 }}>{error}</div>}
      {inv && inv.clean && (
        <div style={{ background: T.greenSoft, color: T.green, borderRadius: 8, padding: 12, fontSize: 13, lineHeight: 1.5 }}>
          No rules fired. That means no recorded contradictions, crashes or gaps, not a verdict that the run was correct.
        </div>
      )}
      {inv && !inv.clean && (() => {
        const findings = sortFindings(inv.findings);
        const counts = countBySeverity(inv.findings);
        return (
          <div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
              {(['critical', 'warning', 'info'] as const).map((s) => counts[s] > 0 && (
                <span key={s} style={{ ...statusPill(s), background: SEVERITY_SOFT[s], color: SEVERITY_COLORS[s] }}>
                  {counts[s]} {s}
                </span>
              ))}
            </div>
            {findings.map((f, i) => (
              <div key={`${f.rule}-${i}`} style={{ ...card, padding: 14, marginBottom: 8, borderLeft: `3px solid ${SEVERITY_COLORS[f.severity] ?? T.border}` }}>
                <div style={{ fontSize: 11.5, marginBottom: 5, ...mono, color: SEVERITY_COLORS[f.severity] ?? T.soft, fontWeight: 600 }}>
                  {f.rule} · {f.severity}
                </div>
                <div style={{ fontSize: 13, marginBottom: 6, color: T.text }}>{f.observed}</div>
                {f.hypothesis && <div style={{ fontSize: 12, color: T.soft, fontStyle: 'italic', marginBottom: 6 }}>Hypothesis: {f.hypothesis}</div>}
                {f.evidence.map((e, j) => (
                  <div key={j} style={{ fontSize: 11, color: T.faint, ...mono, marginTop: 4 }}>
                    evidence {e.event_id ? `${e.kind} ${e.event_id}` : e.kind}: {e.excerpt}
                  </div>
                ))}
              </div>
            ))}
          </div>
        );
      })()}
    </div>
  );
}

/* ---------- run detail ---------- */

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'accent' | 'green' }) {
  return (
    <div style={{ ...card, padding: '12px 16px', minWidth: 120 }}>
      <div style={{ fontSize: 11, color: T.faint, fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 800, color: tone === 'accent' ? T.accent : tone === 'green' ? T.green : T.text }}>
        {value}
      </div>
    </div>
  );
}

function RunDetail({ runId, otherRuns }: { runId: string; otherRuns: ApiRun[] }) {
  const [data, setData] = useState<{ run: ApiRun; events: ApiEvent[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SpanNode | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRun(runId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [runId]);

  if (error) return <div style={{ padding: 20, color: T.red }}>Failed to load run: {error}</div>;
  if (!data) return <div style={{ padding: 20, color: T.soft }}>Loading run…</div>;

  const tree = buildSpanTree(data.events);
  const cost = summarizeCost(data.events);
  const duration = runDuration(data.events);
  const runEvents = data.events.filter((e) => !e.span_id);

  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100%' }}>
      <h2 style={{ margin: '0 0 6px', fontSize: 17, fontWeight: 800, letterSpacing: -0.3 }}>{data.run.task}</h2>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        <span style={statusPill(data.run.status)}>{data.run.status}</span>
        <span style={{ color: T.faint, fontSize: 12, ...mono }}>
          {data.run.run_id}
          {duration != null ? ` · ${(duration / 1000).toFixed(2)}s` : ' · duration unknown (partial run)'}
        </span>
      </div>

      {isDemoRun(data.run) && <DemoExplainer runId={data.run.run_id} />}

      <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
        <Stat label="LLM calls" value={String(cost.llm_calls)} tone="accent" />
        <Stat label="Tool calls" value={String(cost.tool_calls)} />
        <Stat label="Input tokens" value={cost.input_tokens.toLocaleString()} />
        <Stat label="Output tokens" value={cost.output_tokens.toLocaleString()} />
        {cost.cached_tokens > 0 && <Stat label="Cached tokens" value={cost.cached_tokens.toLocaleString()} tone="green" />}
      </div>
      {cost.unknown_usage && (
        <div style={{ background: T.amberSoft, color: T.amber, borderRadius: 8, padding: 10, fontSize: 12.5, marginBottom: 16 }}>
          ⚠ Some LLM calls report no token usage — cost data for those is unknown, not zero.
        </div>
      )}

      <div style={sectionLabel}>Timeline</div>
      <div style={{ ...card, padding: 8 }}>
        {tree.length === 0 && <div style={{ color: T.faint, fontSize: 13, padding: 8 }}>No spans recorded for this run.</div>}
        {tree.map((s) => <SpanRow key={s.span_id} node={s} depth={0} onSelect={setSelected} selectedId={selected?.span_id ?? null} />)}
      </div>

      <InvestigationPanel runId={runId} otherRuns={otherRuns} />

      <div style={sectionLabel}>
        {selected ? `Span: ${selected.label}` : 'Run-level events'}
      </div>
      {selected && (() => {
        const tk = spanTokens(selected.events);
        if (!tk) return null;
        const model = selected.events.find((e) => e.kind === 'llm.response')?.model;
        return (
          <div style={{ color: T.blue, fontSize: 12, marginBottom: 8, ...mono, background: T.blueSoft, borderRadius: 999, padding: '3px 10px', display: 'inline-block' }}>
            {model ? `${model} · ` : ''}{tk.input.toLocaleString()} input / {tk.output.toLocaleString()} output tokens
            {tk.cached > 0 ? ` (${tk.cached.toLocaleString()} cached)` : ''}
          </div>
        );
      })()}
      <div style={{ ...card, padding: 12 }}>
        <EventInspector events={selected ? selected.events : runEvents} />
      </div>
    </div>
  );
}


/* ---------- demo runs vs your runs ---------- */

/** Seeded demo runs carry tags.demo = 'seeded-example'. Everything else
 *  was streamed here by a real agent (SDK or raw HTTP) and belongs to
 *  whoever owns that agent — the two never mix in one list. */
function isDemoRun(run: ApiRun): boolean {
  return Boolean(run.tags && (run.tags as Record<string, unknown>).demo);
}

const DEMO_VALUES_NOTE =
  'Where the numbers come from: every value in this run is synthetic. ' +
  'The events are scripted in afrec/demo_seed.py on the API server and seeded by POST /demo/seed ' +
  '(the Load examples button). Token counts are illustrative but priced at real model rates by the cost estimator; ' +
  'timestamps come from a fixed epoch so the runs sort deterministically. ' +
  'Nothing here is a real production capture — the tag demo: seeded-example on the run says exactly that.';

interface DemoInfo {
  title: string;
  shows: string;
  fires: string;
}

const DEMO_CATALOG: Record<string, DemoInfo> = {
  'demo-1-healthy': {
    title: 'A healthy run',
    shows: 'A support agent checks a refund from two databases, every span opens and closes, every LLM call reports token usage. The investigator stays silent on purpose: no findings is the correct verdict on a clean trace.',
    fires: 'Nothing — clean by design, so you can see what "no rules fired" looks like.',
  },
  'demo-2-crash': {
    title: 'A crash mid-tool-call',
    shows: 'The agent starts rendering a PDF, a worker panic is recorded inside the span, and the run never ends. The partial trace is everything that happened before it died.',
    fires: 'run_status_crashed (critical) and unclosed_span — the crash flag plus the span that never closed.',
  },
  'demo-3-tool-no-result': {
    title: 'A tool call with no result',
    shows: 'email.verify is called and its span ends, but no tool.result was ever recorded — and the agent then claims the email was verified. The recorder keeps the agent honest about what it actually saw.',
    fires: 'tool_without_result — the agent acted on an assumption instead of real output.',
  },
  'demo-4-loop': {
    title: 'A token-burning loop',
    shows: 'The same LLM call, identical arguments, twenty times in a row. About 9,400 tokens burned with zero progress — the exact failure that silently drains a budget.',
    fires: 'repeated_decision, with the honest token burn summed from the recorded responses.',
  },
  'demo-5-baseline-good': {
    title: 'The healthy baseline',
    shows: 'The good half of the divergence pair: the agent reads the live payment status, sees PAID, closes the case. On its own it is just a clean run — its job is to be the baseline for demo-6.',
    fires: 'Nothing on its own. Select it as the baseline when investigating demo-6.',
  },
  'demo-6-baseline-bad': {
    title: 'The diverging run',
    shows: 'Identical start, then decision #3 diverges: instead of the live payment status the agent reads the payments archive and finds a stale UNPAID record from 2025-09-01 — and escalates a case that is already settled.',
    fires: 'baseline_divergence (critical) — investigate it with demo-5 as the baseline and the finding pins the exact call where the two runs parted ways.',
  },
};

function DemoExplainer({ runId }: { runId: string }) {
  const info = DEMO_CATALOG[runId];
  return (
    <div style={{ ...card, padding: 16, marginBottom: 16, borderLeft: `3px solid ${T.amber}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ background: T.amberSoft, color: T.amber, fontSize: 10, fontWeight: 700, borderRadius: 999, padding: '2px 9px', letterSpacing: 0.5 }}>
          DEMO RUN
        </span>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>{info ? info.title : 'Seeded example run'}</span>
      </div>
      {info ? (
        <div style={{ fontSize: 13, color: T.soft, lineHeight: 1.6, marginBottom: 8 }}>
          <div style={{ marginBottom: 6 }}><b style={{ color: T.text }}>What it shows:</b> {info.shows}</div>
          <div><b style={{ color: T.text }}>What the investigator reports:</b> {info.fires}</div>
        </div>
      ) : (
        <div style={{ fontSize: 13, color: T.soft, lineHeight: 1.6, marginBottom: 8 }}>
          A synthetic example run seeded by the API. It is kept separate from your own agent runs so demos never pollute real traces.
        </div>
      )}
      <div style={{ fontSize: 12, color: T.faint, lineHeight: 1.6, background: '#f8f9fc', borderRadius: 8, padding: 10 }}>
        {DEMO_VALUES_NOTE}
      </div>
    </div>
  );
}

/* ---------- dashboard (run explorer) ---------- */

function DashboardPage({ selectedRun, onSelectRun }: { selectedRun: string | null; onSelectRun: (id: string | null) => void }) {
  const [runs, setRuns] = useState<ApiRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);

  const refresh = () => {
    listRuns()
      .then(setRuns)
      .catch((e) => setError(String(e)));
  };

  const loadExamples = () => {
    setSeeding(true);
    seedExamples()
      .then(() => listRuns())
      .then((rs: ApiRun[]) => { setRuns(rs); setSeeding(false); })
      .catch((e: unknown) => { setError(String(e)); setSeeding(false); });
  };

  useEffect(refresh, []);

  return (
    <div className="afrec-shell">
      <ResponsiveStyle />
      <aside className="afrec-sidebar">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <span style={{ ...sectionLabel, margin: 0 }}>Runs</span>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={loadExamples} disabled={seeding}
              style={{ ...btnGhost, padding: '4px 10px', fontSize: 11, opacity: seeding ? 0.6 : 1 }}>
              {seeding ? 'Loading…' : '★ Load examples'}
            </button>
            <button onClick={refresh} style={{ ...btnGhost, padding: '4px 10px', fontSize: 11 }}>↻ Refresh</button>
          </div>
        </div>
        {error && <div style={{ color: T.red, fontSize: 12, marginBottom: 8 }}>Could not reach the API: {error}</div>}
        {!runs && !error && <div style={{ color: T.soft, fontSize: 13 }}>Loading runs…</div>}
        {(() => {
          if (!runs) return null;
          const own = runs.filter((r) => !isDemoRun(r));
          const demo = runs.filter(isDemoRun);
          const row = (r: ApiRun) => (
            <div
              key={r.run_id}
              onClick={() => onSelectRun(r.run_id)}
              style={{
                padding: '10px 12px', borderRadius: 10, cursor: 'pointer', marginBottom: 6,
                border: `1px solid ${selectedRun === r.run_id ? T.accent : T.border}`,
                background: selectedRun === r.run_id ? T.accentSoft : '#fff',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {isDemoRun(r) && (
                  <span style={{ background: T.amberSoft, color: T.amber, fontSize: 9, fontWeight: 700, borderRadius: 999, padding: '1px 7px', letterSpacing: 0.4, flexShrink: 0 }}>
                    DEMO
                  </span>
                )}
                <span style={{ fontSize: 12.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: T.text, flex: 1 }}>{r.task}</span>
              </div>
              <div style={{ marginTop: 5 }}><span style={statusPill(r.status)}>{r.status}</span></div>
            </div>
          );
          return (
            <div>
              <div style={{ ...sectionLabel, marginTop: 8 }}>Your runs ({own.length})</div>
              {own.length === 0 && (
                <div style={{ color: T.faint, fontSize: 12.5, lineHeight: 1.6, marginBottom: 8 }}>
                  No runs from your own agents yet. Stream one with the SDK or raw HTTP (snippets on the About page) and it lands here, never mixed with the demo runs.
                </div>
              )}
              {own.map(row)}
              <div style={{ ...sectionLabel, marginTop: 16 }}>Demo runs ({demo.length})</div>
              {demo.length === 0 && (
                <div style={{ color: T.faint, fontSize: 12.5, lineHeight: 1.6 }}>
                  No demo runs loaded. Use the Load examples button above to seed the six canonical examples.
                </div>
              )}
              {demo.map(row)}
            </div>
          );
        })()}
        {runs?.length === 0 && <div style={{ color: T.faint, fontSize: 13 }}>No runs yet. Record one with the SDK, or load the demo examples.</div>}
      </aside>
      <main className="afrec-main">
        {selectedRun
          ? <RunDetail runId={selectedRun} otherRuns={(runs ?? []).filter((r) => r.run_id !== selectedRun)} />
          : <div style={{ padding: 20, color: T.faint }}>Select a run from the left to inspect it.</div>}
      </main>
    </div>
  );
}

/* ---------- app root ---------- */

function App() {
  const [route] = useHashRoute();
  const [selectedRun, setSelectedRun] = useState<string | null>(null);

  // Support deep links like #/runs/<run_id>
  useEffect(() => {
    const m = route.match(/^#\/runs\/(.+)$/);
    if (m) setSelectedRun(m[1]);
    else if (route === '#/runs' || route === '#/') {
      // keep current selection when navigating between list and detail
    }
  }, [route]);

  return (
    <div style={{ background: T.bg, minHeight: '100vh' }}>
      <Header route={route.replace(/^#/, '')} />
      {route === '' || route === '#/' || route === '#' ? (
        <AboutPage />
      ) : (
        <DashboardPage selectedRun={selectedRun} onSelectRun={setSelectedRun} />
      )}
    </div>
  );
}

const RESPONSIVE_CSS = `
  .afrec-shell { display: flex; height: calc(100vh - 59px); }
  .afrec-sidebar {
    width: 300px; flex-shrink: 0; border-right: 1px solid #e4e8f2; background: #fff;
    overflow-y: auto; padding: 12px;
  }
  .afrec-main { flex: 1; overflow: hidden; }
  @media (max-width: 800px) {
    .afrec-shell { flex-direction: column; height: auto; }
    .afrec-sidebar {
      width: 100%; max-height: 240px; border-right: none; border-bottom: 1px solid #e4e8f2;
    }
    .afrec-main { overflow: visible; }
    .afrec-main > div { height: auto; }
  }
`;

function ResponsiveStyle() {
  return <style>{RESPONSIVE_CSS}</style>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
