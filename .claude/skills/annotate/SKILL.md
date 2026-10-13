---
name: annotate
description: Write annotations (notes) onto a replay in the viewer -- a sentence pinned to objects, with its numbers filled in from the replay and playback easing to a stop on it. Use when explaining a play, a miss, or a finding to the player in the viewer, or when asked to annotate, mark up or narrate a replay.
---

# Annotate a replay

A note is a sentence and the objects it is about. You choose the objects and write the words;
`tools/viewer/build.py` decides when it appears, where it points, when playback stops for it,
and **every number in it**. Never type a number into a note. Cite it with a placeholder, and
the build fills it in from the replay with the same formatting the viewer's inspector uses. A
placeholder that would come out empty is a build error, so a note can't claim a value the
replay doesn't have.

The full format is in the docstring of `tools/viewer/notes.py`. Run everything from the
repository root.

## Step 1: read what you can cite

```sh
python3 tools/viewer/build.py --replay <path.osr> --describe 900-940
```

This prints one row per object: index, time, result, and every value a placeholder can cite
(`press`, `deepest`, `depth`, `gap`, `entered`, `left`, `aim`, `reach`, `inside`, `spacing`,
`heading`, `turn`). A blank cell is a value that doesn't exist; citing it is an error. Leave
out the range to get the whole map, but it is long.

Choose objects from these rows, or better, take them from a deterministic finding (an
`arrival.py` miss class, a `streamfail.py` run). Never pick objects by estimating a time.

## Step 2: write the notes

```json
{"notes": [
  {"objects": [914, 939], "anchor": 916, "point": "press",
   "text": "Cursor leaves before the button: deepest {916.deepest}, out at {916.left}, press {916.press}."},
  {"objects": [914, 939],
   "text": "Across the section the hands are independent ({914-939.rho})."},
  {"time": 199000, "until": 203000, "text": "The ending: three misses in four seconds."}
]}
```

- `objects` is one index or `[first, last]`, the same numbers the viewer and `ora clip` use.
- `anchor` is the object the pointer goes to (default: the last one). `point` is `object`,
  `press` (where the press landed, which for a miss is the stray press) or `deep` (where the
  cursor was deepest).
- `{N.field}` cites object N. `{A-B.field}` cites the pattern A..B: `press`, `deepest`,
  `reach`, `inside`, `gap`, `rho` (needs 8 objects), `misses`, `count`, `span`.
- Use `time`/`until` (ms of map time) only for a stretch with no objects to name: a section
  or a break. Those notes show as captions and don't stop playback unless you add
  `"stop": true`.

## Marks, labels and selectors

`text` is optional. A note without it is a **mark**: its objects are highlighted on the field
and playback doesn't stop. Use marks for bookmarks, or whenever the player wants objects shown
without commentary. Never add text the player didn't ask for.

`label` is a short name, up to 48 characters, with no placeholders. It is a tag beside the
objects, and the name the notes list groups by: notes sharing a label collapse into one row
with ◀ ▶ to step through them.

To mark every instance of a rhythm, **use a selector, never a hand-made list of objects**:

```json
{"select": {"run": {"snap": "1/4", "size": 3}}, "label": "3-note burst"}
{"select": {"run": {"snap": "1/4", "size": [5, 12], "circles": true}}, "label": "stream",
 "text": "Button {run.press}, {run.misses} missed."}
```

The selector picks the objects from the rhythm pass's constant-snap runs: one mark per run.
`size` counts tapped objects (circles and slider heads; slider ends belong to the rhythm but
aren't tapped). It is a number or `[least, most]` and defaults to 3 or more. `"circles": true`
keeps only runs where every tap is a circle. In a selector's text, `{run.field}` cites each
mark's own run with the pattern fields. A selector with no matches is a warning, not an error,
because a map with no 1/8 runs is not a mistake.
- Name the hand whenever you say early, late or rush: "cursor early", "button late". "Late"
  on its own once described a cursor-rushing miss backward.

## Step 3: check, then build

```sh
python3 tools/viewer/build.py --replay <path.osr> --notes notes.json --check
python3 tools/viewer/build.py --replay <path.osr> --notes notes.json --auto 5 --out build/viewer/lap.html
```

`--check` reports every problem at once, each with what to use instead. When the file is
valid, it prints each note's stop time and the raw value behind every placeholder. Read those
values: they are what your sentence will say. Fix and re-check until it is clean.

## How notes behave

Playback eases to a stop just after the last event a note cites, and holds until the player
presses play. If the note cites `{916.press}` and that press is a stray at +28 ms, playback
rests once the red marker is down. A note and a miss at the same moment stop once, with the
note shown. The player can turn note stops off with "Stop on notes"; while a selection is
looping, nothing stops.
