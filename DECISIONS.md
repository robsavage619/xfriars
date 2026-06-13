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
