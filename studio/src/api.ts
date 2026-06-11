// API client — thin fetch wrappers, no external dependencies

const BASE = "/api";

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── Types ──────────────────────────────────────────────────────────────────────

export interface Stats {
  new_candidates: number;
  queue_size: number;
  posted_count: number;
}

export interface Candidate {
  candidate_id: string;
  detector: string;
  subject: string | null;
  as_of: string;
  novelty_score: number;
  status: string;
  facts: Record<string, unknown>;
  claim_scope: string;
  coverage_window: string;
  payload_kind: string;
  has_draft: boolean;
  has_card: boolean;
}

export interface Draft {
  draft_id: string;
  candidate_id: string;
  status: string;
  text: string;
  char_count: number;
  has_card: boolean;
  interesting_judgment: string | null;
  is_projection: boolean;
  created_at: string;
  detector: string;
  novelty_score: number;
  facts: Record<string, unknown>;
  claim_scope: string;
}

export interface ExplorerResult {
  columns: string[];
  rows: unknown[][];
  row_count?: number;
  error?: string;
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

export const api = {
  stats: (): Promise<Stats> =>
    fetch(`${BASE}/stats`).then((r) => _json<Stats>(r)),

  candidates: (status = "new"): Promise<Candidate[]> =>
    fetch(`${BASE}/candidates?status=${status}`).then((r) =>
      _json<Candidate[]>(r),
    ),

  renderCard: (id: string): Promise<{ card_path: string }> =>
    fetch(`${BASE}/candidates/${id}/render`, { method: "POST" }).then((r) =>
      _json(r),
    ),

  cardUrl: (id: string): string =>
    `${BASE}/candidates/${id}/card.png?t=${Date.now()}`,

  drafts: (status = "pending,verified,approved"): Promise<Draft[]> =>
    fetch(`${BASE}/drafts?status=${status}`).then((r) => _json<Draft[]>(r)),

  updateDraftText: (
    id: string,
    text: string,
  ): Promise<{ saved: boolean; digit_audit_errors: string[]; detail?: string }> =>
    fetch(`${BASE}/drafts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => _json(r)),

  approveDraft: (id: string): Promise<{ status: string }> =>
    fetch(`${BASE}/drafts/${id}/approve`, { method: "POST" }).then((r) =>
      _json(r),
    ),

  rejectDraft: (id: string): Promise<{ status: string }> =>
    fetch(`${BASE}/drafts/${id}/reject`, { method: "POST" }).then((r) =>
      _json(r),
    ),

  explorerViews: (): Promise<string[]> =>
    fetch(`${BASE}/explorer/views`).then((r) => _json<string[]>(r)),

  explorerQuery: (view: string): Promise<ExplorerResult> =>
    fetch(`${BASE}/explorer/${view}`).then((r) => _json<ExplorerResult>(r)),
};
