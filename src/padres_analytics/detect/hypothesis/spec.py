"""HypothesisSpec — the constrained MetricSpec that Claude is allowed to emit."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from padres_analytics.detect.registry import MetricSpec

# Lenses a hypothesis may request — kept in sync with scanner._run_metric.
ALLOWED_LENSES: frozenset[str] = frozenset(
    {"rank", "extremeness", "percentile_elite", "milestone_proximity"}
)


class HypothesisWindow(BaseModel):
    """A rolling recency window (last-N-days) for a proposed metric."""

    days: int = Field(ge=1, le=120)


class HypothesisSpec(BaseModel):
    """One LLM-proposed scannable metric.

    Mirrors a :class:`~padres_analytics.detect.registry.MetricSpec` but adds a
    free-text ``rationale`` (logged, never rendered) and an optional rolling
    ``window``. The structural fields are hashed into ``spec_hash`` so the
    explored-space ledger can suppress re-proposals; ``rationale`` is excluded
    from the hash so re-wording the same idea still dedups.
    """

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=1, max_length=500)

    table: str = Field(min_length=1, max_length=64)
    value_col: str = Field(min_length=1, max_length=64)
    derived_expr: str | None = Field(default=None, max_length=200)
    filter_sql: str = Field(default="", max_length=200)

    metric_type: Literal["rate", "counting", "differential", "ordinal"] = "rate"
    direction: Literal["higher", "lower"] = "higher"
    distribution: Literal["empirical", "normal_ok"] = "empirical"
    value_format: str = Field(default=".3f", max_length=8)
    unit: str = Field(default="", max_length=8)
    coverage: str = Field(default="current MLB season", max_length=64)
    lenses: list[str] = Field(default_factory=lambda: ["rank", "extremeness"])
    milestones: list[float] = Field(default_factory=list, max_length=6)
    window: HypothesisWindow | None = None

    def spec_hash(self) -> str:
        """Deterministic 16-char identity over the structural (non-prose) fields."""
        payload = json.dumps(
            {
                "table": self.table,
                "value_col": self.value_col,
                "derived_expr": self.derived_expr,
                "filter_sql": self.filter_sql,
                "metric_type": self.metric_type,
                "direction": self.direction,
                "window": self.window.days if self.window else None,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_metric_spec(self) -> MetricSpec:
        """Project onto the registry's MetricSpec so the scanner can run it.

        The rolling ``window`` is intentionally *not* projected here — windowing
        is handled by the detector, which gates it on date-column availability.
        """
        return MetricSpec(
            id=self.id,
            label=self.label,
            table=self.table,
            value_col=self.value_col,
            derived_expr=self.derived_expr,
            filter_sql=self.filter_sql,
            metric_type=self.metric_type,
            direction=self.direction,
            distribution=self.distribution,
            value_format=self.value_format,
            unit=self.unit,
            coverage=self.coverage,
            population="adhoc_hypothesis",
            lenses=[lens for lens in self.lenses if lens in ALLOWED_LENSES],
            milestones=self.milestones,
        )
