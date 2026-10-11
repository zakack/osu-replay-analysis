---
name: session-recap
description: Summarise the player's latest osu! (lazer) play session, or an earlier one, from the project's computed data - plays, accuracy against recent history, timing, quick retries, setup changes and the tapping-drift check. Use when asked for a session recap, "how did I play", "how did that session go", or a summary of a recent session.
---

# osu! session recap

You are writing a short recap of one play session for the player (zaksynack). All the
numbers come from one script. Your job is to **select and narrate**, not to compute
or to explain causes.

## Step 1: get the facts

Run from the repository root:

```sh
python3 tools/analysis/recap.py              # latest session
python3 tools/analysis/recap.py --session 2  # the one before it, and so on
```

It takes about 15 seconds. Read the whole output before writing anything. If it errors,
report the error and stop; do not guess numbers.

Do **not** run the corpus sync (`tools/corpus/sync.sh`), `ora` extraction commands, or
anything that writes to `build/`. The sync runs automatically when the game closes; if the
latest session has no plays listed, say the sync may not have finished yet.

## Step 2: what the output means

| Section | What it is | Read it as |
|---|---|---|
| header | start/end (local time), audio output, monitor profile, PipeWire settings | context only; mention output/monitor only if they differ from the usual (headphones, game1) |
| Plays | one row per saved replay | `rank F` = failed. `acc` = accuracy. `pp` = performance points the server awarded ("-" = none: failed, unranked, or not submitted). `hit-err` = median timing in ms, **positive = late, negative = early**. `UR` = 10 x SD of timing, lower is steadier; under ~100 is good for this player, over ~200 is a rough play. `aim(r)` = median distance from circle centre at the hit, in circle radii (0 = dead centre, 1 = edge). `miss` = misses |
| pp | the session's highest-pp play, any play that is now in the player's top 200, and total pp / global rank with the change since the last recorded total | **pp is the metric players care about most.** A play entering the top 200 leads the recap, with its position (`#1` is the best play on the profile). If the output says pp is unavailable (no network or credentials), say so in one line and carry on |
| Same maps, previous 90 days | today's best against earlier completed plays of the same difficulty | `[NEW 90-DAY BEST]` is worth leading with. "no completed play in the previous 90 days" means new or long-unplayed, not bad |
| Attempts | every time gameplay started, from lazer's own log, including quick retries and early quits that never became a replay | attempts much larger than saved plays, or many "under 20 s", means a lot of quick-retrying. The player is deliberately trying to quick-retry *less*, so few retries is good news |
| Per-map offset changes | offsets the player changed in-game during the session | recorded hit error includes the map's offset, so timing before and after a change is not comparable |
| Setup changes | rows from `setup-timeline.csv` dated in the 7 days before | **any comparison with earlier plays crosses these.** Read the text for exact times: a change made during the session splits it |
| Tapping drift check | whether the tapping hand's stream timing has regressed against the last 30 days | only `REGRESSION` matters. "no change" is the normal, good result. "cannot carry a CI" means too little stream data to say |
| Input traces | whether doubletap/cursor traces were recorded and lined up with the replays | mention only if missing (recording broken) or if the player asks |

## Step 3: write the recap

Rules:

- **Every number you write must appear in the script output.** Copy it; do not round
  differently, average, or derive new ones. If you want a number that is not there, leave it
  out.
- **No causes you cannot point to.** Do not explain *why* something happened (fatigue,
  warm-up, tablet area, "focus") unless the output shows it, such as a setup change at that
  time. Folklore about tablets, smoothing and hand position is exactly what not to write.
- **Setup changes before causes.** If a setup change falls in or just before the session,
  say that comparisons with earlier plays cross it, instead of crediting or blaming the
  player.
- Single plays are noisy. Say "best", "median", or "N plays" — never present one play as a
  trend.
- Keep it short: a headline line, then at most 6 bullets, then setup caveats. No tables
  unless asked.
- Use the player's vocabulary: map names as shown, "early/late", "UR", "quick retry".

Shape:

```
**<one-line headline: the most notable result; a new top play wins>**

- <pp: new top plays and their position, else the session's highest pp; total change if shown>
- <best results, new 90-day bests>
- <maps that went worse than their 90-day median, if any>
- <timing: early/late pattern, only if consistent across maps>
- <attempts / quick retries>
- <drift check, one line>

<setup caveat, if any>
```

## Example

From the output for the 2026-10-11 00:50 session (17 saved plays, 3 failed, 18 attempts):

> **Two new 90-day bests in a 64-minute session, with almost no quick retries.**
>
> - New 90-day bests on Wizard's Tower [Ultimate Magic] (92.94%, previous best 91.59%) and
>   AaAaAaAAaAaAAa [aAaAaaAaAaaA] (96.37%, previous best 95.28%).
> - Teen Titans Theme got six plays, peaking at 97.75%, just under the 90-day best of 97.88%
>   and well above that map's 94.42% median.
> - Reign of Fear [VI] came in at 86.09%, a little under its 86.50% median, and earlier than
>   usual (median hit error -5.0 ms, against -2.0 ms before).
> - 18 attempts for 17 saved plays, and only 1 under 20 s: barely any quick retrying.
> - Tapping drift check: no change.
>
> Setup: the OpenTabletDriver filter chain changed during this session (Saturn Multifilter,
> final order from 01:08), and the game has run at 1920x1080@165 since the previous session,
> so comparisons with earlier plays cross both changes.

(That example predates the pp section. With it, a new top play is the headline: on
2026-10-11 17:02 the recap should have opened with "98.24% on Twinkle Twinkle for 241pp, a
new #1 top play", which the version without pp missed.)

Note what the example leaves out: the per-play table, UR and aim numbers that did not stand
out, the input-trace line, and any explanation of *why* Reign of Fear was early.

## Going further (only if the player asks)

These read the corpus and print a report; they do not change any data. They cover all play,
not one session, and can take a minute or more. Run them without `--out`.

| Question | Command |
|---|---|
| tapping drift in detail | `python3 tools/analysis/drift.py latest --sessions 3` |
| late clicks on fast jumps | `python3 tools/analysis/lateclick.py` |
| strings of 50s in streams | `python3 tools/analysis/streams.py report` |

Data files, all under `build/` unless noted: `score-index.json` (every score),
`geometry.csv` (per object: timing, aim, geometry), `sessions.jsonl` (one line per session),
`offset-history.csv`, `lazer-logs/` (lazer's runtime logs), `traces/` (key and cursor
traces), and `setup-timeline.csv` at the repository root.
