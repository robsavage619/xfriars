// API client — thin fetch wrappers, no external dependencies.
// The app only reads the board and flips statuses; Sync/Scout kick the engine.

const BASE = "/api";

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((body as { detail?: string }).detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── Types ──────────────────────────────────────────────────────────────────────

export interface BoardCard {
  card_id: string;
  kind: "season_story" | "live_moment" | string;
  subject: string;
  title: string;
  headline: string;
  rank_note: string | null;
  confidence: string;
  reconciled: boolean;
  source: string;
  caption: string;
  status: "new" | "queued" | "dismissed" | string;
  created_at: string;
  has_image: boolean;
}

export interface BoardLead {
  lead_id: string;
  subject: string;
  kind: string;
  headline: string;
  explore: string;
  interest: number;
  status: "new" | "exploring" | "dismissed" | string;
  created_at: string;
}

export interface Board {
  cards: BoardCard[];
  leads: BoardLead[];
}

export interface SyncStep {
  name: string;
  ok: boolean;
  detail: string;
}

export interface SyncState {
  running: boolean;
  season: number | null;
  steps: SyncStep[];
  finished_at: string | null;
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

export const api = {
  board: (): Promise<Board> => fetch(`${BASE}/board`).then((r) => _json<Board>(r)),

  cardImageUrl: (id: string): string => `${BASE}/board/cards/${id}/image.png`,

  setCardStatus: (id: string, status: string): Promise<{ status: string }> =>
    fetch(`${BASE}/board/cards/${id}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then((r) => _json(r)),

  setLeadStatus: (id: string, status: string): Promise<{ status: string }> =>
    fetch(`${BASE}/board/leads/${id}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    }).then((r) => _json(r)),

  startSync: (): Promise<{ started: boolean; running: boolean }> =>
    fetch(`${BASE}/actions/sync`, { method: "POST" }).then((r) => _json(r)),

  syncStatus: (): Promise<SyncState> =>
    fetch(`${BASE}/actions/sync`).then((r) => _json<SyncState>(r)),

  runScout: (): Promise<{ written: number; leads: BoardLead[] }> =>
    fetch(`${BASE}/actions/scout`, { method: "POST" }).then((r) => _json(r)),
};
