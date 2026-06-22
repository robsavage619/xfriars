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
from padres_analytics.detect.conjunction import evaluate_franchise_scope, find_conjunctions
from padres_analytics.detect.discovery import discover_metrics
from padres_analytics.detect.lenses import (
    LensResult,
    extremeness_lens,
    milestone_proximity_lens,
    percentile_elite_lens,
    rank_lens,
)
from padres_analytics.detect.registry import MetricSpec, ScanConfig, load_registry
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.detect.sql import (
    fmt_name,
    max_year,
    padre_ids,
    padre_ids_roster,
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
# TODO: move to private config (the "closed brain") alongside the metric registry.
_STAR_IDS: frozenset[int] = frozenset(
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


def _passes_hero_gate(hit: _Hit) -> bool:
    """True if a single-player hit deserves a standalone (hero) card.

    Per editorial policy: a standalone card requires a genuinely league-elite
    mark (extremeness in roughly the top 5%) OR a marquee player. Everything
    else rolls into a leaderboard or is suppressed — no hero cards for
    dead-average numbers on bench players.
    """
    if hit.player_id in _STAR_IDS:
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
            # Prefer the real 40-man (main.team_rosters); fall back to bwar team assignments.
            padres = padre_ids_roster(conn, roster_year) or padre_ids(conn, roster_year)
            if not padres:
                logger.debug("scan: no Padre IDs for year=%d", roster_year)
                continue

            hits = _run_metric(conn, metric, metric_year, padres, scan_cfg.min_observation_n)
            logger.debug("scan: metric=%s year=%d hits=%d", metric.id, metric_year, len(hits))
            all_hits.extend(hits)

        if not all_hits:
            return []

        # Gate: rarity floor, then dedup to ONE strongest hit per (player, metric).
        # Collapsing across lenses kills the "same player, same metric, 3 cards" spam.
        floored = [h for h in all_hits if h.lens_result.rarity >= scan_cfg.min_rarity]
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

        # Log conjunction stories (multi-metric players) for future narrative use
        conjunctions = find_conjunctions(surviving_hits)
        if conjunctions:
            logger.info(
                "scan: %d conjunction group(s) found: %s",
                len(conjunctions),
                [g.combined_framing[:60] for g in conjunctions[:3]],
            )

        # Group by metric: >= _MIN_LEADERBOARD Padres collapse into ONE ranked card;
        # otherwise emit standalone cards gated by elite-or-star.
        by_metric: dict[str, list[_Hit]] = {}
        for hit in surviving_hits:
            by_metric.setdefault(hit.metric.id, []).append(hit)

        candidates: list[StatCandidate] = []
        for metric_id, hits in by_metric.items():
            try:
                if len(hits) >= _MIN_LEADERBOARD:
                    candidates.append(_build_leaderboard_candidate(hits[0].metric, hits, as_of))
                    continue
                for hit in hits:
                    if _passes_hero_gate(hit):
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


register(GenericScanner())
