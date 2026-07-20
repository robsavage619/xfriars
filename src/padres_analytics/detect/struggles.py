"""The honest-negatives engine — slumps and weaknesses, framed straight.

A stat account that only posts flattering numbers isn't credible. These
detectors surface real struggles from the same data the gem engine uses, in
plain Langs-style language (no spin, no piling on):

- ColdStreakDetector  — active hitless skids from game logs ("0-for-his-last-N").
- WeaknessDetector     — bottom-percentile tools from Statcast ("Nth percentile").
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from padres_analytics.detect.base import register
from padres_analytics.detect.candidates import (
    ChartDataset,
    Column,
    RarityEvidence,
    StatCandidate,
    make_candidate_id,
)
from padres_analytics.detect.sql import fmt_name, ordinal, resolve_table

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

_MIN_SKID_GAMES = 4  # hitless games to qualify as a notable skid
_MIN_SKID_AB = 12  # at-bats in the skid (filters out bench cameos)


class ColdStreakDetector:
    """Active hitless skid for a regular — the honest mirror of hit_streak."""

    name = "cold_streak"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit the most notable active hitless skid, if it clears the thresholds.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            A single-element list (the deepest qualifying skid), or empty.
        """
        try:
            rows = conn.execute(
                """
                SELECT player_id, player_name, game_date, ab, hits
                FROM player_game_batting WHERE season = ?
                ORDER BY player_id, game_date
                """,
                [as_of.year],
            ).fetchall()
        except Exception as exc:
            logger.warning("cold_streak: no player_game_batting (%s)", exc)
            return []
        if not rows:
            return []

        by_player: dict[int, list[tuple]] = {}
        names: dict[int, str] = {}
        for pid, name, _gd, ab, hits in rows:
            by_player.setdefault(pid, []).append((int(ab), int(hits)))
            names[pid] = name

        best = None  # (skid_ab, pid, games, skid_ab)
        for pid, games in by_player.items():
            skid_games = skid_ab = 0
            for ab, hits in reversed(games):  # newest first
                if ab == 0:
                    continue
                if hits == 0:
                    skid_games += 1
                    skid_ab += ab
                else:
                    break
            qualifies = skid_games >= _MIN_SKID_GAMES and skid_ab >= _MIN_SKID_AB
            if qualifies and (best is None or skid_ab > best[0]):
                best = (skid_ab, pid, skid_games, skid_ab)
        if best is None:
            return []

        _key, pid, skid_games, skid_ab = best
        pname = fmt_name(names[pid])
        headline = f"{pname} is 0-for-his-last-{skid_ab}, hitless in {skid_games} straight games"

        dataset = ChartDataset(
            title=pname.upper(),
            subtitle=f"Active hitless skid · {as_of.year}",
            as_of=as_of,
            columns=[Column(key="ab", label="AB", role="measure", format="d")],
            rows=[[skid_ab]],
            hero={
                "value": f"0-{skid_ab}",
                "label": "Over his last " + str(skid_games) + " games",
                "context": "Looking to break out of it",
            },
            framing=headline,
            source="MLB Stats API",
            headline=headline,
            claim_scope=f"{as_of.year}",
            card_hint="hero",
            facts={
                "padre_player_id": pid,
                "player_name": pname,
                "skid_ab": skid_ab,
                "skid_games": skid_games,
            },
        )
        # The skid length is measured, but how unusual an 0-for-N is depends on
        # a hitter's own AB-level rate, which this detector never computes.
        evidence = RarityEvidence(kind="streak")
        subject = f"SDP|cold_streak|{pid}|{as_of.year}"
        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=subject,
                as_of=as_of,
                category="struggle",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[{"source_table": "player_game_batting", "as_of": str(as_of)}],
                coverage_window=f"{as_of.year}-{as_of.year}",
                claim_scope=f"{as_of.year}",
                novelty_score=0.0,  # overwritten by emit() from rarity_evidence
                rarity_evidence=evidence,
            )
        ]


# Statcast percentile columns where a LOW percentile is a meaningful weakness.
# (column, label) — percentile tables are pre-oriented (higher = better).
_WEAKNESS_METRICS: tuple[tuple[str, str], ...] = (
    ("xwoba", "xwOBA"),
    ("whiff_percent", "Whiff %"),
    ("chase_percent", "Chase %"),
    ("k_percent", "Strikeout %"),
    ("hard_hit_percent", "Hard-Hit %"),
    ("exit_velocity", "Exit Velocity"),
)
_WEAKNESS_CEILING = 12  # only surface genuinely poor tools (bottom ~12th percentile)


class WeaknessDetector:
    """Bottom-percentile tool for a Padres regular — honest, league-relative flaw."""

    name = "weakness"

    def run(self, conn: duckdb.DuckDBPyConnection, as_of: date) -> list[StatCandidate]:
        """Emit the single most extreme bottom-percentile weakness on the team.

        Args:
            conn: Read-mode padres.db connection.
            as_of: Reference date.

        Returns:
            A single-element list (the worst qualifying tool), or empty.
        """
        src = resolve_table(conn, "statcast_batter_percentile_ranks")
        cols = ", ".join(c for c, _ in _WEAKNESS_METRICS)
        try:
            regulars = {
                r[0]
                for r in conn.execute(
                    "SELECT player_id FROM player_season_batting "
                    "WHERE season = ? AND team_id = 135 AND pa >= 100",
                    [as_of.year],
                ).fetchall()
            }
            rows = conn.execute(
                f"SELECT player_id, player_name, {cols} FROM {src} WHERE year = ?",
                [as_of.year],
            ).fetchall()
        except Exception as exc:
            logger.warning("weakness: query failed (%s)", exc)
            return []
        if not regulars or not rows:
            return []

        worst = None  # (percentile, player_id, name, label)
        for r in rows:
            pid, name = r[0], r[1]
            if pid not in regulars:
                continue
            for i, (_c, label) in enumerate(_WEAKNESS_METRICS):
                pct = r[2 + i]
                if pct is None:
                    continue
                if pct <= _WEAKNESS_CEILING and (worst is None or pct < worst[0]):
                    worst = (float(pct), pid, fmt_name(name), label)
        if worst is None:
            return []

        pct, pid, pname, label = worst
        from_bottom = round(pct)
        headline = (
            f"{pname} ranks in the {ordinal(pct)} percentile in {label} — "
            f"bottom {from_bottom}% in MLB"
        )
        dataset = ChartDataset(
            title=pname.upper(),
            subtitle=f"{as_of.year} · {label} percentile",
            as_of=as_of,
            columns=[Column(key="pct", label="Percentile", role="measure", domain=(0.0, 100.0))],
            rows=[[pct]],
            hero={
                "value": ordinal(pct),
                "label": f"percentile · {label}",
                "context": f"Bottom {from_bottom}% in MLB",
            },
            framing=headline,
            source="Baseball Savant",
            headline=headline,
            claim_scope="since_2015",
            card_hint="hero",
            facts={
                "padre_player_id": pid,
                "player_name": pname,
                "percentile": pct,
                "metric": label,
            },
        )
        # The card is a bottom-tail claim, so the tail is the percentile itself,
        # not its complement. ``rows`` is the league percentile table for the
        # year, which both floors the tail and supplies an honest denominator.
        evidence = RarityEvidence(
            kind="extremeness",
            tail_p=max(pct / 100.0, 1.0 / len(rows)),
            population=len(rows),
            search_space=len(_WEAKNESS_METRICS),
        )
        subject = f"SDP|weakness|{pid}|{as_of.year}"
        cid = make_candidate_id(self.name, subject, dataset.model_dump(mode="json"))
        return [
            StatCandidate(
                candidate_id=cid,
                detector=self.name,
                subject=subject,
                as_of=as_of,
                category="struggle",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[
                    {"source_table": "statcast_batter_percentile_ranks", "as_of": str(as_of)}
                ],
                coverage_window=f"{as_of.year}-{as_of.year}",
                claim_scope="since_2015",
                novelty_score=0.0,  # overwritten by emit() from rarity_evidence
                rarity_evidence=evidence,
            )
        ]


register(ColdStreakDetector())
register(WeaknessDetector())
