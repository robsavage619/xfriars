"""Novelty scoring for stat candidates."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PRIVATE_WEIGHTS = Path(__file__).resolve().parents[3] / "private" / "interest_weights.toml"
_EXAMPLE_WEIGHTS = (
    Path(__file__).resolve().parents[3] / "examples" / "interest_weights.example.toml"
)


def _load_weights() -> dict:
    """Load interest weights from private/ with fallback to examples/.

    Returns:
        Parsed TOML dict with keys ``weights``, ``thresholds``, ``detector_bonuses``.
    """
    # tomllib is stdlib in 3.11+
    import tomllib

    for candidate in (_PRIVATE_WEIGHTS, _EXAMPLE_WEIGHTS):
        if candidate.exists():
            if candidate == _EXAMPLE_WEIGHTS:
                logger.warning(
                    "Private interest weights not found; using example weights. "
                    "Copy examples/interest_weights.example.toml to private/interest_weights.toml "
                    "and tune from engagement data."
                )
            with candidate.open("rb") as fh:
                return tomllib.load(fh)

    raise FileNotFoundError(
        f"No interest weights found. Expected {_PRIVATE_WEIGHTS} or {_EXAMPLE_WEIGHTS}."
    )


def novelty_score(
    components: dict[str, float],
    detector: str,
) -> tuple[float, dict[str, float]]:
    """Compute a weighted novelty score for a candidate.

    Args:
        components: Dict with keys matching weight names (rarity, magnitude,
            timeliness, rootability, legibility). Values in [0, 1].
        detector: Detector name for optional per-detector bonus.

    Returns:
        Tuple of (final_score, components_used). final_score is clamped to [0, 1].
    """
    cfg = _load_weights()
    w = cfg.get("weights", {})
    bonuses = cfg.get("detector_bonuses", {})

    score = sum(components.get(k, 0.0) * v for k, v in w.items())
    score += bonuses.get(detector, 0.0)
    score = max(0.0, min(1.0, score))

    return score, dict(components)


def min_novelty_threshold() -> float:
    """Return the configured minimum novelty threshold for emission.

    Returns:
        Float threshold below which candidates are suppressed.
    """
    cfg = _load_weights()
    return float(cfg.get("thresholds", {}).get("min_novelty", 0.25))
