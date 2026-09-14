"""A corpus with a known defect in it, written in the real CSV formats.

The point is not to test arithmetic — the modules test their own arithmetic against
closed forms. The point is the end-to-end claim the whole package exists to make: if a
player is worse in one cell and only that cell, does that cell come out on top of the
findings table? Everything between the CSV and the answer is exercised, including the
rhythm join and the radians-to-degrees conversion, both of which are silent when wrong.

Written as files rather than as Observation objects on purpose. A fixture that skips the
loader cannot catch a loader that mis-joins, and mis-joining is the failure this layer is
most exposed to.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random

GEOMETRY_COLUMNS = [
    "replay", "startTime", "kind", "signedAngle", "lazerAngle", "observedAngle",
    "spacingRadii", "minJumpRadii", "deltaTime", "requiredVelocity", "aimErrorRadii",
    "hitError", "result", "travelRadii", "travelTime", "exitSlackRadii",
]

RHYTHM_COLUMNS = [
    "replay", "groupGap", "tolerance", "groupId", "groupSize", "groupIndex", "runId",
    "runSize", "runIndex", "snap", "interiorCircles", "runFirstKind", "runLastKind",
    "clickIndex", "time", "beatLength", "kind", "result", "error", "action",
]

# Where the defect goes. Chosen to be a cell that is ordinary in every other respect,
# so nothing but the injection can explain it standing out.
DEFECT = ("120:150", "2.5:3", "0.25")

# The geometry each cell label is generated from. Values sit mid-bin so that a small
# arithmetic slip does not move an observation into a neighbouring cell and turn a real
# failure into a confusing one.
_ANGLES = {"-150:-120": -135.0, "-120:-90": -105.0, "0:30": 15.0, "60:90": 75.0,
           "120:150": 135.0, "150:180": 165.0}
_SPACINGS = {"1:1.5": 1.25, "2:2.5": 2.25, "2.5:3": 2.75, "3:3.5": 3.25, "4.5:6": 5.0}
_SNAPS = {"0.25": 0.25, "0.5": 0.5, "1": 1.0}

BEAT_LENGTH = 300.0


def _cells():
    """Every combination the fixture generates, in a fixed order so object times are
    stable across runs and the per-object map-bias join has something to land on."""
    return [(a, s, k) for a in _ANGLES for s in _SPACINGS for k in _SNAPS]


def corpus(directory: str, *, players: int = 40, repeats: int = 12,
           beatmaps: int = 3, base_sd: float = 12.0, aim_mean: float = 0.30,
           defect_shift: float = 15.0, defect_widen: float = 1.0,
           map_bias: dict[tuple[int, int], float] | None = None,
           seed: int = 20260913) -> dict[str, str]:
    """Write a player side and a reference side into `directory`.

    Every player, reference and subject alike, is drawn from the same distribution
    everywhere except the defect cell, where the subject's hit errors are shifted by
    `defect_shift` milliseconds and their spread multiplied by `defect_widen`.

    `map_bias` optionally plants a mistiming that everyone shares — keyed by
    (beatmap index, object index) and applied to reference and subject alike, which is
    exactly the confound `maptiming` has to find and remove.
    """
    rng = random.Random(seed)
    os.makedirs(directory, exist_ok=True)

    layout = _cells()
    md5s = [f"{i:032x}" for i in range(1, beatmaps + 1)]
    paths = {
        "player_geometry": os.path.join(directory, "geometry.csv"),
        "player_rhythm": os.path.join(directory, "rhythm.csv"),
        "player_corpus": os.path.join(directory, "corpus.json"),
        "reference_geometry": os.path.join(directory, "geometry-reference.csv"),
        "reference_rhythm": os.path.join(directory, "rhythm-reference.csv"),
        "reference_corpus": os.path.join(directory, "corpus-nm.json"),
        "manifest": os.path.join(directory, "manifest.json"),
    }

    player_corpus, reference_corpus, manifest = [], [], {}

    def emit(geometry, rhythm, replay, md5, subject):
        """One replay: every cell in the layout, `repeats` times each."""
        rows = 0
        for repeat in range(repeats):
            for index, (angle, spacing, snap) in enumerate(layout):
                # Object time is a pure function of position, so the same object in the
                # same beatmap has the same time for every player. Without that the
                # per-object map bias has nothing to group on.
                position = repeat * len(layout) + index
                time = 1000.0 + position * 250.0
                delta = _SNAPS[snap] * BEAT_LENGTH
                defective = subject and (angle, spacing, snap) == DEFECT

                sd = base_sd * (defect_widen if defective else 1.0)
                error = rng.gauss(defect_shift if defective else 0.0, sd)
                error += (map_bias or {}).get((md5s.index(md5), position), 0.0)

                geometry.writerow({
                    "replay": replay,
                    "startTime": f"{time:g}",
                    "kind": "circle",
                    # Radians on the way out, because that is what the real extractor
                    # writes and the conversion is the loader's job to get right.
                    "signedAngle": f"{_ANGLES[angle] / (180 / math.pi):.6f}",
                    "lazerAngle": "", "observedAngle": "",
                    "spacingRadii": f"{_SPACINGS[spacing]:g}",
                    "minJumpRadii": f"{_SPACINGS[spacing]:g}",
                    "deltaTime": f"{delta:g}",
                    "requiredVelocity": f"{_SPACINGS[spacing] / delta:.6f}",
                    "aimErrorRadii": f"{max(0.0, rng.gauss(aim_mean, 0.1)):.5f}",
                    "hitError": f"{error:.3f}",
                    "result": "Great",
                    "travelRadii": "0", "travelTime": "0", "exitSlackRadii": "",
                })
                rhythm.writerow({
                    "replay": replay, "groupGap": "1", "tolerance": "0.02",
                    "groupId": "0", "groupSize": str(len(layout)), "groupIndex": "0",
                    "runId": str(position), "runSize": "4",
                    "runIndex": str(index % 4), "snap": snap, "interiorCircles": "1",
                    "runFirstKind": "circle", "runLastKind": "circle",
                    "clickIndex": str(position), "time": f"{time:g}",
                    "beatLength": f"{BEAT_LENGTH:g}", "kind": "circle",
                    "result": "Great", "error": f"{error:.3f}", "action": "LeftButton",
                })
                rows += 1
        return rows

    with open(paths["player_geometry"], "w", newline="") as pg, \
         open(paths["player_rhythm"], "w", newline="") as pr, \
         open(paths["reference_geometry"], "w", newline="") as rg, \
         open(paths["reference_rhythm"], "w", newline="") as rr:

        writers = {
            "pg": csv.DictWriter(pg, GEOMETRY_COLUMNS), "pr": csv.DictWriter(pr, RHYTHM_COLUMNS),
            "rg": csv.DictWriter(rg, GEOMETRY_COLUMNS), "rr": csv.DictWriter(rr, RHYTHM_COLUMNS),
        }
        for w in writers.values():
            w.writeheader()

        for m, md5 in enumerate(md5s):
            # Dates chosen to land one replay in each of the three input regimes, so a
            # stratified run has something in every stratum.
            date = ["2024-12-02_10-00", "2025-06-02_10-00", "2026-03-02_10-00"][m % 3]
            replay = f"subject playing Synthetic - Map {m} (Fixture) [Test] ({date}).osr"
            emit(writers["pg"], writers["pr"], replay, md5, subject=True)
            player_corpus.append({"Path": f"{directory}/{replay}", "BeatmapMd5": md5})

            for p in range(players):
                score = 900000 + m * 1000 + p
                name = f"ref-{score}.osr"
                emit(writers["rg"], writers["rr"], name, md5, subject=False)
                reference_corpus.append({"Path": f"{directory}/{name}", "BeatmapMd5": md5})
                manifest[str(score)] = {
                    "file": name, "beatmap_md5": md5, "user_id": p,
                    "username": f"player{p:03d}", "mods": [], "filter": "NM",
                }

    json.dump(player_corpus, open(paths["player_corpus"], "w"))
    json.dump(reference_corpus, open(paths["reference_corpus"], "w"))
    json.dump(manifest, open(paths["manifest"], "w"))
    return paths
