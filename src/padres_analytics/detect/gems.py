"""Gem engine — Sarah Langs-style 'first since / Nth all-time / chase' gems.

Computes real franchise career leaderboards from player_season_batting and
surfaces the most compelling *active-player* situation for each counting stat:
the all-time lead, a close chase of a named legend, or a notable all-time rank.

This is the engine shape Langs gems share — rare standing + named precedent —
running on real 1969-present franchise history.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    ChartDataset,
    Column,
    Mark,
    StatCandidate,
    make_candidate_id,
)
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.detect.sql import ordinal

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# (column, singular noun, abbreviation, "close chase" gap threshold)
_STATS: tuple[tuple[str, str, str, int], ...] = (
    ("hr", "home run", "HR", 6),
    ("hits", "hit", "H", 40),
    ("rbi", "RBI", "RBI", 25),
    ("sb", "stolen base", "SB", 8),
    ("doubles", "double", "2B", 12),
)
_TOP_N = 10  # rows shown on the all-time bar card
_RANK_CEILING = 15  # only surface an active player inside the all-time top N


def _career_leaderboard(
    conn: duckdb.DuckDBPyConnection, col: str, table: str = "player_season_batting"
) -> list[tuple[int, str, int, int]]:
    """Return (player_id, name, career_total, last_season) for a stat, best-first."""
    return conn.execute(
        f"""
        SELECT player_id, MAX(player_name) AS name, SUM({col}) AS total, MAX(season) AS last_yr
        FROM {table}
        GROUP BY player_id
        HAVING SUM({col}) > 0
        ORDER BY total DESC
        """
    ).fetchall()


def _active_players(
    conn: duckdb.DuckDBPyConnection, season: int, table: str = "player_season_batting"
) -> set[int]:
    """Player ids with a row in the given season/table (i.e., active this year)."""
    try:
        return {
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT player_id FROM {table} WHERE season = ?", [season]
            ).fetchall()
        }
    except Exception:
        return set()


def _build_chase_card(
    *,
    detector: str,
    board: list[tuple[int, str, int, int]],
    col: str,
    noun: str,
    abbr: str,
    gap_thresh: int,
    active: set[int],
    as_of: date,
    source_table: str,
) -> StatCandidate | None:
    """Build one all-time franchise leaderboard + chase gem card (stat-agnostic)."""
    from padres_analytics.detect.sql import fmt_name

    focal_rank = next(
        (i for i, r in enumerate(board) if r[0] in active and i < _RANK_CEILING), None
    )
    if focal_rank is None:
        return None

    pid, pname_raw, total, _last = board[focal_rank]
    pname = fmt_name(pname_raw)
    rank1 = focal_rank + 1

    if focal_rank == 0:
        headline = f"{pname} is the Padres' all-time {noun} leader ({total} {abbr})"
        tier = "franchise_record"
    else:
        ahead_name = fmt_name(board[focal_rank - 1][1])
        ahead_total = board[focal_rank - 1][2]
        gap = ahead_total - total
        if 0 < gap <= gap_thresh:
            headline = (
                f"{pname} ({total} {abbr}) needs {gap} to pass {ahead_name} "
                f"({ahead_total}) for {ordinal(rank1 - 1)} on the Padres' all-time {noun} list"
            )
            tier = "chase"
        else:
            headline = (
                f"{pname} is {ordinal(rank1)} on the Padres' all-time {noun} list ({total} {abbr})"
            )
            tier = "rank"

    top = board[:_TOP_N]
    rows: list[list[str | int | float | None]] = [[fmt_name(r[1]), int(r[2])] for r in top]
    highlight = [
        Mark(row_index=i, label=fmt_name(r[1])) for i, r in enumerate(top) if r[0] in active
    ]

    dataset = ChartDataset(
        title=f"PADRES ALL-TIME {abbr}",
        subtitle=f"Franchise career {noun}s · 1969-{as_of.year}",
        as_of=as_of,
        columns=[
            Column(key="player", label="Player", role="dimension"),
            Column(key=col, label=abbr, role="measure", format="d", higher_is_better=True),
        ],
        rows=rows,
        highlight=highlight,
        framing=headline,
        source="MLB Stats API",
        headline=headline,
        claim_scope="franchise_1969",
        population_label=f"Padres franchise, 1969-{as_of.year}",
        card_hint="bar",
        facts={
            "player_name": pname,
            "career_total": int(total),
            "franchise_rank": rank1,
            "stat": abbr,
            "tier": tier,
        },
    )

    rarity = {"franchise_record": 0.95, "chase": 0.9, "rank": 0.78}[tier]
    score, components = novelty_score(
        {
            "rarity": rarity,
            "magnitude": max(0.0, 1.0 - (rank1 - 1) / _RANK_CEILING),
            "timeliness": 0.85,
            "rootability": 0.92,
            "legibility": 0.95,
        },
        detector=detector,
    )
    subject = f"SDP|career_{col}|{pid}|{as_of.year}"
    cid = make_candidate_id(detector, subject, dataset.model_dump(mode="json"))
    return StatCandidate(
        candidate_id=cid,
        detector=detector,
        subject=subject,
        as_of=as_of,
        category="franchise",
        payload_kind="dataset",
        facts_json=dataset.model_dump(mode="json"),
        provenance_json=[{"source_table": source_table, "as_of": str(as_of)}],
        coverage_window=f"1969-{as_of.year}",
        claim_scope="franchise_1969",
        novelty_score=score,
        novelty_components=components,
    )


class CareerChaseDetector:
    """Franchise all-time hitting leaderboards + active-Padre chase gems."""

    name = "career_chase"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit one all-time hitting gem card per counting stat with a live storyline."""
        active = _active_players(conn, as_of.year)
        if not active:
            return []
        out: list[StatCandidate] = []
        for col, noun, abbr, gap_thresh in _STATS:
            board = _career_leaderboard(conn, col)
            if len(board) < _TOP_N:
                continue
            cand = _build_chase_card(
                detector=self.name,
                board=board,
                col=col,
                noun=noun,
                abbr=abbr,
                gap_thresh=gap_thresh,
                active=active,
                as_of=as_of,
                source_table="player_season_batting",
            )
            if cand is not None:
                out.append(cand)
        return out


# (column, singular noun, abbr, "close chase" gap) for pitching counting stats.
_PITCH_STATS: tuple[tuple[str, str, str, int], ...] = (
    ("so", "strikeout", "K", 40),
    ("saves", "save", "SV", 8),
    ("wins", "win", "W", 5),
)


class PitcherCareerChaseDetector:
    """Franchise all-time pitching leaderboards + active-Padre chase gems."""

    name = "pitcher_career_chase"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit all-time pitching gem cards (K/SV/W) with live chase storylines."""
        active = _active_players(conn, as_of.year, table="player_season_pitching")
        if not active:
            return []
        out: list[StatCandidate] = []
        for col, noun, abbr, gap_thresh in _PITCH_STATS:
            try:
                board = _career_leaderboard(conn, col, table="player_season_pitching")
            except Exception:
                continue
            if len(board) < _TOP_N:
                continue
            cand = _build_chase_card(
                detector=self.name,
                board=board,
                col=col,
                noun=noun,
                abbr=abbr,
                gap_thresh=gap_thresh,
                active=active,
                as_of=as_of,
                source_table="player_season_pitching",
            )
            if cand is not None:
                out.append(cand)
        return out


register(CareerChaseDetector())
register(PitcherCareerChaseDetector())


# (column, singular noun, abbr, round milestones, proximity gap)
_CLUB_MILESTONES: tuple[tuple[str, str, str, tuple[int, ...], int], ...] = (
    ("hr", "home run", "HR", (100, 150, 200, 250, 300), 12),
    ("hits", "hit", "H", (500, 1000, 1500, 2000, 2500, 3000), 80),
    ("rbi", "RBI", "RBI", (500, 750, 1000, 1250), 50),
    ("sb", "stolen base", "SB", (100, 150, 200, 300), 15),
    ("doubles", "double", "2B", (200, 300, 400), 25),
)
_MAX_CLUB_RANK = 12  # only surface genuinely exclusive clubs ("Nth ever" must be small)


class MilestoneClubDetector:
    """'Nth Padre ever to reach [round milestone]' — rare-club approach gems.

    Forward-looking and valid mid-season: a player's career total is monotonic,
    so "N away from the 200-HR club, would be the Mth Padre ever" is always true.
    """

    name = "milestone_club"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit a rare-club approach gem per stat where an active Padre is near a milestone.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            Up to len(_CLUB_MILESTONES) gem cards.
        """
        from padres_analytics.detect.sql import fmt_name

        try:
            active = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT player_id FROM player_season_batting WHERE season = ?",
                    [as_of.year],
                ).fetchall()
            }
        except Exception:
            return []
        if not active:
            return []

        out: list[StatCandidate] = []
        for col, noun, abbr, milestones, gap_max in _CLUB_MILESTONES:
            board = _career_leaderboard(conn, col)  # (pid, name, total, last_yr) best-first
            totals = [t for _, _, t, _ in board]
            # Best active player approaching (but not yet at) a round milestone.
            best: tuple | None = None
            for pid, name_raw, total, _last in board:
                if pid not in active:
                    continue
                nxt = next((m for m in milestones if m > total), None)
                if nxt is None:
                    continue
                gap = nxt - total
                if 0 < gap <= gap_max and (best is None or gap < best[3]):
                    best = (pid, fmt_name(name_raw), total, gap, nxt)
            if best is None:
                continue
            pid, pname, total, gap, milestone = best
            club_size = sum(1 for t in totals if t >= milestone)
            nth = club_size + 1
            # Exclusivity gate: "2nd ever" is a gem, "33rd ever" is not.
            if nth > _MAX_CLUB_RANK:
                continue
            if club_size == 0:
                club_line = f"the first Padre ever to reach {milestone}"
            else:
                club_line = f"the {ordinal(nth)} Padre ever to reach {milestone}"
            headline = (
                f"{pname} is {gap} {abbr} from {milestone} career {noun}s as a Padre — "
                f"would be {club_line}"
            )

            dataset = ChartDataset(
                title=pname.upper(),
                subtitle=f"Career {noun}s · Padres franchise",
                as_of=as_of,
                columns=[Column(key=col, label=abbr, role="measure", format="d")],
                rows=[[int(total)]],
                hero={
                    "value": str(gap),
                    "label": f"{abbr} from {milestone}",
                    "context": f"{total} now · would be {club_line.replace('the ', '')}",
                },
                framing=headline,
                source="MLB Stats API",
                headline=headline,
                claim_scope="franchise_1969",
                card_hint="hero",
                facts={
                    "padre_player_id": pid,
                    "player_name": pname,
                    "career_total": int(total),
                    "milestone": int(milestone),
                    "gap": int(gap),
                    "club_size": club_size,
                    "would_be_nth": nth,
                    "stat": abbr,
                },
            )
            score, components = novelty_score(
                {
                    "rarity": min(0.86 + (5 - club_size) * 0.02, 0.96) if club_size < 5 else 0.84,
                    "magnitude": max(0.0, 1.0 - gap / gap_max),
                    "timeliness": 0.85,
                    "rootability": 0.9,
                    "legibility": 0.95,
                },
                detector=self.name,
            )
            subject = f"SDP|club_{col}|{pid}|{milestone}"
            cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
            out.append(
                StatCandidate(
                    candidate_id=cid,
                    detector=self.name,
                    subject=subject,
                    as_of=as_of,
                    category="franchise",
                    payload_kind="dataset",
                    facts_json=dataset.model_dump(mode="json"),
                    provenance_json=[
                        {"source_table": "player_season_batting", "as_of": str(as_of)}
                    ],
                    coverage_window=f"1969-{as_of.year}",
                    claim_scope="franchise_1969",
                    novelty_score=score,
                    novelty_components=components,
                )
            )
        return out


register(MilestoneClubDetector())


_MIN_HIT_STREAK = 8  # games — below this isn't gem-worthy


class HitStreakDetector:
    """Active hit-streak gems from current-season game logs.

    A game with no official at-bat (walk-only, pinch-run) neither extends nor
    breaks the streak — the standard MLB hit-streak rule.
    """

    name = "hit_streak"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit the longest active Padres hit streak, if >= _MIN_HIT_STREAK.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            A single-element list (the longest active streak), or empty.
        """
        from padres_analytics.detect.sql import fmt_name

        try:
            rows = conn.execute(
                """
                SELECT player_id, player_name, game_date, ab, hits
                FROM player_game_batting
                WHERE season = ?
                ORDER BY player_id, game_date
                """,
                [as_of.year],
            ).fetchall()
        except Exception as exc:
            logger.warning("hit_streak: no player_game_batting (%s)", exc)
            return []
        if not rows:
            return []

        by_player: dict[int, list[tuple]] = {}
        names: dict[int, str] = {}
        for pid, name, gdate, ab, hits in rows:
            by_player.setdefault(pid, []).append((gdate, int(ab), int(hits)))
            names[pid] = name

        best_pid, best_streak = None, 0
        for pid, games in by_player.items():
            streak = 0
            for _gdate, ab, hits in reversed(games):  # newest first
                if ab == 0:
                    continue  # no AB: neither extends nor breaks
                if hits >= 1:
                    streak += 1
                else:
                    break
            if streak > best_streak:
                best_pid, best_streak = pid, streak

        if best_pid is None or best_streak < _MIN_HIT_STREAK:
            return []

        pname = fmt_name(names[best_pid])
        headline = f"{pname} has hit safely in {best_streak} straight games"

        dataset = ChartDataset(
            title=pname.upper(),
            subtitle=f"Active hit streak · {as_of.year}",
            as_of=as_of,
            columns=[Column(key="games", label="Games", role="measure", format="d")],
            rows=[[best_streak]],
            hero={
                "value": str(best_streak),
                "label": "Game hit streak",
                "context": "Active — and counting",
            },
            framing=headline,
            source="MLB Stats API",
            headline=headline,
            claim_scope=f"{as_of.year}",
            card_hint="hero",
            facts={
                "padre_player_id": best_pid,
                "player_name": pname,
                "streak_games": best_streak,
            },
        )
        score, components = novelty_score(
            {
                "rarity": min(0.80 + (best_streak - _MIN_HIT_STREAK) * 0.02, 0.97),
                "magnitude": min(best_streak / 30.0, 1.0),
                "timeliness": 1.0,
                "rootability": 0.9,
                "legibility": 0.95,
            },
            detector=self.name,
        )
        subject = f"SDP|hit_streak|{best_pid}|{as_of.year}"
        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=subject,
                as_of=as_of,
                category="season",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[{"source_table": "player_game_batting", "as_of": str(as_of)}],
                coverage_window=f"{as_of.year}-{as_of.year}",
                claim_scope=f"{as_of.year}",
                novelty_score=score,
                novelty_components=components,
            )
        ]


register(HitStreakDetector())


# Round thresholds per stat, for flooring a career total to a "club" line.
_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "hr": (100, 150, 200, 250, 300),
    "sb": (50, 100, 150, 200, 250),
    "hits": (500, 1000, 1500, 2000, 2500, 3000),
    "doubles": (150, 200, 250, 300),
    "rbi": (500, 750, 1000, 1250),
}
# Career stat pairs that make a compelling "rare club" (label A, col_a, label B, col_b).
_CONJ_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("HR", "hr", "SB", "sb"),  # power / speed — the classic
    ("HR", "hr", "H", "hits"),
)
_MAX_CONJ_CLUB = 6  # only surface genuinely exclusive joint clubs


def _floor_to(value: int, steps: tuple[int, ...]) -> int | None:
    """Largest round step <= value, or None if below the smallest."""
    hit = [s for s in steps if s <= value]
    return max(hit) if hit else None


class CareerConjunctionDetector:
    """'Only N Padres ever with X and Y' — career rare-club conjunction gems."""

    name = "career_conjunction"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit conjunction gems for active Padres in exclusive 2-stat career clubs.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            Conjunction gem cards (deduped to one per player).
        """
        from padres_analytics.detect.sql import fmt_name

        try:
            active = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT player_id FROM player_season_batting WHERE season = ?",
                    [as_of.year],
                ).fetchall()
            }
        except Exception:
            return []
        if not active:
            return []

        # Career totals for all stats we pair on, per player.
        cols = sorted({c for _, c, _, c2 in _CONJ_PAIRS for c in (c, c2)})
        sums = ", ".join(f"SUM({c}) AS {c}" for c in cols)
        rows = conn.execute(
            f"SELECT player_id, MAX(player_name) AS name, {sums} "
            f"FROM player_season_batting GROUP BY player_id"
        ).fetchall()
        # rows: (pid, name, <cols...>) — index cols by position
        col_idx = {c: 2 + i for i, c in enumerate(cols)}
        totals = [(r[0], r[1], r) for r in rows]

        out: list[StatCandidate] = []
        seen_players: set[int] = set()
        for label_a, col_a, label_b, col_b in _CONJ_PAIRS:
            best: tuple | None = None  # (club_size, pid, name, val_a, val_b, t_a, t_b)
            for pid, name, r in totals:
                if pid not in active or pid in seen_players:
                    continue
                val_a, val_b = int(r[col_idx[col_a]] or 0), int(r[col_idx[col_b]] or 0)
                t_a = _floor_to(val_a, _THRESHOLDS[col_a])
                t_b = _floor_to(val_b, _THRESHOLDS[col_b])
                if t_a is None or t_b is None:
                    continue
                club = sum(
                    1
                    for _p, _n, rr in totals
                    if (rr[col_idx[col_a]] or 0) >= t_a and (rr[col_idx[col_b]] or 0) >= t_b
                )
                if club <= _MAX_CONJ_CLUB and (best is None or club < best[0]):
                    best = (club, pid, fmt_name(name), val_a, val_b, t_a, t_b)
            if best is None:
                continue
            club, pid, pname, val_a, val_b, t_a, t_b = best
            seen_players.add(pid)
            if club == 1:
                lead = f"{pname} is the ONLY Padre ever with {t_a}+ {label_a} and {t_b}+ {label_b}"
            else:
                lead = (
                    f"{pname} is one of only {club} Padres ever with "
                    f"{t_a}+ {label_a} and {t_b}+ {label_b}"
                )
            headline = f"{lead} ({val_a} {label_a}, {val_b} {label_b})"

            dataset = ChartDataset(
                title=pname.upper(),
                subtitle=f"Career {label_a} & {label_b} · Padres franchise",
                as_of=as_of,
                columns=[Column(key="club", label="Club size", role="measure", format="d")],
                rows=[[club]],
                hero={
                    "value": "ONLY" if club == 1 else str(club),
                    "label": f"{t_a}+ {label_a} & {t_b}+ {label_b}, Padres history",
                    "context": f"{pname}: {val_a} {label_a}, {val_b} {label_b}",
                },
                framing=headline,
                source="MLB Stats API",
                headline=headline,
                claim_scope="franchise_1969",
                card_hint="hero",
                facts={
                    "padre_player_id": pid,
                    "player_name": pname,
                    "club_size": club,
                    f"career_{col_a}": val_a,
                    f"career_{col_b}": val_b,
                    "threshold_a": t_a,
                    "threshold_b": t_b,
                },
            )
            score, components = novelty_score(
                {
                    "rarity": min(0.97, 0.99 - (club - 1) * 0.03),
                    "magnitude": 0.85,
                    "timeliness": 0.8,
                    "rootability": 0.92,
                    "legibility": 0.92,
                },
                detector=self.name,
            )
            subject = f"SDP|conj_{col_a}_{col_b}|{pid}"
            cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
            out.append(
                StatCandidate(
                    candidate_id=cid,
                    detector=self.name,
                    subject=subject,
                    as_of=as_of,
                    category="franchise",
                    payload_kind="dataset",
                    facts_json=dataset.model_dump(mode="json"),
                    provenance_json=[
                        {"source_table": "player_season_batting", "as_of": str(as_of)}
                    ],
                    coverage_window=f"1969-{as_of.year}",
                    claim_scope="franchise_1969",
                    novelty_score=score,
                    novelty_components=components,
                )
            )
        return out


register(CareerConjunctionDetector())
