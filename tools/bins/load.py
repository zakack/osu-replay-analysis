"""Reading the two extraction CSVs into ``Observation`` records.

The geometry pass and the rhythm pass were written at different times and emit one file
each, keyed on the same object but agreeing on nothing else: geometry is one row per hit
object, rhythm is one row per *click opportunity* at three different quantisation
tolerances. Joining them is this module's only real work, and it is done once per source
so the streaming loop below stays a single pass over the larger file.

Two sides come through the same code path deliberately. The comparison in ``compare`` is
only meaningful if the player and the reference were filtered, joined and dropped by
identical rules, and the cheapest way to guarantee that is to have one implementation and
a ``Source`` that says where the files are.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass

from .schema import DEG, ERAS, Observation

# Lazer's export filenames end in the local wall-clock time of the play. It is the only
# date the pipeline has - the .osr header's timestamp never reaches these CSVs - and it
# is what the era strata are cut on.
TIMESTAMP = re.compile(r"\((\d{4}-\d{2}-\d{2})_\d{2}-\d{2}\)\.osr$")

# The rhythm pass emits every object at three tolerances so the snap quantisation could be
# swept without re-running extraction. The sweep is over; 0.02 won.
TOLERANCE = "0.02"

# An object the shim never judged, because the replay ended before the map did. Carrying
# these would put tens of thousands of non-plays into every cell's denominator, which
# reads as a miss rate and is really an abandoned map.
UNJUDGED = "Unjudged"


@dataclass(frozen=True)
class Source:
    """Where one side's files live, and which side it is."""

    name: str          # "player" | "reference"
    geometry: str      # path to a geometry CSV
    rhythm: str        # path to a rhythm CSV
    corpus: str        # path to a corpus JSON
    manifest: str | None = None   # reference side only


PLAYER = Source("player", "build/geometry.csv", "build/rhythm.csv", "build/corpus.json")
REFERENCE_NM = Source("reference", "build/geometry-reference-nm.csv",
                      "build/rhythm-reference-nm.csv", "build/corpus-nm.json",
                      "build/reference/manifest.json")


def _num(text: str) -> float | None:
    """An empty cell means the extractor had nothing to say, which is not zero. Angles,
    spacing and hit error are all legitimately zero, so conflating the two would move
    real observations into the wrong bin rather than merely adding noise."""
    return float(text) if text else None


def _era(basename: str) -> str | None:
    """Which input-regime stratum a local replay belongs to, by date.

    ``None`` covers both replays with no timestamp in the name and replays older than the
    first era - the regimes were measured over the recent corpus and saying nothing about
    a 2018 play is more honest than extending the earliest bucket backwards.
    """
    match = TIMESTAMP.search(basename)
    if match is None:
        return None
    day = match.group(1)
    for name, lo, hi in ERAS:
        if lo <= day < hi:
            return name
    return None


def _identify(source: Source) -> tuple[dict[str, str], dict[str, str], dict[str, str | None]]:
    """replay basename -> beatmap md5, player, era, for the replays this source can use.

    The reference side is gated here rather than downstream: a leaderboard entry played
    under mods has hit errors in different units and a different clock rate, so it is not
    a comparison, it is a category error. Only ``filter == "NM"`` entries with an empty
    mod list survive.
    """
    by_file: dict[str, dict] = {}
    if source.manifest is not None:
        with open(source.manifest) as f:
            by_file = {entry["file"]: entry for entry in json.load(f).values()}

    md5s: dict[str, str] = {}
    players: dict[str, str] = {}
    eras: dict[str, str | None] = {}

    with open(source.corpus) as f:
        for record in json.load(f):
            name = sys.intern(os.path.basename(record["Path"]))
            if source.manifest is not None:
                entry = by_file.get(name)
                if entry is None or entry.get("filter") != "NM" or entry.get("mods") != []:
                    continue
                who = entry["username"]
                era = None
            else:
                # "zaksynack playing Artist - Title (Mapper) [Diff] (date).osr". A dozen
                # replays in the local corpus were imported from elsewhere and carry no
                # such prefix; the stem is the only name they have.
                head, sep, _ = name.partition(" playing ")
                who = head if sep else name.removesuffix(".osr")
                era = _era(name)
            md5s[name] = record["BeatmapMd5"]
            players[name] = sys.intern(who)
            eras[name] = era

    return md5s, players, eras


def _runs(source: Source, wanted: set[str]) -> dict[tuple[str, float], tuple[str | None, int, int]]:
    """(replay, time) -> (snap, run index, run size) for every click the rhythm pass saw.

    ``wanted`` is applied while building rather than after, because the reference rhythm
    file is 233MB and a table of the whole thing is several times the size of the slice
    any one comparison needs.

    Objects with ``clickIndex == -1`` were never clicked and carry no position within a
    run, so they are not join candidates at all.
    """
    table: dict[tuple[str, float], tuple[str | None, int, int]] = {}
    with open(source.rhythm) as f:
        for row in csv.DictReader(f):
            if row["tolerance"] != TOLERANCE or row["clickIndex"] == "-1":
                continue
            replay = sys.intern(row["replay"])
            if replay not in wanted:
                continue
            key = (replay, round(float(row["time"]), 3))
            table[key] = (row["snap"] or None, int(row["runIndex"]), int(row["runSize"]))
    return table


def observations(source: Source, *, maps: set[str] | None = None,
                 players: set[str] | None = None) -> Iterator[Observation]:
    """Stream Observations. `maps` restricts to those beatmap MD5s; `players` to those names."""
    md5s, who, eras = _identify(source)
    wanted = {
        name for name, md5 in md5s.items()
        if (maps is None or md5 in maps) and (players is None or who[name] in players)
    }
    if not wanted:
        return

    runs = _runs(source, wanted)

    with open(source.geometry) as f:
        for row in csv.DictReader(f):
            replay = sys.intern(row["replay"])
            if replay not in wanted or row["result"] == UNJUDGED:
                continue

            start = float(row["startTime"])
            snap, run_index, run_size = runs.get((replay, round(start, 3)), (None, None, None))

            angle = _num(row["signedAngle"])
            if angle is not None:
                # Radians out of the shim, degrees on the axis. Getting this backwards
                # does not raise; it puts every observation in the middle bin.
                assert abs(angle) <= math.pi + 1e-4, f"signedAngle not in radians: {angle}"
                angle *= DEG

            yield Observation(
                replay=replay,
                player=who[replay],
                beatmap_md5=md5s[replay],
                era=eras[replay],
                start_time=start,
                target=row["kind"],
                # Populated exactly when the previous object was a slider, which is the
                # thing being facetted: the cursor enters this turn from wherever slider
                # tracking left it, not from the tail.
                from_slider="1" if row["exitSlackRadii"] else "0",
                angle=angle,
                # spacingRadii, not minJumpRadii. The two differ only where a slider came
                # before, and what an axis should carry is the jump the map asks for, not
                # the one the follow circle shortens. The slider case is already visible
                # twice over: fromSlider is a facet, and exitSlackRadii measures the
                # correction directly.
                spacing=_num(row["spacingRadii"]),
                velocity=_num(row["requiredVelocity"]),
                delta_time=_num(row["deltaTime"]),
                snap=snap,
                run_index=run_index,
                run_size=run_size,
                hit_error=_num(row["hitError"]),
                aim_error=_num(row["aimErrorRadii"]),
                result=sys.intern(row["result"]),
            )


def map_coverage(source: Source) -> dict[str, set[str]]:
    """beatmap md5 -> set of replay basenames, for working out what the two sides share."""
    md5s, _, _ = _identify(source)
    coverage: dict[str, set[str]] = {}
    for name, md5 in md5s.items():
        coverage.setdefault(md5, set()).add(name)
    return coverage


def shared_maps(a: Source, b: Source) -> set[str]:
    """Beatmap MD5s present on both sides. The comparison is worthless without this:
    it is what stops one side's whole library being measured against the other's."""
    return set(map_coverage(a)) & set(map_coverage(b))
