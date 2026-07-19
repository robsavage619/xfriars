"""Prompt assembly — the Studio's handoff to Claude.

The app never calls a model. It assembles a prompt complete enough that a human
can paste it into Claude, get back a structured deliverable, and paste that
result into the Studio, where the same deterministic gates that have always
guarded the pipeline take over.

A prompt is only as good as what it forecloses. Each one carries:

1. the mission and the one rule Claude may not break (never compute a number),
2. the dossier — every engine-computed fact the deliverable may cite, verbatim,
3. the constraints — coverage window, scope tier, banned voice tells,
4. the output contract — exact JSON schema, a filled example, and an honest
   exit for "there is no story here",
5. the return path.

The digit audit downstream is unforgiving: any number in a caption that is not
in ``facts_json`` aborts the draft. So the dossier is not a summary — it is the
complete set of numbers the writer is allowed to use, and the prompt says so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

MODEL_TAG = "claude-studio-handoff"

# ── Shared blocks ─────────────────────────────────────────────────────────────

_MISSION = """\
You are writing for @xFriars, a San Diego Padres analytics account. The account's
reputation rests on being right: a wrong number costs more than a missed post.

THE ONE RULE YOU MAY NOT BREAK
You never compute, estimate, derive, or round a statistic. Every number you write
must already appear in the DOSSIER below, exactly as written there. If a number
you want is not in the dossier, you cannot use it — rewrite the claim without it,
or return the honest-exit response. A downstream digit audit compares every digit
token in your output against the source data and rejects the whole deliverable on
a single mismatch, so a guessed number does not get published; it gets thrown away
along with the rest of your work."""

_VOICE = """\
VOICE
Sharp, credible baseball analyst who happens to be a Padres fan. The number is the
news — lead with it. Declarative sentences, active voice. An opinion only when the
finding earns one: a 52-year streak earns a take, a .500 record on a Tuesday does not.

Banned — any one of these means a rewrite:
- Hype scaffolding: "let's dive in", "buckle up", "here's why that matters"
- The pivot tic: "it's not just X — it's Y"
- Rhetorical openers: "Did you know?", "What do you get when..."
- Wrap-up filler: "simply put", "bottom line", "at the end of the day"
- Manufactured awe: "historic", "remarkable", "incredible", "stunning" — the number
  carries the weight, adjectives dilute it
- Emoji/hashtag stuffing (one emoji maximum, and only if it earns its place;
  #Padres is redundant on a Padres account)
- The same sentence structure three posts running — that reads as a bot

Read it aloud before you commit. If it sounds like a brand account, rewrite it. If
it sounds like a knowledgeable fan on baseball Twitter, ship it."""

_COVERAGE = """\
COVERAGE WINDOWS — these bound every superlative you write
| Source                  | Window      |
|-------------------------|-------------|
| MLB Stats API           | 2010-present |
| Statcast/Baseball Savant| 2015-present |
| bWAR (Baseball Reference)| 1871-present |
| Retrosheet transactions | 1880-2009    |

A Statcast-derived claim is "since 2015", full stop — never "ever", never "all-time",
never "in franchise history". "First Padre ever" requires bWAR backing. A scope guard
checks the caption against the engine's own framing string and rejects any claim
broader than the data supports, so write inside the window rather than around it."""


def _honest_exit(kind: str) -> str:
    return f"""\
THE HONEST EXIT
If the evidence does not support a publishable {kind} — the sample is thin, the
effect is an artifact of the endpoints, the finding is real but trivial, or the
numbers you would need are not in the dossier — return exactly:

{{"verdict": "no_story", "why": "<one or two sentences on what failed>"}}

This is a good outcome and it is recorded as one. An account that publishes only
what survives is the entire product. Do not manufacture a story to fill the slot."""


_RETURN_PATH = """\
RETURNING YOUR WORK
Reply with the JSON object and nothing else — no prose before it, no code fence,
no commentary after. Paste it into Studio → Drafts → "Paste result", which runs
the validation, digit audit, scope guard, render, and verification, and reports
which gate passed or failed."""


def _fence(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


@dataclass
class PromptSpec:
    """An assembled prompt plus what it was built from."""

    kind: str
    subject: str
    target_id: str
    prompt: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the prompt endpoint."""
        return {
            "kind": self.kind,
            "subject": self.subject,
            "target_id": self.target_id,
            "prompt": self.prompt,
        }


# ── Dossier assembly ──────────────────────────────────────────────────────────


def _readable_subject(
    conn: duckdb.DuckDBPyConnection, subject: str | None, facts: dict[str, Any]
) -> str:
    """Resolve an engine subject key to a player name for the prompt's dossier."""
    from padres_analytics.daily import _readable_subject as resolve

    return resolve(conn, subject, facts) or (subject or "")


def _candidate_row(conn: duckdb.DuckDBPyConnection, candidate_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT candidate_id, detector, subject, CAST(as_of AS VARCHAR), novelty_score,
               facts_json, provenance_json, claim_scope, coverage_window, payload_kind
        FROM stat_candidates
        WHERE candidate_id = ?
        """,
        [candidate_id],
    ).fetchone()
    if row is None:
        raise LookupError(f"candidate {candidate_id!r} not found")
    facts = json.loads(row[5]) if isinstance(row[5], str) else row[5]
    return {
        "candidate_id": row[0],
        "detector": row[1],
        # Machine subject keys (SDP|CONJUNCTION|665487|2026) tell a writer
        # nothing about who the post is about.
        "subject": _readable_subject(conn, row[2], facts),
        "as_of": row[3],
        "novelty_score": row[4],
        "facts": facts,
        "provenance": json.loads(row[6]) if isinstance(row[6], str) else (row[6] or {}),
        "claim_scope": row[7],
        "coverage_window": row[8],
        "payload_kind": row[9],
    }


def _glossary_notes(facts: dict[str, Any]) -> str:
    """Plain-language readings for the metrics in play, so the caption can gloss them."""
    from padres_analytics import glossary

    seen: list[str] = []
    for key in ("metric", "metric_key", "stat", "stat_key"):
        value = facts.get(key)
        if isinstance(value, str):
            explained = glossary.explain(value)
            if explained:
                seen.append(f"- {value}: {explained}")
    for row in facts.get("rows", [])[:6] if isinstance(facts.get("rows"), list) else []:
        if isinstance(row, dict):
            label = row.get("metric") or row.get("label")
            if isinstance(label, str):
                explained = glossary.explain(label)
                if explained and not any(label in s for s in seen):
                    seen.append(f"- {label}: {explained}")
    if not seen:
        return ""
    return "\nPLAIN-LANGUAGE READINGS (use these to gloss jargon, don't quote them verbatim)\n" + (
        "\n".join(seen)
    )


# ── Builders ──────────────────────────────────────────────────────────────────


def draft_prompt(conn: duckdb.DuckDBPyConnection, candidate_id: str) -> PromptSpec:
    """Prompt to turn one verified candidate into a postable caption."""
    cand = _candidate_row(conn, candidate_id)
    facts = cand["facts"]

    contract = {
        "candidate_id": cand["candidate_id"],
        "draft_kind": "feed",
        "text": "<the post, 280 characters max, every number from the dossier>",
        "is_projection": False,
        "interesting_judgment": "<one sentence: why this is worth a reader's attention>",
        "model": MODEL_TAG,
    }

    prompt = f"""\
{_MISSION}

TASK
Write the post for one verified finding. The card is already rendered from this
same data; your caption sits above it, so do not narrate the chart — say what it
means and why a Padres fan should care.

DOSSIER — the complete set of numbers you may use
Subject: {cand["subject"]}
Detector: {cand["detector"]}   As of: {cand["as_of"]}
Claim scope: {cand["claim_scope"]}
Coverage window: {cand["coverage_window"]}

facts_json (verbatim — every digit you write must appear here):
{_fence(facts)}
{_glossary_notes(facts)}

{_COVERAGE}

{_VOICE}

OUTPUT CONTRACT
Return exactly this JSON shape:
{_fence(contract)}

Field notes:
- "text": the post itself. 280 characters hard maximum, counted including spaces.
- "is_projection": true only if the claim is about what WILL happen, not what has.
- "interesting_judgment": your honest read on why this clears the bar. If you find
  yourself straining to write it, that is the finding telling you to take the exit.

{_honest_exit("post")}

{_RETURN_PATH}"""

    return PromptSpec("draft", cand["subject"], candidate_id, prompt)


def dive_prompt(conn: duckdb.DuckDBPyConnection, lead_id: str) -> PromptSpec:
    """Prompt to investigate a scouted lead and, if it survives, write the post.

    A lead is a starting point, never a story. The dive is where a flagged number
    either earns a card or dies, so this prompt asks for the investigation first
    and the caption only as its consequence.
    """
    row = conn.execute(
        "SELECT lead_id, subject, kind, headline, explore, interest, status "
        "FROM board_leads WHERE lead_id = ?",
        [lead_id],
    ).fetchone()
    if row is None:
        raise LookupError(f"lead {lead_id!r} not found")
    lead = {
        "lead_id": row[0],
        "subject": row[1],
        "kind": row[2],
        "headline": row[3],
        "explore": row[4],
        "interest": row[5],
        "status": row[6],
    }

    candidate_block = ""
    linked = conn.execute(
        """
        SELECT candidate_id FROM stat_candidates
        WHERE subject = ? AND status = 'new'
        ORDER BY ingested_at DESC LIMIT 1
        """,
        [lead["subject"]],
    ).fetchone()
    if linked:
        try:
            cand = _candidate_row(conn, linked[0])
            candidate_block = f"""

THE ENGINE'S CANDIDATE FOR THIS SUBJECT
candidate_id: {cand["candidate_id"]}  (use this exact id if you produce a draft)
detector: {cand["detector"]}   claim scope: {cand["claim_scope"]}
facts_json:
{_fence(cand["facts"])}
{_glossary_notes(cand["facts"])}"""
        except LookupError:
            candidate_block = ""

    contract = {
        "candidate_id": "<the candidate_id above>",
        "draft_kind": "feed",
        "text": "<the post, 280 characters max>",
        "is_projection": False,
        "interesting_judgment": "<what the dive established that the lead alone did not>",
        "model": MODEL_TAG,
    }

    prompt = f"""\
{_MISSION}

TASK — investigate before you write
This is a LEAD: a number that looked anomalous for this subject. It is a starting
point, not a finding. Most leads should die here, and killing one is a result.

The lead:
  Subject:  {lead["subject"]}
  Kind:     {lead["kind"]}
  Flag:     {lead["headline"]}
  Interest: {lead["interest"]}

Work the lead against the dossier below. The questions that kill most leads:
- Sample: is the window long enough to mean anything, or is this 30 plate
  appearances dressed up as a trend?
- Endpoints: does the effect survive moving the start and end dates, or was it
  manufactured by where the window was cut?
- Denominator: is the rate computed over the population the claim implies?
- Baseline: extreme compared to what — his own career, the league, the position?
  An outlier against the wrong baseline is not an outlier.
- Confounds: is there a mundane explanation (park, opponent quality, injury,
  role change) that the number alone would hide?
- So what: if it is real, does it change how a fan sees this player? A true and
  boring fact is still not a post.

The engine's own guards — ECDF extremeness, empirical-Bayes shrinkage toward the
population mean, Benjamini-Hochberg correction across the day's detectors — have
already run. Your job is the reasoning they cannot do.{candidate_block}

{_COVERAGE}

{_VOICE}

OUTPUT CONTRACT
If the lead survives, return exactly:
{_fence(contract)}

The "interesting_judgment" field is where the dive pays off: say what you
established, not what the lead claimed. If you could not link a candidate_id from
the block above, take the honest exit instead of inventing one — a draft without a
real candidate_id cannot be verified and will be rejected.

{_honest_exit("story")}

{_RETURN_PATH}"""

    return PromptSpec("dive", lead["subject"], lead_id, prompt)


def review_prompt(conn: duckdb.DuckDBPyConnection, draft_id: str) -> PromptSpec:
    """Prompt for the referee panel — the reasoning gate before approval."""
    from padres_analytics.review.packet import build_packet

    packet = build_packet(conn, draft_id=draft_id)
    packet_json = packet.model_dump(mode="json")

    contract = {
        "packet_hash": packet.packet_hash(),
        "verdicts": [
            {
                "lens": "statistician",
                "verdict": "PASS",
                "failure_mode": None,
                "evidence": "<why, citing the packet — not a restatement of the claim>",
                "confidence": 0.9,
                "suggested_caption": None,
            }
        ],
    }

    prompt = f"""\
You are the referee panel for @xFriars, a Padres analytics account.

The mechanical gates have already run and passed: every digit in this caption
exists in the source data, the claim sits inside its coverage window, and the
numbers reconcile against the database. Those gates catch a WRONG NUMBER. Nothing
upstream catches a WRONG ARGUMENT — an arbitrary endpoint, a confounded
comparison, a survivorship-filtered population presented as "the league", a causal
claim with no control, or a number that is extreme and means nothing. That is what
you are for.

THE HARD INVARIANT
You return verdicts and critique. You never return numbers. You may block, you may
rewrite caption prose, you may never touch the underlying facts. If you believe a
number is wrong, the verdict is BLOCK — do not "fix" it.

THE PACKET
{_fence(packet_json)}

Note the "not_checked" field. It lists what the engine did not verify. A referee
can only catch what the packet exposes; treat that list as where to look hardest.

THE FIVE LENSES — judge each independently
- statistician: sample size, endpoints, denominator, multiplicity, whether a
  conjunction is really independent.
  Failure modes: arbitrary_endpoint, cherry_picked_window, wrong_denominator,
  survivorship_population, sample_too_small, multiplicity_unaccounted,
  correlated_conjunction
- causal: does the claim imply a cause it has not earned? Is there a control?
  Failure modes: causal_no_control, confounded_comparison
- coverage: does the claim stay inside the data's window and population?
  Failure modes: scope_overreach, stale_source, coverage_mismatch,
  padres_only_as_league
- editor: is it worth a reader's attention, or true-but-empty?
  Failure modes: trivial, tautological, filter_artifact
- voice: does it read as a knowledgeable fan, or as a bot?
  Failure modes: voice_tell, register_mismatch, jargon_ungloss

CONFIDENCE IS LOAD-BEARING
Score each verdict 0.0-1.0 honestly. Below 0.6 counts as uncertain, and an
uncertain PASS from the causal or coverage lens is converted to a BLOCK — those
failures ship a false claim, so being unsure is itself disqualifying there. An
unsure statistician or editor is not enough to kill a card.

OUTPUT CONTRACT
Return exactly this shape, with one entry per lens (five total):
{_fence(contract)}

- "verdict": "PASS" | "REVISE" | "BLOCK"
- "failure_mode": null on PASS; otherwise one key from that lens's list above
- "evidence": one or two sentences citing the packet. Not a restatement of the claim.
- "suggested_caption": prose replacement for a REVISE, or null. Prose only — if you
  want a different NUMBER, block instead.
- "packet_hash": copy it verbatim. It ties your clearance to this exact content, so
  a later edit to the caption invalidates the review rather than riding along on it.

{_RETURN_PATH}"""

    return PromptSpec("review", packet.claim or draft_id, draft_id, prompt)


def hypothesis_prompt(conn: duckdb.DuckDBPyConnection, as_of: date | None = None) -> PromptSpec:
    """Prompt to propose new metric hypotheses for the scanner to test."""
    from padres_analytics.detect.hypothesis.context import build_context_pack

    pack = build_context_pack(conn, as_of or date.today())

    contract = {
        "specs": [
            {
                "id": "<short_snake_case_id>",
                "label": "<human-readable metric name, e.g. Chase Rate vs Breaking>",
                "rationale": "<why this could surface something the registry misses>",
                "table": "<one of the tables listed in the pack>",
                "value_col": "<column in that table>",
                "derived_expr": None,
                "filter_sql": "",
                "metric_type": "rate",
                "direction": "higher",
                "value_format": ".3f",
                "unit": "",
                "coverage": "current MLB season",
            }
        ]
    }

    prompt = f"""\
You are proposing hypotheses for the @xFriars discovery engine.

The engine scans a registry of metrics for Padres outliers. That registry encodes
what someone already thought to look for, which is exactly its blind spot. Your job
is to propose metrics worth testing that are not in it yet.

You do not evaluate anything. Every spec you propose is run through the same
scanner as the built-in metrics — ECDF extremeness, empirical-Bayes shrinkage,
Benjamini-Hochberg correction — and most will surface nothing. That is expected and
costs nothing. A proposal is a question, not a claim.

THE CONTEXT PACK — what has been tried, what is available, what failed
{_fence(pack)}

Read the explored-space ledger before proposing. Re-proposing a metric that has
already been scanned and came back empty wastes the slot; the interesting space is
adjacent to what worked, or orthogonal to what has been tried at all.

WHAT MAKES A GOOD PROPOSAL
- It asks something a fan would actually wonder about, not just a column that exists.
- It has a plausible mechanism — a reason the number would move for a real baseball
  reason, not a statistical accident.
- Its denominator is defensible at the sample sizes the pack reports.
- It is not a rewording of a metric already in the registry.

OUTPUT CONTRACT
Return exactly this shape, with 3-8 specs:
{_fence(contract)}

- "table" and "value_col" must reference what the pack lists as available. A spec
  naming a column that does not exist is rejected by the validator before it runs.
- "derived_expr": a SQL expression over that table when the metric is a ratio or
  difference rather than a bare column; null otherwise.
- "metric_type": "rate" | "counting" | "differential" | "ordinal".
- "direction": "higher" if an extreme HIGH value is the interesting case, else "lower".
- "filter_sql": a WHERE fragment to restrict the population (e.g. a PA floor), or "".

{_RETURN_PATH}"""

    return PromptSpec("hypothesis", "discovery", "hypothesis", prompt)
