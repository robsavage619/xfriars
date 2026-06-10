"""Tweet draft models."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class TweetDraft(BaseModel):
    """A draft produced by the /padres-stat skill and written to inbox/."""

    candidate_id: str
    draft_kind: Literal["feed", "reply", "thread"] = "feed"
    thread_id: str | None = None
    thread_order: int | None = None
    reply_to_url: str | None = None
    text: Annotated[str, Field(max_length=280)]
    is_projection: bool = False
    interesting_judgment: str
    model: str


class ThreadDraft(BaseModel):
    """A multi-tweet thread proposal."""

    thread_id: str
    drafts: list[TweetDraft]
