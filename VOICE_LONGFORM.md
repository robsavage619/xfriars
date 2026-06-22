# @xFriars Long-Form Voice Spec

Companion to [VOICE.md](VOICE.md). That spec governs short cards (lead with the
number, declarative, no scaffolding). Long-form is a *different register*: the
goal is a FanGraphs-grade deep dive that a casual fan finishes and understands.

## The register

A sharp friend who knows the numbers cold, explaining what's actually going on
over a beer — not a research paper, not a hype account. Depth and real numbers
stay; the clinical tone goes. If a smart fan who doesn't know wOBA could read it
start to finish and feel smarter, it works.

## Structure

- **Open on a scene, not the thesis.** A swing, a game, a number that jumped —
  something concrete a casual recognizes. Earn the abstraction.
- **Define on first use.** The first time a stat appears, define it in the same
  sentence. "His xwOBA — what his contact *should* produce, stripped of luck —
  is .410." Never assume the acronym.
- **One idea per section.** `##` headers are signposts, not decoration. A reader
  skimming the headers should get the argument.
- **Let figures carry the weight.** Prose points at what to notice in the chart;
  it doesn't recite the chart. "The gap in April is the whole story" beats
  listing every bar.
- **Land the takeaway.** End on what it means, not a recap. No "in conclusion."

## Rules that carry over from VOICE.md

- **Lead sections with the number when there is one.** The stat is still the news.
- **Coverage-bounded claims.** "since 2015" not "all-time" unless the data goes back.
- **Every number in the prose must be defensible** — sourced, and ideally also on
  a figure. Don't invent or round-trip a stat you can't point to.
- **Opinion only when earned.** A real finding earns a take; a tidy split doesn't.

## Banned tells (same list, long-form is no excuse)

Hype scaffolding ("let's dive in", "buckle up"), the pivot tic ("it's not just X
— it's Y"), rhetorical openers ("Did you know?"), wrap-up filler ("simply put",
"at the end of the day", "bottom line"), manufactured awe ("historic",
"remarkable", "stunning" — let the number do it), and em-dash-and-tricolon
cadence that reads as machine-written. One emoji max, and only if it earns it.

## Self-check before rendering

Read it aloud. If a paragraph sounds like a brand blog or an LLM, rewrite it. If
it sounds like the most knowledgeable person at the bar walking you through it,
ship it. Then run `pad article render <slug>` and read it again on the page —
length and rhythm read differently in the column than in the editor.
