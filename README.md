<p align="center">
  <img src="src/padres_analytics/render/templates/assets/xfriars_logo.png" alt="xFriars" height="60"/>
</p>

<p align="center">
  <b>Engine behind <a href="https://x.com/xFriars">@xFriars</a> — San Diego Padres analytics on X.</b><br/>
  Franchise history · Statcast corpus · deterministic SQL detectors · story infographics · live in-game reads · branded cards.
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="python 3.12"/></a>
  <a href="https://duckdb.org/"><img src="https://img.shields.io/badge/store-DuckDB-fff100" alt="DuckDB"/></a>
  <a href="studio/"><img src="https://img.shields.io/badge/studio-React%2018-61dafb" alt="React 18"/></a>
  <a href="src/padres_analytics/storage/schemas.py"><img src="https://img.shields.io/badge/schema-v16-informational" alt="schema v16"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT"/></a>
</p>

---

*Not affiliated with the San Diego Padres or Major League Baseball.*

---

## What this is

This is a portfolio of the architecture, methodology, and accuracy regime behind
[@xFriars](https://x.com/xFriars). The public code demonstrates the engine — data
ingestion, deterministic SQL detectors, a statistical verification harness, a
D3-powered PNG renderer, and the `pad` CLI. The editorial model, interest weights,
trained detectors, and all data are intentionally private and not included.

This is not a turn-key tool meant to be cloned and run. The private half of the
system (tuned detectors, historical corpus, voice model) is what makes the public
half coherent. The engine skeleton is here to show the architecture.

---

## Data corpus and sources

Four authoritative sources form the corpus. Each is used for what it is uniquely
suited for, with cross-validation running between overlapping windows.

| Source | Coverage | What it anchors |
|---|---|---|
| MLB Stats API | 2010–present | Schedule, box scores, game logs, season stats, leaderboards — the official live spine |
| Baseball Reference bWAR | 1871–present | Historical WAR — the only source that earns "all-time" claims (155 seasons) |
| Baseball Savant / Statcast | 2015–present | Percentile ranks, expected stats (xwOBA, xBA, xSLG), sprint speed, exit velocity, barrel rate — the granular physics layer |
| Retrosheet transactions | 1880–2009 | Pre-API trade and transaction history (bridging the MLB API gap back 130 years) |

**Coverage-bounded claims.** Every superlative is explicitly bounded to the window
the source supports: `since 2015` for Statcast, `since 2010` for MLB API game logs,
`since 1990` for franchise history, `since 1871` only when bWAR confirms it. No
number escapes its provenance window.

**Cross-validation.** Season stats from the MLB API are validated against Statcast
aggregates for the same player-season. Disagreements surface as warnings before any
candidate reaches a draft. The same number must appear identically in both
`facts_json` and the rendered card — the renderer and the caption share one payload
and cannot diverge.

---

## Accuracy architecture

### The verification regime

Every number that reaches a post card passed through four gates:

1. **Provenance gate** — the detector records which source and season window
   produced each fact. A claim is rejected if its source can't cover the
   stated scope.
2. **Coverage preflight** (`pad coverage`) — before any analysis runs, the engine
   audits its own data: per-domain season span, granularity, freshness, and
   player coverage. `can_support()` blocks detection, scouting, and story
   generation on unverified completeness. The engine cannot analyze what it
   cannot prove it has.
3. **Digit audit** — rendered numbers are extracted from the PNG and reconciled
   against `facts_json`. A single digit mismatch aborts the draft.
4. **Scope-upgrade guard** (`verify.py`) — the engine selects the strongest
   *provably true* framing tier (franchise record → first since → Statcast era →
   season best). Claude writes voice over verified facts and may never upgrade
   the scope of a claim. The guard in `verify.py` enforces this structurally.

### Claude's role is strictly voice

Claude does not compute, rank, or select numbers. Detectors and the scan engine
produce all statistics via SQL. Claude receives a `facts_json` payload containing
only pre-verified facts and writes a caption from that payload — nothing else.
This is a hard architectural constraint, not a style guide.

---

## Statistical methodology

### Bespoke detectors

SQL-first classes, one per stat. Each wraps a single deterministic query,
validates provenance, emits a `StatCandidate`, and registers at import time via
`detect/base.py`.

| Detector | Type | Fires when |
|---|---|---|
| `on_this_day_results` | franchise history | game results on today's date exist back to 1990 |
| `on_this_day_transactions` | franchise history | trades on today's date exist (2010+) |
| `hr_leaders` / `avg_leaders` / `era_leaders` | leaderboards | a Padre is in the current-season MLB top 10 |
| `career_milestones` | milestones | a Padre is within 10 of a HR/H/K round number |
| `hit_streak` | streaks | active Padre hit streak ≥ 7 games |
| `streak_vs_team` | crossjoin | strong historical streak vs. the upcoming opponent |
| `opponent_crossjoin` | crossjoin | Padre vs. opponent leaderboard matchup |
| `statcast_profile` | Statcast | fires for any Padre in the current season's Statcast data |
| `xstats_unlucky` | Statcast | any Padre in the MLB top 25 for xwOBA − wOBA gap (≥100 PA) |
| `sprint_speed` | Statcast | any Padre in the MLB top 10 for sprint speed |
| `barrel_rate` | Statcast | any Padre in the MLB top 10 for barrel rate |

### Generic scan engine

A declarative TOML metric registry drives the scan engine — no per-stat Python
required. Each metric declares its population, lenses, milestones, and coverage
window.

| Layer | What it does |
|---|---|
| `detect/registry.py` | Loads the metric registry; validates `MetricSpec` / `PopulationSpec` / `ScanConfig` |
| `detect/lenses.py` | `extremeness` (ECDF + empirical-Bayes shrinkage), `rank` (top-quartile cap), `pace` (milestone countdown), `milestone_proximity` (within 10% of threshold) |
| `detect/conjunction.py` | Franchise scope evaluator (selects strongest true tier), named-anchor resolver ("first Padre since Gwynn (1997)"), conjunction grouper (multi-metric stories) |
| `detect/scanner.py` | Iterates registry → lenses → Benjamini-Hochberg FDR correction → scope strengthening → top-K `ChartDataset` candidates |

**ECDF + empirical-Bayes shrinkage** handles small-sample players: the extremeness
lens estimates where a player sits in the empirical distribution of the metric,
then shrinks toward the population mean proportional to sample size. A 30-PA outlier
is ranked below a 300-PA outlier of the same rate.

**Benjamini-Hochberg FDR correction** is applied across the full candidate set
before scoring. Running dozens of detectors over a full roster inflates the
false-discovery rate; BH correction keeps it bounded so the top-K candidates are
signal, not noise.

---

## Scout → deep dive → story

Three tiers turn raw signal into a publishable card:

- **`pad scout`** — shallow lead scouting. Surfaces *flags* (a number that looks
  anomalous for the subject), ranked by surprise and novelty relative to the
  player's own baseline and the league cohort. A lead is a starting point, never a post.
- **Deep dive** — a flagged lead is investigated across trends, splits,
  correlations, and sample discipline. Only survivors advance.
- **`pad story`** — story-discovery engine. Renders a multi-module `StoryCard`
  (hero hook + percentile panels + narrative) that separates skill from luck across
  several lenses, with every claim significance-gated and every rendered number
  reconciled against source.

Candidates and leads land on the **Board** (`board.py`), the store and API
that backs the Studio review gallery.

---

## Live (in-game) path

`pad live` reads the MLB **GUMBO** feed for pitch-level, in-game analysis —
unofficial and read-only. Live moments are gated and ranked (`live_moments.py`):
only moments that clear a significance threshold surface a card.

| Command | What it does |
|---|---|
| `pad live now` | current pitch-level read of the active Padres game |
| `pad live watch` | stream updates as they land |
| `pad live ask` | plain-language question about the current game state |
| `pad live card` | analytical live pitcher card — CSW% hero, whiff-colored mix, velo trend |

---

## Cards

All cards render to PNG via Playwright + Jinja2 + D3.js v7. Design is
[xFriars brand v3](src/padres_analytics/render/tokens.py) — editorial-light
("Goldsberry"): warm paper canvas, near-black espresso ink carries hierarchy,
gold demoted to a single hairline accent, no glows.

**Table card** — leaderboard or franchise results table with the Padre row highlighted.

**Bar card** — D3 horizontal bar chart with hairline row rules. Powers `statcast_profile`.

**Story card** — multi-module narrative infographic: hero hook + percentile panels
+ reconciled numbers, separating skill from luck.

**Spatial cards** — geometry-driven family: HR spray (`pad spray`), pitch arsenal
(`pad arsenal`), zone (`pad zone`), release-point (`pad release`), rolling-xwOBA,
swing/take run-value, and bat-tracking distribution.

**Live pitcher card** — in-game CSW% hero, whiff-colored pitch mix, velo trend.

---

## Pipeline

```
ingest (MLB API + Baseball Savant → padres.db)
  → detect / scan (SQL detectors + generic scan engine → stat_candidates)
    → /padres-stat skill (judge + caption → inbox JSON)
      → pad draft ingest (validate → digit audit → scope guard → render PNG → verify)
        → pad queue → pad draft approve → pad post
```

---

## Architecture

**Backend** — Python 3.12, DuckDB, Playwright. A `typer` CLI (`pad <verb>`) drives
ingestion, detection, draft management, and posting.

**Frontend (Studio)** — React 18, TypeScript, Vite, hand-written CSS on the
brand-v3 token set. Four views following the pipeline: **Desk** (run discovery,
data freshness, what's waiting), **Triage** (candidates and leads → open a
prompt), **Drafts** (paste Claude's result, edit captions, referee verdicts,
approve), **Shipped** (queue, post commands, scorecard, engagement).

**The prompt desk** — the Studio never calls a model. It assembles a prompt
carrying the full dossier, the coverage windows, the voice rules, and a JSON
contract; a human runs it in Claude and pastes the deliverable back, where the
existing gates (digit audit, scope guard, render, verify, referee) decide its
fate. Pasted output is data, never instructions: it is classified by shape and
can only enter the path that shape allows.

```
src/padres_analytics/
├── app/         # FastAPI Studio + Board backend
│                #   prompts.py — prompt assembly (the handoff to Claude)
│                #   results.py — paste-back door into the deterministic gates
│                #   jobs.py / chains.py — named background jobs (sync, discovery)
├── detect/      # SQL detectors, generic scan engine (registry, lenses, conjunction, scanner), scout, story, discovery
├── ingest/      # MLB API, Statcast (Baseball Savant), live GUMBO poller/serve, ingest-run tracking
├── render/      # Playwright PNG renderer, story + spatial templates, D3 bundle, MLB assets
├── storage/     # DuckDB schema, coverage preflight, connection helpers
├── live*.py     # In-game path: GUMBO reads, moment detector, ask intents, live pitcher card
├── board.py     # The Board — store + API where cards and scout leads land
└── tweets/      # Ammo file exporter, draft pipeline, digit audit, scope guard
studio/          # React SPA — the production desk (discover → triage → prompt → draft → ship)
tests/           # pytest suite (529 tests)
examples/        # Public metric registry + anchor bank (private/ overrides)
```

---

## Design principles

- **One payload, two outputs.** The same `facts_json` drives both the caption
  and the rendered PNG. Text and image cannot diverge.
- **Fail visibly.** A detector with incomplete or stale inputs emits nothing
  and logs why. "Completed" only means nothing was quietly skipped.
- **Coverage-bounded superlatives.** Claims are bounded to the data window
  that supports them. Only Baseball Reference WAR (1871–present) earns "all-time."
- **Claude never computes numbers.** Detectors and the scan engine (SQL) compute;
  Claude writes captions using only numbers present in the verified payload.
- **Engine chooses scope; Claude cannot upgrade it.** The scan engine selects the
  strongest *provably true* framing tier. A scope-upgrade guard in `verify.py`
  blocks any caption that promotes the claim further.
- **Padres-anchored, league-aware.** The Padre is always the protagonist; the table
  is league-wide with the Padre row highlighted. Padres-only context starves; league
  context earns reach beyond Padres fans.
- **Fresh data, automatic.** A `resolve_table()` helper in every Statcast detector
  prefers `main.{table}` (fresh ingest) over the historical attach. Run
  `pad ingest statcast` and everything silently upgrades.

---

## Data attribution

**Retrosheet:** The information used here was obtained free of charge from and is
copyrighted by Retrosheet. Interested parties may contact Retrosheet at
20 Sunset Rd., Newark, DE 19711.

**MLB Stats API:** Used on an unofficial, non-commercial basis per
[MLB's API usage policy](https://gdx.mlb.com/components/copyright.txt).
Not affiliated with or endorsed by Major League Baseball.

**Baseball Reference / Sports Reference:** Historical WAR sourced from
[Baseball-Reference.com](https://www.baseball-reference.com). Non-commercial
research and cross-validation only.

**Baseball Savant / Statcast:** Statcast data from
[baseballsavant.mlb.com](https://baseballsavant.mlb.com), made available by
MLB Advanced Media.

None of these sources endorse this project. No data files are included in this
repository.

---

## License

MIT — see [LICENSE](LICENSE).
