"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from padres_analytics.storage.schemas import initialize


@pytest.fixture()
def padres_db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """In-process DuckDB with full padres.db schema initialized.

    Yields an open write-mode connection; caller must not close it.
    """
    db_path = tmp_path / "padres.db"
    conn = duckdb.connect(str(db_path))
    initialize(conn)
    return conn


@pytest.fixture()
def padres_db_with_hist(
    padres_db: duckdb.DuckDBPyConnection, tmp_path: Path
) -> duckdb.DuckDBPyConnection:
    """padres.db connection with a fixture hist (trades.db) attached.

    The fixture hist includes:
    - game_logs: a small set of Padres games on known calendar dates
    - transactions: a small set of notable Padres transactions
    """
    hist_path = tmp_path / "hist.db"
    hist = duckdb.connect(str(hist_path))

    hist.execute("""
        CREATE TABLE game_logs (
            game_date           DATE NOT NULL,
            game_number         VARCHAR NOT NULL,
            season              INTEGER NOT NULL,
            day_of_week         VARCHAR,
            visitor_team_bref   VARCHAR NOT NULL,
            visitor_league      VARCHAR,
            visitor_game_number INTEGER,
            home_team_bref      VARCHAR NOT NULL,
            home_league         VARCHAR,
            home_game_number    INTEGER,
            visitor_score       INTEGER,
            home_score          INTEGER,
            game_length_outs    INTEGER,
            day_night           VARCHAR,
            park_id             VARCHAR,
            attendance          INTEGER,
            game_time_minutes   INTEGER,
            source              VARCHAR NOT NULL,
            ingested_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (game_date, game_number, visitor_team_bref, home_team_bref)
        )
    """)

    _gl_sql = """
        INSERT INTO game_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    # Jun 9 games for the Padres (known fixture data)
    # fmt: off
    hist.executemany(_gl_sql, [
        ("2023-06-09","0",2023,"Fri","MIL","NL",60,"SDP","NL",61,3,5,27,"N","SDP01",42000,175,"retrosheet",None),
        ("2022-06-09","0",2022,"Thu","SDP","NL",58,"LAD","NL",62,2,7,27,"N","LAN01",51000,190,"retrosheet",None),
        ("2021-06-09","0",2021,"Wed","NYM","NL",55,"SDP","NL",57,4,6,27,"N","SDP01",30000,168,"retrosheet",None),
        ("2019-06-09","0",2019,"Sun","SDP","NL",60,"STL","NL",65,7,3,27,"D","SLN01",43000,162,"retrosheet",None),
        ("2015-06-09","0",2015,"Tue","SDP","NL",58,"COL","NL",59,9,2,27,"N","DEN01",28000,155,"retrosheet",None),
    ])
    # fmt: on

    hist.execute("""
        CREATE TABLE transactions (
            transaction_id BIGINT NOT NULL,
            leg_index      INTEGER NOT NULL,
            date           DATE,
            effective_date DATE,
            resolution_date DATE,
            type_code      VARCHAR NOT NULL,
            type_desc      VARCHAR,
            description    VARCHAR,
            from_team_id   INTEGER,
            from_team_name VARCHAR,
            to_team_id     INTEGER,
            to_team_name   VARCHAR,
            player_id      INTEGER,
            player_name    VARCHAR,
            season         INTEGER,
            source         VARCHAR NOT NULL,
            ingested_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (transaction_id, leg_index)
        )
    """)

    _tx_sql = """
        INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    _cordero_desc = "San Diego Padres traded OF Franchy Cordero to Kansas City Royals."
    _upton_desc = "San Diego Padres designated Justin Upton for assignment."
    # A notable trade on Jun 9 (fixture)
    hist.executemany(
        _tx_sql,
        [
            (
                1001,
                1,
                "2018-06-09",
                "2018-06-09",
                None,
                "TR",
                "Trade",
                _cordero_desc,
                135,
                "San Diego Padres",
                118,
                "Kansas City Royals",
                660261,
                "Franchy Cordero",
                2018,
                "mlb",
                None,
            ),
            (
                1002,
                1,
                "2015-06-09",
                "2015-06-09",
                None,
                "DES",
                "Designated for Assignment",
                _upton_desc,
                135,
                "San Diego Padres",
                None,
                None,
                455931,
                "Justin Upton",
                2015,
                "mlb",
                None,
            ),
        ],
    )

    hist.close()

    # Attach to padres_db as hist
    padres_db.execute(f"ATTACH '{hist_path}' AS hist (READ_ONLY)")
    return padres_db
