"""Tests for the prompt desk — the Studio's handoff to Claude.

A prompt is the app's entire contribution to the analysis step, so what matters
is that it is complete: every number the writer may cite is in it, the rules that
would reject the work are stated before the work is done, the output contract
parses, and there is an honest way to return nothing.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import date
from typing import TYPE_CHECKING

import pytest

from padres_analytics.app import prompts

if TYPE_CHECKING:
    import duckdb


def _flat(text: str) -> str:
    """Collapse the prompt's line wrapping so phrase assertions survive it."""
    return re.sub(r"\s+", " ", text).lower()


def _json_blocks(prompt: str) -> list[dict]:
    """Every JSON object embedded in a prompt, in order."""
    found: list[dict] = []
    for match in re.finditer(r"^\{$", prompt, re.MULTILINE):
        depth, start = 0, match.start()
        for i, ch in enumerate(prompt[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                with contextlib.suppress(json.JSONDecodeError):
                    found.append(json.loads(prompt[start : i + 1]))
                break
    return found


_FACTS = {
    "kind": "table",
    "title": "Hard-Hit % leaders",
    "framing": "this season",
    "rows": [{"metric": "Hard-Hit %", "player": "Fernando Tatis Jr.", "value": 56.3, "rank": 1}],
    "season": 2026,
}


def _candidate(conn: duckdb.DuckDBPyConnection, cid: str = "cand1") -> str:
    conn.execute(
        """
        INSERT INTO stat_candidates (
            candidate_id, detector, subject, as_of, payload_kind,
            facts_json, provenance_json, coverage_window, claim_scope, novelty_score
        ) VALUES (?, 'statcast', 'Fernando Tatis Jr.', '2026-06-20', 'table',
                  ?, '[]', '2015-2026', 'since_2015', 0.87)
        """,
        [cid, json.dumps(_FACTS)],
    )
    return cid


def _lead(conn: duckdb.DuckDBPyConnection) -> str:
    from padres_analytics.board import add_leads
    from padres_analytics.detect.leads import Lead

    add_leads(
        conn,
        [Lead("Fernando Tatis Jr.", "scan", "Tatis: 56.3 Hard-Hit %, top 5%", "dig in", 88.0)],
    )
    row = conn.execute("SELECT lead_id FROM board_leads LIMIT 1").fetchone()
    assert row is not None
    return row[0]


# ── The dossier carries every number the writer may use ───────────────────────


def test_draft_prompt_embeds_the_facts_verbatim(padres_db: duckdb.DuckDBPyConnection) -> None:
    """The digit audit rejects any number not in facts, so the prompt must carry them all."""
    cid = _candidate(padres_db)
    spec = prompts.draft_prompt(padres_db, cid)

    assert "56.3" in spec.prompt, "the value the caption will cite must be in the prompt"
    assert "Fernando Tatis Jr." in spec.prompt
    assert "Hard-Hit %" in spec.prompt
    assert cid in spec.prompt, "the caption must carry a real candidate_id"
    assert spec.subject == "Fernando Tatis Jr."


def test_draft_prompt_states_the_rules_that_would_reject_the_work(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """Constraints stated after the fact are just a rejection; these come first."""
    spec = prompts.draft_prompt(padres_db, _candidate(padres_db))

    assert "never compute" in _flat(spec.prompt)
    assert "digit audit" in _flat(spec.prompt)
    assert "280" in spec.prompt, "the length limit is a hard gate"
    assert "since 2015" in spec.prompt, "the coverage window bounds the superlative"
    assert "since_2015" in spec.prompt, "the candidate's own claim scope"


def test_draft_prompt_carries_the_voice_ban_list(padres_db: duckdb.DuckDBPyConnection) -> None:
    spec = prompts.draft_prompt(padres_db, _candidate(padres_db))
    for tell in ("let's dive in", "bottom line", "it's not just"):
        assert tell in _flat(spec.prompt), f"banned tell {tell!r} must be named"


def test_prompt_output_contract_is_valid_json(padres_db: duckdb.DuckDBPyConnection) -> None:
    """The example must parse — a contract you can't copy is not a contract."""
    spec = prompts.draft_prompt(padres_db, _candidate(padres_db))
    contracts = [b for b in _json_blocks(spec.prompt) if "text" in b]
    assert contracts, "the prompt shows a JSON shape"
    assert set(contracts[0]) >= {"candidate_id", "text", "interesting_judgment", "model"}


def test_every_prompt_offers_an_honest_exit(padres_db: duckdb.DuckDBPyConnection) -> None:
    """Returning nothing must be a first-class outcome, or the writer invents a story."""
    for spec in (
        prompts.draft_prompt(padres_db, _candidate(padres_db, "c_exit")),
        prompts.dive_prompt(padres_db, _lead(padres_db)),
    ):
        assert '"no_story"' in spec.prompt
        assert "good outcome" in _flat(spec.prompt)


def test_missing_target_is_a_lookup_error(padres_db: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(LookupError):
        prompts.draft_prompt(padres_db, "nope")
    with pytest.raises(LookupError):
        prompts.dive_prompt(padres_db, "nope")


# ── The dive asks for investigation, not just prose ───────────────────────────


def test_dive_prompt_asks_the_questions_that_kill_leads(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    spec = prompts.dive_prompt(padres_db, _lead(padres_db))

    lowered = _flat(spec.prompt)
    for probe in ("sample", "endpoint", "denominator", "baseline", "confound"):
        assert probe in lowered, f"the dive must probe {probe}"
    assert "starting point, not a finding" in lowered, "a lead is not a finding"


def test_dive_prompt_links_the_engine_candidate_for_the_subject(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """Without a real candidate_id the resulting draft cannot be verified."""
    cid = _candidate(padres_db)
    spec = prompts.dive_prompt(padres_db, _lead(padres_db))

    assert cid in spec.prompt
    assert "56.3" in spec.prompt, "the dive gets the same dossier the draft would"


def test_dive_without_a_candidate_says_so_rather_than_inventing_one(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    spec = prompts.dive_prompt(padres_db, _lead(padres_db))
    assert "honest exit" in _flat(spec.prompt)
    assert "inventing" in _flat(spec.prompt) or "real candidate_id" in spec.prompt


# ── The referee prompt keeps the referee out of the numbers ───────────────────


def test_review_prompt_forbids_returning_numbers(padres_db: duckdb.DuckDBPyConnection) -> None:
    """The invariant that makes the referee safe: verdicts and critique, never facts."""
    cid = _candidate(padres_db)
    padres_db.execute(
        "INSERT INTO tweet_drafts (draft_id, candidate_id, text, status) VALUES (?,?,?,?)",
        ["d1", cid, "Tatis is hitting the ball harder than anyone: 56.3% hard-hit.", "verified"],
    )

    spec = prompts.review_prompt(padres_db, "d1")

    assert "never return numbers" in _flat(spec.prompt)
    assert "BLOCK" in spec.prompt
    for lens in ("statistician", "causal", "coverage", "editor", "voice"):
        assert lens in spec.prompt
    assert "packet_hash" in spec.prompt, "clearance must be tied to exact content"
    assert "0.6" in spec.prompt, "the uncertainty floor is load-bearing"


def test_review_prompt_names_the_failure_mode_vocabulary(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """Free-text reasons can't be counted, so the panel must pick from the controlled list."""
    cid = _candidate(padres_db)
    padres_db.execute(
        "INSERT INTO tweet_drafts (draft_id, candidate_id, text, status) VALUES (?,?,?,?)",
        ["d2", cid, "Tatis: 56.3% hard-hit.", "verified"],
    )

    spec = prompts.review_prompt(padres_db, "d2")
    for mode in ("arbitrary_endpoint", "causal_no_control", "scope_overreach", "voice_tell"):
        assert mode in spec.prompt


# ── Hypotheses must match the schema the validator enforces ───────────────────


def test_hypothesis_contract_matches_the_real_spec_schema(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    """A contract that doesn't match HypothesisSpec produces rejected work."""
    from padres_analytics.detect.hypothesis.spec import HypothesisSpec

    spec = prompts.hypothesis_prompt(padres_db, date(2026, 6, 20))
    blocks = [b for b in _json_blocks(spec.prompt) if "specs" in b]
    assert blocks, "the prompt shows the specs shape"

    example = blocks[0]["specs"][0]
    example.update(
        {
            "id": "x",
            "label": "X",
            "rationale": "r",
            "table": "t",
            "value_col": "v",
        }
    )
    HypothesisSpec.model_validate(example)  # raises if the contract drifted


def test_hypothesis_prompt_points_at_the_explored_ledger(
    padres_db: duckdb.DuckDBPyConnection,
) -> None:
    spec = prompts.hypothesis_prompt(padres_db, date(2026, 6, 20))
    assert "explored" in _flat(spec.prompt), "re-proposing dead metrics wastes the slot"
    assert "do not evaluate" in _flat(spec.prompt), "the scanner judges, not the proposer"
