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
