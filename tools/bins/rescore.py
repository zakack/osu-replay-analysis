"""Re-scoring the objects lazer measured at a press that was not theirs.

geometry.csv carries each object's hit error and aim error at the press lazer judged it with.
Inside a stream that shifted because of the cursor -- a tap that landed on no circle, one
refused by notelock, or a circle the cursor left before its tap -- that press was meant for
a neighbour, so both numbers are a real measurement of the wrong thing: the hit error is
about one tap interval and the aim error is the cursor against a circle it was not aiming
at. The stream alignment (tools/analysis/streams.py) says which press each circle was meant
to receive, and for the cursor class the player's own fingers agree with it 93-97% where
they alternate (attribution_check.py, attribution_riding.py). Shifts that start at a dropped
or double tap are left alone: the fingers side with lazer's reading on most of them.

Per re-scored object the table holds the intended press's hit error (map time, the unit
geometry.csv uses) and aim error (radii), and lazer's originals beside them. An object in a
cursor chain with no press meant for it gets empty values, because lazer's are known wrong
and there is nothing to put in their place.

Radius is not recomputed: lazer's own aim error is distance over radius, so each replay's
radius is read back from geometry rows as distance(cursor, target) / aimErrorRadii, and the
spread of that estimate within a replay is reported as a check that it is one number.

The table is built per side, and both sides of a comparison must have one: correcting the
player and not the reference would move every player-versus-reference finding by the size
of the correction.

    python3 -m bins rescore        # both sides; needs build/presses-reference-nm.csv
"""
from __future__ import annotations

import bisect
import collections
import csv
import math
import os
import statistics
import sys

from . import load

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))
import streams as S  # noqa: E402

COLUMNS = ("replay", "startTime", "origin", "hitError", "aimErrorRadii",
           "lazerHitError", "lazerAimErrorRadii")


def _presses(path: str, wanted: set[str]) -> dict[str, list[tuple]]:
    """replay -> [(time, outcome, target, action, x, y)] in time order."""
    out: dict[str, list[tuple]] = collections.defaultdict(list)
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["replay"] in wanted:
                out[r["replay"]].append((float(r["time"]), r["outcome"],
                                         float(r["target"]) if r["target"] else None,
                                         r["action"], float(r["x"]), float(r["y"])))
    for v in out.values():
        v.sort()
    return out


def intended(source: load.Source) -> tuple[dict[tuple[str, float], tuple], collections.Counter]:
    """(replay, start) -> (origin, press or None) for every judged object in a cursor chain."""
    md5s, _, _ = load._identify(source)
    wanted = set(md5s)
    runs = S.build_runs(source.rhythm, lambda name: name in wanted)
    presses = _presses(source.presses, {r for r, _ in runs})
    marked: dict[tuple[str, float], tuple] = {}
    counts: collections.Counter = collections.Counter()
    for replay, events in runs:
        ps = presses.get(replay)
        if not ps:
            continue
        objects = [e[0] for e in events]
        beat, snap = events[0][1], events[0][2]
        interval = snap * beat
        lo = bisect.bisect_left(ps, (objects[0] - interval,))
        hi = bisect.bisect_right(ps, (objects[-1] + interval, "~"))
        window = ps[lo:hi]
        w3 = [p[:3] for p in window]
        r = S.classify(objects, [e[3] for e in events], [e[4] for e in events], w3, snap, beat)
        if r["verdict"] != "ok":
            continue
        r["interval"] = interval
        meant = {i: j for i, j in r["pairs"] if i is not None and j is not None}
        for origin, idx in S.chains(objects, w3, r):
            if origin not in S.CURSOR:
                continue
            for i in idx:
                if events[i][3] == "Miss" or events[i][4] is None:
                    continue
                j = meant.get(i)
                marked[(replay, round(objects[i], 3))] = (origin, window[j] if j is not None else None)
                counts[origin] += 1
    return marked, counts


def build(source: load.Source) -> None:
    marked, counts = intended(source)
    replays = {k[0] for k in marked}

    radii: dict[str, list[float]] = collections.defaultdict(list)
    rows: dict[tuple[str, float], dict] = {}
    xy_diff = []
    press_xy: dict[str, dict[float, tuple[float, float]]] = collections.defaultdict(dict)
    # Cursor positions at every press of the marked replays, for the differential check below.
    for replay, ps in _presses(source.presses, replays).items():
        press_xy[replay] = {round(p[0], 1): (p[4], p[5]) for p in ps}

    with open(source.geometry, newline="") as fh:
        for row in csv.DictReader(fh):
            replay = row["replay"]
            if replay not in replays or row["result"] == load.UNJUDGED:
                continue
            start = float(row["startTime"])
            key = (replay, round(start, 3))
            if key in marked:
                rows[key] = row
            if row.get("cursorSource") != "press" or not row["aimErrorRadii"] or not row["hitError"]:
                continue
            tx, ty = float(row["targetX"]), float(row["targetY"])
            cx, cy = float(row["cursorX"]), float(row["cursorY"])
            aim = float(row["aimErrorRadii"])
            if aim > 0.05:
                radii[replay].append(math.hypot(cx - tx, cy - ty) / aim)
            # The press log and the geometry pass both read the cursor at the judging press;
            # they must agree, or a re-scored aim error is in a different space from the rest.
            if key not in marked:
                at = press_xy[replay].get(round(start + float(row["hitError"]), 1))
                if at is not None:
                    xy_diff.append(math.hypot(at[0] - cx, at[1] - cy))

    spread = []
    radius = {}
    for replay, values in radii.items():
        radius[replay] = statistics.median(values)
        spread.append((max(values) - min(values)) / radius[replay])

    out_path = source.rescore
    written = empty = 0
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        for key in sorted(marked):
            row = rows.get(key)
            if row is None or key[0] not in radius:
                continue
            origin, press = marked[key]
            if press is None:
                hit = aim = ""
                empty += 1
            else:
                hit = round(press[0] - float(row["startTime"]), 3)
                aim = round(math.hypot(press[4] - float(row["targetX"]),
                                       press[5] - float(row["targetY"])) / radius[key[0]], 5)
            w.writerow((key[0], row["startTime"], origin, hit, aim, row["hitError"], row["aimErrorRadii"]))
            written += 1

    xy_diff.sort()
    spread.sort()
    print(f"{source.name}: {written} objects re-scored ({empty} with no press meant for them) "
          f"in {len(replays)} replays -> {out_path}", file=sys.stderr)
    print(f"  by origin: " + ", ".join(f"{o} {n}" for o, n in counts.most_common()), file=sys.stderr)
    if xy_diff:
        print(f"  check, press log vs geometry cursor at {len(xy_diff)} ordinary presses: "
              f"median {xy_diff[len(xy_diff) // 2]:.4f}, max {xy_diff[-1]:.4f} playfield units", file=sys.stderr)
    if spread:
        print(f"  check, radius read back per replay: relative spread median {spread[len(spread) // 2]:.5f}, "
              f"max {spread[-1]:.5f}", file=sys.stderr)


def read(path: str) -> dict[tuple[str, float], tuple[float | None, float | None]]:
    """(replay, start) -> (hit error, aim error), None where nothing replaces lazer's value."""
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out[(sys.intern(r["replay"]), round(float(r["startTime"]), 3))] = (
                float(r["hitError"]) if r["hitError"] else None,
                float(r["aimErrorRadii"]) if r["aimErrorRadii"] else None)
    return out
