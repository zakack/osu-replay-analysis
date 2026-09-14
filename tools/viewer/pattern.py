"""Build a pattern card: every traversal of one bin, cropped to the pattern.

The viewer shows a play. This shows a *cell* — the unit the analysis actually reasons
in — by collecting every object on a beatmap that falls in that bin and drawing everyone
who went through it, scoped in both space and time to the pattern itself.

    python3 tools/viewer/pattern.py --beatmap d3e1b4ed8e7e \
        --cell "-150:-120|1.5:2|0.25" --out build/viewer/cell.html

Two claims make this more than a montage, and both come from the feature design:

* **Cell membership is a property of the object, not the play.** Angle, spacing and snap
  are map geometry, so the same objects are in the cell for every player. That is what
  makes "everyone's traversal of the same pattern" a well-defined set rather than a
  selection the author made.
* **The features are rotation invariant, so the instances can be superimposed.** Each
  traversal is translated to put the apex at the origin and rotated to put the incoming
  leg on a fixed bearing, then scaled into circle radii. Chirality is *not* folded — the
  cell is one handedness band already, and mirroring the other one in would erase the
  asymmetry the charter keeps signed angles to preserve.

Cell membership comes from the bins package rather than from arithmetic here, so the
picture and the table cannot disagree about what is in the bin.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bins import cells, load  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pattern.html")
ORA = os.path.join(ROOT, "src", "Cli", "bin", "Release", "net10.0", "ora.dll")

# How much of the approach and the exit to keep around a traversal. Wide enough to show
# where the cursor came from and where it went, tight enough that the pattern is the
# subject rather than the map around it.
LEAD_MS = 90
TRAIL_MS = 90


def scene(replay: str, destination: str) -> dict:
    result = subprocess.run(["dotnet", ORA, "scene", replay, destination],
                            capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"scene failed on {os.path.basename(replay)}")
    with open(destination) as handle:
        return json.load(handle)


def instance_times(md5: str, cell: tuple[str, ...], scheme: str,
                   target: str, from_slider: str) -> set[float]:
    """The object times on this beatmap that fall in the cell.

    Taken from the player's own extraction because the geometry is the map's and is
    therefore the same for everyone; the reference pass would name the identical objects.
    """
    found = set()
    for o in load.observations(load.PLAYER, maps={md5}, players={"zaksynack"}):
        if o.target != target or o.from_slider != from_slider:
            continue
        if cells.key(o, scheme) == cell:
            found.add(round(o.start_time, 3))
    return found


def traversal(document: dict, apex_index: int, frames: list) -> dict | None:
    """One player's cursor through one instance, in the pattern's own frame.

    Returns both the canonical path — apex at the origin, incoming leg along +x, measured
    in circle radii — and the raw one, because the contact sheet draws the pattern where
    it actually sits on the playfield and the overlay draws it where it can be compared.
    """
    objects = document["objects"]
    if apex_index < 1 or apex_index + 1 >= len(objects):
        return None

    before, apex, after = objects[apex_index - 1], objects[apex_index], objects[apex_index + 1]
    radius = document["beatmap"]["radius"]

    start = (before.get("endTime") or before["t"]) - LEAD_MS
    end = after["t"] + TRAIL_MS

    raw = []
    for k in range(0, len(frames), 4):
        t = frames[k]
        if t < start:
            continue
        if t > end:
            break
        raw.append((round(t - apex["t"], 1), frames[k + 1], frames[k + 2]))

    if len(raw) < 3:
        return None

    # The incoming leg sets the frame. Where the player came *from* is the stable
    # reference; where they go next is the thing being compared.
    bearing = math.atan2(before["y"] - apex["y"], before["x"] - apex["x"])
    cos, sin = math.cos(-bearing), math.sin(-bearing)

    def canon(x, y):
        dx, dy = (x - apex["x"]) / radius, (y - apex["y"]) / radius
        return round(dx * cos - dy * sin, 4), round(dx * sin + dy * cos, 4)

    flat = []
    for t, x, y in raw:
        cx, cy = canon(x, y)
        flat += [t, cx, cy]

    return {
        "t": flat,
        "err": apex.get("error"),
        "result": apex["result"],
        "raw": [v for t, x, y in raw for v in (t, round(x, 1), round(y, 1))],
        "in": canon(before["x"], before["y"]),
        "out": canon(after["x"], after["y"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beatmap", required=True, help="beatmap md5, or a unique prefix")
    parser.add_argument("--cell", required=True, help='cell labels joined by "|"')
    parser.add_argument("--scheme", default="angle-spacing-snap")
    parser.add_argument("--target", default="circle")
    parser.add_argument("--from-slider", default="0")
    parser.add_argument("--reference", type=int, default=12, help="reference replays to draw")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if not os.path.exists(ORA):
        raise SystemExit(f"no release build at {ORA}; run: dotnet build -c Release")

    cell = tuple(args.cell.split("|"))
    corpus = json.load(open(os.path.join(ROOT, "build", "corpus.json")))
    reference = json.load(open(os.path.join(ROOT, "build", "corpus-nm.json")))
    manifest = json.load(open(os.path.join(ROOT, "build", "reference", "manifest.json")))
    by_file = {v["file"]: v for v in manifest.values()}

    md5 = next(r["BeatmapMd5"] for r in corpus if r["BeatmapMd5"].startswith(args.beatmap))
    mine = [r["Path"] for r in corpus if r["BeatmapMd5"] == md5 and r.get("Paired")]

    others = sorted(((by_file[os.path.basename(r["Path"])].get("pp", 0), r["Path"])
                     for r in reference if r["BeatmapMd5"] == md5
                     and not by_file[os.path.basename(r["Path"])]["mods"]), reverse=True)
    others = [p for _, p in others[:args.reference]]

    times = instance_times(md5, cell, args.scheme, args.target, args.from_slider)
    print(f"cell {args.cell}: {len(times)} objects on this beatmap, "
          f"{len(mine)} of your runs, {len(others)} reference runs")
    if not times:
        raise SystemExit("no instances of that cell on this beatmap")

    instances: dict[int, dict] = {}
    stats = {"you": [], "reference": []}
    meta: dict = {}

    with tempfile.TemporaryDirectory() as work:
        for n, (path, is_mine) in enumerate([(p, True) for p in mine] +
                                            [(p, False) for p in others]):
            document = scene(path, os.path.join(work, f"s{n}.json"))
            meta = meta or {
                "artist": document["beatmap"]["artist"],
                "title": document["beatmap"]["title"],
                "difficulty": document["beatmap"]["difficulty"],
                "radius": document["beatmap"]["radius"],
                "circleSize": document["beatmap"]["circleSize"],
                "approachRate": document["beatmap"]["approachRate"],
            }
            name = ("you" if is_mine
                    else by_file[os.path.basename(path)]["username"])

            # Object index is the join key across replays of one beatmap: the scene pass
            # emits objects in start-time order, so index i is the same object for all.
            wanted = [k for k, o in enumerate(document["objects"])
                      if round(o["t"], 3) in times]

            for k in wanted:
                row = traversal(document, k, document["frames"])
                if row is None:
                    continue
                row["who"] = name
                row["mine"] = is_mine

                slot = instances.setdefault(k, {
                    "i": k,
                    "t": document["objects"][k]["t"],
                    "objects": [
                        {"x": document["objects"][j]["x"], "y": document["objects"][j]["y"],
                         "type": document["objects"][j]["type"]}
                        for j in (k - 1, k, k + 1)
                    ],
                    "paths": [],
                })
                slot["paths"].append(row)

                if row["err"] is not None:
                    stats["you" if is_mine else "reference"].append(row["err"])

            print(f"  {name:<18}{len(wanted):>4} traversals")

    for key, values in stats.items():
        if len(values) > 1:
            print(f"{key:<12} n={len(values):<6} spread {statistics.pstdev(values):.2f}ms "
                  f"mean {statistics.fmean(values):+.2f}ms")

    finding = {}
    findings_path = os.path.join(ROOT, "build", "findings.csv")
    if os.path.exists(findings_path):
        for r in csv.DictReader(open(findings_path)):
            if (r["scheme"] == args.scheme and r["cell"] == args.cell
                    and r["target"] == args.target and r["fromSlider"] == args.from_slider
                    and r["stratum"] == "all" and r["detrended"] == "0"):
                finding[r["metric"]] = r

    payload = {
        "cell": args.cell,
        "axes": list(cells.describe(args.scheme)),
        "scheme": args.scheme,
        "target": args.target,
        "fromSlider": args.from_slider,
        "finding": finding,
        "beatmap": meta,
        "instances": sorted(instances.values(), key=lambda s: s["t"]),
        "spread": {k: round(statistics.pstdev(v), 2) if len(v) > 1 else None
                   for k, v in stats.items()},
        "n": {k: len(v) for k, v in stats.items()},
    }

    with open(TEMPLATE) as handle:
        html = handle.read()

    html = html.replace("/*__PAYLOAD__*/",
                        json.dumps(payload, separators=(",", ":")).replace("</", r"<\/"), 1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        handle.write(html)

    print(f"\nwritten: {args.out}  ({len(html) / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
