"""Unit tests for detect/registry.py — TOML metric registry loading."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from padres_analytics.detect.registry import Registry, _parse, load_registry


def _minimal_toml() -> dict:
    import tomllib

    raw = textwrap.dedent("""
        [scan]
        top_k = 5
        fdr_alpha = 0.05
        min_observation_n = 20

        [population.qual_batters]
        table = "statcast_batter_exitvelo_barrels"
        value_col = "brl_percent"
        filter_sql = "attempts >= 100"

        [[metric]]
        id = "barrel_rate"
        label = "Barrel %"
        table = "statcast_batter_exitvelo_barrels"
        value_col = "brl_percent"
        population = "qual_batters"
        coverage = "since_2015"
        lenses = ["rank", "extremeness"]
    """).encode()
    return tomllib.loads(raw.decode())


def test_parse_minimal_toml() -> None:
    reg = _parse(_minimal_toml())
    assert isinstance(reg, Registry)
    assert len(reg.metrics) == 1
    assert reg.metrics[0].id == "barrel_rate"
    assert "qual_batters" in reg.populations
    assert reg.scan.top_k == 5


def test_metric_defaults_applied() -> None:
    reg = _parse(_minimal_toml())
    m = reg.metrics[0]
    assert m.id_col == "player_id"
    assert m.year_col == "year"
    assert m.direction == "higher"
    assert m.metric_type == "rate"
    assert m.stabilization_n == 200


def test_population_defaults_applied() -> None:
    reg = _parse(_minimal_toml())
    p = reg.populations["qual_batters"]
    assert p.id_col == "player_id"
    assert p.scope == "mlb"


def test_scan_defaults_applied() -> None:
    reg = _parse({})
    assert reg.scan.top_k == 12
    assert reg.scan.fdr_alpha == pytest.approx(0.05)


def test_load_registry_returns_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_registry() should fall back to the real example TOML."""
    import padres_analytics.detect.registry as mod

    monkeypatch.setattr(mod, "_PRIVATE_METRICS", tmp_path / "nonexistent.toml")
    reg = load_registry()
    assert isinstance(reg, Registry)
    assert len(reg.metrics) > 0


def test_derived_expr_field_preserved() -> None:
    import tomllib

    raw = textwrap.dedent("""
        [population.p]
        table = "t"
        value_col = "v"

        [[metric]]
        id = "gap"
        label = "Gap"
        table = "statcast_batting_expected"
        value_col = "est_woba"
        derived_expr = "ROUND(est_woba - woba, 3)"
        population = "p"
        coverage = "since_2015"
    """).encode()
    reg = _parse(tomllib.loads(raw.decode()))
    assert reg.metrics[0].derived_expr == "ROUND(est_woba - woba, 3)"
