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
    metric_family,
)
from padres_analytics.detect.discovery import discover_metrics
from padres_analytics.detect.lenses import (
    LensResult,
    bh_is_feasible,
    bh_surviving_indices,
    expected_false_discoveries,
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
    sample_sizes: dict[int, int] | None = None,
) -> list[_Hit]:
    """Apply a metric's declared lenses to a pre-fetched leaderboard.

    Split out of :func:`_run_metric` so alternative fetch paths (e.g. rolling
    windows, pitch-level aggregates) can reuse the exact same lens logic and gates.

    Args:
        metric: Metric specification.
        rows: Leaderboard rows (player_id, player_name, value), best-first.
        src: Resolved source table (for provenance).
        year: Season year stamped on the resulting hits.
        padres: Set of MLBAM IDs to treat as focal subjects.
        min_n: Minimum population size to run lenses.
        sample_sizes: Optional per-player observation counts. Supplied by
            event-grain fetches, where the player's own denominator — not the
            size of the league — is what shrinkage should rest on.

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
                    focal_n=sample_sizes.get(pid) if sample_sizes else None,
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


def _scan_aggregates(
    conn: duckdb.DuckDBPyConnection,
    year: int,
    padres: set[int],
    scan_cfg: ScanConfig,
) -> list[_Hit]:
    """Run pitch-level rate metrics, both overall and per curated split."""
    from padres_analytics.detect.aggregates import BATTER_AGGS, fetch_agg_rows
    from padres_analytics.detect.splits import CONTRAST_PAIRS

    # Overall, plus each side of every contrast pair — a player can be notable
    # on one side of a split without the gap itself being notable.
    split_options: list[object] = [None]
    for pair in CONTRAST_PAIRS.values():
        split_options.extend(pair)

    hits: list[_Hit] = []
    for metric in BATTER_AGGS:
        for split in split_options:
            if not metric.accepts(split):  # type: ignore[arg-type]
                continue
            rows, sizes, src = fetch_agg_rows(conn, metric, year, split)  # type: ignore[arg-type]
            if len(rows) < scan_cfg.min_observation_n:
                continue
            spec = metric.to_metric_spec(split)  # type: ignore[arg-type]
            if split is not None:
                spec = spec.model_copy(update={"coverage": f"{year}, {split.display()}"})  # type: ignore[union-attr]
            else:
                spec = spec.model_copy(update={"coverage": str(year)})
            for hit in lenses_over_rows(
                spec, rows, src, year, padres, scan_cfg.min_observation_n, sample_sizes=sizes
            ):
                hits.append(hit)
    return hits


def _scan_contrasts(
    conn: duckdb.DuckDBPyConnection,
    year: int,
    padres: set[int],
    as_of: date,
) -> list[StatCandidate]:
    """Rank each Padre's split gaps against the league distribution of that gap."""
    from padres_analytics.detect.aggregates import BATTER_AGGS, population_label
    from padres_analytics.detect.contrast import (
        MIN_SIDE_OPPORTUNITIES,
        fetch_contrast_rows,
        split_contrast_lens,
    )
    from padres_analytics.detect.splits import CONTRAST_PAIRS

    out: list[StatCandidate] = []
    for metric in BATTER_AGGS:
        for pair_name, (split_a, split_b) in CONTRAST_PAIRS.items():
            if not (metric.accepts(split_a) and metric.accepts(split_b)):
                continue
            try:
                population = fetch_contrast_rows(conn, metric, split_a, split_b, year)
            except Exception as exc:
                logger.warning("scan: contrast %s/%s failed: %s", metric.id, pair_name, exc)
                continue

            scope = (
                f"{year}, {split_a.display()} against {split_b.display()}, "
                f"min {MIN_SIDE_OPPORTUNITIES} each side"
            )
            pop_label = population_label(conn, len(population), year)
            for row in population:
                if row.player_id not in padres:
                    continue
                lr = split_contrast_lens(
                    focal=row,
                    population=population,
                    metric=metric,
                    split_a=split_a,
                    split_b=split_b,
                    claim_scope=scope,
                    population_label=pop_label,
                )
                if lr is None:
                    continue
                out.append(
                    _build_contrast_candidate(
                        row, lr, metric, split_a, split_b, len(population), year, as_of, pop_label
                    )
                )
    return out


def _build_contrast_candidate(
    row,
    lr: LensResult,
    metric,
    split_a,
    split_b,
    population_size: int,
    year: int,
    as_of: date,
    pop_label: str,
) -> StatCandidate:
    """Build a split-contrast candidate: both sides shown, gap as the story."""
    dataset = ChartDataset(
        title=row.player_name.upper(),
        subtitle=f"{year} · {metric.label} · {split_a.display()} against {split_b.display()}",
        as_of=as_of,
        columns=[
            Column(key="split", label="Split", role="dimension"),
            Column(
                key="value",
                label=metric.label,
                role="measure",
                unit=metric.unit or None,
                format=metric.value_format,
                higher_is_better=(metric.direction == "higher"),
            ),
            Column(key="n", label="Pitches", role="measure", format=".0f"),
        ],
        rows=[
            [split_a.display(), round(row.a_value, 1), row.a_n],
            [split_b.display(), round(row.b_value, 1), row.b_n],
        ],
        framing=lr.framing,
        source="Baseball Savant",
        headline=lr.framing,
        claim_scope=lr.claim_scope,
        population_label=pop_label,
        card_hint="contrast",
        facts={
            "player_id": row.player_id,
            "a_value": round(row.a_value, 1),
            "b_value": round(row.b_value, 1),
            "a_n": row.a_n,
            "b_n": row.b_n,
            "gap": round(abs(row.diff), 1),
            "population_size": population_size,
            "metric_year": year,
        },
    )

    score, components = novelty_score(
        {
            "rarity": lr.rarity,
            "magnitude": min(lr.rarity, 0.95),
            "timeliness": 0.80,
            "rootability": 0.88,
            "legibility": 0.85,
        },
        detector="scan",
    )
    subject = f"SDP|contrast|{metric.id}|{split_a.key()}|{row.player_id}|{year}"
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
                "source_table": metric.table,
                "metric_id": metric.id,
                "lens": "split_contrast",
                "split_a": split_a.key(),
                "split_b": split_b.key(),
                "year": year,
                "as_of": str(as_of),
            }
        ],
        coverage_window=f"{year}-{year}",
        claim_scope=lr.claim_scope,
        novelty_score=score,
        novelty_components=components,
    )


def _scan_career_shifts(
    conn: duckdb.DuckDBPyConnection,
    season: int,
    padres: set[int],
    as_of: date,
) -> list[StatCandidate]:
    """Career-baseline shifts: is this a different player than he has been?"""
    from padres_analytics.detect.changepoint import (
        MIN_PRIOR_SEASONS,
        MIN_SEASON_PA,
        detect_career_shifts,
        rarity_from_shift,
    )
    from padres_analytics.storage.coverage import CAREER_BASELINE, audit, can_support

    try:
        supported, reason = can_support(audit(conn), CAREER_BASELINE)
    except Exception as exc:  # coverage machinery absent (fixtures) — don't block
        logger.debug("scan: coverage check unavailable for career shifts: %s", exc)
        supported, reason = True, ""
    if not supported:
        logger.info("scan: career-baseline shifts blocked by coverage: %s", reason)
        return []

    out: list[StatCandidate] = []
    for shift in detect_career_shifts(conn, season, padres):
        rarity = rarity_from_shift(shift)
        fmt = shift.value_format
        dataset = ChartDataset(
            title=shift.player_name.upper(),
            subtitle=f"{shift.season} vs his own {shift.prior_seasons}-season baseline",
            as_of=as_of,
            columns=[
                Column(key="period", label="Period", role="dimension"),
                Column(
                    key="value",
                    label=shift.metric_label,
                    role="measure",
                    format=fmt,
                    higher_is_better=True,
                ),
            ],
            rows=[
                [f"Career ({shift.prior_seasons} seasons)", round(shift.baseline, 3)],
                [str(shift.season), round(shift.current, 3)],
            ],
            framing=shift.framing(),
            source="MLB Stats API / Baseball Reference",
            headline=shift.framing(),
            claim_scope=(
                f"{shift.season} vs prior {shift.prior_seasons} seasons, min {MIN_SEASON_PA} PA"
            ),
            population_label=(
                f"players with {MIN_PRIOR_SEASONS}+ qualified prior seasons, league drift removed"
            ),
            card_hint="contrast",
            facts={
                "player_id": shift.player_id,
                "baseline": round(shift.baseline, 3),
                "current": round(shift.current, 3),
                "net_delta": round(shift.net_delta, 3),
                "league_delta": round(shift.league_delta, 3),
                "prior_seasons": shift.prior_seasons,
                "z": round(shift.z, 2),
                "metric_year": shift.season,
            },
        )
        score, components = novelty_score(
            {
                "rarity": rarity,
                "magnitude": min(rarity, 0.92),
                "timeliness": 0.85,
                "rootability": 0.90,
                "legibility": 0.88,
            },
            detector="scan",
        )
        subject = f"SDP|career_shift|{shift.metric}|{shift.player_id}|{shift.season}"
        out.append(
            StatCandidate(
                candidate_id=make_candidate_id("scan", subject, dataset.model_dump(mode="json")),
                detector="scan",
                subject=subject,
                as_of=as_of,
                category="season",
                payload_kind="dataset",
                facts_json=dataset.model_dump(mode="json"),
                provenance_json=[
                    {
                        "source_table": "player_season_batting",
                        "metric_id": shift.metric,
                        "lens": "career_baseline",
                        "year": shift.season,
                        "as_of": str(as_of),
                    }
                ],
                coverage_window=f"{shift.season - shift.prior_seasons}-{shift.season}",
                claim_scope=dataset.claim_scope,
                novelty_score=score,
                novelty_components=components,
            )
        )
    return out


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

        # Pitch-level plate-discipline rates, overall and per split. These reach
        # the event grain the season-summary tables can't express.
        event_year = max_year(conn, "statcast_batter_pitches")
        contrast_candidates: list[StatCandidate] = []
        if event_year is not None:
            roster_year = event_year if event_year <= as_of.year else as_of.year
            event_padres = available_padre_ids(conn, roster_year)
            if event_padres:
                agg_hits = _scan_aggregates(conn, event_year, event_padres, scan_cfg)
                logger.info("scan: %d pitch-level aggregate hit(s)", len(agg_hits))
                all_hits.extend(agg_hits)

                contrast_candidates = _scan_contrasts(conn, event_year, event_padres, as_of)
                logger.info("scan: %d split-contrast candidate(s)", len(contrast_candidates))

        # Career baselines: the same player against his own past, not the league.
        season_padres = available_padre_ids(conn, as_of.year)
        if season_padres:
            shift_candidates = _scan_career_shifts(conn, as_of.year, season_padres, as_of)
            logger.info("scan: %d career-shift candidate(s)", len(shift_candidates))
            contrast_candidates = [*contrast_candidates, *shift_candidates]

        if not all_hits and not contrast_candidates:
            return []

        # Contrast candidates are built directly rather than as _Hits, so they
        # would otherwise sit outside the day's multiplicity accounting. The
        # battery is every comparison the engine ran today, not just the ones
        # that happen to flow through one code path.
        battery_size = len(all_hits) + len(contrast_candidates)

        # Gate: rarity floor, then dedup to ONE strongest hit per (player, metric).
        # Collapsing across lenses kills the "same player, same metric, 3 cards" spam.
        floored = [h for h in all_hits if h.lens_result.rarity >= scan_cfg.min_rarity]
        largest_population = max((h.population_size for h in all_hits), default=0)
        floored = self._apply_fdr(floored, scan_cfg, battery_size, largest_population)
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

        candidates: list[StatCandidate] = list(contrast_candidates)

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

        return self._rank_with_priors(conn, candidates, scan_cfg)

    @staticmethod
    def _rank_with_priors(
        conn: duckdb.DuckDBPyConnection,
        candidates: list[StatCandidate],
        scan_cfg: ScanConfig,
    ) -> list[StatCandidate]:
        """Order by learned editorial priors, keeping slots open for the unproven.

        Priors bias rank order only — they never touch facts or relax a gate. The
        exploration floor is the guard against the obvious failure mode: rank by
        what got approved before and the engine converges on a house style,
        stops surfacing anything unfamiliar, and quietly gets more boring. A
        fixed number of slots always go to the best candidates by *raw* novelty,
        so something unproven reaches the Board every day.
        """
        from padres_analytics.learn.apply import apply_priors, latest_stats
        from padres_analytics.learn.features import _star_ids, candidate_features

        by_raw = sorted(candidates, key=lambda c: c.novelty_score, reverse=True)

        stats = latest_stats(conn)
        if not stats:
            return by_raw[: scan_cfg.top_k]

        stars = _star_ids(conn)
        adjusted: list[StatCandidate] = []
        for cand in candidates:
            feats = candidate_features(
                cand.detector, cand.facts_json, cand.provenance_json, cand.novelty_score, stars
            )
            score, components = apply_priors(stats, cand.novelty_score, feats)
            if components:
                adjusted.append(
                    cand.model_copy(
                        update={
                            "novelty_score": score,
                            "novelty_components": {**(cand.novelty_components or {}), **components},
                        }
                    )
                )
            else:
                adjusted.append(cand)

        adjusted.sort(key=lambda c: c.novelty_score, reverse=True)

        reserved = min(scan_cfg.exploration_slots, scan_cfg.top_k)
        if reserved <= 0:
            return adjusted[: scan_cfg.top_k]

        picked: list[StatCandidate] = []
        seen: set[str] = set()
        for cand in by_raw[:reserved]:
            picked.append(cand)
            seen.add(cand.candidate_id)
        for cand in adjusted:
            if len(picked) >= scan_cfg.top_k:
                break
            if cand.candidate_id not in seen:
                picked.append(cand)
                seen.add(cand.candidate_id)
        return picked

    @staticmethod
    def _apply_fdr(
        hits: list[_Hit],
        scan_cfg: ScanConfig,
        battery_size: int | None = None,
        population_size: int = 0,
    ) -> list[_Hit]:
        """Apply Benjamini-Hochberg correction *within metric families*.

        Correcting across the whole day's battery pooled tests that have nothing
        to do with each other — sprint speed, chase rate and defensive range are
        not exchangeable, which is the assumption BH rests on. Worse, pooling
        drove the required threshold below what the method can even resolve: an
        ECDF over n players cannot produce a p-value proxy smaller than 1/n, so
        with a large battery the best hitter in baseball could not pass.

        Correcting within families fixes both problems at once. Each family is a
        set of genuinely related tests, and its smaller m keeps the threshold
        inside the achievable range — without asking fewer questions overall.

        A family whose correction is still unachievable is reported and passed
        through rather than silently emptied.

        Args:
            hits: Hits that already cleared the rarity floor.
            scan_cfg: Scan configuration (mode + alpha).
            battery_size: Total comparisons run today, including paths that don't
                produce _Hits (split contrasts). Logged so the multiplicity the
                day actually carried is visible even when correction is advisory.
            population_size: Largest comparison universe seen today, used to check
                whether BH can be satisfied at all before enforcing it.

        Returns:
            The surviving hits under ``strict``; the input unchanged otherwise.
        """
        if scan_cfg.fdr_mode == "off" or not hits:
            return hits

        m_total = battery_size if battery_size is not None else len(hits)
        expected_noise = expected_false_discoveries(m_total, scan_cfg.min_rarity)

        families: dict[str, list[int]] = {}
        for i, hit in enumerate(hits):
            families.setdefault(metric_family(hit.metric.id), []).append(i)

        survivors: set[int] = set()
        infeasible: list[str] = []
        for family, idxs in sorted(families.items()):
            m_family = len(idxs)
            if population_size > 0 and not bh_is_feasible(
                population_size, m_family, scan_cfg.fdr_alpha
            ):
                # Cannot be corrected at this resolution — keep the family and say
                # so, rather than dropping everything in it and calling it a gate.
                infeasible.append(f"{family}(m={m_family})")
                survivors.update(idxs)
                continue
            local = bh_surviving_indices(
                [hits[i].lens_result.rarity for i in idxs], scan_cfg.fdr_alpha
            )
            survivors.update(idxs[j] for j in local)

        logger.info(
            "scan: BH fdr_mode=%s alpha=%.3f tested=%d battery=%d families=%d "
            "survivors=%d dropped=%d | at floor=%.2f expect ~%.1f of %d by chance",
            scan_cfg.fdr_mode,
            scan_cfg.fdr_alpha,
            len(hits),
            m_total,
            len(families),
            len(survivors),
            len(hits) - len(survivors),
            scan_cfg.min_rarity,
            expected_noise,
            len(hits),
        )
        if infeasible:
            logger.warning(
                "scan: %d famil(y/ies) too large to correct at population=%d and passed "
                "through uncorrected: %s",
                len(infeasible),
                population_size,
                ", ".join(infeasible),
            )

        if scan_cfg.fdr_mode == "advisory":
            return hits
        return [h for i, h in enumerate(hits) if i in survivors]


register(GenericScanner())
