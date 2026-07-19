"""Generic scanner: runs registry metrics through statistical lenses.

Replaces hand-coded per-metric detectors with a declarative TOML-driven engine.
Each metric runs its declared lenses; surviving results (after BH correction) are
ranked by surprise x relevance x reliability and emitted as ChartDataset candidates.

Legacy detectors continue to run in parallel during P2. This scanner registers as
'scan' and is invoked via ``pad scan`` (not ``pad detect run``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from padres_analytics.detect.conjunction import (
    CONJUNCTION_PERCENTILE_CUT,
    ConjunctionGroup,
    count_players_meeting_all,
    evaluate_franchise_scope,
    find_conjunctions,
)
from padres_analytics.detect.discovery import discover_metrics
from padres_analytics.detect.lenses import (
    LensResult,
    bh_surviving_indices,
    extremeness_lens,
    milestone_proximity_lens,
    percentile_elite_lens,
    rank_lens,
)
from padres_analytics.detect.registry import MetricSpec, ScanConfig, load_registry
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.detect.sql import (
    available_padre_ids,
    fmt_name,
    max_year,
    resolve_table,
)

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


@dataclass
class _Hit:
    """Intermediate result: one lens applied to one Padre player for one metric."""

    lens_result: LensResult
    metric: MetricSpec
    player_id: int
    player_name: str
    focal_value: float
    rank: int
    population_size: int
    leaderboard: list[tuple[int, str, float]]
    resolved_table: str
    metric_year: int


def _fetch_rows(
    conn: duckdb.DuckDBPyConnection,
    metric: MetricSpec,
    year: int,
) -> tuple[list[tuple[int, str, float]], str]:
    """Fetch all qualified rows for a metric, ordered best-first.

    Args:
        conn: DB connection.
        metric: Metric specification.
        year: Season year.

    Returns:
        Tuple of (rows, resolved_table_name). Rows are (player_id, player_name, value).
    """
    src = resolve_table(conn, metric.table)
    value_expr = metric.derived_expr if metric.derived_expr else metric.value_col
    where = f"AND ({metric.filter_sql})" if metric.filter_sql else ""
    order_dir = "DESC" if metric.direction == "higher" else "ASC"

    try:
        rows = conn.execute(
            f"""
            SELECT {metric.id_col}, {metric.name_col}, {value_expr}
            FROM {src}
            WHERE {metric.year_col} = ? {where}
              AND {value_expr} IS NOT NULL
            ORDER BY {value_expr} {order_dir}
            """,
            [year],
        ).fetchall()
    except Exception as exc:
        logger.warning("scan: metric=%s fetch failed: %s", metric.id, exc)
        return [], src

    return [(int(r[0]), fmt_name(str(r[1])), float(r[2])) for r in rows], src


def _run_metric(
    conn: duckdb.DuckDBPyConnection,
    metric: MetricSpec,
    year: int,
    padres: set[int],
    min_n: int,
) -> list[_Hit]:
    """Run all declared lenses for one metric against qualifying Padre players.

    Args:
        conn: DB connection.
        metric: Metric specification.
        year: Season year.
        padres: Set of MLBAM IDs on the Padres roster.
        min_n: Minimum population size to run lenses.

    Returns:
        List of _Hit objects from all fired lenses.
    """
    rows, src = _fetch_rows(conn, metric, year)
    return lenses_over_rows(metric, rows, src, year, padres, min_n)


def lenses_over_rows(
    metric: MetricSpec,
    rows: list[tuple[int, str, float]],
    src: str,
    year: int,
    padres: set[int],
    min_n: int,
) -> list[_Hit]:
    """Apply a metric's declared lenses to a pre-fetched leaderboard.

    Split out of :func:`_run_metric` so alternative fetch paths (e.g. rolling
    windows) can reuse the exact same lens logic and gates.

    Args:
        metric: Metric specification.
        rows: Leaderboard rows (player_id, player_name, value), best-first.
        src: Resolved source table (for provenance).
        year: Season year stamped on the resulting hits.
        padres: Set of MLBAM IDs to treat as focal subjects.
        min_n: Minimum population size to run lenses.

    Returns:
        List of _Hit objects from all fired lenses.
    """
    if not rows:
        return []

    population_values = [v for _, _, v in rows]
    pop_size = len(rows)

    if pop_size < min_n:
        logger.debug("scan: metric=%s skipped (n=%d < %d)", metric.id, pop_size, min_n)
        return []

    hits: list[_Hit] = []

    for rank, (pid, pname, val) in enumerate(rows, start=1):
        if pid not in padres:
            continue

        for lens_name in metric.lenses:
            lr: LensResult | None = None

            if lens_name == "extremeness" and metric.metric_type in ("rate", "differential"):
                lr = extremeness_lens(
                    focal_value=val,
                    population_values=population_values,
                    metric_label=metric.label,
                    player_name=pname,
                    higher_is_better=(metric.direction == "higher"),
                    value_format=metric.value_format,
                    unit=metric.unit,
                    claim_scope=metric.coverage,
                    stabilization_n=metric.stabilization_n,
                )

            elif lens_name == "percentile_elite":
                lr = percentile_elite_lens(
                    percentile=val,
                    metric_label=metric.label,
                    player_name=pname,
                    claim_scope=metric.coverage,
                )

            elif lens_name == "rank":
                lr = rank_lens(
                    focal_rank=rank,
                    population_size=pop_size,
                    player_name=pname,
                    focal_value=val,
                    metric_label=metric.label,
                    value_format=metric.value_format,
                    unit=metric.unit,
                    claim_scope=metric.coverage,
                )

            elif lens_name == "milestone_proximity":
                for threshold in metric.milestones:
                    mlr = milestone_proximity_lens(
                        focal_value=val,
                        milestone=threshold,
                        metric_label=metric.label,
                        player_name=pname,
                        value_format=metric.value_format,
                        unit=metric.unit,
                        claim_scope=metric.coverage,
                    )
                    if mlr is not None:
                        hits.append(
                            _Hit(
                                lens_result=mlr,
                                metric=metric,
                                player_id=pid,
                                player_name=pname,
                                focal_value=val,
                                rank=rank,
                                population_size=pop_size,
                                leaderboard=rows,
                                resolved_table=src,
                                metric_year=year,
                            )
                        )
                continue

            else:
                logger.debug("scan: unknown lens '%s' for metric=%s", lens_name, metric.id)

            if lr is not None:
                hits.append(
                    _Hit(
                        lens_result=lr,
                        metric=metric,
                        player_id=pid,
                        player_name=pname,
                        focal_value=val,
                        rank=rank,
                        population_size=pop_size,
                        leaderboard=rows,
                        resolved_table=src,
                        metric_year=year,
                    )
                )

    return hits


def _build_candidate(hit: _Hit, as_of: date) -> StatCandidate:
    """Convert a _Hit into a StatCandidate with a ChartDataset payload.

    Rank lens hits become leaderboard cards (dimension + measure -> bar/table via selector).
    Extremeness lens hits become hero cards (hero dict + single row -> hero via selector).

    Args:
        hit: Firing _Hit from a lens.
        as_of: Reference date.

    Returns:
        StatCandidate ready for emit().
    """
    metric = hit.metric
    lr = hit.lens_result
    year = hit.metric_year

    measure_col = Column(
        key="value",
        label=metric.label,
        role="measure",
        unit=metric.unit if metric.unit else None,
        format=metric.value_format,
        higher_is_better=(metric.direction == "higher"),
    )

    if lr.lens == "rank":
        top_n = min(10, len(hit.leaderboard))
        display = hit.leaderboard[:top_n]
        padre_idx = next(
            (i for i, (pid, _, _) in enumerate(display) if pid == hit.player_id),
            hit.rank - 1,
        )
        dataset = ChartDataset(
            title=f"MLB {metric.label.upper()} LEADERS",
            subtitle=f"{year} Season · Statcast",
            as_of=as_of,
            columns=[
                Column(key="player", label="Player", role="dimension"),
                measure_col,
            ],
            rows=[[pname, val] for _, pname, val in display],
            highlight=[Mark(row_index=padre_idx, label=hit.player_name, note=f"#{hit.rank}")],
            framing=lr.framing,
            source="Baseball Savant",
            headline=lr.framing,
            claim_scope=lr.claim_scope,
            facts={
                "padre_value": hit.focal_value,
                "padre_rank": hit.rank,
                "padre_player_id": hit.player_id,
                "population_size": hit.population_size,
                "metric_year": year,
            },
        )

    else:
        ecdf_pct = round(lr.rarity * 100)
        direction = "top" if metric.direction == "higher" else "bottom"
        val_str = f"{hit.focal_value:{metric.value_format}}"
        if metric.unit:
            val_str = f"{val_str} {metric.unit}"
        dataset = ChartDataset(
            title=hit.player_name.upper(),
            subtitle=f"{year} · {metric.label}",
            as_of=as_of,
            columns=[measure_col],
            rows=[[hit.focal_value]],
            hero={
                "value": val_str,
                "label": metric.label,
                "context": f"{direction.title()} {100 - ecdf_pct}% in MLB",
            },
            framing=lr.framing,
            source="Baseball Savant",
            headline=lr.framing,
            claim_scope=lr.claim_scope,
            facts={
                "padre_value": hit.focal_value,
                "padre_percentile": ecdf_pct,
                "padre_player_id": hit.player_id,
                "population_size": hit.population_size,
                "metric_year": year,
            },
        )

    score, components = novelty_score(
        {
            "rarity": lr.rarity,
            "magnitude": min(lr.rarity, 0.95),
            "timeliness": 0.80,
            "rootability": 0.85,
            "legibility": 0.90,
        },
        detector="scan",
    )

    subject = f"SDP|{metric.id}|{hit.player_id}|{year}|{lr.lens}"
    cid = make_candidate_id("scan", subject, dataset.model_dump(mode="json"))

    return StatCandidate(
        candidate_id=cid,
        detector="scan",
        subject=subject,
        as_of=as_of,
        category="season",
        payload_kind="dataset",
        facts_json=dataset.model_dump(mode="json"),
        provenance_json=[
            {
                "source_table": hit.resolved_table,
                "metric_id": metric.id,
                "lens": lr.lens,
                "year": year,
                "as_of": str(as_of),
            }
        ],
        coverage_window=f"{year}-{year}",
        claim_scope=lr.claim_scope,
        novelty_score=score,
        novelty_components=components,
    )


# Collapse a metric into one ranked leaderboard card once this many Padres fire it.
_MIN_LEADERBOARD = 3
# Extremeness rarity that counts as genuinely league-elite (≈ top 5%).
_HERO_ELITE_RARITY = 0.95
# Marquee Padres — a non-elite stat still earns a standalone card for these names.
# Editorial judgment, so private/metrics.toml [scan] star_ids overrides this; the
# built-in list is only the fallback when no private registry is present.
_STAR_IDS_FALLBACK: frozenset[int] = frozenset(
    {
        665487,  # Fernando Tatis Jr.
        592518,  # Manny Machado
        701538,  # Jackson Merrill
        593428,  # Xander Bogaerts
        630105,  # Jake Cronenworth
        650333,  # Luis Arraez
    }
)

# Per-metric editorial presentation. Generic fallback used when a metric is absent.
_METRIC_PRESENTATION: dict[str, dict[str, str]] = {
    "sprint_speed": {
        "title": "FASTEST PADRES",
        "lead": "is the fastest Padre",
    },
    "xwoba_gap": {
        "title": "DUE FOR A BREAKOUT",
        "subtitle": "Biggest gap between expected and actual production · regression coming",
        "lead": "has the biggest gap between expected and actual production on the Padres",
    },
}


def _passes_hero_gate(hit: _Hit, star_ids: frozenset[int]) -> bool:
    """True if a single-player hit deserves a standalone (hero) card.

    Per editorial policy: a standalone card requires a genuinely league-elite
    mark (extremeness in roughly the top 5%) OR a marquee player. Everything
    else rolls into a leaderboard or is suppressed — no hero cards for
    dead-average numbers on bench players.
    """
    if hit.player_id in star_ids:
        return True
    return hit.lens_result.lens == "extremeness" and hit.lens_result.rarity >= _HERO_ELITE_RARITY


def _build_leaderboard_candidate(
    metric: MetricSpec,
    hits: list[_Hit],
    as_of: date,
) -> StatCandidate:
    """Collapse multiple same-metric Padre hits into one ranked bar leaderboard.

    Args:
        metric: The shared metric.
        hits: All surviving Padre hits for this metric (>= _MIN_LEADERBOARD).
        as_of: Reference date.

    Returns:
        A single StatCandidate carrying a ranked bar ChartDataset.
    """
    higher = metric.direction == "higher"
    ranked = sorted(hits, key=lambda h: h.focal_value, reverse=higher)
    year = ranked[0].metric_year
    pres = _METRIC_PRESENTATION.get(metric.id, {})

    title = pres.get("title", f"PADRES {metric.label.upper()} LEADERS")
    subtitle = pres.get("subtitle", f"{year} · {metric.label} · ranked")
    leader = ranked[0]
    val_str = f"{leader.focal_value:{metric.value_format}}"
    if metric.unit:
        val_str = f"{val_str} {metric.unit}"
    lead_phrase = pres.get("lead", f"leads the Padres in {metric.label}")
    headline = f"{leader.player_name} {lead_phrase} ({val_str}, {year})"

    measure = Column(
        key="value",
        label=metric.label,
        role="measure",
        unit=metric.unit or None,
        format=metric.value_format,
        higher_is_better=higher,
    )
    dataset = ChartDataset(
        title=title,
        subtitle=subtitle,
        as_of=as_of,
        columns=[Column(key="player", label="Player", role="dimension"), measure],
        rows=[[h.player_name, round(h.focal_value, 3)] for h in ranked],
        framing=headline,
        source="Baseball Savant",
        headline=headline,
        claim_scope=metric.coverage,
        population_label=f"Padres roster, {year}",
        card_hint="bar",
        facts={
            "metric_id": metric.id,
            "metric_year": year,
            "leader_name": leader.player_name,
            "leader_value": round(leader.focal_value, 3),
            "n_padres": len(ranked),
        },
    )

    score, components = novelty_score(
        {
            "rarity": leader.lens_result.rarity,
            "magnitude": min(leader.lens_result.rarity, 0.95),
            "timeliness": 0.80,
            "rootability": 0.85,
            "legibility": 0.92,
        },
        detector="scan",
    )
    subject = f"SDP|{metric.id}|leaderboard|{year}"
    cid = make_candidate_id("scan", subject, dataset.model_dump(mode="json"))
    return StatCandidate(
        candidate_id=cid,
        detector="scan",
        subject=subject,
        as_of=as_of,
        category="season",
        payload_kind="dataset",
        facts_json=dataset.model_dump(mode="json"),
        provenance_json=[
            {"source_table": ranked[0].resolved_table, "metric_id": metric.id, "as_of": str(as_of)}
        ],
        coverage_window=f"{year}-{year}",
        claim_scope=metric.coverage,
        novelty_score=score,
        novelty_components=components,
    )


def _build_conjunction_candidate(
    group: ConjunctionGroup,
    peer_count: tuple[int, int] | None,
    as_of: date,
) -> StatCandidate:
    """Build a compound candidate: one player, several elite marks at once.

    The claim scope is the *most conservative* member scope — a conjunction can
    never be broader than its narrowest member, or a Statcast-era mark would
    smuggle a franchise-history claim in alongside it.

    Args:
        group: A conjunction group (2+ distinct metrics for one player).
        peer_count: ``(qualifying, population)`` at the fixed percentile cut, or
            None when it couldn't be computed (no uniqueness claim is then made).
        as_of: Reference date.

    Returns:
        StatCandidate carrying a conjunction ChartDataset.
    """
    members = group.hits
    year = members[0].metric_year

    rows: list[list[str | int | float | None]] = []
    facts: dict[str, str | int | float] = {
        "player_id": group.player_id,
        "n_metrics": len(members),
        "metric_year": year,
        "combined_rarity": round(group.combined_rarity, 4),
    }
    for hit in members:
        metric = hit.metric
        val = round(hit.focal_value, 3)
        # For a Savant percentile column the value IS a percentile; printing it
        # beside our own ECDF percentile puts two different percentiles on one
        # row and reads as a contradiction. Show the rank only.
        is_percentile_metric = hit.lens_result.lens == "percentile_elite"
        rows.append(
            [
                metric.label,
                None if is_percentile_metric else val,
                round(hit.lens_result.rarity * 100),
            ]
        )
        if not is_percentile_metric:
            facts[f"{metric.id}_value"] = val
        facts[f"{metric.id}_percentile"] = round(hit.lens_result.rarity * 100)

    # A conjunction over one season's leaderboards is a claim about that season,
    # whatever era the underlying source spans. Inheriting the members' source
    # coverage ("since_2015") would assert a scope the comparison never made.
    claim_scope = str(year)

    # "Elite" is only true for metrics where high = good, so the framing states
    # the *rank* and lets each metric's own label carry its meaning. Labels keep
    # their casing (xwOBA, not xwoba). The cut is fixed in advance, never fitted
    # to the subject.
    feats = " and ".join(h.metric.label for h in members)
    top_pct = round((1.0 - CONJUNCTION_PERCENTILE_CUT) * 100)
    quantifier = "both" if len(members) == 2 else "all of"
    facts["top_percent"] = top_pct

    if peer_count is not None:
        qualifying, population = peer_count
        facts["players_meeting_all"] = qualifying
        facts["population_size"] = population
        subject_phrase = (
            f"{group.player_name} is the only player out of {population} qualified"
            if qualifying == 1
            else f"{group.player_name} is one of {qualifying} players out of {population} qualified"
        )
        headline = f"{subject_phrase} in the top {top_pct}% in {quantifier} {feats} ({year})"
    else:
        headline = (
            f"{group.player_name} ranks in MLB's top {top_pct}% in {quantifier} {feats} ({year})"
        )

    dataset = ChartDataset(
        title=group.player_name.upper(),
        subtitle=f"{year} · {len(members)} top-{top_pct}% marks",
        as_of=as_of,
        columns=[
            Column(key="metric", label="Metric", role="dimension"),
            Column(key="value", label="Value", role="measure", format=".3f"),
            Column(
                key="percentile",
                label="MLB Percentile",
                role="measure",
                format=".0f",
                higher_is_better=True,
            ),
        ],
        rows=rows,
        framing=headline,
        source="Baseball Savant",
        headline=headline,
        claim_scope=claim_scope,
        population_label=f"Qualified MLB players, {year}",
        card_hint="conjunction",
        facts=facts,
    )

    score, components = novelty_score(
        {
            "rarity": group.combined_rarity,
            # A compound story is the point of the engine — magnitude tracks the
            # combined mark rather than being capped like a single-metric hit.
            "magnitude": min(group.combined_rarity + 0.05, 0.98),
            "timeliness": 0.80,
            "rootability": 0.90,
            "legibility": 0.85,
        },
        detector="scan",
    )

    subject = f"SDP|conjunction|{group.player_id}|{year}"
    cid = make_candidate_id("scan", subject, dataset.model_dump(mode="json"))
    return StatCandidate(
        candidate_id=cid,
        detector="scan",
        subject=subject,
        as_of=as_of,
        category="season",
        payload_kind="dataset",
        facts_json=dataset.model_dump(mode="json"),
        provenance_json=[
            {
                "source_table": h.resolved_table,
                "metric_id": h.metric.id,
                "lens": h.lens_result.lens,
                "year": year,
                "as_of": str(as_of),
            }
            for h in members
        ],
        coverage_window=f"{year}-{year}",
        claim_scope=claim_scope,
        novelty_score=score,
        novelty_components=components,
    )


class GenericScanner:
    """Runs the TOML metric registry through statistical lenses.

    Registered as detector 'scan'. Invoke via ``pad scan``, not ``pad detect run scan``,
    to keep it separate from the legacy detector pipeline during P2.
    """

    name = "scan"

    def run(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: date,
    ) -> list[StatCandidate]:
        """Run all registry metrics, apply BH correction, return top-K candidates.

        Args:
            conn: Read-only padres.db connection with hist attached.
            as_of: Reference date.

        Returns:
            Up to ScanConfig.top_k StatCandidate objects, sorted by novelty score.
        """
        try:
            reg = load_registry()
        except FileNotFoundError:
            reg = None

        # Source of metrics is the live schema (discovery), not a hand-typed list.
        # A *private* registry may add extra metrics; the public example would only
        # duplicate discovered ones, so it is ignored as a metric source.
        metrics = discover_metrics(conn)
        scan_cfg = reg.scan if reg is not None else ScanConfig()
        logger.info("scan: %d metrics discovered from schema", len(metrics))

        all_hits: list[_Hit] = []

        for metric in metrics:
            metric_year = max_year(conn, metric.table)
            if metric_year is None:
                logger.debug("scan: metric=%s table=%s not found", metric.id, metric.table)
                continue

            roster_year = metric_year if metric_year <= as_of.year else as_of.year
            # Availability-filtered: the 40-man includes IL and optioned players,
            # and an out-for-season bat must never headline a card.
            padres = available_padre_ids(conn, roster_year)
            if not padres:
                logger.debug("scan: no available Padre IDs for year=%d", roster_year)
                continue

            hits = _run_metric(conn, metric, metric_year, padres, scan_cfg.min_observation_n)
            logger.debug("scan: metric=%s year=%d hits=%d", metric.id, metric_year, len(hits))
            all_hits.extend(hits)

        if not all_hits:
            return []

        # Gate: rarity floor, then dedup to ONE strongest hit per (player, metric).
        # Collapsing across lenses kills the "same player, same metric, 3 cards" spam.
        floored = [h for h in all_hits if h.lens_result.rarity >= scan_cfg.min_rarity]
        floored = self._apply_fdr(floored, scan_cfg)
        best: dict[tuple[int, str], _Hit] = {}
        for h in floored:
            key = (h.player_id, h.metric.id)
            if key not in best or h.lens_result.rarity > best[key].lens_result.rarity:
                best[key] = h
        surviving_hits = list(best.values())
        logger.info(
            "scan: %d total hits, %d above floor=%.2f, %d after per-(player,metric) dedup",
            len(all_hits),
            len(floored),
            scan_cfg.min_rarity,
            len(surviving_hits),
        )

        # Strengthen framing via franchise scope evaluator for surviving hits
        for hit in surviving_hits:
            try:
                scope = evaluate_franchise_scope(
                    conn=conn,
                    metric=hit.metric,
                    player_id=hit.player_id,
                    player_name=hit.player_name,
                    focal_value=hit.focal_value,
                    year=hit.metric_year,
                    base_framing=hit.lens_result.framing,
                )
                if scope.tier in ("franchise_record", "first_since"):
                    hit.lens_result = LensResult(
                        rarity=min(hit.lens_result.rarity + 0.05, 1.0),
                        framing=scope.framing,
                        claim_scope=hit.lens_result.claim_scope,
                        lens=hit.lens_result.lens,
                    )
                    logger.debug(
                        "scan: scope strengthened to %s for %s/%s",
                        scope.tier,
                        hit.player_name,
                        hit.metric.id,
                    )
            except Exception as exc:
                logger.debug("scan: scope eval failed: %s", exc)

        candidates: list[StatCandidate] = []

        # Conjunctions first: a player elite in several things at once is a better
        # story than any of those things alone, and the members are then spent —
        # they must not also surface as standalone hero cards.
        conjunctions = [
            g for g in find_conjunctions(surviving_hits) if g.combined_rarity >= scan_cfg.min_rarity
        ]
        spent: set[tuple[int, str]] = set()
        for group in conjunctions:
            try:
                peers = count_players_meeting_all(conn, group.hits)
                candidates.append(_build_conjunction_candidate(group, peers, as_of))
                spent.update((group.player_id, h.metric.id) for h in group.hits)
                logger.info(
                    "scan: conjunction %s across %s (peers=%s)",
                    group.player_name,
                    group.metric_ids,
                    peers,
                )
            except Exception as exc:
                logger.warning("scan: conjunction build failed for %s: %s", group.player_name, exc)

        remaining = [h for h in surviving_hits if (h.player_id, h.metric.id) not in spent]

        # Group by metric: >= _MIN_LEADERBOARD Padres collapse into ONE ranked card;
        # otherwise emit standalone cards gated by elite-or-star.
        by_metric: dict[str, list[_Hit]] = {}
        for hit in remaining:
            by_metric.setdefault(hit.metric.id, []).append(hit)

        star_ids = frozenset(scan_cfg.star_ids) if scan_cfg.star_ids else _STAR_IDS_FALLBACK

        for metric_id, hits in by_metric.items():
            try:
                if len(hits) >= _MIN_LEADERBOARD:
                    candidates.append(_build_leaderboard_candidate(hits[0].metric, hits, as_of))
                    continue
                for hit in hits:
                    if _passes_hero_gate(hit, star_ids):
                        candidates.append(_build_candidate(hit, as_of))
                    else:
                        logger.debug(
                            "scan: suppressed non-elite/non-star %s/%s (rarity=%.2f)",
                            hit.player_name,
                            metric_id,
                            hit.lens_result.rarity,
                        )
            except Exception as exc:
                logger.warning("scan: candidate build failed metric=%s: %s", metric_id, exc)

        candidates.sort(key=lambda c: c.novelty_score, reverse=True)
        return candidates[: scan_cfg.top_k]

    @staticmethod
    def _apply_fdr(hits: list[_Hit], scan_cfg: ScanConfig) -> list[_Hit]:
        """Apply Benjamini-Hochberg correction across the day's test battery.

        Args:
            hits: Hits that already cleared the rarity floor.
            scan_cfg: Scan configuration (mode + alpha).

        Returns:
            The surviving hits under ``strict``; the input unchanged otherwise.
        """
        if scan_cfg.fdr_mode == "off" or not hits:
            return hits

        surviving = bh_surviving_indices([h.lens_result.rarity for h in hits], scan_cfg.fdr_alpha)
        dropped = len(hits) - len(surviving)
        logger.info(
            "scan: BH fdr_mode=%s alpha=%.3f battery=%d survivors=%d dropped=%d",
            scan_cfg.fdr_mode,
            scan_cfg.fdr_alpha,
            len(hits),
            len(surviving),
            dropped,
        )
        if scan_cfg.fdr_mode == "advisory":
            return hits
        return [h for i, h in enumerate(hits) if i in surviving]


register(GenericScanner())
