"""Build a self-contained replay viewer from a play and a reference field.

Takes one of your replays plus other people's replays of the same beatmap, runs each
through `ora scene`, and folds the results into `template.html` as a single inlined
payload. The output is one file with no fetches in it, because an artifact's content
policy blocks them and a viewer that cannot load its own data is not a viewer.

    python3 tools/viewer/build.py --replay <path.osr> --out build/viewer/lap.html \
        --reference build/reference-nm/ref-1.osr build/reference-nm/ref-2.osr

With `--auto N` the reference field is chosen for you: the N highest-pp no-mod lazer
scores on the same beatmap that the corpus already has.

With `--mine N` the field is *you*, N of your own other completed runs on the same beatmap,
sampled evenly across the span you have been playing it. The comparison stops being "a
better player's line" and becomes your own spread: the same objects, the same hand, on
different days. Each ghost is labelled by date and carries its own timing spread, so the
roster reads as a history rather than a leaderboard.

Every object also carries what the object inspector shows -- heading, spacing, turn, aim
error, and the cursor's visit to it -- folded in from `ora geometry` and `ora clicks` over
the same replay, so the browser draws those numbers and never derives them.

Reference players contribute frames and per-object errors only, never geometry. They are
on the same beatmap, so the objects are already in the document once, and object index is
the join key — the scene pass emits objects in start-time order for every replay of a
beatmap, so index *i* is the same object for everybody.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "analysis"))
from streamfail import corner  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notes as annotations  # noqa: E402

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
ORA = os.path.join(ROOT, "src", "Cli", "bin", "Release", "net10.0", "ora.dll")


def scene(replay: str, destination: str) -> dict:
    """Run the extraction shim over one replay and read back its scene document.

    The command exits non-zero when its own path-walk cross-check diverges, and that is
    worth propagating rather than swallowing: a viewer drawing a polyline the shim could
    not reproduce positions on is drawing something nobody has checked.
    """
    result = subprocess.run(
        ["dotnet", ORA, "scene", replay, destination],
        capture_output=True, text=True, cwd=ROOT)

    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"scene failed on {os.path.basename(replay)}")

    with open(destination) as handle:
        return json.load(handle)


def attribute_misses(objects: list[tuple[float, float, bool]],
                     presses: list[tuple[float, str, float, float]]) -> dict[int, list[tuple[float, float, float]]]:
    """The presses each missed object was meant to receive, by time, first one first.

    ``objects`` are every circle and slider head as (start, meh window, missed), in start
    order; ``presses`` every button-down as (time, outcome, x, y), outcome as presses.csv
    gives it. A press that judged nothing belongs to the object nearest it in time, and is
    kept when that object missed and the press is inside its meh window, so a miss's tick
    always lands where the judgement would have been. Time, not cursor: clicks.csv's stray
    attribution follows the last circle the cursor was inside, which the replay's own buttons
    contradict wherever the two disagree (91% for timing against 15%, plain-buttons era), and
    which cannot attribute anything to a circle the cursor never reached.

    Two or more on one miss: the first is the attempt, measured by the same buttons (69% on
    the predicted finger against 61% for the nearest and 36% for the last). The rest are
    returned after it so they can still be drawn, as presses that were not the attempt.
    """
    starts = [o[0] for o in objects]
    out: dict[int, list[tuple[float, float, float]]] = {}
    for time, outcome, x, y in sorted(presses):
        if outcome == "hit" or not objects:
            continue
        k = bisect.bisect_left(starts, time)
        near = min((q for q in (k - 1, k) if 0 <= q < len(objects)),
                   key=lambda q: (abs(starts[q] - time), q))
        start, meh, missed = objects[near]
        if missed and abs(time - start) <= meh:
            out.setdefault(near, []).append((time, x, y))
    return out


def inspect(replay: str, work: str) -> tuple[dict[int, dict], list[dict] | None]:
    """Per-object numbers the analysis already has, for the viewer's object inspector.

    The viewer draws nothing it computed itself, and that holds for the inspector too:
    heading, spacing, turn and aim error come from `ora geometry`, and the cursor's visit to
    each circle -- when it was deepest, how deep, when it entered and left -- from `ora
    clicks`, and the presses a miss was meant to receive from `ora presses`, attributed by
    time (`attribute_misses`). All take a corpus, so the replay is surveyed on its own
    through a one-file folder first. Keyed by start time rounded to the millisecond,
    which is how the two CSVs and the scene document agree on an object.

    Returns (numbers, runs): the runs are the rhythm pass's constant-snap stretches, which
    note selectors pick objects from. An empty dict and no runs on any failure. The
    inspector is an addition to the viewer, not
    a precondition of it, and a lap that cannot be inspected is still worth watching.
    """
    folder = os.path.join(work, "inspect")
    os.makedirs(folder, exist_ok=True)
    os.symlink(os.path.abspath(replay), os.path.join(folder, "replay.osr"))
    corpus = os.path.join(work, "inspect.json")
    geometry = os.path.join(work, "inspect-geometry.csv")
    clicks = os.path.join(work, "inspect-clicks.csv")
    rhythm = os.path.join(work, "inspect-rhythm.csv")
    presses = os.path.join(work, "inspect-presses.csv")

    for command in (["survey", "--out", corpus, folder],
                    ["geometry", "--corpus", corpus, "--out", geometry],
                    ["clicks", "--corpus", corpus, "--out", clicks],
                    ["rhythm", "--corpus", corpus, "--out", rhythm],
                    ["presses", "--corpus", corpus, "--out", presses]):
        result = subprocess.run(["dotnet", ORA, *command], capture_output=True, text=True,
                                cwd=ROOT)
        if result.returncode != 0 or (command[0] != "survey"
                                      and not os.path.exists(command[-1])):
            print(f"  inspector: `ora {command[0]}` failed, building without it")
            return {}, None

    def num(text):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    out: dict[int, dict] = {}
    with open(geometry, newline="") as handle:
        for r in csv.DictReader(handle):
            out.setdefault(round(float(r["startTime"])), {}).update({
                "sp": num(r["spacingRadii"]),
                "hd": num(r["heading"]),
                "an": num(r["signedAngle"]),
                "ae": num(r["aimErrorRadii"]),
                # Not shipped: the inputs reach and inside are computed from below.
                "tx": num(r["targetX"]), "ty": num(r["targetY"]),
                "cx": num(r["cursorX"]), "cy": num(r["cursorY"]),
            })
    with open(clicks, newline="") as handle:
        rows = sorted(csv.DictReader(handle), key=lambda r: float(r["startTime"]))
    with open(presses, newline="") as handle:
        pressed = [(float(r["time"]), r["outcome"], float(r["x"]), float(r["y"]))
                   for r in csv.DictReader(handle)]
    attempts = attribute_misses(
        [(float(r["startTime"]), float(r["meh"]), r["result"] == "Miss") for r in rows], pressed)
    for k, r in enumerate(rows):
        start = float(r["startTime"])

        def offset(column):
            value = num(r[column])
            return None if value is None else value - start

        visit = {
            "cl": offset("closestTime"),
            "cd": num(r["closestDist"]),
            "en": offset("enterTime"),
            "ex": offset("exitTime"),
        }
        if k in attempts:
            (t, x, y), *rest = attempts[k]
            visit.update({"sy": t - start, "zx": x, "zy": y,
                          "sz": [[t2 - start, x2, y2] for t2, x2, y2 in rest] or None})
        else:
            # No press in the window: fall back to the cursor's attribution, which reaches
            # later presses (up to meh + 100 ms) and is right less often (74-84% on the
            # predicted finger). Coverage first; `sc` marks the tick as the weaker kind.
            visit.update({"sy": offset("strayPressTime"), "zx": num(r["strayX"]),
                          "zy": num(r["strayY"]),
                          "sc": 1 if r["result"] == "Miss" and r["strayPressTime"] else None})
        out.setdefault(round(start), {}).update(visit)
    missed = sum(r["result"] == "Miss" for r in rows)
    fallback = sum(1 for k, r in enumerate(rows)
                   if k not in attempts and r["result"] == "Miss" and r["strayPressTime"])
    print(f"  misses: {missed}; a press by time on {len(attempts)}, by cursor on {fallback}, "
          f"{sum(len(v) > 1 for v in attempts.values())} with more than one press")
    # The runs a selector picks from: constant-snap stretches, from the same rhythm pass the
    # analysis uses, at its default gap and tolerance.
    runs, skipped = annotations.read_runs(rhythm)
    if skipped:
        print(f"  rhythm: {skipped} run(s) at a beat or wider could not be rebuilt and are "
              f"left out of selectors")
    return out, runs


def attach(document: dict, numbers: dict[int, dict]) -> int:
    """Fold the inspector's numbers onto the scene's objects, and return how many matched.

    One shift happens here and nowhere else. Geometry's row for object n measures the turn
    whose apex is object n-1 (see `atlas.py`), so the turn *at* an object is the angle on the
    next object's row. Anything else is the object's own row. The next object has to be the
    next one in the scene, not the next row, because spinners have no geometry row and the
    turn across one is not a turn.
    """
    objects = document["objects"]
    matched = 0
    rows = [numbers.get(round(o["t"])) for o in objects]
    for k, o in enumerate(objects):
        # The viewer and `ora clip` both address objects by position in the beatmap, and
        # a pattern selection hands that number straight to the clip command.
        if o["i"] != k:
            raise SystemExit(f"scene object {k} carries index {o['i']}; the clip command "
                             f"the viewer prints would cut the wrong objects")
        row = rows[k]
        if row is None:
            continue
        matched += 1
        following = rows[k + 1] if k + 1 < len(objects) else None
        previous = rows[k - 1] if k > 0 else None

        # Reach and inside, by the analysis's own function rather than a copy of it, so the
        # viewer's numbers and streamfail.py's cannot drift apart. Same inputs it takes:
        # the targets either side and the cursor at the press (or, on a miss, at the
        # object's time), against the geometry pass's aim error. A spinner either side
        # leaves no corner to measure.
        reach = inside = None
        if (previous and following and objects[k - 1]["type"] != "spinner"
                and objects[k + 1]["type"] != "spinner"
                and None not in (previous.get("tx"), row.get("tx"), following.get("tx"),
                                 row.get("cx"), row.get("cy"))):
            reach, inside = corner((previous["tx"], previous["ty"]), (row["tx"], row["ty"]),
                                   (following["tx"], following["ty"]),
                                   (row["cx"], row["cy"]), row.get("ae"))
        fields = {
            "rr": (reach, 3), "ri": (inside, 3),
            "sp": (row.get("sp"), 3), "hd": (row.get("hd"), 4), "ae": (row.get("ae"), 3),
            "tn": (following.get("an") if following else None, 4),
            "cl": (row.get("cl"), 1), "cd": (row.get("cd"), 3),
            "en": (row.get("en"), 1), "ex": (row.get("ex"), 1), "sy": (row.get("sy"), 1),
            "zx": (row.get("zx"), 2), "zy": (row.get("zy"), 2), "sc": (row.get("sc"), 0),
        }
        for key, (value, places) in fields.items():
            if value is not None:
                o[key] = round(value, places)
        if row.get("sz"):
            o["sz"] = [[round(e, 1), round(x, 2), round(y, 2)] for e, x, y in row["sz"]]
    return matched


def spread(document: dict) -> float:
    """Timing spread over the clicks that have one.

    Spinners and misses carry no hit error — a spinner never had a click, a miss never
    received one — and counting either as a zero would report a steadier player than the
    replay shows.
    """
    errors = [o["error"] for o in document["objects"] if o.get("error") is not None]
    return round(statistics.pstdev(errors), 2) if len(errors) > 1 else 0.0


def corpus_lookup(md5: str, limit: int) -> list[str]:
    """The best no-mod lazer scores the reference corpus holds for one beatmap."""
    reference = json.load(open(os.path.join(ROOT, "build", "corpus-nm.json")))
    manifest = json.load(open(os.path.join(ROOT, "build", "reference", "manifest.json")))
    by_file = {v["file"]: v for v in manifest.values()}

    rows = []
    for record in reference:
        if record["BeatmapMd5"] != md5:
            continue
        entry = by_file.get(os.path.basename(record["Path"]))
        if entry and not entry["mods"]:
            rows.append((entry.get("pp", 0), record["Path"], entry["username"]))

    rows.sort(reverse=True)
    return [path for _, path, _ in rows[:limit]]


def own_runs(md5: str, limit: int, exclude: str) -> list[str]:
    """N of my own other completed runs on one beatmap, spread across their whole span.

    Evenly sampled rather than best-first or most-recent, because the question an all-me
    field answers is how much the same hand varies, and taking the top N by anything
    answers a narrower one. The corpus is in no particular order, so sort before sampling.
    """
    corpus = json.load(open(os.path.join(ROOT, "build", "corpus.json")))
    paths = sorted(record["Path"] for record in corpus
                   if record["BeatmapMd5"] == md5 and record.get("Paired")
                   and os.path.abspath(record["Path"]) != os.path.abspath(exclude))

    if len(paths) <= limit:
        return paths

    # Endpoints included, so the span is actually shown rather than its middle.
    step = (len(paths) - 1) / (limit - 1) if limit > 1 else 0
    return [paths[round(i * step)] for i in range(limit)]


def when(path: str) -> str:
    """The run's date, from the name lazer exported it under: `... (2026-01-09_11-55).osr`.

    This is the ghost label for an all-me field, where every name would otherwise be the
    same word. Falls back to the bare file name, which is wrong-looking rather than silently
    identical -- two ghosts sharing a label would make the roster unreadable.
    """
    stamp = re.search(r"\((\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\)", os.path.basename(path))
    return f"{stamp.group(1)} {stamp.group(2)}:{stamp.group(3)}" if stamp \
        else os.path.splitext(os.path.basename(path))[0][-24:]


def username(path: str) -> str:
    """Whoever set the score, from the fetch manifest, falling back to the file name."""
    try:
        manifest = json.load(open(os.path.join(ROOT, "build", "reference", "manifest.json")))
        by_file = {v["file"]: v for v in manifest.values()}
        entry = by_file.get(os.path.basename(path))
        if entry:
            return entry["username"]
    except FileNotFoundError:
        pass

    name = os.path.basename(path)
    return name.split(" playing ")[0] if " playing " in name else os.path.splitext(name)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", required=True, help="the play the viewer is about")
    parser.add_argument("--out", help="destination HTML file")
    parser.add_argument("--reference", nargs="*", default=[], help="other players' replays")
    parser.add_argument("--auto", type=int, default=0,
                        help="instead, take the N best no-mod lazer scores from the corpus")
    parser.add_argument("--mine", type=int, default=0,
                        help="instead, take N of your own other runs on the same beatmap")
    parser.add_argument("--notes", help="annotations to show, as JSON (see notes.py)")
    parser.add_argument("--check", action="store_true",
                        help="validate --notes against the replay and stop, without building")
    parser.add_argument("--describe", nargs="?", const="all", metavar="A-B",
                        help="print every value a note can cite, per object, and stop; "
                             "optionally only objects A-B")
    args = parser.parse_args()

    if args.check and not args.notes:
        raise SystemExit("--check validates --notes; give it a notes file")
    # The out path only matters when something is built.
    if not (args.check or args.describe) and not args.out:
        raise SystemExit("--out is required to build")

    if args.auto and args.mine:
        raise SystemExit("--auto and --mine are different fields; pick one")

    if not os.path.exists(ORA):
        raise SystemExit(f"no release build at {ORA}; run: dotnet build -c Release")

    with tempfile.TemporaryDirectory() as work:
        subject = scene(args.replay, os.path.join(work, "subject.json"))
        print(f"{len(subject['objects'])} objects, {subject['frameCount']} frames, "
              f"spread {spread(subject)}ms")

        numbers, runs = inspect(args.replay, work)
        matched = attach(subject, numbers)
        clickable = sum(1 for o in subject["objects"] if o["type"] != "spinner")
        print(f"inspector: {matched} of {clickable} objects carry geometry and cursor visits")

        if args.describe:
            lo = hi = None
            if args.describe != "all":
                found = re.fullmatch(r"(\d+)(?:-(\d+))?", args.describe)
                if not found:
                    raise SystemExit("--describe takes a range A-B or a single index")
                lo = int(found.group(1))
                hi = int(found.group(2)) if found.group(2) else lo
            print(annotations.describe(subject, lo, hi))
            return 0

        # Notes are validated before anything slow, and every problem is reported at once.
        notes = []
        if args.notes:
            notes, problems, warnings = annotations.resolve(
                annotations.load(args.notes), subject, runs)
            for w in warnings:
                print("  warning: " + w, file=sys.stderr)
            if problems:
                print(f"\n{len(problems)} problem(s) in {args.notes}:", file=sys.stderr)
                for p in problems:
                    print("  " + p, file=sys.stderr)
                return 1
            stops = sum(1 for n in notes if n["stop"])
            print(f"notes: {len(notes)} valid, {stops} stop playback")
            if args.check:
                # A selector's marks are summarised -- how many, and the first few -- since
                # two hundred identical rows tell an author less than one count does.
                shown: dict[str, int] = {}
                for n in notes:
                    group = n.get("group")
                    if group is not None:
                        shown[group] = shown.get(group, 0) + 1
                        if shown[group] > 3:
                            continue
                    where = (f"objects {n['lo']}-{n['hi']}" if n["kind"] == "objects"
                             else f"{n['from']:.0f}-{n['to']:.0f} ms")
                    stop = f"stops at {n['stopAt']:.0f} ms" if n["stop"] else "no stop"
                    words = n["text"] or f"(mark{', ' + n['label'] if n.get('label') else ''})"
                    tag = f"[{group}] " if group is not None else ""
                    print(f"  {where:<22} {stop:<20} {tag}{words[:60]}")
                    for line in annotations.values(n["text"], subject):
                        print(f"  {'':<22} {'':<20}   {line}")
                for group, count in shown.items():
                    if count > 3:
                        print(f"  [{group}]: {count} marks in all, first three shown")
                return 0

        paths, field = args.reference, "reference"
        if args.auto:
            paths = corpus_lookup(subject["beatmapMd5"], args.auto)
            print(f"reference field: {len(paths)} no-mod lazer scores from the corpus")
        elif args.mine:
            paths = own_runs(subject["beatmapMd5"], args.mine, args.replay)
            field = "your runs"
            print(f"own field: {len(paths)} of your other runs on this beatmap")

        ghosts = []
        for n, path in enumerate(paths):
            other = scene(path, os.path.join(work, f"ghost{n}.json"))

            # Same beatmap or the join key means nothing. Checked rather than assumed,
            # because a mismatch would silently attribute one map's errors to another's.
            if other["beatmapMd5"] != subject["beatmapMd5"]:
                print(f"  skipped {os.path.basename(path)}: different beatmap")
                continue

            # Same speed, or the overlay compares two different demands. The reference path
            # filters to no-mod before it gets here; an all-me field cannot, because the
            # corpus holds every completed run and the player does use rate mods.
            if other["rate"] != subject["rate"] or other["mods"] != subject["mods"]:
                print(f"  skipped {when(path)}: {other['mods'] or 'no mod'} at "
                      f"{other['rate']}x, not {subject['mods'] or 'no mod'} at "
                      f"{subject['rate']}x")
                continue

            # Where each of their presses landed, not just how they moved. The viewer
            # leaves these on the field the same way it leaves the player's, which is what
            # makes a stream's evenness checkable: a row of marks either steps along the
            # path at one pitch or it does not.
            marks = [[o["i"], o["hitTime"], o["hx"], o["hy"]]
                     for o in other["objects"]
                     if o.get("hx") is not None and o.get("hitTime") is not None]

            ghosts.append({
                "name": when(path) if args.mine else username(path),
                "sd": spread(other),
                "frames": other["frames"],
                "marks": marks,
                "errors": {str(o["i"]): o["error"]
                           for o in other["objects"] if o.get("error") is not None},
            })
            print(f"  {ghosts[-1]['name']:<18} spread {ghosts[-1]['sd']}ms")

    payload = json.dumps(
        {"scene": subject, "ghosts": ghosts, "field": field, "notes": notes,
         "you": {"name": "you", "sd": spread(subject)}},
        separators=(",", ":"))

    with open(TEMPLATE) as handle:
        html = handle.read()

    if "/*__PAYLOAD__*/" not in html:
        raise SystemExit("template has no payload slot")

    # A closing tag inside the JSON would end the script element early and truncate the
    # document at whatever object happened to contain it.
    html = html.replace("/*__PAYLOAD__*/", payload.replace("</", r"<\/"), 1)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        handle.write(html)

    print(f"\nwritten: {args.out}  ({len(html) / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
