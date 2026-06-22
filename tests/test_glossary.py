"""Tests for the plain-language glossary (casual accessibility layer)."""

from __future__ import annotations

from padres_analytics.glossary import GLOSSARY, explain, tier


def test_explain_gives_definition_and_ranking() -> None:
    """A known metric with a value glosses the meaning and where it ranks."""
    out = explain("xwoba", 0.451)
    assert out is not None
    assert "xwOBA is" in out and "elite" in out and "average is about .320" in out


def test_explain_without_value_is_just_the_definition() -> None:
    out = explain("fip")
    assert out is not None and out.startswith("FIP is") and "should" in out
    assert "average is about" not in out  # no ranking without a value


def test_tier_is_direction_aware() -> None:
    """Higher-is-better and lower-is-better metrics both classify correctly."""
    assert tier("xwoba", 0.400) == "elite"  # higher better
    assert tier("xwoba", 0.300) == "below average"
    assert tier("fip", 3.00) == "elite"  # lower better
    assert tier("fip", 4.80) == "below average"
    assert tier("chase_pct", 18) == "elite"  # lower better, swinging less at balls
    assert tier("chase_pct", 33) == "below average"


def test_unknown_metric_returns_none() -> None:
    """Callers can fall back to the raw label when a metric isn't glossed."""
    assert explain("not_a_stat", 1.0) is None
    assert tier("not_a_stat", 1.0) is None


def test_every_term_glosses_cleanly() -> None:
    """No term is jargon-defined-with-jargon: the plain text avoids the stat's own name."""
    for slug, term in GLOSSARY.items():
        text = explain(slug, term.elite)
        assert text is not None and term.name in text and "elite" in text
