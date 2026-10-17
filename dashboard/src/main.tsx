import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { listRuns, getRun, investigateRun, seedExamples, getProfile, ApiRun, ApiEvent, ApiInvestigation, ApiProfile } from './api';
import { sortFindings, countBySeverity } from './trace';
import { buildSpanTree, summarizeCost, runDuration, spanTokens, SpanNode, decisionSequence, firstDivergence, groupSpans } from './trace';

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
          'The investigator runs twelve deterministic rules over each run: crashes, runs that never ended, errors, unclosed spans, tool calls without results, conflicting records, stale-value consumption, empty-search conclusions, token capture gaps, repeated decisions, and baseline comparison against a healthy run (identical or diverging).')}
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


      <h2 style={{ fontSize: 20, fontWeight: 800, letterSpacing: -0.4, margin: '40px 0 6px' }}>Security, honestly</h2>
      <p style={{ fontSize: 14, color: T.soft, margin: '0 0 16px' }}>
        Straight answers, because a security reviewer will ask.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
        {[
          ['Is the hosted API authenticated?', 'No. This deployment accepts runs from anyone and shows all runs to anyone. "Your runs" means runs streamed by real agents, as opposed to the seeded demo runs — it is not a per-user partition.'],
          ['Should I point my agent at it?', 'For demos, evals and toy projects, yes. For anything with real customer data, run the ingestion API yourself: it is one repo, one DATABASE_URL, and your data never leaves infrastructure you control.'],
          ['Are secrets redacted?', 'Yes, twice. The SDK redacts before sending (API keys, bearer tokens, password-like fields), and the API redacts again on ingest, so a client that forgot still cannot leak through. Traces store what your agent actually did, minus known secret shapes.'],
          ['What about prompts and task text?', 'They are stored as-is, because a flight recorder that drops the prompt cannot explain the failure. Assume anything you stream is visible to whoever has the API URL, and self-host when that is not acceptable.'],
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

/* ---------- span timeline (grouped repeats, duration bars, cost chips) ---------- */

function fmtCost(v: number | null | undefined): string {
  if (v == null) return 'unknown';
  if (v === 0) return '$0';
  if (v < 0.01) return `$${v.toFixed(4)}`;
  if (v < 1) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(2)}`;
}

/** Duration meter: proportional fill so slow steps stand out at a glance. */
function DurationBar({ node, barMax }: { node: SpanNode; barMax: number }) {
  if (node.duration_ms == null) return null;
  const pct = Math.min(100, (node.duration_ms / barMax) * 100);
  const color = KIND_COLORS[node.kind] ?? KIND_COLORS.span;
  return (
    <div style={{ width: 72, height: 5, background: '#edf0f6', borderRadius: 999, flexShrink: 0, overflow: 'hidden' }}>
      <div style={{ width: `${Math.max(6, pct)}%`, height: '100%', background: color, borderRadius: 999 }} />
    </div>
  );
}

function SpanRow({ node, depth, onSelect, selectedId, barMax, costUsd }: {
  node: SpanNode; depth: number; onSelect: (n: SpanNode) => void; selectedId: string | null;
  barMax: number; costUsd: number | null | undefined;
}) {
  const color = KIND_COLORS[node.kind] ?? KIND_COLORS.span;
  return (
    <div>
      <div
        onClick={() => onSelect(node)}
        className="afrec-spanrow"
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '7px 10px', marginLeft: depth * 18, cursor: 'pointer',
          borderRadius: 8,
          background: selectedId === node.span_id ? T.accentSoft : 'transparent',
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: 4, background: color, flexShrink: 0 }} />
        <span style={{ flex: 1, ...mono, fontSize: 12.5, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.label}</span>
        {spanTokens(node.events) && (
          <span style={{ color: T.blue, fontSize: 11.5, fontWeight: 500, background: T.blueSoft, borderRadius: 999, padding: '2px 8px', flexShrink: 0 }}>
            {spanTokens(node.events)!.input.toLocaleString()} in / {spanTokens(node.events)!.output.toLocaleString()} out
          </span>
        )}
        {costUsd !== undefined && (
          <span style={{ color: T.green, fontSize: 11, fontWeight: 600, background: T.greenSoft, borderRadius: 999, padding: '2px 8px', flexShrink: 0 }}>
            {fmtCost(costUsd)}
          </span>
        )}
        <DurationBar node={node} barMax={barMax} />
        {node.duration_ms != null
          ? <span style={{ color: T.faint, fontSize: 11.5, width: 64, textAlign: 'right', flexShrink: 0 }}>{node.duration_ms >= 1000 ? `${(node.duration_ms / 1000).toFixed(2)} s` : `${node.duration_ms.toFixed(0)} ms`}</span>
          : <span style={{ color: T.red, fontSize: 11.5, fontWeight: 600, flexShrink: 0 }}>open / crashed</span>}
        <span className="afrec-hint" style={{ color: T.accent, fontSize: 10.5, fontWeight: 600, flexShrink: 0 }}>inspect ▸</span>
      </div>
      {node.children.map((c) => (
        <SpanRow key={c.span_id} node={c} depth={depth + 1} onSelect={onSelect} selectedId={selectedId} barMax={barMax}
          costUsd={c.span_id === node.span_id ? costUsd : undefined} />
      ))}
    </div>
  );
}

/** A run of consecutive identical decisions renders as one row with a ×N chip. */
function SpanGroupRow({ group, depth, onSelect, selectedId, barMax, spanCosts }: {
  group: ReturnType<typeof groupSpans>[number]; depth: number;
  onSelect: (n: SpanNode) => void; selectedId: string | null;
  barMax: number; spanCosts: Map<string, number | null>;
}) {
  const [open, setOpen] = useState(false);
  const n = group.nodes.length;
  const totalTokens = group.nodes.reduce((acc, s) => {
    const tk = spanTokens(s.events);
    return tk ? { input: acc.input + tk.input, output: acc.output + tk.output } : acc;
  }, { input: 0, output: 0 });
  const totalDur = group.nodes.reduce((acc, s) => acc + (s.duration_ms ?? 0), 0);
  const totalCost = group.nodes.reduce((acc, s) => acc + (spanCosts.get(s.span_id) ?? 0), 0);
  const anyCost = group.nodes.some((s) => spanCosts.has(s.span_id));
  const head = group.nodes[0];
  const color = KIND_COLORS[head.kind] ?? KIND_COLORS.span;

  if (n === 1) {
    return <SpanRow node={head} depth={depth} onSelect={onSelect} selectedId={selectedId} barMax={barMax}
      costUsd={spanCosts.has(head.span_id) ? spanCosts.get(head.span_id) : undefined} />;
  }
  return (
    <div>
      <div
        className="afrec-spanrow"
        onClick={() => setOpen(!open)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '7px 10px', marginLeft: depth * 18, cursor: 'pointer', borderRadius: 8,
          background: open ? '#f6f8fe' : 'transparent',
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: 4, background: color, flexShrink: 0 }} />
        <span style={{ ...mono, fontSize: 12.5, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{head.label}</span>
        <span style={{ background: T.amberSoft, color: T.amber, fontSize: 10.5, fontWeight: 700, borderRadius: 999, padding: '2px 8px', flexShrink: 0 }}>
          ×{n} repeated
        </span>
        <span style={{ flex: 1 }} />
        {(totalTokens.input > 0 || totalTokens.output > 0) && (
          <span style={{ color: T.blue, fontSize: 11.5, fontWeight: 500, background: T.blueSoft, borderRadius: 999, padding: '2px 8px', flexShrink: 0 }}>
            {totalTokens.input.toLocaleString()} in / {totalTokens.output.toLocaleString()} out
          </span>
        )}
        {anyCost && (
          <span style={{ color: T.green, fontSize: 11, fontWeight: 600, background: T.greenSoft, borderRadius: 999, padding: '2px 8px', flexShrink: 0 }}>
            {fmtCost(totalCost)}
          </span>
        )}
        <span style={{ color: T.faint, fontSize: 11.5, width: 64, textAlign: 'right', flexShrink: 0 }}>{(totalDur / 1000).toFixed(2)} s</span>
        <span className="afrec-hint" style={{ color: T.accent, fontSize: 10.5, fontWeight: 600, flexShrink: 0 }}>{open ? 'collapse ▾' : 'expand ▸'}</span>
      </div>
      {open && group.nodes.map((s) => (
        <SpanRow key={s.span_id} node={s} depth={depth + 1} onSelect={onSelect} selectedId={selectedId} barMax={barMax}
          costUsd={spanCosts.has(s.span_id) ? spanCosts.get(s.span_id) : undefined} />
      ))}
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

function InvestigationPanel({ runId, otherRuns, runTags, onCompare }: {
  runId: string; otherRuns: ApiRun[]; runTags: Record<string, unknown> | undefined;
  onCompare: (baselineRunId: string) => void;
}) {
  const [inv, setInv] = useState<ApiInvestigation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<string>('');

  // The divergence demo always investigates against its healthy pair — the
  // headline finding should never sit behind a manual dropdown.
  const naturalBaseline = otherRuns.find(
    (r) => (r.tags as Record<string, unknown> | undefined)?.shape === 'baseline-good');
  const autoBaseline = runTags?.shape === 'baseline-bad' && naturalBaseline
    ? naturalBaseline.run_id : '';

  const run = async (b: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await investigateRun(runId, b || undefined);
      setInv(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setInv(null);
    setBaseline(autoBaseline);
    run(autoBaseline);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return (
    <div>
      <div style={sectionLabel}>Investigation</div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={() => run(baseline)} disabled={busy} style={{ ...btn, opacity: busy ? 0.6 : 1 }}>
          {busy ? 'Investigating…' : 'Investigate this run'}
        </button>
        <select
          value={baseline}
          onChange={(e) => { setBaseline(e.target.value); run(e.target.value); }}
          style={{ background: '#fff', color: T.text, border: `1px solid ${T.border}`, borderRadius: 8, padding: '7px 10px', fontSize: 12, maxWidth: 220 }}
        >
          <option value="">no baseline (single-run rules)</option>
          {otherRuns.map((r) => (
            <option key={r.run_id} value={r.run_id}>baseline: {r.task.slice(0, 40)}</option>
          ))}
        </select>
        {baseline && (
          <button onClick={() => onCompare(baseline)} style={btnGhost}>
            ⇄ Compare side by side
          </button>
        )}
      </div>
      {busy && !inv && <div style={{ color: T.soft, fontSize: 12.5 }}>Investigating…</div>}
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

/* ---------- side-by-side baseline comparison ---------- */

const DECISION_ICONS: Record<string, string> = {
  tool_call: '🔧', llm_call: '✳', llm_payload: '↩', tool_payload: '↩',
};

function CompareCell({ row, missing }: { row: ReturnType<typeof decisionSequence>[number] | undefined; missing?: boolean }) {
  if (!row || missing) {
    return <div style={{ ...mono, fontSize: 11.5, color: T.faint, padding: '6px 8px' }}>— run ended here</div>;
  }
  return (
    <div style={{ padding: '6px 8px' }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{ fontSize: 11 }}>{DECISION_ICONS[row.type] ?? '·'}</span>
        <span style={{ ...mono, fontSize: 12, fontWeight: 600, color: T.text }}>{row.label}</span>
      </div>
      {row.detail && (
        <div style={{ ...mono, fontSize: 10.5, color: T.soft, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {row.detail}
        </div>
      )}
    </div>
  );
}

function CompareView({ baselineId, runId, onClose }: { baselineId: string; runId: string; onClose: () => void }) {
  const [pair, setPair] = useState<{ a: { run: ApiRun; events: ApiEvent[] }; b: { run: ApiRun; events: ApiEvent[] } } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getRun(baselineId), getRun(runId)])
      .then(([a, b]) => { if (!cancelled) setPair({ a, b }); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [baselineId, runId]);

  if (error) return <div style={{ padding: 12, color: T.red, ...card, marginBottom: 12 }}>Compare failed: {error}</div>;
  if (!pair) return <div style={{ padding: 12, color: T.soft, ...card, marginBottom: 12 }}>Loading comparison…</div>;

  const seqA = decisionSequence(pair.a.events);
  const seqB = decisionSequence(pair.b.events);
  const div = firstDivergence(seqA, seqB);
  const n = Math.max(seqA.length, seqB.length);

  return (
    <div style={{ ...card, padding: 14, marginBottom: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div>
          <div style={sectionLabel}>Side-by-side comparison</div>
          <div style={{ fontSize: 12, color: T.soft }}>
            <span style={{ color: T.green, fontWeight: 600 }}>baseline:</span> {pair.a.run.task.slice(0, 48)}
            {' vs '}
            <span style={{ color: T.accent, fontWeight: 600 }}>this run:</span> {pair.b.run.task.slice(0, 48)}
          </div>
        </div>
        <button onClick={onClose} style={{ ...btnGhost, padding: '4px 10px', fontSize: 11 }}>✕ close</button>
      </div>

      {div == null ? (
        <div style={{ background: T.greenSoft, color: T.green, borderRadius: 8, padding: 10, fontSize: 12.5 }}>
          The two runs made identical decisions — same tools, same arguments, same models. No divergence to show.
        </div>
      ) : (
        <div style={{ background: T.redSoft, color: T.red, borderRadius: 8, padding: 10, fontSize: 12.5, marginBottom: 10, lineHeight: 1.5 }}>
          <b>First divergence at decision #{div + 1}.</b> Everything before it is identical; everything after it is fallout, not cause.
        </div>
      )}

      <div style={{ border: `1px solid ${T.border}`, borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '44px 1fr 1fr', background: '#f6f8fc', borderBottom: `1px solid ${T.border}` }}>
          <div style={{ padding: '6px 8px', fontSize: 10.5, fontWeight: 700, color: T.faint }}>#</div>
          <div style={{ padding: '6px 8px', fontSize: 10.5, fontWeight: 700, color: T.faint, borderLeft: `1px solid ${T.border}` }}>BASELINE</div>
          <div style={{ padding: '6px 8px', fontSize: 10.5, fontWeight: 700, color: T.faint, borderLeft: `1px solid ${T.border}` }}>THIS RUN</div>
        </div>
        {Array.from({ length: n }, (_, i) => {
          const a = seqA[i];
          const b = seqB[i];
          const same = a?.fingerprint === b?.fingerprint;
          const isDiv = div != null && i === div;
          const after = div != null && i > div && !same;
          const bg = isDiv ? T.redSoft : after ? '#fdf7f0' : 'transparent';
          const border = isDiv ? `2px solid ${T.red}` : after ? `1px solid ${T.amber}` : `1px solid transparent`;
          return (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '44px 1fr 1fr', background: bg, borderTop: border }}>
              <div style={{ padding: '6px 8px', ...mono, fontSize: 11, color: same ? T.faint : isDiv ? T.red : T.amber, fontWeight: isDiv ? 700 : 500 }}>
                {isDiv ? '▶' : ''}{i + 1}
              </div>
              <div style={{ borderLeft: `1px solid ${T.border}` }}><CompareCell row={a} missing={!a} /></div>
              <div style={{ borderLeft: `1px solid ${T.border}` }}><CompareCell row={b} missing={!b} /></div>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 8, fontSize: 11, color: T.faint, lineHeight: 1.5 }}>
        Rows are the same decision fingerprint the server's diff uses (tool calls by name and arguments, model calls, payloads).
        <span style={{ background: T.redSoft, color: T.red, borderRadius: 4, padding: '0 4px' }}> red = where they parted ways</span>,
        <span style={{ background: '#fdf7f0', color: T.amber, borderRadius: 4, padding: '0 4px' }}> amber = later differences</span>.
      </div>
    </div>
  );
}

function maxSpanDuration(nodes: SpanNode[]): number {
  let mx = 0;
  const walk = (n: SpanNode) => {
    if (n.duration_ms != null && n.duration_ms > mx) mx = n.duration_ms;
    n.children.forEach(walk);
  };
  nodes.forEach(walk);
  return mx;
}

function RunDetail({ runId, otherRuns }: { runId: string; otherRuns: ApiRun[] }) {
  const [data, setData] = useState<{ run: ApiRun; events: ApiEvent[] } | null>(null);
  const [profile, setProfile] = useState<ApiProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SpanNode | null>(null);
  const [compareWith, setCompareWith] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCompareWith(null);
    getRun(runId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(String(e)); });
    getProfile(runId)
      .then((pr) => { if (!cancelled) setProfile(pr); })
      .catch(() => { if (!cancelled) setProfile(null); }); // cost display is best-effort
    return () => { cancelled = true; };
  }, [runId]);

  if (error) return <div style={{ padding: 20, color: T.red }}>Failed to load run: {error}</div>;
  if (!data) return <div style={{ padding: 20, color: T.soft }}>Loading run…</div>;

  const tree = buildSpanTree(data.events);
  const cost = summarizeCost(data.events);
  const duration = runDuration(data.events);
  const runEvents = data.events.filter((e) => !e.span_id);
  const barMax = Math.max(1, maxSpanDuration(tree));
  const spanCosts = new Map<string, number | null>();
  profile?.llm_calls.forEach((c) => { if (c.span_id) spanCosts.set(c.span_id, c.estimated_cost_usd); });

  // per-model breakdown from the server's own estimator
  const byModel = new Map<string, { calls: number; input: number; output: number; usd: number }>();
  profile?.llm_calls.forEach((c) => {
    const m = byModel.get(c.model) ?? { calls: 0, input: 0, output: 0, usd: 0 };
    m.calls += 1;
    m.input += c.input_tokens ?? 0;
    m.output += c.output_tokens ?? 0;
    m.usd += c.estimated_cost_usd ?? 0;
    byModel.set(c.model, m);
  });

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
        <Stat label="Est. cost" value={profile ? fmtCost(profile.totals.estimated_cost_usd) : '…'} tone="green" />
      </div>
      {profile && byModel.size > 0 && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
          {[...byModel.entries()].map(([model, m]) => (
            <span key={model} style={{ ...mono, fontSize: 11.5, color: T.soft, background: '#f1f4fa', borderRadius: 999, padding: '3px 10px' }}>
              {model} · {m.calls} call{m.calls === 1 ? '' : 's'} · {m.input.toLocaleString()} in / {m.output.toLocaleString()} out · {fmtCost(m.usd)}
            </span>
          ))}
        </div>
      )}
      {profile && profile.totals.unknown_cost_calls > 0 && (
        <div style={{ background: '#f4f6fa', color: T.soft, borderRadius: 8, padding: 10, fontSize: 12.5, marginBottom: 16 }}>
          {profile.totals.unknown_cost_calls} call{profile.totals.unknown_cost_calls === 1 ? '' : 's'} cost unknown
          {profile.unknown_pricing_models.length > 0 && <> (no published rate for {profile.unknown_pricing_models.join(', ')})</>} — excluded from the estimate, never guessed as zero.
        </div>
      )}
      {cost.unknown_usage && (
        <div style={{ background: T.amberSoft, color: T.amber, borderRadius: 8, padding: 10, fontSize: 12.5, marginBottom: 16 }}>
          ⚠ Some LLM calls report no token usage — cost data for those is unknown, not zero.
        </div>
      )}

      <div style={sectionLabel}>Timeline <span style={{ textTransform: 'none', letterSpacing: 0, color: T.faint, fontWeight: 500 }}>(identical repeats collapsed — click to expand, click any row to inspect its inputs and outputs)</span></div>
      <div style={{ ...card, padding: 8 }}>
        {tree.length === 0 && <div style={{ color: T.faint, fontSize: 13, padding: 8 }}>No spans recorded for this run.</div>}
        {groupSpans(tree).map((g) => (
          <SpanGroupRow key={g.key + g.nodes[0].span_id} group={g} depth={0} onSelect={setSelected}
            selectedId={selected?.span_id ?? null} barMax={barMax} spanCosts={spanCosts} />
        ))}
      </div>

      {compareWith && (
        <CompareView baselineId={compareWith} runId={runId} onClose={() => setCompareWith(null)} />
      )}

      <InvestigationPanel runId={runId} otherRuns={otherRuns} runTags={data.run.tags} onCompare={setCompareWith} />

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
    fires: 'baseline_divergence (critical) — this run auto-compares against demo-5, and the side-by-side view pins the exact call where the two runs parted ways.',
  },
  'demo-7-token-gap': {
    title: 'A missing usage field',
    shows: 'The second model call came back through a wrapper that dropped the usage block. The recorder keeps the response but refuses to invent token numbers for it.',
    fires: 'token_capture_gap (info) — and the cost banner says those tokens are unknown, not zero.',
  },
  'demo-8-conflicting': {
    title: 'Two records that disagree',
    shows: 'The archive says pay_881 was UNPAID as of 2025-09-01; the live record says PAID as of 2025-10-10. The agent sees both and correctly uses the fresh one.',
    fires: 'conflicting_records (info) — an observation that old and fresh records coexist. No failure here: the newer value won.',
  },
  'demo-9-stale-value': {
    title: 'A stale value actually consumed',
    shows: 'The agent looks up the surcharge rate twice (fiscal years 2024-25 and 2025-26), then computes with the OLD 0.1 rate even though the fresh 0.15 is sitting in its own trace.',
    fires: 'stale_consumption (warning) — the calculator call consumed the stale rate while the fresher value was on record. Plus conflicting_records (info) for the two rates.',
  },
  'demo-10-empty-search': {
    title: 'An empty-search conclusion',
    shows: 'Both searches return zero rows, and the agent answers "no matching transactions found, no refund due." Empty results describe the queries, not the corpus.',
    fires: 'empty_search_conclusion (warning) — the run may have concluded "no data exists" from its own failed retrieval.',
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
                  No demo runs loaded. Use the Load examples button above to seed the ten canonical examples.
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
  .afrec-spanrow { position: relative; }
  .afrec-spanrow:hover { background: #f2f5fd; }
  .afrec-spanrow .afrec-hint { visibility: hidden; }
  .afrec-spanrow:hover .afrec-hint { visibility: visible; }
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
