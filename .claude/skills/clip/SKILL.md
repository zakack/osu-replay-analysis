---
name: clip
description: Cut a section of an osu! beatmap (a stream, a jump section, a pattern the player keeps missing) into its own practice beatmapset (.osz) that the player imports into lazer, where mods apply and replays are saved, unlike the editor's test play. Use when the player asks to clip, cut out, isolate or practise part of a map, or when a finding needs a concrete example of a pattern to practise.
---

# Clip a map section into a practice beatmapset

The `ora` CLI does all the work. You choose **which objects** to clip and **where the
lead-in starts**, using object indices that the CLI prints. Never estimate a boundary
from a time you remember or worked out yourself. Background and the three import rules the
command enforces are in `docs/practice-maps.md`.

Run everything from the repository root. `ora` below means:

```sh
dotnet run --project src/Cli -c Release -- <command> ...
```

## Step 1: find the beatmap

```sh
ora clip-find 3919008                    # online beatmap id (the number after #osu/ in a URL)
ora clip-find "twinkle twinkle basement" # or text from "artist - title [difficulty]"
```

Each match prints its MD5 (used by every later step), its online id, and its name. With no
match, the map is not in the player's lazer library. Say so and stop. With several, pick
the difficulty the player named, or ask.

## Step 2: find the section

```sh
ora clip-list <md5>                  # breaks, and every run of 8+ objects at one snap
ora clip-list <md5> 50000 56000      # every object between two times, one per line
```

A run line reads `204-332  54732-65641 ms  129  1/4  176bpm  128/1/0`: indices 204 to 332,
129 objects with 1/4 gaps, of which 128 are circles, 1 is a slider and 0 are spinners. Neighbouring
runs share their boundary object. The object listing shows each object's index, time, type,
snap from the previous object, position and new combo.

Use the run list to find what the player described ("the long stream"). Then list the objects
around both ends to choose exact boundaries. Usually a run ends on a different object type,
such as a slider after a stream. Decide whether that object belongs in the clip, and say
what you decided.

## Step 3: choose a lead-in

A lead-in is usually best when one is available. The player gets the rhythm and the flow into
the section, not a cold start three seconds after a skip. Aim for **about two bars** before
the first clip object (one bar = beats per bar × beat length; 2 bars at 176 BPM in 4/4 is
2,727 ms). Prefer starting on a new combo or the start of a phrase near that point. Then:

- **No lead-in if nothing is there to lead in with.** If the clip starts the map, or follows
  a break, use no lead-in. Never reach back across a break: nothing before a break leads into
  what follows it. The CLI warns if your lead-in contains one.
- **A spinner that leads into the clip is the lead-in.** Start the lead-in at the spinner,
  and take nothing before it.
- Otherwise go back about two bars, never further. A lead-in is warm-up, not a second clip.

The lead-in is recorded separately from the clip, so the analysis can tell its objects apart.
Never widen the clip range itself to include warm-up.

## Step 4: build it

```sh
ora clip <md5> <first> <last> <label> --lead-in-from <index>
```

`<label>` is one or two words for song select ("stream", "jumps", "kiai"). Leave out
`--lead-in-from` for no lead-in. Example (the first clip made, 2026-10-11):

```sh
ora clip 52b8eac3fdb00c9be27c27e72bbf900d 204 331 stream --lead-in-from 193
```

The command:
- removes the beatmap and set online ids, so the import cannot delete the original map;
- gives the clip a distinct difficulty name and filename, so it is never matched to the
  ranked map;
- writes `clip:<source-md5>:<first>-<last>:<written-first>-<written-last>:0` into Tags;
- decodes the clip and checks that every played object sits exactly where the source puts it
  under NM, HR and EZ. If a cut changes stacking, it adds objects outside the range until the
  positions match, and says so;
- writes the `.osz` (the clip's `.osu` and the source's audio) to `build/clips/` and prints
  its absolute path.

If it errors, report the error. Do not work around it by editing the `.osu` or `.osz`.

## Step 5: hand it over

Give the player:
- the difficulty name and what it contains: clip objects, lead-in (or why there is none),
  and the time range;
- the absolute `.osz` path;
- how to import: **Settings → Maintenance → Import Files** in lazer. A command-line import
  also works with an absolute path, but use `/usr/bin/osu-lazer`, not the `osu-lazer` on
  PATH. That one is the session wrapper: it restarts OpenTabletDriver and starts another
  session log;
- that the song plays from the start: press skip to jump to 3 s before the first object, on
  every retry.

**Lazer deletes the `.osz` after a successful import** (`BeatmapImporter.ShouldDeleteArchive`
covers every `.osz`, wherever it was opened from). So `build/clips/` holds clips not yet
imported, not a record of what was imported. The record is realm, plus the `clip:` tag inside
each imported `.osu`. A missing `.osz` means it was imported, not that the build failed. To
get it back, rebuild it with the same arguments.

The difficulty name records the clip range and the lead-in start. **Importing a clip with the
same name does not replace the earlier one.** Lazer matches an import to an existing set only
by content hash (`RealmArchiveModelImporter.CheckForExisting`). A clip has no online id, and
any change to its contents changes the hash, so the import adds a second set that looks the
same. Even a rebuild with the same arguments may not be byte-identical once the code has
changed. So:

- If the player wants a fresh clip **beside** the old one, give it a different label.
- If the player wants it to **replace** the old one, keep the name, and tell them to delete
  the old set in song select before importing. Their scores on it stay in realm (scores
  outlive their beatmaps), but each score stays tied to the old version's content, not the
  new one's.

## Several maps to practise rather than one section

When the request is a set of whole maps (every map carrying a cell, a mapper's square maps),
use `ora collection --name <n>` instead of clipping; it pipes in any output carrying MD5s
and prints the import path. `docs/practice-maps.md` has the details.

## After the player has played it

Plays on a clip are ordinary local scores. Pass and fail are both kept, and nothing is
submitted. To bring them into the pipeline, rebuild the index (`ora index`). After that,
`ora scene <replay>` and the other replay commands find the clip by its MD5 like any map. In
realm, `ScoreInfo.BeatmapHash` is the `.osu`'s SHA-256, not its MD5. To compare clip plays
with plays of the full map, map objects back with the `clip:` tag: clip object k is source
object `written-first + k`.
