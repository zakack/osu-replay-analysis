# Interesting

An append-only log of leads, appended by agents mid-task and curated by hand. Nothing here is
a conclusion. It is a place to drop a thing that looked odd at the moment it looked odd, so
that deciding what to do about it can happen later, by a human, with time.

Agents append and never read. Grooming, deleting and promoting anything into `CLAUDE.md` is a
manual pass; entries sit here untouched until then.

Each entry is a date, the thing in a phrase, a pointer to the evidence, and optionally one
line on why it might matter:

    ## <date> — <what, in a phrase>

    `<where the evidence is>`

    <why it might matter, one line, optional>

No severity, no category, no next step — those are curation decisions, and an agent guessing
at them mid-task is friction that buys nothing. Nothing is validated and nothing is rejected.

Newest at the bottom. `.gitattributes` sets `merge=union` so parallel branches concatenate
instead of conflicting at the end of the file.

---

## 2026-09-19 — one click judgement in the best-play corpus disagrees with its header

`build/best/best-6473309535.osr` — top-list position 68, Super Nuko World [Insane] +HD, build 2026.401.0-lazer-linux

One object simulated Meh where the header says Ok. The only one in 198 replays, with no confounds around it, so it is the cheapest reproduction of the bucket that should be empty.

## 2026-09-19 — 120 of the top 198 plays were never exported locally

`build/best/manifest.json` vs `build/corpus.json`, matched on frame hash

Export rate falls monotonically down the top list: 37/50, 18/50, 15/50, 8/50. The reflex fires on plays that felt good, so the unexported ones are the runs that charted without feeling special.

## 2026-09-19 — one map holds 52 exports of the same geometry

`build/corpus.json`, grouped by BeatmapMd5 across the 198 top-200 maps

385 exports across 108 distinct maps, median 1, max 52. Fifty-two runs of identical objects by one player over time is a longitudinal series with every cross-map confound held constant by construction.

## 2026-09-19 — the server stamps a replay ~257ms after the client does

`build/best/*.osr` vs the matching lazer exports, .osr header `ticks` field

Server later in 76 of 76, floor 193ms, neither value on a second boundary. That gap is the submission round trip, sitting in every pair of files for free. Probably useless.

## 2026-09-19 — lazer replaying a replay reproduces the simulation, not the header, on a click

`build/oracle/9a59c02f967d4f686df74b29a7dcca9f.json` vs `build/verification-best.json` row for `best-6473309535.osr`

The differential oracle judges the same three Mehs at the same milliseconds as the simulation (1 divergence in 1724, and it is a spinner tick), while the header says one of them was an Ok. The residual is play-vs-recording, not port-vs-lazer, which the four causes in CLAUDE.md have no bucket for.

## 2026-09-19 — a clean 1-2-1 triple read out as Miss / Ok / Meh because a press missed a circle by 0.21 units

`build/best/best-6473309535.osr` at 69450ms; `tests/Tests/StackedPressCascadeTests.cs`

Three circles stacked at (204,14), tapped at offsets 0, -5 and -15ms. The cursor sat 36.703 units from the first circle's stacked centre against a 36.495 radius, so the press went to the second circle instead and notelock missed the first. Every hit error in the group is then attributed to the wrong object, and nothing about the read-out says so.

## 2026-09-19 — a slider head judged Miss 175ms before its own start time, with nothing hit at that instant

`build/best/best-6473309535.osr`, object 787, slider at 175081ms; oracle `TimeAbsolute` 174906

Both the simulation and the live game stamp the judgement at 174906, which is the moment of a press that landed on the head while 175ms too early for any window. Notelock is the only force-miss path that applies a result at an arbitrary time, and it only fires on a hit — no object was hit there. The route is unexplained; the totals are unaffected, so it has never had to be.

## 2026-09-19 — geometry rows are padded to map length, so "sample size" splits three ways

`build/geometry.csv` vs `build/corpus.json`

Every replay of a map emits exactly the same row count (rows = replays x objects, to the
unit), so rows carry nothing about how far a play got; only `hitError != ""` does. The
ranking flips depending which you count: Bass Slut leads by replays (52), The Pretender by
timed samples (42,377). And the longest maps are the least finished — there are no angels
here. [archangeloi.] is 36% timed, Save Me [Tragedy] 55%, Blue Zenith [FOUR DIMENSIONS] 65%
— so pooling by rows weights hardest toward the plays that were abandoned.

## 2026-09-19 — the variance study needs no replays at all

`build/best/manifest.json` accuracy fields; osu! API `/scores` and `/users/{id}/scores/recent?include_fails=1`

Per-map accuracy variance comes from score metadata, which every listing carries and which exists even for fails; map physicality comes from the `.osu` geometry. Neither side of "do high-physicality maps convert luck into pp" touches a replay, so the preservation bias that truncates every replay source does not apply to it.
