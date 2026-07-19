# The xFriars Glossary — Stats, in Plain English

You don't need a stats degree to follow xFriars. Every advanced number we post
means something simple underneath. Here's the whole vocabulary, in one sentence
each, with a sense of what's good.

This is the casual companion to [METHODOLOGY.md](METHODOLOGY.md) — that one is *how
we calculate*; this one is *what the words mean*.

---

## Hitting

**wOBA** — One number for everything a hitter does at the plate, weighted by how
much each outcome actually helps a team score. *League average ≈ .320; .370 is
excellent.*

**xwOBA** ("expected wOBA") — What a hitter's contact *should* be worth, based on
how hard and at what angle he hits the ball — with luck and where the fielders
stood taken out. When xwOBA is much higher than wOBA, he's been unlucky and better
days are coming. *Average ≈ .320; .370 is excellent.*

**xwOBA on contact** — The same idea, but only counting balls he put in play. A
clean read on how well he's squaring the ball up, ignoring walks and strikeouts.
*Average ≈ .370; .450 is elite.*

**On-base percentage (OBP)** — How often he reaches base — hits, walks, and
hit-by-pitches all count. *Average ≈ .320; .370 is excellent.*

**Barrel%** — How often he hits a ball in the perfect speed-and-angle window that
almost always goes for extra bases. *Average ≈ 8%; 14%+ is elite.*

**Hard-Hit%** — The share of batted balls hit at 95 mph or harder. *Average ≈ 40%;
50%+ is elite.*

**Chase rate** — How often he swings at pitches outside the strike zone. *Lower is
better — average ≈ 28%, and the disciplined hitters live near 22%.*

**Strikeout rate (K%)** — The share of plate appearances ending in a strikeout.
*Lower is better — average ≈ 22%.*

## Pitching

**ERA** — Earned runs a pitcher allows per nine innings. The classic number, but it
includes luck and defense. *Average ≈ 4.20; under 3.20 is excellent.*

**FIP** — What a pitcher's ERA *should* be if you judge only what he controls —
strikeouts, walks, and home runs — and take luck and his defense out of it. When ERA
is far below FIP, the shiny ERA is likely to climb. *Average ≈ 4.20; under 3.20 is
excellent.*

## When we talk about "real"

**"Is it real?"** — Short stretches lie. Before we call something a trend, we check
whether the change is bigger than normal game-to-game randomness. If it isn't, we
say so.

**"Is it him, or the league?"** — Sometimes a whole month is hot or cold across the
sport (the ball, the weather, the pitching). We compare a player against the rest of
the league over the *same* dates, so a hot streak only counts if he's beating the
trend, not riding it.

---

*Every stat above comes from Baseball Savant (Statcast) and the MLB Stats API.
Numbers on our cards are rounded the way a broadcaster would say them.*


## Plate discipline (pitch-level)

**Chase rate** — how often a hitter swings at pitches outside the strike zone,
out of all the out-of-zone pitches he sees. Lower is better: it means he isn't
being tempted. Around 28% is average.

**Whiff rate** — how often a swing misses the ball entirely, out of all his
swings. Lower is better. A foul tip counts as contact, not a whiff.

**Zone contact** — how often he makes contact when he swings at a strike. Higher
is better; this is the "can he handle the pitches he should handle" number.

**Swing rate** — how often he offers at anything at all. This measures
aggression, not quality — a high swing rate is neither good nor bad on its own.

**Split gap** — the difference in one of these rates between two situations, like
against lefties versus righties. What matters isn't the gap itself (everyone has
one) but whether it's unusually large compared to other hitters. When we report a
gap we show both numbers, because a wide gap can mean great discipline *or* extreme
aggression, and those are opposite things.
