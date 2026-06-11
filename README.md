<p align="center">
  <img src="src/padres_analytics/render/templates/assets/xfriars_logo.png" alt="xFriars" height="60"/>
</p>

<p align="center">
  <b>Engine behind <a href="https://x.com/xFriars">@xFriars</a> — San Diego Padres analytics on X.</b><br/>
  Franchise history, current-season Statcast leaderboards, crossjoin queries, branded stat cards.
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-blue.svg" alt="python 3.12"/></a>
  <a href="https://duckdb.org/"><img src="https://img.shields.io/badge/store-DuckDB-fff100" alt="DuckDB"/></a>
  <a href="studio/"><img src="https://img.shields.io/badge/studio-React%2019-61dafb" alt="React 19"/></a>
  <a href="src/padres_analytics/storage/schemas.py"><img src="https://img.shields.io/badge/schema-v3-informational" alt="schema v3"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT"/></a>
</p>

---

*Not affiliated with the San Diego Padres or Major League Baseball.*

---

## What this repo is

This repo demonstrates the engine: data ingestion, deterministic SQL detectors, a
verification regime, a D3-powered PNG renderer, and the `pad` CLI. The editorial
model, interest weights, full detector SQL arsenal, and all data are intentionally
not included.

The pipeline:

```
ingest (MLB API + Baseball Savant → padres.db)
  → detect (SQL detectors → stat_candidates)
    → /padres-stat skill (judge + caption → inbox JSON)
      → pad draft ingest (validate → render PNG → verify)
        → pad queue → pad draft approve → pad post
```

Every number that reaches a post card passed through the accuracy regime in
`src/padres_analytics/detect/base.py`: provenance, coverage-bounded claims,
and a two-path verification gate.

---

## Design principles

- **One payload, two outputs.** The same `facts_json` drives both the caption
  and the rendered PNG. Text and image cannot diverge.
- **Fail visibly.** A detector with incomplete or stale inputs emits nothing
  and logs why. "Completed" only means nothing was quietly skipped.
- **Coverage-bounded superlatives.** Claims are bounded to the data window that
  supports them (`since 1990`, `since 2015`). Only Baseball Reference WAR
  (1871–present) earns "all-time."
- **Claude never computes numbers.** Detectors (SQL) compute; Claude writes
  captions using only numbers present in the verified payload.
- **Padres-anchored, league-aware.** The Padre is always the protagonist; the
  table is league-wide with the Padre row highlighted. Padres-only starves;
  league context gets shared by non-Padres fans.
- **Fresh data, automatic.** A `_tbl()` resolver in every Statcast detector
  prefers `main.{table}` (fresh ingest) over the historical attach. Run
  `pad ingest statcast` and detectors silently upgrade.

---

## Detectors

Detectors are SQL-first: a Python class wraps a query, validates provenance,
and emits a `StatCandidate`. They register themselves at import time.

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

---

## Cards

All cards render to PNG via Playwright + Jinja2 + D3.js v7. The design spec is
[xFriars brand v2](src/padres_analytics/render/tokens.py): near-black canvas,
white for hierarchy, gold as data ink and accent only.

**Table card** — leaderboard or franchise results table with Padre row highlighted.
Big Shoulders Display (900) for titles, Space Grotesk for labels and cells.

**Bar card** — D3 horizontal bar chart with gradient fills, glow highlight on
the best metric, and hairline row rules. Powers `statcast_profile`.

---

## Data sources

| Source | Coverage | Role |
|---|---|---|
| MLB Stats API | 2010–present | schedule, box scores, game logs, season stats, leaderboards |
| Baseball Reference bWAR | 1871–present | historical WAR (via `savage-trade-evaluator` attach) |
| Baseball Savant / Statcast | 2015–present | percentile ranks, expected stats, sprint speed, exit velo / barrels |
| Retrosheet transactions | 1880–2009 | pre-API trade history (via attach) |

`padres.db` is owned by this project. Historical data (`trades.db`) is attached
READ-ONLY from `savage-trade-evaluator`. No data files are committed to this repo.

---

## Under the hood

**Backend** — Python 3.12, DuckDB, Playwright. A `typer` CLI (`pad <verb>`) drives
ingestion, detection, draft management, and posting.

**Frontend (Studio)** — React 19, TypeScript, Vite, Tailwind. Candidate review,
draft queue, and PNG card preview. `pad studio` launches it.

```
src/padres_analytics/
├── app/         # FastAPI Studio backend
├── detect/      # SQL detectors (base + leaderboards + crossjoin + statcast)
├── ingest/      # MLB API, Statcast (Baseball Savant), ingest-run tracking
├── render/      # Playwright PNG renderer, Jinja2 templates, D3 bundle, MLB assets
├── storage/     # DuckDB schema, connection helpers
└── tweets/      # Ammo file exporter, draft pipeline
studio/          # React SPA (candidate review + card preview)
tests/           # pytest suite with snapshot tests
```

---

## Run it yourself

```bash
# Install and initialize
uv sync
uv run playwright install chromium
uv run pad init                      # create padres.db, apply schema

# Ingest current-season data
uv run pad ingest mlb                # MLB Stats API: schedule, box scores, leaders
uv run pad ingest statcast           # Baseball Savant: percentile ranks, xStats, sprint, barrels

# Run detectors
uv run pad detect run statcast_profile    # Statcast tool card for each Padre
uv run pad detect run xstats_unlucky      # xwOBA luck leaderboard
uv run pad detect list                    # show all candidates with novelty scores

# Draft and review
uv run pad ammo                      # export ammo file for /padres-stat skill
uv run pad studio                    # open the Studio UI at http://localhost:5173
```

---

## Accuracy policy

This project treats correctness as the non-negotiable constraint. Coverage is
bounded: claims say "since 1990" or "since 2015," not "ever." Cross-validation
runs on every detector before a candidate is approved. Claude does not invent
numbers — it reads from `facts_json` only.

See [`VOICE.md`](VOICE.md) for editorial style and the banned-tells list.

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
