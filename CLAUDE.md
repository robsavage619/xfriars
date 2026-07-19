# padres-analytics — Agent project context

Engine behind @xFriars. Deterministic SQL-first stat detection → narrative infographics. Rob's global conventions (`~/.claude/CLAUDE.md`) apply; this is the project overlay.

## The one rule that outranks everything

**Claude never computes stats; Claude only writes voice.** Every number comes from SQL detectors + the scan engine, lands in `facts_json`, and is immutable from there. The caption writer receives pre-verified facts and may not add, recompute, or upgrade the scope of any claim. `tweets/verify.py` enforces this structurally (digit audit + scope guard) — a single digit mismatch between rendered PNG and `facts_json` aborts the draft.

## Coverage windows (bound every superlative)

| Source | Window |
|---|---|
| MLB Stats API | 2010+ |
| Statcast / Baseball Savant | 2015+ |
| bWAR (Baseball Reference) | 1871+ |
| Retrosheet transactions | 1880–2009 |

"First Padre ever" requires bWAR-backed verification; a Statcast-derived claim is "since 2015", full stop. The scope guard blocks promotion across tiers — don't fight it, phrase within the window.

## Architecture invariants

- **Core loop**: ingest → detect/scan → scout → story → render (Playwright PNG) → digit audit + scope guard → post. Candidates and drafts land in The Board (`board.py`, FastAPI).
- **Detectors register at import** via `detect/base.py` registry. The generic scan engine is driven by a TOML metric registry — public example at `examples/metrics.example.toml`, private tuned values in `private/metrics.toml`. New stats are registry rows, not new Python, unless the shape is genuinely new.
- **Statistical guards are load-bearing**: ECDF extremeness + empirical-Bayes shrinkage (small samples shrink toward population mean) + Benjamini–Hochberg FDR across daily detectors. Naive "highest rate" rankings bypass both and produce noise — don't.
- **Coverage preflight**: `storage` exposes `can_support()` gates; check before promising a stat exists for a date range.

## Stack & commands

- Python 3.12, DuckDB (schema v12), Typer CLI, Playwright render, React 19 + Vite studio.
- CLI: `uv run pad <verb>` — `init`, `ingest`, `detect run|list`, `scout`, `story`, `queue`, `render`, `draft`, `post`, spatial cards (`spray|hotcold|rolling|swingtake|batspeed`), `live now|watch|ask|card`.
- Tests: `uv run pytest tests/` (358 tests).
- Studio: `cd studio && npm run dev`.

## Where to read first

- [README.md](README.md) — full corpus + accuracy architecture
- [DECISIONS.md](DECISIONS.md) — ADRs (ChartDataset, scan engine, data-shape selector, long-form articles)
- [VOICE.md](VOICE.md) / [VOICE_LONGFORM.md](VOICE_LONGFORM.md) — editorial voice; banned AI tells. Tweets and articles use different registers — don't blend.
- `docs/` — GLOSSARY, METHODOLOGY, VISUAL_LIBRARY, CAPTIONS
