"""Featurize reviewed work into (feature, label) pairs the priors learn from.

Every editorial decision the system already makes is a supervised label that was
being thrown away: a card queued or dismissed on the Board, a candidate rejected
in Studio, a draft that reached posted, a referee BLOCK. This module turns those
into feature keys so the engine can learn *which kinds of claim* survive review,
rather than re-deriving the same ranking every day.

Features are deliberately coarse. A key like ``metric_family:contact_quality``
accumulates evidence across many cards; ``metric:pctl_B_max_ev`` would see three
examples a season and never leave its prior.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from padres_analytics.detect.conjunction import metric_family

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Observation:
    """One reviewed item: its feature keys, whether it survived, and when."""

    features: tuple[str, ...]
    positive: bool
    observed_at: date
    source: str


def _loads(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if raw is not None else {}


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.today()


def _rarity_band(score: float | None) -> str | None:
    """Bin a candidate's interest score into a learnable band.

    Bands follow the verdict bands in :mod:`padres_analytics.detect.interest`,
    so a learned prior on a band means the same thing the scorer means.

    The key is deliberately ``interest_band``, not the old ``rarity_band``. The
    previous bins were 95+/90-95/85-90 — built around the old novelty score,
    which was structurally confined to roughly [0.85, 0.95]. On the interest
    scale, which genuinely spans [0, 1], every one of those bins would collapse:
    a 0.05 and a 0.89 both fell into "85-90". Reusing the key would silently
    average evidence gathered under two different meanings.
    """
    if score is None:
        return None
    if score >= 0.70:
        return "interest_band:strong"
    if score >= 0.50:
        return "interest_band:ok"
    if score >= 0.35:
        return "interest_band:thin"
    return "interest_band:boring"


def candidate_features(
    detector: str | None,
    facts_json: Any,
    provenance_json: Any,
    novelty: float | None,
    star_ids: frozenset[int],
) -> tuple[str, ...]:
    """Feature keys for a stat candidate.

    Args:
        detector: Detector that emitted it.
        facts_json: The payload (ChartDataset dump).
        provenance_json: Provenance rows (carry metric_id and lens).
        novelty: Novelty score, for the rarity band.
        star_ids: Marquee player ids, for the star tier.

    Returns:
        Deduplicated feature keys.
    """
    facts = _loads(facts_json)
    prov = _loads(provenance_json) or []
    inner = facts.get("facts", {}) if isinstance(facts, dict) else {}

    keys: list[str] = []
    if detector:
        keys.append(f"detector:{detector}")

    if isinstance(facts, dict):
        if facts.get("card_hint"):
            keys.append(f"card:{facts['card_hint']}")
        if facts.get("kind"):
            keys.append(f"payload:{facts['kind']}")

    for p in prov if isinstance(prov, list) else []:
        if not isinstance(p, dict):
            continue
        if p.get("lens"):
            keys.append(f"lens:{p['lens']}")
        if p.get("metric_id"):
            keys.append(f"metric_family:{metric_family(str(p['metric_id']))}")
        if p.get("origin"):
            keys.append(f"origin:{p['origin']}")

    band = _rarity_band(novelty)
    if band:
        keys.append(band)

    if isinstance(inner, dict):
        pid = inner.get("player_id") or inner.get("padre_player_id")
        if isinstance(pid, int):
            keys.append("star_tier:star" if pid in star_ids else "star_tier:regular")
        if inner.get("n_metrics"):
            keys.append("shape:conjunction")

    return tuple(dict.fromkeys(keys))


def _star_ids(conn: duckdb.DuckDBPyConnection) -> frozenset[int]:
    from padres_analytics.detect.registry import load_registry
    from padres_analytics.detect.scanner import _STAR_IDS_FALLBACK

    try:
        reg = load_registry()
        if reg.scan.star_ids:
            return frozenset(reg.scan.star_ids)
    except FileNotFoundError:
        pass
    return _STAR_IDS_FALLBACK


def _rows(conn: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    try:
        return conn.execute(sql).fetchall()
    except Exception as exc:
        logger.debug("learn.features: query unavailable (%s)", exc)
        return []


def collect(conn: duckdb.DuckDBPyConnection) -> list[Observation]:
    """Gather every labelled observation the system has accumulated.

    Four label sources, strongest signal first:

    - **Referee blocks** — an explicit, reasoned rejection, and the *reason*
      becomes a feature of its own.
    - **Studio rejections** — a human declining a candidate outright.
    - **Board verdicts** — queued (kept) versus dismissed.
    - **Drafts** — reaching approved/posted is the strongest positive available.

    Returns:
        All observations; an empty list when nothing has been reviewed yet.
    """
    stars = _star_ids(conn)
    out: list[Observation] = []

    # ── candidates: rejected in Studio, or promoted into a draft ──────────────
    for row in _rows(
        conn,
        """
        SELECT c.candidate_id, c.detector, c.facts_json, c.provenance_json,
               c.novelty_score, c.status, c.as_of,
               MAX(CASE WHEN d.status IN ('approved','posted') THEN 1 ELSE 0 END) AS promoted
        FROM stat_candidates c
        LEFT JOIN tweet_drafts d ON d.candidate_id = c.candidate_id
        GROUP BY c.candidate_id, c.detector, c.facts_json, c.provenance_json,
                 c.novelty_score, c.status, c.as_of
        """,
    ):
        _cid, detector, facts, prov, novelty, status, as_of, promoted = row
        feats = candidate_features(detector, facts, prov, novelty, stars)
        if not feats:
            continue
        if promoted:
            out.append(Observation(feats, True, _as_date(as_of), "draft_promoted"))
        elif status == "rejected":
            out.append(Observation(feats, False, _as_date(as_of), "studio_rejected"))

    # ── referee verdicts: the reason is itself a feature ──────────────────────
    for lens, mode, verdict, reviewed_at in _rows(
        conn,
        """
        SELECT lens, failure_mode, verdict, reviewed_at
        FROM review_verdicts
        WHERE verdict IN ('BLOCK', 'PASS')
        """,
    ):
        feats = [f"referee_lens:{lens}"]
        if mode:
            feats.append(f"failure_mode:{mode}")
        out.append(Observation(tuple(feats), verdict == "PASS", _as_date(reviewed_at), "referee"))

    # ── board cards: queued vs dismissed ──────────────────────────────────────
    for kind, angle_key, status, created_at in _rows(
        conn,
        "SELECT kind, angle_key, status, created_at FROM board_cards WHERE status != 'new'",
    ):
        feats = [f"card_kind:{kind}"]
        if angle_key:
            feats.append(f"angle:{angle_key}")
        out.append(
            Observation(tuple(feats), status == "queued", _as_date(created_at), "board_card")
        )

    # ── board leads: explored vs dismissed ────────────────────────────────────
    for kind, status, created_at in _rows(
        conn,
        "SELECT kind, status, created_at FROM board_leads WHERE status != 'new'",
    ):
        if not kind:
            continue
        out.append(
            Observation(
                (f"lead_kind:{kind}",),
                status == "exploring",
                _as_date(created_at),
                "board_lead",
            )
        )

    logger.info("learn: collected %d labelled observation(s)", len(out))
    return out
