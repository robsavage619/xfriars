"""Versioned DDL for padres-analytics DuckDB schemas.

Schemas evolve via additive migrations only. Every table carries
``ingested_at`` for provenance. SCHEMA_VERSION is bumped whenever DDL changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version  INTEGER NOT NULL,
        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Freshness guard — detectors refuse stale/partial inputs (§2.5)
    """
    CREATE TABLE IF NOT EXISTS ingest_runs (
        run_id       VARCHAR PRIMARY KEY,
        source       VARCHAR NOT NULL,
        started_at   TIMESTAMP,
        finished_at  TIMESTAMP,
        complete     BOOLEAN DEFAULT FALSE,
        rows_written INTEGER,
        note         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stat_candidates (
        candidate_id      VARCHAR PRIMARY KEY,
        detector          VARCHAR NOT NULL,
        subject           VARCHAR,
        as_of             DATE NOT NULL,
        category          VARCHAR,
        payload_kind      VARCHAR NOT NULL,
        facts_json        JSON NOT NULL,
        provenance_json   JSON NOT NULL,
        coverage_window   VARCHAR NOT NULL,
        claim_scope       VARCHAR NOT NULL,
        novelty_score     DOUBLE NOT NULL,
        novelty_components JSON,
        status            VARCHAR DEFAULT 'new',
        ingested_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidates_status
        ON stat_candidates(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidates_detector
        ON stat_candidates(detector)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_candidates_as_of
        ON stat_candidates(as_of)
    """,
    """
    CREATE TABLE IF NOT EXISTS tweet_drafts (
        draft_id             VARCHAR PRIMARY KEY,
        candidate_id         VARCHAR REFERENCES stat_candidates(candidate_id),
        draft_kind           VARCHAR DEFAULT 'feed',
        thread_id            VARCHAR,
        thread_order         INTEGER,
        reply_to_url         VARCHAR,
        text                 VARCHAR NOT NULL,
        media_path           VARCHAR,
        is_projection        BOOLEAN DEFAULT FALSE,
        model                VARCHAR,
        source               VARCHAR DEFAULT 'skill',
        interesting_judgment VARCHAR,
        verification_json    JSON,
        status               VARCHAR DEFAULT 'pending',
        created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        posted_tweet_id      VARCHAR,
        posted_at            TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS post_metrics (
        posted_tweet_id     VARCHAR,
        captured_at         TIMESTAMP,
        impressions         INTEGER,
        likes               INTEGER,
        reposts             INTEGER,
        replies             INTEGER,
        bookmarks           INTEGER,
        follows_attributed  INTEGER,
        PRIMARY KEY (posted_tweet_id, captured_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS predictions (
        prediction_id VARCHAR PRIMARY KEY,
        draft_id      VARCHAR,
        claim         VARCHAR NOT NULL,
        posted_at     TIMESTAMP,
        resolves_by   DATE,
        outcome       VARCHAR DEFAULT 'open'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS corrections (
        correction_id       VARCHAR PRIMARY KEY,
        original_tweet_id   VARCHAR,
        what_was_wrong      VARCHAR,
        root_cause          VARCHAR,
        corrected_at        TIMESTAMP
    )
    """,
    # ── Phase 2: current-season ingest tables ──────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS game_schedule (
        game_pk       INTEGER PRIMARY KEY,
        season        INTEGER NOT NULL,
        game_date     DATE NOT NULL,
        game_type     VARCHAR DEFAULT 'R',
        status        VARCHAR,
        home_team_id  INTEGER,
        away_team_id  INTEGER,
        home_team_abbr VARCHAR,
        away_team_abbr VARCHAR,
        venue_id      INTEGER,
        venue_name    VARCHAR,
        ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS game_box (
        game_pk       INTEGER PRIMARY KEY,
        game_date     DATE NOT NULL,
        home_team_id  INTEGER,
        away_team_id  INTEGER,
        home_score    INTEGER,
        away_score    INTEGER,
        innings       INTEGER,
        winning_pitcher_id   INTEGER,
        losing_pitcher_id    INTEGER,
        save_pitcher_id      INTEGER,
        ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_game_logs (
        game_pk       INTEGER NOT NULL,
        player_id     INTEGER NOT NULL,
        team_id       INTEGER NOT NULL,
        game_date     DATE NOT NULL,
        group_type    VARCHAR NOT NULL,
        stats_json    JSON NOT NULL,
        ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (game_pk, player_id, group_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_season_stats (
        player_id     INTEGER NOT NULL,
        season        INTEGER NOT NULL,
        team_id       INTEGER NOT NULL,
        group_type    VARCHAR NOT NULL,
        stats_json    JSON NOT NULL,
        player_name   VARCHAR,
        team_abbr     VARCHAR,
        ingested_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (player_id, season, team_id, group_type)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_player_season_stats_team
        ON player_season_stats(team_id, season)
    """,
    """
    CREATE TABLE IF NOT EXISTS mlb_leaders (
        season            INTEGER NOT NULL,
        stat_group        VARCHAR NOT NULL,
        stat_type         VARCHAR NOT NULL,
        rank              INTEGER NOT NULL,
        player_id         INTEGER NOT NULL,
        player_name       VARCHAR,
        team_id           INTEGER,
        team_abbr         VARCHAR,
        value             VARCHAR NOT NULL,
        fetched_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (season, stat_type, rank, player_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mlb_leaders_stat
        ON mlb_leaders(season, stat_type)
    """,
    # ── Statcast tables (pulled from Baseball Savant via pybaseball) ──────────
    """
    CREATE TABLE IF NOT EXISTS statcast_batter_percentile_ranks (
        player_id         INTEGER NOT NULL,
        player_name       VARCHAR NOT NULL,
        year              INTEGER NOT NULL,
        xwoba             DOUBLE,
        xba               DOUBLE,
        xslg              DOUBLE,
        xiso              DOUBLE,
        xobp              DOUBLE,
        brl               DOUBLE,
        brl_percent       DOUBLE,
        exit_velocity     DOUBLE,
        max_ev            DOUBLE,
        hard_hit_percent  DOUBLE,
        k_percent         DOUBLE,
        bb_percent        DOUBLE,
        whiff_percent     DOUBLE,
        chase_percent     DOUBLE,
        arm_strength      DOUBLE,
        sprint_speed      DOUBLE,
        oaa               DOUBLE,
        bat_speed         DOUBLE,
        squared_up_rate   DOUBLE,
        swing_length      DOUBLE,
        ingested_at       TIMESTAMP,
        PRIMARY KEY (player_id, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statcast_batting_expected (
        player_id   INTEGER NOT NULL,
        player_name VARCHAR NOT NULL,
        year        INTEGER NOT NULL,
        pa          INTEGER,
        bip         INTEGER,
        ba          DOUBLE,
        est_ba      DOUBLE,
        slg         DOUBLE,
        est_slg     DOUBLE,
        woba        DOUBLE,
        est_woba    DOUBLE,
        ingested_at TIMESTAMP,
        PRIMARY KEY (player_id, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statcast_sprint_speed (
        player_id        INTEGER NOT NULL,
        player_name      VARCHAR NOT NULL,
        year             INTEGER NOT NULL,
        sprint_speed     DOUBLE,
        competitive_runs INTEGER,
        ingested_at      TIMESTAMP,
        PRIMARY KEY (player_id, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statcast_batter_exitvelo_barrels (
        player_id     INTEGER NOT NULL,
        player_name   VARCHAR NOT NULL,
        year          INTEGER NOT NULL,
        attempts      INTEGER,
        avg_hit_speed DOUBLE,
        max_hit_speed DOUBLE,
        barrels       INTEGER,
        brl_percent   DOUBLE,
        ingested_at   TIMESTAMP,
        PRIMARY KEY (player_id, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statcast_batted_balls (
        player_id        INTEGER NOT NULL,
        player_name      VARCHAR,
        season           INTEGER NOT NULL,
        game_type        VARCHAR,
        game_date        DATE,
        game_pk          INTEGER NOT NULL,
        at_bat_number    INTEGER NOT NULL,
        pitch_number     INTEGER NOT NULL,
        events           VARCHAR,
        bb_type          VARCHAR,
        description      VARCHAR,
        stand            VARCHAR,
        p_throws         VARCHAR,
        hc_x             DOUBLE,
        hc_y             DOUBLE,
        launch_speed     DOUBLE,
        launch_angle     DOUBLE,
        hit_distance_sc  DOUBLE,
        estimated_woba   DOUBLE,
        ingested_at      TIMESTAMP,
        PRIMARY KEY (player_id, game_pk, at_bat_number, pitch_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sc_battedballs_player_season
        ON statcast_batted_balls(player_id, season)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sc_percentile_year
        ON statcast_batter_percentile_ranks(year)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sc_expected_year
        ON statcast_batting_expected(year)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sc_sprint_year
        ON statcast_sprint_speed(year)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sc_barrels_year
        ON statcast_batter_exitvelo_barrels(year)
    """,
)


def initialize(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables idempotently and record schema version.

    Args:
        conn: An open write-mode DuckDB connection to padres.db.
    """
    for stmt in DDL_STATEMENTS:
        conn.execute(stmt)

    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    recorded = current[0] if current else None

    if recorded is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
        logger.info("Initialized schema version %d", SCHEMA_VERSION)
    elif recorded < SCHEMA_VERSION:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", [SCHEMA_VERSION])
        logger.info("Migrated schema from %d to %d", recorded, SCHEMA_VERSION)
    else:
        logger.debug("Schema is current at version %d", recorded)
