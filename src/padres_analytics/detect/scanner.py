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
from padres_analytics.detect.lenses import (
    LensResult,
    bh_surviving_indices,
    extremeness_lens,
    rank_lens,
)
from padres_analytics.detect.registry import MetricSpec, load_registry
from padres_analytics.detect.scoring import novelty_score
from padres_analytics.detect.sql import fmt_name, max_year, padre_ids, resolve_table

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
        except FileNotFoundError as exc:
            logger.error("scan: registry not found: %s", exc)
            return []

        all_hits: list[_Hit] = []

        for metric in reg.metrics:
            metric_year = max_year(conn, metric.table)
            if metric_year is None:
                logger.debug("scan: metric=%s table=%s not found", metric.id, metric.table)
                continue

            bwar_year = metric_year if metric_year <= as_of.year else as_of.year
            padres = padre_ids(conn, bwar_year)
            if not padres:
                logger.debug("scan: no Padre IDs for year=%d", bwar_year)
                continue

            hits = _run_metric(conn, metric, metric_year, padres, reg.scan.min_observation_n)
            logger.debug("scan: metric=%s year=%d hits=%d", metric.id, metric_year, len(hits))
            all_hits.extend(hits)

        if not all_hits:
            return []

        rarities = [h.lens_result.rarity for h in all_hits]
        surviving = bh_surviving_indices(rarities, reg.scan.fdr_alpha)
        logger.info(
            "scan: %d total hits, %d survive BH (alpha=%.2f)",
            len(all_hits),
            len(surviving),
            reg.scan.fdr_alpha,
        )

        candidates: list[StatCandidate] = []
        for idx, hit in enumerate(all_hits):
            if idx not in surviving:
                continue
            try:
                cand = _build_candidate(hit, as_of)
                candidates.append(cand)
            except Exception as exc:
                logger.warning(
                    "scan: build_candidate failed metric=%s player=%s: %s",
                    hit.metric.id,
                    hit.player_name,
                    exc,
                )

        candidates.sort(key=lambda c: c.novelty_score, reverse=True)
        return candidates[: reg.scan.top_k]


register(GenericScanner())
