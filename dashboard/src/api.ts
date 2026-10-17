/** API client for the ingestion API (Phase 2). */

const API_BASE = (import.meta as unknown as { env: Record<string, string | undefined> }).env?.VITE_API_URL ?? '';

export interface ApiRun {
  run_id: string;
  task: string;
  started_at: number;
  ended_at: number | null;
  status: string;
  tags: Record<string, unknown>;
}

export interface ApiEvent {
  event_id: string | null;
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

export async function listRuns(): Promise<ApiRun[]> {
  const res = await fetch(`${API_BASE}/runs`);
  if (!res.ok) throw new Error(`GET /runs failed: ${res.status}`);
  return res.json();
}

export async function getRun(runId: string): Promise<{ run: ApiRun; events: ApiEvent[] }> {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(runId)}`);
  if (!res.ok) throw new Error(`GET /runs/${runId} failed: ${res.status}`);
  return res.json();
}


export interface ApiEvidence {
  event_id: string | null;
  kind: string;
  t_offset_ms: number | null;
  excerpt: string;
}

export interface ApiFinding {
  rule: string;
  severity: 'critical' | 'warning' | 'info';
  observed: string;
  hypothesis: string;
  evidence: ApiEvidence[];
}

export interface ApiInvestigation {
  run_id: string;
  clean: boolean;
  finding_count: number;
  findings: ApiFinding[];
  note: string;
}

/** Phase 6: evidence-linked failure investigation for one run.
 *  Optional baseline run adds the earliest-divergence finding. */
export async function investigateRun(runId: string, baselineRunId?: string): Promise<ApiInvestigation> {
  const url = baselineRunId
    ? `${API_BASE}/runs/${encodeURIComponent(runId)}/investigate?baseline_run_id=${encodeURIComponent(baselineRunId)}`
    : `${API_BASE}/runs/${encodeURIComponent(runId)}/investigate`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`investigate failed: ${res.status}`);
  return res.json();
}

export async function seedExamples(): Promise<{ ok: boolean; seeded: string[] }> {
  const res = await fetch(`${API_BASE}/demo/seed`, { method: 'POST' });
  if (!res.ok) throw new Error(`POST /demo/seed failed: ${res.status}`);
  return res.json();
}
