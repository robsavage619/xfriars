"""Splits, pitch-level aggregates, and the split-contrast lens."""

from __future__ import annotations

import pytest

from padres_analytics.detect.aggregates import BATTER_AGGS, fetch_agg_rows
from padres_analytics.detect.contrast import (
    MIN_CONTRAST_POPULATION,
    MIN_SIDE_OPPORTUNITIES,
    ContrastRow,
    split_contrast_lens,
)
from padres_analytics.detect.lenses import extremeness_lens
from padres_analytics.detect.splits import (
    CONTRAST_PAIRS,
    ENUM_COLUMNS,
    SplitError,
    SplitSpec,
    parse,
    render_predicate,
)

_CHASE = next(m for m in BATTER_AGGS if m.id == "chase_rate")
_SWING = next(m for m in BATTER_AGGS if m.id == "swing_rate")


# ── the trust boundary ──────────────────────────────────────────────────────


def test_only_allowlisted_columns_are_accepted() -> None:
    with pytest.raises(SplitError):
        parse("player_name", "Machado")


def test_only_allowlisted_values_are_accepted() -> None:
    """The allowlist is the whole defense — an unknown value must never render."""
    with pytest.raises(SplitError):
        parse("p_throws", "X")


def test_injection_attempt_is_rejected_not_escaped() -> None:
    with pytest.raises(SplitError):
        parse("p_throws", "L' OR 1=1 --")


def test_literals_originate_in_the_allowlist() -> None:
    assert render_predicate(SplitSpec(column="p_throws", value="L")) == "p_throws = 'L'"


def test_derived_families_expand_to_their_real_column() -> None:
    pred = render_predicate(SplitSpec(column="pitch_class", value="breaking"))
    assert pred.startswith("pitch_type IN (")
    assert "'SL'" in pred and "'FF'" not in pred


def test_zone_buckets_render_as_numeric_predicates() -> None:
    pred = render_predicate(SplitSpec(column="zone_bucket", value="chase"))
    assert pred == "zone IN (11, 12, 13, 14)"
    assert "'" not in pred


def test_every_contrast_pair_is_allowlisted() -> None:
    for a, b in CONTRAST_PAIRS.values():
        assert a.value in ENUM_COLUMNS[a.column]
        assert b.value in ENUM_COLUMNS[b.column]


def test_splits_carry_readable_labels() -> None:
    assert SplitSpec(column="p_throws", value="L").display() == "vs LHP"
    assert SplitSpec(column="pitch_class", value="breaking").display() == "vs breaking balls"


# ── metric/split coherence ──────────────────────────────────────────────────


def test_chase_rate_refuses_a_zone_split() -> None:
    """Chase is defined on out-of-zone pitches; slicing it by zone is incoherent."""
    assert _CHASE.accepts(SplitSpec(column="zone_bucket", value="heart")) is False
    assert _CHASE.accepts(SplitSpec(column="p_throws", value="L")) is True
    assert _CHASE.accepts(None) is True


def test_swing_rate_accepts_every_split() -> None:
    for column, values in ENUM_COLUMNS.items():
        assert _SWING.accepts(SplitSpec(column=column, value=next(iter(values))))


def test_rates_have_a_real_denominator() -> None:
    """A rate over the wrong opportunity set is a different, meaningless stat."""
    for metric in BATTER_AGGS:
        assert metric.denominator
        assert metric.stabilization_n > 0


# ── shrinkage uses the player's own sample ──────────────────────────────────


def test_extremeness_shrinks_on_the_focal_sample_not_the_league_size() -> None:
    """A thin split must be shrunk even when the league population is large."""
    population = [float(i) for i in range(200)]
    thin = extremeness_lens(
        focal_value=199.0,
        population_values=population,
        metric_label="Chase Rate",
        player_name="P",
        higher_is_better=True,
        value_format=".1f",
        unit="%",
        claim_scope="2026",
        stabilization_n=250,
        focal_n=60,
    )
    full = extremeness_lens(
        focal_value=199.0,
        population_values=population,
        metric_label="Chase Rate",
        player_name="P",
        higher_is_better=True,
        value_format=".1f",
        unit="%",
        claim_scope="2026",
        stabilization_n=250,
        focal_n=250,
    )
    # Shrinkage pulls the thin sample below the emit threshold entirely, which
    # is the point: a split too small to trust produces no card at all.
    assert thin is None
    assert full is not None and full.rarity > 0.9


def test_a_tiny_focal_sample_is_refused_outright() -> None:
    result = extremeness_lens(
        focal_value=199.0,
        population_values=[float(i) for i in range(200)],
        metric_label="Chase Rate",
        player_name="P",
        higher_is_better=True,
        value_format=".1f",
        unit="%",
        claim_scope="2026",
        stabilization_n=250,
        focal_n=12,
    )
    assert result is None


# ── the contrast lens ───────────────────────────────────────────────────────


def _row(pid: int, a: float, b: float, n: int = 400) -> ContrastRow:
    return ContrastRow(player_id=pid, player_name=f"P{pid}", a_value=a, b_value=b, a_n=n, b_n=n)


def _population(size: int = 60) -> list[ContrastRow]:
    return [_row(i, 30.0 + i * 0.1, 30.0) for i in range(size)]


def _lens(focal: ContrastRow, population: list[ContrastRow]):
    a, b = CONTRAST_PAIRS["platoon"]
    return split_contrast_lens(
        focal=focal,
        population=population,
        metric=_SWING,
        split_a=a,
        split_b=b,
        claim_scope="2026, vs LHP against vs RHP, min 60 pitches each side",
    )


def test_a_thin_population_yields_no_claim() -> None:
    """A differential carries ~2x the variance; the gap distribution needs bodies."""
    small = _population(MIN_CONTRAST_POPULATION - 1)
    assert _lens(small[0], small) is None


def test_a_thin_focal_sample_yields_no_claim() -> None:
    """A platoon split over 30 pitches is noise wearing a narrative."""
    pop = _population()
    focal = ContrastRow(player_id=1, player_name="P", a_value=99.0, b_value=30.0, a_n=20, b_n=400)
    assert _lens(focal, [*pop, focal]) is None


def test_an_extreme_gap_fires() -> None:
    pop = _population()
    focal = _row(999, 90.0, 30.0)
    result = _lens(focal, [*pop, focal])
    assert result is not None
    assert result.lens == "split_contrast"


def test_a_middling_gap_does_not_fire() -> None:
    pop = _population()
    middle = pop[len(pop) // 2]
    assert _lens(middle, pop) is None


def test_framing_states_both_sides_not_just_the_gap() -> None:
    """A bare differential hides which term drives it — restraint and aggression differ."""
    pop = _population()
    focal = _row(999, 90.0, 30.0)
    result = _lens(focal, [*pop, focal])
    assert result is not None
    assert "90.0%" in result.framing and "30.0%" in result.framing


def test_framing_never_calls_the_sample_the_whole_league() -> None:
    """The population is whoever has pitch-level data ingested, not MLB."""
    pop = _population()
    focal = _row(999, 90.0, 30.0)
    result = _lens(focal, [*pop, focal])
    assert result is not None
    assert "with pitch-level data" in result.framing
    assert "of MLB" not in result.framing


def test_claim_scope_carries_the_split_qualifiers() -> None:
    pop = _population()
    focal = _row(999, 90.0, 30.0)
    result = _lens(focal, [*pop, focal])
    assert result is not None
    assert "vs LHP" in result.claim_scope
    assert str(MIN_SIDE_OPPORTUNITIES) in result.claim_scope


def test_weaker_side_drives_reliability() -> None:
    row = ContrastRow(player_id=1, player_name="P", a_value=50.0, b_value=30.0, a_n=800, b_n=61)
    assert row.weaker_n == 61


# ── aggregate SQL against a fixture ─────────────────────────────────────────


def _seed_pitches(conn) -> None:
    conn.execute(
        """
        CREATE TABLE statcast_batter_pitches (
            batter_id INTEGER, batter_name VARCHAR, season INTEGER,
            game_date DATE, pitch_type VARCHAR, zone INTEGER,
            description VARCHAR, p_throws VARCHAR
        )
        """
    )
    rows = []
    # Player 1: swings at every out-of-zone pitch (chase rate 100%).
    for _ in range(120):
        rows.append((1, "Chaser", 2026, "2026-05-01", "FF", 11, "swinging_strike", "R"))
    # Player 2: never chases (chase rate 0%).
    for _ in range(120):
        rows.append((2, "Patient", 2026, "2026-05-01", "FF", 11, "ball", "R"))
    conn.executemany("INSERT INTO statcast_batter_pitches VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def test_chase_rate_is_hand_computable(padres_db) -> None:
    padres_db.execute("DROP TABLE IF EXISTS statcast_batter_pitches")
    _seed_pitches(padres_db)
    rows, sizes, _ = fetch_agg_rows(padres_db, _CHASE, 2026)
    by_id = {pid: val for pid, _, val in rows}
    assert by_id[1] == 100.0
    assert by_id[2] == 0.0
    assert sizes[1] == 120  # the denominator is out-of-zone pitches, not all pitches


def test_split_narrows_the_aggregate(padres_db) -> None:
    padres_db.execute("DROP TABLE IF EXISTS statcast_batter_pitches")
    _seed_pitches(padres_db)
    left = fetch_agg_rows(padres_db, _CHASE, 2026, SplitSpec(column="p_throws", value="L"))[0]
    right = fetch_agg_rows(padres_db, _CHASE, 2026, SplitSpec(column="p_throws", value="R"))[0]
    assert left == []  # no LHP faced in the fixture
    assert right


# ── multiplicity feasibility ────────────────────────────────────────────────


def test_bh_is_unachievable_at_our_population_and_battery() -> None:
    """An ECDF over n players can't resolve below 1/n; BH needs alpha/m."""
    from padres_analytics.detect.lenses import bh_is_feasible

    # 135 ingested hitters, 46 comparisons: even a perfect result can't pass.
    assert bh_is_feasible(population_size=135, battery_size=46, alpha=0.05) is False
    # A big population and a small battery can.
    assert bh_is_feasible(population_size=5000, battery_size=3, alpha=0.05) is True


def test_strict_mode_falls_back_rather_than_emptying_the_feed() -> None:
    """A gate nothing can pass is indistinguishable from a broken one."""
    from padres_analytics.detect.lenses import LensResult
    from padres_analytics.detect.registry import MetricSpec, ScanConfig
    from padres_analytics.detect.scanner import GenericScanner, _Hit

    m = MetricSpec(id="x", label="X", table="t", value_col="v", population="p")
    hits = [
        _Hit(
            LensResult(0.99, "f", "2026", "extremeness"), m, i, f"P{i}", 1.0, 1, 135, [], "t", 2026
        )
        for i in range(30)
    ]
    kept = GenericScanner._apply_fdr(hits, ScanConfig(fdr_mode="strict"), 46, 135)
    assert len(kept) == len(hits)  # infeasible -> advisory, nothing dropped

    dropped = GenericScanner._apply_fdr(hits, ScanConfig(fdr_mode="strict"), 3, 5000)
    assert len(dropped) < len(hits)  # feasible -> actually enforced


def test_expected_false_discoveries_reports_the_noise_floor() -> None:
    from padres_analytics.detect.lenses import expected_false_discoveries

    assert expected_false_discoveries(46, 0.85) == pytest.approx(6.9)
    assert expected_false_discoveries(10, 1.0) == 0.0


# ── career baselines ────────────────────────────────────────────────────────


def test_career_shift_removes_league_drift() -> None:
    """A player who moved with the league has not moved."""
    from padres_analytics.detect.changepoint import CareerShift

    shift = CareerShift(
        player_id=1,
        player_name="P",
        metric="ops",
        metric_label="OPS",
        value_format=".3f",
        current=0.700,
        baseline=0.800,
        prior_seasons=4,
        league_delta=-0.100,
        cohort_sd=0.050,
        season=2026,
    )
    assert shift.raw_delta == pytest.approx(-0.100)
    assert shift.net_delta == pytest.approx(0.0)
    assert shift.z == pytest.approx(0.0)


def test_career_shift_z_is_relative_to_how_players_normally_move() -> None:
    from padres_analytics.detect.changepoint import CareerShift

    shift = CareerShift(
        player_id=1,
        player_name="P",
        metric="ops",
        metric_label="OPS",
        value_format=".3f",
        current=0.900,
        baseline=0.800,
        prior_seasons=4,
        league_delta=0.0,
        cohort_sd=0.050,
        season=2026,
    )
    assert shift.z == pytest.approx(2.0)


def test_career_shift_framing_states_the_baseline_and_the_league_move() -> None:
    from padres_analytics.detect.changepoint import CareerShift

    shift = CareerShift(
        player_id=1,
        player_name="Test Padre",
        metric="ops",
        metric_label="OPS",
        value_format=".3f",
        current=0.900,
        baseline=0.800,
        prior_seasons=4,
        league_delta=-0.020,
        cohort_sd=0.050,
        season=2026,
    )
    text = shift.framing()
    assert "0.800" in text and "0.900" in text
    assert "4-season baseline" in text
    assert "league" in text


def test_a_thin_cohort_yields_no_shift_at_all(padres_db) -> None:
    """Three players is not a distribution; dividing by its spread invents z-scores."""
    from padres_analytics.detect.changepoint import detect_career_shifts

    padres_db.execute(
        """
        CREATE TABLE player_season_batting (
            player_id INTEGER, player_name VARCHAR, season INTEGER, team_id INTEGER,
            pa INTEGER, ops VARCHAR, obp VARCHAR, slg VARCHAR
        )
        """
    )
    rows = []
    for pid in range(3):
        for season in (2022, 2023, 2024, 2025):
            rows.append((pid, f"P{pid}", season, 135, 400, "0.800", "0.350", "0.450"))
        rows.append((pid, f"P{pid}", 2026, 135, 400, "0.500", "0.250", "0.250"))
    padres_db.executemany("INSERT INTO player_season_batting VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    assert detect_career_shifts(padres_db, 2026, {0, 1, 2}) == []


# ── the population caveat corrects itself as ingest fills in ────────────────


def test_partial_coverage_says_it_is_not_the_league(padres_db) -> None:
    """Calling a convenience sample 'the league' is what a referee blocked."""
    from padres_analytics.detect.aggregates import population_label

    padres_db.executemany(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa) "
        "VALUES (?, ?, 2026, 400)",
        [(i, f"P{i}") for i in range(300)],
    )
    label = population_label(padres_db, measured=135, year=2026)
    assert "not the full league" in label
    assert "135 of 300" in label


def test_near_complete_coverage_drops_the_caveat(padres_db) -> None:
    """Once the sample is the league, the caveat undersells a real comparison."""
    from padres_analytics.detect.aggregates import population_label

    padres_db.executemany(
        "INSERT INTO statcast_batting_expected (player_id, player_name, year, pa) "
        "VALUES (?, ?, 2026, 400)",
        [(i, f"P{i}") for i in range(300)],
    )
    label = population_label(padres_db, measured=290, year=2026)
    assert "not the full league" not in label
    assert "league-wide" in label
    # Must not claim a PA qualification the measured group doesn't necessarily hold.
    assert "min 100 PA" not in label


def test_label_is_conservative_when_the_population_is_unknown(padres_db) -> None:
    from padres_analytics.detect.aggregates import population_label

    label = population_label(padres_db, measured=42, year=2026)
    assert "event-level data" in label
    assert "league-wide" not in label
