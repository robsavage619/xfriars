// Shapes the API returns. Kept beside the client so a backend change that
// breaks a view breaks the build instead of the page.

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

export interface JobStep {
  name: string;
  ok: boolean | null; // null while the step is still running
  detail: string;
}

export interface JobState {
  job?: string;
  running: boolean;
  season: number | null;
  steps: JobStep[];
  started_at: string | null;
  finished_at: string | null;
  ok: boolean | null;
  summary: string;
  run_id?: string;
}

export interface Stats {
  new_candidates: number;
  queue_size: number;
  posted_count: number;
  open_leads: number;
  board_new: number;
  board_queued: number;
}

export interface CoverageReport {
  domain: string;
  table: string;
  status: "OK" | "STALE" | "PARTIAL" | "EMPTY" | "MISSING" | string;
  rows: number;
  seasons: number[];
  latest_date: string | null;
  n_players: number;
  blocks: string[];
  reason: string;
}

export interface Candidate {
  candidate_id: string;
  detector: string;
  subject: string;
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

export interface RefereeLens {
  lens: string;
  verdict: "PASS" | "REVISE" | "BLOCK" | string;
  failure_mode: string | null;
  evidence: string;
  confidence: number;
}

export interface Referee {
  outcome: "cleared" | "revise" | "blocked" | string;
  stale: boolean;
  failure_modes: string[];
  lenses: RefereeLens[];
}

export interface Draft {
  draft_id: string;
  candidate_id: string;
  status: "pending" | "verified" | "approved" | "posted" | "rejected" | string;
  text: string;
  char_count: number;
  has_card: boolean;
  interesting_judgment: string;
  is_projection: boolean;
  created_at: string;
  detector: string;
  novelty_score: number;
  facts: Record<string, unknown>;
  claim_scope: string;
  referee: Referee | null;
}

export interface Prompt {
  kind: string;
  subject: string;
  target_id: string;
  prompt: string;
}

export interface GateOutcome {
  kind: string;
  accepted: boolean;
  summary: string;
  gates: { name: string; ok: boolean; detail: string }[];
  draft_id: string | null;
  saved_to: string | null;
}

export interface Prediction {
  prediction_id: string;
  claim: string;
  posted_at: string | null;
  resolves_by: string | null;
  outcome: string | null;
}

export interface Predictions {
  scorecard: Record<string, unknown>;
  recent: Prediction[];
}

export interface PostedItem {
  draft_id: string;
  text: string;
  posted_at: string | null;
  posted_tweet_id: string | null;
  detector: string | null;
  subject: string | null;
  impressions: number | null;
  likes: number | null;
  reposts: number | null;
  replies: number | null;
}
