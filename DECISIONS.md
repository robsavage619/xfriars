# Architecture Decision Records

## ADR-001 — `env.example` not `.env.example`

**Date:** 2026-06-09
**Status:** Decided

**Context:** The execution plan specifies `.env.example` as the dotenv template file. The global filesystem deny-list (`~/.claude/security/filesystem-deny.txt`) blocks `**/.env.*`.

**Decision:** Use `env.example` (no leading dot) as the template filename.

**Consequences:** Same semantic purpose, equally conventional in Python projects. Any documentation or tooling that references `.env.example` should be updated to `env.example`. If the deny-list is ever narrowed to allow `.env.example`, the file can be renamed.

## ADR-002 — Visual + discovery overhaul: ChartDataset, generic scan engine, data-shape selector

**Date:** 2026-06-13
**Status:** Decided (phased build in progress; P0 landed)

**Context:** Cards were limited to tables/bars, and every "interesting stat" was a hand-coded detector with hard-coded thresholds that also fixed the visual. Goal: striking visuals chosen by data shape, fed by organic stat discovery — without breaking the accuracy regime. A simulated expert panel (ESPN, Codify, Fangraphs, MLB/Savant) pressure-tested the design before build.

**Decision:**
- **`ChartDataset`** — a role-typed payload (columns tagged dimension/measure/distribution/spatial/temporal/…) replaces table-only payloads. Its `model_dump(mode="json")` stays the digit-audit corpus, so accuracy guarantees carry over unchanged. Coexists with `TablePayload`/`SeriesPayload` via `payload_kind`.
- **Generic scan engine** — declarative metric registry (TOML, private override) + statistical lenses (extremeness/rank/record/drought/streak/pace) + a conjunction/multi-scope/named-anchor layer + narrative lenses. New stats = registry rows, not code.
- **Ranking = surprise × relevance × reliability**, not surprise alone (panel correction). Reliability = empirical-Bayes shrinkage + stabilization-n + FDR control + distribution-free (ECDF) tails; inputs prefer park/era-adjusted metrics (wRC+/OPS+/ERA-) because Petco biases raw rates. Relevance = star power × recency × live-story.
- **Data-shape selector** picks a *card type* (not just axis geometry), defaulting to the simplest legible unit (hero/lower-third); raw beeswarm/ridgeline/scatter demoted to opt-in. Canvas is portrait/square (1080×1350), not landscape.
- **Per-metric tiered framing** chosen in code; the LLM writes voice over the pre-verified framing string and may never upgrade a claim's scope.
- **Viz stack:** Observable Plot + D3, vendored for `file://` Playwright loading; same HTML→PNG pipeline and brand tokens.

**Consequences:** Adding a stat is a config edit, not a detector. Visual emerges from data, decoupled from discovery. Biggest risks: surprise→junk calibration (needs real-data tuning), statistical credibility (shrinkage/FDR/park-adjustment mandatory for a Petco team), and the conjunction layer being real engineering, not a placeholder. Legacy table/bar cards remain unchanged through every phase.

## ADR-003 — Long-form deep dives: in-repo authoring → GitHub Pages → Medium import

**Date:** 2026-06-21
**Status:** Decided

**Context:** We wanted to publish long-form Padres deep dives (FanGraphs-grade depth, casual-approachable) to Medium — a different format than the short X cards. Medium's publishing API is effectively dead: integration tokens have not been issued since 2025 and new integrations are disallowed, so direct programmatic posting was off the table (we hold no legacy token). Browser-automating the Medium editor is brittle to DOM changes.

**Decision:**
- **Authoring stays in-repo, chat-driven.** Articles live at `articles/<slug>/article.md` — YAML frontmatter (metadata + figures) + Markdown body, with `[[figure:id]]` shortcodes. Prose is written by hand; nothing in the pipeline calls an LLM (consistent with the no-backend-API-key posture).
- **Charts reuse the card engine.** Figures render through the same Jinja2 → Playwright → PNG pipeline as the X cards (Observable Plot/D3, brand tokens), baked to PNG so they survive Medium's importer (which re-hosts `<img>` and strips scripts). One visual language across cards and articles. New `longform/` module; `pad article new|render|render-all|list`.
- **Publishing = GitHub Pages → Medium "Import a story."** Rendered HTML lands in `docs/articles/<slug>/`; Pages serves it; Medium imports from the public URL and sets the canonical link back to us. Durable (no API), and we own SEO. Inline images use relative URLs (preview locally + Medium resolves against the fetch URL); canonical/OG tags use the absolute Pages URL. `.nojekyll` keeps Pages from munging the raw assets.
- **Voice:** `VOICE_LONGFORM.md` governs the long-form register (companion to the tweet-tuned `VOICE.md`).

**Consequences:** One manual click per publish (the Medium import), accepted in exchange for durability and canonical ownership. If Medium ever re-opens API tokens, a `publish.py` direct-to-draft path can be added without changing authoring or rendering. Requires GitHub Pages enabled on the repo (source: `main` / `docs`).

## ADR-004 — The referee: an agentic reasoning gate before publication

**Date:** 2026-07-18
**Status:** Decided (Phase R shipped; learning integration follows in Phase 2)

**Context:** The engine had four mechanical gates — digit audit, scope guard, coverage contract, availability filter. All four check whether the *numbers* are right. None checks whether the *argument* is any good. A card can pass every gate and still rest on an endpoint chosen after the fact, a survivorship-filtered population described as "the league", a cause asserted with no control, or a number that is rare and meaningless. That gap is the mechanism behind "the engine is going through the motions": it was structurally incapable of noticing a well-formed bad idea.

**Decision:**
- **A five-lens adversarial panel** (statistician, causal skeptic, coverage auditor, editor, voice) reviews a frozen `ReviewPacket` before a draft can move `verified → approved`. Each lens is briefed to *refute*, not approve; the briefs live in `review/lenses.py` so they version with the engine rather than drifting inside a skill file.
- **Any single BLOCK blocks.** Not a majority vote — one sound refutation suffices. Two REVISEs send it back; a lone REVISE is a prose note. **Uncertainty (confidence < 0.6) resolves to BLOCK for the causal and coverage lenses** and to the stated verdict elsewhere. The asymmetry is deliberate: a false "first ever" costs more than a missed post.
- **The referee may never compute.** It returns verdicts and critique only. A suggested rewrite may rephrase but may not introduce a figure, since referee prose never passed the digit audit. `assert_no_fact_mutation` and `assert_caption_digits_unchanged` enforce both structurally. If a referee believes a number is wrong, the only legal outcome is BLOCK — the fix is a detector change and a re-run.
- **Clearances are bound to content** via `packet_hash`. Re-rendering or re-captioning invalidates the clearance, so an approval can never ride along on content the panel never saw.
- **Rejections name a failure mode** from a controlled vocabulary. Free text cannot be counted and therefore cannot be learned from; these keys become features in the editorial prior (Phase 2) and summary stats in the hypothesis context pack (Phase 4c), closing the loop from critic back to discovery.
- **Agent-mediated, no API key** — same posture as the hypothesis loop. `pad review pack` emits the packet, Claude Code runs the panel as parallel subagents (the `xfriars-referee` skill), `pad review record` adjudicates and stores.

**Consequences:** Publishing is slower by one deliberate step, and cards will be blocked that would previously have shipped. On its first live run the panel blocked a conjunction card three ways and every finding was a genuine engine defect (a membership cut fitted to the subject, a conjunction joining a skill to a luck residual, and a claim scope asserting the Statcast era for a single-season comparison) — all since fixed. The main risk is rubber-stamping, so `pad review queue` reports block rate per lens and a lens that never blocks is treated as a defect. The secondary risk is over-blocking starving the feed; the Board shows blocked cards with reasons and Rob can override, and overrides are recorded as their own label so the priors learn from the override rather than only from the block.

**Alternative rejected:** a single general-purpose reviewer. Five identical reviewers converge on the same reading; five different briefs catch failures the others are blind to. The first live run bore this out — the three blocks came from three different lenses and none of them would have been caught by the other two.
