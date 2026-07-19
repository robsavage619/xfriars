"""Learning-prior tests: cold start, clamps, decay, determinism, exploration."""

from __future__ import annotations

from datetime import date, timedelta

from padres_analytics.learn.apply import apply_priors
from padres_analytics.learn.features import Observation, candidate_features
from padres_analytics.learn.priors import (
    MIN_EVIDENCE,
    FeatureStat,
    combine,
    decay_weight,
    detector_reliability,
    feature_stats,
)

_TODAY = date(2026, 7, 18)


def _obs(feature: str, positive: bool, days_ago: int = 0) -> Observation:
    return Observation(
        features=(feature,),
        positive=positive,
        observed_at=_TODAY - timedelta(days=days_ago),
        source="test",
    )


# ── cold start ──────────────────────────────────────────────────────────────


def test_no_observations_means_no_priors() -> None:
    assert feature_stats([], _TODAY) == {}


def test_thin_evidence_stays_exactly_neutral() -> None:
    """The engine is label-starved; a feature must be silent, not confidently wrong."""
    obs = [_obs("detector:scan", True) for _ in range(int(MIN_EVIDENCE) - 1)]
    stats = feature_stats(obs, _TODAY)
    assert stats["detector:scan"].multiplier == 1.0


def test_evidence_floor_lets_a_feature_speak() -> None:
    obs = [_obs("detector:scan", True) for _ in range(12)]
    obs += [_obs("detector:other", False) for _ in range(12)]
    stats = feature_stats(obs, _TODAY)
    assert stats["detector:scan"].multiplier > 1.0
    assert stats["detector:other"].multiplier < 1.0


# ── bounded influence ───────────────────────────────────────────────────────


def test_a_perfect_record_cannot_run_away() -> None:
    """Learning tilts ranking; it must never dominate the statistical gates."""
    obs = [_obs("detector:scan", True) for _ in range(500)]
    obs += [_obs("detector:other", False) for _ in range(500)]
    stats = feature_stats(obs, _TODAY)
    assert stats["detector:scan"].multiplier <= 1.25
    assert stats["detector:other"].multiplier >= 0.80


def test_combined_multiplier_is_clamped() -> None:
    stats = {f"f{i}": FeatureStat(f"f{i}", 9, 10, 1.25) for i in range(6)}
    assert combine(stats, tuple(stats)) <= 1.40


def test_combine_ignores_neutral_features() -> None:
    stats = {
        "known": FeatureStat("known", 9, 10, 1.20),
        "unknown": FeatureStat("unknown", 1, 2, 1.0),
    }
    assert combine(stats, ("known", "unknown")) == combine(stats, ("known",))


def test_combine_with_nothing_known_is_neutral() -> None:
    assert combine({}, ("anything",)) == 1.0


# ── decay ───────────────────────────────────────────────────────────────────


def test_evidence_halves_at_the_half_life() -> None:
    assert decay_weight(_TODAY - timedelta(days=90), _TODAY) == 0.5


def test_todays_evidence_counts_fully() -> None:
    assert decay_weight(_TODAY, _TODAY) == 1.0


def test_stale_verdicts_lose_their_grip() -> None:
    """Editorial taste drifts — last spring's dismissals shouldn't still steer today."""
    fresh = feature_stats([_obs("x", True, 0) for _ in range(20)], _TODAY)
    stale = feature_stats([_obs("x", True, 400) for _ in range(20)], _TODAY)
    assert stale["x"].n_total < fresh["x"].n_total
    assert stale["x"].multiplier == 1.0  # decayed below the evidence floor


# ── determinism ─────────────────────────────────────────────────────────────


def test_same_inputs_give_identical_priors() -> None:
    obs = [_obs("detector:scan", i % 3 == 0) for i in range(30)]
    assert feature_stats(obs, _TODAY) == feature_stats(obs, _TODAY)


# ── detector reliability ────────────────────────────────────────────────────


def test_ungraded_detector_is_neutral() -> None:
    assert detector_reliability({"scan": (0, 0)})["scan"] == 1.0


def test_reliability_shrinks_a_tiny_sample_toward_neutral() -> None:
    """Three graded predictions has not proven anything."""
    small = detector_reliability({"d": (3, 3)})["d"]
    large = detector_reliability({"d": (100, 100)})["d"]
    assert 1.0 < small < large


def test_a_detector_that_keeps_missing_is_down_weighted() -> None:
    assert detector_reliability({"d": (2, 40)})["d"] < 1.0


def test_reliability_is_clamped_both_ways() -> None:
    assert detector_reliability({"d": (500, 500)})["d"] <= 1.15
    assert detector_reliability({"d": (0, 500)})["d"] >= 0.85


# ── application ─────────────────────────────────────────────────────────────


def test_apply_priors_is_a_noop_without_a_snapshot() -> None:
    score, components = apply_priors({}, 0.9, ("detector:scan",))
    assert score == 0.9
    assert components == {}


def test_apply_priors_records_why_the_score_moved() -> None:
    """A prior that changes ranking without a legible reason is indistinguishable from a bug."""
    stats = {"detector:scan": FeatureStat("detector:scan", 18, 20, 1.20)}
    score, components = apply_priors(stats, 0.5, ("detector:scan",))
    assert score > 0.5
    assert components["editorial_prior"] == 1.2
    assert components["raw_novelty"] == 0.5


def test_apply_priors_cannot_exceed_the_score_range() -> None:
    stats = {"f": FeatureStat("f", 20, 20, 1.25)}
    score, _ = apply_priors(stats, 0.99, ("f",))
    assert score <= 1.0


# ── featurization ───────────────────────────────────────────────────────────


def test_candidate_features_are_coarse_enough_to_accumulate() -> None:
    feats = candidate_features(
        "scan",
        {"kind": "dataset", "card_hint": "conjunction", "facts": {"player_id": 1, "n_metrics": 2}},
        [{"lens": "extremeness", "metric_id": "pctl_B_max_ev"}],
        0.93,
        frozenset({1}),
    )
    # The family, not the specific metric — pctl_B_max_ev alone would never
    # accumulate enough examples to leave its prior.
    assert "metric_family:contact_quality" in feats
    assert "metric:pctl_B_max_ev" not in feats
    assert "star_tier:star" in feats
    assert "shape:conjunction" in feats
    assert "rarity_band:90-95" in feats


def test_candidate_features_deduplicate() -> None:
    feats = candidate_features(
        "scan",
        {"kind": "dataset", "facts": {}},
        [{"lens": "rank", "metric_id": "a"}, {"lens": "rank", "metric_id": "b"}],
        0.9,
        frozenset(),
    )
    assert len(feats) == len(set(feats))


# ── end-to-end: does the loop actually close? ───────────────────────────────


def _seed_board_verdicts(conn, kind: str, queued: int, dismissed: int) -> None:
    for i in range(queued + dismissed):
        conn.execute(
            "INSERT INTO board_cards (card_id, kind, angle_key, image_path, status, created_at) "
            "VALUES (?, ?, ?, 'x.png', ?, CURRENT_TIMESTAMP)",
            [f"{kind}-{i}", kind, f"angle_{kind}", "queued" if i < queued else "dismissed"],
        )


def test_board_verdicts_become_priors_and_persist(padres_db) -> None:
    """The whole point: a decision Rob already makes changes what surfaces next."""
    from padres_analytics.learn.apply import latest_stats
    from padres_analytics.learn.run import learn

    _seed_board_verdicts(padres_db, "kept_kind", queued=10, dismissed=1)
    _seed_board_verdicts(padres_db, "killed_kind", queued=1, dismissed=10)

    result = learn(padres_db, _TODAY)
    assert result.observations == 22

    kept = result.feature_stats["card_kind:kept_kind"].multiplier
    killed = result.feature_stats["card_kind:killed_kind"].multiplier
    assert kept > 1.0 > killed

    # And the snapshot is readable by consumers, not just held in memory.
    stats = latest_stats(padres_db)
    assert stats["card_kind:kept_kind"].multiplier == kept


def test_learning_run_is_idempotent(padres_db) -> None:
    from padres_analytics.learn.run import learn

    _seed_board_verdicts(padres_db, "k", queued=8, dismissed=4)
    first = learn(padres_db, _TODAY)
    second = learn(padres_db, _TODAY)
    assert {k: v.multiplier for k, v in first.feature_stats.items()} == {
        k: v.multiplier for k, v in second.feature_stats.items()
    }


def test_report_names_starvation_rather_than_showing_a_clean_sheet(padres_db) -> None:
    """Silence about missing data reads as 'nothing to learn', which is a lie."""
    from padres_analytics.learn.run import learn, report

    text = report(learn(padres_db, _TODAY, dry_run=True))
    assert "No labelled decisions yet" in text
    assert "No graded predictions yet" in text


def test_referee_blocks_are_learned_with_their_reason(padres_db) -> None:
    """The failure mode is the feature — that's what makes the critic teachable."""
    from padres_analytics.learn.run import learn

    # A mix is required for the comparison to mean anything: the multiplier is
    # relative to the global approve rate, so if everything is a block, nothing
    # is relatively worse.
    for i in range(10):
        padres_db.execute(
            "INSERT INTO review_verdicts (verdict_id, target_kind, target_id, packet_hash, "
            "lens, verdict, failure_mode, outcome) "
            "VALUES (?, 'draft', ?, 'h', 'editor', 'BLOCK', 'trivial', 'blocked')",
            [f"v{i}", f"d{i}"],
        )
    for i in range(10):
        padres_db.execute(
            "INSERT INTO review_verdicts (verdict_id, target_kind, target_id, packet_hash, "
            "lens, verdict, failure_mode, outcome) "
            "VALUES (?, 'draft', ?, 'h', 'statistician', 'PASS', NULL, 'cleared')",
            [f"p{i}", f"c{i}"],
        )
    result = learn(padres_db, _TODAY)
    assert result.feature_stats["failure_mode:trivial"].multiplier < 1.0
    assert result.feature_stats["referee_lens:statistician"].multiplier > 1.0
