"""P0 tests for the ChartDataset abstraction.

Proves the new dataset payload preserves the accuracy regime: its dumped JSON is
the digit-audit corpus (parity with TablePayload), and the Path-B sanity check
validates dataset structure and per-column domains.
"""

from __future__ import annotations

from datetime import date

import pytest

from padres_analytics.detect.candidates import (
    ChartDataset,
    Column,
    Mark,
    TablePayload,
    audit_corpus,
)
from padres_analytics.tweets.verify import (
    VerificationError,
    _sanity_check_facts,
    digit_audit,
)


def _table() -> TablePayload:
    return TablePayload(
        title="MLB BARREL RATE",
        subtitle="2024 Season",
        as_of=date(2024, 6, 9),
        columns=["#", "Player", "Brl%"],
        rows=[["1", "Aaron Judge", "26.5"], ["4", "Fernando Tatis Jr.", "18.2"]],
        highlight_row=1,
        source="Baseball Savant",
        headline="Tatis ranks 4th in barrel rate",
        claim_scope="since_2015",
    )


def _dataset() -> ChartDataset:
    return ChartDataset(
        title="BARREL RATE",
        subtitle="vs qualified MLB hitters",
        as_of=date(2024, 6, 9),
        columns=[
            Column(key="player", label="Player", role="dimension"),
            Column(key="brl", label="Brl%", role="measure", unit="%", domain=(0.0, 40.0)),
        ],
        rows=[["Aaron Judge", 26.5], ["Fernando Tatis Jr.", 18.2]],
        highlight=[Mark(row_index=1, label="Tatis Jr.", note="4th")],
        hero={"value": "18.2", "label": "Barrel %", "context": "4th in MLB"},
        framing="4th in MLB barrel rate (Statcast era)",
        population_label="Qualified MLB hitters, 2024",
        n=129,
        source="Baseball Savant",
        headline="Tatis ranks 4th in barrel rate",
        claim_scope="since_2015",
        facts={"padre_value": 18.2, "padre_rank": 4},
    )


def test_audit_corpus_contains_every_number() -> None:
    """Every renderable number must land in the audit corpus string."""
    corpus = audit_corpus(_dataset())
    for token in ("18.2", "26.5", "129", "4", "40.0"):
        assert token in corpus, f"{token!r} missing from audit corpus"


def test_digit_audit_parity_with_table() -> None:
    """A caption that passes against the table dump also passes against the dataset dump."""
    caption = "Tatis Jr. sits at 18.2% barrels — 4th in MLB."
    table_offenders = digit_audit(caption, _table().model_dump(mode="json"))
    dataset_offenders = digit_audit(caption, _dataset().model_dump(mode="json"))
    assert table_offenders == []
    assert dataset_offenders == []


def test_digit_audit_catches_invented_number_in_dataset() -> None:
    """A number absent from the dataset must be flagged regardless of payload type."""
    caption = "Tatis Jr. sits at 18.2% barrels — best mark since 1998."
    offenders = digit_audit(caption, _dataset().model_dump(mode="json"))
    assert "1998" in offenders


def test_dataset_sanity_check_passes() -> None:
    checks: list[str] = []
    _sanity_check_facts(_dataset().model_dump(mode="json"), checks)
    assert any("dataset shape OK" in c for c in checks)


def test_dataset_sanity_check_rejects_misaligned_rows() -> None:
    facts = _dataset().model_dump(mode="json")
    facts["rows"] = [["Aaron Judge", 26.5, "extra"]]  # 3 cells, 2 columns
    with pytest.raises(VerificationError, match="match columns"):
        _sanity_check_facts(facts, [])


def test_dataset_sanity_check_rejects_out_of_domain() -> None:
    facts = _dataset().model_dump(mode="json")
    facts["rows"] = [["Aaron Judge", 26.5], ["Fernando Tatis Jr.", 99.0]]  # 99 > domain max 40
    with pytest.raises(VerificationError, match="outside declared domain"):
        _sanity_check_facts(facts, [])


def test_dataset_nested_facts_range_checked() -> None:
    facts = _dataset().model_dump(mode="json")
    facts["facts"] = {"era": 42.0}  # impossible ERA in nested facts
    with pytest.raises(VerificationError, match=r"era = 42.0 outside"):
        _sanity_check_facts(facts, [])
