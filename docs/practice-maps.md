# Practice maps: clipping a section into its own beatmapset

Feasibility note, 2026-10-11. Read against ppy/osu `c729c6d7a2`. Built as `ora clip-find`,
`ora clip-list` and `ora clip` (`src/Extract/Clip.cs`). Usage is in the `clip` skill
(`.claude/skills/clip/SKILL.md`).

**Verdict: feasible.** An imported `.osz` is an ordinary local beatmap. It takes mods, it
saves a score and replay on a pass, and nothing is submitted online. Each of those was a
limit of the editor's test play. Three rules have to be enforced in code. Stacking is the one
geometric trap, and it was measured.

## What lazer does with a local map

- **Replays save.** `Player.ImportScore` runs whether or not the score is submitted.
  `SoloPlayer.CreateTokenRequest` returns null when `OnlineID <= 0`, and
  `ShouldExitOnTokenRetrievalFailure => false`, so play continues without a token. The same
  rule applies as on any map: a pass imports automatically, and a fail imports only through
  the save-replay button.
- **Mods** are chosen at song select like on any map. That includes free rate.
- **Start and retry.** Gameplay starts at `min(0, firstObject - 2000)`. Skip seeks to
  `firstObject - 3000` (`DrawableRuleset.GameplayStartTime` minus `MINIMUM_SKIP_TIME`). If the
  clip keeps the full audio, every retry costs a skip press. The run ends after the last
  object, so there is no dead tail.
- **Delivery.** Settings → Maintenance → Import Files works (first clip, 2026-10-11). The
  command line also reaches the game over IPC, but give it an **absolute** path: a relative
  one is resolved against some directory other than the shell's, and the import fails with
  "not a valid archive". Run `/usr/bin/osu-lazer /abs/path/clip.osz` while the game is open. It goes over
  `ArchiveImportIPCChannel`, the same path as opening a file. It
  reached the game on the first try, so it works apart from path resolution. Do not use the `osu-lazer` on
  PATH: that is `tools/corpus/osu-lazer`, which forwards `"$@"` but also restarts OTD,
  switches the monitor mode and starts a second session log and trace pair.
- **The `.osz` is consumed.** After a successful import lazer deletes the archive it was
  given (`BeatmapImporter.ShouldDeleteArchive` is true for any `.osz`). `build/clips/` is a
  staging area, not an archive.

## Three rules the tool must enforce

1. **Strip `BeatmapID` and `BeatmapSetID`.** `BeatmapImporter.PreImport` handles an import
   whose set `OnlineID` matches an existing set by marking that set `DeletePending` and
   resetting its beatmaps' online info. A clip that carries the source's set ID would delete
   the source map from the player's library. Assert both are gone before writing the `.osz`.
2. **Give the `.osu` a unique filename and `Version`.** `LocalCachedBeatmapMetadataSource`
   matches on `checksum = MD5 OR filename = Path`. A clip with the source's filename would get
   the source's online IDs back after import. Import never sets `LocallyModified`. Only the
   editor save and external-edit paths do (`BeatmapManager.updateHashAndMarkDirty`,
   `save`, `ExternalEditScreen`). So a clip that got the IDs back would request a
   submission token against the ranked map. After the first real import, check realm for `OnlineID == -1` on
   the clip.
3. **Store provenance inside the `.osu`.** Every index here keys on MD5, so to the pipeline a
   clip is an unrelated beatmap. Write
   `clip:<source-md5>:<first-object>:<last-object>:<time-shift-ms>` into `[Metadata] Tags`.
   That is what lets clip plays pool with the source's bins and makes "did practice help"
   answerable. Include the shift field now, even before anything shifts time. Once padding exists (see
   below), also record which objects are *targets* and which are warm-up. Otherwise every
   clip play dilutes its own bin with lead-in objects.

## Stacking changes at the cut (measured)

`tools/experiments/clip-stacking`: 300 random osu! maps from the beatmap index, 4 random
clips each (30–100 objects). Each clip was a text filter of `[HitObjects]`, decoded by lazer
and diffed by `StackedPosition` against the same objects in the full decode.

| | |
|---|---|
| clips with ≥1 moved object | 101 / 1,004 (10%) |
| moved objects | 185 / 65,740 |
| shift | median 5.8 px, max 280 px |
| moved objects within 500 ms of an edge | 136 (12 at the leading edge, the rest at the trailing edge) |
| moved objects 0.5–4 s from an edge | 49 |
| moved objects more than 4 s from an edge | 0 |

The 280 px tail is one map, `5083c1084c688ae21109bfc6a47223d0` (WMFJ - Viral TikTok Video
[Bugs Bunny]): a 1/4 stream stacked 64 deep on a single point. Cutting it un-stacks the
whole run. That map accounts for 66 of the 185 moved objects. Without it the typical shift
is a few pixels, which is still enough to move a spacing bin.

Most shifts are at the trailing edge. Lazer's stacking pass walks backwards, so an object's
stack height depends on the objects *after* it. Dropping the objects past the cut un-stacks
the end of the clip. The leading edge moves too, through the slider-end case
(`StackHeight = objectN.StackHeight - 1`). A chain can carry the effect up to 4 s in.

**Rule:** after building a clip, decode it and diff it against the source decode, as the
spike does. Then pad lead-in and lead-out objects until every target object's
`StackedPosition` matches. The source decode is the oracle, and it is always available.
The stack threshold is `(int)TimePreempt * StackLeniency`, so it moves with AR under
HR, EZ and DA. The spike ran no-mod only. Either diff under the largest preempt the clip
could be played at, or verify for the mod set the clip was built for and put that mod set
in the tag. Do
not try to predict stacking: that would be porting `OsuBeatmapProcessor`.

## Open decisions

- **Text filter or `LegacyBeatmapEncoder`.** A text filter keeps the source's lines byte for
  byte, so slider definitions never pass through a re-encode, and lazer still does all the
  geometry. The encoder needs its own round-trip test (does it write unstacked `Position`?
  does it keep curve types?). Recommendation: text filter, run from the C# CLI so the stacking
  diff above runs in the same place.
- **Audio.** v1 keeps the full original audio and reuses the file. Lazer's store is
  content-addressed, so it adds nothing on disk, and the timing stays exact. Trimming with
  ffmpeg and shifting every time by −T removes the skip press but risks encoder-delay
  offsets, which would corrupt hit error. Measure that against the source's plays first. A
  synthesized click track from timing points avoids both problems and allows looping a
  section N times, at the cost of the music. Audio never leaves the machine either way, so
  the never-ship rule is not touched.
- **Composite "example of a pattern" sets.** Clips from different maps cannot share a
  difficulty, because CS, AR and OD are per beatmap and so is the audio. Make one difficulty
  per clip inside one set. Each difficulty can name its own `AudioFilename`.
- **Corpus hygiene.** Clip plays land in realm like any score. Anything that ranks or recaps
  plays should recognise the `clip:` tag rather than treat a 20-second clip as a full play.

## First clip (2026-10-11)

Twinkle Twinkle (Sped Up & Cut Ver.) [Mommy's Basement], objects 204–331: the 128-note 1/4
stream. No padding was needed. After import, realm shows the clip with `OnlineID -1` and set
`-1`, and the source is untouched (3919008 / 1901049, not pending deletion). All four plays
were stored, failed ones included. `scene` decoded the D play through the rebuilt index and
reproduced its header exactly (67/54/6/1). Note that `ScoreInfo.BeatmapHash` is the `.osu`'s
SHA-256, not its MD5.

## Collections: a set of maps as a playlist (2026-10-12)

`ora collection --name <n> [--maps md5...] [--out dir]` (`src/Extract/Collection.cs`). Lazer's
`BeatmapCollection` is a name and a list of beatmap MD5s, which is this project's join key, so
any query that yields maps is one import away from a playlist in song select. Without
`--maps` it takes every MD5 in stdin, so `ora clip-find … | ora collection --name …` works,
as does any CSV or findings output with an MD5 column.

There is no drop handler for `collection.db`. The only importer is lazer's own
`LegacyCollectionImporter`, reached through the stable import, so the command writes a folder
that passes as a stable install: `collection.db` beside an empty `Songs/`
(`LegacyImportManager.IsUsableForStableImport` accepts `osu!.*.cfg`, `Songs` or `Skins`).

- **Where to click.** Settings → General → *Run setup wizard*, the *Import from stable* step,
  point it at the folder, tick only Collections. The *Import collections from stable* button
  under Settings → Debug exists only in debug builds (`DebugSection`). The wizard's checkbox
  shows the collection count, read through the same importer, before anything is written.
- **The folder is fixed** (`build/collection/` by default) and overwritten on each run. Lazer
  keeps the chosen stable path for the rest of its session (`cachedStorage`), so the second
  import of a session needs no folder picking.
- **Import merges.** A collection whose name already exists gains the new maps and loses
  none. To narrow a collection, delete or rename it in lazer first.
- **Collections-only runs at once.** The collections step is chained after the beatmap import
  with `OnlyOnRanToCompletion`; with beatmaps unticked that task is already complete.
- **Not tried:** writing to `~/.osu` instead, which `OsuGameDesktop.getStableInstallPath`
  auto-detects when it holds `Songs/`. That would remove the folder picker for good, at the cost
  of a fake stable install in `$HOME`. `--out ~/.osu` does it.
- `CollectionTests` reads the file back through lazer's private `readCollections` by reflection.
  Maps absent from `build/beatmap-index.json` are counted on output; lazer keeps them in the
  collection and shows nothing for them.
