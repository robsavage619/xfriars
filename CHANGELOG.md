# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
