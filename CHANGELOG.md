# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — Live path, story infographics, spatial cards, scout → deep dive → Board

### Added — In-game (live) path
- **`live.py` + `pad live now`** — GUMBO feed client: pulls the current Padres game from the MLB live feed and emits pitch-level reads (unofficial, read-only).
- **`ingest/live_poll.py` + `ingest/live_serve.py`** — live poller and `pad live serve` daemon; `pad live watch` streams updates as they land.
- **`live_ask.py` + `pad live ask`** — plain-language question intent over the in-game feed (matchup / RISP / count-state reads).
- **`live_moments.py`** — gated, ranked in-game moment detector (not a fixed template): only moments that clear the significance gate surface a card.
- **`live_card.py` + `pad live card`** — analytical live pitcher card: CSW% hero, whiff-colored pitch mix, velo trend, player headshot; team-correct and legible to casual fans.
- `storage/schemas.py` — `live_pitches` table; schema bumped through v10.

### Added — Story infographics (multi-module narrative cards)
- **`detect/story.py`** + **`render/story_infographic.py`** + **`render/templates/card_story.html.j2`** — composed `StoryCard` image type: hero hook + player percentile panels + narrative, separating skill from luck across several lenses with every claim significance-gated.
- **`pad story` / `pad discover`** — story-discovery engine: ranked, gated, audited infographics. Copy aligned to `VOICE.md` (FanGraphs-adjacent, fan lean).
- **Number reconciliation** — rendered numbers are verified against source, not just displayed.

### Added — Spatial visual library
- Panel-spec'd geometry kit driving spatial cards: `pad spray` (HR spray), `pad arsenal`, `pad zone`, `pad release` (release-point / arm-slot deception), rolling-xwOBA, swing/take run-value, and bat-speed (bat-tracking) distribution cards.
- Studio spatial-card picker (full-stack) + detector → spatial-card auto-mapping; spatial cards wired into the draft/render/verify pipeline.

### Added — Scout → deep dive → Board
- **`pad scout`** — shallow leads scout: surfaces flags (starting points for a deep dive), never stories. The lead is never posted on its own.
- **`board.py`** — the Board: store + API where Claude-generated cards and scout leads land; Studio is the editorial-light gallery over it.
- **Surprise + novelty ranking** — candidates rank by what is unusual *for the subject*, not by raw magnitude.

### Added — Data-coverage preflight
- **`storage/coverage.py` + `pad coverage`** — the engine knows its own stat coverage (season span, granularity, freshness, player coverage) before it detects, scouts, or tells a story; `can_support()` gates analysis on verified completeness.

### Changed
- **Player availability gate** — only available players are featured; injured / out-for-season bats are filtered on roster status.
- CI: `gitleaks` runs on pre-push as well as pre-commit; `.github` CI / CODEOWNERS / dependabot config removed.

---

## [Unreleased] — P3: Conjunction + scope framing + milestone proximity

### Added
- **`detect/conjunction.py`** — conjunction layer with three capabilities:
  - `evaluate_franchise_scope()` — queries Statcast history joined to bWAR to select the strongest provable franchise-scope framing tier: `franchise_record` > `first_since` > `statcast_era_best` > `season_best`.
  - Named-anchor resolver — embedded inside scope evaluator; finds the most recent prior SDP player who held the feat, so framing renders "first Padre since [Name] ([Year])."
  - `find_conjunctions()` — groups `_Hit` objects by player; players with 2+ distinct metrics firing yield a `ConjunctionGroup` whose `combined_rarity` is the geometric mean of individual rarities.
- **`detect/lenses.py`** — `milestone_proximity_lens`: fires when a player is within 10% below a threshold milestone (e.g. barrel rate approaching 20% or 25%); rarity 0.80–0.95 scaled linearly by distance remaining.
- **`detect/registry.py`** — `milestones: list[float]` field on `MetricSpec`; `milestone_proximity` lens dispatches over it.
- **`detect/scanner.py`** — three additions after BH correction: milestone_proximity dispatch in `_run_metric`; scope-strengthening loop (franchise_record / first_since hits get +0.05 rarity boost); conjunction logging for multi-metric players.
- **`tweets/verify.py`** — `check_scope_upgrade(framing, caption) -> list[str]`: detects scope upgrade by comparing engine-chosen framing tier keywords against forbidden caption phrases. Prevents "Statcast era" → "ever," "franchise record" → "MLB history," etc.
- **`tweets/draft.py`** — step 3b in `ingest_draft`: calls `check_scope_upgrade` after digit audit; raises `DraftIngestError` on any violation.
- **`examples/anchors.example.toml`** — milestone thresholds, Padres legend baselines (Gwynn, Hoffman, Gonzalez), and "more X than Y" anchor bank.
- **`examples/metrics.example.toml`** — `milestone_proximity` lens + `milestones` added for `barrel_rate` ([20.0, 25.0]), `sprint_speed` ([30.0, 29.0]), `xwoba_gap` ([0.050]).
- 15 new tests in `tests/test_conjunction.py` covering scope tiers, graceful fallback, conjunction grouping, milestone proximity, and scope-upgrade detection; 118 total passing.

### Changed
- **`tweets/verify.py`** `verify_path_b` — `sql` key is no longer required for `payload_kind == "dataset"` provenance entries. Dataset candidates emit structural provenance (table + metric_id + lens + as_of); only legacy `TablePayload` candidates still require raw SQL.

---

## [Unreleased] — P2: Generic scan engine

### Added
- **`detect/sql.py`** — shared DuckDB helpers extracted from `statcast.py`: `fmt_name`, `ordinal`, `resolve_table`, `max_year`, `padre_ids`, `padre_ids_latest`.
- **`detect/registry.py`** — TOML metric registry loader: `MetricSpec`, `PopulationSpec`, `ScanConfig`; private override falls back to `examples/metrics.example.toml`.
- **`detect/lenses.py`** — four statistical lenses: `extremeness_lens` (ECDF + empirical-Bayes shrinkage), `rank_lens` (top-quartile cap), `pace_lens` (milestone countdown from games played), `bh_surviving_indices` (Benjamini-Hochberg FDR correction).
- **`detect/scanner.py`** — `GenericScanner` (`name="scan"`): iterates registry metrics, dispatches lenses, applies BH correction, builds `ChartDataset` candidates ranked by `novelty_score`.
- **`examples/metrics.example.toml`** — three example metrics: `barrel_rate`, `sprint_speed`, `xwoba_gap`.
- **`pad scan run`** CLI command — runs `GenericScanner` against live DuckDB; emits top-K candidates to `stat_candidates`.
- Tests: `tests/test_lenses.py` (12 tests), `tests/test_registry.py` (7 tests); 103 total passing post-P2.

### Changed
- `detect/statcast.py` — local `_fmt_name`, `_ordinal`, `_tbl`, `_max_year`, `_padre_ids` removed; imports from `detect/sql.py` instead.

---

## [Unreleased] — P0+P1: ChartDataset + hero card + percentile slider

### Added
- **`detect/candidates.py`** — `Column`, `Mark`, `ChartDataset` (role-typed dataset payload), `SemanticRole`, `audit_corpus()`. `TablePayload` / `SeriesPayload` unchanged.
- **`detect/scoring.py`** — `select_card(dataset)` data-shape selector: rank lens → bar/table, extremeness → hero card.
- **`render/templates/hero_card.html.j2`** — hero / lower-third card: huge number + framing line + provenance chip. Portrait canvas 540×675 CSS px (1080×1350 @2×).
- **`render/templates/slider_card.html.j2`** — Savant-style percentile slider (red→neutral→blue) for one-player multi-metric profiles.
- `render/tokens.py` — portrait canvas dimensions (`CANVAS_W=540`, `CANVAS_H=675`).
- `tests/test_chart_dataset.py` — digit-audit parity tests for `ChartDataset` vs `TablePayload`.

---

## [Unreleased] — Phase 3: Statcast + brand polish

### Added
- **`detect/statcast.py`** — four Statcast detectors:
  - `statcast_profile` — Statcast percentile bar chart per Padre hitter (xwOBA, exit velo, barrel %, hard-hit %, sprint speed, K-control); bars normalized to the player's own max metric, glow highlight on the best tool.
  - `xstats_unlucky` — MLB xwOBA gap leaderboard (est_woba − woba); positive gap = underperforming expected production. Requires ≥100 PA; any Padre in the top 25 fires the detector.
  - `sprint_speed` — MLB sprint speed leaderboard from Baseball Savant; any Padre in the top 10 fires.
  - `barrel_rate` — MLB barrel-rate leaderboard from Baseball Savant; any Padre in the top 10 fires.
- **`ingest/statcast.py`** + **`pad ingest statcast`** CLI — pulls four Statcast tables from Baseball Savant via `pybaseball` into `padres.db` (`main.` schema): `statcast_batter_percentile_ranks`, `statcast_batting_expected`, `statcast_sprint_speed`, `statcast_batter_exitvelo_barrels`. Each table's rows for the target season are deleted and replaced. Per-table `ingest_runs` records with `rows_written`.
- **`render/mlb_assets.py`** — lazy downloader/cache for MLB team SVG logos and player PNG headshots, stored under `data/mlb_assets/` (gitignored). Used by future card types that need team or player imagery.
- **`render/static/d3.v7.min.js`** — D3.js v7 bundled for `file://` Playwright renders. Eliminates the network dependency during card generation.
- **`render/templates/bar_card.html.j2`** — D3-powered bar chart card template: gradient fills (`linearGradient`), `feGaussianBlur` glow on the highlight row, hairline row rules, Big Shoulders Display + Space Grotesk fonts, `#chart-ready` sentinel for Playwright.
- **`studio/src/components/MlbAssets.tsx`** — `PlayerPhoto` (circular crop, face-anchored) and `TeamLogo` React components backed by the `mlb_assets` cache endpoints.
- `_tbl(conn, name)` helper in `detect/statcast.py` — prefers `main.{name}` (fresh ingest) over `hist.{name}` (trades.db READ-ONLY attach); detectors auto-upgrade to the most current data without code changes.

### Changed
- **Font stack** (canonical v2): `Big Shoulders Display` variable TTF (weight 900, uppercase) for all display/title text; `Space Grotesk` variable TTF for body/labels/data. Replaces Bebas Neue + DM Sans.
  - `render/tokens.py` — `FONT_DISPLAY` and `FONT_BODY` token vars updated; TTF paths updated to new filenames.
  - `render/templates/table_card.html.j2` — `@font-face` and CSS updated; nav-logo weight fixed to 900.
  - `studio/src/index.css` — `.nav-logo` weight 900, `text-transform: uppercase`, `letter-spacing: 0.12em`.
  - `studio/index.html` — Google Fonts preconnect updated for new families.
  - `studio/src/api.ts`, `Candidates.tsx`, `Explorer.tsx` — minor Studio UI polish aligned to brand v2.
- `cli.py` — `pad ingest statcast` added to the `ingest` sub-app group; `detect/statcast` imported to register the four new detectors at CLI load time.
- `storage/schemas.py` — `SCHEMA_VERSION` bumped 2 → 3; four new `statcast_*` tables with `PRIMARY KEY (player_id, year)` and year-indexed secondary indexes.
- `app/api.py` — Studio API surface updated for new detector registry entries.
- `tests/fixtures/table_card_reference.png` — snapshot regenerated after font swap (`PADRES_UPDATE_SNAPSHOTS=1`).

### Fixed
- `statcast_profile` title bug: `.split()[-1]` yielded "JR." for Fernando Tatis Jr. Fix: `" ".join(name_parts[1:])` → "TATIS JR."
- `ingest_statcast` context-manager error: `record_run` is a `@contextmanager`, not a start/complete function pair. Restructured to `with record_run(...) as run:` per table; individual failures are logged and skipped, not propagated.
- `itertuples()` attribute error on `'last_name, first_name'` column: renamed to `player_name` before iteration in all three affected ingest functions.

---

## [Unreleased] — Phase 2: crossjoin detectors + milestones

### Added
- **Phase 3 detectors** (crossjoin + milestones) — `detect/crossjoin.py`:
  - `career_milestones` — upcoming HR/H/K round-number milestones (within 10).
  - `hit_streak` — active hit streaks ≥ 7 games.
  - `streak_vs_team` — historical streaks when a specific opponent is next up.
  - `opponent_crossjoin` — Padre vs. upcoming opponent leaderboard matchups.
- `detect/base.py` extended with `register()` / `get_detector()` global registry; detectors self-register at import time.
- `cli.py` — `pad detect run <detector>` and `pad detect list` commands.

### Changed
- `storage/schemas.py` — `stat_candidates` table added `novelty_components JSON` column.
- `render/cards.py` — table card renderer updated for column highlight on Padre rows.

---

## [Unreleased] — Phase 1: data spine + first detectors + renderer

### Added
- **`ingest/mlb_api.py`** (`MlbStatsClient`) — MLB Stats API adapter: schedule, box scores, player game logs, season stats, leaderboards. Respects the `SDP` team filter; writes to `padres.db`.
- **`ingest/runs.py`** — `record_run()` context manager: marks `complete=True` on clean exit, `complete=False` on exception. Detectors gate on `complete` before reading.
- **`detect/leaderboards.py`** — `pad ingest stats` + three leaderboard detectors: `hr_leaders`, `avg_leaders`, `era_leaders`. Padres-anchored: the Padre row is always present even if outside the display window.
- **`detect/on_this_day.py`** — `on_this_day_results` and `on_this_day_transactions`; SQL filtered to `EXTRACT(YEAR) >= 1990` for results, `EXTRACT(YEAR) >= 2010` for transactions (MLB Stats API coverage).
- **`render/cards.py`** + **`render/templates/table_card.html.j2`** — Playwright-based PNG renderer; Jinja2 template with xFriars brand, D3-optional bar rows, `#chart-ready` sentinel.
- **`render/tokens.py`** — design token helpers injected into every template render.
- **Studio** (`studio/`) — React + TypeScript + Vite + Tailwind app for candidate review, draft queue, and card preview; `pad studio` launches it.
- `storage/schemas.py` — versioned DuckDB DDL, `SCHEMA_VERSION = 1` → `2`. Tables: `schema_version`, `ingest_runs`, `stat_candidates`, `tweet_drafts`, `post_metrics`, `predictions`, `corrections`, `game_schedule`, `game_box`, `player_game_logs`, `player_season_stats`, `mlb_leaders`.
- `cli.py` — `pad` Typer CLI: `ingest`, `detect`, `draft`, `queue`, `post`, `studio`, `ammo` sub-apps.
- `tweets/ammo.py` — "ammo file" generator: serializes approved candidates + drafts to a structured JSON payload for the `/padres-stat` Claude skill.
- Pre-commit hooks: `ruff --fix`, `pyright --basic`, `pytest -x -q` (fast suite only).
- `VOICE.md` — editorial style guide and banned-tells list.

---

## [0.0.0] — Phase 0: security scaffold (2026-05-15)

Pre-code security and scaffold. Not tracked in detail here.

- `.gitignore` — blocks `*.db`, `data/`, `inbox/`, `private/`, `*.env*`, credentials.
- `env.example` — documented required env vars (no actual secrets).
- Pre-commit config with deny-list for secret patterns.
- `pyproject.toml` — uv project, Python 3.12, ruff + pyright + pytest; `src/padres_analytics/` layout.
- `SECURITY.md` — responsible disclosure policy.
- `VOICE.md` stub.
