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

## 2026-09-19 — the daily corpus contains none of the map genre the variance hypothesis is about

`build/daily/room-*.jsonl` meta joined against bulk `/beatmaps` — 38 assignments resolved

Zero TV-size, nightcore, cut-version or sped-up titles; median playcount 36,874 against the hundreds of thousands that define a farm map; only 3 of 38 meet four of six slop markers and none is a jump map. The curation panel selects on quality rather than popularity, so the daily is not an attenuated sample of aim-heavy maps, it is an absent one. Only the firehose reaches that population.

## 2026-09-19 — daily star rating is a fixed weekly ramp, Thursday to Wednesday

`build/daily/room-*.jsonl`, 30 consecutive assignments grouped Thu-start; osu! wiki Gameplay/Daily_challenge

3.13 / 3.74 / 4.33 / 4.84 / 5.41 / 5.75 / 6.23 by weekday, monotonic within all five archived weeks. So star rating is perfectly confounded with day of week, and day of week decides who shows up — any difficulty effect in this corpus is entangled with population composition and cannot be identified. Length is NOT scheduled and varies freely, which is what leaves the length question answerable.

## 2026-09-20 — S-curve handedness: null on misses, a hint in timing, sample too small to settle

`build/rhythm.csv` joined to `build/geometry.csv` on (replay, time); nomod 1/4 runs, reversal points bent on both sides

Zak's standing intuition is that an S-curve is harder in one mirror orientation than the other. Miss rates say no — 2.61% vs 2.72%, but that ratio rests on 9 misses against 12. Timing says maybe: CW-first is hit 24.89ms off against 21.98ms for CCW-first. S-curves are 786 notes out of ~26,000 stream notes, about 3%, so the question needs accumulation rather than cleverness. Re-run as: reversal points, bent both sides, split by entry sign, compared on |err| not misses.

## 2026-09-20 — no dead quadrant, on a well-powered null

`build/scene/zaksynack*.json` — 79 replays, 62 maps, 48,103 objects with absolute x/y

Miss rate spans 2.44-2.60% and |err| spans 16.44-17.06ms across the four playfield quadrants, on ~12,000 objects each. That rules out the least fixable explanation for any handedness effect: it is not where on the tablet the pattern sits. Two sharper tests remain unrun — quadrant restricted to 1/4 runs, where a speed-dependent effect would not be diluted by slow objects, and the direction of aim error as a vector (`hx`/`hy` minus `x`/`y`), which would show a systematic pull. Both need `ora scene` run over more than 79 replays.

## 2026-09-20 — stream 50s come from autocorrelated drift, not press-attribution desync

`build/rhythm.csv`, nomod 1/4 runs 6+ notes, error expressed in note-intervals

A one-object desync would spike the error distribution at exactly +1.00 intervals. It does not: 1.26% sit there against 1.19% at -1.00, symmetric, no excess late mass. But an early tap predicts the next tap being early by -0.484 intervals against +0.051 otherwise, so taps run in streaks. At 190bpm half an interval is ~38ms, past the 34.5ms Great window, which produces exactly the observed run of 50s from sustained earliness rather than from misattribution. Matters because drift is trainable and a mechanical desync would not be.

## 2026-09-20 — one map, two failure modes, and the curvature call was exact

`build/scene/zaksynack*tenderly*.json` (15 attempts) joined to `build/geometry.csv` signedAngle

look at me tenderly splits cleanly. The sharp sections Zak calls "bo peep" hooks measure 105.4 and 122.9 degrees mean angle against 151.4 in the 4:30 endurance run, and only 38%/55% of their objects are near-straight against 84%. He called that from feel before it was measured. The sections then fail differently: 3:15-3:25 has the map's highest MISS rate at 3.15%, five times the endurance run's 0.64%, while the endurance run bleeds accuracy instead (136 fifties, timing spread widening to 18.62ms against ~14ms elsewhere). Runs end at the hooks; runs finish worse because of the endurance section. Per-object the hooks are worse, in aggregate the endurance run costs more, and both readings are true at once.

## 2026-09-20 — two bo peep sections on one map fail in opposite ways: one aim, one drift

`build/scene/zaksynack*tenderly*.json` frames interpolated to each object's due time, 15 attempts

At 3:15-3:25 a miss has the cursor a median 1.91 radii from the circle (35% inside) while the three preceding notes are hit at -1.57ms -- perfect rhythm, cursor absent, miss registered +81ms later as the window expires. At 2:25-2:45 the same-looking pattern fails the other way: cursor on the circle (0.77 radii, 62% inside) and the lead-in notes +25.38ms late. Successful hits sit at 0.40 radii everywhere. So "I miss the hooks" is two distinct defects wearing one name, and only one of them is the drift mechanism. Explains why it is subjectively opaque: a rhythm error has a feel, a cursor being elsewhere while the hands keep time does not.

## 2026-09-20 — tap rhythm follows cursor deceleration, and it is the strongest effect measured so far

`build/scene/zaksynack*tenderly*.json` frames; cursor speed over 60ms windows either side of each object, vs hit error

In the 2:25-2:57 hook passage, mean hit error runs -1.83ms when the cursor is accelerating hardest to +10.92ms when decelerating hardest, monotone across five quintiles, corr +0.227. A 12.75ms swing -- larger than curvature (+1.2ms), chirality (2.9ms) or stream position. Zak has reported feeling this for years without being able to name it.

Section-specific: the 4:30 endurance run is flat (-1.7 to -0.1ms, corr +0.031), whole map sits between at +0.148. And it is largely NOT the angle -- corr(straightness, deceleration) is only -0.057 in that passage against -0.152 map-wide, so deceleration and curvature are separate channels there and deceleration is the stronger one. The earlier +12.34ms hump in the 132-155 degree band was angle acting as a partial proxy for this.

Mechanism: the passage forces repeated deceleration, tap rhythm tracks the hand rather than the beat, ~11ms late lands on the slowest objects, stacks onto drift already running, and a note falls past the window with the cursor sitting on it. Caveat: one map, 15 attempts, and vin-vout over 60ms windows sits near the acceleration boundary CLAUDE.md warns about. Corroborated by being monotone over five buckets and section-specific; settle it corpus-wide.

## 2026-09-20 — the deceleration coupling is universal, and precision is the whole gap

`build/glory/` — 14 nomod top-50 replays on Glory Days [Maki's Extra] vs zaksynack, same map, same mods

The coupling between cursor deceleration and late tapping is NOT a skill deficit. The board's range is +0.149 to +0.634, bracketing zaksynack's +0.381, and corr(player accuracy, coupling) across the board is +0.005 — dead zero. Two top-50 players couple harder than he does. Nobody, including him, missed a single hairpin object.

What separates him is precision, and it is not pattern-specific: error sd 23.42ms against a board median of 8.43, with the entire top fourteen inside a 6.93-9.13ms band. Same conclusion the accidental Pretender corpus reached from a different direction — the gap is consistency, not bias, and not geometry. Retracted on the strength of this: a practice recommendation to "hold tap rhythm independent of cursor speed", which the best players on the map demonstrably do not do either.

## 2026-09-20 — NoFail usage tracks daily difficulty, so the hard boards are the LEAST truncated

`build/daily/room-*.jsonl` — 51 archived dailies with 300+ scores, mods per score row

Median NF usage runs 3.9% at SR under 4, 8.5% at 4-5, 15.1% at 5-6 and 23.5% above 6, with corr(star rating, NF) = +0.73 across 51 days. The tail reaches 37.5% on a 6.13 and 32.4% on a 6.84 — on hard days a third of the board refuses to let the run end.

This reverses an earlier note in this log. The daily boards were written off as truncated at the fail threshold "hardest on exactly the hard maps"; the opposite is true, because NF rises with difficulty. The population of runs that fall apart lives in the Wednesday boards and is already archived. Caveat: an NF run is a player choosing a different contract, not a random sample of would-be failures — many pass fine and take the safety anyway — so it is a partial window, not a clean one. Still far closer to genuine play than an RX run, which changes the input model entirely and scores zero pp.

Also a methodology lesson: the 5% figure that prompted this came from a single map at SR 3.76, third from the bottom of the ramp. One map sampled at the easy end of a scheduled gradient reported the floor as if it were the rate.

## 2026-09-20 — matched-skill peers are a third reference corpus, and the best-controlled one

`build/friends.json`, `build/friends-best.jsonl` — 56 friends inside #120k-220k, 11,200 scores

168 maps in Zak's own top-200 are also in a peer's, 105 with five or more peers and 40 with ten or more. Best covered: Ai no Sukima [Radiance] with 39, Mizuoto to Curtain [Lucid] with 30, Marshmary [Horizon] with 30. Their accuracy clusters 96-99% against his 96.86%.

This is the control the other two corpora cannot be. A map's top-50 board is four digits above him so every difference is confounded with being far better -- the Glory Days comparison came back "their error sd is a third of yours" and nothing more specific. The daily boards span the ladder but ppy assigns the map, so nothing is shared with his own history. Peers are matched on skill AND choose the same maps, which makes any shared map a controlled comparison for free. Only the friends list needs user auth; everything downstream is public.

## 2026-09-20 — map overlap with better players recovers through rate mods, confirmed at #1

`build/friends-best.jsonl`; mrekk's top 200 via `/users/{id}/scores/best`

mrekk's base-map difficulty falls monotonically as he stacks mods: nomod median 10.07 SR, HR-only 9.18, DT 7.09, DT+HR 6.85 (reaching 5.90). 17 of his 200 sit inside zaksynack's 5.0-6.5 nomod range and ALL 17 were played with a rate mod — the overlap is 100% mechanism, 0% coincidence.

So overlap with stronger players never vanishes, because HDDTHR keeps dragging their base-map requirement back down. What should vary is volume, and locating the peak is the open question: Zak predicts 4-digit. Shared-map counts by rank so far run 4 at #25k, 24 at #84k, ~24 median in the #120-180k band, 9 at #300k — overlap peaks near one's own rank and decays BOTH directions, so "better players share less" was wrong; players unlike you share less, either side.

Unfinished: `build/above.log` was mid-pull of 159 friends above #120k when the session ended. Re-run `/tmp` script or `tools/reference/friends.py --low 0 --high 120000`. The test needs shared-count AND modded-share to rise together at the same rank; shared count rising on nomod maps would be taste convergence instead, and the in-band modded share swings 0-82% on personal preference, so the signal must clear real noise.

## 2026-09-20 — lazer kept every completed run; the export corpus was 7% of it

`ora scores --export build/replays` — 17,500 osu! replays, 3,281 distinct beatmaps, 2013-06-28 to today

The hand-export corpus was 1,259 files. Everything else was already on disk in the file
store, unreachable only because realm holds the score-to-file mapping. Also: every score in
realm has `Passed = true`, so the "no failed runs" limit is structural, not a sampling gap.

## 2026-09-20 — the click-mismatch bucket is not empty, and the 86 in it share a signature

`build/verification.json`, Outcome=Mismatch on a modern client with Unjudged=0 and HeaderShortfall=0

86 of 10,810. Net across them: Great -138, Meh +156, Miss -32, Ok +14, and 48 of the 86 move
exactly one object. Great -> Meh skips a whole window, which no hit-window change produces.
Spread over 60 maps and every client version, and the same map reproduces fine on most runs
(4 bad of 63 on tenderly), so it is run-specific rather than map- or version-specific. Reads
like the Super Nuko World press-attribution cascade as a population rather than a one-off.
