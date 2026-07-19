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

## ADR-005 — Self-learning editorial priors (`pad learn`)

**Date:** 2026-07-18
**Status:** Decided (Phase 2 shipped; consumers wired in both pipelines)

**Context:** Every editorial decision the system made was being collected and never read back. Board cards recorded queued/dismissed; Studio recorded candidate rejections; `predict.py` graded falsifiable calls and published a scorecard; the referee (ADR-004) recorded reasoned blocks. None of it influenced what surfaced next. `scoring.py` had always *read* `private/interest_weights.toml` and its docstring said "tune from engagement data" — but no code anywhere wrote that file, so the engine ran on untuned example values indefinitely. The one genuinely closed loop (engagement → `angles._rerank`) had never fired, because `post_metrics` was empty.

**Decision:**
- **Beta-Bernoulli approve rates over coarse features**, not machine learning. Every multiplier is recomputable by hand from its counts, which matters because these numbers change what Rob sees and he has to be able to audit why one moved. `Beta(2,2)` prior; features are coarse (`metric_family:contact_quality`, not `metric:pctl_B_max_ev`) so evidence accumulates instead of scattering.
- **Cold-start neutrality is explicit.** Below 5 units of decayed evidence a feature returns exactly 1.0. The system is label-starved today; the correct behavior is silence, not confidence.
- **Bounded influence.** Per-feature clamp [0.80, 1.25], combined clamp [0.70, 1.40] — the same envelope as `engagement_prior`. Combination is a geometric mean, not a product, because a card's features overlap heavily and multiplying correlated evidence compounds one signal into several. Learning tilts ranking; it never overrides the statistical gates.
- **Decay.** Editorial evidence halves every 90 days, engagement every 45. Taste drifts, and an old dismissal should fade rather than ossify.
- **An exploration floor.** `ScanConfig.exploration_slots` (default 2) reserves top-K slots for the best candidates by *raw* novelty, ignoring priors. Without it, ranking by what was approved before converges on a house style and the feed quietly narrows — the exact failure this whole overhaul exists to fix.
- **Priors touch ranking only.** Never `facts_json`, never a claim, never a gate. Applied in `scanner._rank_with_priors` and `angles._rerank`, with the multiplier and raw score recorded in `novelty_components` so `pad detect list` shows why a candidate moved.
- **Referee failure modes are features.** A block on `trivial` teaches the ranker something a bare rejection cannot; this is what makes ADR-004's controlled vocabulary pay off.
- **`pad learn run` writes `private/interest_weights.toml`** — the writer that never existed. Only `[detector_bonuses]` is learned; `[weights]` and `[thresholds]` carry through untouched, because refitting five component weights from sparse editorial verdicts is under-determined.

**Consequences:** Stateless recompute (v14 `learned_priors` / `learning_runs`) means nothing accumulates incrementally and nothing can silently corrupt; the history is the audit trail. `pad learn report` states plainly when a prior is running on zero data, so starvation is visible rather than reading as a clean sheet — on the first live run it correctly reported that 3 observations cleared no evidence floor and every prior stayed neutral. `pad metrics record --from-board` reduces the transcription friction that kept `post_metrics` empty. The main risk is the echo chamber, addressed by the clamps, decay, and exploration floor together; the residual risk is that Board verdicts encode Rob's taste rather than audience response, which is why engagement and prediction grades are separate inputs rather than folded into one score.

## ADR-006 — Categorical splits and the split-contrast lens

**Date:** 2026-07-18
**Status:** Decided (Phase 3 core shipped; changepoint primitives and league event backfill remain)

**Context:** The scan DSL could express exactly one question shape: "which single Padre is in the top X% of the league this season on one scalar from one table." The richest data in the database — hundreds of thousands of pitch rows carrying swing decisions, zone, handedness and bat tracking — was unreachable, because `discover_metrics` only read player-season summary tables. And the hypothesis validator's blanket ban on quote characters (correct as a SQL trust boundary) made every categorical split inexpressible: no platoon, no vs-breaking, no in/out of zone. Those splits are what fans actually argue about, so their absence is most of why the feed felt generic.

**Decision:**
- **Splits without loosening the validator.** `detect/splits.py` holds a curated `ENUM_COLUMNS` allowlist; the **engine** renders `p_throws = 'L'` only for pairs in that map. String literals originate in engine code, never in caller or LLM text, so `validate.py` stays byte-identical and the injection surface stays closed. Derived families (`pitch_class`, `zone_bucket`) expand to `IN` lists over their real underlying columns.
- **Pitch-level rates with explicit denominators** (`detect/aggregates.py`). Chase rate is swings over *out-of-zone pitches*, whiff rate is misses over *swings*. The numerator/denominator pair is the whole metric; getting it wrong yields a different stat wearing the same name.
- **Not every metric crosses with every split.** `AggMetric.excluded_split_columns` refuses incoherent combinations — chase rate is defined on out-of-zone pitches, so slicing it by zone is meaningless. Generating the full cross product mechanically is how an engine produces confident nonsense.
- **Contrasts rank the gap against the league's distribution of that same gap** (`detect/contrast.py`), never against zero: every hitter has a platoon split, so zero is not the null. This keeps ECDF extremeness and shrinkage valid on a differential, with the *smaller* side's sample driving reliability.
- **Rank the gap's magnitude, not its signed value.** A chase-vs-zone split is negative for every hitter, so a signed rank describes the widest gap in baseball as "narrower than 96%" — true of the number, backwards as English.
- **Both sides are always printed.** A bare differential hides which term drives it: a wide swing-rate gap can come from elite restraint or from sheer aggression at strikes, and those are opposite claims about a hitter. This was a referee finding, not a design foresight.
- **Shrinkage now uses the focal player's own sample.** `extremeness_lens` had shrunk on *population* size, so with ~500 qualified players and `stabilization_n=200` the shrinkage never fired at all. Harmless for season-grain metrics; fatal once a split halves a sample. `focal_n` is now threaded from every event-grain fetch.

**Consequences:** The engine can finally ask a question a fan would ask. First live run surfaced real findings (a hitter whose swing-rate gap between breaking balls and fastballs is wider than 98% of measured hitters) alongside the plate-discipline leaderboards. Multiplicity grew — the daily battery went from ~18 to ~46 comparisons — so BH now logs `tested` and `battery` separately, and contrast candidates are counted in the battery even though they don't flow through the `_Hit` path. FDR stays advisory pending calibration.

**Honesty constraint we had to adopt:** pitch-level data covers roughly 135 hitters, not all of MLB, because event ingest is roster-scoped. Every split claim now says "of the 135 hitters with pitch-level data" rather than "qualified MLB hitters" — the referee's statistician lens blocked the first draft for exactly this, calling a convenience sample a league. A league-wide event backfill would remove the caveat; until then the caveat ships.

## ADR-007 — BH correction is unachievable at our resolution; report the noise floor instead

**Date:** 2026-07-18
**Status:** Decided

**Context:** ADR-002 mandated FDR control, and the Phase 3 plan called for flipping it from advisory to strict once splits multiplied the daily battery. Every run reported `survivors=0, dropped=31`, which looked like a calibration problem. It is not.

An ECDF over `n` players cannot resolve finer than `1/n`: a player who beats everyone reaches `1 - 1/n`, so the smallest p-value proxy the method can emit is `1/n`. Benjamini-Hochberg requires the strongest result to clear `alpha/m`. With a 135-player ingested population and a 46-comparison battery, that is 0.0074 against a threshold of 0.0011 — **the best hitter in baseball could not pass.** Empirical-Bayes shrinkage makes it strictly worse, since it pulls rarity toward 0.5. Flipping to strict as planned would have silently emptied the feed and read as a run of quiet days.

**Decision:**
- `bh_is_feasible(population, battery, alpha)` encodes the resolution ceiling. Strict mode **refuses to enforce** when infeasibility is provable, logs why, and falls back to advisory. The veto fires only on proof — an unknown population must not disable the gate, which would be the same failure in reverse.
- Report `expected_false_discoveries(battery, floor)` every run instead. At a 0.85 floor over 46 comparisons, ~7 hits are expected from chance. This is more useful than a binary verdict: it says out loud that a day surfacing 8 hits surfaced approximately nothing.
- The real enforced filters are the rarity floor, the sample and stabilization gates, the referee, and human approval. `docs/METHODOLOGY.md` now says this plainly rather than implying FDR is load-bearing.

**Consequences:** Widening the ingested population or narrowing the daily battery makes BH feasible; lowering alpha never does, because both sides scale together. This supersedes the Phase 3 plan's "flip FDR to strict" step, which was wrong.

## ADR-008 — Career-baseline detection ships silent until a league cohort exists

**Date:** 2026-07-18
**Status:** Decided

**Context:** `detect/changepoint.py` compares a player's season to his own multi-year baseline — the "is he a different player" question. Building it surfaced two data facts. `player_season_batting` is sourced per team (`team_season_hitting/135`), so it holds Padres only, and its stat columns are VARCHAR, so `AVG(ops)` raised an error that an over-broad handler turned into a silent "no shifts today."

**Decision:** The detector is complete and correct, and gated off by `MIN_COHORT = 30`. The cohort supplying the "how much do players normally move" spread currently contains three players. Dividing by a three-observation standard deviation manufactures large z-scores from nothing, and the cohort would consist of the subject's own teammates — the self-comparison the league-control rule exists to prevent. The gate logs the cohort size and what would unlock it, and the detector activates on its own once league-wide season data is ingested. The VARCHAR casts are fixed and the fetch handler now logs at error level, because a failure that presents as "no story today" is the worst available outcome.

**Consequences:** A built, tested detector sits dormant. That is the right trade against shipping a plausible-looking claim built on a three-player spread — and it is the same class of defect the referee's coverage lens blocked in the split work (a convenience sample described as a league).

## ADR-009 — Study dossiers: frozen decomposition with a third verdict

**Date:** 2026-07-18
**Status:** Decided (Phase 5 core shipped; compose-to-article deferred)

**Context:** Cards state a finding. A deep dive has to decompose one — anomaly, components, mechanism, context, prediction — and the engine had no structure for that. The risk in building it is obvious: a decomposition chosen after seeing the data is how a narrative gets fitted to a conclusion.

**Decision:**
- **The walk is fixed in code.** Same questions, same order, every study. Thresholds are decided in advance.
- **Three verdicts, not two.** `fired`, `quiet`, and `insufficient` — the third carrying a required reason. A study that omits a step it couldn't answer is claiming completeness it doesn't have, so the dossier reports the shape of its own ignorance and `coverage_notes` collects it.
- **The dossier is frozen and is the digit-audit corpus.** `audit_corpus()` plays the role `facts_json` plays for a card, with a `digest()` for change detection. Claude narrates over it and may not add a number.
- **The comps node stays `insufficient` rather than substituting a weaker answer.** Finding hitters with a similar *luck profile* and reporting what they did next needs expected stats across two or more seasons; we have one. A bWAR production-similarity comp was built, produced 1913 and 1926 seasons matched on partial-season WAR, and was **deleted** — a weak answer sitting beside an honest "cannot answer" muddies the honesty rather than adding to it.

**Consequences:** `pad study run <player_id|candidate_id>` produces a real decomposition today: gap sized and ranked, gap attributed to average versus power, contact quality percentile-ranked, approach change ruled in or out against how the league's hitters moved, and the regression loop explicitly left open. Schema v15 adds `study_dossiers`.

**What building it caught:** the study's approach node reported a chase rate of 99.8%, which is impossible. The Phase 3 aggregate metrics had numerators that did not imply their denominators — chase counted *all* swings over *out-of-zone* pitches, and zone contact counted all non-whiff rows including takes. Fixed, with an empirical `rate_is_bounded` invariant test over data containing every event type (textual containment can't be asserted, since a whiff is semantically a swing without naming every swing type). Post-fix the rates land on published MLB values: chase median 29.6%, whiff 21.3%, zone contact 86.4%, swing 47.1%. No surfaced claim had used the broken metrics — the contrast candidates that fired were all swing rate — but they were one extreme value away from doing so.

## ADR-010 — Study composition, a gradable registry, and the ingest gap that hid under both

**Date:** 2026-07-18
**Status:** Decided

**Context:** Three loose ends from Phase 5: dossiers couldn't be rendered, `predict.py` could only grade two hardcoded detectors, and the pitch data was a month stale.

**Decision:**
- **Composition is selection, not authorship** (`study/compose.py`). Which panels appear is decided by which nodes fired, so a card's shape follows the investigation rather than a template — this retires the hardcoded five-player roster that never varied. Nothing in composition computes; every displayed value is copied from a node's facts, keeping the dossier the audit corpus. A study with fewer than two fired nodes composes nothing: one answered question is a fact, not a story.
- **The closing line names what is still open**, and does not restate the finding the hero already carries. A deep dive that hides its open questions is selling a conclusion it didn't reach.
- **Studies join the normal candidate path** as `payload_kind="story"`, so they pass the same gates as anything else — including the referee. The CLI's render dispatch was missing that branch even though the renderer supported it.
- **Gradable claims are registered, not hardcoded** (`register_gradable` / `gradable_spec`). Re-binding an existing key raises, because silently changing how a claim grades would rewrite the meaning of predictions already logged against it.

**The gap this surfaced:** `pad ingest all-events` covered batted balls and pitcher events but **not** `statcast_batter_pitches` — the table every plate-discipline rate and split contrast reads from. There was no roster-wide command for it at all, only a per-player one, so the entire Phase 3 feature set was silently pinned to whenever that table was last hand-filled (a month prior). Batters now get their faced pitches in the same pass; all three event tables are current.

**A second honesty fix in the same area:** with fresh data a 21-day window contained eight players, all Padres, because event ingest is roster-scoped and recent windows fill for the roster before the rest of the league. The population gate correctly refused to run, but the ledger reported "none reached the rarity floor" — implying a league comparison that never happened. It now distinguishes a population too thin to compare from a comparison that found nothing, and says which. This is the third instance of the same class of bug in this ledger; the pattern to watch is any path where an empty result can arise from two different causes that imply opposite next moves.

## ADR-011 — League-wide event backfill, and a self-correcting population label

**Date:** 2026-07-18
**Status:** Decided (backfill run; 353 hitters covered)

**Context:** Every split contrast and plate-discipline percentile compares a Padre against whoever happens to be in the event table. That was ~135 players, so each claim carried "not the full league" — a caveat the referee's statistician lens had (correctly) forced after the first draft called a convenience sample "qualified MLB hitters."

**Decision:**
- **Throttled and resumable before running.** `pad ingest league-events` sources the qualified population from the season-summary table (using the table being filled would only return who's already in it), spaces requests by a configurable delay, and skips players already current so an interruption resumes rather than restarts. A single unavailable player is counted, never fatal. Run: 338 fetched, 8 skipped, 0 failed, 403,409 rows, ~24 minutes at 3.5s/player.
- **The population label derives from measured coverage**, not a constant. Below 90% of the qualified population it names the shortfall; at or above it reports league-wide coverage. Nobody has to remember to retire the caveat, and it returns automatically if coverage regresses.
- **At full coverage the label still refuses "qualified (min 100 PA)."** The measured group is whoever cleared the metric's pitch minimum, which is not the PA-qualified set it's compared against — coverage came out at 102%, i.e. some measured hitters sit below the PA bar. Attributing a qualification the group doesn't hold is the same error as calling a partial sample the league, one order smaller.

**Consequences:** Claims now read "wider than 97% of 350 MLB hitters (league-wide coverage)". Contrast candidates fell from 6 to 4 — against a real league, fewer gaps are rare, which is the correction doing its job rather than a regression.

**Extended to batted balls (same day).** The driver was generalized to a `LeagueGroup` rather than copied, since two divergent copies of throttle/resume/failure-isolation logic is worse than either. `statcast_batted_balls` went from 12 players to 352 (339 fetched, 0 failed, 70,869 rows); both event domains now report OK. The label wording moved from "pitch-level" to grain-neutral "league-wide coverage" now that it describes batted-ball populations too.

**What that unlocked immediately:** the hypothesis engine went from every windowed spec returning `no_data` or `coverage_blocked` to four of five emitting against populations of 249–310 players — bat speed over 21 days, swing length, run value per pitch seen, launch angle over 30 days. None of these exist as a fixed detector; they are LLM-proposed questions the scanner measured and gated. The fifth was held at `below_gate` (rarity 0.83 against a 0.85 floor), which is the system declining to publish something merely close.

## ADR-012 — Historical expected stats, and the control that deflated our own luck story

**Date:** 2026-07-18
**Status:** Decided

**Context:** The study's comps node returned `insufficient` on every run because expected stats covered one season. Closing it turned out to be nearly free: `statcast_batter_expected_stats(season)` is a *leaderboard* pull, one API call per season, and all four Statcast season tables delete by year before inserting, so a backfill cannot clobber the current season.

**Decision:** Backfilled 2015–2025 (11 seasons, ~1 minute). `statcast_batting_expected` now spans 12 seasons with 1,968 players carrying 2+ seasons and 1,063 carrying 4+.

**What that produced, and why it changed the node's design.** With history available, the comps node initially reported: *"Of 291 hitters who carried a similar gap, 210 (72%) saw their wOBA rise the following season — an average move of +0.024."* A strong, postable finding.

It is also very likely an artifact, and checking took one query. Hitters carrying a large positive gap have by construction just had a **bad results season**, and bad seasons are followed by better ones regardless of luck. Against a control of hitters with the same wOBA and *no* meaningful gap: 75% improved, mean **+0.022**. The gap cohort's edge over the control is **+0.002**.

So the node now runs the control by default and reports the net effect. On current data it returns `quiet` with: *"the gap is worth +0.002, so this rebound is ordinary regression, not owed luck."* It fires only when the net effect clears 0.010, and returns `insufficient` when no control cohort exists — because without one, a rebound cannot be separated from mean regression at all.

**Consequences:** This is the same league-control principle the engine already applies to short-window player changes, applied to a longitudinal claim. It deflates a story this account would otherwise be inclined to tell, which is the point of building the control rather than the reason to skip it. Scope note recorded in METHODOLOGY: this measures the *next-season* horizon; the in-season luck detectors make a shorter-horizon claim that this does not directly test.
