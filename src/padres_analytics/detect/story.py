"""Compose story cards — multi-panel infographics from verified data.

A story card narrates a situation (e.g. the current funk) by composing a macro
hook with several player percentile callouts. Numbers come straight from the
ingested tables; the panel selection + reads are editorial.
"""

from __future__ import annotations

from datetime import date

import duckdb

from padres_analytics.detect.candidates import StoryBlock, StoryCard

# Curated funk panels: (name LIKE, percentile column, metric label, tone, read).
# The number is pulled live; the framing is the editorial choice.
_FUNK_PANELS: tuple[tuple[str, str, str, str, str], ...] = (
    ("Machado", "xwoba", "xwOBA", "bad", "The captain stuck in neutral"),
    ("Bogaerts", "hard_hit_percent", "Hard-Hit %", "bad", "Contact gone soft"),
    ("Andujar", "chase_percent", "Chase %", "bad", "Chasing out of the zone"),
    ("Tatis", "hard_hit_percent", "Hard-Hit %", "good", "Still punishing the ball"),
    ("Laureano", "brl_percent", "Barrel %", "good", "Quiet thump in the lineup"),
)


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]}"


def _display_name(raw: str | None, fallback: str) -> str:
    if not raw:
        return fallback
    if ", " in raw:
        last, first = raw.split(", ", 1)
        return f"{first} {last}"
    return raw


def build_funk_story(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    *,
    as_of: date | None = None,
) -> StoryCard | None:
    """Compose the 'state of the funk' story card from standings + percentiles.

    Args:
        conn: Read connection to padres.db.
        season: Season year.
        as_of: Card date; defaults to today.

    Returns:
        A validated ``StoryCard``, or ``None`` if the standings/percentile data
        needed for the hook isn't ingested.
    """
    # Macro hook — Padres record + games back of the division leader.
    try:
        pad = conn.execute(
            """
            SELECT wins, losses, games_back, division_id
            FROM standings WHERE team_name = 'Padres' AND season = ?
            """,
            [season],
        ).fetchone()
    except duckdb.CatalogException:
        return None  # standings not ingested yet
    if pad is None:
        return None
    wins, losses, gb_raw, division_id = pad
    try:
        gb = float(gb_raw)
    except (TypeError, ValueError):
        gb = 0.0
    leader = conn.execute(
        """
        SELECT team_name FROM standings
        WHERE division_id = ? AND season = ?
        ORDER BY win_pct DESC LIMIT 1
        """,
        [division_id, season],
    ).fetchone()
    leader_name = leader[0] if leader else "the division"

    blocks: list[StoryBlock] = []
    for name_like, col, metric, tone, note in _FUNK_PANELS:
        row = conn.execute(
            f"""
            SELECT player_id, player_name, {col}
            FROM statcast_batter_percentile_ranks
            WHERE year = ? AND player_name LIKE ?
            """,
            [season, f"%{name_like}%"],
        ).fetchone()
        if row is None or row[2] is None:
            continue
        pid, pname, pct = int(row[0]), row[1], int(row[2])
        blocks.append(
            StoryBlock(
                player_id=pid,
                label=_display_name(pname, name_like),
                metric=metric,
                value=_ordinal(pct),
                percentile=pct,
                note=note,
                tone=tone,  # type: ignore[arg-type]
            )
        )

    if not blocks:
        return None

    return StoryCard(
        title="Treading Water",
        kicker="San Diego Padres · State of the Lineup",
        subtitle=f"{season} · through {as_of or date.today()}",
        as_of=as_of or date.today(),
        hero={
            "value": f"{gb:.0f}",
            "label": "Games Back",
            "context": f"{wins}-{losses} · behind {leader_name}",
        },
        blocks=blocks,
        narrative=(
            "Tatis is carrying a lineup whose middle went quiet — the contact "
            "quality is still there, the runs aren't."
        ),
        source="MLB Stats API · Baseball Savant",
        headline=f"Padres {wins}-{losses}, {gb:.0f} back — the funk, by the numbers",
        claim_scope=f"{season} season to date",
    )
