"""The prior math: Beta-Bernoulli approve rates and detector reliability.

Deliberately not machine learning. Every number here can be recomputed by hand
from the underlying counts, which matters because these multipliers change what
gets surfaced and Rob has to be able to see *why* one moved. Pure functions, no
state, no dependencies beyond the stdlib.

Three properties are load-bearing:

- **Cold-start neutrality.** A feature with too little evidence returns exactly
  1.0. The engine is starved of labels today, so most features must be silent
  rather than confidently wrong.
- **Bounded influence.** Multipliers are clamped, so no prior can dominate the
  statistical gates. Learning tilts the ranking; it never overrides the math.
- **Decay.** Evidence loses half its weight every 90 days, so last season's
  editorial taste fades instead of ossifying.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

# Beta(2, 2) — a weak, symmetric prior. Two pseudo-observations each way is
# enough to keep a 1-for-1 feature from reading as a 100% approve rate.
_PRIOR_ALPHA = 2.0
_PRIOR_BETA = 2.0

# Below this much (decayed) evidence a feature contributes exactly 1.0.
MIN_EVIDENCE = 5.0

# Per-feature clamp, then the combined clamp. Both are tight on purpose.
_FEATURE_FLOOR, _FEATURE_CEIL = 0.80, 1.25
_COMBINED_FLOOR, _COMBINED_CEIL = 0.70, 1.40

# Evidence half-life. Editorial taste drifts; a dismissal from last spring should
# not still be steering today's feed at full strength.
_HALF_LIFE_DAYS = 90.0

# Detector reliability shrinks toward a coin flip until a detector has a record.
_RELIABILITY_PSEUDO = 3.0
_RELIABILITY_FLOOR, _RELIABILITY_CEIL = 0.85, 1.15


@dataclass(frozen=True)
class FeatureStat:
    """Decayed evidence for one feature key."""

    feature: str
    n_pos: float
    n_total: float
    multiplier: float

    @property
    def rate(self) -> float:
        """Smoothed approve rate."""
        return (self.n_pos + _PRIOR_ALPHA) / (self.n_total + _PRIOR_ALPHA + _PRIOR_BETA)


def decay_weight(observed_at: date, as_of: date, half_life_days: float = _HALF_LIFE_DAYS) -> float:
    """Exponential recency weight for one observation.

    Args:
        observed_at: When the decision was made.
        as_of: Reference date.
        half_life_days: Days after which evidence counts half.

    Returns:
        A weight in (0, 1]. Future-dated observations count fully.
    """
    age = (as_of - observed_at).days
    if age <= 0:
        return 1.0
    return 0.5 ** (age / half_life_days)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def feature_stats(
    observations: list,
    as_of: date,
) -> dict[str, FeatureStat]:
    """Compute a decayed, smoothed approve-rate multiplier per feature.

    The multiplier is the feature's smoothed approve rate divided by the global
    smoothed rate, so it answers "does this kind of claim survive review more or
    less often than average?" — not "is it good in absolute terms." A feature
    below the evidence floor returns exactly 1.0.

    Args:
        observations: Observation records (see ``learn.features``).
        as_of: Reference date for decay.

    Returns:
        Feature key -> FeatureStat.
    """
    pos: dict[str, float] = {}
    tot: dict[str, float] = {}
    global_pos = 0.0
    global_tot = 0.0

    for obs in observations:
        w = decay_weight(obs.observed_at, as_of)
        global_tot += w
        if obs.positive:
            global_pos += w
        for key in obs.features:
            tot[key] = tot.get(key, 0.0) + w
            if obs.positive:
                pos[key] = pos.get(key, 0.0) + w

    if global_tot <= 0:
        return {}

    global_rate = (global_pos + _PRIOR_ALPHA) / (global_tot + _PRIOR_ALPHA + _PRIOR_BETA)

    stats: dict[str, FeatureStat] = {}
    for key, n_total in tot.items():
        n_pos = pos.get(key, 0.0)
        if n_total < MIN_EVIDENCE:
            stats[key] = FeatureStat(key, n_pos, n_total, 1.0)
            continue
        rate = (n_pos + _PRIOR_ALPHA) / (n_total + _PRIOR_ALPHA + _PRIOR_BETA)
        mult = _clamp(rate / global_rate, _FEATURE_FLOOR, _FEATURE_CEIL) if global_rate > 0 else 1.0
        stats[key] = FeatureStat(key, n_pos, n_total, mult)
    return stats


def combine(stats: dict[str, FeatureStat], features: tuple[str, ...] | list[str]) -> float:
    """Combine per-feature multipliers for one item.

    Uses a geometric mean rather than a product: features overlap heavily (a
    conjunction card carries its detector, lens, card type and shape all at
    once), and multiplying correlated evidence would compound one signal into
    several.

    Args:
        stats: Feature statistics from :func:`feature_stats`.
        features: This item's feature keys.

    Returns:
        A multiplier in ``[0.70, 1.40]``; exactly 1.0 when nothing is known.
    """
    mults = [stats[f].multiplier for f in features if f in stats]
    informative = [m for m in mults if m != 1.0]
    if not informative:
        return 1.0
    log_mean = sum(math.log(m) for m in informative) / len(informative)
    return _clamp(math.exp(log_mean), _COMBINED_FLOOR, _COMBINED_CEIL)


def detector_reliability(graded: dict[str, tuple[int, int]]) -> dict[str, float]:
    """Turn prediction outcomes into a per-detector ranking multiplier.

    A detector whose falsifiable calls keep landing has earned more of the feed;
    one that keeps missing has not. Shrunk hard toward a coin flip, because a
    detector with three graded predictions has not proven anything.

    Args:
        graded: detector -> (correct, graded_total).

    Returns:
        detector -> multiplier in ``[0.85, 1.15]``.
    """
    out: dict[str, float] = {}
    for detector, (correct, total) in graded.items():
        if total <= 0:
            out[detector] = 1.0
            continue
        rate = (correct + _RELIABILITY_PSEUDO) / (total + 2 * _RELIABILITY_PSEUDO)
        # 0.5 is the neutral point: map [0,1] accuracy onto the clamp range.
        out[detector] = _clamp(
            1.0 + (rate - 0.5) * 2 * (_RELIABILITY_CEIL - 1.0),
            _RELIABILITY_FLOOR,
            _RELIABILITY_CEIL,
        )
    return out
