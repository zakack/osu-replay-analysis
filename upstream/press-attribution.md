# Draft issue for ppy/osu — review before posting

**Title:** Replaying a replay can reach different click judgements than the play recorded — press attribution, not tracking

---

## Summary

It is generally understood that replaying a lazer replay reproduces every click judgement
exactly, and that only tracking-dependent judgements (slider tails, ticks) are unreachable.
#28744 is usually read that way, and the reasoning is sound as far as it goes: the recorder
takes an extra frame whenever a button changes state, so a press's *time* and the cursor
position *at* that press are both recorded rather than interpolated.

Measuring it over a corpus of 17,236 lazer replays, that turns out not to hold. Click
judgements do not reproduce either, at a rate of roughly 1 in 100 completed plays.

The press time survives. **Which object the press is attributed to does not.**

## What was measured

Every replay was played back through lazer's own gameplay stack, hosted headlessly, with the
judgements recorded object by object, then compared three ways: the `.osr` header (what the
original play scored), the live game's playback, and a separate reimplementation.

Restricting to plays that ran to completion on a client at or after `2025.710.0` — so that
the floored hit windows of `0f078ee550` applied to both the play and the playback:

| | |
|---|---|
| completed plays on a post-`2025.710.0` client | 9,114 |
| where playback reaches different click counts than the header | 85 (0.93%) |

Of 86 examined through the headless host, **85 had the live game disagreeing with the header
its own play wrote.** The net drift of playback against those headers:

```
Great  -137     Ok  +11     Meh  +157     Miss  -31
```

## Why this is not the hit-window change

The obvious candidate is `0f078ee550`, which moved every window edge half a millisecond off
the whole-millisecond grid that replay frame times sit on. Two things rule it out.

Every score here was set on a client at or after the release carrying that commit, so both
the play and the playback used floored windows.

And the drift has the wrong shape. Moving a window edge moves a judgement **one step** — a
Great just outside the new edge becomes an Ok. Here the mass moves Great to **Meh**, skipping
the Ok window entirely: Meh gains 157 while Ok gains 11. No edge adjustment produces a
two-step move.

## Mechanism: unknown, with one candidate ruled out

The obvious explanation is that press *attribution* drifts. `DrawableHitCircle` registers a
press only while `IsHovered`, and during playback the cursor path between recorded samples is
interpolated, so if hover were evaluated against a stale position — the last update before the
press rather than the press itself — a press could land on a neighbouring object, and the
intended one would be judged later and worse. That fits the Great-to-Meh direction exactly.

**It has been tested and it does not hold.** Playing replays through the headless gameplay
stack at two playback rates, giving 5x to 35x finer sampling of the same interpolated path,
produced identical press judgements on all of 2,881 across six replays — including replays
that are themselves in the 85. A stale-hover mechanism predicts cadence dependence. There is
none.

So press judgements are stable under *how finely playback samples*, and still differ from what
the play recorded. Whatever the cause, it is a difference between the play and the file rather
than a sensitivity in playback, and it is not explained here.

## Limits of the measurement

- **85 is a lower bound.** The 86 sent to the headless host were pre-selected as replays where
  an independent reimplementation disagreed with the header. Replays where that
  reimplementation *agreed* with the header but lazer's playback did not would not have been
  caught. The true population is at least this and may be larger.
- **One player's corpus** — 17,273 replays over 3,239 beatmaps, one account, mixed
  difficulties. Not a cross-section of the playerbase.
- The 0.94% is a rate of affected *replays*. Within an affected replay the drift is usually
  one or two judgements out of several hundred.
- Scores set with mods that move the drawable (Depth, Magnetised, Repel) were excluded, since
  hit detection follows the rendered position for those and the comparison means something
  different.

## Why it might matter

Mostly because the current understanding shapes what gets trusted. "Counts on the results
screen can differ from counts when you watch the replay back" is a known and reported player
experience, and the standing explanation attributes it to tracking. If a measurable share is
actually press attribution, then replay playback is not a faithful record of a play in a way
that is independent of sliders entirely, and a fix would live somewhere quite different.

## What would settle it

A synthetic case: a beatmap built in code with two circles close enough that the hover regions
nearly touch, and a replay whose cursor crosses between them near a press. If playback at two
different sampling cadences attributes that press to different objects, the mechanism is
confirmed using only first-party code and no third-party beatmap. This is buildable and has
not been built yet.
