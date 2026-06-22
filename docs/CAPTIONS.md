# Caption Playbook — Writing for How X Ranks (2026)

How xFriars writes a post so the X "For You" algorithm actually carries it — without
faking or begging for engagement, which gets demoted and cheapens the brand. This is
the third public-method doc, alongside [METHODOLOGY.md](METHODOLOGY.md) (how we
calculate) and [GLOSSARY.md](GLOSSARY.md) (what the words mean).

**Sourcing caveat:** these reflect current (2025–2026) multi-source reporting on the
X ranking, not the stale 2023 open-source weights. Sources agree on the *ordering*
of signals but disagree on exact multiples, and X changes the weights over time —
so treat specific numbers as directional, the ordering as durable.

---

## What the algorithm rewards (in order)

1. **Replies ≫ reposts ≫ bookmarks ≫ likes.** A reply is worth somewhere between
   ~13× and ~27× a like depending on the source; *the author replying back* is
   weighted higher still. **Write for replies and saves, not likes.**
2. **Early-engagement velocity (first 15–30 minutes)** is the single biggest reach
   signal. A post that gets a few engagements fast goes out-of-network; one that
   doesn't, dies. Post when your audience is live and reply to early engagers within
   minutes.
3. **Bookmarks** ("save-worthy") are a strong long-term signal — a falsifiable call
   people want to check back on earns them honestly.
4. **A visual.** A post with media out-reaches text-only; a data card qualifies.

## What it demotes (avoid)

- **External links in the post** — a 50–90% reach cut. Put the methodology/source
  link in your **first reply**, never the main post.
- **2+ hashtags** — trips spam filters and cuts reach. Use 0–1.
- **Engagement-bait** ("like if…", "follow for…", "tag a friend") — demoted, and
  off-brand.
- **Over-posting** (>10/day) and post-then-delete — lower authority.

## The xFriars post shape

A post is **two pieces**, and the engine drafts both (`src/padres_analytics/caption.py`):

- **Main post** — a line-1 verdict hook (the surprising number), then a genuine
  **debate question** that the audience can argue. Tight (< 280), no link, ≤ 1
  hashtag. Example:
  > Gavin Sheets has cooled hard — 176 points of on-base off his prior form. Just a
  > slump, or has the league adjusted to him?
- **Author's first reply** — the plain-language gloss *and* the methodology/source
  link. This keeps the link out of the post, adds accessibility, and gives you a
  reason to self-reply (a weighted signal). Example:
  > In plain terms: on-base percentage is how often he reaches base… (methodology
  > linked).

`caption_audit()` lints any draft for the demotions above; `pad daily` runs it and
surfaces the behavioral reach levers (post timing, reply-fast, link-in-reply) the
caption itself can't control.

## Why this fits the brand, not fights it

The highest-leverage signal — replies — is earned by asking an honest, defensible
question, which is exactly what a receipts-keeping, "is it real?" account does. The
algorithm rewards the same thing the brand is built on: starting a real argument and
being around to have it.
