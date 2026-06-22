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
