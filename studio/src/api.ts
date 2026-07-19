// API client — thin fetch wrappers, no external dependencies.
//
// The app reads the engine's output and hands prompts out; it never calls a
// model. Results come back through /results, where the server's gates decide.

import type {
  Board,
  BoardLead,
  Candidate,
  CoverageReport,
  Draft,
  GateOutcome,
  JobState,
  PostedItem,
  Predictions,
  Prompt,
  Stats,
} from "./types.ts";

const BASE = "/api";

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

function _post<T>(path: string, body?: unknown): Promise<T> {
  return fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => _json<T>(r));
}

export const api = {
  // ── Board ──────────────────────────────────────────────────────────────────
  board: (): Promise<Board> => fetch(`${BASE}/board`).then((r) => _json<Board>(r)),

  // created_at busts the cache: a re-render reuses the URL with new pixels.
  cardImageUrl: (id: string, version?: string): string =>
    `${BASE}/board/cards/${id}/image.png${version ? `?v=${encodeURIComponent(version)}` : ""}`,

  setCardStatus: (id: string, status: string): Promise<{ status: string }> =>
    _post(`/board/cards/${id}/status`, { status }),

  setLeadStatus: (id: string, status: string): Promise<{ status: string }> =>
    _post(`/board/leads/${id}/status`, { status }),

  // ── Desk context ───────────────────────────────────────────────────────────
  stats: (): Promise<Stats> => fetch(`${BASE}/stats`).then((r) => _json<Stats>(r)),

  coverage: (): Promise<CoverageReport[]> =>
    fetch(`${BASE}/coverage`).then((r) => _json<CoverageReport[]>(r)),

  predictions: (): Promise<Predictions> =>
    fetch(`${BASE}/predictions`).then((r) => _json<Predictions>(r)),

  posted: (): Promise<PostedItem[]> =>
    fetch(`${BASE}/posted`).then((r) => _json<PostedItem[]>(r)),

  // ── Triage ─────────────────────────────────────────────────────────────────
  candidates: (status = "new"): Promise<Candidate[]> =>
    fetch(`${BASE}/candidates?status=${status}`).then((r) => _json<Candidate[]>(r)),

  renderCandidate: (id: string): Promise<{ card_path: string }> =>
    _post(`/candidates/${id}/render`),

  candidateImageUrl: (id: string, bust?: number): string =>
    `${BASE}/candidates/${id}/card.png${bust ? `?v=${bust}` : ""}`,

  rejectCandidate: (id: string): Promise<unknown> => _post(`/candidates/${id}/reject`),

  // ── Drafts ─────────────────────────────────────────────────────────────────
  drafts: (status = "pending,verified,approved"): Promise<Draft[]> =>
    fetch(`${BASE}/drafts?status=${status}`).then((r) => _json<Draft[]>(r)),

  updateDraft: (id: string, text: string): Promise<unknown> =>
    fetch(`${BASE}/drafts/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }).then((r) => _json(r)),

  approveDraft: (id: string): Promise<unknown> => _post(`/drafts/${id}/approve`),
  rejectDraft: (id: string): Promise<unknown> => _post(`/drafts/${id}/reject`),

  // ── Prompt desk ────────────────────────────────────────────────────────────
  divePrompt: (leadId: string): Promise<Prompt> =>
    fetch(`${BASE}/prompts/dive/${leadId}`).then((r) => _json<Prompt>(r)),

  draftPrompt: (candidateId: string): Promise<Prompt> =>
    fetch(`${BASE}/prompts/draft/${candidateId}`).then((r) => _json<Prompt>(r)),

  reviewPrompt: (draftId: string): Promise<Prompt> =>
    fetch(`${BASE}/prompts/review/${draftId}`).then((r) => _json<Prompt>(r)),

  hypothesisPrompt: (): Promise<Prompt> =>
    fetch(`${BASE}/prompts/hypothesis`).then((r) => _json<Prompt>(r)),

  landResult: (text: string): Promise<GateOutcome> => _post("/results", { text }),

  // ── Jobs ───────────────────────────────────────────────────────────────────
  startJob: (job: "sync" | "discover"): Promise<{ started: boolean; blocked_by?: string }> =>
    _post(`/actions/${job}`),

  jobStatus: (job: "sync" | "discover"): Promise<JobState> =>
    fetch(`${BASE}/actions/${job}`).then((r) => _json<JobState>(r)),

  runScout: (): Promise<{ written: number; leads: BoardLead[] }> => _post("/actions/scout"),
};
