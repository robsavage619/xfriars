# xFriars Spatial Visual Library — Spec

Status: in build. Panel-reviewed 2026-06-18 (simulated Savant / Codify / FanGraphs / ESPN).
Visual system: editorial-light v3 (see memory `project_xfriars_style_guide`).

## Why this exists
The engine renders only generic data cards (`hero, slider, scatter, bar, table, bars`). It
has **no baseball-native spatial visuals** and ingests **only season-aggregate Statcast
leaderboards** — no event-level data. This spec defines the library that makes xFriars look
professional, and the two-layer build (event ingest + spatial renderers) behind it.

## Decisions (locked)
1. **Build order — "spray first, inside the rigor harness":** harness + geometry kit →
   spray → arsenal/movement → hot/cold zones → HR spray+distance → rolling form → LA/EV →
   release. Reconciles the panel split (engagement vs defensibility): spray leads because
   it's the flagship, but only ships gated by the rigor harness.
2. **Rigor harness is a required template field.** Every spatial card MUST populate
   `n`, `coverage` (since-date), `handedness` (vs RHP/LHP/All), `pov`, `park`. A card that
   can't fill them does not render. This is the single highest-leverage rule — it enforces
   accuracy-first by construction.
3. **One shared geometry kit, reused everywhere:** identical home-plate pentagon, one
   outfield arc (foul lines + arc only — no infield/grass/fences), one true-proportion
   strike-zone box. Lives in `render/static/baseball.js`. Build once.
4. **Palette: +1 color only.** Add slate-teal `#2C6E7F` for CVD-safe diverging hot/cold
   (`#2C6E7F` → paper → `#C0392B`). Density = single-hue brown ramp, never rainbow/hexbin.
   Pitch types separated by **position + in-situ label, not hue**. No legends/colorbars.

## Technical-correctness rules (never ship a card that violates these)
- **Spray coords:** `x_ft = (hc_x − 125.42) * 2.5`, `y_ft = (198.27 − hc_y) * 2.5`.
  `hc_y` is screen-inverted — forgetting it renders the chart upside-down. 2B ≈ (0, 127).
- **Zones are catcher's POV:** positive `plate_x` plots on the **left**. `plate_x/plate_z`
  are already feet; rulebook zone ≈ −0.83..0.83 ft wide, `sz_bot..sz_top` tall (per-pitch,
  never hardcoded).
- **Pitch movement:** `pfx_x/pfx_z` are **feet → ×12 for inches**. Mirror `pfx_x` for LHP
  (or facet by `p_throws`) or the arsenal cluster is nonsense. `pfx_z+` = induced "rise".
- **HR distance:** use `hit_distance_sc` (trajectory model). Never `sqrt(x²+y²)` from hc
  coords — that's landing projection, not carry.
- **Rolling windows:** roll over PA/BBE, not calendar games. Plot xwOBA (stable), not wOBA.

## Sample-size floors (below = "illustrative, not predictive", or don't post)
| Visual | Floor | Comfortable |
|---|---|---|
| Spray | 50 BBE | 100+ |
| Hot/cold zones | 50 BBE total **and** ~5–7 per cell; suppress low-N cells | 150+ BBE |
| Pitcher location heatmap | 100 pitches | 300+ |
| LA/EV distribution | 40 BBE (min-BBE on card) | 100+ |
| Pitch movement/arsenal | 25–40 **per pitch type** | 100+ |
| HR spray+distance | 8–10 HR (gallery, never a rate claim) | 15+ |
| Rolling xwOBA | window ≥ 50 BBE (~150–200 PA to claim a trend) | — |

## Required caveats on the card face
Universal: `N`, coverage window, handedness split. Plus per family: zones/movement → POV;
movement → induced-movement reference; spray/HR → park + shift-era note (post-2023 limits);
outcomes → show xwOBA alongside wOBA. Single-game = event description, never "trend".

## Design house-style (ESPN panel)
Cream paper, espresso ink, brown geometry chrome in thin hairlines, one loud element per
card (red data-max OR gold "now", never both). One marker language: dot=event, line=
trajectory/trend, ellipse=distribution, wedge=angular band. In-situ labels, no legends.
Round like a broadcaster (.312, 94.3, 28°). Portrait 4:5 default; square only for intrinsically
square spaces (movement, LA fan, rolling). Field = home-plate pentagon + outfield arc + two
foul lines, nothing else. Zone = one true-proportion box, 3×3 hairline split, faint dashed
shadow-zone, catcher's POV.

## Data layer
- ✅ **Batted balls (batter):** `pad ingest batted-balls --player <id> --season <yr>` →
  `ingest_batted_balls` (`ingest/statcast_events.py`) via `pybaseball.statcast_batter`, stored
  in `statcast_batted_balls` (hc_x/hc_y, launch_speed, launch_angle, hit_distance_sc, events,
  bb_type, stand, p_throws, estimated_woba, **game_type**). `build_spray` (`detect/spatial.py`)
  filters to `game_type='R'` — "season" = regular season, never spring/postseason.
- ❌ **Pitch events (pitcher):** still unbuilt — `statcast_pitcher(...)` → table for plate_x,
  plate_z, sz_top, sz_bot, pfx_x, pfx_z, release_*, pitch_type, description, p_throws, stand.
  Needed for arsenal/movement, zones, and location heatmaps.

## Render layer
New `SpatialDataset` payload (points + rigor fields) dispatched in `render/cards.py`.
Card templates: `card_spray`, `card_movement`, `card_zone`, `card_hr`, `card_rolling`,
`card_launch`. Geometry kit shared via `render/static/baseball.js`.

## Signature recurring formats to OWN (Codify panel)
1. **"The Spray"** — Petco-overlaid spray (flagship).
2. **"The Book"** — hot/cold 3×3 zone (QT/debate engine).
3. **"Friar Profile"** — portrait percentile identity card (existing slider, productized).
4. **"Pitcher's Night"** — post-start arsenal + zone combo.
5. **"The Arc"** — HR trajectory on Petco silhouette.
