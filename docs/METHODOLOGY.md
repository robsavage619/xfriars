# How xFriars Calculates Things

This is the methodology behind every xFriars card and post — the math, the sample
gates, and the honesty rules. We publish it because an analytics account should be
able to show its work. If a number reaches a card, the way it was computed is here.

**What this document covers:** the formulas, the significance gates, and how a
claim is graded. **What it does not cover:** how candidates are *ranked and chosen*,
how copy is *worded*, and which thresholds are *tuned* — that judgment layer is the
account's editorial voice and stays private. The math is standard sabermetrics and
is shown in full; the taste is not. ("Open skeleton, closed brain.")

Three rules apply to everything below:

1. **Reliability before assertion.** Every talent claim carries a reliability
   `r = n / (n + k)` (Tango/Lichtman/Dolphin, *The Book*; `k = 220` PA for wOBA).
   A claim is never stated more confidently than its sample supports.
2. **Significance gates.** A divergence must clear both an effect-size threshold
   *and* a minimum sample to become a story. A 5-point wobble over 80 PA is not a
   story.
3. **Coverage before analysis.** The engine checks what data it actually has —
   seasons, granularity, freshness — before it runs. It will decline to make a
   claim it can't support rather than guess.

---

## Luck vs. skill: wOBA − xwOBA (hitters)

**Question:** are a hitter's results ahead of, or behind, the quality of his
contact?

- **wOBA** (weighted on-base average) values each outcome by its real run value.
- **xwOBA** (expected wOBA) is what that same contact — exit velocity and launch
  angle — *typically* produces, independent of where fielders stood or how the ball
  bounced.
- We regress the expected mark toward league average by the player's sample:
  `true_talent = (n · xwOBA + k · league_xwOBA) / (n + k)`, `k = 220`.
- The gap, in points of wOBA, is reported as luck: a hitter well below his contact
  quality is "owed a bounce"; one well above is "outrunning the bat."

**Gate:** ≥ 150 PA and a regressed gap of ≥ 22 points (individual) / 8 points
(team) before it's a story.

## Luck vs. skill: ERA − FIP (pitchers)

**Question:** is a pitcher's run prevention real, or is his ERA out over its skis?

- **FIP** (Fielding Independent Pitching) strips out everything but the three true
  outcomes a pitcher controls — strikeouts, walks (and HBP), and home runs:

  ```
  FIP = (13·HR + 3·(BB + HBP) − 2·K) / IP + C
  ```

- `C` is the **league FIP constant**, which scales FIP onto the ERA baseline so the
  two are directly comparable. We compute `C` from real league-wide totals every
  season (`C = lgERA − lgCore`), never a hardcoded value. For the current season it
  comes out near 3.10, as it should.
- The ERA − FIP gap is the pitching analogue of the hitter's luck signal: ERA above
  FIP is hard luck bound to improve; ERA below FIP is outrunning the peripherals.

**One detail that matters:** innings pitched is notation, not a decimal — `.1` and
`.2` mean one and two *thirds* of an inning. We convert to outs before dividing, so
the FIP denominator is right.

**Gate:** ≥ 30 IP, and an ERA − FIP gap of ≥ 0.50 runs.

## Change detection: did something actually shift?

**Question:** a hitter looks hot (or cold) lately — is it a real change or just
noise?

- We compare his most recent 15 games against the prior 15 games (on-base rate).
- The split is **pre-registered**: recent-vs-prior, fixed in advance. We do *not*
  search for the date that maximizes the difference — that would manufacture
  significance out of randomness (the classic streak fallacy).
- The two windows must be **statistically distinguishable**, not merely different.
  We run a pooled two-proportion z-test and report the probability the split is
  real; below ~80% confidence, there is no story.

**Honesty rule:** on-base rate over ~30 games is far short of where it stabilizes
(~460 PA), so a change is reported as *"results have shifted,"* never *"he's a
different hitter."* That's a difference the card states out loud.

### Contact-quality change (a deeper read)

On-base results mix in luck and defense. A second detector watches the **quality of
contact** instead: the expected wOBA of his batted balls (xwOBACON), split into a
recent vs. prior window of 50 batted balls each. Because a single batted ball's
expected wOBA is wildly variable (an out is near 0, a homer near 2), the two windows
are compared with a **Welch two-sample test**, and the bar stays conservative. This
answers a different question — *is he squaring the ball up better or worse?* —
independent of where the results have fallen.

## League control: is it him, or the whole league?

**Question:** a Padre's numbers moved — but did everyone's? This is the step almost
no one takes.

- For the same two calendar windows, we pull **every qualified hitter in the
  league** and measure how much the league drifted (weather, the ball, pitching
  quality all move the baseline).
- The control cohort is **non-team** — every league hitter *except* the Padres — so
  the player isn't compared against himself or his own teammates.
- We report his change **net of league drift**, and judge that residual against how
  much individual hitters normally vary window-to-window (the cohort's spread). A
  swing only counts if it's large *relative to normal player-to-player variation*.

**What this buys you:** "Player X is hot over 25 games" becomes "Player X is hot
*even after* the league got hot — and by more than normal variation explains." One
is a stat; the other is a finding.

## Compound claims: "one of N players with X and Y"

**Question:** is a player doing several rare things at once — and how rare is that
combination, really?

- A conjunction fires when one player clears the rarity floor on **two or three
  metrics that measure different things**. The count of players meeting every mark
  simultaneously is a real SQL count against the league, not an estimate, and it
  lands in the card's facts so the digit audit covers it — **with its denominator**.
  "One of 6" is not a claim; "one of 6 out of 209 qualified" is.
- **The membership threshold is fixed in advance** at the top 10%, never read off
  the subject's own weakest percentile. A cut fitted to the player makes his
  membership true by construction and turns the peer count into an artifact of
  where the line was drawn — move the line and the "finding" appears or vanishes.
- **Luck residuals never join a conjunction.** A wOBA − xwOBA gap is what's *left
  over* after skill is accounted for, not a skill. "Elite fielder who has also been
  unlucky at the plate" joins a talent to a coincidence, and the two have no
  relationship worth a card.
- Scope is the **season the comparison ran in**, not the era its source table
  spans. A 2026 leaderboard comparison is a 2026 claim even though Statcast
  reaches back to 2015.
- **Correlated metrics are collapsed first.** Exit velocity, max exit velocity and
  hard-hit% are three names for one skill. Chaining them would manufacture a
  guaranteed "only player in baseball" out of a single underlying trait, so metrics
  are grouped into families (contact quality, swing, plate discipline, speed,
  defense, expected outcomes, …) and only the strongest member of each family can
  join a conjunction.
- Members are capped at three. Past that a card stops being a story and becomes a
  stat dump, and the combined-rarity math (a geometric mean, which assumes rough
  independence) stops being defensible.
- The framing states the **rank**, not a verdict: "in the top 12% in both X and Y."
  A high xwOBA − wOBA gap means *unlucky*, not excellent, so a compound claim never
  calls a member "elite" — each metric's own label carries its meaning.
**Gate:** combined rarity (geometric mean) must itself clear the rarity floor —
two mediocre marks never add up to one good story.

## Splits and contrasts: the shape most findings take

**Question:** not "how good is he at this" but "how differently does he do it in
one situation versus another" — against lefties, against breaking balls, in the
zone versus out of it.

- **Rates come from pitch-level data**, with the denominator stated: chase rate
  is swings divided by *out-of-zone pitches*, whiff rate is misses divided by
  *swings*. A rate over the wrong opportunity set is a different stat wearing
  the same name.
- **A gap is ranked against the league's distribution of that same gap**, never
  against zero. Every hitter has some platoon split; day-to-day variance
  guarantees a nonzero number. What's notable is where a player's gap sits among
  everyone else's, so that's what we measure — which keeps the same ECDF and
  shrinkage machinery valid on a differential.
- **Both sides are printed, always.** A bare differential hides which term drives
  it: a wide swing-rate gap can come from elite restraint or from sheer
  aggression at strikes, and those are opposite stories about a hitter.
- **Both sides must clear a sample floor**, and the *smaller* side drives the
  reliability shrinkage — a "platoon split" over 30 pitches is noise wearing a
  narrative. A player who qualifies on only one side is dropped rather than
  counted as having no gap.
- **Not every metric crosses with every split.** Chase rate is *defined* on
  out-of-zone pitches, so slicing it by zone is incoherent and the engine
  refuses the combination rather than producing a confident empty number.
- **The gap's size is ranked, not its sign.** A chase-versus-zone split is
  negative for every hitter, so ranking the signed value would describe the
  widest gap in baseball as "narrower than 96%" — true of the number, backwards
  as English.

**Sample gates:** at least 60 pitches on each side, at least 40 players in the
gap distribution, and shrinkage toward the mean below each metric's
stabilization point (swing decisions stabilize much faster than contact
quality).

**One honest limit:** pitch-level data has been ingested for roughly 135 hitters,
not all of MLB. Every split claim says so — "wider than 95% of the 135 hitters
with pitch-level data" — because calling that group "qualified MLB hitters"
would describe a convenience sample as if it were the league.

## Career baselines: is he different from who he has been?

**Question:** every other lens compares a player to the league. This one compares
him to himself — the question a fan actually asks about a familiar name.

- The baseline is his own prior seasons (at least three, each with 150+ PA).
  Fewer than that and a "baseline" is one or two numbers wearing a average.
- **League drift is removed.** The whole league moves year to year — the ball,
  the rules, the pitching pool — so part of any career-vs-now delta is the era,
  not the player. We subtract the league's own move over the same span before
  claiming anything.
- The residual is judged against **how much players normally move season to
  season**, not against the player's own noise. A three-season personal standard
  deviation is far too unstable to divide by.

**Gate:** the cohort supplying that "normal movement" spread must contain at
least 30 players. Below that, the spread is an accident of a handful of
observations, and dividing by it manufactures large-looking changes out of
nothing.

**Status: currently silent, by design.** Our season-level batting table is
ingested per team, so the cohort is about twenty Padres a season and only a
handful carry enough prior seasons to contribute. Rather than compare a Padre
against a three-player spread — which would also mean comparing him to his own
teammates, the exact self-comparison the league-control rule exists to prevent —
the detector reports why it can't run. It activates on its own once league-wide
season data is ingested.

## The referee: a reasoning check before anything posts

Every gate above catches a **wrong number**. None of them catches a **wrong
argument** — an endpoint chosen after the fact, a survivorship-filtered
population described as "the league", a cause asserted without a control, or a
number that is genuinely rare and genuinely meaningless.

So before a card can be approved, a panel of five reviewers judges it, each
prompted to *refute* rather than approve:

| Lens | Looks for |
|---|---|
| Statistician | arbitrary endpoints, wrong denominators, survivorship, thin samples, multiplicity, correlated conjunctions |
| Causal skeptic | "because" / "since the change" with no control cohort; confounds |
| Coverage auditor | scope overreach, stale sources, Padres-only data passed off as league-wide |
| Editor | the "so what?" — trivial, tautological, or filter-artifact findings |
| Voice | jargon left ungloss'd, register drift, the banned AI tells |

**Any single BLOCK stops the card** — this is not a majority vote, because one
sound refutation is enough. Two or more revisions send it back. When a reviewer
is unsure, the causal and coverage lenses default to blocking and the others
default to their stated verdict: a wrong "first ever" costs more than a missed
post.

Two limits make this safe. The referee **may never compute, correct, or add a
number** — if it believes a figure is wrong its only option is to block and name
the defect, because the fix belongs in a detector, not in prose. And a rewrite
may rephrase but may not introduce a figure, since referee prose never passed the
digit audit.

Every rejection names a **failure mode** from a fixed vocabulary
(`arbitrary_endpoint`, `survivorship_population`, `causal_no_control`,
`scope_overreach`, `trivial`, …). Free text can't be counted; a controlled
vocabulary can, which is what lets the engine learn which kinds of claim keep
getting refuted. We also track the block rate of each lens, because a reviewer
that never blocks is a rubber stamp rather than a clean bill of health.

A worked example, from the first live run: a card reported that a player ranked
top-12% in both Outs Above Average and wOBA − xwOBA gap. Every mechanical gate
passed. The panel blocked it three ways — the 12% cut had been derived from the
player's own weaker percentile (so his membership was guaranteed), the two
metrics have no relationship to each other (glove and plate luck), and the scope
field claimed the Statcast era for a single-season comparison. All three were
real defects in the engine, and all three are now fixed. That is what this stage
is for.

## Multiplicity: correcting for the size of the daily search

Every scan day runs many comparisons at once (every metric × every lens × every
rostered player), and the more you look, the more "rare" things you find by chance.
We run **Benjamini–Hochberg** false-discovery-rate correction across the whole
day's battery and log how many hits it would drop.

One honest caveat: the engine ranks effect sizes over an empirical distribution, so
`1 − rarity` is a **p-value proxy, not a calibrated p-value**. BH over that proxy is
a multiplicity *check*, not a significance test.

There is a second, sharper reason it runs in advisory mode, and it's arithmetic
rather than caution. An ECDF over `n` players cannot resolve finer than `1/n` — a
player who beats everyone still only reaches `1 − 1/n`. So the smallest p-value the
method can produce is `1/n`, while BH requires the best result to clear `α/m`. With
135 players and a 46-comparison battery, that's 0.0074 against a threshold of 0.0011:
**the best hitter in baseball could not pass.** Enforcing it wouldn't be strict, it
would reject everything and look like a quiet day. The engine now refuses to enforce
a gate it can prove is unachievable, and says so in the log. Widening the ingested
population or narrowing the daily battery fixes this; lowering α does not, because
both sides scale together.

What we report instead is the **expected noise floor**: at a 0.85 rarity floor over a
46-comparison battery, roughly 7 hits are expected from chance alone. That's logged
every run. It's more useful than a binary gate, because it says plainly that a day
surfacing 8 hits has surfaced approximately nothing.

The enforced filters today are the rarity floor, the sample and stabilization gates
above, the referee, and a human approval step.

## Spatial cards: the rigor harness

Every spray chart, zone map, and pitch-arsenal card must state its denominators on
the card face — sample size (`n`), the coverage window, handedness split, park, and
point of view. A card that can't fill those fields does not render. Below each
visual's sample floor (e.g. 50 batted balls for a spray, 25–40 pitches per type for
an arsenal) it is labeled *illustrative, not predictive* — or not posted.

The coordinate math is standard Statcast: spray uses
`x = (hc_x − 125.42)·2.5`, `y = (198.27 − hc_y)·2.5` (the y-axis is screen-
inverted); pitch movement is feet × 12 for inches; home-run distance uses the
trajectory model's carry, never the straight-line landing point.

---

## Receipts: how we grade ourselves

Calibrated claims are only worth something if they're checked. The engine logs its
**falsifiable** calls — the luck detectors, which predict regression toward a
baseline — as dated predictions, then grades them once they mature.

- A prediction records the metric, the value now, where the peripherals say it's
  heading, and a due date (default 30 days out).
- At maturity we re-measure and score it **correct** (moved toward the target),
  **incorrect** (moved away), or **push** (didn't move enough to call).
- Only forward, falsifiable claims are logged. "What changed" and "is it him" are
  descriptions of the present, not predictions, so they aren't graded.

**The honesty band:** grading currently reads movement in a cumulative season stat,
which dilutes a 30-day window. So *no movement is scored a push, never a hit.* The
scorecard is designed to under-claim — a record you can trust is the entire point.

---

*Questions about a specific number on a card? The calculation is above. The data is
Baseball Savant (Statcast) and the MLB Stats API, used on an unofficial,
non-commercial basis.*
