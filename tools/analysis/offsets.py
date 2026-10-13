"""The per-beatmap offset a replay was played under, so its hit error can be read as timing.

Why it matters. Lazer adds the player's per-beatmap offset to gameplay time
(``ppy/osu/osu.Game/Beatmaps/FramedBeatmapClock.cs:91,120``, a plain ``FramedOffsetClock``,
so in map time and not scaled by rate). The player presses to the audio, so

    recorded hit error = raw timing + offset,   raw = hitError - offset

which is the same sign lazer's own calibration uses: ``computeSuggestedOffset`` returns
``currentOffset - median`` (``Screens/Play/PlayerSettings/BeatmapOffsetControl.cs:371``), i.e.
it moves the offset by minus the hit error it wants to cancel. One value covers every
difficulty sharing the audio file (``BeatmapOffsetControl.cs:233``).

Where the value comes from. No score records it, and realm keeps only the current one, so the
history is snapshots: ``build/beatmap-index-2026-09-11.json`` (the earliest realm copy), then
``build/offset-history.csv``, which the after-close sync appends to with each change bounded by
the two snapshot times either side of it, and ``build/offset-state.json``, the latest snapshot.

Status per replay:
  known         no logged change window contains the play; the value is what was in force
  ambiguous     a change window [after, before] contains the play, so either value is possible
  pre-snapshot  played before the Sep 11 snapshot; its value is assumed, never observed

Convention, from 2026-10-12. A window shows that an offset changed, never when, so the player
changes per-map offsets only at the end of a session (or ends the session and starts a new one
when a change cannot wait). A change logged in a window is therefore taken to have happened
after every play inside it: those plays ran under the old value and are "known", not
"ambiguous". Only windows opening at or after ``CONVENTION`` are resolved this way; earlier
ones keep the ambiguous status, since the opposite habit (change mid-session) was attempted
and not kept up.

The player's global audio offset is out of scope here: it is one setting, not per map, and is
also not in any score.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re

STATE = "build/offset-state.json"
HISTORY = "build/offset-history.csv"
FIRST = "build/beatmap-index-2026-09-11.json"
FIRST_TIME = "2026-09-11T19:48:00-0400"
# The offset-state snapshot taken when the end-of-session convention above was adopted.
CONVENTION = "2026-10-12T19:16:39-0400"

# Replay filenames carry local wall-clock time; the machine is US Eastern.
_STAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\)(?: \(\d+\))?\.osr$")
_EASTERN = dt.timezone(dt.timedelta(hours=-4))  # EDT; every logged change so far is in EDT


def _parse(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z")


def play_time(replay_name: str) -> dt.datetime | None:
    m = _STAMP.search(replay_name)
    if not m:
        return None
    day, hh, mm = m.groups()
    return dt.datetime.fromisoformat(f"{day}T{hh}:{mm}:00").replace(tzinfo=_EASTERN)


class Offsets:
    def __init__(self, root: str = "."):
        p = lambda f: os.path.join(root, f)
        first = json.load(open(p(FIRST)))
        self.first = {k.lower(): v.get("Offset", 0) for k, v in first.items()}
        self.first_time = _parse(FIRST_TIME)
        self.convention = _parse(CONVENTION)
        self.changes: dict[str, list[tuple[dt.datetime, dt.datetime, float, float]]] = {}
        if os.path.exists(p(HISTORY)):
            with open(p(HISTORY), newline="") as fh:
                for r in csv.DictReader(fh):
                    self.changes.setdefault(r["md5"].lower(), []).append(
                        (_parse(r["after"]), _parse(r["before"]), float(r["old"]), float(r["new"])))
        for v in self.changes.values():
            v.sort()

    def at(self, md5: str, replay_name: str) -> tuple[float, str]:
        md5 = md5.lower()
        t = play_time(replay_name)
        value = self.first.get(md5, 0.0)
        if t is None:
            return value, "ambiguous"
        if t < self.first_time:
            return value, "pre-snapshot"
        for after, before, old, new in self.changes.get(md5, ()):
            if t >= before:
                value = new
            elif t > after:
                # Changed at the end of the session, so the play ran under the old value.
                return value, "known" if after >= self.convention else "ambiguous"
            else:
                break
        return value, "known"


_DEFAULT: Offsets | None = None


def offset_at(md5: str, replay_name: str) -> tuple[float, str]:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Offsets()
    return _DEFAULT.at(md5, replay_name)
