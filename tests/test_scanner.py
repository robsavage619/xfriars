"""Unit tests for the GenericScanner collapse / dedup / hero-gate logic (P-panel fixes)."""

from __future__ import annotations

from datetime import date

from padres_analytics.detect.conjunction import (
    MAX_CONJUNCTION_MEMBERS,
    ConjunctionGroup,
    find_conjunctions,
    metric_family,
)
from padres_analytics.detect.lenses import LensResult
from padres_analytics.detect.registry import MetricSpec, ScanConfig
from padres_analytics.detect.scanner import (
    _STAR_IDS_FALLBACK,
    GenericScanner,
    _build_conjunction_candidate,
    _build_leaderboard_candidate,
    _Hit,
    _passes_hero_gate,
)

_STAR_ID = next(iter(_STAR_IDS_FALLBACK))


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
    assert _passes_hero_gate(h, _STAR_IDS_FALLBACK) is True


def test_hero_gate_nonstar_elite_extremeness_passes() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=111, player_name="Role Guy", value=30.5, lens="extremeness", rarity=0.96, metric=m
    )
    assert _passes_hero_gate(h, _STAR_IDS_FALLBACK) is True


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
    assert _passes_hero_gate(h, _STAR_IDS_FALLBACK) is False


def test_hero_gate_nonstar_weak_extremeness_suppressed() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=111, player_name="Avg Guy", value=28.0, lens="extremeness", rarity=0.90, metric=m
    )
    assert _passes_hero_gate(h, _STAR_IDS_FALLBACK) is False


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


# ── conjunction candidates (Phase 1a) ───────────────────────────────────────


def _barrel_metric() -> MetricSpec:
    return MetricSpec(
        id="barrel_rate",
        label="Barrel %",
        table="statcast_batter_exitvelo_barrels",
        value_col="brl_percent",
        value_format=".1f",
        unit="%",
        direction="higher",
        population="p",
        coverage="since_2015",
    )


def _conj_group(*, peers_scope: str = "since_2015") -> ConjunctionGroup:
    hits = [
        _hit(
            player_id=111,
            player_name="Test Padre",
            value=29.5,
            lens="extremeness",
            rarity=0.97,
            metric=_sprint_metric(),
        ),
        _hit(
            player_id=111,
            player_name="Test Padre",
            value=18.2,
            lens="extremeness",
            rarity=0.93,
            metric=_barrel_metric(),
        ),
    ]
    hits[1].lens_result = LensResult(
        rarity=0.93, framing="f", claim_scope=peers_scope, lens="extremeness"
    )
    return find_conjunctions(hits)[0]


def test_conjunction_states_both_numerator_and_denominator() -> None:
    """'5 players' without an N is not a verifiable claim."""
    cand = _build_conjunction_candidate(_conj_group(), (3, 140), date(2026, 7, 18))
    headline = cand.facts_json["headline"].lower()
    assert "one of 3 players out of 140 qualified" in headline
    # Both must be audited facts, not prose-only, or the digit audit can't see them.
    assert cand.facts_json["facts"]["players_meeting_all"] == 3
    assert cand.facts_json["facts"]["population_size"] == 140


def test_conjunction_singleton_still_carries_the_population() -> None:
    cand = _build_conjunction_candidate(_conj_group(), (1, 140), date(2026, 7, 18))
    headline = cand.facts_json["headline"].lower()
    assert "only player out of 140 qualified" in headline


def test_conjunction_cut_is_fixed_not_fitted_to_the_subject() -> None:
    """A cut read off the subject's own percentile makes membership true by construction."""
    weak = _build_conjunction_candidate(_conj_group(), (5, 140), date(2026, 7, 18))
    assert weak.facts_json["facts"]["top_percent"] == 10
    assert "top 10%" in weak.facts_json["headline"]


def test_conjunction_without_peer_count_makes_no_uniqueness_claim() -> None:
    cand = _build_conjunction_candidate(_conj_group(), None, date(2026, 7, 18))
    headline = cand.facts_json["headline"].lower()
    assert "one of" not in headline and "only player" not in headline
    assert "players_meeting_all" not in cand.facts_json["facts"]


def test_conjunction_scope_is_the_season_it_compared() -> None:
    """A single-season leaderboard comparison cannot claim its source's full era."""
    group = _conj_group(peers_scope="mlb_all")
    cand = _build_conjunction_candidate(group, (2, 140), date(2026, 7, 18))
    assert cand.claim_scope == "2026"


def test_conjunction_card_hint_routes_to_its_own_template() -> None:
    cand = _build_conjunction_candidate(_conj_group(), (2, 140), date(2026, 7, 18))
    assert cand.facts_json["card_hint"] == "conjunction"


# ── FDR gate (Phase 1b) ─────────────────────────────────────────────────────


def _rarity_hits(rarities: list[float]) -> list[_Hit]:
    m = _sprint_metric()
    return [
        _hit(
            player_id=100 + i,
            player_name=f"P{i}",
            value=28.0,
            lens="extremeness",
            rarity=r,
            metric=m,
        )
        for i, r in enumerate(rarities)
    ]


def test_fdr_advisory_reports_but_never_drops() -> None:
    hits = _rarity_hits([0.999, 0.90, 0.87, 0.86, 0.86, 0.85])
    cfg = ScanConfig(fdr_mode="advisory")
    assert len(GenericScanner._apply_fdr(hits, cfg)) == len(hits)


def test_fdr_strict_drops_borderline_hits() -> None:
    hits = _rarity_hits([0.999, 0.90, 0.87, 0.86, 0.86, 0.85])
    cfg = ScanConfig(fdr_mode="strict")
    # Population large enough that BH is achievable; see the feasibility veto.
    kept = GenericScanner._apply_fdr(hits, cfg, 6, 5000)
    assert len(kept) < len(hits)
    # The overwhelming signal always survives multiplicity correction.
    assert max(h.lens_result.rarity for h in kept) == 0.999


def test_fdr_off_is_a_passthrough() -> None:
    hits = _rarity_hits([0.86, 0.86, 0.86])
    assert GenericScanner._apply_fdr(hits, ScanConfig(fdr_mode="off")) == hits


def test_star_ids_come_from_config_when_present() -> None:
    m = _sprint_metric()
    h = _hit(
        player_id=999,
        player_name="Config Star",
        value=27.0,
        lens="milestone_proximity",
        rarity=0.88,
        metric=m,
    )
    assert _passes_hero_gate(h, frozenset({999})) is True
    assert _passes_hero_gate(h, _STAR_IDS_FALLBACK) is False


# ── conjunction member selection (correlated-metric guard) ──────────────────


def test_correlated_metrics_collapse_to_one_family() -> None:
    """Exit velo, max EV and hard-hit% are one skill — chaining them fakes uniqueness."""
    correlated = ["pctl_B_exit_velocity", "pctl_B_max_ev", "pctl_B_hard_hit_percent"]
    assert len({metric_family(m) for m in correlated}) == 1


def test_distinct_skills_stay_distinct_families() -> None:
    distinct = ["pctl_B_sprint_speed", "pctl_B_oaa", "gap_woba", "pctl_B_chase_percent"]
    assert len({metric_family(m) for m in distinct}) == len(distinct)


def test_unknown_metric_is_its_own_family_never_merged() -> None:
    assert metric_family("some_new_metric") == "some_new_metric"


def _hit_for(metric_id: str, rarity: float) -> _Hit:
    m = _sprint_metric().model_copy(update={"id": metric_id, "label": metric_id})
    return _hit(
        player_id=42, player_name="P", value=1.0, lens="extremeness", rarity=rarity, metric=m
    )


def test_conjunction_keeps_one_member_per_family() -> None:
    hits = [
        _hit_for("pctl_B_exit_velocity", 0.96),
        _hit_for("pctl_B_max_ev", 0.95),
        _hit_for("pctl_B_hard_hit_percent", 0.94),
        _hit_for("pctl_B_sprint_speed", 0.93),
    ]
    group = find_conjunctions(hits)[0]
    assert len(group.hits) == 2  # one contact-quality + one speed
    assert "pctl_B_exit_velocity" in group.metric_ids  # the strongest of its family


def test_conjunction_is_capped_at_max_members() -> None:
    hits = [
        _hit_for("pctl_B_exit_velocity", 0.99),
        _hit_for("pctl_B_sprint_speed", 0.98),
        _hit_for("pctl_B_oaa", 0.97),
        _hit_for("gap_woba", 0.96),
        _hit_for("pctl_B_chase_percent", 0.95),
    ]
    group = find_conjunctions(hits)[0]
    assert len(group.hits) == MAX_CONJUNCTION_MEMBERS


def test_single_family_player_produces_no_conjunction() -> None:
    hits = [_hit_for("pctl_B_exit_velocity", 0.97), _hit_for("pctl_B_max_ev", 0.96)]
    assert find_conjunctions(hits) == []


def test_luck_residuals_never_join_a_conjunction() -> None:
    """ "Elite fielder who has also been unlucky" joins a talent to a coincidence."""
    hits = [_hit_for("pctl_B_oaa", 0.96), _hit_for("gap_woba", 0.88)]
    assert find_conjunctions(hits) == []


def test_conjunction_framing_never_asserts_elite() -> None:
    """Direction differs per metric, so the framing states rank, not a verdict."""
    hits = [_hit_for("pctl_B_oaa", 0.96), _hit_for("pctl_B_sprint_speed", 0.93)]
    group = find_conjunctions(hits)[0]
    cand = _build_conjunction_candidate(group, (5, 140), date(2026, 7, 18))
    assert "elite" not in cand.facts_json["headline"].lower()
