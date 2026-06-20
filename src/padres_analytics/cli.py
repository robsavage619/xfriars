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
live_app = typer.Typer(help="In-game (live) reads from the MLB GUMBO feed.")
app.add_typer(live_app, name="live")

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


@ingest_app.command("batted-balls")
def ingest_batted_balls_cmd(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Fetch one hitter's event-level batted balls (hc_x/hc_y) for spray charts."""
    configure_logging()
    from padres_analytics.ingest.statcast_events import ingest_batted_balls
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting batted balls for player {player}, season {ref_season} …")

    with connect() as conn:
        initialize(conn)
        try:
            n = ingest_batted_balls(conn, ref_season, player)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n} batted balls written to statcast_batted_balls.")


@ingest_app.command("batter-pitches")
def ingest_batter_pitches_cmd(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Fetch every pitch a hitter faced (run value, attack zone, bat tracking)."""
    configure_logging()
    from padres_analytics.ingest.statcast_events import ingest_batter_pitches
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting faced pitches for batter {player}, season {ref_season} …")

    with connect() as conn:
        initialize(conn)
        try:
            n = ingest_batter_pitches(conn, ref_season, player)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n} pitches written to statcast_batter_pitches.")


@ingest_app.command("pitches")
def ingest_pitches_cmd(
    player: int = typer.Option(..., "--player", help="MLBAM pitcher id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Fetch one pitcher's event-level pitches (movement/location) for arsenal cards."""
    configure_logging()
    from padres_analytics.ingest.statcast_events import ingest_pitches
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    ref_season = season or _la_today().year
    typer.echo(f"Ingesting pitches for pitcher {player}, season {ref_season} …")

    with connect() as conn:
        initialize(conn)
        try:
            n = ingest_pitches(conn, ref_season, player)
        except Exception as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(ERR) from exc

    typer.echo(f"Done. {n} pitches written to statcast_pitches.")


@app.command()
def spray(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
    vs_hand: str = typer.Option("", "--vs", help="Filter pitcher hand: R or L (blank = all)."),
) -> None:
    """Render a spray-chart card from stored batted balls to the cards dir."""
    configure_logging()
    from padres_analytics.detect.spatial import build_spray
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    hand = vs_hand.upper() or None

    with connect(read_only=True) as conn:
        dataset = build_spray(conn, player, ref_season, vs_hand=hand)

    if dataset is None:
        typer.echo(
            f"No plottable batted balls for player {player}, season {ref_season}. "
            f"Run 'pad ingest batted-balls --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    cid = f"spray_{player}_{ref_season}" + (f"_{hand}" if hand else "")
    try:
        out = render_card(dataset, CARDS_DIR, cid)
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-BBE spray → {out}")


@app.command()
def hotcold(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a hot/cold zone card (xwOBA on contact by zone) from stored batted balls."""
    configure_logging()
    from padres_analytics.detect.spatial import build_hot_cold
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_hot_cold(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No in-zone contact with location for player {player}, season {ref_season}. "
            f"Re-run 'pad ingest batted-balls --player {player} --season {ref_season}' "
            f"(plate_x/plate_z needed).",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"hotcold_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-BBE hot/cold → {out}")


@app.command()
def rolling(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a rolling-xwOBA (form) card from stored batted balls."""
    configure_logging()
    from padres_analytics.detect.spatial import build_rolling
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_rolling(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"Too few batted balls for a rolling trend for player {player}, season {ref_season}.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"rolling_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-BBE rolling xwOBA → {out}")


@app.command()
def swingtake(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a swing/take run-value card from stored faced pitches."""
    configure_logging()
    from padres_analytics.detect.spatial import build_swing_take
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_swing_take(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No faced pitches with run value for player {player}, season {ref_season}. "
            f"Run 'pad ingest batter-pitches --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"swingtake_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    rv = (dataset.hero or {}).get("value", "")
    typer.echo(f"Rendered swing/take ({rv} RV) → {out}")


@app.command()
def batspeed(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a bat-speed (bat-tracking) distribution card from stored faced pitches."""
    configure_logging()
    from padres_analytics.detect.spatial import build_bat_speed
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_bat_speed(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No tracked swings for player {player}, season {ref_season}. "
            f"Run 'pad ingest batter-pitches --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"batspeed_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-swing bat speed → {out}")


@app.command("spatial-card")
def spatial_card_cmd(
    card: str = typer.Option(..., "--card", help="Spatial card name, e.g. spray, arsenal, zone."),
    player: int = typer.Option(..., "--player", help="MLBAM player id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Emit a spatial card as a stat candidate so the draft pipeline can post it."""
    configure_logging()
    from padres_analytics.detect.base import emit
    from padres_analytics.detect.spatial import SPATIAL_BUILDERS, emit_spatial_candidate
    from padres_analytics.storage.db import connect

    if card not in SPATIAL_BUILDERS:
        typer.echo(
            f"Unknown card {card!r}. Available: {', '.join(sorted(SPATIAL_BUILDERS))}", err=True
        )
        raise typer.Exit(ERR)

    ref_season = season or _la_today().year
    with connect() as conn:
        candidate = emit_spatial_candidate(conn, card, player, ref_season)
        if candidate is None:
            typer.echo(
                f"Insufficient data to build a {card!r} card for player {player}, "
                f"season {ref_season}. Ingest the source events first.",
                err=True,
            )
            raise typer.Exit(ERR)
        n = emit(conn, [candidate])

    if n:
        typer.echo(f"Emitted candidate {candidate.candidate_id} ({card}).")
        typer.echo(
            "Draft it: write a TweetDraft referencing that candidate_id, then 'pad draft ingest'."
        )
    else:
        typer.echo(f"Candidate {candidate.candidate_id} already exists (skipped).")


@app.command("spatial-suggest")
def spatial_suggest_cmd(
    status: str = typer.Option("new", "--status", help="Candidate status to scan."),
) -> None:
    """Emit companion spatial cards for detected stats that map to a visual."""
    configure_logging()
    from padres_analytics.detect.base import emit
    from padres_analytics.detect.spatial_map import spatial_companion
    from padres_analytics.storage.db import connect

    emitted = 0
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT candidate_id, detector, as_of, facts_json
            FROM stat_candidates
            WHERE status = ? AND payload_kind != 'spatial'
            """,
            [status],
        ).fetchall()
        for _cid, detector, as_of, facts_raw in rows:
            facts = json.loads(facts_raw) if isinstance(facts_raw, str) else facts_raw
            companion = spatial_companion(conn, detector, facts, as_of)
            if companion is None:
                continue
            if emit(conn, [companion]):
                emitted += 1
                typer.echo(f"  {detector} → {companion.detector} ({companion.candidate_id})")

    typer.echo(f"Emitted {emitted} companion spatial card(s).")


@app.command()
def story(
    kind: str = typer.Option("funk", "--kind", help="Story type. Currently: funk."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a composed story-card infographic (e.g. the current funk)."""
    configure_logging()
    from padres_analytics.detect.story import build_funk_story
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    builders = {"funk": build_funk_story}
    if kind not in builders:
        typer.echo(f"Unknown story {kind!r}. Available: {', '.join(builders)}", err=True)
        raise typer.Exit(ERR)

    with connect(read_only=True) as conn:
        card = builders[kind](conn, ref_season)

    if card is None:
        typer.echo(
            f"Not enough data for a {kind!r} story (need standings + statcast). "
            "Ingest current standings and statcast first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(card, CARDS_DIR, f"story_{kind}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {kind} story → {out}")


@app.command("discover")
def discover_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Scan every lens and print the defensible story angles, strongest first.

    Each angle reports its effect size, reliability, and confidence; angles that
    don't clear their significance gate are not shown (no noise stories).
    """
    configure_logging()
    from padres_analytics.detect.angles import discover
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        angles = discover(conn, ref_season)

    if not angles:
        typer.echo("No story clears the significance gates right now.", err=True)
        raise typer.Exit(ERR)

    for i, a in enumerate(angles, 1):
        typer.echo(
            f"{i}. [{a.key}] interest={a.interest:.1f} conf={a.confidence} (r={a.reliability:.2f})"
        )
        typer.echo(f"   {a.headline}")
        typer.echo(f"   {a.thesis}")


@app.command("story")
def story_infographic_cmd(
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
    angle: str = typer.Option(
        "", "--angle", help="Force an angle key (team_luck, player_luck, ...). Default: strongest."
    ),
) -> None:
    """Render the strongest defensible analytical infographic (or a forced angle).

    Discovers candidate stories across lenses, picks the highest-interest one,
    audits that every claimed number is on the card, then renders it.
    """
    configure_logging()
    from padres_analytics.detect.angles import discover
    from padres_analytics.detect.reconcile import ReconcileError, verify_angle
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.story_infographic import render_angle
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        angles = discover(conn, ref_season)

        if angle:
            angles = [a for a in angles if a.key == angle]
        if not angles:
            typer.echo(
                "No story to render (nothing cleared the gates, or unknown --angle). "
                "Try 'pad discover' to see candidates.",
                err=True,
            )
            raise typer.Exit(ERR)

        chosen = angles[0]
        # Verify every number traces to source before we render anything postable.
        try:
            verify_angle(conn, chosen)
        except ReconcileError as exc:
            typer.echo(f"Refusing to render — {exc}", err=True)
            raise typer.Exit(ERR) from exc

    try:
        out = render_angle(chosen, CARDS_DIR, f"story_{chosen.key}_{ref_season}")
    except (RenderError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"[{chosen.key}] {chosen.headline}")
    typer.echo(f"Rendered story → {out}  (numbers reconciled vs source ✓)")


@live_app.command("now")
def live_now_cmd(
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD. Defaults to today (LA)."),
) -> None:
    """Show the Padres' current game: last pitch + the batter's line tonight.

    Reads the MLB GUMBO live feed (unofficial, near-real-time). Stats are
    preliminary and revised after the game — never archive a live read as truth.
    """
    configure_logging()
    from padres_analytics.ingest.mlb_api import MlbApiError, MlbStatsClient
    from padres_analytics.live import current_snapshot

    on_date = on or _la_today().isoformat()
    try:
        with MlbStatsClient() as client:
            snap = current_snapshot(client, on_date)
    except MlbApiError as exc:
        typer.echo(f"Error reaching the MLB feed: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    if snap is None:
        typer.echo(f"No Padres game found on {on_date}.")
        return

    typer.echo(f"{snap.scoreline()}  ·  {snap.detail}")
    if snap.inning and snap.half:
        typer.echo(f"{snap.half} {snap.inning}")
    if snap.last_pitch:
        lp = snap.last_pitch
        typer.echo(f"{lp.pitcher} to {lp.batter}: {lp.describe()}")
    if snap.batter_line:
        typer.echo(f"{snap.batter_line.name} tonight: {snap.batter_line.line()}")
    if not snap.is_live:
        typer.echo(f"(not live — game state: {snap.state})")
    if snap.as_of:
        typer.echo(f"live · unofficial · as of {snap.as_of}")


@live_app.command("watch")
def live_watch_cmd(
    interval: float = typer.Option(10.0, "--interval", help="Seconds between polls."),
    once: bool = typer.Option(False, "--once", help="Poll a single time and exit."),
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD. Defaults to today (LA)."),
) -> None:
    """Poll the Padres' current game and persist pitches to live_pitches.

    Runs until the game goes Final (Ctrl-C to stop), or once with --once. The
    table is unofficial and per-game; it never feeds the season/skill tables.
    """
    configure_logging()
    from padres_analytics.ingest.live_poll import poll_once, watch
    from padres_analytics.ingest.mlb_api import MlbApiError, MlbStatsClient
    from padres_analytics.live import resolve_game_pk
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    on_date = on or _la_today().isoformat()
    try:
        with MlbStatsClient() as client, connect() as conn:
            initialize(conn)  # ensure live_pitches exists (schema v10+)
            game_pk = resolve_game_pk(client, on_date)
            if game_pk is None:
                typer.echo(f"No Padres game found on {on_date}.")
                return
            if once:
                snap, n = poll_once(client, conn, game_pk)
                typer.echo(f"game {game_pk}: {snap.state} · {n} pitches stored")
            else:
                typer.echo(f"Watching game {game_pk} (every {interval:.0f}s, Ctrl-C to stop)…")
                polls = watch(client, conn, game_pk, interval=interval)
                typer.echo(f"Done — {polls} poll(s), game Final.")
    except MlbApiError as exc:
        typer.echo(f"Error reaching the MLB feed: {exc}", err=True)
        raise typer.Exit(ERR) from exc
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@live_app.command("ask")
def live_ask_cmd(
    question: str = typer.Argument(..., help='e.g. "how is King throwing tonight"'),
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD. Defaults to today (LA)."),
) -> None:
    """Ask about tonight's game in plain language (player must be in the game)."""
    configure_logging()
    from padres_analytics.ingest.mlb_api import MlbApiError
    from padres_analytics.live_ask import answer

    on_date = on or _la_today().isoformat()
    try:
        typer.echo(answer(question, on_date))
    except MlbApiError as exc:
        typer.echo(f"Error reaching the MLB feed: {exc}", err=True)
        raise typer.Exit(ERR) from exc


@live_app.command("serve")
def live_serve_cmd(
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD. Defaults to today (LA)."),
    game_pk: int = typer.Option(0, "--game-pk", help="Watch a specific game, skip resolution."),
    preview_interval: float = typer.Option(60.0, "--preview-interval", help="Poll gap pre-game."),
    live_interval: float = typer.Option(10.0, "--live-interval", help="Poll gap once live."),
    max_cycles: int = typer.Option(0, "--max-cycles", help="Cap polls (0 = until Final)."),
) -> None:
    """Daemon: poll the Padres' game from warmup to Final, persisting pitches.

    Schedule it daily near first pitch (see live_serve.py for cron/launchd snippets).
    """
    configure_logging()
    from padres_analytics.ingest.live_serve import serve, serve_today
    from padres_analytics.ingest.mlb_api import MlbApiError, MlbStatsClient
    from padres_analytics.storage.db import connect
    from padres_analytics.storage.schemas import initialize

    on_date = on or _la_today().isoformat()
    cap = max_cycles or None
    try:
        with MlbStatsClient() as client, connect() as conn:
            initialize(conn)  # ensure live_pitches exists (schema v10+)
            if game_pk:
                polls = serve(
                    client,
                    conn,
                    game_pk,
                    preview_interval=preview_interval,
                    live_interval=live_interval,
                    max_cycles=cap,
                )
            else:
                polls = serve_today(
                    client,
                    conn,
                    on_date,
                    preview_interval=preview_interval,
                    live_interval=live_interval,
                    max_cycles=cap,
                )
            if polls is None:
                typer.echo(f"No Padres game found on {on_date}.")
                return
            typer.echo(f"Done — {polls} poll(s).")
    except MlbApiError as exc:
        typer.echo(f"Error reaching the MLB feed: {exc}", err=True)
        raise typer.Exit(ERR) from exc
    except KeyboardInterrupt:
        typer.echo("\nStopped.")


@live_app.command("card")
def live_card_cmd(
    on: str = typer.Option("", "--on", help="Date YYYY-MM-DD. Defaults to today (LA)."),
    game_pk: int = typer.Option(0, "--game-pk", help="Render a specific game, skip resolution."),
) -> None:
    """Render the strongest card-worthy live moment (dominant starter or a Padre's big night)."""
    configure_logging()
    from padres_analytics.ingest.mlb_api import MlbApiError, MlbStatsClient
    from padres_analytics.live import resolve_game_pk
    from padres_analytics.live_moments import render_live_moment

    on_date = on or _la_today().isoformat()
    try:
        with MlbStatsClient() as client:
            pk = game_pk or resolve_game_pk(client, on_date)
            if pk is None:
                typer.echo(f"No Padres game found on {on_date}.")
                return
            feed = client.live_feed(pk)
        out = render_live_moment(feed, CARDS_DIR, f"live_{pk}")
    except MlbApiError as exc:
        typer.echo(f"Error reaching the MLB feed: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    if out is None:
        typer.echo("No card-worthy moment yet — no dominant start or standout bat. Try later.")
        return
    typer.echo(f"Rendered live moment → {out}")


@app.command()
def hr_spray(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a home-run spray card (landing spots + true distance) from stored events."""
    configure_logging()
    from padres_analytics.detect.spatial import build_hr_spray
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_hr_spray(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No regular-season home runs for player {player}, season {ref_season}. "
            f"Run 'pad ingest batted-balls --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"hr_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-HR spray → {out}")


@app.command()
def launch(
    player: int = typer.Option(..., "--player", help="MLBAM batter id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a launch-angle / exit-velo card from stored batted balls."""
    configure_logging()
    from padres_analytics.detect.spatial import build_launch
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_launch(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No batted balls with launch data for player {player}, season {ref_season}. "
            f"Run 'pad ingest batted-balls --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"launch_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-BBE launch profile → {out}")


@app.command()
def arsenal(
    player: int = typer.Option(..., "--player", help="MLBAM pitcher id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a pitch-movement (arsenal) card from stored pitches."""
    configure_logging()
    from padres_analytics.detect.spatial import build_arsenal
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_arsenal(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No pitches with movement data for pitcher {player}, season {ref_season}. "
            f"Run 'pad ingest pitches --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"arsenal_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-pitch arsenal → {out}")


@app.command()
def zone(
    player: int = typer.Option(..., "--player", help="MLBAM pitcher id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
    pitch: str = typer.Option("", "--pitch", help="Restrict to one pitch type, e.g. SL."),
) -> None:
    """Render a pitch-location density (zone) card from stored pitches."""
    configure_logging()
    from padres_analytics.detect.spatial import build_zone
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    pt = pitch.upper() or None

    with connect(read_only=True) as conn:
        dataset = build_zone(conn, player, ref_season, pitch_type=pt)

    if dataset is None:
        typer.echo(
            f"No pitches with location data for pitcher {player}, season {ref_season}. "
            f"Run 'pad ingest pitches --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    cid = f"zone_{player}_{ref_season}" + (f"_{pt}" if pt else "")
    try:
        out = render_card(dataset, CARDS_DIR, cid)
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-pitch zone → {out}")


@app.command()
def release(
    player: int = typer.Option(..., "--player", help="MLBAM pitcher id."),
    season: int = typer.Option(0, "--season", help="Season year. Defaults to current year."),
) -> None:
    """Render a release-point card from stored pitches."""
    configure_logging()
    from padres_analytics.detect.spatial import build_release
    from padres_analytics.render.cards import RenderError
    from padres_analytics.render.cards import render as render_card
    from padres_analytics.storage.db import connect

    ref_season = season or _la_today().year
    with connect(read_only=True) as conn:
        dataset = build_release(conn, player, ref_season)

    if dataset is None:
        typer.echo(
            f"No pitches with release data for pitcher {player}, season {ref_season}. "
            f"Run 'pad ingest pitches --player {player} --season {ref_season}' first.",
            err=True,
        )
        raise typer.Exit(ERR)

    try:
        out = render_card(dataset, CARDS_DIR, f"release_{player}_{ref_season}")
    except RenderError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(ERR) from exc

    typer.echo(f"Rendered {dataset.n}-pitch release point → {out}")


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
