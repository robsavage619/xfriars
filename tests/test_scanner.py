"""Unit tests for the GenericScanner collapse / dedup / hero-gate logic (P-panel fixes)."""

from __future__ import annotations

from datetime import date

from padres_analytics.detect.lenses import LensResult
from padres_analytics.detect.registry import MetricSpec
from padres_analytics.detect.scanner import (
    _STAR_IDS,
    _build_leaderboard_candidate,
    _Hit,
    _passes_hero_gate,
)

_STAR_ID = next(iter(_STAR_IDS))


def _hit(
    *,
    player_id: int,
    player_name: str,
    value: float,
    lens: str,
    rarity: float,
    metric: MetricSpec,
) -> _Hit:
    return _Hit(
        lens_result=LensResult(rarity=rarity, framing="f", claim_scope="since_2015", lens=lens),
        metric=metric,
        player_id=player_id,
        player_name=player_name,
        focal_value=value,
        rank=1,
        population_size=300,
        leaderboard=[],
        resolved_table="statcast_sprint_speed",
        metric_year=2026,
    )


def _sprint_metric() -> MetricSpec:
    return MetricSpec(
        id="sprint_speed",
        label="Sprint Speed",
        table="statcast_sprint_speed",
        value_col="sprint_speed",
        value_format=".1f",
        unit="ft/s",
        direction="higher",
        population="p",
        coverage="since_2015",
    )


# ── hero gate ───────────────────────────────────────────────────────────────


def test_hero_gate_star_passes_even_when_not_elite() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=_STAR_ID,
        player_name="Star",
        value=27.5,
        lens="milestone_proximity",
        rarity=0.88,
        metric=m,
    )
    assert _passes_hero_gate(h) is True


def test_hero_gate_nonstar_elite_extremeness_passes() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=111, player_name="Role Guy", value=30.5, lens="extremeness", rarity=0.96, metric=m
    )
    assert _passes_hero_gate(h) is True


def test_hero_gate_nonstar_milestone_suppressed() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=111,
        player_name="Bench Guy",
        value=27.5,
        lens="milestone_proximity",
        rarity=0.90,
        metric=m,
    )
    assert _passes_hero_gate(h) is False


def test_hero_gate_nonstar_weak_extremeness_suppressed() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=111, player_name="Avg Guy", value=28.0, lens="extremeness", rarity=0.90, metric=m
    )
    assert _passes_hero_gate(h) is False


# ── leaderboard collapse ─────────────────────────────────────────────────────


def test_leaderboard_collapse_ranks_and_titles() -> None:
    m = _sprint_metric()
    hits = [
        _hit(
            player_id=1,
            player_name="Slow",
            value=27.5,
            lens="milestone_proximity",
            rarity=0.86,
            metric=m,
        ),
        _hit(
            player_id=2,
            player_name="Fast",
            value=28.9,
            lens="milestone_proximity",
            rarity=0.94,
            metric=m,
        ),
        _hit(
            player_id=3,
            player_name="Mid",
            value=28.2,
            lens="milestone_proximity",
            rarity=0.90,
            metric=m,
        ),
    ]
    cand = _build_leaderboard_candidate(m, hits, date(2026, 6, 14))
    assert cand.payload_kind == "dataset"
    facts = cand.facts_json
    assert facts["card_hint"] == "bar"
    assert facts["title"] == "FASTEST PADRES"  # presentation override
    # Ranked highest-first for a higher-is-better metric
    assert [r[0] for r in facts["rows"]] == ["Fast", "Mid", "Slow"]
    assert facts["facts"]["leader_name"] == "Fast"
    assert "fastest Padre" in facts["headline"]


def test_leaderboard_xwoba_gap_breakout_framing() -> None:
    m = MetricSpec(
        id="xwoba_gap",
        label="xwOBA-wOBA Gap",
        table="statcast_batting_expected",
        value_col="est_woba",
        derived_expr="est_woba - woba",
        value_format="+.3f",
        direction="higher",
        population="p",
        coverage="since_2015",
    )
    hits = [
        _hit(player_id=1, player_name="A", value=0.072, lens="extremeness", rarity=0.91, metric=m),
        _hit(player_id=2, player_name="B", value=0.046, lens="extremeness", rarity=0.89, metric=m),
        _hit(player_id=3, player_name="C", value=0.045, lens="extremeness", rarity=0.88, metric=m),
    ]
    cand = _build_leaderboard_candidate(m, hits, date(2026, 6, 14))
    assert cand.facts_json["title"] == "DUE FOR A BREAKOUT"
    # Honest regression framing, never a "top 1% flex"
    assert "gap between expected and actual" in cand.facts_json["headline"]
    assert "top 1%" not in cand.facts_json["headline"]
