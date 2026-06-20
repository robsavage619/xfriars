"""The Board — where Claude-generated cards and scout leads land for review.

The engine (Claude running the CLI / skills) writes here: ``add_card`` records a
rendered infographic + its reasoning, ``add_leads`` records the scout's threads.
The app only *reads* the board and flips statuses — no analytical logic lives in
the frontend. This is the surface where the generated images show up.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

    from padres_analytics.detect.angles import StoryAngle
    from padres_analytics.detect.leads import Lead

_CARD_STATUSES = ("new", "queued", "dismissed")
_LEAD_STATUSES = ("new", "exploring", "dismissed")


def _id(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:12]


def add_card(
    conn: duckdb.DuckDBPyConnection,
    angle: StoryAngle,
    image_path: str,
    *,
    kind: str,
    reconciled: bool,
    caption: str | None = None,
) -> str:
    """Record a rendered card on the board (idempotent on kind+subject+title).

    Args:
        conn: Write connection to padres.db.
        angle: The rendered story angle (carries the reasoning to display).
        image_path: Path to the rendered PNG.
        kind: "season_story" or "live_moment".
        reconciled: Whether the numbers were reconciled vs source (False for live).
        caption: Suggested post text; defaults to the headline.

    Returns:
        The card id.
    """
    cid = _id(kind, angle.subject, angle.title)
    now = datetime.now(UTC)
    conn.execute("DELETE FROM board_cards WHERE card_id = ?", [cid])
    conn.execute(
        "INSERT INTO board_cards (card_id, kind, angle_key, subject, title, headline, rank_note, "
        "confidence, reconciled, source, image_path, caption, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            cid,
            kind,
            angle.key,
            angle.subject,
            angle.title,
            angle.headline,
            angle.rank_note,
            angle.confidence,
            reconciled,
            angle.source,
            image_path,
            caption or angle.headline,
            "new",
            now,
        ],
    )
    return cid


def add_leads(conn: duckdb.DuckDBPyConnection, leads: list[Lead]) -> int:
    """Replace the open leads lane with the latest scout output.

    Dismissed leads are kept (so they don't reappear); the rest is refreshed.

    Returns:
        Number of leads written.
    """
    now = datetime.now(UTC)
    dismissed = {
        r[0]
        for r in conn.execute(
            "SELECT lead_id FROM board_leads WHERE status = 'dismissed'"
        ).fetchall()
    }
    conn.execute("DELETE FROM board_leads WHERE status != 'dismissed'")
    n = 0
    for lead in leads:
        lid = _id(lead.subject, lead.headline)
        if lid in dismissed:
            continue
        conn.execute(
            "INSERT INTO board_leads (lead_id, subject, kind, headline, explore, interest, "
            "status, created_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            [lid, lead.subject, lead.kind, lead.headline, lead.explore, lead.interest, "new", now],
        )
        n += 1
    return n


def list_cards(conn: duckdb.DuckDBPyConnection, status: str | None = None) -> list[dict[str, Any]]:
    """Cards on the board, newest first (optionally filtered by status)."""
    sql = (
        "SELECT card_id, kind, angle_key, subject, title, headline, rank_note, confidence, "
        "reconciled, source, image_path, caption, status, created_at FROM board_cards"
    )
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC"
    cols = [
        "card_id",
        "kind",
        "angle_key",
        "subject",
        "title",
        "headline",
        "rank_note",
        "confidence",
        "reconciled",
        "source",
        "image_path",
        "caption",
        "status",
        "created_at",
    ]
    return [dict(zip(cols, r, strict=True)) for r in conn.execute(sql, params).fetchall()]


def list_leads(conn: duckdb.DuckDBPyConnection, status: str | None = None) -> list[dict[str, Any]]:
    """Leads on the board, strongest first (optionally filtered by status)."""
    sql = (
        "SELECT lead_id, subject, kind, headline, explore, interest, status, created_at "
        "FROM board_leads"
    )
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY interest DESC"
    cols = ["lead_id", "subject", "kind", "headline", "explore", "interest", "status", "created_at"]
    return [dict(zip(cols, r, strict=True)) for r in conn.execute(sql, params).fetchall()]


def set_card_status(conn: duckdb.DuckDBPyConnection, card_id: str, status: str) -> bool:
    """Flip a card's status (queued/dismissed/new). Returns False on unknown status."""
    if status not in _CARD_STATUSES:
        return False
    conn.execute("UPDATE board_cards SET status = ? WHERE card_id = ?", [status, card_id])
    return True


def set_lead_status(conn: duckdb.DuckDBPyConnection, lead_id: str, status: str) -> bool:
    """Flip a lead's status (exploring/dismissed/new). Returns False on unknown status."""
    if status not in _LEAD_STATUSES:
        return False
    conn.execute("UPDATE board_leads SET status = ? WHERE lead_id = ?", [status, lead_id])
    return True
