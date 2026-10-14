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
