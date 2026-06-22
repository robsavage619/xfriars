"""Data-coverage preflight — the engine's understanding of the stats it holds.

The detectors, scout, and story engine all run SQL over whatever happens to be
in ``padres.db``. Season *aggregates* can look complete while the *granular*
tables that a trend/approach/swing claim actually needs are stale, single-player,
or absent. That mismatch silently produces plausible-but-unsupported analysis,
which is an accuracy failure.

This module makes coverage an explicit precondition. A :class:`DomainSpec`
contract declares, per stat domain, the table, its granularity, whether it must
include the current season, whether a *prior-season baseline at the same
granularity* is required, a minimum player count, and the analytical
capabilities the domain underpins. :func:`audit` inspects the live database and
reports actual coverage and status; :func:`can_support` is the gate a caller
gates a claim on before promising it.

Read-only: nothing here mutates the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

# Analytical capabilities a claim can require. The gate is keyed on these.
CAREER_BASELINE = "career-baseline"  # multi-season aggregate comparison
SEASON_LUCK = "season-luck"  # wOBA vs xwOBA, season aggregate
APPROACH_STATE = "approach-state"  # current chase/whiff/K/BB level (no delta)
SWING_PATH_STATE = "swing-path-state"  # current bat-speed/length/squared-up level
GAME_TREND = "game-trend"  # game-by-game counting trend
CONTACT_TREND = "contact-trend"  # within-season EV/xwOBA-on-contact trend
APPROACH_TREND = "approach-trend"  # pitch-level chase/whiff trend (decision change)
SWING_PATH_CHANGE = "swing-path-change"  # bat-tracking delta vs a prior baseline


@dataclass(frozen=True)
class DomainSpec:
    """Contract for one stat domain — what it should hold and what it underpins.

    Args:
        domain: Human-readable domain name.
        table: Backing table in padres.db.
        granularity: One of ``season-agg`` | ``game`` | ``batted-ball`` | ``pitch``.
        needs_current: A useful domain must include the current season.
        needs_baseline: Change/trend claims need a prior season at this granularity.
        min_players: Below this many distinct players the domain is PARTIAL.
        supports: Capabilities this domain underpins when its status is OK.
    """

    domain: str
    table: str
    granularity: str
    needs_current: bool
    needs_baseline: bool
    min_players: int
    supports: tuple[str, ...]


# The contract. Order is roughly aggregate → granular.
CONTRACT: tuple[DomainSpec, ...] = (
    DomainSpec(
        "Season batting (career)",
        "player_season_batting",
        "season-agg",
        needs_current=True,
        needs_baseline=False,
        min_players=20,
        supports=(CAREER_BASELINE,),
    ),
    DomainSpec(
        "Expected (luck)",
        "statcast_batting_expected",
        "season-agg",
        needs_current=True,
        needs_baseline=False,
        min_players=100,
        supports=(SEASON_LUCK,),
    ),
    DomainSpec(
        "Percentile ranks",
        "statcast_batter_percentile_ranks",
        "season-agg",
        needs_current=True,
        needs_baseline=True,
        min_players=100,
        supports=(APPROACH_STATE, SWING_PATH_STATE),
    ),
    DomainSpec(
        "Game batting",
        "player_game_batting",
        "game",
        needs_current=True,
        needs_baseline=False,
        min_players=10,
        supports=(GAME_TREND,),
    ),
    DomainSpec(
        "Batted balls",
        "statcast_batted_balls",
        "batted-ball",
        needs_current=True,
        needs_baseline=False,
        min_players=20,
        supports=(CONTACT_TREND,),
    ),
    DomainSpec(
        "Pitch-level (batter)",
        "statcast_batter_pitches",
        "pitch",
        needs_current=True,
        needs_baseline=False,
        min_players=20,
        supports=(APPROACH_TREND,),
    ),
)

# Capabilities that additionally require a prior-season baseline to be honest.
# A "change" claim is unsupported unless the backing domain spans >=2 seasons.
_CHANGE_CAPS: frozenset[str] = frozenset({SWING_PATH_CHANGE, APPROACH_TREND, CONTACT_TREND})


@dataclass(frozen=True)
class CoverageReport:
    """Audited coverage for one domain."""

    domain: str
    table: str
    granularity: str
    rows: int
    seasons: tuple[int, ...]
    latest_date: date | None
    n_players: int
    status: str  # OK | STALE | PARTIAL | EMPTY | MISSING
    supports: tuple[str, ...]  # capabilities currently backed (status OK only)
    blocks: tuple[str, ...]  # declared capabilities NOT currently backed
    reason: str


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]
    except duckdb.Error:
        return []


def _count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    """Run a single-value COUNT query, returning 0 when the row is absent."""
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _current_season(conn: duckdb.DuckDBPyConnection) -> int:
    """Newest season present anywhere, falling back to the calendar year."""
    best = 0
    for table, col in (
        ("player_game_batting", "season"),
        ("standings", "season"),
        ("player_season_batting", "season"),
        ("statcast_batting_expected", "year"),
    ):
        try:
            row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
            if row and row[0]:
                best = max(best, int(row[0]))
        except duckdb.Error:
            continue
    return best or date.today().year


def _inspect(conn: duckdb.DuckDBPyConnection, spec: DomainSpec, current: int) -> CoverageReport:
    cols = _columns(conn, spec.table)
    if not cols:
        return CoverageReport(
            spec.domain,
            spec.table,
            spec.granularity,
            0,
            (),
            None,
            0,
            "MISSING",
            (),
            spec.supports,
            "table absent",
        )

    rows = _count(conn, f"SELECT COUNT(*) FROM {spec.table}")
    season_col = "season" if "season" in cols else ("year" if "year" in cols else None)
    seasons: tuple[int, ...] = ()
    if season_col:
        seasons = tuple(
            int(r[0])
            for r in conn.execute(
                f"SELECT DISTINCT {season_col} FROM {spec.table} "
                f"WHERE {season_col} IS NOT NULL ORDER BY 1"
            ).fetchall()
        )
    latest: date | None = None
    if "game_date" in cols:
        row = conn.execute(f"SELECT MAX(game_date) FROM {spec.table}").fetchone()
        latest = row[0] if row else None

    # Player key varies by table: season tables use player_id, event tables use
    # batter_id/pitcher_id, and some only carry player_name.
    id_col = next(
        (c for c in ("player_id", "batter_id", "pitcher_id", "player_name") if c in cols),
        None,
    )
    n_players = _count(conn, f"SELECT COUNT(DISTINCT {id_col}) FROM {spec.table}") if id_col else 0

    status, reason = _classify(spec, current, rows, seasons, n_players)
    has_baseline = len(seasons) >= 2
    backed: list[str] = []
    blocked: list[str] = []
    for cap in spec.supports:
        # A capability is blocked if the domain isn't OK, or it's a "change" read
        # with no prior-season baseline. A state read (needs_baseline) is still
        # backed — the missing-baseline caveat is carried in _classify's reason.
        if status != "OK" or (cap in _CHANGE_CAPS and not has_baseline):
            blocked.append(cap)
        else:
            backed.append(cap)
    return CoverageReport(
        spec.domain,
        spec.table,
        spec.granularity,
        rows,
        seasons,
        latest,
        n_players,
        status,
        tuple(backed),
        tuple(blocked),
        reason,
    )


def _classify(
    spec: DomainSpec,
    current: int,
    rows: int,
    seasons: tuple[int, ...],
    n_players: int,
) -> tuple[str, str]:
    if rows == 0:
        return "EMPTY", "no rows"
    if spec.needs_current and seasons and current not in seasons:
        return "STALE", f"newest season {seasons[-1]}, current is {current}"
    if n_players and n_players < spec.min_players:
        return "PARTIAL", f"{n_players} players (< {spec.min_players} expected)"
    if spec.needs_baseline and len(seasons) < 2:
        return "OK", "current-season state only — no prior-year baseline for deltas"
    return "OK", "current and adequately populated"


def audit(conn: duckdb.DuckDBPyConnection) -> list[CoverageReport]:
    """Inspect every contracted domain against the live database."""
    current = _current_season(conn)
    return [_inspect(conn, spec, current) for spec in CONTRACT]


def can_support(reports: list[CoverageReport], capability: str) -> tuple[bool, str]:
    """Gate a claim on coverage.

    Args:
        reports: Output of :func:`audit`.
        capability: One of the module-level capability constants.

    Returns:
        ``(True, reason)`` if some domain currently backs the capability,
        otherwise ``(False, reason)`` naming why it is unsupported.
    """
    for r in reports:
        if capability in r.supports:
            return True, f"{r.domain} ({r.table})"
    for r in reports:
        if capability in r.blocks:
            return False, f"{r.domain} blocked: {r.reason}"
    return False, "no domain in the contract underpins this capability"
