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
