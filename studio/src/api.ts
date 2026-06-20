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

  renderCard: (id: string, visual = "table"): Promise<{ card_path: string; visual: string }> =>
    fetch(`${BASE}/candidates/${id}/render?visual=${visual}`, { method: "POST" }).then((r) =>
      _json(r),
    ),

  cardUrl: (id: string): string =>
    `${BASE}/candidates/${id}/card.png?t=${Date.now()}`,

  rejectCandidate: (id: string): Promise<{ status: string }> =>
    fetch(`${BASE}/candidates/${id}/reject`, { method: "POST" }).then((r) =>
      _json(r),
    ),

  drafts: (status = "pending,verified,approved"): Promise<Draft[]> =>
    fetch(`${BASE}/drafts?status=${status}`).then((r) => _json<Draft[]>(r)),

  updateDraftText: (
    id: string,
    text: string,
  ): Promise<{ saved: boolean }> =>
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

  spatialCards: (): Promise<string[]> =>
    fetch(`${BASE}/spatial/cards`).then((r) => _json<string[]>(r)),

  renderSpatial: (
    card: string,
    player: number,
    season: number,
  ): Promise<{ card: string; player: number; season: number; n: number; id: string }> =>
    fetch(`${BASE}/spatial/render?card=${card}&player=${player}&season=${season}`, {
      method: "POST",
    }).then((r) => _json(r)),

  spatialPreviewUrl: (card: string, player: number, season: number): string =>
    `${BASE}/spatial/${card}/${player}/${season}/card.png?t=${Date.now()}`,
};

// ── MLB asset URL helpers (CDN — browser can fetch directly) ──────────────────

/** MLBAM headshot for a player. Falls back to generic silhouette on CDN. */
export function mlbPlayerPhotoUrl(mlbId: number | string): string {
  return (
    `https://img.mlbstatic.com/mlb-photos/image/upload` +
    `/d_people:generic:headshot:67:current.png` +
    `/w_213,q_auto:best/v1/people/${mlbId}/headshot/67/current`
  );
}

/** Official team logo SVG from MLB static CDN. */
export function mlbTeamLogoUrl(mlbamTeamId: number): string {
  return `https://www.mlbstatic.com/team-logos/${mlbamTeamId}.svg`;
}

/** BRef team code → MLBAM team ID (subset — most common codes). */
export const BREF_TO_MLBAM: Record<string, number> = {
  ARI: 109, ATL: 144, BAL: 110, BOS: 111,
  CHN: 112, CHA: 145, CIN: 113, CLE: 114,
  COL: 115, DET: 116, HOU: 117, KCA: 118,
  LAA: 108, LAN: 119, MIA: 146, MIL: 158,
  MIN: 142, NYN: 121, NYA: 147, OAK: 133,
  PHI: 143, PIT: 134, SDP: 135, SEA: 136,
  SFN: 137, STL: 138, TBA: 139, TEX: 140,
  TOR: 141, WSN: 120,
  // aliases
  SD: 135, LAD: 119, SF: 137, NYY: 147, NYM: 121,
  WSH: 120, CWS: 145, CHC: 112, KC: 118, TB: 139,
};
