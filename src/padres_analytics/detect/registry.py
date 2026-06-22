"""Metric registry: load TOML -> MetricSpec / PopulationSpec / ScanConfig."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_PRIVATE_METRICS = Path(__file__).resolve().parents[3] / "private" / "metrics.toml"
_EXAMPLE_METRICS = Path(__file__).resolve().parents[3] / "examples" / "metrics.example.toml"


class PopulationSpec(BaseModel):
    """How to fetch the comparison universe for a metric."""

    table: str
    id_col: str = "player_id"
    name_col: str = "player_name"
    value_col: str
    year_col: str = "year"
    filter_sql: str = ""
    scope: Literal["mlb", "nl", "al", "franchise"] = "mlb"


class MetricSpec(BaseModel):
    """One scannable metric from the registry."""

    id: str
    label: str
    table: str
    grain: Literal["player_season", "player_game", "team_season"] = "player_season"
    value_col: str
    derived_expr: str | None = None
    id_col: str = "player_id"
    name_col: str = "player_name"
    year_col: str = "year"
    filter_sql: str = ""
    metric_type: Literal["rate", "counting", "differential", "ordinal"] = "rate"
    direction: Literal["higher", "lower"] = "higher"
    value_format: str = ".1f"
    unit: str = ""
    park_adjusted: bool = False
    era_indexed: bool = False
    stabilization_n: int = 200
    distribution: Literal["empirical", "normal_ok"] = "empirical"
    reference_is_survivor_set: bool = False
    population: str
    coverage: str = "mlb_all"
    lenses: list[str] = Field(default_factory=lambda: ["rank"])
    milestones: list[float] = Field(default_factory=list)


class ScanConfig(BaseModel):
    """Top-level scan behaviour knobs."""

    top_k: int = 12
    subject_filter: str = "padres"
    fdr_alpha: float = 0.05
    min_observation_n: int = 30
    min_rarity: float = 0.85
    """Rarity floor for surfacing a hit. The daily battery is ranked effect sizes,
    not independent significance tests, so a floor + the Studio human kill-switch
    guard against noise. BH FDR (fdr_alpha) is retained for opt-in strict mode."""


class Registry(BaseModel):
    """Parsed metric registry."""

    metrics: list[MetricSpec]
    populations: dict[str, PopulationSpec]
    scan: ScanConfig = Field(default_factory=ScanConfig)


def load_registry() -> Registry:
    """Load the metric registry from private/ with fallback to examples/.

    Returns:
        Validated Registry.

    Raises:
        FileNotFoundError: If neither private nor example TOML exists.
    """
    import tomllib

    for path in (_PRIVATE_METRICS, _EXAMPLE_METRICS):
        if path.exists():
            if path == _EXAMPLE_METRICS:
                logger.warning(
                    "private/metrics.toml not found; using example registry. "
                    "Copy examples/metrics.example.toml to private/metrics.toml."
                )
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
            return _parse(raw)

    raise FileNotFoundError(
        f"No metric registry found. Expected {_PRIVATE_METRICS} or {_EXAMPLE_METRICS}."
    )


def _parse(raw: dict) -> Registry:
    metrics = [MetricSpec.model_validate(m) for m in raw.get("metric", [])]
    populations = {
        k: PopulationSpec.model_validate(v) for k, v in raw.get("population", {}).items()
    }
    scan_raw = raw.get("scan", {})
    scan = ScanConfig.model_validate(scan_raw) if scan_raw else ScanConfig()
    return Registry(metrics=metrics, populations=populations, scan=scan)
