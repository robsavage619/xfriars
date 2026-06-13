"""Unit tests for detect/lenses.py — statistical lens functions."""

from __future__ import annotations

import pytest

from padres_analytics.detect.lenses import (
    bh_surviving_indices,
    extremeness_lens,
    pace_lens,
    rank_lens,
)

# ── extremeness_lens ─────────────────────────────────────────────────────────


def _pop(n: int = 300, focal: float = 20.0, spread: float = 10.0) -> list[float]:
    """Synthetic population: focal is near the top."""
    return [focal - spread + i * (2 * spread / n) for i in range(n)]


def test_extremeness_fires_for_elite_value() -> None:
    pop = _pop(300, focal=25.0)
    focal = max(pop) - 0.1  # near the very top
    lr = extremeness_lens(
        focal_value=focal,
        population_values=pop,
        metric_label="Barrel %",
        player_name="Fernando Tatis Jr.",
        higher_is_better=True,
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
        stabilization_n=150,
    )
    assert lr is not None
    assert lr.rarity >= 0.80
    assert "Tatis" in lr.framing
    assert lr.lens == "extremeness"


def test_extremeness_returns_none_for_median() -> None:
    pop = list(range(1, 301))
    focal = 150  # dead median
    lr = extremeness_lens(
        focal_value=float(focal),
        population_values=[float(v) for v in pop],
        metric_label="xwOBA-wOBA Gap",
        player_name="Test Player",
        higher_is_better=True,
        value_format=".3f",
        unit="",
        claim_scope="since_2015",
        stabilization_n=200,
    )
    assert lr is None


def test_extremeness_returns_none_when_population_too_small() -> None:
    lr = extremeness_lens(
        focal_value=99.0,
        population_values=[1.0, 2.0, 3.0],  # far below stabilization_n
        metric_label="Sprint Speed",
        player_name="Test Player",
        higher_is_better=True,
        value_format=".1f",
        unit="ft/s",
        claim_scope="since_2015",
        stabilization_n=200,
    )
    assert lr is None


def test_extremeness_lower_is_better() -> None:
    pop = [float(i) for i in range(1, 301)]
    lr = extremeness_lens(
        focal_value=1.0,  # best when lower_is_better
        population_values=pop,
        metric_label="ERA-",
        player_name="Joe Musgrove",
        higher_is_better=False,
        value_format=".1f",
        unit="",
        claim_scope="since_2015",
        stabilization_n=150,
    )
    assert lr is not None
    assert lr.rarity >= 0.80


# ── rank_lens ─────────────────────────────────────────────────────────────────


def test_rank_lens_fires_top_5() -> None:
    lr = rank_lens(
        focal_rank=4,
        population_size=300,
        player_name="Fernando Tatis Jr.",
        focal_value=18.2,
        metric_label="Barrel %",
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
    )
    assert lr is not None
    assert lr.rarity > 0.0
    assert "#4" in lr.framing
    assert lr.lens == "rank"


def test_rank_lens_suppressed_outside_top_quartile() -> None:
    lr = rank_lens(
        focal_rank=100,
        population_size=300,  # cutoff = min(15, 75) = 15
        player_name="Player",
        focal_value=10.0,
        metric_label="Barrel %",
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
    )
    assert lr is None


def test_rank_lens_rank_1_is_max_rarity() -> None:
    lr = rank_lens(
        focal_rank=1,
        population_size=200,
        player_name="Player",
        focal_value=25.0,
        metric_label="Barrel %",
        value_format=".1f",
        unit="%",
        claim_scope="since_2015",
    )
    assert lr is not None
    assert lr.rarity == pytest.approx(1.0)


# ── pace_lens ─────────────────────────────────────────────────────────────────


def test_pace_lens_on_pace_for_milestone() -> None:
    lr = pace_lens(
        current_value=25.0,
        games_played=81,
        season_games=162,
        player_name="Player",
        metric_label="HR",
        milestone=40.0,
        unit="HR",
        claim_scope="2024",
    )
    assert lr is not None
    assert "50" in lr.framing  # pace = 25 * 162/81 = 50
    assert lr.lens == "pace"


def test_pace_lens_not_on_pace_returns_none() -> None:
    lr = pace_lens(
        current_value=5.0,
        games_played=81,
        season_games=162,
        player_name="Player",
        metric_label="HR",
        milestone=40.0,
        unit="HR",
        claim_scope="2024",
    )
    assert lr is None  # pace = 10, below milestone 40


def test_pace_lens_too_few_games_returns_none() -> None:
    lr = pace_lens(
        current_value=5.0,
        games_played=5,  # below 10-game minimum
        season_games=162,
        player_name="Player",
        metric_label="HR",
        milestone=40.0,
        unit="HR",
        claim_scope="2024",
    )
    assert lr is None


# ── bh_surviving_indices ───────────────────────────────────────────────────────


def test_bh_empty_list() -> None:
    assert bh_surviving_indices([]) == set()


def test_bh_all_strong_signals_survive() -> None:
    rarities = [0.99, 0.98, 0.97, 0.96]
    surviving = bh_surviving_indices(rarities, alpha=0.05)
    assert surviving == {0, 1, 2, 3}


def test_bh_weak_signals_pruned() -> None:
    # 3 tests, alpha=0.05: BH threshold for rank-1 = (1/3)*0.05 = 0.0167
    # rarity 0.99 -> p-proxy 0.01 < 0.0167 -> survives
    # rarity 0.81 -> p-proxy 0.19 >> threshold -> pruned
    rarities = [0.99, 0.81, 0.81]
    surviving = bh_surviving_indices(rarities, alpha=0.05)
    assert 0 in surviving
    assert {1, 2}.isdisjoint(surviving)
