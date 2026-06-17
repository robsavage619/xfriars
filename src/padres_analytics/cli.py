"""pad — Padres analytics CLI."""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from padres_analytics.config import CARDS_DIR, DUCKDB_PATH, configure_logging

app = typer.Typer(
    name="pad",
    help="Padres analytics engine — detect, draft, render, approve, post.",
    no_args_is_help=True,
)
detect_app = typer.Typer(help="Run and list stat candidates.")
draft_app = typer.Typer(help="Manage tweet drafts.")
ingest_app = typer.Typer(help="Ingest data from MLB Stats API and other sources.")
app.add_typer(detect_app, name="detect")
app.add_typer(draft_app, name="draft")
app.add_typer(ingest_app, name="ingest")
scan_app = typer.Typer(help="Generic metric scanner (registry-driven).")
app.add_typer(scan_app, name="scan")

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Los_Angeles")

# Exit codes: 0=ok, 1=error, 2=gate-blocked
OK = 0
ERR = 1
GATE = 2


def _la_today() -> date:
    from datetime import datetime

    return datetime.now(_TZ).date()


# Detector modules that self-register on import. Loaded via importlib so the
# side-effect imports can't be stripped by lint autofix.
_DETECTOR_MODULES = (
    "crossjoin",
    "first_since",
    "gems",  # career_chase, milestone_club, hit_streak, career_conjunction, pitcher_career_chase
    "historical",
    "leaderboards",
    "milestones",
    "prospects",  # farm_performance
    "standings",
    "statcast",
    "struggles",  # cold_streak, weakness
    "trades",  # deadline_history
)


def _load_detectors() -> None:
    """Import every detector module so its register() side effect runs."""
    import importlib

    for mod in _DETECTOR_MODULES:
        importlib.import_module(f"padres_analytics.detect.{mod}")


# ── pad init ───────────────────────────────────────────────────────────────────


@app.command()
def init() -> None:
    """Create padres.db, initialize schema, and verify hist attachment."""
    configure_logging()
    from padres_analytics.storage.db import (
        TradesDbNotFoundError,
        attach_trades,
        connect,
    )
    from padres_analytics.storage.schemas import initialize

    typer.echo(f"Initializing padres.db at {DUCKDB_PATH} …")
    with connect() as conn:
        initialize(conn)

    typer.echo("Schema initialized.")

    typer.echo("Verifying hist (trades.db) attachment …")
    try:
        with connect() as conn:
            attach_trades(conn)
            result = conn.execute("SELECT COUNT(*) FROM hist.game_logs").fetchone()
            n = result[0] if result else 0
            typer.echo(f"hist.game_logs: {n:,} rows — OK")
    except TradesDbNotFoundError as exc:
        typer.echo(f"Warning: {exc}", err=True)
        typer.echo("Set PADRES_TRADES_DB_PATH to enable hist queries.", err=True)

    typer.echo("Done.")


# ── pad detect run ─────────────────────────────────────────────────────────────


@detect_app.command("run")
def detect_run(
    detector: str = typer.Argument("all", help="Detector name or 'all'."),
    as_of: str | None = typer.Option(
        None, "--date", help="Reference date (YYYY-MM-DD). Defaults to today LA time."
    ),
) -> None:
    """Run detector(s) and emit candidates to padres.db."""
    configure_logging()
    _load_detectors()
    from padres_analytics.detect.base import all_detectors, emit, get_detector
    from padres_analytics.storage.db import (
        TradesDbNotFoundError,
        attach_trades,
        connect,
    )

    ref_date = date.fromisoformat(as_of) if as_of else _la_today()

    names = all_detectors() if detector == "all" else [detector]
    try:
        detectors = [get_detector(n) for n in names]
    except KeyError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    total = 0
    with connect() as conn:
        try:
            attach_trades(conn)
        except TradesDbNotFoundError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

        for det in detectors:
            typer.echo(f"Running detector: {det.name} (as_of={ref_date})")
            try:
                candidates = det.run(conn, ref_date)
            except Exception as exc:
                typer.echo(f"Detector {det.name} failed: {exc}", err=True)
                raise typer.Exit(ERR) from exc

            n = emit(conn, candidates)
            typer.echo(f"  {det.name}: {len(candidates)} found, {n} new")
            total += n

    typer.echo(f"Total new candidates: {total}")


# ── pad detect list ────────────────────────────────────────────────────────────


@detect_app.command("list")
def detect_list(
    status: str = typer.Option("new", "--status", help="Filter by status."),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List stat candidates."""
    configure_logging()
    from padres_analytics.storage.db import connect

    with connect(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT candidate_id, detector, subject, as_of, payload_kind,
                   novelty_score, status, facts_json, provenance_json
            FROM stat_candidates
            WHERE status = ?
            ORDER BY novelty_score DESC
            """,
            [status],
        ).fetchall()

    if output_json:
        result = [
            {
                "candidate_id": r[0],
                "detector": r[1],
                "subject": r[2],
                "as_of": str(r[3]),
                "payload_kind": r[4],
                "novelty_score": r[5],
                "status": r[6],
                "facts_json": json.loads(r[7]) if isinstance(r[7], str) else r[7],
                "provenance_json": json.loads(r[8]) if isinstance(r[8], str) else r[8],
            }
            for r in rows
        ]
        typer.echo(json.dumps(result, indent=2))
    else:
        if not rows:
            typer.echo(f"No candidates with status={status!r}.")
            return
        for r in rows:
            typer.echo(f"{r[0]}  {r[1]:20s}  score={r[5]:.2f}  {r[4]}  {r[3]}")


# ── pad draft ingest ───────────────────────────────────────────────────────────


@draft_app.command("ingest")
def draft_ingest(
    file: Path = typer.Option(..., "--file", help="Path to inbox JSON draft file."),
) -> None:
    """Validate, digit-audit, render, and verify a skill draft."""
    configure_logging()
    from padres_analytics.render.cards import RenderError
    from padres_analytics.storage.db import (
        TradesDbNotFoundError,
        attach_trades,
        connect,
    )
    from padres_analytics.tweets.draft import DraftIngestError, ingest_draft

    if not file.exists():
        typer.echo(f"Error: file not found: {file}", err=True)
        raise typer.Exit(ERR)

    with connect() as conn:
        try:
            attach_trades(conn)
        except TradesDbNotFoundError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

        try:
            draft_id = ingest_draft(conn, file, CARDS_DIR)
        except DraftIngestError as exc:
            typer.echo(f"Ingest failed: {exc}", err=True)
            raise typer.Exit(GATE) from exc
        except RenderError as exc:
            typer.echo(f"Render failed: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Draft {draft_id} ingested and verified.")
    typer.echo(f"Card: {CARDS_DIR / (draft_id + '.png')!s}")
    typer.echo(f"Run 'pad queue' to review, then 'pad draft approve {draft_id}'.")


# ── pad queue ─────────────────────────────────────────────────────────────────


@app.command()
def queue() -> None:
    """Show pending/verified drafts with card path and char count."""
    configure_logging()
    from padres_analytics.storage.db import connect

    with connect(read_only=True) as conn:
        rows = conn.execute(
            """
            SELECT td.draft_id, td.status, LENGTH(td.text) AS chars,
                   td.text, td.media_path, td.candidate_id
            FROM tweet_drafts td
            WHERE td.status IN ('pending', 'verified')
            ORDER BY td.created_at
            """
        ).fetchall()

    if not rows:
        typer.echo("Queue is empty.")
        return

    for r in rows:
        draft_id, status, chars, text, media_path, _cid = r
        card_ok = "✓" if media_path and Path(media_path).exists() else "✗"
        typer.echo(f"\n{'─' * 60}")
        typer.echo(f"  Draft:    {draft_id}  [{status}]  {chars}/280 chars  card:{card_ok}")
        typer.echo(f"  Card:     {media_path or 'none'}")
        typer.echo(f"  Caption:  {text[:120]}{'…' if len(text) > 120 else ''}")


# ── pad render ────────────────────────────────────────────────────────────────


@app.command()
def render(
    candidate_id: str = typer.Argument(..., help="Candidate to render a card for."),
    card: str | None = typer.Option(
        None, "--card", help="Override dataset card type (e.g. hero, slider)."
    ),
    visual: str = typer.Option("table", "--visual", help="Legacy table visual: table|bars."),
) -> None:
    """Render a candidate's card to the cards dir (debug / fast iteration)."""
    configure_logging()
    from padres_analytics.detect.candidates import ChartDataset, TablePayload
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    with connect(read_only=True) as conn:
        row = conn.execute(
            "SELECT payload_kind, facts_json FROM stat_candidates WHERE candidate_id = ?",
            [candidate_id],
        ).fetchone()

    if row is None:
        typer.echo(f"Error: candidate {candidate_id!r} not found.", err=True)
        raise typer.Exit(ERR)

    payload_kind, facts_raw = row
    facts = json.loads(facts_raw) if isinstance(facts_raw, str) else facts_raw

    try:
        if payload_kind == "dataset":
            out = render_card(
                ChartDataset.model_validate(facts), CARDS_DIR, candidate_id, card=card
            )
        elif payload_kind == "table":
            out = render_card(
                TablePayload.model_validate(facts), CARDS_DIR, candidate_id, visual=visual
            )
        else:
            typer.echo(f"Error: unsupported payload_kind {payload_kind!r}", err=True)
            raise typer.Exit(ERR)
    except RenderError as exc:
        typer.echo(f"Render failed: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered: {out}")


# ── pad draft show / approve / reject ─────────────────────────────────────────


@draft_app.command("show")
def draft_show(draft_id: str = typer.Argument(...)) -> None:
    """Show full details of a draft."""
    configure_logging()
    from padres_analytics.storage.db import connect

    with connect(read_only=True) as conn:
        row = conn.execute("SELECT * FROM tweet_drafts WHERE draft_id = ?", [draft_id]).fetchone()

    if row is None:
        typer.echo(f"Draft {draft_id!r} not found.", err=True)
        raise typer.Exit(ERR)

    cols = [
        "draft_id",
        "candidate_id",
        "draft_kind",
        "thread_id",
        "thread_order",
        "reply_to_url",
        "text",
        "media_path",
        "is_projection",
        "model",
        "source",
        "interesting_judgment",
        "verification_json",
        "status",
        "created_at",
        "posted_tweet_id",
        "posted_at",
    ]
    for col, val in zip(cols, row, strict=False):
        typer.echo(f"  {col:<24} {val}")


@draft_app.command("approve")
def draft_approve(draft_id: str = typer.Argument(...)) -> None:
    """Approve a verified draft for posting."""
    configure_logging()
    from padres_analytics.storage.db import connect
    from padres_analytics.tweets.draft import StateTransitionError, transition

    with connect() as conn:
        try:
            transition(conn, draft_id, "approved")
        except StateTransitionError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(GATE) from exc

    typer.echo(f"Draft {draft_id} approved. Run 'pad post {draft_id}' to post.")


@draft_app.command("reject")
def draft_reject(draft_id: str = typer.Argument(...)) -> None:
    """Reject a draft (any pre-posted state)."""
    configure_logging()
    from padres_analytics.storage.db import connect
    from padres_analytics.tweets.draft import StateTransitionError, transition

    with connect() as conn:
        try:
            transition(conn, draft_id, "rejected")
        except StateTransitionError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(GATE) from exc

    typer.echo(f"Draft {draft_id} rejected.")


# ── pad post ──────────────────────────────────────────────────────────────────


@app.command()
def post(
    draft_id: str = typer.Argument(...),
    live: bool = typer.Option(False, "--live", help="Post live via tweepy (Phase 3)."),
) -> None:
    """Post an approved draft. Default is --dry-run; pass --live for real posting."""
    configure_logging()
    from padres_analytics.storage.db import connect
    from padres_analytics.tweets.post import DuplicatePostError, PostError
    from padres_analytics.tweets.post import post as do_post

    out_dir = CARDS_DIR / "out"
    with connect() as conn:
        try:
            post_dir = do_post(conn, draft_id, out_dir, dry_run=not live)
        except DuplicatePostError as exc:
            typer.echo(f"Duplicate: {exc}", err=True)
            raise typer.Exit(GATE) from exc
        except PostError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    if not live:
        typer.echo(f"[DRY RUN] Output written to: {post_dir}")
    else:
        typer.echo(f"Posted. Output: {post_dir}")


# ── pad ingest leaders ────────────────────────────────────────────────────────


@ingest_app.command("leaders")
def ingest_leaders_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
    limit: int = typer.Option(25, "--limit", help="Leaders per stat type (1-100)."),
) -> None:
    """Fetch MLB leaderboards from the Stats API and store in mlb_leaders."""
    configure_logging()
    from padres_analytics.ingest.mlb_api import ingest_leaders
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting MLB leaders for season {ref_season} (limit={limit}) …")

    with connect() as conn:
        try:
            n = ingest_leaders(conn, ref_season, limit=limit)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n} rows written to mlb_leaders.")


# ── pad ingest statcast ───────────────────────────────────────────────────────


@ingest_app.command("statcast")
def ingest_statcast_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Fetch Statcast data from Baseball Savant and store in padres.db.

    Refreshes four tables: statcast_batter_percentile_ranks,
    statcast_batting_expected, statcast_sprint_speed,
    statcast_batter_exitvelo_barrels.
    """
    configure_logging()
    from padres_analytics.ingest.statcast import ingest_statcast
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting Statcast data for season {ref_season} from Baseball Savant …")

    with connect() as conn:
        initialize(conn)
        try:
            results = ingest_statcast(conn, ref_season)
        except RuntimeError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    for table, n in results.items():
        typer.echo(f"  {table}: {n} rows")
    typer.echo(f"Done. Season {ref_season} Statcast data refreshed.")


# ── pad ingest standings ──────────────────────────────────────────────────────


@ingest_app.command("standings")
def ingest_standings_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Fetch live MLB standings from the Stats API into main.standings.

    The nl_west_race detector prefers this fresh snapshot over the simulated
    hist.standings, so the division race reflects the real season.
    """
    configure_logging()
    from padres_analytics.ingest.mlb_api import ingest_standings
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting MLB standings for season {ref_season} …")

    with connect() as conn:
        try:
            n = ingest_standings(conn, ref_season)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n} teams written to main.standings.")


# ── pad ingest history ────────────────────────────────────────────────────────


@ingest_app.command("history")
def ingest_history_cmd(
    start: int = typer.Option(1969, "--start", help="First season (Padres began 1969)."),
    end: int = typer.Option(0, "--end", help="Last season. Defaults to current year."),
) -> None:
    """Ingest full franchise player-season hitting history into main.

    The gem data layer: real HR/H/RBI/etc. per Padre per year, powering
    "first Padre with X since [legend]" and "Nth season in franchise history" gems.
    """
    configure_logging()
    from padres_analytics.ingest.mlb_api import ingest_pitcher_seasons, ingest_player_seasons
    from padres_analytics.storage.db import connect

    ref_end = end or _la_today().year
    typer.echo(f"Ingesting Padres player-season history {start}-{ref_end} …")

    with connect() as conn:
        try:
            n_bat = ingest_player_seasons(conn, start, ref_end)
            n_pit = ingest_pitcher_seasons(conn, start, ref_end)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n_bat} batting + {n_pit} pitching player-season rows written.")


# ── pad ingest milb ───────────────────────────────────────────────────────────


@ingest_app.command("milb")
def ingest_milb_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Ingest real minor-league hitting across the Padres' affiliates (farm/prospect watch)."""
    configure_logging()
    from padres_analytics.ingest.mlb_api import ingest_milb
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting Padres MiLB stats for {ref_season} …")
    with connect() as conn:
        try:
            n = ingest_milb(conn, ref_season)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc
    typer.echo(f"Done. {n} MiLB player rows written to main.milb_batting.")


# ── pad ingest gamelogs ───────────────────────────────────────────────────────


@ingest_app.command("gamelogs")
def ingest_gamelogs_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Ingest current-season per-game hitting logs (powers active-streak gems)."""
    configure_logging()
    from padres_analytics.ingest.mlb_api import ingest_game_logs
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting Padres game logs for {ref_season} …")
    with connect() as conn:
        try:
            n = ingest_game_logs(conn, ref_season)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc
    typer.echo(f"Done. {n} game-rows written to main.player_game_batting.")


# ── pad ingest roster ─────────────────────────────────────────────────────────


@ingest_app.command("roster")
def ingest_roster_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
    roster_type: str = typer.Option("40Man", "--type", help="Roster type (40Man, active, …)."),
) -> None:
    """Fetch the live Padres roster into main.team_rosters.

    The scan engine prefers this real 40-man over the simulated hist.team_rosters,
    so non-Padres can't surface in Padre-only cards.
    """
    configure_logging()
    from padres_analytics.ingest.mlb_api import ingest_roster
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting Padres {roster_type} roster for season {ref_season} …")

    with connect() as conn:
        try:
            n = ingest_roster(conn, ref_season, roster_type=roster_type)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n} players written to main.team_rosters.")


# ── pad scan ──────────────────────────────────────────────────────────────────


@scan_app.command("run")
def scan_run(
    as_of: str | None = typer.Option(
        None, "--date", help="Reference date (YYYY-MM-DD). Defaults to today LA time."
    ),
    top_k: int | None = typer.Option(None, "--top-k", help="Override registry top_k."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print candidates without writing to DB."
    ),
    output_json: bool = typer.Option(False, "--json", help="Print candidates as JSON."),
) -> None:
    """Run the generic metric scanner and emit candidates."""
    configure_logging()
    import padres_analytics.detect.scanner  # noqa: F401 — triggers registration
    from padres_analytics.detect.base import emit, get_detector
    from padres_analytics.detect.registry import load_registry
    from padres_analytics.storage.db import TradesDbNotFoundError, attach_trades, connect

    ref_date = date.fromisoformat(as_of) if as_of else _la_today()
    scanner = get_detector("scan")

    try:
        reg = load_registry()
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    effective_k = top_k if top_k is not None else reg.scan.top_k

    with connect() as conn:
        try:
            attach_trades(conn)
        except TradesDbNotFoundError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

        candidates = scanner.run(conn, ref_date)

        if not candidates:
            typer.echo("scan: no candidates surfaced.")
            return

        if output_json:
            typer.echo(
                json.dumps([c.model_dump(mode="json") for c in candidates[:effective_k]], indent=2)
            )
            return

        typer.echo(f"\nscan: {len(candidates)} candidates (top {effective_k} shown)\n")
        for c in candidates[:effective_k]:
            typer.echo(
                f"  {c.candidate_id[:20]}  score={c.novelty_score:.2f}"
                f"  {c.detector:8s}  {c.subject}"
            )

        if not dry_run:
            n = emit(conn, candidates[:effective_k])
            typer.echo(f"\n{n} new candidate(s) written.")
        else:
            typer.echo("\n[dry-run] no writes.")


# ── pad ammo ──────────────────────────────────────────────────────────────────


@app.command()
def ammo(
    query: str = typer.Argument(..., help="Search query (player name, stat, etc.)"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON."),
    limit: int = typer.Option(5, "--limit", help="Max results (default 5)."),
) -> None:
    """Search verified facts for reply use. Returns top matches by novelty x recency."""
    configure_logging()
    from padres_analytics.storage.db import connect
    from padres_analytics.tweets.ammo import search_ammo

    with connect(read_only=True) as conn:
        results = search_ammo(conn, query, as_of=_la_today(), limit=limit)

    if not results:
        typer.echo(f"No results for {query!r}.")
        return

    if output_json:
        typer.echo(json.dumps(results, indent=2))
        return

    for r in results:
        typer.echo(f"\n{'─' * 60}")
        typer.echo(f"  [{r['candidate_id']}]  {r['detector']}  score={r['ammo_score']:.3f}")
        typer.echo(f"  {r['headline']}")
        typer.echo(f"  {r['claim_scope']} · as_of={r['as_of']}")


# ── pad serve ─────────────────────────────────────────────────────────────────


@app.command()
def serve(
    port: int = typer.Option(7547, "--port", help="API server port."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes."),
) -> None:
    """Start the xFriars Studio API server (FastAPI + uvicorn).

    In dev mode, also start the Vite frontend:
        cd studio && npm run dev
    """
    configure_logging()
    import subprocess
    import sys

    from padres_analytics.config import PROJECT_ROOT

    studio_dist = PROJECT_ROOT / "studio" / "dist"
    if studio_dist.exists():
        typer.echo(f"Serving built app at http://localhost:{port}/")
    else:
        typer.echo(
            f"API running at http://localhost:{port}/api\n"
            f"  → For the UI: cd studio && npm install && npm run dev"
        )

    typer.echo("API docs: http://localhost:{port}/api/docs")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "padres_analytics.app.api:app",
        "--port",
        str(port),
        "--host",
        "127.0.0.1",
    ]
    if reload:
        cmd.append("--reload")

    subprocess.run(cmd, check=False)


def main() -> None:
    """Entrypoint for the pad CLI."""
    configure_logging(logging.DEBUG if "--verbose" in sys.argv else logging.INFO)
    app()


if __name__ == "__main__":
    main()
