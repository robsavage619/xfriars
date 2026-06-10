# padres-analytics

Engine behind [@xFriars](https://x.com/xFriars) — a Padres analytics account built on
genuinely novel statistics: franchise history, current-season leaderboards, crossjoin
queries no one else can run, and branded stat cards designed for the X timeline.

**Not affiliated with MLB or the San Diego Padres.**

---

## What this repo is

This repo demonstrates the engine: data ingestion, deterministic detectors, a
verification regime, a renderer, and the `pad` CLI. The editorial model, interest
weights, full detector SQL arsenal, and all data are intentionally not included.

The pipeline shape:

```
ingest (MLB API + Statcast → padres.db)
  → detect (SQL detectors → stat_candidates)
    → /padres-stat skill (judge + caption → inbox JSON)
      → pad draft ingest (validate → render PNG → verify)
        → pad queue → pad draft approve → pad post
```

Every number that reaches a post card passed through the accuracy regime described
in `src/padres_analytics/detect/base.py`: provenance, coverage-bounded claims,
and a two-path verification gate.

---

## Design

- **One payload, two outputs.** The same `facts_json` drives both the caption
  and the rendered PNG. Text and image cannot diverge.
- **Fail visibly.** A detector with incomplete or stale inputs emits nothing
  and logs why. "Completed" only means nothing was quietly skipped.
- **Coverage-bounded superlatives.** Claims are bounded to the data window that
  supports them (`since 1990`, `since 2015`). Only Baseball-Reference WAR
  (1871–present) earns "ever/all-time."
- **Claude never computes numbers.** Detectors (SQL) compute; Claude writes
  captions using only numbers present in the verified payload.
- **Padres-anchored, league-aware.** The Padre is always the protagonist; the
  table is league-wide with the Padre row highlighted. Padres-only starves;
  league context gets shared by non-Padres fans.

---

## Stack

Python 3.12, [uv](https://docs.astral.sh/uv/), DuckDB, Playwright (card rendering),
matplotlib, Typer, Pydantic, Jinja2.

```bash
uv sync
uv run playwright install chromium
uv run pad --help
```

---

## Data terms and attribution

This project uses several public data sources on a non-commercial basis:

**Retrosheet:**
The information used here was obtained free of charge from and is copyrighted by
Retrosheet. Interested parties may contact Retrosheet at 20 Sunset Rd., Newark,
DE 19711.

**MLB Stats API:**
Data from the MLB Stats API is used on an unofficial, non-commercial basis in
accordance with [MLB's API usage policy](https://gdx.mlb.com/components/copyright.txt).
This project is not affiliated with or endorsed by Major League Baseball.

**Baseball-Reference / Sports Reference:**
Historical WAR and statistics sourced from
[Baseball-Reference.com](https://www.baseball-reference.com). Used for non-commercial
research and cross-validation only.

**FanGraphs:**
Statistics cited from [FanGraphs.com](https://www.fangraphs.com) for non-commercial
use only.

**Spotrac:**
Contract data cited from [Spotrac.com](https://www.spotrac.com) for non-commercial
use only.

**Baseball Savant / Statcast:**
Statcast data from [baseballsavant.mlb.com](https://baseballsavant.mlb.com), made
available by MLB Advanced Media.

None of these sources endorse this project. No data files are included in this
repository.

---

## License

MIT — see [LICENSE](LICENSE).
