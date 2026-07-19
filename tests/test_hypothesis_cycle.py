"""The daily hypothesis cycle and the honesty of its ledger outcomes."""

from __future__ import annotations

import json
from pathlib import Path

from padres_analytics.daily import _run_hypothesis_cycle


def test_cycle_stages_tomorrows_pack_even_with_an_empty_queue(padres_db, tmp_path, monkeypatch):
    """Both halves must run daily, or the machinery is complete and never fires."""
    monkeypatch.chdir(tmp_path)
    from datetime import date

    notes = _run_hypothesis_cycle(padres_db, date(2026, 7, 18))
    pack = Path("inbox") / "hypothesis_context_2026-07-18.json"
    assert pack.exists()
    assert any("Context pack" in n for n in notes)


def test_pack_carries_the_split_vocabulary(padres_db, tmp_path, monkeypatch):
    """A proposer that can't see the split axis can't propose the interesting questions."""
    monkeypatch.chdir(tmp_path)
    from datetime import date

    _run_hypothesis_cycle(padres_db, date(2026, 7, 18))
    pack = json.loads(
        (Path("inbox") / "hypothesis_context_2026-07-18.json").read_text(encoding="utf-8")
    )
    vocab = pack["split_vocabulary"]
    assert "p_throws" in vocab["legal_splits"]
    assert "platoon" in vocab["contrast_pairs"]
    assert any(m["id"] == "chase_rate" for m in vocab["aggregate_metrics"])
    # Incompatible crosses must be advertised, not discovered by failure.
    chase = next(m for m in vocab["aggregate_metrics"] if m["id"] == "chase_rate")
    assert "zone_bucket" in chase["incompatible_splits"]


def test_pack_tells_the_proposer_what_keeps_getting_blocked(padres_db, tmp_path, monkeypatch):
    """Proposal quality compounds only if the critic's reasons feed back."""
    monkeypatch.chdir(tmp_path)
    from datetime import date

    for i in range(3):
        padres_db.execute(
            "INSERT INTO review_verdicts (verdict_id, target_kind, target_id, packet_hash, "
            "lens, verdict, failure_mode, outcome) "
            "VALUES (?, 'draft', ?, 'h', 'editor', 'BLOCK', 'trivial', 'blocked')",
            [f"v{i}", f"d{i}"],
        )
    _run_hypothesis_cycle(padres_db, date(2026, 7, 18))
    pack = json.loads(
        (Path("inbox") / "hypothesis_context_2026-07-18.json").read_text(encoding="utf-8")
    )
    modes = pack["referee_history"]["common_failure_modes"]
    assert any(m["failure_mode"] == "trivial" and m["n"] == 3 for m in modes)


def test_a_failing_scan_never_costs_the_day_its_story(padres_db, tmp_path, monkeypatch):
    """Discovery is additive to the briefing; a bad spec must not raise."""
    monkeypatch.chdir(tmp_path)
    from datetime import date

    padres_db.execute("DROP TABLE IF EXISTS hypothesis_queue")
    notes = _run_hypothesis_cycle(padres_db, date(2026, 7, 18))
    assert isinstance(notes, list)  # reported, not raised


def test_identifier_columns_are_not_proposable_metrics(padres_db, tmp_path, monkeypatch):
    """ "Average batter_id" is not a stat."""
    monkeypatch.chdir(tmp_path)
    from datetime import date

    _run_hypothesis_cycle(padres_db, date(2026, 7, 18))
    pack = json.loads(
        (Path("inbox") / "hypothesis_context_2026-07-18.json").read_text(encoding="utf-8")
    )
    for table in pack["metric_catalog"]:
        assert "batter_id" not in table["numeric_columns"]
        assert "pitcher_id" not in table["numeric_columns"]
        assert "player_id" not in table["numeric_columns"]
