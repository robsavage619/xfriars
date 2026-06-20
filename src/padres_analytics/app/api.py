"""FastAPI backend for xFriars Studio.

Serves candidate/draft data from padres.db and renders card PNGs on demand.
Run via: uv run uvicorn padres_analytics.app.api:app --port 7547 --reload
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from padres_analytics.config import CARDS_DIR, DUCKDB_PATH
from padres_analytics.detect.candidates import ChartDataset, SpatialDataset, TablePayload
from padres_analytics.render.cards import RenderError, render
from padres_analytics.render.mlb_assets import (
    BREF_TO_MLBAM,
    player_photo_path,
    team_logo_path,
)
from padres_analytics.tweets.draft import StateTransitionError, transition
from padres_analytics.tweets.verify import digit_audit

logger = logging.getLogger(__name__)

app = FastAPI(title="xFriars Studio", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:7547",  # FastAPI itself (built app)
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

_STUDIO_DIST = Path(__file__).parents[3] / "studio" / "dist"


# ── DB helpers ─────────────────────────────────────────────────────────────────


def _ro() -> duckdb.DuckDBPyConnection:
    """Open a read-only padres.db connection. Caller must close."""
    if not DUCKDB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="padres.db not found. Run: uv run pad init",
        )
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def _rw() -> duckdb.DuckDBPyConnection:
    """Open a read-write padres.db connection. Caller must close."""
    if not DUCKDB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="padres.db not found. Run: uv run pad init",
        )
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(DUCKDB_PATH))


def _attach_hist(conn: duckdb.DuckDBPyConnection) -> bool:
    """Attempt to attach trades.db as hist. Returns True if successful."""
    env_path = os.environ.get("PADRES_TRADES_DB_PATH")
    if env_path:
        trades_path = Path(env_path)
    else:
        trades_path = (
            Path(__file__).resolve().parents[4]
            / "savage-trade-evaluator"
            / "data"
            / "duckdb"
            / "trades.db"
        )
    if not trades_path.exists():
        return False
    try:
        conn.execute(f"ATTACH '{trades_path}' AS hist (READ_ONLY)")
        return True
    except Exception:
        return False


def _table_exists(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    rows = conn.execute("SHOW TABLES").fetchall()
    return any(r[0] == name for r in rows)


# ── Stats ──────────────────────────────────────────────────────────────────────


@app.get("/api/stats")
def get_stats() -> dict[str, int]:
    """Dashboard summary counts."""
    conn = _ro()
    try:
        new_c = conn.execute("SELECT COUNT(*) FROM stat_candidates WHERE status = 'new'").fetchone()
        queue = conn.execute(
            "SELECT COUNT(*) FROM tweet_drafts WHERE status IN ('pending','verified','approved')"
        ).fetchone()
        posted = conn.execute(
            "SELECT COUNT(*) FROM tweet_drafts WHERE status = 'posted'"
        ).fetchone()
        return {
            "new_candidates": new_c[0] if new_c else 0,
            "queue_size": queue[0] if queue else 0,
            "posted_count": posted[0] if posted else 0,
        }
    finally:
        conn.close()


# ── Candidates ─────────────────────────────────────────────────────────────────


@app.get("/api/candidates")
def list_candidates(status: str = "new") -> list[dict[str, Any]]:
    """List stat candidates filtered by status, sorted by novelty desc."""
    conn = _ro()
    try:
        rows = conn.execute(
            """
            SELECT sc.candidate_id, sc.detector, sc.subject, CAST(sc.as_of AS VARCHAR),
                   sc.novelty_score, sc.status, sc.facts_json,
                   sc.claim_scope, sc.coverage_window, sc.payload_kind,
                   EXISTS(
                       SELECT 1 FROM tweet_drafts td
                       WHERE td.candidate_id = sc.candidate_id
                   ) AS has_draft
            FROM stat_candidates sc
            WHERE sc.status = ?
            ORDER BY sc.novelty_score DESC
            """,
            [status],
        ).fetchall()

        result = []
        for r in rows:
            facts: dict[str, Any] = json.loads(r[6]) if isinstance(r[6], str) else r[6]
            card_path = CARDS_DIR / f"{r[0]}.png"
            result.append(
                {
                    "candidate_id": r[0],
                    "detector": r[1],
                    "subject": r[2],
                    "as_of": r[3],
                    "novelty_score": round(r[4], 3),
                    "status": r[5],
                    "facts": facts,
                    "claim_scope": r[7],
                    "coverage_window": r[8],
                    "payload_kind": r[9],
                    "has_draft": bool(r[10]),
                    "has_card": card_path.exists(),
                }
            )
        return result
    finally:
        conn.close()


@app.post("/api/candidates/{candidate_id}/render")
async def render_candidate_card(
    candidate_id: str, visual: str = "table", card: str | None = None
) -> dict[str, str]:
    """Render a card PNG for a candidate (idempotent — overwrites existing).

    Query params:
        visual: legacy TablePayload card type — "table" (default) or "bars".
        card: ChartDataset card-type override; defaults to the data-shape selector.

    Playwright runs in a thread pool to avoid greenlet conflicts.
    """
    import asyncio

    conn = _ro()
    try:
        row = conn.execute(
            "SELECT facts_json, payload_kind FROM stat_candidates WHERE candidate_id = ?",
            [candidate_id],
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Candidate not found")

    facts_raw, payload_kind = row
    facts: dict[str, Any] = json.loads(facts_raw) if isinstance(facts_raw, str) else facts_raw

    try:
        if payload_kind == "dataset":
            dataset = ChartDataset.model_validate(facts)
            card_path = await asyncio.to_thread(
                render, dataset, CARDS_DIR, candidate_id, "table", card
            )
            return {"card_path": str(card_path), "visual": card or "auto"}
        if payload_kind == "table":
            payload = TablePayload.model_validate(facts)
            card_path = await asyncio.to_thread(render, payload, CARDS_DIR, candidate_id, visual)
            return {"card_path": str(card_path), "visual": visual}
        if payload_kind == "spatial":
            spatial = SpatialDataset.model_validate(facts)
            card_path = await asyncio.to_thread(render, spatial, CARDS_DIR, candidate_id)
            return {"card_path": str(card_path), "visual": spatial.card}
        raise HTTPException(status_code=422, detail=f"Cannot render payload_kind={payload_kind!r}")
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Spatial card picker ──────────────────────────────────────────────────────


@app.get("/api/spatial/cards")
def list_spatial_cards() -> list[str]:
    """List the spatial card types the picker can render."""
    from padres_analytics.detect.spatial import SPATIAL_BUILDERS

    return sorted(SPATIAL_BUILDERS)


@app.post("/api/spatial/render")
async def render_spatial_preview(card: str, player: int, season: int) -> dict[str, Any]:
    """Build and render a spatial card for a player/season (idempotent preview)."""
    import asyncio

    from padres_analytics.detect.spatial import SPATIAL_BUILDERS, build_spatial

    if card not in SPATIAL_BUILDERS:
        raise HTTPException(status_code=422, detail=f"Unknown card {card!r}")

    conn = _ro()
    try:
        dataset = build_spatial(conn, card, player, season)
    finally:
        conn.close()

    if dataset is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No data to build a {card!r} card for player {player}, season {season}. "
                f"Ingest the source events first."
            ),
        )

    card_id = f"spatial_{card}_{player}_{season}"
    try:
        await asyncio.to_thread(render, dataset, CARDS_DIR, card_id)
    except RenderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"card": card, "player": player, "season": season, "n": dataset.n, "id": card_id}


@app.get("/api/spatial/{card}/{player}/{season}/card.png", response_class=FileResponse)
def get_spatial_preview(card: str, player: int, season: int) -> FileResponse:
    """Serve a rendered spatial preview PNG."""
    card_path = CARDS_DIR / f"spatial_{card}_{player}_{season}.png"
    if not card_path.exists():
        raise HTTPException(status_code=404, detail="Not rendered yet. POST /api/spatial/render.")
    return FileResponse(str(card_path), media_type="image/png")


@app.get("/api/candidates/{candidate_id}/card.png", response_class=FileResponse)
def get_candidate_card(candidate_id: str) -> FileResponse:
    """Serve the rendered card PNG for a candidate."""
    card_path = CARDS_DIR / f"{candidate_id}.png"
    if not card_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Card not rendered yet. POST /api/candidates/{id}/render first.",
        )
    return FileResponse(str(card_path), media_type="image/png")


@app.post("/api/candidates/{candidate_id}/reject")
def reject_candidate(candidate_id: str) -> dict[str, str]:
    """Reject a new candidate — curation kill switch. Only 'new' can be rejected."""
    conn = _rw()
    try:
        row = conn.execute(
            "SELECT status FROM stat_candidates WHERE candidate_id = ?",
            [candidate_id],
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if row[0] != "new":
            raise HTTPException(
                status_code=422,
                detail=f"Only 'new' candidates can be rejected (status: {row[0]!r})",
            )
        conn.execute(
            "UPDATE stat_candidates SET status = 'rejected' WHERE candidate_id = ?",
            [candidate_id],
        )
        return {"candidate_id": candidate_id, "status": "rejected"}
    finally:
        conn.close()


# ── Drafts ─────────────────────────────────────────────────────────────────────


@app.get("/api/drafts")
def list_drafts(status: str = "pending,verified,approved") -> list[dict[str, Any]]:
    """List tweet drafts, joined with their candidate."""
    statuses = [s.strip() for s in status.split(",")]
    placeholders = ", ".join(["?"] * len(statuses))
    conn = _ro()
    try:
        rows = conn.execute(
            f"""
            SELECT td.draft_id, td.candidate_id, td.status, td.text,
                   td.media_path, td.interesting_judgment, td.is_projection,
                   CAST(td.created_at AS VARCHAR),
                   sc.detector, sc.novelty_score, sc.facts_json, sc.claim_scope
            FROM tweet_drafts td
            JOIN stat_candidates sc ON sc.candidate_id = td.candidate_id
            WHERE td.status IN ({placeholders})
            ORDER BY td.created_at DESC
            """,
            statuses,
        ).fetchall()

        result = []
        for r in rows:
            facts: dict[str, Any] = json.loads(r[10]) if isinstance(r[10], str) else r[10]
            media_path = r[4]
            result.append(
                {
                    "draft_id": r[0],
                    "candidate_id": r[1],
                    "status": r[2],
                    "text": r[3],
                    "char_count": len(r[3]) if r[3] else 0,
                    "has_card": bool(media_path and Path(media_path).exists()),
                    "interesting_judgment": r[5],
                    "is_projection": bool(r[6]),
                    "created_at": r[7],
                    "detector": r[8],
                    "novelty_score": round(r[9], 3),
                    "facts": facts,
                    "claim_scope": r[11],
                }
            )
        return result
    finally:
        conn.close()


class DraftTextUpdate(BaseModel):
    """Request body for updating draft caption text."""

    text: str


@app.patch("/api/drafts/{draft_id}")
def update_draft_text(draft_id: str, body: DraftTextUpdate) -> dict[str, Any]:
    """Update caption text with digit-audit validation."""
    if len(body.text) > 280:
        raise HTTPException(status_code=422, detail="Caption exceeds 280 characters")

    conn = _ro()
    try:
        row = conn.execute(
            """
            SELECT sc.facts_json FROM tweet_drafts td
            JOIN stat_candidates sc ON sc.candidate_id = td.candidate_id
            WHERE td.draft_id = ?
            """,
            [draft_id],
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    facts: dict[str, Any] = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    offenders = digit_audit(body.text, facts)
    if offenders:
        raise HTTPException(
            status_code=422,
            detail=f"Digit audit failed — numbers not in facts_json: {offenders}",
        )

    conn_w = _rw()
    try:
        conn_w.execute(
            "UPDATE tweet_drafts SET text = ? WHERE draft_id = ?",
            [body.text, draft_id],
        )
        return {"draft_id": draft_id, "digit_audit_errors": [], "saved": True}
    finally:
        conn_w.close()


@app.post("/api/drafts/{draft_id}/approve")
def approve_draft(draft_id: str) -> dict[str, str]:
    """Approve a verified draft."""
    conn = _rw()
    try:
        transition(conn, draft_id, "approved")
        return {"draft_id": draft_id, "status": "approved"}
    except StateTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/drafts/{draft_id}/reject")
def reject_draft(draft_id: str) -> dict[str, str]:
    """Reject a draft (any pre-posted state)."""
    conn = _rw()
    try:
        transition(conn, draft_id, "rejected")
        return {"draft_id": draft_id, "status": "rejected"}
    except StateTransitionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


# ── MLB assets ────────────────────────────────────────────────────────────────


@app.get("/api/mlb/player/{mlb_id}/photo", response_class=FileResponse)
def get_player_photo(mlb_id: int) -> FileResponse:
    """Serve a cached MLB player headshot PNG, downloading if needed."""
    path = player_photo_path(mlb_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Photo not available for {mlb_id}")
    return FileResponse(str(path), media_type="image/png")


@app.get("/api/mlb/team/{bref_code}/logo", response_class=FileResponse)
def get_team_logo(bref_code: str) -> FileResponse:
    """Serve a cached MLB team logo SVG."""
    path = team_logo_path(bref_code.upper())
    if path is None:
        raise HTTPException(status_code=404, detail=f"Logo not available for {bref_code!r}")
    return FileResponse(str(path), media_type="image/svg+xml")


@app.get("/api/mlb/teams")
def list_mlb_teams() -> dict[str, int]:
    """Return the BRef-code → MLBAM-ID mapping (for frontend logo construction)."""
    return BREF_TO_MLBAM


# ── Explorer ───────────────────────────────────────────────────────────────────

_EXPLORER_QUERIES: dict[str, tuple[str, bool]] = {
    # name → (SQL, requires_hist)
    "all_candidates": (
        """
        SELECT candidate_id, detector, subject, CAST(as_of AS VARCHAR) as as_of,
               ROUND(novelty_score, 3) AS novelty_score, status, claim_scope
        FROM stat_candidates
        ORDER BY novelty_score DESC
        LIMIT 200
        """,
        False,
    ),
    "leaderboard": (
        """
        SELECT stat_type, rank, player_name, team_abbrev,
               ROUND(stat_value, 3) AS stat_value, season
        FROM mlb_leaders
        ORDER BY season DESC, stat_type, rank
        LIMIT 300
        """,
        False,
    ),
    "franchise_war": (
        """
        SELECT name_common, ROUND(SUM(war), 1) AS career_war,
               MIN(year_id) AS first_yr, MAX(year_id) AS last_yr,
               COUNT(DISTINCT year_id) AS seasons
        FROM hist.bwar_player_seasons
        WHERE team_id = 'SDP' AND war > 0
        GROUP BY name_common, mlb_id
        ORDER BY career_war DESC
        LIMIT 30
        """,
        True,
    ),
    "dollar_per_war": (
        """
        WITH payroll AS (
            SELECT team_bref, season, ROUND(SUM(cap_hit) / 1e6, 1) AS payroll_m
            FROM hist.spotrac_player_contracts
            GROUP BY team_bref, season
        ),
        war AS (
            SELECT team_id, year_id, ROUND(SUM(GREATEST(war, 0)), 1) AS total_war
            FROM hist.bwar_player_seasons
            GROUP BY team_id, year_id
        )
        SELECT p.season, p.team_bref,
               p.payroll_m,
               w.total_war,
               ROUND(p.payroll_m / NULLIF(w.total_war, 0), 2) AS m_per_war
        FROM payroll p
        JOIN war w ON w.team_id = p.team_bref AND w.year_id = p.season
        WHERE p.season = (SELECT MAX(season) - 1 FROM payroll)
        ORDER BY m_per_war DESC
        LIMIT 30
        """,
        True,
    ),
    "draft_history": (
        """
        SELECT td.draft_id, sc.detector, td.status,
               CAST(td.created_at AS VARCHAR) AS created_at,
               CAST(td.posted_at AS VARCHAR) AS posted_at,
               LEFT(td.text, 100) AS caption_preview
        FROM tweet_drafts td
        LEFT JOIN stat_candidates sc ON sc.candidate_id = td.candidate_id
        ORDER BY td.created_at DESC
        LIMIT 100
        """,
        False,
    ),
}


@app.get("/api/explorer/views")
def explorer_views() -> list[str]:
    """List available explorer view names."""
    return list(_EXPLORER_QUERIES.keys())


@app.get("/api/explorer/{view_name}")
def explorer_view(view_name: str) -> dict[str, Any]:
    """Run a named explorer query and return columns + rows."""
    if view_name not in _EXPLORER_QUERIES:
        raise HTTPException(status_code=404, detail=f"Unknown view: {view_name!r}")

    sql, requires_hist = _EXPLORER_QUERIES[view_name]
    conn = _ro()
    hist_available = False

    try:
        if requires_hist:
            hist_available = _attach_hist(conn)
            if not hist_available:
                return {
                    "columns": [],
                    "rows": [],
                    "error": "hist (trades.db) not available. Set PADRES_TRADES_DB_PATH.",
                }

        if not requires_hist:
            # Check if the target table exists (e.g., mlb_leaders may not be populated)
            table_name = view_name if view_name in ("leaderboard",) else None
            if table_name == "leaderboard" and not _table_exists(conn, "mlb_leaders"):
                return {
                    "columns": [],
                    "rows": [],
                    "error": "mlb_leaders not populated. Run: uv run pad ingest leaders",
                }

        rel = conn.execute(sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        rows = rel.fetchall()
        return {
            "columns": cols,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
        }
    finally:
        conn.close()


# ── Static files (built React app) ────────────────────────────────────────────

if _STUDIO_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(_STUDIO_DIST / "assets")),
        name="studio-assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        """Serve the React app; unknown non-API paths fall back to index.html."""
        candidate = _STUDIO_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_STUDIO_DIST / "index.html"))
