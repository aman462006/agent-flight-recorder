import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { listRuns, getRun, ApiRun, ApiEvent } from './api';
import { buildSpanTree, summarizeCost, runDuration, SpanNode } from './trace';

const KIND_COLORS: Record<string, string> = {
  llm: '#f0b429',
  tool: '#7ec8e3',
  span: '#9aa5b1',
  error: '#e5484d',
};

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
          padding: '6px 8px', marginLeft: depth * 18, cursor: 'pointer',
          borderRadius: 6,
          background: selectedId === node.span_id ? '#1c2430' : 'transparent',
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: 4, background: color, flexShrink: 0 }} />
        <span style={{ flex: 1, fontFamily: 'ui-monospace, monospace', fontSize: 13 }}>{node.label}</span>
        {node.duration_ms != null
          ? <span style={{ color: '#8b96a5', fontSize: 12 }}>{node.duration_ms.toFixed(1)} ms</span>
          : <span style={{ color: '#e5484d', fontSize: 12 }}>open / crashed</span>}
      </div>
      {node.children.map((c) => <SpanRow key={c.span_id} node={c} depth={depth + 1} onSelect={onSelect} selectedId={selectedId} />)}
    </div>
  );
}

function EventInspector({ events }: { events: ApiEvent[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div style={{ color: '#8b96a5', fontSize: 12, marginBottom: 4 }}>
        {events.length} event{events.length === 1 ? '' : 's'}
      </div>
      {events.map((ev) => (
        <div key={ev.event_id ?? `${ev.kind}-${ev.t_offset_ms}`} style={{ marginBottom: 6 }}>
          <button
            onClick={() => setOpen(!open)}
            style={{ all: 'unset', cursor: 'pointer', color: KIND_COLORS[ev.kind.split('.')[0]] ?? '#dbe2ef', fontFamily: 'ui-monospace, monospace', fontSize: 12 }}
          >
            {open ? '▾' : '▸'} {ev.kind}
          </button>
          {open && (
            <pre style={{ background: '#0f141c', padding: 10, borderRadius: 6, overflowX: 'auto', fontSize: 12, margin: '4px 0' }}>
              {JSON.stringify(ev.data, null, 2)}
            </pre>
          )}
        </div>
      ))}
      {events.length === 0 && <div style={{ color: '#5c6773', fontSize: 13 }}>No payload events on this span.</div>}
    </div>
  );
}

function RunDetail({ runId }: { runId: string }) {
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

  if (error) return <div style={{ color: '#e5484d' }}>Failed to load run: {error}</div>;
  if (!data) return <div style={{ color: '#8b96a5' }}>Loading run…</div>;

  const tree = buildSpanTree(data.events);
  const cost = summarizeCost(data.events);
  const duration = runDuration(data.events);
  const runEvents = data.events.filter((e) => !e.span_id);

  return (
    <div style={{ padding: 20, overflowY: 'auto', height: '100%' }}>
      <h2 style={{ margin: '0 0 4px', fontSize: 17 }}>{data.run.task}</h2>
      <div style={{ color: '#8b96a5', fontSize: 12, marginBottom: 16 }}>
        run {data.run.run_id} · {data.run.status}
        {duration != null ? ` · ${(duration / 1000).toFixed(2)}s` : ' · duration unknown (partial run)'}
      </div>

      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
        <Stat label="LLM calls" value={String(cost.llm_calls)} />
        <Stat label="Tool calls" value={String(cost.tool_calls)} />
        <Stat label="Input tokens" value={cost.input_tokens.toLocaleString()} />
        <Stat label="Output tokens" value={cost.output_tokens.toLocaleString()} />
        {cost.cached_tokens > 0 && <Stat label="Cached tokens" value={cost.cached_tokens.toLocaleString()} />}
      </div>
      {cost.unknown_usage && (
        <div style={{ color: '#f0b429', fontSize: 12, marginBottom: 16 }}>
          ⚠ Some LLM calls report no token usage — cost data for those is unknown, not zero.
        </div>
      )}

      <h3 style={{ fontSize: 13, color: '#8b96a5', margin: '16px 0 8px', textTransform: 'uppercase', letterSpacing: 0.5 }}>Timeline</h3>
      <div style={{ background: '#0f141c', borderRadius: 8, padding: 8 }}>
        {tree.length === 0 && <div style={{ color: '#5c6773', fontSize: 13, padding: 8 }}>No spans recorded for this run.</div>}
        {tree.map((s) => <SpanRow key={s.span_id} node={s} depth={0} onSelect={setSelected} selectedId={selected?.span_id ?? null} />)}
      </div>

      <h3 style={{ fontSize: 13, color: '#8b96a5', margin: '16px 0 8px', textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {selected ? `Span: ${selected.label}` : 'Run-level events'}
      </h3>
      <div style={{ background: '#0f141c', borderRadius: 8, padding: 12 }}>
        <EventInspector events={selected ? selected.events : runEvents} />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: '#0f141c', borderRadius: 8, padding: '10px 14px', minWidth: 110 }}>
      <div style={{ color: '#8b96a5', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 600 }}>{value}</div>
    </div>
  );
}

function App() {
  const [runs, setRuns] = useState<ApiRun[] | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listRuns()
      .then((r) => { setRuns(r); if (r.length > 0) setSelectedRun(r[0].run_id); })
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      <aside style={{ width: 300, borderRight: '1px solid #1c2430', overflowY: 'auto', padding: 12 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12, letterSpacing: 0.3 }}>
          ✈ Agent Flight Recorder
        </div>
        {error && <div style={{ color: '#e5484d', fontSize: 12 }}>API unreachable: {error}</div>}
        {runs === null && !error && <div style={{ color: '#8b96a5', fontSize: 13 }}>Loading runs…</div>}
        {runs?.map((r) => (
          <div
            key={r.run_id}
            onClick={() => setSelectedRun(r.run_id)}
            style={{
              padding: 10, borderRadius: 8, cursor: 'pointer', marginBottom: 6,
              background: selectedRun === r.run_id ? '#1c2430' : 'transparent',
            }}
          >
            <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.task}</div>
            <div style={{ color: r.status === 'crashed' ? '#e5484d' : r.status === 'completed' ? '#4cc38a' : '#8b96a5', fontSize: 11 }}>
              {r.status}
            </div>
          </div>
        ))}
        {runs?.length === 0 && <div style={{ color: '#5c6773', fontSize: 13 }}>No runs yet. Record one with the SDK.</div>}
      </aside>
      <main style={{ flex: 1 }}>
        {selectedRun ? <RunDetail runId={selectedRun} /> : <div style={{ padding: 20, color: '#5c6773' }}>Select a run.</div>}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
