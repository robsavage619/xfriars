"""Dry-run post writer (Phase 1). Live posting via tweepy added in Phase 3."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from padres_analytics.tweets.draft import StateTransitionError, transition

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)


class PostError(RuntimeError):
    """Raised when a post attempt hard-fails."""


class DuplicatePostError(PostError):
    """Raised when attempting to post an already-posted candidate."""


def post(
    conn: duckdb.DuckDBPyConnection,
    draft_id: str,
    out_dir: Path,
    dry_run: bool = True,
) -> Path:
    """Write a caption + copy the PNG card to out_dir.

    In Phase 1, this is always a dry run. Phase 3 adds the ``--live`` path.

    Args:
        conn: Write-mode padres.db connection.
        draft_id: The approved draft to post.
        out_dir: Destination directory for caption.txt + card PNG.
        dry_run: If False, requires Phase 3 tweepy integration (raises PostError).

    Returns:
        Path to the output directory for this post.

    Raises:
        PostError: If draft is not in 'approved' state, or live mode is invoked.
        DuplicatePostError: If the candidate was already posted.
    """
    row = conn.execute(
        """
        SELECT td.status, td.text, td.media_path, td.candidate_id,
               sc.candidate_id as sc_cid
        FROM tweet_drafts td
        LEFT JOIN stat_candidates sc ON sc.candidate_id = td.candidate_id
        WHERE td.draft_id = ?
        """,
        [draft_id],
    ).fetchone()

    if row is None:
        raise PostError(f"Draft {draft_id!r} not found.")

    status, text, media_path, candidate_id, _sc_cid = row

    if status != "approved":
        raise PostError(f"Draft {draft_id!r} is in status {status!r}; must be 'approved' to post.")

    # Duplicate guard — check if this candidate was already posted
    already = conn.execute(
        """
        SELECT draft_id FROM tweet_drafts
        WHERE candidate_id = ? AND status = 'posted'
        """,
        [candidate_id],
    ).fetchone()
    if already:
        raise DuplicatePostError(
            f"Candidate {candidate_id} was already posted (draft {already[0]}). "
            "Use --force to override (not yet implemented)."
        )

    if not dry_run:
        raise PostError(
            "Live posting requires Phase 3 (tweepy integration). Run without --live to dry-run."
        )

    # Write output
    out_dir.mkdir(parents=True, exist_ok=True)
    post_dir = out_dir / draft_id

    post_dir.mkdir(exist_ok=True)
    caption_path = post_dir / "caption.txt"
    caption_path.write_text(text, encoding="utf-8")

    if media_path and Path(media_path).exists():
        dest = post_dir / "card.png"
        shutil.copy2(media_path, dest)
        logger.info("[DRY RUN] Would post card: %s", dest)
    else:
        logger.warning("No card PNG found at %s", media_path)

    logger.info("[DRY RUN] Caption:\n%s", text)

    # Transition to posted
    try:
        transition(conn, draft_id, "posted")
    except StateTransitionError as exc:
        raise PostError(f"State transition failed: {exc}") from exc

    return post_dir
